"""FastAPI entry point: serves the voice UI and the streaming chat API."""

import hmac
import json
import logging
import os
import threading
from pathlib import Path

from typing import Any

import anthropic
import groq
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import TutorAgent, load_system_prompt
from . import knowledge
from .groq_agent import GroqTutorAgent
from .config import ON_VERCEL, get_settings
from .memory import LEVELS, SUBJECTS, Memory
from .modes import MODES
from .stt import TranscriptionError, transcribe
from .tts import GEMINI_ENGLISH_VOICES, GROQ_ENGLISH_VOICES, AzureTTS, ChainTTS, ElevenLabsTTS, GeminiTTS, GroqTTS, SpeechError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def create_agent() -> Any:
    settings = get_settings()
    log = logging.getLogger("tutor")
    memory = Memory(settings.memory_target)
    if settings.llm_provider == "anthropic":
        system_prompt = load_system_prompt(settings.system_prompt_path)
        if not (settings.anthropic_api_key or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            log.warning("ANTHROPIC_API_KEY is not set.")
        # api_key=None lets the SDK fall back to its own credential resolution
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        return TutorAgent(client, settings, memory, system_prompt)
    if not settings.groq_api_key:
        log.warning("GROQ_API_KEY is not set. Copy .env.example to .env and add your key.")
    # A placeholder key lets the app start; requests then fail with a clear auth error.
    # max_retries=0: the agent handles rate limits itself and tells the user it is waiting.
    groq_client = groq.AsyncGroq(api_key=settings.groq_api_key or "missing-key", max_retries=0)
    return GroqTutorAgent(groq_client, settings, memory,
                          load_system_prompt(settings.groq_system_prompt_path))


_agent_lock = threading.Lock()


def get_agent(request: Request) -> Any:
    """Create the agent on first use (works the same locally and on serverless platforms)."""
    state = request.app.state
    if not hasattr(state, "agent"):
        with _agent_lock:
            if not hasattr(state, "agent"):
                state.agent = create_agent()
    return state.agent


app = FastAPI(title="Rizwan's AI Voice Tutor")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def require_passcode(request: Request, call_next):
    if request.url.path.startswith("/api/"):
        passcode = get_settings().app_passcode
        if passcode:
            given = request.headers.get("x-app-passcode", "")
            if not hmac.compare_digest(given.encode(), passcode.encode()):
                return JSONResponse({"detail": "passcode required"}, status_code=401)
        elif ON_VERCEL:
            # Never expose the API (and the LLM key's credit) publicly without a passcode.
            return JSONResponse({"detail": "Set APP_PASSCODE in the Vercel project settings."},
                                status_code=503)
    return await call_next(request)


class ChatRequest(BaseModel):
    text: str = Field(max_length=8000)
    session_id: str = Field(default="default", max_length=64)
    mode: str | None = None
    urdu_voice: bool = False


class SpeakRequest(BaseModel):
    text: str = Field(max_length=4000)


class EnglishSpeakRequest(BaseModel):
    text: str = Field(max_length=4000)
    voice: str = Field(max_length=40)


class ResetRequest(BaseModel):
    session_id: str = Field(default="default", max_length=64)


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/manifest.webmanifest", include_in_schema=False)
async def manifest():
    return FileResponse(STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js", include_in_schema=False)
async def service_worker():
    # Served from the root so it can control the whole app; never cached so updates apply quickly.
    return FileResponse(STATIC_DIR / "sw.js", media_type="text/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(STATIC_DIR / "icons" / "favicon-32.png", media_type="image/png")


@app.get("/api/auth")
async def auth():
    """Lets the UI check a passcode (the middleware does the actual check)."""
    return {"ok": True}


@app.get("/api/modes")
async def modes():
    return {key: m["label"] for key, m in MODES.items()}


@app.post("/api/chat")
async def chat(req: ChatRequest, request: Request):
    agent = get_agent(request)

    async def event_stream():
        async for event in agent.run_turn(req.session_id, req.text, req.mode, req.urdu_voice):
            if await request.is_disconnected():
                break
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


_stt_client: groq.AsyncGroq | None = None


@app.post("/api/transcribe")
async def transcribe_audio(request: Request, lang: str | None = None):
    """Speech-to-text with Groq Whisper. Body: raw audio bytes (webm/mp4/ogg/wav)."""
    global _stt_client
    settings = get_settings()
    if not settings.groq_api_key:
        return JSONResponse({"detail": "Server speech recognition needs GROQ_API_KEY."}, status_code=501)
    if _stt_client is None:
        _stt_client = groq.AsyncGroq(api_key=settings.groq_api_key)
    audio = await request.body()
    try:
        text = await transcribe(_stt_client, audio, request.headers.get("content-type", ""),
                                lang, settings.groq_stt_model)
    except TranscriptionError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)
    return {"text": text}


_tts: Any = None
_gemini_tts: GeminiTTS | None = None


def get_gemini_tts() -> GeminiTTS | None:
    """One Gemini client for both the Urdu voice and the English voices."""
    global _gemini_tts
    settings = get_settings()
    if _gemini_tts is None and settings.gemini_api_key:
        _gemini_tts = GeminiTTS(settings.gemini_api_key, settings.gemini_tts_model,
                                settings.gemini_tts_voice, settings.gemini_tts_style,
                                settings.gemini_tts_fallback_model)
    return _gemini_tts


def get_tts() -> Any:
    """Gemini (then ElevenLabs as backup), ElevenLabs, Azure, or None (the page uses device voices)."""
    global _tts
    settings = get_settings()
    if _tts is not None:
        return _tts
    eleven = (ElevenLabsTTS(settings.elevenlabs_api_key, settings.elevenlabs_voice_id,
                            settings.elevenlabs_model) if settings.elevenlabs_api_key else None)
    gemini = get_gemini_tts()
    if gemini:
        _tts = ChainTTS([gemini, eleven] if eleven else [gemini])
    elif eleven:
        _tts = eleven
    elif settings.azure_speech_key and settings.azure_speech_region:
        _tts = AzureTTS(settings.azure_speech_key, settings.azure_speech_region,
                        settings.azure_urdu_voice, settings.azure_english_voice,
                        settings.azure_speech_rate)
    return _tts


def tts_scope() -> str:
    """Which sentences the page should send to the server voice: 'urdu' or 'all'."""
    settings = get_settings()
    if settings.gemini_api_key or settings.elevenlabs_api_key:
        return "urdu" if settings.elevenlabs_scope.lower() != "all" else "all"
    return "all"


@app.get("/api/config")
async def config():
    """Which server-side speech features are available, so the page can pick the best path."""
    tts = get_tts()
    settings = get_settings()
    # The per-reply character budget saves ElevenLabs credits; Gemini's free tier counts requests.
    budget = (settings.elevenlabs_reply_char_budget
              if settings.elevenlabs_api_key and not settings.gemini_api_key else None)
    return {"tts": tts is not None, "tts_scope": tts_scope() if tts else None,
            "tts_reply_budget": budget, "stt": bool(settings.groq_api_key),
            "english_voices": list(GROQ_ENGLISH_VOICES) if settings.groq_api_key else [],
            "gemini_voices": ([{"name": n, "desc": d} for n, d in GEMINI_ENGLISH_VOICES]
                              if settings.gemini_api_key else [])}


@app.get("/api/tts/usage")
async def tts_usage():
    """Remaining ElevenLabs characters this month, if the key may read it."""
    tts = get_tts()
    usage = await tts.usage() if tts is not None and hasattr(tts, "usage") else None
    return {"usage": usage}


@app.post("/api/tts")
async def speak(req: SpeakRequest):
    """Text-to-speech with Azure (Urdu or English voice chosen from the text). Returns MP3."""
    tts = get_tts()
    if tts is None:
        return JSONResponse({"detail": "Server voice needs GEMINI_API_KEY or ELEVENLABS_API_KEY (or Azure Speech settings)."},
                            status_code=501)
    try:
        if hasattr(tts, "synthesize_with_type"):
            audio, media_type = await tts.synthesize_with_type(req.text)
        else:
            audio, media_type = await tts.synthesize(req.text), getattr(tts, "media_type", "audio/mpeg")
    except SpeechError as exc:
        logging.getLogger("tutor.tts").warning("TTS failed: %s", exc)
        headers = {"Retry-After": str(max(1, round(exc.retry_after)))} if exc.retry_after else None
        return JSONResponse({"detail": str(exc)}, status_code=exc.status, headers=headers)
    return Response(content=audio, media_type=media_type, headers={"Cache-Control": "no-store"})


_groq_tts: GroqTTS | None = None


def get_groq_tts() -> GroqTTS | None:
    global _groq_tts
    settings = get_settings()
    if _groq_tts is None and settings.groq_api_key:
        _groq_tts = GroqTTS(settings.groq_api_key, settings.groq_tts_model)
    return _groq_tts


@app.post("/api/tts/english")
async def speak_english(req: EnglishSpeakRequest):
    """Natural English voice chosen in the page's voice settings: "gemini:<Voice>" or "groq:<voice>"
    (a bare name means Groq). A Groq voice that fails (e.g. free limit) falls back to Gemini. Returns WAV."""
    settings = get_settings()
    provider, _, name = req.voice.rpartition(":")
    provider = provider or "groq"
    groq_tts, gemini = get_groq_tts(), get_gemini_tts()
    log = logging.getLogger("tutor.tts")
    try:
        if provider == "gemini":
            if gemini is None:
                return JSONResponse({"detail": "Gemini voices need GEMINI_API_KEY."}, status_code=501)
            audio = await gemini.synthesize(req.text, name, settings.gemini_tts_english_style)
        elif provider == "groq":
            if groq_tts is None:
                return JSONResponse({"detail": "English voices need GROQ_API_KEY."}, status_code=501)
            try:
                audio = await groq_tts.synthesize(req.text, name)
            except SpeechError as exc:
                if gemini is None or exc.status == 400:
                    raise
                log.warning("Groq TTS failed (%s); using Gemini", exc)
                audio = await gemini.synthesize(req.text, settings.gemini_tts_english_voice,
                                                settings.gemini_tts_english_style)
        else:
            return JSONResponse({"detail": f"Unknown voice '{req.voice}'."}, status_code=400)
    except SpeechError as exc:
        log.warning("English TTS failed: %s", exc)
        headers = {"Retry-After": str(max(1, round(exc.retry_after)))} if exc.retry_after else None
        return JSONResponse({"detail": str(exc)}, status_code=exc.status, headers=headers)
    return Response(content=audio, media_type="audio/wav", headers={"Cache-Control": "no-store"})


@app.get("/api/library")
async def library():
    """The built-in study library: topics, key points, commands and Q&A with model answers."""
    return {"topics": knowledge.catalog()}


@app.get("/api/progress")
async def progress(request: Request):
    memory: Memory = get_agent(request).memory
    return {
        "subjects": SUBJECTS,
        "levels": LEVELS,
        "progress": memory.get_progress(),
        "current_lesson": memory.get_current_lesson(),
        "notes": memory.get_notes(limit=10),
        "english_corrections": memory.get_english_corrections(limit=10),
    }


@app.get("/api/history")
async def history(request: Request, session_id: str = "default"):
    """Visible transcript (text only) for restoring the UI after a reload."""
    return {"messages": get_agent(request).visible_history(session_id)}


@app.post("/api/reset")
async def reset(req: ResetRequest, request: Request):
    get_agent(request).reset(req.session_id)
    return {"ok": True}
