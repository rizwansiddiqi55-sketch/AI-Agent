import json

from fastapi.testclient import TestClient

from app.agent import TutorAgent
from app.config import Settings
from app.main import app
from tests.conftest import Block, FakeClient


def make_client(memory, script):
    app.state.agent = TutorAgent(FakeClient(script), Settings(anthropic_api_key="test"), memory, "SYS")
    return TestClient(app)


def test_index_and_modes(memory):
    client = make_client(memory, [])
    assert "AI Voice Tutor" in client.get("/").text
    assert client.get("/api/modes").json()["continue"] == "Continue"


def test_chat_streams_sse_and_history(memory):
    client = make_client(memory, [(["Hello ", "Rizwan."], [Block(type="text", text="Hello Rizwan.")], "end_turn")])
    res = client.post("/api/chat", json={"text": "Salam", "session_id": "t1"})
    events = [json.loads(line[5:]) for line in res.text.split("\n\n") if line.startswith("data:")]
    assert "".join(e["text"] for e in events if e["type"] == "text") == "Hello Rizwan."
    assert events[-1]["type"] == "done"

    hist = client.get("/api/history", params={"session_id": "t1"}).json()["messages"]
    assert hist == [{"role": "user", "text": "Salam"}, {"role": "assistant", "text": "Hello Rizwan."}]

    assert client.post("/api/reset", json={"session_id": "t1"}).json() == {"ok": True}
    assert client.get("/api/history", params={"session_id": "t1"}).json()["messages"] == []


def test_progress_endpoint(memory):
    memory.update_progress("English", "Present perfect", "Developing", "corrected 3 sentences")
    client = make_client(memory, [])
    data = client.get("/api/progress").json()
    assert data["progress"][0]["topic"] == "Present perfect"
    assert "Mastered" in data["levels"]
