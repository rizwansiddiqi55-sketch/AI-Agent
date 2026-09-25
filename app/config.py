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
