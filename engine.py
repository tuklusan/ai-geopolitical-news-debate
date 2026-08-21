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
Conversation engine for Live News Debate Wall.

Coordinates RSS topic discovery, a bounded topic queue with per-topic turn
lifespans, weighted non-consecutive speaker selection, LLM generation behind
a single pacing limiter, defensive validation, one repair retry, per-topic
memory of points already made, and SQLite persistence. Responses generated
for a superseded topic are discarded.
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import List, Optional, Tuple

import aiohttp

from database import Database, QueuedItem, StoredTopic
from feed import FeedClient, FeedItem
from llm_client import LLMClient
from personas import Persona, PERSONAS, persona_keys
from validator import ValidationResult, validate_output, repair_instruction

logger = logging.getLogger("live_news_wall.engine")

# Speaker balancing window and the usage count at which a persona is damped.
SPEAKER_WINDOW = 8
SPEAKER_HEAVY_USE = 3
SPEAKER_DAMPED_WEIGHT = 0.25
SPEAKER_NORMAL_WEIGHT = 1.0

# How many prior visible turns are sent to the model as context.
CONTEXT_TURNS = 12
# How many distinct points are remembered per topic.
MAX_TOPIC_POINTS = 20
# Topics that may not be immediately revisited.
NO_REVISIT_RECENT = 3

# Pause before retrying when a turn produced nothing.
IDLE_SLEEP_SECONDS = 2.0

# Typing speed used to pace the loop, matching the browser's typewriter.
DEFAULT_TYPING_CPS = 25.0
# A pathological reply must not stall the wall for minutes.
MAX_TYPING_WAIT_SECONDS = 30.0

# Stored feed items retained when the config does not say.
DEFAULT_FEED_RETENTION = 500

# Validation reasons that indicate the provider itself failed, as opposed to
# the model returning something that was merely rejected.
PROVIDER_FAILURE_REASONS = frozenset(
    {"model unavailable", "no model output", "no repair output"}
)


def _cfg_num(config, name: str, default, cast=float):
    """Read a numeric setting defensively.

    Test doubles supply ``MagicMock`` attributes for settings they do not
    care about; casting those raises ``TypeError``. Fall back to the
    documented default rather than crashing the engine.
    """
    try:
        return cast(getattr(config, name, default))
    except (TypeError, ValueError):
        return default


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
        self._active_topic_link: Optional[str] = None
        self._topic_version = 0
        self._topic_lifespan = self._random_lifespan()
        self._recent_lines: List[str] = []
        self._last_message_chars = 0
        self._rss_healthy = False
        self._model_healthy = False
        self._model_disabled = False
        self._running = False
        self._http_session: Optional[aiohttp.ClientSession] = None
        self._tasks: List[asyncio.Task] = []
        # Serializes every LLM attempt and enforces the pacing delay.
        self._llm_gate = asyncio.Lock()
        # Serializes topic changes. The RSS loop and the conversation loop
        # can both switch topics; interleaving their awaits would leave the
        # cached topic fields disagreeing with the active row in SQLite,
        # after which every generated turn is discarded as "obsolete".
        self._topic_lock = asyncio.Lock()
        if llm_client is None or not getattr(config, "has_api_key", False):
            self._model_disabled = True
            logger.warning("Model generation unavailable: no API key configured.")

    # ------------------------------------------------------------------
    # settings
    # ------------------------------------------------------------------
    def _random_lifespan(self) -> int:
        lo = int(_cfg_num(self._cfg, "topic_turns_min", 8, int))
        hi = int(_cfg_num(self._cfg, "topic_turns_max", 12, int))
        if lo < 1:
            lo = 1
        if hi < lo:
            hi = lo
        return random.randint(lo, hi)

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

    @property
    def topic_version(self) -> int:
        return self._topic_version

    # ------------------------------------------------------------------
    # speaker selection
    # ------------------------------------------------------------------
    def choose_next_speaker(
        self,
        last_speaker: Optional[str],
        recent_speakers: Optional[List[str]] = None,
    ) -> Persona:
        """Pick a weighted random persona that is not the previous speaker.

        Any persona that already used three or more of the last eight visible
        turns is damped rather than removed, so every persona stays
        selectable while long runs remain reasonably balanced.
        """
        keys = persona_keys()
        candidates = [k for k in keys if k != last_speaker]
        if not candidates:
            candidates = list(keys)
        window = list(recent_speakers or [])[:SPEAKER_WINDOW]
        weights = []
        for key in candidates:
            used = window.count(key)
            if used >= SPEAKER_HEAVY_USE:
                weights.append(SPEAKER_DAMPED_WEIGHT)
            else:
                weights.append(SPEAKER_NORMAL_WEIGHT)
        chosen = random.choices(candidates, weights=weights, k=1)[0]
        return PERSONAS[chosen]

    # ------------------------------------------------------------------
    # topic management
    # ------------------------------------------------------------------
    async def _switch_topic(self, item: QueuedItem, reason: str) -> None:
        """Make ``item`` the active topic and invalidate in-flight results."""
        topic_id = await self._db.get_or_create_topic(item.title, item.link, item.summary)
        await self._db.reset_topic_turns(topic_id)
        await self._db.mark_item_discussed(item.link)
        self._active_topic_id = topic_id
        self._active_topic_title = item.title
        self._active_topic_link = item.link
        self._topic_lifespan = self._random_lifespan()
        self._topic_version += 1
        # Old context belongs to the previous headline; drop it.
        self._recent_lines = []
        await self._db.set_state("topic_version", str(self._topic_version))
        await self._db.set_state("topic_lifespan", str(self._topic_lifespan))
        logger.info(
            "Active topic (%s): %s (id=%s, lifespan=%s turns, version=%s)",
            reason,
            item.title,
            topic_id,
            self._topic_lifespan,
            self._topic_version,
        )

    async def refresh_feed(self) -> None:
        """Fetch the RSS feed, register new items, and react to new news."""
        items = await self._feed.fetch_items(session=self._http_session)
        if not items:
            self._rss_healthy = False
            logger.warning("RSS unavailable; continuing in degraded state.")
            return
        self._rss_healthy = True

        new_items: List[FeedItem] = []
        for it in items:
            # Three-tier identity: GUID, normalized link, title+date hash.
            if not await self._db.is_known_feed_item(it):
                await self._db.add_feed_item(it)
                new_items.append(it)

        async with self._topic_lock:
            if new_items:
                logger.info(
                    "RSS refresh: %d new item(s) of %d.", len(new_items), len(items)
                )
                # Genuinely new news pre-empts the current discussion.
                head = new_items[0]
                await self._switch_topic(
                    QueuedItem(head.link, head.title, head.summary), "new item"
                )
            elif self._active_topic_id is None:
                logger.debug("RSS refresh: no new items; selecting from stored queue.")
                await self._advance_topic()
            else:
                logger.debug("RSS refresh: no new items; topic unchanged.")
        await self._prune_feed_items()

    async def _prune_feed_items(self) -> None:
        """Keep the stored feed bounded so it cannot grow without limit."""
        keep = int(_cfg_num(self._cfg, "feed_retention_items", DEFAULT_FEED_RETENTION, int))
        try:
            removed = await self._db.prune_feed_items(keep, self._active_topic_link or "")
        except Exception as exc:  # noqa: BLE001 - pruning is housekeeping
            logger.warning("Could not prune stored feed items: %s", exc)
            return
        if removed:
            logger.info("Pruned %d stored feed item(s), keeping the newest %d.", removed, keep)

    async def _advance_topic(self) -> bool:
        """Move to the next queued item, or revisit the stalest stored one."""
        unseen = await self._db.get_unseen_items()
        for item in unseen:
            if item.link != self._active_topic_link:
                await self._switch_topic(item, "next queued item")
                return True
        exclude = await self._db.get_recent_topic_links(NO_REVISIT_RECENT)
        if self._active_topic_link and self._active_topic_link not in exclude:
            exclude = list(exclude) + [self._active_topic_link]
        item = await self._db.get_revisit_item(exclude)
        if item is not None:
            await self._switch_topic(item, "revisit from a fresh angle")
            return True
        logger.debug("No alternative topic available; continuing current topic.")
        return False

    async def _maybe_advance_topic(self) -> Optional[StoredTopic]:
        """Return the topic to discuss, advancing when its lifespan is spent."""
        async with self._topic_lock:
            topic = await self._db.get_active_topic()
            if topic is None:
                if await self._advance_topic():
                    topic = await self._db.get_active_topic()
                return topic
            turns = await self._db.get_topic_turns(topic.id)
            if turns >= self._topic_lifespan:
                logger.info(
                    "Topic exhausted after %d turns (lifespan %d): %s",
                    turns,
                    self._topic_lifespan,
                    topic.title,
                )
                if await self._advance_topic():
                    topic = await self._db.get_active_topic()
                else:
                    # Nothing else to discuss: extend rather than stall.
                    await self._db.reset_topic_turns(topic.id)
                    self._topic_lifespan = self._random_lifespan()
                    # Persist it too, or a restart would restore the
                    # superseded lifespan and advance at the wrong turn.
                    await self._db.set_state(
                        "topic_lifespan", str(self._topic_lifespan)
                    )
            return topic

    def _is_current_topic(self, topic_id: Optional[int], topic_title: Optional[str]) -> bool:
        return (
            self._active_topic_id is not None
            and topic_id == self._active_topic_id
            and topic_title == self._active_topic_title
        )

    # ------------------------------------------------------------------
    # generation pipeline
    # ------------------------------------------------------------------
    async def _paced_generate(self, persona: Persona, topic: StoredTopic, **kwargs):
        """Run one LLM attempt behind the shared pacing limiter.

        Every attempt, including a repair retry, waits a uniformly random
        interval first, and only one request is in flight at a time.
        """
        lo = _cfg_num(self._cfg, "message_min_delay_seconds", 3.0)
        hi = _cfg_num(self._cfg, "message_max_delay_seconds", 6.0)
        if hi < lo:
            hi = lo
        async with self._llm_gate:
            delay = random.uniform(lo, hi)
            logger.debug("Pacing LLM attempt: waiting %.2fs", delay)
            if delay > 0:
                await asyncio.sleep(delay)
            return await self._llm.generate(
                persona.system_prompt,
                topic.title,
                list(self._recent_lines),
                session=self._http_session,
                **kwargs,
            )

    async def _generate_one(
        self, persona: Persona, topic: StoredTopic
    ) -> Tuple[Optional[str], ValidationResult]:
        """Generate + validate. At most one repair attempt. Returns text/result."""
        if self._llm is None or self._model_disabled:
            return None, ValidationResult.invalid("model unavailable")
        prior_points = await self._prior_points(topic)
        raw = await self._paced_generate(
            persona, topic, topic_summary=topic.summary, prior_points=prior_points
        )
        if raw is None:
            return None, ValidationResult.invalid("no model output")
        result = validate_output(raw, persona)
        if result.ok:
            return result.text, result
        # One repair attempt.
        repair_msg = repair_instruction(persona, result.reason)
        raw2 = await self._paced_generate(
            persona,
            topic,
            topic_summary=topic.summary,
            prior_points=prior_points,
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

    async def _prior_points(self, topic: StoredTopic) -> List[str]:
        """Return the points already made on this topic."""
        try:
            memory = await self._db.get_topic_memory(topic.id)
        except Exception as exc:  # noqa: BLE001 - memory is an optimisation
            logger.warning("Could not read topic memory: %s", exc)
            return []
        points = memory.get("points") if isinstance(memory, dict) else None
        return [str(p) for p in points] if isinstance(points, list) else []

    @staticmethod
    def _summarize_point(persona: Persona, text: str) -> str:
        """Condense a visible turn into a short 'already said' note."""
        flat = " ".join(text.split())
        if len(flat) > 160:
            flat = flat[:157].rstrip() + "…"
        return f"{persona.display_name}: {flat}"

    async def _produce_message(self) -> bool:
        """Generate, validate, and persist one contribution.

        Returns True only when a message was stored, so the caller can idle
        instead of spinning when there is nothing to say.
        """
        if self._model_disabled:
            return False
        topic = await self._maybe_advance_topic()
        if topic is None:
            return False
        last_speaker = await self._db.get_last_speaker()
        recent_speakers = await self._db.get_recent_speakers(SPEAKER_WINDOW)
        persona = self.choose_next_speaker(last_speaker, recent_speakers)
        version_at_start = self._topic_version
        text, result = await self._generate_one(persona, topic)
        if text is None:
            # Only a provider failure means the model is unhealthy. A
            # rejected-but-delivered response says the endpoint is fine.
            if result.reason in PROVIDER_FAILURE_REASONS:
                self._model_healthy = False
            return False
        # Re-check topic currency before storing (topic may have changed).
        if not self._is_current_topic(topic.id, topic.title) or version_at_start != self._topic_version:
            logger.info("Discarding response for obsolete topic: %s", topic.title)
            return False
        await self._db.add_message(persona.key, text, topic_id=topic.id)
        await self._db.increment_topic_turns(topic.id)
        await self._db.append_topic_point(
            topic.id, self._summarize_point(persona, text), MAX_TOPIC_POINTS
        )
        self._recent_lines.append(f"{persona.display_name}: {text}")
        if len(self._recent_lines) > CONTEXT_TURNS:
            self._recent_lines = self._recent_lines[-CONTEXT_TURNS:]
        self._model_healthy = True
        self._last_message_chars = len(text)
        logger.info("Turn stored: %s on topic %s", persona.key, topic.id)
        return True

    def _typing_seconds(self, characters: int) -> float:
        """How long the browser will spend typing a message of this length.

        The conversation loop waits this out before requesting the next
        turn, so a speaker finishes typing before the next one starts and
        the model is called less often.
        """
        cps = _cfg_num(self._cfg, "typing_chars_per_second", DEFAULT_TYPING_CPS)
        if cps <= 0:
            cps = DEFAULT_TYPING_CPS
        return min(max(0.0, characters) / cps, MAX_TYPING_WAIT_SECONDS)

    async def _conversation_loop(self) -> None:
        """Main loop. Pacing is enforced per attempt by the LLM limiter."""
        while self._running:
            try:
                if self._model_disabled:
                    await asyncio.sleep(5)
                    continue
                produced = await self._produce_message()
                if produced:
                    # Let the message finish typing on screen before asking
                    # for the next one. Also the main brake on how often the
                    # model is called.
                    typing = self._typing_seconds(self._last_message_chars)
                    if typing > 0:
                        logger.debug("Waiting %.1fs for the message to type out.", typing)
                        await asyncio.sleep(typing)
                    else:
                        # Yield so a cancelled loop stops promptly even when
                        # the limiter delay is configured to zero.
                        await asyncio.sleep(0)
                else:
                    # No topic yet, or the turn was dropped. Without this the
                    # loop spins at full CPU whenever there is nothing to say
                    # (for example while the feed is unreachable at startup).
                    await asyncio.sleep(IDLE_SLEEP_SECONDS)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - never crash the loop
                logger.warning("Conversation loop error: %s", exc)
                self._model_healthy = False
                await asyncio.sleep(1)

    async def _rss_loop(self) -> None:
        """Periodic RSS refresh loop."""
        interval = max(30, int(_cfg_num(self._cfg, "rss_refresh_interval_seconds", 300, int)))
        while self._running:
            await asyncio.sleep(interval)
            if not self._running:
                return
            try:
                await self.refresh_feed()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("RSS loop error: %s", exc)
                self._rss_healthy = False

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    async def _restore_state(self) -> None:
        """Restore the active topic, its pacing, and recent context."""
        topic = await self._db.get_active_topic()
        if topic is None:
            return
        self._active_topic_id = topic.id
        self._active_topic_title = topic.title
        self._active_topic_link = topic.link
        try:
            self._topic_version = int(await self._db.get_state("topic_version", "0") or 0)
        except (TypeError, ValueError):
            self._topic_version = 0
        try:
            stored = await self._db.get_state("topic_lifespan")
            if stored:
                self._topic_lifespan = max(1, int(stored))
        except (TypeError, ValueError):
            pass
        recent = await self._db.get_latest_messages(CONTEXT_TURNS)
        lines = []
        for m in recent:
            if m.topic_id != topic.id:
                continue
            persona = PERSONAS.get(m.speaker)
            name = persona.display_name if persona else m.speaker
            lines.append(f"{name}: {m.text}")
        self._recent_lines = lines
        logger.info(
            "Restored topic '%s' (id=%s) with %d context line(s).",
            topic.title,
            topic.id,
            len(lines),
        )

    async def start(self) -> None:
        self._http_session = aiohttp.ClientSession()
        # Set the running flag before creating tasks: the loops test it on
        # their first iteration and would otherwise exit immediately.
        self._running = True
        try:
            await self._restore_state()
        except Exception as exc:  # noqa: BLE001 - a fresh start is acceptable
            logger.warning("Could not restore prior state: %s", exc)
        # Initial RSS fetch.
        await self.refresh_feed()
        self._tasks = [
            asyncio.create_task(self._rss_loop()),
            asyncio.create_task(self._conversation_loop()),
        ]

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
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
            "topic_version": self._topic_version,
            "topic_lifespan": self._topic_lifespan,
        }
