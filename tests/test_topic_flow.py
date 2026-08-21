"""
Tests for topic advancement, anti-repetition memory, weighted speaker
selection, restart continuity, and message-API validation.

These cover the defects found after the first release:
- the discussion never left the newest headline;
- per-topic memory was written by nobody, so personas looped;
- the RSS refresh loop exited immediately after startup;
- speaker choice was unweighted and collapsed into two-person ping-pong.
"""
import asyncio
import os
import sys
from dataclasses import dataclass, field
from typing import List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from database import Database
from engine import ConversationEngine, SPEAKER_WINDOW
from feed import FeedItem
from personas import PERSONAS, persona_keys
from web_server import WebServer
from tests import fixtures


@dataclass
class FakeConfig:
    """A real config object: MagicMock cannot be cast to int."""

    has_api_key: bool = True
    message_min_delay_seconds: float = 0.0
    message_max_delay_seconds: float = 0.0
    rss_refresh_interval_seconds: int = 9999
    topic_turns_min: int = 8
    topic_turns_max: int = 12


class CountingLLM:
    """Returns an endless supply of distinct, persona-valid responses.

    Gronk requires exactly three lines, so the double must honour that or
    every Gronk turn is (correctly) rejected by the validator and the
    transcript silently loses a quarter of its speakers.
    """

    def __init__(self):
        self.calls = 0
        self.prior_points_seen: List[List[str]] = []
        self.recent_lines_seen: List[List[str]] = []

    async def generate(self, persona_system, topic_title, recent_lines,
                       session=None, extra_instruction=None, **kwargs):
        self.calls += 1
        self.prior_points_seen.append(list(kwargs.get("prior_points") or []))
        self.recent_lines_seen.append(list(recent_lines))
        if "Gronk" in persona_system:
            return (
                f"Form number {self.calls} filed,\n"
                "Committee defers the question,\n"
                "Stamp remains missing."
            )
        return f"Point number {self.calls} is made here."


class ListFeed:
    """Serves a fixed list of feed items."""

    def __init__(self, items):
        self.items = list(items)

    async def fetch_items(self, session=None):
        return list(self.items)


@pytest_asyncio.fixture
async def db(tmp_path):
    d = Database(str(tmp_path / "flow.db"))
    d.initialize()
    yield d
    d.close()


def make_engine(db, llm, feed, **cfg_kwargs):
    return ConversationEngine(db, feed, llm, FakeConfig(**cfg_kwargs))


class TestTopicAdvancement:
    @pytest.mark.asyncio
    async def test_topic_advances_after_lifespan(self, db):
        """The discussion must move on once a topic's turns are spent."""
        feed = ListFeed([
            FeedItem("First headline.", "http://a", "sa"),
            FeedItem("Second headline.", "http://b", "sb"),
        ])
        eng = make_engine(db, CountingLLM(), feed, topic_turns_min=2, topic_turns_max=2)
        await eng.refresh_feed()
        first = await db.get_active_topic()
        assert first.title == "First headline."

        for _ in range(2):
            await eng._produce_message()
        assert await db.get_topic_turns(first.id) == 2

        # The next turn must switch to the queued item.
        await eng._produce_message()
        second = await db.get_active_topic()
        assert second.title == "Second headline."
        assert second.id != first.id

    @pytest.mark.asyncio
    async def test_topic_version_increments_on_switch(self, db):
        feed = ListFeed([FeedItem("H1.", "http://a", "s")])
        eng = make_engine(db, CountingLLM(), feed)
        await eng.refresh_feed()
        v1 = eng.topic_version
        feed.items = [FeedItem("H2.", "http://b", "s")]
        await eng.refresh_feed()
        assert eng.topic_version == v1 + 1
        assert eng._recent_lines == []

    @pytest.mark.asyncio
    async def test_new_item_preempts_current_topic(self, db):
        feed = ListFeed([FeedItem("Old.", "http://a", "s")])
        eng = make_engine(db, CountingLLM(), feed, topic_turns_min=50, topic_turns_max=50)
        await eng.refresh_feed()
        await eng._produce_message()
        # Breaking news arrives well before the lifespan expires.
        feed.items = [FeedItem("Breaking.", "http://b", "s"), FeedItem("Old.", "http://a", "s")]
        await eng.refresh_feed()
        active = await db.get_active_topic()
        assert active.title == "Breaking."

    @pytest.mark.asyncio
    async def test_revisit_when_queue_exhausted(self, db):
        """With every item discussed, the engine revisits the stalest one."""
        feed = ListFeed([
            FeedItem("A.", "http://a", "s"),
            FeedItem("B.", "http://b", "s"),
        ])
        eng = make_engine(db, CountingLLM(), feed, topic_turns_min=1, topic_turns_max=1)
        await eng.refresh_feed()
        seen = set()
        for _ in range(6):
            await eng._produce_message()
            active = await db.get_active_topic()
            seen.add(active.title)
        # Both headlines were discussed; the engine never stalled.
        assert seen == {"A.", "B."}

    @pytest.mark.asyncio
    async def test_no_duplicate_topic_rows_on_revisit(self, db):
        """Revisiting a headline reuses its topic row, keeping its memory."""
        first = await db.get_or_create_topic("A.", "http://a", "s")
        second = await db.get_or_create_topic("B.", "http://b", "s")
        again = await db.get_or_create_topic("A.", "http://a", "s")
        assert again == first
        assert second != first
        active = await db.get_active_topic()
        assert active.id == first


class TestTopicMemory:
    @pytest.mark.asyncio
    async def test_points_accumulate_and_are_sent_to_model(self, db):
        feed = ListFeed([FeedItem("H.", "http://a", "s")])
        llm = CountingLLM()
        eng = make_engine(db, llm, feed, topic_turns_min=50, topic_turns_max=50)
        await eng.refresh_feed()
        for _ in range(3):
            await eng._produce_message()
        topic = await db.get_active_topic()
        memory = await db.get_topic_memory(topic.id)
        assert len(memory["points"]) == 3
        # The third call must have been told about the first two points.
        assert len(llm.prior_points_seen[2]) == 2

    @pytest.mark.asyncio
    async def test_points_capped(self, db):
        points = []
        for i in range(25):
            points = await db.append_topic_point(1, f"point {i}", max_points=20)
        assert len(points) == 20
        assert points[-1] == "point 24"

    @pytest.mark.asyncio
    async def test_context_window_is_twelve_turns(self, db):
        feed = ListFeed([FeedItem("H.", "http://a", "s")])
        llm = CountingLLM()
        eng = make_engine(db, llm, feed, topic_turns_min=99, topic_turns_max=99)
        await eng.refresh_feed()
        for _ in range(15):
            await eng._produce_message()
        assert len(eng._recent_lines) == 12
        assert len(llm.recent_lines_seen[-1]) == 12


class TestSpeakerWeighting:
    def test_overused_persona_is_damped(self):
        eng = ConversationEngine(MagicMock(), MagicMock(), None, FakeConfig(has_api_key=False))
        # gronk dominated the recent window.
        window = ["gronk"] * 5 + ["eu"] * 3
        counts = {k: 0 for k in persona_keys()}
        for _ in range(2000):
            p = eng.choose_next_speaker("potus", window)
            counts[p.key] += 1
        # Damped personas still appear, but far less than fresh ones.
        assert counts["gronk"] > 0
        assert counts["eu"] > 0
        assert counts["yoda"] > counts["gronk"] * 2
        assert counts["potus"] == 0  # previous speaker excluded

    def test_all_selectable_with_empty_window(self):
        eng = ConversationEngine(MagicMock(), MagicMock(), None, FakeConfig(has_api_key=False))
        seen = set()
        last = None
        for _ in range(200):
            p = eng.choose_next_speaker(last, [])
            seen.add(p.key)
            last = p.key
        assert seen == set(persona_keys())

    @pytest.mark.asyncio
    async def test_long_run_is_reasonably_balanced(self, db):
        """A long run must not collapse into two-person ping-pong."""
        feed = ListFeed([FeedItem("H.", "http://a", "s")])
        eng = make_engine(db, CountingLLM(), feed, topic_turns_min=999, topic_turns_max=999)
        await eng.refresh_feed()
        for _ in range(120):
            await eng._produce_message()
        msgs = await db.get_messages(limit=1000)
        counts = {k: 0 for k in persona_keys()}
        for m in msgs:
            counts[m.speaker] += 1
        assert all(c > 10 for c in counts.values()), counts
        # No persona may dominate more than half the transcript.
        assert max(counts.values()) < len(msgs) * 0.5

    @pytest.mark.asyncio
    async def test_no_consecutive_repeat_over_long_run(self, db):
        feed = ListFeed([FeedItem("H.", "http://a", "s")])
        eng = make_engine(db, CountingLLM(), feed, topic_turns_min=999, topic_turns_max=999)
        await eng.refresh_feed()
        for _ in range(60):
            await eng._produce_message()
        msgs = await db.get_messages(limit=1000)
        speakers = [m.speaker for m in msgs]
        assert all(a != b for a, b in zip(speakers, speakers[1:]))


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_start_sets_running_before_tasks(self, db):
        """The RSS loop must not exit on its first iteration."""
        feed = ListFeed([FeedItem("H.", "http://a", "s")])
        eng = make_engine(db, CountingLLM(), feed)
        await eng.start()
        try:
            assert eng._running is True
            assert len(eng._tasks) == 2
            await asyncio.sleep(0)
            assert all(not t.done() for t in eng._tasks)
        finally:
            await eng.stop()
        assert eng._http_session is None
        assert all(t.done() for t in eng._tasks) or eng._tasks == []

    @pytest.mark.asyncio
    async def test_restart_restores_topic_and_context(self, tmp_path):
        path = str(tmp_path / "restart.db")
        feed = ListFeed([FeedItem("Persistent headline.", "http://a", "s")])

        db1 = Database(path)
        db1.initialize()
        eng1 = make_engine(db1, CountingLLM(), feed, topic_turns_min=99, topic_turns_max=99)
        await eng1.refresh_feed()
        for _ in range(3):
            await eng1._produce_message()
        first_topic = await db1.get_active_topic()
        last_speaker = await db1.get_last_speaker()
        db1.close()

        db2 = Database(path)
        db2.initialize()
        eng2 = make_engine(db2, CountingLLM(), feed, topic_turns_min=99, topic_turns_max=99)
        await eng2._restore_state()
        assert eng2._active_topic_id == first_topic.id
        assert eng2._active_topic_title == "Persistent headline."
        assert len(eng2._recent_lines) == 3
        # Message ids continue without collision.
        new_id = await db2.add_message("yoda", "Continue, we do.", topic_id=first_topic.id)
        assert new_id == 4
        # The restored engine must not immediately repeat the last speaker.
        for _ in range(20):
            assert eng2.choose_next_speaker(last_speaker, []).key != last_speaker
        db2.close()

    @pytest.mark.asyncio
    async def test_restart_does_not_duplicate_topic(self, tmp_path):
        path = str(tmp_path / "dup.db")
        feed = ListFeed([FeedItem("Same.", "http://a", "s")])

        db1 = Database(path)
        db1.initialize()
        eng1 = make_engine(db1, CountingLLM(), feed)
        await eng1.refresh_feed()
        first = await db1.get_active_topic()
        db1.close()

        db2 = Database(path)
        db2.initialize()
        eng2 = make_engine(db2, CountingLLM(), feed)
        await eng2._restore_state()
        await eng2.refresh_feed()
        second = await db2.get_active_topic()
        assert second.id == first.id
        db2.close()


class TestConcurrentIds:
    @pytest.mark.asyncio
    async def test_concurrent_adds_get_unique_ids(self, db):
        """Id allocation and insert share one lock acquisition."""
        ids = await asyncio.gather(
            *[db.add_message("potus", f"Line {i}.", topic_id=1) for i in range(25)]
        )
        assert len(set(ids)) == 25
        assert sorted(ids) == list(range(1, 26))


@pytest_asyncio.fixture
async def api():
    db = Database(":memory:")
    db.initialize()
    feed = MagicMock()
    feed.fetch_items = AsyncMock(return_value=[])
    eng = ConversationEngine(db, feed, None, FakeConfig(has_api_key=False))
    srv = WebServer(db, eng)
    client = TestClient(TestServer(srv._app))
    await client.start_server()
    yield db, client
    await client.close()
    db.close()


class TestMessageApi:
    @pytest.mark.asyncio
    async def test_invalid_since_returns_400(self, api):
        db, client = api
        resp = await client.get("/api/messages?since=abc")
        assert resp.status == 400
        data = await resp.json()
        assert data["error"] == "bad_request"

    @pytest.mark.asyncio
    async def test_negative_since_returns_400(self, api):
        db, client = api
        resp = await client.get("/api/messages?since=-1")
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_invalid_limit_returns_400(self, api):
        db, client = api
        assert (await client.get("/api/messages?limit=0")).status == 400
        assert (await client.get("/api/messages?limit=x")).status == 400

    @pytest.mark.asyncio
    async def test_limit_is_capped(self, api):
        db, client = api
        for i in range(10):
            await db.add_message("potus", f"Line {i}.", topic_id=1)
        resp = await client.get("/api/messages?since=0&limit=99999")
        assert resp.status == 200
        data = await resp.json()
        assert len(data["messages"]) == 10

    @pytest.mark.asyncio
    async def test_since_zero_returns_newest_and_marks_truncation(self, api):
        db, client = api
        for i in range(10):
            await db.add_message("potus", f"Line {i}.", topic_id=1)
        resp = await client.get("/api/messages?since=0&limit=3")
        data = await resp.json()
        assert [m["text"] for m in data["messages"]] == ["Line 7.", "Line 8.", "Line 9."]
        assert data["truncated"] is True
        assert data["latest_id"] == 10

    @pytest.mark.asyncio
    async def test_no_store_cache_header(self, api):
        db, client = api
        resp = await client.get("/api/messages")
        assert resp.headers["Cache-Control"] == "no-store"
        health = await client.get("/healthz")
        assert health.headers["Cache-Control"] == "no-store"

    @pytest.mark.asyncio
    async def test_no_secret_leakage_in_api(self, api):
        db, client = api
        text = await (await client.get("/api/messages")).text()
        assert "api_key" not in text.lower()
        assert "bearer" not in text.lower()
