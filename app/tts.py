"""Server-side text-to-speech: ElevenLabs (Eleven v3, supports Urdu) or Azure Speech neural voices."""

import asyncio
import base64
import io
import logging
import re
import time
import wave
from collections import deque
from xml.sax.saxutils import escape

import httpx

MAX_TTS_CHARS = 1500
log = logging.getLogger("tutor.tts")
_URDU_SCRIPT = re.compile(r"[؀-ۿ]")
_LATIN = re.compile(r"[A-Za-z]")


class SpeechError(Exception):
    def __init__(self, message: str, status: int = 502, retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


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

    async def usage(self) -> dict | None:
        """Characters used/left this month (needs the key's 'User: read' permission)."""
        try:
            res = await self.client.get("https://api.elevenlabs.io/v1/user/subscription",
                                        headers={"xi-api-key": self.api_key})
        except httpx.HTTPError:
            return None
        if res.status_code != 200:
            return None
        data = res.json()
        used, limit = data.get("character_count"), data.get("character_limit")
        if not isinstance(used, int) or not isinstance(limit, int):
            return None
        return {"used": used, "limit": limit, "left": max(0, limit - used),
                "resets_at": data.get("next_character_count_reset_unix")}

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


GROQ_ENGLISH_VOICES = ("troy", "austin", "daniel", "autumn", "diana", "hannah")
GROQ_TTS_MAX_CHARS = 200  # Orpheus limit per request


def split_for_tts(text: str, limit: int = GROQ_TTS_MAX_CHARS) -> list[str]:
    """Split text into pieces of at most `limit` chars, at sentence ends, then commas, then spaces."""
    text = " ".join(text.split())
    parts: list[str] = []
    while len(text) > limit:
        window = text[: limit + 1]
        cut = -1
        for pattern in (r"[.!?]\s", r"[,;:]\s", r"\s"):
            ends = [m.end() for m in re.finditer(pattern, window)]
            if ends:
                cut = ends[-1]
                break
        if cut <= 0:
            cut = limit
        parts.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        parts.append(text)
    return parts


def join_wavs(chunks: list[bytes]) -> bytes:
    """Concatenate WAV files that share one format into a single WAV."""
    if len(chunks) == 1:
        return chunks[0]
    out = io.BytesIO()
    writer = None
    for data in chunks:
        with wave.open(io.BytesIO(data), "rb") as reader:
            if writer is None:
                writer = wave.open(out, "wb")
                writer.setparams(reader.getparams())
            writer.writeframes(reader.readframes(reader.getnframes()))
    writer.close()
    return out.getvalue()


class GroqTTS:
    """Natural English voices from Groq (Canopy Labs Orpheus), using the same GROQ_API_KEY."""

    URL = "https://api.groq.com/openai/v1/audio/speech"
    # Short rate-limit waits are worth it (the sentence still gets the natural voice); longer ones
    # are returned as 429 + Retry-After so the page uses the phone voice meanwhile.
    MAX_WAIT = 8.0

    def __init__(self, api_key: str, model: str, client: httpx.AsyncClient | None = None,
                 rpm: int = 10, clock=time.monotonic, sleep=asyncio.sleep):
        self.api_key = api_key
        self.model = model
        self.client = client or httpx.AsyncClient(timeout=30)
        self.rpm = rpm  # Groq free tier: 10 requests per minute for Orpheus
        self._sent: deque[float] = deque()
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()

    async def _take_slot(self) -> None:
        """Stay under the requests-per-minute limit (per server instance)."""
        async with self._lock:
            now = self._clock()
            while self._sent and now - self._sent[0] >= 60:
                self._sent.popleft()
            if self.rpm and len(self._sent) >= self.rpm:
                wait = 60 - (now - self._sent[0])
                if wait > self.MAX_WAIT:
                    raise SpeechError("Natural voice: free limit of 10 requests per minute reached.",
                                      429, retry_after=wait)
                await self._sleep(wait)
                self._sent.popleft()
            self._sent.append(self._clock())

    async def synthesize(self, text: str, voice: str) -> bytes:
        if voice not in GROQ_ENGLISH_VOICES:
            raise SpeechError(f"Unknown voice '{voice}'.", 400)
        parts = split_for_tts(text[:MAX_TTS_CHARS])
        if not parts:
            raise SpeechError("No text to speak.", 400)
        return join_wavs([await self._synthesize(part, voice) for part in parts])

    async def _synthesize(self, text: str, voice: str) -> bytes:
        for attempt in range(3):
            try:
                return await self._request(text, voice)
            except SpeechError as exc:
                wait = exc.retry_after
                if exc.status != 429 or wait is None or wait > self.MAX_WAIT or attempt == 2:
                    raise
                await self._sleep(wait + 0.3)
        raise AssertionError("unreachable")

    async def _request(self, text: str, voice: str) -> bytes:
        await self._take_slot()
        try:
            res = await self.client.post(
                self.URL,
                json={"model": self.model, "voice": voice, "input": text, "response_format": "wav"},
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
        except httpx.HTTPError as exc:
            raise SpeechError(f"Could not reach Groq: {exc.__class__.__name__}") from exc
        if res.status_code == 200:
            return res.content
        message = _groq_error_message(res)
        if res.status_code == 401:
            raise SpeechError("Groq API key is invalid (GROQ_API_KEY).")
        if res.status_code == 429:
            wait = groq_retry_after(res, message)
            raise SpeechError("Natural voice: Groq free limit reached"
                              + (f", try again in {wait:.0f}s." if wait else "."), 429, retry_after=wait)
        if "terms" in message.lower():
            raise SpeechError("Accept the Orpheus model terms once in the Groq console "
                              f"(Playground → {self.model}), then try again.", 403)
        raise SpeechError(f"Groq voice error {res.status_code}: {message}")


def groq_retry_after(res: httpx.Response, message: str) -> float | None:
    """Seconds to wait, from the Retry-After header or 'try again in 1h2m3.5s' in the message."""
    try:
        return float(res.headers["retry-after"])
    except (KeyError, ValueError):
        pass
    match = re.search(r"try again in (?:([\d.]+)h)?(?:([\d.]+)m(?!s))?(?:([\d.]+)s)?(?:([\d.]+)ms)?", message)
    if not match or not any(match.groups()):
        return None
    h, m, sec, ms = (float(g) if g else 0.0 for g in match.groups())
    return h * 3600 + m * 60 + sec + ms / 1000


def _groq_error_message(res: httpx.Response) -> str:
    try:
        body = res.json()
    except ValueError:
        return res.text[:200]
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])
    return res.text[:200]


def pcm_to_wav(pcm: bytes, rate: int = 24000, channels: int = 1, width: int = 2) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(pcm)
    return out.getvalue()


class GeminiTTS:
    """Google Gemini text-to-speech (supports Urdu). Free tier is request-limited, not per character."""

    media_type = "audio/wav"
    URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    MAX_WAIT = 8.0

    def __init__(self, api_key: str, model: str, voice: str, style: str = "",
                 fallback_model: str | None = None, client: httpx.AsyncClient | None = None,
                 sleep=asyncio.sleep):
        self.api_key = api_key
        self.models = [model] + ([fallback_model] if fallback_model and fallback_model != model else [])
        self.voice = voice
        self.style = style.strip()
        self.client = client or httpx.AsyncClient(timeout=60)
        self._sleep = sleep

    async def synthesize(self, text: str) -> bytes:
        text = text.strip()[:MAX_TTS_CHARS]
        if not text:
            raise SpeechError("No text to speak.", 400)
        prompt = f"{self.style}\n{text}" if self.style else text
        for attempt in range(2):
            try:
                return await self._synthesize_any_model(prompt)
            except SpeechError as exc:
                wait = exc.retry_after
                if exc.status != 429 or wait is None or wait > self.MAX_WAIT or attempt == 1:
                    raise
                await self._sleep(wait + 0.3)
        raise AssertionError("unreachable")

    async def _synthesize_any_model(self, prompt: str) -> bytes:
        while True:
            model = self.models[0]
            try:
                return await self._request(model, prompt)
            except _ModelUnavailable as exc:
                if len(self.models) == 1:
                    raise SpeechError(f"Gemini voice model unavailable: {exc}") from exc
                log.warning("Gemini TTS model %s unavailable (%s); switching to %s",
                            model, exc, self.models[1])
                self.models.pop(0)

    async def _request(self, model: str, prompt: str) -> bytes:
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": self.voice}}},
            },
        }
        try:
            res = await self.client.post(self.URL.format(model=model), json=body,
                                         headers={"x-goog-api-key": self.api_key})
        except httpx.HTTPError as exc:
            raise SpeechError(f"Could not reach Gemini: {exc.__class__.__name__}") from exc
        if res.status_code == 200:
            return _gemini_audio(res)
        error = _gemini_error(res)
        message = error.get("message") or res.text[:200]
        if res.status_code == 404:
            raise _ModelUnavailable(message)
        if res.status_code in (401, 403) or error.get("status") == "PERMISSION_DENIED" \
                or "API key not valid" in message:
            raise SpeechError("Gemini API key is invalid (GEMINI_API_KEY).")
        if res.status_code == 429:
            wait = _gemini_retry_delay(error)
            raise SpeechError("Gemini free voice limit reached"
                              + (f", try again in {wait:.0f}s." if wait else "."), 429, retry_after=wait)
        if res.status_code == 400 and ("not found" in message.lower() or "not supported" in message.lower()):
            raise _ModelUnavailable(message)
        raise SpeechError(f"Gemini voice error {res.status_code}: {message}")


class _ModelUnavailable(Exception):
    pass


def _gemini_error(res: httpx.Response) -> dict:
    try:
        body = res.json()
    except ValueError:
        return {}
    error = body.get("error") if isinstance(body, dict) else None
    return error if isinstance(error, dict) else {}


def _gemini_retry_delay(error: dict) -> float | None:
    for detail in error.get("details") or []:
        delay = detail.get("retryDelay") if isinstance(detail, dict) else None
        if isinstance(delay, str) and delay.endswith("s"):
            try:
                return float(delay[:-1])
            except ValueError:
                pass
    match = re.search(r"retry in ([\d.]+)s", error.get("message") or "")
    return float(match.group(1)) if match else None


def _gemini_audio(res: httpx.Response) -> bytes:
    try:
        parts = res.json()["candidates"][0]["content"]["parts"]
        inline = next(p["inlineData"] for p in parts if "inlineData" in p)
        pcm = base64.b64decode(inline["data"])
    except (ValueError, KeyError, IndexError, StopIteration, TypeError) as exc:
        raise SpeechError("Gemini returned no audio.") from exc
    rate = re.search(r"rate=(\d+)", inline.get("mimeType", ""))
    return pcm_to_wav(pcm, rate=int(rate.group(1)) if rate else 24000)


class ChainTTS:
    """Try each voice in order (e.g. Gemini first, then ElevenLabs when Gemini's free limit is hit)."""

    def __init__(self, engines: list):
        self.engines = engines

    @property
    def media_type(self) -> str:
        return getattr(self.engines[0], "media_type", "audio/mpeg")

    async def synthesize(self, text: str) -> bytes:
        return (await self.synthesize_with_type(text))[0]

    async def synthesize_with_type(self, text: str) -> tuple[bytes, str]:
        last: SpeechError | None = None
        for engine in self.engines:
            try:
                return await engine.synthesize(text), getattr(engine, "media_type", "audio/mpeg")
            except SpeechError as exc:
                if exc.status == 400:
                    raise
                log.warning("%s failed (%s); trying the next voice", type(engine).__name__, exc)
                last = exc
        assert last is not None
        raise last
