"""
Configuration loader for Live News Debate Wall.

Reads environment variables (optionally from a .env file) and exposes a
strongly-typed :class:`Config` object. The API key is read from configuration
only and is never hard-coded, stored in SQLite, displayed, logged, or
included in test output.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - dotenv is a soft dependency fallback
    def load_dotenv(*_args, **_kwargs) -> bool:  # type: ignore
        return False


DEFAULTS = {
    "LLM_BASE_URL": "https://integrate.api.nvidia.com/v1",
    "LLM_MODEL": "z-ai/glm-5.2",
    "LLM_TEMPERATURE": "0.55",
    "LLM_MAX_TOKENS": "140",
    "LLM_TIMEOUT_SECONDS": "90",
    "RSS_FEED_URL": "https://www.france24.com/en/business/rss",
    "HOST": "0.0.0.0",
    "PORT": "8765",
    "DB_PATH": "live_news_wall.db",
    "RSS_REFRESH_INTERVAL_SECONDS": "300",
    "MESSAGE_MIN_DELAY_SECONDS": "3",
    "MESSAGE_MAX_DELAY_SECONDS": "6",
}


@dataclass(frozen=True)
class Config:
    """Immutable application configuration."""

    llm_base_url: str
    llm_model: str
    llm_temperature: float
    llm_max_tokens: int
    llm_timeout_seconds: float
    llm_api_key: Optional[str]
    rss_feed_url: str
    host: str
    port: int
    db_path: str
    rss_refresh_interval_seconds: int
    message_min_delay_seconds: int
    message_max_delay_seconds: int

    @property
    def has_api_key(self) -> bool:
        """Return True when a non-empty API key is configured."""
        return bool(self.llm_api_key and self.llm_api_key.strip())


def load_config(env_file: str = "config/.env") -> Config:
    """Load configuration from a .env file and the process environment.

    Parameters
    ----------
    env_file:
        Path to the dotenv file. Missing files are ignored.

    Returns
    -------
    Config
        Frozen configuration object.
    """
    if os.path.exists(env_file):
        try:
            load_dotenv(env_file)
        except Exception:
            # dotenv should never prevent startup; fall back to os.environ.
            pass

    def _get(key: str) -> str:
        return os.environ.get(key, DEFAULTS[key])

    raw_key = os.environ.get("LLM_API_KEY", "").strip()
    api_key = raw_key if raw_key and raw_key != "replace-with-your-real-api-key" else None

    return Config(
        llm_base_url=_get("LLM_BASE_URL"),
        llm_model=_get("LLM_MODEL"),
        llm_temperature=float(_get("LLM_TEMPERATURE")),
        llm_max_tokens=int(_get("LLM_MAX_TOKENS")),
        llm_timeout_seconds=float(_get("LLM_TIMEOUT_SECONDS")),
        llm_api_key=api_key,
        rss_feed_url=_get("RSS_FEED_URL"),
        host=_get("HOST"),
        port=int(_get("PORT")),
        db_path=_get("DB_PATH"),
        rss_refresh_interval_seconds=int(_get("RSS_REFRESH_INTERVAL_SECONDS")),
        message_min_delay_seconds=int(_get("MESSAGE_MIN_DELAY_SECONDS")),
        message_max_delay_seconds=int(_get("MESSAGE_MAX_DELAY_SECONDS")),
    )
