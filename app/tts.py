"""Server-side text-to-speech with Azure Speech (native Pakistani Urdu neural voices)."""

import re
from xml.sax.saxutils import escape

import httpx

MAX_TTS_CHARS = 1500
_URDU_SCRIPT = re.compile(r"[؀-ۿ]")
_LATIN = re.compile(r"[A-Za-z]")


class SpeechError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def is_urdu(text: str) -> bool:
    """Urdu-script text (even with English technical terms mixed in) gets the Urdu voice."""
    urdu = len(_URDU_SCRIPT.findall(text))
    return urdu > 0 and urdu >= len(_LATIN.findall(text)) * 0.3


def build_ssml(text: str, voice: str, lang: str, rate: str) -> str:
    return (
        f"<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='{lang}'>"
        f"<voice name='{voice}'><prosody rate='{rate}'>{escape(text)}</prosody></voice></speak>"
    )


class AzureTTS:
    def __init__(self, key: str, region: str, urdu_voice: str, english_voice: str, rate: str,
                 client: httpx.AsyncClient | None = None):
        self.key = key
        self.url = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"
        self.urdu_voice = urdu_voice
        self.english_voice = english_voice
        self.rate = rate
        self.client = client or httpx.AsyncClient(timeout=20)

    def pick_voice(self, text: str) -> tuple[str, str]:
        if is_urdu(text):
            return self.urdu_voice, "ur-PK"
        return self.english_voice, "en-US"

    async def synthesize(self, text: str) -> bytes:
        text = text.strip()
        if not text:
            raise SpeechError("No text to speak.", 400)
        if len(text) > MAX_TTS_CHARS:
            text = text[:MAX_TTS_CHARS]
        voice, lang = self.pick_voice(text)
        try:
            res = await self.client.post(
                self.url,
                content=build_ssml(text, voice, lang, self.rate).encode("utf-8"),
                headers={
                    "Ocp-Apim-Subscription-Key": self.key,
                    "Content-Type": "application/ssml+xml",
                    "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
                    "User-Agent": "ai-voice-tutor",
                },
            )
        except httpx.HTTPError as exc:
            raise SpeechError(f"Could not reach Azure Speech: {exc.__class__.__name__}") from exc
        if res.status_code in (401, 403):
            raise SpeechError("Azure Speech key or region is invalid (AZURE_SPEECH_KEY / AZURE_SPEECH_REGION).")
        if res.status_code == 429:
            raise SpeechError("Azure Speech rate limit reached.", 429)
        if res.status_code != 200:
            raise SpeechError(f"Azure Speech error {res.status_code}: {res.text[:200]}")
        return res.content
