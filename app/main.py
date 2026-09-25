"""FastAPI entry point: serves the voice UI and the streaming chat API."""

import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import anthropic
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import TutorAgent, load_system_prompt
from .config import get_settings
from .memory import LEVELS, SUBJECTS, Memory
from .modes import MODES

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def create_agent() -> TutorAgent:
    settings = get_settings()
    if not (settings.anthropic_api_key or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        logging.getLogger("tutor").warning(
            "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key.")
    memory = Memory(settings.db_path)
    # api_key=None lets the SDK fall back to its own credential resolution
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    return TutorAgent(client, settings, memory, load_system_prompt(settings.system_prompt_path))


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not hasattr(app.state, "agent"):
        app.state.agent = create_agent()
    yield


app = FastAPI(title="Rizwan's AI Voice Tutor", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


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


@app.get("/api/modes")
async def modes():
    return {key: m["label"] for key, m in MODES.items()}


@app.post("/api/chat")
async def chat(req: ChatRequest, request: Request):
    agent: TutorAgent = request.app.state.agent

    async def event_stream():
        async for event in agent.run_turn(req.session_id, req.text, req.mode, req.urdu_voice):
            if await request.is_disconnected():
                break
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/progress")
async def progress(request: Request):
    memory: Memory = request.app.state.agent.memory
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
    memory: Memory = request.app.state.agent.memory
    items = []
    for m in memory.get_history(session_id):
        texts = [b["text"] for b in m["content"] if isinstance(b, dict) and b.get("type") == "text"]
        if m["role"] == "user" and texts and texts[0].startswith("<session_context>"):
            texts = texts[1:]
        if texts:
            items.append({"role": m["role"], "text": "\n".join(texts)})
    return {"messages": items}


@app.post("/api/reset")
async def reset(req: ResetRequest, request: Request):
    request.app.state.agent.memory.clear_history(req.session_id)
    return {"ok": True}
