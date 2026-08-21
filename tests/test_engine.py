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
Engine tests: speaker selection, generation pipeline with repair, topic
currency checks, and malformed-output never-stored behavior.

Uses a fake LLM client so no real model API is required.
"""
import os
import sys
from unittest.mock import MagicMock

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from database import Database
from engine import ConversationEngine
from personas import PERSONAS, persona_keys
from tests import fixtures


@pytest_asyncio.fixture
async def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.initialize()
    yield d
    d.close()


class FakeLLM:
    """Fake LLM returning a queue of canned responses."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    async def generate(self, persona_system, topic_title, recent_lines,
                       session=None, extra_instruction=None, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        self.last_recent_lines = list(recent_lines)
        if self._responses:
            return self._responses.pop(0)
        return None


class FakeFeed:
    async def fetch_items(self, session=None):
        return []


def make_engine(db, llm, config=None):
    cfg = MagicMock()
    cfg.has_api_key = True
    cfg.message_min_delay_seconds = 0
    cfg.message_max_delay_seconds = 0
    cfg.rss_refresh_interval_seconds = 9999
    if config:
        cfg = config
    return ConversationEngine(db, FakeFeed(), llm, cfg)


class TestSpeakerSelection:
    def test_no_consecutive_repeat(self, db):
        eng = make_engine(db, FakeLLM([]))
        last = "potus"
        for _ in range(20):
            p = eng.choose_next_speaker(last)
            assert p.key != last
            last = p.key

    def test_all_personas_selectable(self, db):
        eng = make_engine(db, FakeLLM([]))
        seen = set()
        last = None
        for _ in range(100):
            p = eng.choose_next_speaker(last)
            seen.add(p.key)
            last = p.key
        assert seen == set(persona_keys())


class TestGenerationPipeline:
    @pytest.mark.asyncio
    async def test_valid_message_stored(self, db):
        llm = FakeLLM([fixtures.VALID_POTUS])
        eng = make_engine(db, llm)
        await db.add_topic("Markets rally.", "http://x", "summary")
        topic = await db.get_active_topic()
        text, result = await eng._generate_one(PERSONAS["potus"], topic)
        assert text == fixtures.VALID_POTUS
        assert result.ok

    @pytest.mark.asyncio
    async def test_repair_succeeds(self, db):
        # First output is malformed, repair is valid.
        llm = FakeLLM([fixtures.MALFORMED_MID_SENTENCE, fixtures.VALID_POTUS])
        eng = make_engine(db, llm)
        await db.add_topic("Markets rally.", "http://x", "summary")
        topic = await db.get_active_topic()
        text, result = await eng._generate_one(PERSONAS["potus"], topic)
        assert text is not None
        assert llm.calls == 2  # one initial + one repair

    @pytest.mark.asyncio
    async def test_repair_fails_skips(self, db):
        llm = FakeLLM([fixtures.MALFORMED_MID_SENTENCE, fixtures.MALFORMED_MID_SENTENCE])
        eng = make_engine(db, llm)
        await db.add_topic("Markets rally.", "http://x", "summary")
        topic = await db.get_active_topic()
        text, result = await eng._generate_one(PERSONAS["potus"], topic)
        assert text is None
        assert not result.ok
        assert llm.calls == 2  # only one repair attempt

    @pytest.mark.asyncio
    async def test_malformed_never_stored(self, db):
        llm = FakeLLM([fixtures.MALFORMED_FENCED_JSON, fixtures.MALFORMED_FENCED_JSON])
        eng = make_engine(db, llm)
        await db.add_topic("Markets rally.", "http://x", "summary")
        topic = await db.get_active_topic()
        text, result = await eng._generate_one(PERSONAS["potus"], topic)
        assert text is None
        # Nothing stored in messages table.
        msgs = await db.get_messages()
        assert len(msgs) == 0

    @pytest.mark.asyncio
    async def test_only_one_repair_attempt(self, db):
        llm = FakeLLM([
            fixtures.MALFORMED_FENCED_JSON,
            fixtures.MALFORMED_INCOMPLETE_JSON,
            fixtures.VALID_POTUS,  # should NOT be used
        ])
        eng = make_engine(db, llm)
        await db.add_topic("Markets rally.", "http://x", "summary")
        topic = await db.get_active_topic()
        text, _ = await eng._generate_one(PERSONAS["potus"], topic)
        assert text is None
        assert llm.calls == 2

    @pytest.mark.asyncio
    async def test_gronk_valid_stored(self, db):
        llm = FakeLLM([fixtures.VALID_GRONK])
        eng = make_engine(db, llm)
        await db.add_topic("Markets rally.", "http://x", "summary")
        topic = await db.get_active_topic()
        text, result = await eng._generate_one(PERSONAS["gronk"], topic)
        assert text is not None
        assert result.ok
        lines = [ln for ln in text.split("\n") if ln.strip()]
        assert len(lines) == 3
        lines = [ln for ln in text.split("\n") if ln.strip()]
        assert len(lines) == 3


class TestTopicManagement:
    @pytest.mark.asyncio
    async def test_no_topic_churn_on_unchanged_feed(self, db):
        """refresh_feed should not create a new topic when the newest
        item title matches the currently active topic."""
        from feed import FeedItem

        class StableFeed:
            async def fetch_items(self, session=None):
                return [FeedItem("Same Headline.", "http://link1", "summary")]

        cfg = MagicMock()
        cfg.has_api_key = True
        cfg.message_min_delay_seconds = 0
        cfg.message_max_delay_seconds = 0
        cfg.rss_refresh_interval_seconds = 9999
        eng = ConversationEngine(db, StableFeed(), FakeLLM([]), cfg)
        import aiohttp

        eng._http_session = aiohttp.ClientSession()
        try:
            await eng.refresh_feed()
            first_topic = await db.get_active_topic()
            assert first_topic is not None
            assert first_topic.title == "Same Headline."
            # Refresh again with same feed — should NOT create a new topic.
            await eng.refresh_feed()
            # The active topic should still be the same one.
            active = await db.get_active_topic()
            assert active.title == "Same Headline."
            assert active.id == first_topic.id
        finally:
            await eng._http_session.close()
            eng._http_session = None

    @pytest.mark.asyncio
    async def test_topic_changes_on_new_headline(self, db):
        """refresh_feed should create a new topic when a different newest
        item appears, and discard stale recent lines."""
        from feed import FeedItem

        class ChangingFeed:
            def __init__(self):
                self.titles = ["First Headline.", "Second Headline."]

            async def fetch_items(self, session=None):
                title = self.titles.pop(0)
                return [FeedItem(title, f"http://{title}", "summary")]

        cfg = MagicMock()
        cfg.has_api_key = True
        cfg.message_min_delay_seconds = 0
        cfg.message_max_delay_seconds = 0
        cfg.rss_refresh_interval_seconds = 9999
        eng = ConversationEngine(db, ChangingFeed(), FakeLLM([]), cfg)
        import aiohttp

        eng._http_session = aiohttp.ClientSession()
        try:
            await eng.refresh_feed()
            first = await db.get_active_topic()
            eng._recent_lines = ["stale context line"]
            await eng.refresh_feed()
            second = await db.get_active_topic()
            assert second.title == "Second Headline."
            assert second.id != first.id
            assert eng._recent_lines == []
        finally:
            await eng._http_session.close()
            eng._http_session = None

    @pytest.mark.asyncio
    async def test_obsolete_topic_discarded(self, db):
        llm = FakeLLM([fixtures.VALID_POTUS])
        eng = make_engine(db, llm)
        await db.add_topic("Old topic.", "http://old", "summary")
        # Simulate topic change before storing.
        eng._active_topic_id = 999
        eng._active_topic_title = "New topic."
        # _produce_message should discard.
        await eng._produce_message()
        msgs = await db.get_messages()
        assert len(msgs) == 0


class TestMonotonicIds:
    @pytest.mark.asyncio
    async def test_ids_monotonic(self, db):
        ids = []
        for i in range(5):
            mid = await db.add_message("potus", f"Message {i}.", topic_id=1)
            ids.append(mid)
        assert ids == sorted(ids)
        assert len(set(ids)) == 5
        assert ids == [1, 2, 3, 4, 5]
