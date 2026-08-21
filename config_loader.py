# ============================================================================
# Copyright (c) 2026 Supratim Sanyal of SANYALnet Labs.
# Proprietary rights reserved except as expressly licensed herein.
#
# LIVE NEWS DEBATE WALL
# This file is governed by the SANYALnet Labs Non-Commercial License in the
# root LICENSE file. Non-Commercial use is permitted; Commercial Use and use
# for AI/ML model training are prohibited unless separately authorized.
#
# Attribution is required: "Based on original work by Supratim Sanyal of
# SANYALnet Labs." See LICENSE for full terms, warranty disclaimer, termination,
# patent, trademark, and governing-law provisions.
# ============================================================================

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
    "LLM_MODEL": "deepseek-ai/deepseek-v4-flash-0731",
    "LLM_TEMPERATURE": "0.55",
    # 400, not 140: glm-5.2 spends part of its budget on reasoning tokens
    # before the visible answer, and a tight cap truncates replies
    # mid-sentence. The validator still enforces the per-persona word limits,
    # so a larger budget does not make messages longer.
    "LLM_MAX_TOKENS": "400",
    "LLM_TIMEOUT_SECONDS": "90",
    "RSS_FEED_URL": "https://www.france24.com/en/business/rss",
    "HOST": "0.0.0.0",
    "PORT": "8765",
    "DB_PATH": "live_news_wall.db",
    "RSS_REFRESH_INTERVAL_SECONDS": "300",
    "MESSAGE_MIN_DELAY_SECONDS": "3",
    "MESSAGE_MAX_DELAY_SECONDS": "6",
    "TOPIC_TURNS_MIN": "8",
    "TOPIC_TURNS_MAX": "12",
    "MAX_CLIENTS": "1024",
    "FEED_RETENTION_ITEMS": "500",
    "TRANSCRIPT_RETENTION_MESSAGES": "5000",
    # Typing speed for the on-screen typewriter effect. The engine waits the
    # same length of time before asking for the next turn, so this also sets
    # how often the model is called.
    "TYPING_CHARS_PER_SECOND": "25",
}

PLACEHOLDER_KEYS = {
    "replace-with-your-real-api-key",
    "your-api-key-here",
    "changeme",
}


class ConfigError(ValueError):
    """Raised when configuration values are missing or invalid."""


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
    message_min_delay_seconds: float
    message_max_delay_seconds: float
    topic_turns_min: int
    topic_turns_max: int
    max_clients: int
    feed_retention_items: int
    transcript_retention_messages: int
    typing_chars_per_second: float

    @property
    def has_api_key(self) -> bool:
        """Return True when a non-empty API key is configured."""
        return bool(self.llm_api_key and self.llm_api_key.strip())

    def public_summary(self) -> dict:
        """Return non-secret settings suitable for logging."""
        return {
            "llm_base_url": self.llm_base_url,
            "llm_model": self.llm_model,
            "llm_api_key_present": self.has_api_key,
            "rss_feed_url": self.rss_feed_url,
            "host": self.host,
            "port": self.port,
            "db_path": self.db_path,
            "rss_refresh_interval_seconds": self.rss_refresh_interval_seconds,
            "message_delay_seconds": (
                self.message_min_delay_seconds,
                self.message_max_delay_seconds,
            ),
            "topic_turns": (self.topic_turns_min, self.topic_turns_max),
            "max_clients": self.max_clients,
            "feed_retention_items": self.feed_retention_items,
            "transcript_retention_messages": self.transcript_retention_messages,
            "typing_chars_per_second": self.typing_chars_per_second,
        }


def _as_number(raw: str, key: str, cast):
    try:
        return cast(raw)
    except (TypeError, ValueError):
        raise ConfigError(f"{key} must be a number, got {raw!r}") from None


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

    Raises
    ------
    ConfigError
        If a value is malformed or outside its permitted range.
    """
    if os.path.exists(env_file):
        try:
            load_dotenv(env_file)
        except Exception:
            # dotenv should never prevent startup; fall back to os.environ.
            pass

    def _get(key: str) -> str:
        value = os.environ.get(key)
        if value is None or value == "":
            return DEFAULTS[key]
        return value

    # LLM_API_KEY falls back to API_KEY, and LLM_BASE_URL to BASE_URL, so an
    # existing OpenAI-compatible environment works unchanged.
    raw_key = (os.environ.get("LLM_API_KEY") or os.environ.get("API_KEY") or "").strip()
    api_key = raw_key if raw_key and raw_key not in PLACEHOLDER_KEYS else None

    base_url = os.environ.get("LLM_BASE_URL") or os.environ.get("BASE_URL") or DEFAULTS["LLM_BASE_URL"]

    temperature = _as_number(_get("LLM_TEMPERATURE"), "LLM_TEMPERATURE", float)
    max_tokens = _as_number(_get("LLM_MAX_TOKENS"), "LLM_MAX_TOKENS", int)
    timeout_seconds = _as_number(_get("LLM_TIMEOUT_SECONDS"), "LLM_TIMEOUT_SECONDS", float)
    port = _as_number(_get("PORT"), "PORT", int)
    refresh = _as_number(
        _get("RSS_REFRESH_INTERVAL_SECONDS"), "RSS_REFRESH_INTERVAL_SECONDS", int
    )
    min_delay = _as_number(
        _get("MESSAGE_MIN_DELAY_SECONDS"), "MESSAGE_MIN_DELAY_SECONDS", float
    )
    max_delay = _as_number(
        _get("MESSAGE_MAX_DELAY_SECONDS"), "MESSAGE_MAX_DELAY_SECONDS", float
    )
    turns_min = _as_number(_get("TOPIC_TURNS_MIN"), "TOPIC_TURNS_MIN", int)
    turns_max = _as_number(_get("TOPIC_TURNS_MAX"), "TOPIC_TURNS_MAX", int)
    max_clients = _as_number(_get("MAX_CLIENTS"), "MAX_CLIENTS", int)
    retention = _as_number(_get("FEED_RETENTION_ITEMS"), "FEED_RETENTION_ITEMS", int)
    transcript_keep = _as_number(_get("TRANSCRIPT_RETENTION_MESSAGES"), "TRANSCRIPT_RETENTION_MESSAGES", int)
    typing_cps = _as_number(_get("TYPING_CHARS_PER_SECOND"), "TYPING_CHARS_PER_SECOND", float)

    if not 1 <= port <= 65535:
        raise ConfigError(f"PORT must be between 1 and 65535, got {port}")
    if refresh <= 0:
        raise ConfigError(f"RSS_REFRESH_INTERVAL_SECONDS must be positive, got {refresh}")
    if min_delay < 0 or max_delay < 0:
        raise ConfigError("MESSAGE_MIN/MAX_DELAY_SECONDS must not be negative")
    if max_delay < min_delay:
        raise ConfigError(
            f"MESSAGE_MAX_DELAY_SECONDS ({max_delay}) is below "
            f"MESSAGE_MIN_DELAY_SECONDS ({min_delay})"
        )
    if turns_min < 1:
        raise ConfigError(f"TOPIC_TURNS_MIN must be at least 1, got {turns_min}")
    if turns_max < turns_min:
        raise ConfigError(
            f"TOPIC_TURNS_MAX ({turns_max}) is below TOPIC_TURNS_MIN ({turns_min})"
        )
    if max_clients < 1:
        raise ConfigError(f"MAX_CLIENTS must be at least 1, got {max_clients}")
    if retention < 10:
        raise ConfigError(f"FEED_RETENTION_ITEMS must be at least 10, got {retention}")
    if transcript_keep < 100:
        raise ConfigError(
            f"TRANSCRIPT_RETENTION_MESSAGES must be at least 100, got {transcript_keep}"
        )
    if typing_cps <= 0:
        raise ConfigError(
            f"TYPING_CHARS_PER_SECOND must be greater than 0, got {typing_cps}"
        )
    if max_tokens <= 0:
        raise ConfigError(f"LLM_MAX_TOKENS must be positive, got {max_tokens}")
    if timeout_seconds <= 0:
        raise ConfigError(f"LLM_TIMEOUT_SECONDS must be positive, got {timeout_seconds}")
    if temperature < 0:
        raise ConfigError(f"LLM_TEMPERATURE must not be negative, got {temperature}")

    return Config(
        llm_base_url=base_url,
        llm_model=_get("LLM_MODEL"),
        llm_temperature=temperature,
        llm_max_tokens=max_tokens,
        llm_timeout_seconds=timeout_seconds,
        llm_api_key=api_key,
        rss_feed_url=_get("RSS_FEED_URL"),
        host=_get("HOST"),
        port=port,
        db_path=_get("DB_PATH"),
        rss_refresh_interval_seconds=refresh,
        message_min_delay_seconds=min_delay,
        message_max_delay_seconds=max_delay,
        topic_turns_min=turns_min,
        topic_turns_max=turns_max,
        max_clients=max_clients,
        feed_retention_items=retention,
        transcript_retention_messages=transcript_keep,
        typing_chars_per_second=typing_cps,
    )
