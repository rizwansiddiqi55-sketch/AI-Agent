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
