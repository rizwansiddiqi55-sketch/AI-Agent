import os
from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent
ON_VERCEL = bool(os.environ.get("VERCEL"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", extra="ignore")

    # "groq" (default) or "anthropic"
    llm_provider: str = "groq"

    # Groq
    groq_api_key: str | None = None
    groq_model: str = "openai/gpt-oss-120b"
    # low | medium | high (only sent to reasoning models such as gpt-oss / qwen3)
    groq_reasoning_effort: str | None = "low"
    # Output cap per reply (includes hidden reasoning). Voice replies are short.
    groq_max_tokens: int = 2048
    # Recent conversation sent per request, in characters (~4 chars per token). Keeps requests
    # inside Groq's free-tier tokens-per-minute limit.
    groq_history_chars: int = 6000
    # Wait for a Groq rate limit if it clears within this many seconds; otherwise show the error.
    groq_max_rate_limit_wait: float = 30
    # Groq uses a condensed prompt to stay within free-tier limits; Claude uses the full one.
    groq_system_prompt_path: str = str(ROOT_DIR / "prompts" / "compact_system_prompt.md")
    # Whisper model for server-side speech recognition (needs GROQ_API_KEY, any LLM provider)
    groq_stt_model: str = "whisper-large-v3"
    # Natural English voices (Troy, Austin, Daniel, Autumn, Diana, Hannah); needs GROQ_API_KEY.
    groq_tts_model: str = "canopylabs/orpheus-v1-english"

    # ElevenLabs text-to-speech (used instead of Azure when a key is set).
    elevenlabs_api_key: str | None = None
    # Default: "George", a premade multilingual voice available in every account. Replace with
    # any voice ID from your ElevenLabs voice library (e.g. a Pakistani Urdu voice).
    elevenlabs_voice_id: str = "JBFqnCBsd6RMkjVDRZzb"
    # Eleven v3 is required for Urdu.
    elevenlabs_model: str = "eleven_v3"
    # "urdu": only Urdu sentences use ElevenLabs (saves the free quota; English uses the device
    # voice). "all": every sentence uses ElevenLabs.
    elevenlabs_scope: str = "urdu"
    # Max characters of one reply sent to ElevenLabs; the rest stays on screen (saves credits).
    elevenlabs_reply_char_budget: int = 350

    # Azure Speech text-to-speech (natural Urdu voice). Both key and region are needed.
    azure_speech_key: str | None = None
    azure_speech_region: str | None = None
    azure_urdu_voice: str = "ur-PK-AsadNeural"      # or ur-PK-UzmaNeural (female)
    azure_english_voice: str = "en-US-AndrewNeural"  # e.g. en-US-AvaNeural, en-GB-RyanNeural
    azure_speech_rate: str = "0%"                    # e.g. "-10%" to speak a little slower

    # Anthropic (Claude)
    anthropic_api_key: str | None = None
    claude_model: str = "claude-opus-5"
    effort: str = "medium"
    max_tokens: int = 16000
    enable_fallback: bool = True
    # Postgres URL (e.g. Supabase transaction pooler or Neon). Takes precedence over db_path.
    database_url: str | None = Field(
        default=None, validation_alias=AliasChoices("DATABASE_URL", "POSTGRES_URL")
    )
    # SQLite fallback. Only /tmp is writable on Vercel (and it is not persistent there).
    db_path: str = "/tmp/tutor.db" if ON_VERCEL else str(ROOT_DIR / "tutor.db")
    # When set, every /api request must send this passcode in the X-App-Passcode header.
    app_passcode: str | None = None
    max_history_messages: int = 80
    max_tool_rounds: int = 8
    system_prompt_path: str = str(ROOT_DIR / "prompts" / "master_system_prompt.md")

    @property
    def memory_target(self) -> str:
        return self.database_url or self.db_path


@lru_cache
def get_settings() -> Settings:
    return Settings()
