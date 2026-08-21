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
Live News Debate Wall — main application module.

Launches the full application: SQLite persistence, RSS feed fetching,
fictional AI-parody conversation generation, and an aiohttp web server
serving an embedded responsive HTML/CSS/JS interface with correct
transcript auto-scroll behavior.

POTUS and the President of the European Commission are fictional
representations of the offices only. No real officeholder is named.

Foreground launch:
    python live_news_wall.py

Background launch:
    nohup python live_news_wall.py > live_news_wall.log 2>&1 &

Configuration is read from config/.env (see config/.env.example).
"""
from __future__ import annotations

import asyncio
import logging
import signal
import socket
import sys
from typing import List, Optional

from config_loader import Config, ConfigError, load_config
from database import Database
from engine import ConversationEngine
from feed import FeedClient
from llm_client import LLMClient
from web_server import WebServer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("live_news_wall")


def detected_lan_urls(port: int) -> List[str]:
    """Return http URLs for every detected non-loopback IPv4 address."""
    urls: List[str] = []
    seen: set = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if address.startswith("127.") or address in seen:
                continue
            seen.add(address)
            urls.append(f"http://{address}:{port}/")
    except OSError as exc:
        logger.debug("Could not enumerate LAN addresses: %s", exc)
    return urls


class Application:
    """Top-level orchestrator wiring config -> db -> engine -> web."""

    def __init__(self, config: Optional[Config] = None):
        self._cfg = config or load_config()
        self._db = Database(self._cfg.db_path)
        self._feed = FeedClient(self._cfg.rss_feed_url)
        self._llm: Optional[LLMClient] = None
        if self._cfg.has_api_key:
            self._llm = LLMClient(
                base_url=self._cfg.llm_base_url,
                model=self._cfg.llm_model,
                api_key=self._cfg.llm_api_key,
                temperature=self._cfg.llm_temperature,
                max_tokens=self._cfg.llm_max_tokens,
                timeout_seconds=self._cfg.llm_timeout_seconds,
            )
        self._engine = ConversationEngine(self._db, self._feed, self._llm, self._cfg)
        self._web = WebServer(
            self._db,
            self._engine,
            max_clients=self._cfg.max_clients,
            typing_cps=self._cfg.typing_chars_per_second,
        )

    async def run(self) -> None:
        """Initialize, start engine + server, and run until shutdown."""
        self._db.initialize()
        try:
            await self._engine.start()
        except Exception:
            # start() may fail after opening its aiohttp session; close it
            # rather than leaking the connector.
            await self._engine.stop()
            self._db.close()
            raise
        try:
            await self._web.start(self._cfg.host, self._cfg.port)
        except Exception:
            # If the web server fails to bind (e.g. port in use), ensure
            # the engine and its aiohttp ClientSession are cleanly shut
            # down before propagating the error.
            await self._engine.stop()
            self._db.close()
            raise

        self._log_startup_banner()
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()

        def _stop(*_args):
            loop.call_soon_threadsafe(stop_event.set)

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _stop)
            except (NotImplementedError, AttributeError, ValueError):
                # Windows has no loop signal handlers; fall back to the
                # synchronous handler so shutdown is still graceful.
                try:
                    signal.signal(sig, _stop)
                except (OSError, ValueError, AttributeError):
                    logger.debug("Could not install a handler for %s", sig)

        try:
            await stop_event.wait()
        except asyncio.CancelledError:
            pass
        await self._shutdown()

    def _log_startup_banner(self) -> None:
        """Log the listening URLs and the non-secret configuration."""
        logger.info(
            "Live News Debate Wall started on http://%s:%s",
            self._cfg.host,
            self._cfg.port,
        )
        logger.info("Local URL: http://127.0.0.1:%s/", self._cfg.port)
        for url in detected_lan_urls(self._cfg.port):
            logger.info("LAN URL:   %s", url)
        for key, value in self._cfg.public_summary().items():
            logger.info("config %s = %s", key, value)

    async def _shutdown(self) -> None:
        logger.info("Shutting down…")
        await self._engine.stop()
        await self._web.stop()
        self._db.close()
        logger.info("Shutdown complete.")


def main() -> None:
    """Entry point for direct execution."""
    try:
        app = Application()
    except ConfigError as exc:
        logger.error("Configuration error: %s", exc)
        sys.exit(2)
    try:
        asyncio.run(app.run())
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")


if __name__ == "__main__":
    main()
