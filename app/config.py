"""Application configuration.

All settings are read from environment variables, optionally loaded from a
``.env`` file in the project root via python-dotenv. No credentials are
hard-coded. Secrets are held as ``SecretStr`` so they are never rendered by
``repr()``/``str()`` or accidentally serialised.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field, SecretStr, ValidationError

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_SUBREDDITS = ["legaladvice", "law", "LegalAdviceUK"]


class ConfigurationError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


class Settings(BaseModel):
    # --- Reddit -------------------------------------------------------------
    reddit_client_id: str | None = None
    reddit_client_secret: SecretStr | None = None
    reddit_user_agent: str | None = None
    # Optional: only used if BOTH are supplied (script-app password grant).
    reddit_username: str | None = None
    reddit_password: SecretStr | None = None

    subreddits: list[str] = Field(default_factory=lambda: list(DEFAULT_SUBREDDITS))
    reddit_post_limit: int = Field(20, ge=1, le=100)
    reddit_listing: Literal["new", "hot", "rising"] = "new"

    # --- Claude -------------------------------------------------------------
    anthropic_api_key: SecretStr | None = None
    claude_model: str = "claude-opus-5-5"
    claude_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    claude_max_tokens: int = Field(16000, ge=1024, le=20000)
    claude_timeout_seconds: float = Field(120.0, gt=0)
    claude_max_retries: int = Field(2, ge=0, le=10)
    claude_enable_fallback: bool = True
    max_claude_calls_per_run: int = Field(10, ge=0)
    confidence_review_threshold: float = Field(0.75, ge=0, le=1)

    # --- Filtering ----------------------------------------------------------
    # None means "use the built-in default keyword list".
    legal_keywords: list[str] | None = None
    extra_legal_keywords: list[str] = Field(default_factory=list)
    min_keyword_score: int = Field(1, ge=1)

    # --- Storage / logging --------------------------------------------------
    results_dir: Path = PROJECT_ROOT / "data" / "results"
    skip_already_processed: bool = True
    log_level: str = "INFO"
    log_format: Literal["text", "json"] = "text"

    @property
    def uses_reddit_password_auth(self) -> bool:
        return bool(self.reddit_username and self.reddit_password)

    def require_reddit(self) -> None:
        missing = [
            name
            for name, value in (
                ("REDDIT_CLIENT_ID", self.reddit_client_id),
                ("REDDIT_CLIENT_SECRET", self.reddit_client_secret),
                ("REDDIT_USER_AGENT", self.reddit_user_agent),
            )
            if not value
        ]
        if missing:
            raise ConfigurationError(
                f"Missing required Reddit configuration: {', '.join(missing)}. "
                "Set them in your .env file (see .env.example)."
            )
        if bool(self.reddit_username) != bool(self.reddit_password):
            raise ConfigurationError(
                "REDDIT_USERNAME and REDDIT_PASSWORD must be supplied together "
                "(or both left empty for read-only application-only auth)."
            )

    def require_anthropic(self) -> None:
        if not self.anthropic_api_key:
            raise ConfigurationError(
                "Missing required configuration: ANTHROPIC_API_KEY. "
                "Set it in your .env file (see .env.example)."
            )

    def secret_values(self) -> list[str]:
        """Raw secret values, used only by the log-redaction filter."""
        return [
            s.get_secret_value()
            for s in (self.reddit_client_secret, self.reddit_password, self.anthropic_api_key)
            if s is not None and s.get_secret_value()
        ]


def _env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _csv(value: str | None) -> list[str] | None:
    if value is None:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def load_settings(env_file: Path | None = None) -> Settings:
    """Build Settings from the environment (and .env file, if present).

    Values already present in the process environment take precedence over
    the .env file.
    """
    load_dotenv(env_file or PROJECT_ROOT / ".env", override=False)

    raw: dict[str, object] = {
        "reddit_client_id": _env("REDDIT_CLIENT_ID"),
        "reddit_client_secret": _env("REDDIT_CLIENT_SECRET"),
        "reddit_user_agent": _env("REDDIT_USER_AGENT"),
        "reddit_username": _env("REDDIT_USERNAME"),
        "reddit_password": _env("REDDIT_PASSWORD"),
        "subreddits": _csv(_env("SUBREDDITS")),
        "reddit_post_limit": _env("REDDIT_POST_LIMIT"),
        "reddit_listing": _env("REDDIT_LISTING"),
        "anthropic_api_key": _env("ANTHROPIC_API_KEY"),
        "claude_model": _env("CLAUDE_MODEL"),
        "claude_effort": _env("CLAUDE_EFFORT"),
        "claude_max_tokens": _env("CLAUDE_MAX_TOKENS"),
        "claude_timeout_seconds": _env("CLAUDE_TIMEOUT_SECONDS"),
        "claude_max_retries": _env("CLAUDE_MAX_RETRIES"),
        "claude_enable_fallback": _env("CLAUDE_ENABLE_FALLBACK"),
        "max_claude_calls_per_run": _env("MAX_CLAUDE_CALLS_PER_RUN"),
        "confidence_review_threshold": _env("CONFIDENCE_REVIEW_THRESHOLD"),
        "legal_keywords": _csv(_env("LEGAL_KEYWORDS")),
        "extra_legal_keywords": _csv(_env("EXTRA_LEGAL_KEYWORDS")),
        "min_keyword_score": _env("MIN_KEYWORD_SCORE"),
        "results_dir": _env("RESULTS_DIR"),
        "skip_already_processed": _env("SKIP_ALREADY_PROCESSED"),
        "log_level": _env("LOG_LEVEL"),
        "log_format": _env("LOG_FORMAT"),
    }
    # Unset values fall back to the model defaults.
    values = {key: value for key, value in raw.items() if value is not None}

    if "results_dir" in values:
        path = Path(str(values["results_dir"]))
        values["results_dir"] = path if path.is_absolute() else PROJECT_ROOT / path

    try:
        return Settings(**values)
    except ValidationError as exc:
        # Never echo input values: they may be secrets.
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']).upper()}: {err['msg']}"
            for err in exc.errors(include_input=False, include_url=False)
        )
        raise ConfigurationError(f"Invalid configuration - {problems}") from None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
