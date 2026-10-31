"""Application configuration loaded from environment variables.

Why pydantic-settings instead of plain os.getenv():
- Type safety: a missing required variable fails at startup with a clear
  message, instead of failing later mid-request with a confusing 403.
- Validation: e.g. we can assert the SEC User-Agent is non-empty and looks
  like it contains a contact address.
- One typed object (Settings) passed around the app -- no global os.environ
  reads scattered through the codebase, which makes testing harder.

Usage:
    from app.config import get_settings
    settings = get_settings()          # cached; reads .env once
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ root directory (this file lives at backend/app/config.py)
BACKEND_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Typed application settings, sourced from environment variables / .env."""

    model_config = SettingsConfigDict(
        env_file=str(BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",  # tolerate unrelated env vars; do not crash on them
    )

    # --- Application ---
    app_env: str = "development"
    log_level: str = "INFO"

    # --- SEC EDGAR (no API key; identification + rate limits required) ---
    sec_user_agent: str = Field(
        ...,
        description="Descriptive User-Agent with contact info, required by SEC fair-access policy.",
    )
    sec_base_url: str = "https://data.sec.gov"
    sec_files_url: str = "https://www.sec.gov"
    # SEC allows 10 req/s; we deliberately use 8 to stay safely under it.
    # One retry triggered by a burst could otherwise push us over the limit.
    sec_requests_per_second: float = 8.0
    sec_timeout_seconds: float = 30.0
    sec_max_retries: int = 3
    sec_backoff_base_seconds: float = 0.5

    # --- Storage ---
    data_raw_dir: Path = BACKEND_ROOT / "data" / "raw"
    data_processed_dir: Path = BACKEND_ROOT / "data" / "processed"
    data_filings_dir: Path = BACKEND_ROOT / "data" / "filings"
    logs_dir: Path = BACKEND_ROOT / "logs"

    # --- LLM providers (used from M5; validated there, not here) ---
    groq_api_key: str = ""
    gemini_api_key: str = ""
    llm_model: str = "openai/gpt-oss-20b"
    fallback_llm_model: str = ""
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = ""

    # --- Database (wired in M2) ---
    database_url: str = ""

    # --- ChromaDB (wired in M4) ---
    chroma_persist_directory: Path = BACKEND_ROOT / "data" / "chroma_db"

    @field_validator("sec_user_agent")
    @classmethod
    def _validate_user_agent(cls, value: str) -> str:
        """Fail fast on obviously-bad User-Agent values.

        SEC policy requires a *descriptive* agent with contact information.
        An empty or placeholder value would get us blocked, and debugging a
        403 from SEC without this hint is painful.
        """
        value = value.strip()
        if not value:
            raise ValueError("SEC_USER_AGENT must not be empty (SEC fair-access policy).")
        if "example.com" in value.lower():
            raise ValueError(
                "SEC_USER_AGENT still contains a placeholder email. "
                "Replace it with your real contact address in backend/.env."
            )
        return value

    def ensure_directories(self) -> None:
        """Create data/log directories if missing (idempotent)."""
        for path in (
            self.data_raw_dir,
            self.data_processed_dir,
            self.data_filings_dir,
            self.logs_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    """Return the cached Settings instance.

    lru_cache ensures .env is parsed exactly once per process -- important for
    tests, which can clear the cache (get_settings.cache_clear()) to inject
    different values.
    """
    return Settings()
