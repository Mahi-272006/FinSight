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

    # --- LLM providers (M5; free tiers only -- no paid provider code exists) ---
    groq_api_key: str = ""
    gemini_api_key: str = ""
    # llm_model = model for the PRIMARY provider in the chain (Groq default);
    # fallback_llm_model = model for the SECONDARY provider (Gemini).
    # Per-provider because the chain order is configurable, but model IDs are
    # provider-specific (Groq's are namespaced like "openai/gpt-oss-20b").
    llm_model: str = "openai/gpt-oss-20b"
    fallback_llm_model: str = ""      # empty -> code default "gemini-2.5-flash"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = ""            # required only when "ollama" is in the chain
    # Comma-separated failover chain, tried in order. Providers with no
    # credentials are skipped (logged), never silently replaced by a paid
    # alternative; an empty resulting chain raises LLMConfigurationError.
    llm_provider_chain: str = "groq,gemini"
    llm_timeout_seconds: float = 30.0
    # M6 hardening: one wait on a rate-limit error (Retry-After hint wins
    # when the message carries one, capped at 60s), one retry, then failover.
    # Sized to the observed Groq free-tier 429 ("try again in ~14s").
    llm_rate_limit_wait_seconds: float = 12.0
    # 2048, not 1024: reasoning models (gpt-oss) spend completion budget on
    # their reasoning channel too -- at 1024 the live demo answer was cut
    # mid-citation. Still bounded; still free-tier friendly.
    llm_max_tokens: int = 2048
    # Low by design: the analyst quotes evidence, it does not creative-write.
    llm_temperature: float = 0.1
    # Total retrieved-context budget for one prompt (chars). Bounds prompt
    # size so an oversized context cannot trigger provider 400s -> failover
    # storms, and defines which (lowest-ranked) chunks get truncated first.
    prompt_context_chars: int = 16_000

    # --- Research agent (M6) ---
    # Hard caps: the LLM never decides when the loop ends. An iteration is
    # one research (LLM) call; at the cap a force-stop message demands a
    # final answer, and one defensive extra call is tolerated before the
    # graph validates whatever exists.
    agent_max_iterations: int = 6
    # Validation-driven revision rounds before finalizing with limitations.
    agent_max_revises: int = 1

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

    @field_validator(
        "data_raw_dir", "data_processed_dir", "data_filings_dir",
        "logs_dir", "chroma_persist_directory",
    )
    @classmethod
    def _resolve_relative_paths(cls, value: Path) -> Path:
        """Anchor relative paths at BACKEND_ROOT, never at the CWD.

        .env.example ships CHROMA_PERSIST_DIRECTORY=./data/chroma_db. Resolved
        against the *current working directory*, that silently points somewhere
        different depending on where the process was started: an M5 probe run
        from the repo root opened a brand-new EMPTY Chroma store (zero hits,
        and a stray chroma.sqlite3 created outside backend/) while the same
        script from backend/ found all 542 chunks. Anchoring relative values        at BACKEND_ROOT makes behavior cwd-independent; absolute values
        (including user overrides) pass through untouched.
        """
        value = Path(value)
        if not value.is_absolute():
            value = BACKEND_ROOT / value
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
