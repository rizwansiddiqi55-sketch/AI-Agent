import asyncio

import httpx
import pytest

from app.tts import AzureTTS, SpeechError, build_ssml, is_urdu


def test_is_urdu():
    assert is_urdu("او ایس پی ایف ایک روٹنگ پروٹوکول ہے۔")
    assert is_urdu("OSPF ایک link-state پروٹوکول ہے جو بہترین راستہ نکالتا ہے")
    assert not is_urdu("OSPF is a link-state protocol.")
    assert not is_urdu("OSPF ek routing protocol hai")  # Roman Urdu -> English voice


def test_ssml_escapes_text():
    ssml = build_ssml("R1 < R2 & 'BGP'", "ur-PK-AsadNeural", "ur-PK", "0%")
    assert "R1 &lt; R2 &amp; 'BGP'" in ssml
    assert "<voice name='ur-PK-AsadNeural'>" in ssml and "xml:lang='ur-PK'" in ssml


def make_tts(handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return AzureTTS("key123", "eastus", "ur-PK-AsadNeural", "en-US-AndrewNeural", "-5%", client=client)


def test_synthesize_picks_voice_and_sends_ssml():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        seen["body"] = request.content.decode()
        return httpx.Response(200, content=b"ID3mp3data", headers={"content-type": "audio/mpeg"})

    tts = make_tts(handler)
    audio = asyncio.run(tts.synthesize("یہ ایک ٹیسٹ ہے"))
    assert audio == b"ID3mp3data"
    assert seen["url"] == "https://eastus.tts.speech.microsoft.com/cognitiveservices/v1"
    assert seen["headers"]["Ocp-Apim-Subscription-Key"] == "key123"
    assert seen["headers"]["X-Microsoft-OutputFormat"] == "audio-24khz-48kbitrate-mono-mp3"
    assert "ur-PK-AsadNeural" in seen["body"] and "rate='-5%'" in seen["body"]

    asyncio.run(tts.synthesize("Hello Rizwan."))
    assert "en-US-AndrewNeural" in seen["body"]


@pytest.mark.parametrize("status,fragment", [(401, "invalid"), (429, "rate limit"), (500, "error 500")])
def test_synthesize_errors(status, fragment):
    tts = make_tts(lambda r: httpx.Response(status, text="nope"))
    with pytest.raises(SpeechError) as exc:
        asyncio.run(tts.synthesize("hello"))
    assert fragment in str(exc.value)


def test_empty_text_rejected():
    tts = make_tts(lambda r: httpx.Response(200))
    with pytest.raises(SpeechError):
        asyncio.run(tts.synthesize("   "))


from app.tts import ElevenLabsTTS


def make_eleven(handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ElevenLabsTTS("xi-key", "voice123", "eleven_v3", client=client, retry_delay=0)


def test_elevenlabs_request():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["key"] = request.headers["xi-api-key"]
        seen["body"] = __import__("json").loads(request.content)
        return httpx.Response(200, content=b"ID3mp3", headers={"content-type": "audio/mpeg"})

    audio = asyncio.run(make_eleven(handler).synthesize("  او ایس پی ایف ایک پروٹوکول ہے۔  "))
    assert audio == b"ID3mp3"
    assert seen["url"] == "https://api.elevenlabs.io/v1/text-to-speech/voice123?output_format=mp3_44100_64"
    assert seen["key"] == "xi-key"
    assert seen["body"] == {"text": "او ایس پی ایف ایک پروٹوکول ہے۔", "model_id": "eleven_v3"}


@pytest.mark.parametrize("status,body,fragment,code", [
    (401, {"detail": {"status": "quota_exceeded", "message": "This request exceeds your quota"}}, "quota", 429),
    (401, {"detail": {"status": "invalid_api_key", "message": "Invalid API key"}}, "invalid", 502),
    (429, {"detail": {"status": "too_many_concurrent_requests"}}, "busy", 429),
    (400, {"detail": {"status": "invalid_model", "message": "Model not found"}}, "Model not found", 502),
])
def test_elevenlabs_errors(status, body, fragment, code):
    tts = make_eleven(lambda r: httpx.Response(status, json=body))
    with pytest.raises(SpeechError) as exc:
        asyncio.run(tts.synthesize("hello"))
    assert fragment in str(exc.value) and exc.value.status == code


def test_elevenlabs_falls_back_to_default_voice_on_free_plan():
    from app.tts import ELEVENLABS_DEFAULT_VOICE

    urls = []

    def handler(request):
        urls.append(request.url.path)
        if "libraryvoice" in request.url.path:
            return httpx.Response(402, json={"detail": {"status": "payment_required", "message":
                "Free users cannot use library voices via the API. Please upgrade your subscription to use this voice."}})
        return httpx.Response(200, content=b"ID3ok")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tts = ElevenLabsTTS("xi", "libraryvoice", "eleven_v3", client=client, retry_delay=0)
    assert asyncio.run(tts.synthesize("سلام")) == b"ID3ok"
    assert urls == ["/v1/text-to-speech/libraryvoice", f"/v1/text-to-speech/{ELEVENLABS_DEFAULT_VOICE}"]
    # Remembered: the next sentence goes straight to the default voice
    asyncio.run(tts.synthesize("شکریہ"))
    assert urls[-1] == f"/v1/text-to-speech/{ELEVENLABS_DEFAULT_VOICE}" and len(urls) == 3


def test_elevenlabs_default_voice_refused_raises():
    from app.tts import ELEVENLABS_DEFAULT_VOICE

    client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(402, json={"detail": {"message": "Payment required"}})))
    tts = ElevenLabsTTS("xi", ELEVENLABS_DEFAULT_VOICE, "eleven_v3", client=client, retry_delay=0)
    with pytest.raises(SpeechError) as exc:
        asyncio.run(tts.synthesize("سلام"))
    assert "Payment required" in str(exc.value)


def test_elevenlabs_limits_concurrency_to_two():
    active = {"now": 0, "max": 0}

    async def handler(request):
        active["now"] += 1
        active["max"] = max(active["max"], active["now"])
        await asyncio.sleep(0.02)
        active["now"] -= 1
        return httpx.Response(200, content=b"ID3")

    async def run_many():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        tts = ElevenLabsTTS("xi", "v", "eleven_v3", client=client, retry_delay=0)
        return await asyncio.gather(*(tts.synthesize(f"جملہ {i}") for i in range(8)))

    results = asyncio.run(run_many())
    assert len(results) == 8 and active["max"] == 2


def test_elevenlabs_busy_is_retried():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(429, json={"detail": {"status": "too_many_concurrent_requests"}})
        return httpx.Response(200, content=b"ID3")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tts = ElevenLabsTTS("xi", "v", "eleven_v3", client=client, retry_delay=0)
    assert asyncio.run(tts.synthesize("سلام")) == b"ID3" and len(calls) == 3


def test_elevenlabs_usage():
    def handler(request):
        assert request.url.path == "/v1/user/subscription"
        return httpx.Response(200, json={"character_count": 2600, "character_limit": 10000,
                                         "next_character_count_reset_unix": 1790000000})

    tts = make_eleven(handler)
    assert asyncio.run(tts.usage()) == {"used": 2600, "limit": 10000, "left": 7400, "resets_at": 1790000000}
    denied = make_eleven(lambda r: httpx.Response(401, json={"detail": {"status": "missing_permissions"}}))
    assert asyncio.run(denied.usage()) is None


# ---- Groq English voices (Orpheus) ----
import io
import json
import wave

from app.tts import GroqTTS, join_wavs, split_for_tts


def _wav(frames: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(frames)
    return buf.getvalue()


def test_split_for_tts_respects_limit_and_sentences():
    text = "OSPF is a link-state protocol. " * 12
    parts = split_for_tts(text)
    assert len(parts) > 1 and all(len(p) <= 200 for p in parts)
    assert all(p.endswith(".") for p in parts)
    assert " ".join(parts) == " ".join(text.split())
    assert split_for_tts("x" * 450) == ["x" * 200, "x" * 200, "x" * 50]
    assert split_for_tts("  ") == []


def test_join_wavs_concatenates_frames():
    joined = join_wavs([_wav(b"\x01\x00" * 10), _wav(b"\x02\x00" * 5)])
    with wave.open(io.BytesIO(joined), "rb") as r:
        assert r.getnframes() == 15 and r.getframerate() == 24000


def test_groq_tts_splits_long_text_and_joins_audio():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        assert request.headers["Authorization"] == "Bearer gk"
        return httpx.Response(200, content=_wav(b"\x00\x00" * 4))

    tts = GroqTTS("gk", "canopylabs/orpheus-v1-english",
                  client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    audio = asyncio.run(tts.synthesize("Hello Rizwan. " * 30, "troy"))
    assert len(bodies) > 1 and all(len(b["input"]) <= 200 for b in bodies)
    assert bodies[0]["voice"] == "troy" and bodies[0]["response_format"] == "wav"
    with wave.open(io.BytesIO(audio), "rb") as r:
        assert r.getnframes() == 4 * len(bodies)


def test_groq_tts_errors():
    def terms(request):
        return httpx.Response(400, json={"error": {"message": "The model requires terms acceptance"}})

    tts = GroqTTS("gk", "m", client=httpx.AsyncClient(transport=httpx.MockTransport(terms)))
    with pytest.raises(SpeechError) as exc:
        asyncio.run(tts.synthesize("hi", "troy"))
    assert exc.value.status == 403 and "terms" in str(exc.value)

    with pytest.raises(SpeechError) as exc:
        asyncio.run(tts.synthesize("hi", "nobody"))
    assert exc.value.status == 400

    tts = GroqTTS("gk", "m", client=httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(429, json={}))))
    with pytest.raises(SpeechError) as exc:
        asyncio.run(tts.synthesize("hi", "troy"))
    assert exc.value.status == 429


def test_groq_retry_after_parsing():
    from app.tts import groq_retry_after

    def res(headers=None):
        return httpx.Response(429, headers=headers or {})

    assert groq_retry_after(res({"retry-after": "7"}), "") == 7
    assert groq_retry_after(res(), "Please try again in 6s. Need more tokens?") == 6
    assert groq_retry_after(res(), "Please try again in 1m30.5s.") == 90.5
    assert groq_retry_after(res(), "Please try again in 2h3m") == 2 * 3600 + 180
    assert groq_retry_after(res(), "Please try again in 450ms") == 0.45
    assert groq_retry_after(res(), "rate limited") is None


def test_groq_tts_waits_briefly_on_rate_limit_then_succeeds():
    calls, slept = [], []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, json={"error": {"message": "Please try again in 6s."}})
        return httpx.Response(200, content=_wav(b"\x00\x00"))

    async def fake_sleep(s):
        slept.append(s)

    tts = GroqTTS("gk", "m", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                  sleep=fake_sleep)
    assert asyncio.run(tts.synthesize("hi", "troy"))
    assert len(calls) == 2 and slept and slept[0] >= 6


def test_groq_tts_long_limit_returns_retry_after():
    tts = GroqTTS("gk", "m", client=httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(429, json={"error": {"message": "Please try again in 2m0s."}}))))
    with pytest.raises(SpeechError) as exc:
        asyncio.run(tts.synthesize("hi", "troy"))
    assert exc.value.status == 429 and exc.value.retry_after == 120


def test_groq_tts_stays_under_requests_per_minute():
    now = [0.0]
    slept = []

    async def fake_sleep(s):
        slept.append(s)
        now[0] += s

    tts = GroqTTS("gk", "m", rpm=2, clock=lambda: now[0], sleep=fake_sleep,
                  client=httpx.AsyncClient(transport=httpx.MockTransport(
                      lambda r: httpx.Response(200, content=_wav(b"\x00\x00")))))

    async def run():
        await tts.synthesize("one", "troy")
        now[0] = 55.0
        await tts.synthesize("two", "troy")
        await tts.synthesize("three", "troy")  # 3rd in the minute: waits ~5s for a free slot
        now[0] = 58.0
        with pytest.raises(SpeechError) as exc:  # next free slot is >8s away: don't block
            await tts.synthesize("four", "troy")
        return exc.value

    err = asyncio.run(run())
    assert slept == [5.0]
    assert err.status == 429 and err.retry_after > 8
