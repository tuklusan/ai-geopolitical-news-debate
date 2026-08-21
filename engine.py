"""
Conversation engine for Live News Debate Wall.

Coordinates RSS topic discovery, randomized non-consecutive speaker
selection, LLM generation, defensive validation, single repair retry, and
SQLite persistence. Discards responses generated for obsolete topics.
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import List, Optional, Tuple

import aiohttp

import personas as persona_mod
from database import Database, StoredTopic
from feed import FeedClient, FeedItem
from llm_client import LLMClient
from personas import Persona, PERSONAS, persona_keys
from validator import ValidationResult, validate_output, repair_instruction

logger = logging.getLogger("live_news_wall.engine")


class ConversationEngine:
    """Drives the fictional AI-parody debate loop."""

    def __init__(
        self,
        db: Database,
        feed_client: FeedClient,
        llm_client: Optional[LLMClient],
        config,
    ):
        self._db = db
        self._feed = feed_client
        self._llm = llm_client
        self._cfg = config
        self._active_topic_id: Optional[int] = None
        self._active_topic_title: Optional[str] = None
        self._recent_lines: List[str] = []
        self._rss_healthy = False
        self._model_healthy = False
        self._model_disabled = False
        self._running = False
        self._http_session: Optional[aiohttp.ClientSession] = None
        if llm_client is None or not getattr(config, "has_api_key", False):
            self._model_disabled = True
            logger.warning("Model generation unavailable: no API key configured.")

    # ------------------------------------------------------------------
    # health
    # ------------------------------------------------------------------
    @property
    def rss_healthy(self) -> bool:
        return self._rss_healthy

    @property
    def model_healthy(self) -> bool:
        return self._model_healthy

    @property
    def model_disabled(self) -> bool:
        return self._model_disabled

    # ------------------------------------------------------------------
    # speaker selection
    # ------------------------------------------------------------------
    def choose_next_speaker(self, last_speaker: Optional[str]) -> Persona:
        """Pick a random persona that is not the previous speaker."""
        keys = persona_keys()
        candidates = [k for k in keys if k != last_speaker]
        if not candidates:
            candidates = keys
        return PERSONAS[random.choice(candidates)]

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # topic management
    # ------------------------------------------------------------------
    async def refresh_feed(self) -> None:
        """Fetch the RSS feed, register new items, and activate newest topic."""
        assert self._http_session is not None
        items = await self._feed.fetch_items(session=self._http_session)
        if not items:
            self._rss_healthy = False
            logger.warning("RSS unavailable; continuing in degraded state.")
            return
        self._rss_healthy = True
        new_items: List[FeedItem] = []
        for it in items:
            known = await self._db.is_known_item(it.link)
            if not known:
                await self._db.add_known_item(it.link, it.title, it.summary)
                new_items.append(it)
        # Activate the newest item as the active topic only when it differs
        # from the currently active topic. This prevents spurious topic churn
        # on every refresh cycle when the feed has not changed.
        newest = items[0]
        if self._active_topic_title != newest.title:
            topic_id = await self._db.add_topic(newest.title, newest.link, newest.summary)
            self._active_topic_id = topic_id
            self._active_topic_title = newest.title
            # Topic changed: discard stale recent lines so old context does
            # not bleed into the new discussion.
            self._recent_lines = []
            logger.info("Active topic: %s (id=%s)", newest.title, topic_id)
        else:
            logger.debug("Feed refreshed; topic unchanged: %s", newest.title)

    def _is_current_topic(self, topic_id: Optional[int], topic_title: Optional[str]) -> bool:
        return (
            self._active_topic_id is not None
            and topic_id == self._active_topic_id
            and topic_title == self._active_topic_title
        )

    # ------------------------------------------------------------------
    # generation pipeline
    # ------------------------------------------------------------------
    async def _generate_one(
        self, persona: Persona, topic: StoredTopic
    ) -> Tuple[Optional[str], ValidationResult]:
        """Generate + validate. At most one repair attempt. Returns text/result."""
        if self._llm is None or self._model_disabled:
            return None, ValidationResult.invalid("model unavailable")
        recent = list(self._recent_lines)
        raw = await self._llm.generate(
            persona.system_prompt,
            topic.title,
            recent,
            session=self._http_session,
        )
        if raw is None:
            return None, ValidationResult.invalid("no model output")
        result = validate_output(raw, persona)
        if result.ok:
            return result.text, result
        # One repair attempt.
        repair_msg = repair_instruction(persona, result.reason)
        raw2 = await self._llm.generate(
            persona.system_prompt,
            topic.title,
            recent,
            session=self._http_session,
            extra_instruction=repair_msg,
        )
        if raw2 is None:
            return None, ValidationResult.invalid("no repair output")
        result2 = validate_output(raw2, persona)
        if result2.ok:
            return result2.text, result2
        # Repair failed: skip contribution.
        logger.warning(
            "Skipping %s contribution: repair failed (%s)",
            persona.key,
            result2.reason,
        )
        return None, result2

    async def _produce_message(self) -> None:
        """Generate, validate, and persist one contribution."""
        if self._model_disabled:
            return
        topic = await self._db.get_active_topic()
        if topic is None:
            return
        last_speaker = await self._db.get_last_speaker()
        persona = self.choose_next_speaker(last_speaker)
        text, result = await self._generate_one(persona, topic)
        if text is None:
            # Generation/validation failed; do not store or display anything.
            return
        # Re-check topic currency before storing (topic may have changed).
        if not self._is_current_topic(topic.id, topic.title):
            logger.info("Discarding response for obsolete topic: %s", topic.title)
            return
        await self._db.add_message(persona.key, text, topic_id=topic.id)
        self._recent_lines.append(f"{persona.display_name}: {text}")
        if len(self._recent_lines) > 20:
            self._recent_lines = self._recent_lines[-20:]
        self._model_healthy = True

    async def _conversation_loop(self) -> None:
        """Main loop: produce messages spaced by 3-6 seconds."""
        self._running = True
        while self._running:
            try:
                if self._model_disabled:
                    await asyncio.sleep(5)
                    continue
                await self._produce_message()
            except Exception as exc:  # noqa: BLE001 - never crash the loop
                logger.warning("Conversation loop error: %s", exc)
                self._model_healthy = False
            delay = random.uniform(
                max(1, self._cfg.message_min_delay_seconds),
                max(2, self._cfg.message_max_delay_seconds),
            )
            await asyncio.sleep(delay)

    async def _rss_loop(self) -> None:
        """Periodic RSS refresh loop."""
        while self._running:
            try:
                await self.refresh_feed()
            except Exception as exc:  # noqa: BLE001
                logger.warning("RSS loop error: %s", exc)
                self._rss_healthy = False
            await asyncio.sleep(max(30, self._cfg.rss_refresh_interval_seconds))

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        self._http_session = aiohttp.ClientSession()
        # Initial RSS fetch.
        await self.refresh_feed()
        asyncio.create_task(self._rss_loop())
        asyncio.create_task(self._conversation_loop())

    async def stop(self) -> None:
        self._running = False
        if self._http_session is not None:
            await self._http_session.close()
            self._http_session = None

    # ------------------------------------------------------------------
    # health snapshot
    # ------------------------------------------------------------------
    def health(self) -> dict:
        return {
            "rss_healthy": self._rss_healthy,
            "model_healthy": self._model_healthy,
            "model_disabled": self._model_disabled,
            "active_topic": self._active_topic_title,
        }
