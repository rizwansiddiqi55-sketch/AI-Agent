from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", extra="ignore")

    anthropic_api_key: str | None = None
    claude_model: str = "claude-opus-5"
    effort: str = "medium"
    max_tokens: int = 16000
    enable_fallback: bool = True
    db_path: str = str(ROOT_DIR / "tutor.db")
    max_history_messages: int = 80
    max_tool_rounds: int = 8
    system_prompt_path: str = str(ROOT_DIR / "prompts" / "master_system_prompt.md")


@lru_cache
def get_settings() -> Settings:
    return Settings()
