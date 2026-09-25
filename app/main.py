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
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import TutorAgent, load_system_prompt
from .groq_agent import GroqTutorAgent
from .config import ON_VERCEL, get_settings
from .memory import LEVELS, SUBJECTS, Memory
from .modes import MODES

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def create_agent() -> Any:
    settings = get_settings()
    log = logging.getLogger("tutor")
    memory = Memory(settings.memory_target)
    system_prompt = load_system_prompt(settings.system_prompt_path)
    if settings.llm_provider == "anthropic":
        if not (settings.anthropic_api_key or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            log.warning("ANTHROPIC_API_KEY is not set.")
        # api_key=None lets the SDK fall back to its own credential resolution
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        return TutorAgent(client, settings, memory, system_prompt)
    if not settings.groq_api_key:
        log.warning("GROQ_API_KEY is not set. Copy .env.example to .env and add your key.")
    # A placeholder key lets the app start; requests then fail with a clear auth error.
    groq_client = groq.AsyncGroq(api_key=settings.groq_api_key or "missing-key")
    return GroqTutorAgent(groq_client, settings, memory, system_prompt)


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


class ResetRequest(BaseModel):
    session_id: str = Field(default="default", max_length=64)


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


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
