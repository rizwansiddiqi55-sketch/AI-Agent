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


def test_passcode_required_when_configured(memory, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("APP_PASSCODE", "s3cret")
    get_settings.cache_clear()
    try:
        client = make_client(memory, [])
        assert client.get("/").status_code == 200  # the page itself stays public
        assert client.get("/api/auth").status_code == 401
        assert client.get("/api/modes", headers={"X-App-Passcode": "wrong"}).status_code == 401
        assert client.get("/api/auth", headers={"X-App-Passcode": "s3cret"}).json() == {"ok": True}
    finally:
        get_settings.cache_clear()


def test_api_locked_on_vercel_without_passcode(memory, monkeypatch):
    import app.main as main

    monkeypatch.setattr(main, "ON_VERCEL", True)
    client = make_client(memory, [])
    res = client.get("/api/modes")
    assert res.status_code == 503 and "APP_PASSCODE" in res.json()["detail"]


def test_create_agent_picks_provider(tmp_path, monkeypatch):
    from app.config import get_settings
    from app.groq_agent import GroqTutorAgent
    from app.main import create_agent

    monkeypatch.setenv("DB_PATH", str(tmp_path / "p.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    try:
        get_settings.cache_clear()
        assert isinstance(create_agent(), GroqTutorAgent)
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
        get_settings.cache_clear()
        assert isinstance(create_agent(), TutorAgent)
    finally:
        get_settings.cache_clear()


class FakeTranscriptions:
    def __init__(self):
        self.calls = []

    async def create(self, **kwargs):
        from types import SimpleNamespace
        self.calls.append(kwargs)
        return SimpleNamespace(text="  OSPF mujhe samjhao  ")


def test_transcribe_endpoint(memory, monkeypatch):
    from types import SimpleNamespace

    import app.main as main
    from app.config import get_settings

    fake = FakeTranscriptions()
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    get_settings.cache_clear()
    monkeypatch.setattr(main, "_stt_client", SimpleNamespace(audio=SimpleNamespace(transcriptions=fake)))
    try:
        client = make_client(memory, [])
        res = client.post("/api/transcribe?lang=ur", content=b"x" * 5000,
                          headers={"Content-Type": "audio/mp4"})
        assert res.json() == {"text": "OSPF mujhe samjhao"}
        call = fake.calls[0]
        assert call["language"] == "ur" and call["model"] == "whisper-large-v3"
        assert call["file"][0] == "speech.m4a" and len(call["file"][1]) == 5000
        # unknown language is not forwarded; empty audio rejected; oversized audio rejected
        client.post("/api/transcribe?lang=xx", content=b"x" * 10, headers={"Content-Type": "audio/webm"})
        assert "language" not in fake.calls[1]
        assert client.post("/api/transcribe", content=b"").status_code == 400
        assert client.post("/api/transcribe", content=b"x" * (4 * 1024 * 1024 + 1)).status_code == 413
    finally:
        get_settings.cache_clear()


def test_transcribe_without_groq_key(memory, monkeypatch):
    from app.config import get_settings

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    get_settings.cache_clear()
    try:
        client = make_client(memory, [])
        assert client.post("/api/transcribe", content=b"x" * 5000).status_code == 501
    finally:
        get_settings.cache_clear()


def test_tts_and_config_endpoints(memory, monkeypatch):
    import app.main as main
    from app.config import get_settings

    class FakeTTS:
        async def synthesize(self, text):
            return b"mp3:" + text.encode()

    monkeypatch.delenv("AZURE_SPEECH_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    get_settings.cache_clear()
    monkeypatch.setattr(main, "_tts", None)
    try:
        client = make_client(memory, [])
        assert client.get("/api/config").json()["tts"] is False
        assert client.post("/api/tts", json={"text": "hi"}).status_code == 501

        monkeypatch.setenv("AZURE_SPEECH_KEY", "k")
        monkeypatch.setenv("AZURE_SPEECH_REGION", "eastus")
        get_settings.cache_clear()
        monkeypatch.setattr(main, "_tts", FakeTTS())
        assert client.get("/api/config").json()["tts"] is True
        res = client.post("/api/tts", json={"text": "سلام"})
        assert res.status_code == 200 and res.headers["content-type"] == "audio/mpeg"
        assert res.content == "mp3:سلام".encode()
    finally:
        get_settings.cache_clear()


def test_english_voice_endpoint(memory, monkeypatch):
    class FakeGroqTTS:
        async def synthesize(self, text, voice):
            return b"RIFF" + f"{voice}:{text}".encode()

    from app import main
    from app.config import get_settings

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    get_settings.cache_clear()
    monkeypatch.setattr(main, "_groq_tts", None)
    try:
        client = make_client(memory, [])
        assert client.get("/api/config").json()["english_voices"] == []
        assert client.post("/api/tts/english", json={"text": "hi", "voice": "troy"}).status_code == 501

        monkeypatch.setenv("GROQ_API_KEY", "gk")
        get_settings.cache_clear()
        monkeypatch.setattr(main, "_groq_tts", FakeGroqTTS())
        assert "troy" in client.get("/api/config").json()["english_voices"]
        res = client.post("/api/tts/english", json={"text": "hi", "voice": "troy"})
        assert res.status_code == 200 and res.headers["content-type"] == "audio/wav"
        assert res.content == b"RIFFtroy:hi"
    finally:
        get_settings.cache_clear()


def test_gemini_is_the_urdu_voice_with_elevenlabs_backup(memory, monkeypatch):
    from app import main
    from app.config import get_settings
    from app.tts import ChainTTS, ElevenLabsTTS, GeminiTTS

    monkeypatch.setenv("GEMINI_API_KEY", "gk")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "ek")
    get_settings.cache_clear()
    monkeypatch.setattr(main, "_tts", None)
    try:
        client = make_client(memory, [])
        cfg = client.get("/api/config").json()
        assert cfg["tts"] is True and cfg["tts_scope"] == "urdu" and cfg["tts_reply_budget"] is None
        assert isinstance(main._tts, ChainTTS)
        assert [type(e) for e in main._tts.engines] == [GeminiTTS, ElevenLabsTTS]
    finally:
        get_settings.cache_clear()


def test_library_endpoint(memory):
    client = make_client(memory, [])
    topics = client.get("/api/library").json()["topics"]
    assert len(topics) >= 20
    ospf = next(t for t in topics if t["id"] == "ospf")
    assert ospf["qa"] and ospf["key_points"] and ospf["commands"]


def test_pwa_files_are_public(memory, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("APP_PASSCODE", "s3cret")
    get_settings.cache_clear()
    try:
        client = make_client(memory, [])
        m = client.get("/manifest.webmanifest")
        assert m.status_code == 200 and m.headers["content-type"].startswith("application/manifest+json")
        assert m.json()["display"] == "standalone"
        sw = client.get("/sw.js")
        assert sw.status_code == 200 and "javascript" in sw.headers["content-type"]
        assert sw.headers["cache-control"] == "no-cache"
        assert client.get("/favicon.ico").headers["content-type"] == "image/png"
        page = client.get("/").text
        assert 'rel="manifest"' in page and "apple-touch-icon" in page
    finally:
        get_settings.cache_clear()


def test_elevenlabs_preferred_and_scope(memory, monkeypatch):
    import app.main as main
    from app.config import get_settings
    from app.tts import ElevenLabsTTS

    monkeypatch.setenv("ELEVENLABS_API_KEY", "xi")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("AZURE_SPEECH_KEY", "k")
    monkeypatch.setenv("AZURE_SPEECH_REGION", "eastus")
    get_settings.cache_clear()
    monkeypatch.setattr(main, "_tts", None)
    try:
        client = make_client(memory, [])
        cfg = client.get("/api/config").json()
        assert cfg["tts"] is True and cfg["tts_scope"] == "urdu"
        assert isinstance(main._tts, ElevenLabsTTS) and main._tts.model == "eleven_v3"
        monkeypatch.setenv("ELEVENLABS_SCOPE", "all")
        get_settings.cache_clear()
        assert client.get("/api/config").json()["tts_scope"] == "all"
    finally:
        get_settings.cache_clear()
        monkeypatch.setattr(main, "_tts", None)


def test_tts_usage_and_budget_config(memory, monkeypatch):
    import app.main as main
    from app.config import get_settings

    class FakeTTS:
        async def synthesize(self, text):
            return b""

        async def usage(self):
            return {"used": 1, "limit": 10, "left": 9, "resets_at": None}

    monkeypatch.setenv("ELEVENLABS_API_KEY", "xi")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("ELEVENLABS_REPLY_CHAR_BUDGET", "200")
    get_settings.cache_clear()
    monkeypatch.setattr(main, "_tts", FakeTTS())
    try:
        client = make_client(memory, [])
        assert client.get("/api/config").json()["tts_reply_budget"] == 200
        assert client.get("/api/tts/usage").json() == {"usage": {"used": 1, "limit": 10, "left": 9, "resets_at": None}}
    finally:
        get_settings.cache_clear()
        monkeypatch.setattr(main, "_tts", None)


def test_context_asks_for_short_urdu_when_hd_voice(memory):
    from app.agent import build_context_block

    assert "about 2-3 short sentences" in build_context_block(memory, None, urdu_voice=True)
    assert "Roman Urdu" in build_context_block(memory, None, urdu_voice=False)
