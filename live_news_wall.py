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
import sys
from typing import Optional

from config_loader import Config, load_config
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
        self._web = WebServer(self._db, self._engine)

    async def run(self) -> None:
        """Initialize, start engine + server, and run until shutdown."""
        self._db.initialize()
        await self._engine.start()
        try:
            await self._web.start(self._cfg.host, self._cfg.port)
        except Exception:
            # If the web server fails to bind (e.g. port in use), ensure
            # the engine and its aiohttp ClientSession are cleanly shut
            # down before propagating the error.
            await self._engine.stop()
            self._db.close()
            raise
        logger.info("Live News Debate Wall started on http://%s:%s", self._cfg.host, self._cfg.port)
        stop_event = asyncio.Event()

        def _stop(*_args):
            stop_event.set()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _stop)
            except NotImplementedError:
                pass
        await stop_event.wait()
        await self._shutdown()

    async def _shutdown(self) -> None:
        logger.info("Shutting down…")
        await self._engine.stop()
        await self._web.stop()
        self._db.close()
        logger.info("Shutdown complete.")


def main() -> None:
    """Entry point for direct execution."""
    try:
        asyncio.run(Application().run())
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")


if __name__ == "__main__":
    main()
