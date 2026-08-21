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
    live-news-wall            (console script)
    python -m live_news_wall  (equivalent)

Background launch:
    nohup live-news-wall > /dev/null 2>&1 &

Send stdout to /dev/null rather than to live_news_wall.log: the application
writes and rotates that file itself, and a shell redirect to the same name
would fight the rotation for it.

Configuration is read from config/.env, which "live-news-wall --init"
creates from the template packaged alongside this module.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import pathlib
import signal
import socket
import sys
from logging.handlers import RotatingFileHandler
from typing import List, Optional

from . import __version__
from .config_loader import Config, ConfigError, load_config, read_config_template
from .database import Database
from .engine import ConversationEngine
from .feed import FeedClient
from .llm_client import LLMClient
from .web_server import WebServer

LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
logger = logging.getLogger("live_news_wall")


def _configure_logging() -> None:
    """Set up stderr logging immediately, before any config is read."""
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format=LOG_FORMAT,
    )


def apply_logging_config(cfg) -> Optional[RotatingFileHandler]:
    """Apply the loaded logging settings: level first, then the file handler.

    Called after load_config so LOG_LEVEL and LOG_FILE set in config/.env are
    honoured. Reading them at import time missed the dotenv file entirely,
    which silently pinned the level at INFO however the file was written.
    A log path that cannot be written is reported and downgraded to
    stderr-only rather than preventing startup.
    """
    level = (getattr(cfg, "log_level", "") or "INFO").strip().upper()
    logging.getLogger().setLevel(level)
    path = (getattr(cfg, "log_file", "") or "").strip()
    if not path:
        return None
    try:
        handler = RotatingFileHandler(
            path,
            maxBytes=max(1024, int(cfg.log_max_bytes)),
            backupCount=max(0, int(cfg.log_backup_count)),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("Cannot write log file %s (%s); logging to stderr only.", path, exc)
        return None
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logging.getLogger().addHandler(handler)
    logger.info(
        "Logging to %s (rotating at %d bytes, keeping %d)",
        path, cfg.log_max_bytes, cfg.log_backup_count,
    )
    return handler


_configure_logging()


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
        apply_logging_config(self._cfg)
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


ATTRIBUTION = "Based on original work by Supratim Sanyal of SANYALnet Labs."

ABOUT = f"""Live News Debate Wall {__version__}

{ATTRIBUTION}

Every message the program displays is generated by a language model for
fictional parody and software demonstration. No real person takes part, and
nothing shown is a real statement, policy, or position of any person,
government, or institution.

Licensed under the SANYALnet Labs Non-Commercial License. Non-commercial use
is permitted; commercial use, and use of this code to train or fine-tune
AI/ML models, are prohibited without written permission. See the LICENSE
file distributed with this software for the full terms.
"""


def write_starter_config(path: str) -> int:
    """Create a starter dotenv file. Returns a process exit code.

    Doing this in Python rather than documenting a shell one-liner keeps
    setup identical on every platform: ``printf`` does not exist in
    PowerShell, and a wheel install has no checked-out example file to copy.
    """
    target = pathlib.Path(path)
    if target.exists():
        print(f"{target} already exists; not overwriting it.", file=sys.stderr)
        return 1
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(read_config_template(), encoding="utf-8")
    except OSError as exc:
        print(f"Could not write {target}: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote {target}")
    print("Set LLM_API_KEY in that file, then run: live-news-wall")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Command-line interface for the console script."""
    parser = argparse.ArgumentParser(
        prog="live-news-wall",
        description="Run the Live News Debate Wall server.",
        epilog=ATTRIBUTION,
    )
    parser.add_argument("--version", action="version",
                        version=f"live-news-wall {__version__}")
    parser.add_argument("--about", action="store_true",
                        help="show licence and parody notice, then exit")
    parser.add_argument("--config", metavar="PATH", default="config/.env",
                        help="dotenv file to read (default: config/.env)")
    parser.add_argument("--host", help="override the bind address")
    parser.add_argument("--port", type=int, help="override the listen port")
    parser.add_argument("--init", action="store_true",
                        help="write a starter config file and exit")
    parser.add_argument("--check", action="store_true",
                        help="validate configuration and exit without serving")
    return parser


def main(argv: Optional[List[str]] = None) -> None:
    """Entry point for direct execution and the console script."""
    args = build_parser().parse_args(argv)
    if args.about:
        print(ABOUT)
        return
    if args.init:
        sys.exit(write_starter_config(args.config))
    # Command line beats the environment, which beats the dotenv file.
    if args.host is not None:
        os.environ["HOST"] = args.host
    if args.port is not None:
        os.environ["PORT"] = str(args.port)
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        logger.error("Configuration error: %s", exc)
        sys.exit(2)
    if args.check:
        apply_logging_config(cfg)
        logger.info("Configuration OK.")
        for key, value in cfg.public_summary().items():
            logger.info("config %s = %s", key, value)
        return
    app = Application(cfg)
    try:
        asyncio.run(app.run())
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")


if __name__ == "__main__":
    main()
