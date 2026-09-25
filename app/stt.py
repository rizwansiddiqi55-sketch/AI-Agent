"""Server-side speech-to-text with Groq Whisper (works on every browser, incl. iPhone Safari)."""

import groq

MAX_AUDIO_BYTES = 4 * 1024 * 1024  # stays under Vercel's 4.5 MB request body limit

# Helps Whisper spell technical terms correctly
VOCAB_PROMPT = (
    "Networking and AI tutoring. Terms: OSPF, BGP, EIGRP, VLAN, STP, HSRP, NAT, ACL, IPsec, "
    "SD-WAN, Cisco, Catalyst, ISE, Fortinet, FortiGate, Palo Alto, subnet, Python, Netmiko, "
    "RAG, LLM, API, Groq."
)

_EXTENSIONS = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mp4": "m4a",
    "audio/x-m4a": "m4a",
    "audio/aac": "m4a",
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
}

LANGUAGES = {"en", "ur"}


class TranscriptionError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def audio_filename(content_type: str) -> str:
    base = (content_type or "").split(";")[0].strip().lower()
    return f"speech.{_EXTENSIONS.get(base, 'webm')}"


async def transcribe(client: groq.AsyncGroq, audio: bytes, content_type: str,
                     language: str | None, model: str) -> str:
    if not audio:
        raise TranscriptionError("No audio received.")
    if len(audio) > MAX_AUDIO_BYTES:
        raise TranscriptionError("Recording is too long. Keep it under about a minute.", 413)
    kwargs = {}
    if language in LANGUAGES:
        kwargs["language"] = language
    try:
        result = await client.audio.transcriptions.create(
            model=model,
            file=(audio_filename(content_type), audio),
            prompt=VOCAB_PROMPT,
            response_format="json",
            temperature=0.0,
            **kwargs,
        )
    except groq.AuthenticationError as exc:
        raise TranscriptionError("Groq API key is missing or invalid. Set GROQ_API_KEY.", 502) from exc
    except groq.RateLimitError as exc:
        raise TranscriptionError("Speech recognition rate limit reached. Wait a moment.", 429) from exc
    except groq.APIStatusError as exc:
        body = exc.body if isinstance(exc.body, dict) else {}
        err = body.get("error") if isinstance(body.get("error"), dict) else {}
        raise TranscriptionError(f"Speech recognition failed: {err.get('message') or exc.status_code}",
                                 502) from exc
    except groq.APIConnectionError as exc:
        raise TranscriptionError("Could not reach Groq for speech recognition.", 502) from exc
    return (result.text or "").strip()
