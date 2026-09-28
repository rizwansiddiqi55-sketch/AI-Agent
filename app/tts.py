"""Server-side text-to-speech: ElevenLabs (Eleven v3, supports Urdu) or Azure Speech neural voices."""

import asyncio
import logging
import re
from xml.sax.saxutils import escape

import httpx

MAX_TTS_CHARS = 1500
log = logging.getLogger("tutor.tts")
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


# "George": a premade (default) voice. Free ElevenLabs plans can use premade voices via the API,
# but not voices added from the Voice Library.
ELEVENLABS_DEFAULT_VOICE = "JBFqnCBsd6RMkjVDRZzb"


class ElevenLabsTTS:
    """ElevenLabs text-to-speech. Urdu needs the Eleven v3 model (older models don't support Urdu)."""

    # Free plan allows only a few simultaneous requests; queue the rest and retry when busy.
    MAX_CONCURRENT = 2
    BUSY_RETRIES = 3

    def __init__(self, api_key: str, voice_id: str, model: str,
                 client: httpx.AsyncClient | None = None, retry_delay: float = 0.7):
        self.api_key = api_key
        self.voice_id = voice_id
        self.model = model
        self.client = client or httpx.AsyncClient(timeout=30)
        self.retry_delay = retry_delay
        self._slots = asyncio.Semaphore(self.MAX_CONCURRENT)

    async def synthesize(self, text: str) -> bytes:
        text = text.strip()
        if not text:
            raise SpeechError("No text to speak.", 400)
        text = text[:MAX_TTS_CHARS]
        async with self._slots:
            return await self._synthesize_with_retries(text)

    async def _synthesize_with_retries(self, text: str) -> bytes:
        for attempt in range(self.BUSY_RETRIES + 1):
            try:
                return await self._synthesize_any_voice(text)
            except _Busy:
                if attempt == self.BUSY_RETRIES:
                    raise SpeechError("ElevenLabs is busy (too many requests at once). Try again.", 429)
                await asyncio.sleep(self.retry_delay * (2 ** attempt))
        raise AssertionError("unreachable")

    async def _synthesize_any_voice(self, text: str) -> bytes:
        try:
            return await self._synthesize(text, self.voice_id)
        except _VoiceNotAllowed as exc:
            if self.voice_id == ELEVENLABS_DEFAULT_VOICE:
                raise SpeechError(f"ElevenLabs: {exc}") from exc
            # Free plan + Voice Library voice: switch to the premade default voice for good.
            log.warning("ElevenLabs voice %s not allowed on this plan (%s); using default voice",
                        self.voice_id, exc)
            self.voice_id = ELEVENLABS_DEFAULT_VOICE
            return await self._synthesize(text, self.voice_id)

    async def _synthesize(self, text: str, voice_id: str) -> bytes:
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        try:
            res = await self.client.post(
                url,
                params={"output_format": "mp3_44100_64"},
                json={"text": text, "model_id": self.model},
                headers={"xi-api-key": self.api_key, "Accept": "audio/mpeg"},
            )
        except httpx.HTTPError as exc:
            raise SpeechError(f"Could not reach ElevenLabs: {exc.__class__.__name__}") from exc
        if res.status_code == 200:
            return res.content
        detail = _elevenlabs_detail(res)
        status = detail.get("status", "")
        if status == "quota_exceeded" or "quota" in status:
            raise SpeechError("ElevenLabs free monthly character quota is used up.", 429)
        if res.status_code == 401:
            raise SpeechError("ElevenLabs API key is invalid (ELEVENLABS_API_KEY).")
        if res.status_code == 429:
            raise _Busy(status or "rate_limited")
        message = detail.get("message") or res.text[:200]
        if res.status_code == 402 or (res.status_code in (400, 404) and "voice" in message.lower()):
            raise _VoiceNotAllowed(message)
        raise SpeechError(f"ElevenLabs error {res.status_code}: {message}")


class _Busy(Exception):
    """Too many concurrent requests / system busy: worth a short retry."""


class _VoiceNotAllowed(Exception):
    """The configured voice can't be used (e.g. a Voice Library voice on the free plan)."""


def _elevenlabs_detail(res: httpx.Response) -> dict:
    try:
        body = res.json()
    except ValueError:
        return {}
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict):
        return detail
    if isinstance(detail, str):
        return {"message": detail}
    return {}
