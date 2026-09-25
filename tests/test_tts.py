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
