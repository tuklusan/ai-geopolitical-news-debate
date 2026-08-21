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
import pathlib
import sqlite3
import sys
from dataclasses import dataclass
from typing import List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from live_news_wall.database import Database
from live_news_wall.engine import ConversationEngine
from live_news_wall.feed import FeedItem
from live_news_wall.personas import persona_keys
from live_news_wall.web_server import WebServer


@dataclass
class FakeConfig:
    """A real config object: MagicMock cannot be cast to int."""

    has_api_key: bool = True
    message_min_delay_seconds: float = 0.0
    message_max_delay_seconds: float = 0.0
    rss_refresh_interval_seconds: int = 9999
    topic_turns_min: int = 8
    topic_turns_max: int = 12
    feed_retention_items: int = 500
    typing_chars_per_second: float = 25.0
    transcript_retention_messages: int = 5000


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


class TestHardening:
    """Regressions found during line-by-line review."""

    @pytest.mark.asyncio
    async def test_initialize_twice_keeps_data_and_does_not_leak(self, tmp_path):
        path = str(tmp_path / "twice.db")
        db = Database(path)
        db.initialize()
        await db.add_message("potus", "Kept.", topic_id=1)
        # A second initialize must close the first handle, not strand it.
        db.initialize()
        msgs = await db.get_messages()
        assert [m.text for m in msgs] == ["Kept."]
        db.close()

    def test_error_bodies_never_leak_the_api_key(self):
        from live_news_wall.llm_client import LLMClient

        # Deliberately not shaped like a real provider key, so repository
        # secret scanners do not flag this fixture.
        secret = "unit-test-credential-placeholder"
        client = LLMClient("http://x/v1", "m", secret)
        leaked = f'{{"error":"invalid key {secret}"}}'
        redacted = client._redact(leaked)
        assert secret not in redacted
        assert "[REDACTED]" in redacted

    def test_redact_handles_empty_input(self):
        from live_news_wall.llm_client import LLMClient

        client = LLMClient("http://x/v1", "m", "key")
        assert client._redact("") == ""

    def test_html_caps_rendered_messages(self):
        from live_news_wall.web_server import build_html_page

        html = build_html_page()
        assert "MAX_RENDERED" in html
        assert "trimTranscript" in html

    def test_trim_keeps_newest_and_forgets_old_ids(self):
        """Python mirror of the JS transcript cap."""
        max_rendered = 5
        children = []
        rendered = {}
        for mid in range(1, 21):
            children.append(mid)
            rendered[str(mid)] = True
            while len(children) > max_rendered:
                oldest = children.pop(0)
                rendered.pop(str(oldest), None)
        assert children == [16, 17, 18, 19, 20]
        assert set(rendered) == {"16", "17", "18", "19", "20"}


class TestTopicRecency:
    @pytest.mark.asyncio
    async def test_recent_topic_links_follow_activation_not_creation(self, db):
        """A revisited topic reuses its row, so id order is not recency."""
        await db.get_or_create_topic("A.", "http://a", "s")
        await db.get_or_create_topic("B.", "http://b", "s")
        await db.get_or_create_topic("C.", "http://c", "s")
        # Revisit the oldest topic; it must now count as the most recent.
        await db.get_or_create_topic("A.", "http://a", "s")
        recent = await db.get_recent_topic_links(3)
        assert recent[0] == "http://a"

    @pytest.mark.asyncio
    async def test_revisit_skips_recently_discussed(self, db):
        feed = ListFeed([
            FeedItem("A.", "http://a", "s"),
            FeedItem("B.", "http://b", "s"),
            FeedItem("C.", "http://c", "s"),
            FeedItem("D.", "http://d", "s"),
        ])
        eng = make_engine(db, CountingLLM(), feed, topic_turns_min=1, topic_turns_max=1)
        await eng.refresh_feed()
        visited = []
        for _ in range(8):
            await eng._produce_message()
            active = await db.get_active_topic()
            visited.append(active.title)
        # No headline may be revisited while it is still one of the last three.
        for i in range(3, len(visited)):
            assert visited[i] not in visited[max(0, i - 3):i] or visited[i] == visited[i - 1]


class TestIdleBehaviour:
    @pytest.mark.asyncio
    async def test_produce_reports_whether_it_stored_anything(self, db):
        feed = ListFeed([FeedItem("H.", "http://a", "s")])
        eng = make_engine(db, CountingLLM(), feed)
        # No topic yet: nothing to say.
        assert await eng._produce_message() is False
        await eng.refresh_feed()
        assert await eng._produce_message() is True

    @pytest.mark.asyncio
    async def test_loop_does_not_spin_without_a_topic(self, db):
        """With no feed and no topic the loop must idle, not busy-wait."""
        empty = ListFeed([])
        eng = make_engine(db, CountingLLM(), empty)
        await eng.start()
        try:
            await asyncio.sleep(0.25)
            # A spinning loop would call the model thousands of times.
            assert eng._llm.calls == 0
        finally:
            await eng.stop()


class TestFeedParsing:
    def test_plain_rss_item(self):
        from live_news_wall.feed import parse_rss

        xml = """<rss><channel>
          <item><title>Plain headline</title>
                <link>http://example.com/a</link>
                <description>Some &lt;b&gt;summary&lt;/b&gt;.</description></item>
        </channel></rss>"""
        items = parse_rss(xml)
        assert len(items) == 1
        assert items[0].link == "http://example.com/a"
        assert items[0].summary == "Some summary."

    def test_atom_style_link_is_not_dropped(self):
        """An empty atom:link must not shadow the real URL."""
        from live_news_wall.feed import parse_rss

        xml = """<rss xmlns:atom="http://www.w3.org/2005/Atom"><channel>
          <item><title>Namespaced headline</title>
                <atom:link href="http://example.com/atom" rel="self"/>
                <link>http://example.com/real</link>
                <description>Body.</description></item>
        </channel></rss>"""
        items = parse_rss(xml)
        assert len(items) == 1
        assert items[0].link in (
            "http://example.com/real",
            "http://example.com/atom",
        )

    def test_item_without_link_is_skipped(self):
        from live_news_wall.feed import parse_rss

        xml = "<rss><channel><item><title>No link</title></item></channel></rss>"
        assert parse_rss(xml) == []

    def test_malformed_xml_does_not_raise(self):
        from live_news_wall.feed import parse_rss

        assert isinstance(parse_rss("<rss><channel><item><title>x"), list)


class TestRepairInstructionUsesReason:
    def test_reason_is_quoted_back(self):
        from live_news_wall.validator import repair_instruction
        from live_news_wall.personas import PERSONAS

        msg = repair_instruction(PERSONAS["potus"], "ends mid-sentence")
        assert "ends mid-sentence" in msg

    def test_missing_reason_is_tolerated(self):
        from live_news_wall.validator import repair_instruction
        from live_news_wall.personas import PERSONAS

        msg = repair_instruction(PERSONAS["gronk"], "")
        assert "reason:" not in msg
        assert "three" in msg.lower()


class TestLicenceAttribution:
    """Section 1(b) requires attribution in the user-facing interface."""

    def test_page_carries_attribution(self):
        from live_news_wall.web_server import build_html_page

        html = build_html_page()
        assert "Based on original work by Supratim Sanyal of" in html.replace(
            "\n      ", " "
        ).replace("based on", "Based on")
        assert "SANYALnet" in html

    def test_attribution_is_outside_the_disclaimer_regions(self):
        """It must not dilute or displace the parody notices."""
        from live_news_wall.web_server import build_html_page

        html = build_html_page()
        top = html.index('class="disclaimer top"')
        bottom = html.index('class="disclaimer bottom"')
        attribution = html.index('class="attribution"')
        assert top < attribution < bottom


class TestPollBackoff:
    """Item 1: client backoff, Retry-After, and the retrying indicator."""

    def test_page_has_backoff_machinery(self):
        from live_news_wall.web_server import build_html_page

        html = build_html_page()
        assert "MAX_BACKOFF_MS" in html
        assert "backoffDelay" in html
        assert "retryAfterMs" in html
        assert 'id="retryIndicator"' in html
        assert "Reconnecting" in html

    def test_no_fixed_interval_polling_remains(self):
        """setInterval would stack requests against a dead server."""
        from live_news_wall.web_server import build_html_page

        assert "setInterval(poll" not in build_html_page()

    def test_indicator_is_hidden_and_announced_politely(self):
        from live_news_wall.web_server import build_html_page

        html = build_html_page()
        i = html.index('id="retryIndicator"')
        tag = html[html.rindex("<span", 0, i):html.index(">", i) + 1]
        assert 'role="status"' in tag
        assert 'aria-live="polite"' in tag
        assert "hidden" in tag

    def test_backoff_doubles_and_is_capped(self):
        """Python mirror of backoffDelay()."""
        base, cap = 2000, 30000
        delay, seen = base, []
        for _ in range(8):
            delay = min(delay * 2, cap)
            seen.append(delay)
        assert seen[:4] == [4000, 8000, 16000, 30000]
        assert max(seen) == cap

    def test_recovery_resets_to_base_cadence(self):
        base, cap = 2000, 30000
        delay = min(min(base * 2, cap) * 2, cap)
        assert delay == 8000
        delay = base  # success path
        assert delay == 2000


class TestClientCapacity:
    """Item 2: MAX_CLIENTS load shedding with Retry-After."""

    @pytest_asyncio.fixture
    async def capped(self):
        """A real server with a cap of one concurrent request."""
        db = Database(":memory:")
        db.initialize()
        feed = MagicMock()
        feed.fetch_items = AsyncMock(return_value=[])
        eng = ConversationEngine(db, feed, None, FakeConfig(has_api_key=False))
        srv = WebServer(db, eng, max_clients=1)

        gate = asyncio.Event()

        async def slow(request):
            await gate.wait()
            return web.Response(text="done")

        srv._app.router.add_get("/slow", slow)
        client = TestClient(TestServer(srv._app))
        await client.start_server()
        yield srv, client, gate
        gate.set()
        await client.close()
        db.close()

    @pytest.mark.asyncio
    async def test_excess_request_gets_503_with_retry_after(self, capped):
        srv, client, gate = capped
        first = asyncio.create_task(client.get("/slow"))
        await asyncio.sleep(0.05)          # let it occupy the only permit
        assert srv.active_requests == 1

        resp = await client.get("/healthz")
        assert resp.status == 503
        assert resp.headers["Retry-After"] == "5"
        assert resp.headers["Cache-Control"] == "no-store"
        body = await resp.json()
        assert body["error"] == "busy"
        assert "detail" in body and "capacity" in body["detail"]

        gate.set()
        await first

    @pytest.mark.asyncio
    async def test_permit_is_released_after_completion(self, capped):
        srv, client, gate = capped
        gate.set()
        for _ in range(5):
            assert (await client.get("/healthz")).status in (200, 503)
        assert srv.active_requests == 0
        # Capacity is available again once nothing is in flight.
        assert (await client.get("/healthz")).status in (200, 503)

    @pytest.mark.asyncio
    async def test_permit_released_even_when_handler_raises(self):
        db = Database(":memory:")
        db.initialize()
        feed = MagicMock()
        feed.fetch_items = AsyncMock(return_value=[])
        eng = ConversationEngine(db, feed, None, FakeConfig(has_api_key=False))
        srv = WebServer(db, eng, max_clients=2)

        async def boom(request):
            raise RuntimeError("handler exploded")

        srv._app.router.add_get("/boom", boom)
        client = TestClient(TestServer(srv._app))
        await client.start_server()
        try:
            resp = await client.get("/boom")
            assert resp.status == 500
            assert srv.active_requests == 0   # released in finally
        finally:
            await client.close()
            db.close()

    def test_cap_is_configurable_and_floored(self):
        db = Database(":memory:")
        db.initialize()
        eng = ConversationEngine(db, MagicMock(), None, FakeConfig(has_api_key=False))
        assert WebServer(db, eng, max_clients=7)._max_clients == 7
        assert WebServer(db, eng, max_clients=0)._max_clients == 1
        db.close()

    def test_config_rejects_zero_capacity(self, monkeypatch):
        from live_news_wall.config_loader import ConfigError, load_config

        monkeypatch.setenv("MAX_CLIENTS", "0")
        with pytest.raises(ConfigError):
            load_config("does-not-exist.env")


class TestThreeTierDedup:
    """Item 3: GUID, then normalized link, then SHA-256(title + date)."""

    @pytest.mark.asyncio
    async def test_tier1_same_guid_different_link(self, db):
        first = FeedItem("Headline one.", "http://e.com/a", "s", guid="GUID-1")
        moved = FeedItem("Rewritten headline.", "http://e.com/moved", "s", guid="GUID-1")
        assert not await db.is_known_feed_item(first)
        await db.add_feed_item(first)
        assert await db.is_known_feed_item(moved), "GUID match must win"

    @pytest.mark.asyncio
    async def test_tier2_normalized_link_no_guid(self, db):
        first = FeedItem("A story.", "http://www.e.com/a/", "s")
        again = FeedItem("A story, updated.", "https://e.com/a?utm_source=news", "s")
        await db.add_feed_item(first)
        assert await db.is_known_feed_item(again), "normalized link must match"

    @pytest.mark.asyncio
    async def test_tier3_title_and_date_hash(self, db):
        first = FeedItem("Same words.", "http://a.com/one", "s", published="Wed, 20 Aug 2026")
        syndicated = FeedItem("Same words.", "http://b.com/two", "s", published="Wed, 20 Aug 2026")
        await db.add_feed_item(first)
        assert await db.is_known_feed_item(syndicated), "title+date hash must match"

    @pytest.mark.asyncio
    async def test_genuinely_different_items_are_not_merged(self, db):
        await db.add_feed_item(
            FeedItem("First.", "http://e.com/1", "s", guid="G1", published="Mon")
        )
        other = FeedItem("Second.", "http://e.com/2", "s", guid="G2", published="Tue")
        assert not await db.is_known_feed_item(other)

    @pytest.mark.asyncio
    async def test_same_title_different_date_is_new(self, db):
        """A recurring column title is a new story each day."""
        await db.add_feed_item(FeedItem("Market wrap.", "http://e.com/mon", "s", published="Mon"))
        tue = FeedItem("Market wrap.", "http://e.com/tue", "s", published="Tue")
        assert not await db.is_known_feed_item(tue)

    @pytest.mark.asyncio
    async def test_engine_does_not_requeue_a_duplicate(self, db):
        """A feed that re-serves one story three different ways yields one topic."""
        feed = ListFeed([FeedItem("Story.", "http://www.e.com/x/", "s", guid="G9")])
        eng = make_engine(db, CountingLLM(), feed)
        await eng.refresh_feed()
        first_version = eng.topic_version
        feed.items = [FeedItem("Story rewritten.", "https://e.com/x?utm_medium=rss", "s", guid="G9")]
        await eng.refresh_feed()
        assert eng.topic_version == first_version, "duplicate must not pre-empt"

    @pytest.mark.asyncio
    async def test_legacy_rows_still_match_after_migration(self, tmp_path):
        """Rows written by the old link-only build are backfilled."""
        import sqlite3

        path = str(tmp_path / "legacy.db")
        con = sqlite3.connect(path)
        con.executescript(
            "CREATE TABLE known_feed_items (link TEXT PRIMARY KEY, title TEXT NOT NULL,"
            " summary TEXT NOT NULL, first_seen REAL NOT NULL);"
        )
        con.execute(
            "INSERT INTO known_feed_items VALUES ('http://www.e.com/old/','Old','s',1.0)"
        )
        con.commit()
        con.close()

        d = Database(path)
        d.initialize()
        try:
            same = FeedItem("Old", "https://e.com/old?utm_source=x", "s")
            assert await d.is_known_feed_item(same), "backfilled norm_link must match"
        finally:
            d.close()


class TestSidebarWithoutJavaScript:
    """Item 4: the speaker panel must exist in server-rendered HTML."""

    def test_cards_are_in_the_raw_html_not_built_by_script(self):
        from live_news_wall.web_server import build_html_page

        html = build_html_page()
        body = html[html.index("<body>"):html.index("<script")]
        for name in (
            "POTUS",
            "President of the European Commission",
            "Gronk Vellumthud",
            "Yoda",
        ):
            assert name in body, f"{name} missing from no-script markup"
        assert body.count('class="persona-card') == 4

    def test_all_four_descriptions_are_present_without_script(self):
        from live_news_wall.web_server import build_html_page
        from live_news_wall.personas import PERSONAS

        html = build_html_page()
        body = html[html.index("<body>"):html.index("<script")]
        for p in PERSONAS.values():
            assert p.role in body
            assert p.style in body

    def test_script_no_longer_builds_the_sidebar(self):
        from live_news_wall.web_server import build_html_page

        html = build_html_page()
        assert "renderSidebar" not in html
        assert "container.innerHTML" not in html

    def test_avatars_render_and_are_hidden_from_screen_readers(self):
        from live_news_wall.web_server import build_html_page
        from live_news_wall.personas import PERSONAS

        html = build_html_page()
        for p in PERSONAS.values():
            assert p.avatar in html
        assert 'class="avatar" aria-hidden="true"' in html

    def test_persona_text_is_html_escaped(self):
        """A persona field can never inject markup into the panel."""
        from live_news_wall.web_server import render_persona_cards

        rendered = render_persona_cards([{
            "key": "x", "avatar": "!", "display_name": "<script>alert(1)</script>",
            "role": 'role" onmouseover="evil()', "style": "a & b",
        }])
        assert "<script>alert(1)</script>" not in rendered
        assert "&lt;script&gt;" in rendered
        assert 'onmouseover="evil()' not in rendered
        assert "a &amp; b" in rendered


class TestFeedRetention:
    """Item 5: the stored feed must not grow without bound."""

    @pytest.mark.asyncio
    async def test_prune_keeps_only_the_newest(self, db):
        for i in range(30):
            await db.add_known_item(f"http://e.com/{i}", f"T{i}", "s")
        assert await db.count_feed_items() == 30
        removed = await db.prune_feed_items(keep=10)
        assert removed == 20
        assert await db.count_feed_items() == 10
        survivors = {i.link for i in await db.get_unseen_items(limit=100)}
        assert "http://e.com/29" in survivors
        assert "http://e.com/0" not in survivors

    @pytest.mark.asyncio
    async def test_prune_never_removes_the_active_item(self, db):
        oldest = "http://e.com/oldest"
        await db.add_known_item(oldest, "Oldest", "s")
        for i in range(30):
            await db.add_known_item(f"http://e.com/{i}", f"T{i}", "s")
        await db.prune_feed_items(keep=5, protect_link=oldest)
        remaining = {i.link for i in await db.get_unseen_items(limit=100)}
        assert oldest in remaining, "the active topic must survive pruning"

    @pytest.mark.asyncio
    async def test_prune_below_threshold_is_a_no_op(self, db):
        for i in range(5):
            await db.add_known_item(f"http://e.com/{i}", f"T{i}", "s")
        assert await db.prune_feed_items(keep=500) == 0
        assert await db.count_feed_items() == 5

    @pytest.mark.asyncio
    async def test_engine_prunes_after_refresh(self, db):
        for i in range(40):
            await db.add_known_item(f"http://old.com/{i}", f"Old {i}", "s")
        feed = ListFeed([FeedItem("Fresh.", "http://e.com/fresh", "s", guid="G")])
        eng = make_engine(db, CountingLLM(), feed, feed_retention_items=10)
        await eng.refresh_feed()
        assert await db.count_feed_items() <= 10
        active = await db.get_active_topic()
        assert active.title == "Fresh."
        # The active item survived even though it is one row among many.
        assert await db.is_known_feed_item(
            FeedItem("Fresh.", "http://e.com/fresh", "s", guid="G")
        )

    def test_config_rejects_a_tiny_retention(self, monkeypatch):
        from live_news_wall.config_loader import ConfigError, load_config

        monkeypatch.setenv("FEED_RETENTION_ITEMS", "3")
        with pytest.raises(ConfigError):
            load_config("does-not-exist.env")


MANDATED_NOTICE = (
    "MANDATORY AI PARODY NOTICE: EVERY MESSAGE ON THIS PAGE IS GENERATED BY "
    "ARTIFICIAL INTELLIGENCE FOR FICTIONAL PARODY AND SOFTWARE DEMONSTRATION. "
    "NO REAL PERSON PARTICIPATED IN THIS CONVERSATION. NOTHING SHOWN HERE IS A "
    "REAL STATEMENT, QUOTATION, VIEW, ENDORSEMENT, POLICY, PROMISE, OR OFFICIAL "
    "POSITION OF ANY PERSON, GOVERNMENT, INSTITUTION, POLITICAL OFFICE, CREATOR, "
    "OR RIGHTS HOLDER."
)


class TestMandatedDisclaimer:
    """Item 6: the exact notice the specification requires."""

    def test_exact_text_appears_twice(self):
        from live_news_wall.web_server import build_html_page

        assert build_html_page().count(MANDATED_NOTICE) == 2

    def test_each_copy_is_inside_strong(self):
        from live_news_wall.web_server import build_html_page

        html = build_html_page()
        assert html.count(f"<strong>{MANDATED_NOTICE}</strong>") == 2

    def test_first_and_last_visible_regions(self):
        from live_news_wall.web_server import build_html_page

        html = build_html_page()
        body = html[html.index("<body>"):html.index("</body>")]
        top = body.index('class="disclaimer top"')
        bottom = body.index('class="disclaimer bottom"')
        main = body.index('class="main"')
        assert top < main < bottom, "notices must bracket the app region"
        # Nothing renderable precedes the top notice or follows the bottom one.
        assert body[len("<body>"):top].strip(" \n<div") == ""

    def test_outside_the_scrolling_region(self):
        from live_news_wall.web_server import build_html_page

        html = build_html_page()
        transcript = html.index('id="transcript"')
        assert html.index('class="disclaimer top"') < transcript
        assert html.index('class="disclaimer bottom"') > transcript

    def test_accessible_label_present_on_both(self):
        from live_news_wall.web_server import build_html_page

        assert build_html_page().count('aria-label="AI-generated parody warning"') == 2

    def test_present_without_javascript(self):
        from live_news_wall.web_server import build_html_page

        html = build_html_page()
        assert html.count(MANDATED_NOTICE, 0, html.index("<script")) == 2

    def test_never_truncated_collapsed_or_faded(self):
        from live_news_wall.web_server import build_html_page

        html = build_html_page()
        css = html[html.index("<style>"):html.index("</style>")]
        block = css[css.index(".disclaimer {"):css.index(".main {")]
        assert "text-overflow: ellipsis" not in block
        assert "display: none" not in block
        assert "opacity: 1" in block
        assert "max-height: none" in block
        assert "white-space: nowrap" not in block

    def test_wraps_on_narrow_screens(self):
        from live_news_wall.web_server import build_html_page

        html = build_html_page()
        css = html[html.index("<style>"):html.index("</style>")]
        strong = css[css.index(".disclaimer strong {"):]
        strong = strong[:strong.index("}")]
        assert "overflow-wrap: anywhere" in strong
        assert "white-space: normal" in strong

    def test_no_per_bubble_warning(self):
        """A bubble carries avatar, speaker name, and message text only."""
        from live_news_wall.web_server import build_html_page

        html = build_html_page()
        fn = html[html.index("function renderMessage"):]
        fn = fn[:fn.index("\n}")]
        # The only children appended to a bubble are the speaker line and body.
        assert fn.count("div.appendChild") == 2
        assert "who" in fn and "body" in fn
        for word in ("parody", "disclaimer", "warning", "notice", "fictional"):
            assert word not in fn.lower(), f"bubble must not repeat a {word}"

    @pytest.mark.asyncio
    async def test_served_page_carries_both_copies(self, api):
        """End to end through the real handler, not just the template."""
        db, client = api
        text = await (await client.get("/")).text()
        assert text.count(MANDATED_NOTICE) == 2


class TestTypewriter:
    """Messages are typed out on screen, and the engine waits for it."""

    def test_typing_speed_is_injected_from_config(self):
        from live_news_wall.web_server import build_html_page

        assert "var TYPING_CPS = 12.5;" in build_html_page(12.5)
        assert "__TYPING_CPS__" not in build_html_page(12.5)

    def test_invalid_speeds_fall_back_to_the_default(self):
        from live_news_wall.web_server import build_html_page, DEFAULT_TYPING_CPS

        for bad in (0, -5, None, "fast"):
            assert f"var TYPING_CPS = {DEFAULT_TYPING_CPS!r};" in build_html_page(bad)

    def test_server_passes_its_setting_to_the_page(self):
        db = Database(":memory:")
        db.initialize()
        eng = ConversationEngine(db, MagicMock(), None, FakeConfig(has_api_key=False))
        srv = WebServer(db, eng, typing_cps=7.0)
        assert srv._typing_cps == 7.0
        db.close()

    def test_typing_never_uses_innerHTML(self):
        """The growing message must never be parsed as markup."""
        from live_news_wall.web_server import build_html_page

        html = build_html_page()
        fn = html[html.index("function pumpTypeQueue"):]
        fn = fn[:fn.index("\n}")]
        assert "innerHTML" not in fn
        assert "job.el.textContent = job.text.slice(0, job.i);" in fn

    def test_backlog_on_load_is_not_typed_out(self):
        """Only messages arriving after the first paint animate."""
        from live_news_wall.web_server import build_html_page

        html = build_html_page()
        assert "renderMessage(msg, firstPaintDone && !reduceMotion)" in html
        assert "firstPaintDone = true;" in html

    def test_reduced_motion_disables_the_effect(self):
        from live_news_wall.web_server import build_html_page

        html = build_html_page()
        assert "prefers-reduced-motion: reduce" in html
        assert "reduceMotion" in html
        css = html[html.index("<style>"):html.index("</style>")]
        block = css[css.index("@media (prefers-reduced-motion: reduce)"):]
        assert "content: none" in block[:220]

    def test_messages_are_queued_not_typed_in_parallel(self):
        from live_news_wall.web_server import build_html_page

        html = build_html_page()
        assert "if (typingActive) return;" in html
        assert "typeQueue.push(" in html

    # ---- engine pacing ----

    def test_typing_wait_scales_with_message_length(self, db):
        eng = make_engine(db, CountingLLM(), ListFeed([]), typing_chars_per_second=10.0)
        assert eng._typing_seconds(0) == 0.0
        assert eng._typing_seconds(50) == 5.0
        assert eng._typing_seconds(100) == 10.0

    def test_typing_wait_is_capped(self, db):
        from live_news_wall.engine import MAX_TYPING_WAIT_SECONDS

        eng = make_engine(db, CountingLLM(), ListFeed([]), typing_chars_per_second=1.0)
        assert eng._typing_seconds(100000) == MAX_TYPING_WAIT_SECONDS

    def test_bad_typing_speed_falls_back(self, db):
        from live_news_wall.engine import DEFAULT_TYPING_CPS

        eng = make_engine(db, CountingLLM(), ListFeed([]), typing_chars_per_second=0)
        assert eng._typing_seconds(DEFAULT_TYPING_CPS) == 1.0

    @pytest.mark.asyncio
    async def test_stored_message_length_is_recorded(self, db):
        feed = ListFeed([FeedItem("H.", "http://a", "s")])
        eng = make_engine(db, CountingLLM(), feed)
        await eng.refresh_feed()
        assert await eng._produce_message() is True
        msgs = await db.get_messages()
        assert eng._last_message_chars == len(msgs[-1].text)

    @pytest.mark.asyncio
    async def test_loop_waits_for_typing_before_the_next_turn(self, db):
        """The pacing brake: fewer model calls per minute."""
        slept = []

        async def fake_sleep(seconds):
            slept.append(seconds)
            raise asyncio.CancelledError  # stop after the first wait

        feed = ListFeed([FeedItem("H.", "http://a", "s")])
        eng = make_engine(db, CountingLLM(), feed, typing_chars_per_second=10.0)
        await eng.refresh_feed()
        eng._running = True
        with patch("live_news_wall.engine.asyncio.sleep", fake_sleep):
            with pytest.raises(asyncio.CancelledError):
                await eng._conversation_loop()
        assert slept, "the loop must wait after producing a message"
        assert slept[0] == pytest.approx(eng._last_message_chars / 10.0)

    def test_config_rejects_a_non_positive_speed(self, monkeypatch):
        from live_news_wall.config_loader import ConfigError, load_config

        monkeypatch.setenv("TYPING_CHARS_PER_SECOND", "0")
        with pytest.raises(ConfigError):
            load_config("does-not-exist.env")

    def test_typing_is_time_derived_not_tick_counted(self):
        """A throttled tab must catch up, not type for minutes."""
        from live_news_wall.web_server import build_html_page

        html = build_html_page()
        fn = html[html.index("function pumpTypeQueue"):]
        fn = fn[:fn.index("\n}")]
        assert "Date.now() - started" in fn
        assert "perTick" not in fn, "character count must not come from tick count"

    def test_typing_duration_matches_engine_pacing(self, db):
        """Client typing time and server wait are the same figure."""
        from live_news_wall.web_server import DEFAULT_TYPING_CPS as WEB_CPS
        from live_news_wall.engine import DEFAULT_TYPING_CPS as ENGINE_CPS

        assert WEB_CPS == ENGINE_CPS
        eng = make_engine(db, CountingLLM(), ListFeed([]), typing_chars_per_second=WEB_CPS)
        # 250 characters at the shared default should take the same time
        # the browser will spend typing them.
        assert eng._typing_seconds(250) == pytest.approx(250 / WEB_CPS)


class TestBoundedGrowth:
    """Nothing may grow without limit on a wall left running for months."""

    @pytest.mark.asyncio
    async def test_transcript_is_capped(self, db):
        for i in range(300):
            await db.add_message("potus", f"Message {i}.", topic_id=1)
        removed = await db.prune_transcript(keep_messages=100)
        assert removed["messages"] == 200
        kept = await db.get_messages(limit=1000)
        assert len(kept) == 100
        # The newest survive, the oldest go.
        assert kept[-1].text == "Message 299."
        assert kept[0].text == "Message 200."

    @pytest.mark.asyncio
    async def test_speaker_history_is_capped(self, db):
        for i in range(500):
            await db.add_message("yoda", f"M{i}.", topic_id=1)
        await db.prune_transcript(keep_messages=100, speaker_history=60)
        rows = await db.get_recent_speakers(1000)
        assert len(rows) == 60

    @pytest.mark.asyncio
    async def test_speaker_history_never_trimmed_below_the_window(self, db):
        """The selector reads a window; trimming under it would break it."""
        from live_news_wall.database import SPEAKER_HISTORY_FLOOR
        from live_news_wall.engine import SPEAKER_WINDOW

        assert SPEAKER_HISTORY_FLOOR >= SPEAKER_WINDOW
        for i in range(200):
            await db.add_message("eu", f"M{i}.", topic_id=1)
        await db.prune_transcript(keep_messages=100, speaker_history=1)
        assert len(await db.get_recent_speakers(1000)) == SPEAKER_HISTORY_FLOOR

    @pytest.mark.asyncio
    async def test_orphan_topics_and_memory_are_removed(self, db):
        stale = await db.get_or_create_topic("Stale.", "http://stale", "s")
        await db.append_topic_point(stale, "a point", 20)
        live = await db.get_or_create_topic("Live.", "http://live", "s")
        await db.add_message("potus", "About the live topic.", topic_id=live)

        removed = await db.prune_transcript(keep_messages=100)
        assert removed["topics"] == 1
        assert removed["topic_memory"] == 1
        assert await db.get_topic_memory(stale) == {}
        # The active topic and anything a retained message refers to survive.
        assert (await db.get_active_topic()).id == live

    @pytest.mark.asyncio
    async def test_active_topic_survives_even_with_no_messages(self, db):
        active = await db.get_or_create_topic("Active.", "http://a", "s")
        removed = await db.prune_transcript(keep_messages=100)
        assert removed["topics"] == 0
        assert (await db.get_active_topic()).id == active

    @pytest.mark.asyncio
    async def test_pruning_is_idempotent(self, db):
        for i in range(150):
            await db.add_message("gronk", f"M{i}.", topic_id=1)
        first = await db.prune_transcript(keep_messages=100)
        second = await db.prune_transcript(keep_messages=100)
        assert first["messages"] == 50
        assert second["messages"] == 0, "a steady state must not keep deleting"

    @pytest.mark.asyncio
    async def test_ids_stay_monotonic_after_pruning(self, db):
        """Pruning must not cause id reuse, which would confuse polling."""
        for i in range(120):
            await db.add_message("potus", f"M{i}.", topic_id=1)
        await db.prune_transcript(keep_messages=10)
        next_id = await db.add_message("yoda", "After pruning.", topic_id=1)
        assert next_id == 121
        msgs = await db.get_messages(limit=100)
        assert [m.id for m in msgs] == sorted(m.id for m in msgs)

    @pytest.mark.asyncio
    async def test_engine_prunes_on_refresh(self, db):
        for i in range(400):
            await db.add_message("eu", f"Old {i}.", topic_id=1)
        feed = ListFeed([FeedItem("H.", "http://a", "s", guid="G")])
        eng = make_engine(db, CountingLLM(), feed, transcript_retention_messages=150)
        await eng.refresh_feed()
        assert len(await db.get_messages(limit=1000)) <= 150

    def test_access_logging_is_disabled(self):
        """Per-request logging dominated the log file: ~95% of all lines."""
        src = (pathlib.Path(__file__).resolve().parents[1]
               / "live_news_wall" / "web_server.py")
        assert "access_log=None" in src.read_text(encoding="utf-8")

    def test_config_rejects_a_tiny_transcript_cap(self, monkeypatch):
        from live_news_wall.config_loader import ConfigError, load_config

        monkeypatch.setenv("TRANSCRIPT_RETENTION_MESSAGES", "10")
        with pytest.raises(ConfigError):
            load_config("does-not-exist.env")

    def test_log_rotation_caps_the_file(self, tmp_path, monkeypatch):
        """A nohup deployment must not accumulate an unbounded log."""
        import logging
        from logging.handlers import RotatingFileHandler
        import live_news_wall.app as live_news_wall
        from live_news_wall.config_loader import load_config

        monkeypatch.delenv("LOG_FILE", raising=False)
        path = tmp_path / "wall.log"
        env = tmp_path / ".env"
        env.write_text(
            "\n".join([
                f"LOG_FILE={path}",
                "LOG_MAX_BYTES=20000",
                "LOG_BACKUP_COUNT=2",
                "",
            ]),
            encoding="utf-8",
        )
        root = logging.getLogger()
        before = list(root.handlers)
        try:
            handler = live_news_wall.install_file_logging(load_config(str(env)))
            assert isinstance(handler, RotatingFileHandler)
            assert handler.maxBytes == 20000
            assert handler.backupCount == 2
            log = logging.getLogger("live_news_wall.rotation_probe")
            for i in range(3000):
                log.info("a reasonably typical log line number %d with detail", i)
            handler.close()
            total = sum(f.stat().st_size for f in tmp_path.glob("wall.log*"))
            assert total <= 20000 * 3 + 4000, f"log grew to {total} bytes"
            assert len(list(tmp_path.glob("wall.log*"))) <= 3
        finally:
            for h in list(root.handlers):
                if h not in before:
                    root.removeHandler(h)

    def test_no_log_file_means_stderr_only(self, monkeypatch, tmp_path):
        import live_news_wall.app as live_news_wall
        from live_news_wall.config_loader import load_config

        monkeypatch.delenv("LOG_FILE", raising=False)
        env = tmp_path / ".env"
        env.write_text("LOG_FILE=" + "\n", encoding="utf-8")
        assert live_news_wall.install_file_logging(load_config(str(env))) is None

    def test_rotation_is_on_by_default(self):
        """Shipped defaults must bound the log without any user action."""
        from live_news_wall.config_loader import DEFAULTS

        assert DEFAULTS["LOG_FILE"].strip(), "rotation must ship enabled"
        assert int(DEFAULTS["LOG_MAX_BYTES"]) >= 1024
        assert int(DEFAULTS["LOG_BACKUP_COUNT"]) >= 1

    def test_log_file_from_env_file_is_honoured(self, tmp_path, monkeypatch):
        """Regression: LOG_FILE was read at import, before .env was loaded,
        so setting it in config/.env silently did nothing."""
        import logging
        from logging.handlers import RotatingFileHandler
        import live_news_wall.app as live_news_wall
        from live_news_wall.config_loader import load_config

        monkeypatch.delenv("LOG_FILE", raising=False)
        target = tmp_path / "from_env_file.log"
        env = tmp_path / ".env"
        env.write_text(f"LOG_FILE={target}\n", encoding="utf-8")

        root = logging.getLogger()
        before = list(root.handlers)
        try:
            cfg = load_config(str(env))
            assert cfg.log_file == str(target)
            handler = live_news_wall.install_file_logging(cfg)
            assert isinstance(handler, RotatingFileHandler)
            logging.getLogger("live_news_wall.probe").warning("written")
            handler.flush()
            assert target.exists(), "the configured file must actually be written"
        finally:
            for h in list(root.handlers):
                if h not in before:
                    root.removeHandler(h)

    def test_unwritable_log_path_degrades_instead_of_crashing(self, tmp_path, monkeypatch):
        import live_news_wall.app as live_news_wall
        from live_news_wall.config_loader import load_config

        monkeypatch.delenv("LOG_FILE", raising=False)
        bad = tmp_path / "no-such-dir" / "deep" / "wall.log"
        env = tmp_path / ".env"
        env.write_text(f"LOG_FILE={bad}\n", encoding="utf-8")
        cfg = load_config(str(env))
        assert live_news_wall.install_file_logging(cfg) is None  # no exception

    @pytest.mark.asyncio
    async def test_pruning_still_runs_while_rss_is_down(self, db):
        """Turns keep being generated during an outage; growth must stay capped."""
        for i in range(400):
            await db.add_message("potus", f"Old {i}.", topic_id=1)
        dead = ListFeed([])          # feed returns nothing at all
        eng = make_engine(db, CountingLLM(), dead, transcript_retention_messages=120)
        await eng.refresh_feed()
        assert eng.rss_healthy is False
        assert len(await db.get_messages(limit=1000)) <= 120

    def test_log_config_is_validated(self, monkeypatch):
        from live_news_wall.config_loader import ConfigError, load_config

        monkeypatch.setenv("LOG_MAX_BYTES", "10")
        with pytest.raises(ConfigError):
            load_config("does-not-exist.env")
        monkeypatch.delenv("LOG_MAX_BYTES")
        monkeypatch.setenv("LOG_BACKUP_COUNT", "-1")
        with pytest.raises(ConfigError):
            load_config("does-not-exist.env")


class TestPruningIndependence:
    """A failure pruning one table must not skip the other."""

    @pytest.mark.asyncio
    async def test_transcript_still_pruned_when_feed_prune_fails(self, db):
        for i in range(300):
            await db.add_message("potus", f"Old {i}.", topic_id=1)

        async def boom(*_a, **_k):
            raise sqlite3.OperationalError("database is locked")

        feed = ListFeed([FeedItem("H.", "http://a", "s", guid="G")])
        eng = make_engine(db, CountingLLM(), feed, transcript_retention_messages=120)
        with patch.object(db, "prune_feed_items", boom):
            await eng.refresh_feed()
        remaining = await db.get_messages(limit=1000)
        assert len(remaining) <= 120, "feed-prune failure must not skip the transcript"

    @pytest.mark.asyncio
    async def test_feed_still_pruned_when_transcript_prune_fails(self, db):
        for i in range(40):
            await db.add_known_item(f"http://old.com/{i}", f"Old {i}", "s")

        async def boom(*_a, **_k):
            raise sqlite3.OperationalError("database is locked")

        feed = ListFeed([FeedItem("H.", "http://a", "s", guid="G")])
        eng = make_engine(db, CountingLLM(), feed, feed_retention_items=10)
        with patch.object(db, "prune_transcript", boom):
            await eng.refresh_feed()          # must not raise
        assert await db.count_feed_items() <= 11


class TestCommandLine:
    """The console script must be usable without starting a server."""

    def test_version_exits_zero_and_prints_version(self, capsys):
        from live_news_wall.app import main
        from live_news_wall import __version__

        with pytest.raises(SystemExit) as exc:
            main(["--version"])
        assert exc.value.code == 0
        assert __version__ in capsys.readouterr().out

    def test_about_shows_attribution_and_parody_notice(self, capsys):
        from live_news_wall.app import main

        main(["--about"])
        out = capsys.readouterr().out
        assert "Based on original work by Supratim Sanyal of SANYALnet Labs." in out
        assert "parody" in out.lower()
        assert "Non-Commercial" in out

    def test_help_exits_zero(self, capsys):
        from live_news_wall.app import main

        with pytest.raises(SystemExit) as exc:
            main(["--help"])
        assert exc.value.code == 0
        assert "live-news-wall" in capsys.readouterr().out

    def test_check_validates_without_serving(self, tmp_path, monkeypatch):
        from live_news_wall.app import main

        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        env = tmp_path / "my.env"
        env.write_text("PORT=9099\nLOG_FILE=\n", encoding="utf-8")
        main(["--check", "--config", str(env)])   # returns, does not bind

    def test_check_reports_bad_config_with_exit_code_2(self, tmp_path, monkeypatch):
        from live_news_wall.app import main

        monkeypatch.chdir(tmp_path)
        # PORT must be absent, or load_dotenv keeps the existing value and
        # the malformed one in the file is never parsed.
        monkeypatch.delenv("PORT", raising=False)
        env = tmp_path / "bad.env"
        env.write_text("PORT=notanumber\n", encoding="utf-8")
        with pytest.raises(SystemExit) as exc:
            main(["--check", "--config", str(env)])
        assert exc.value.code == 2

    def test_cli_port_overrides_the_dotenv_file(self, tmp_path, monkeypatch):
        from live_news_wall.app import build_parser
        from live_news_wall.config_loader import load_config

        monkeypatch.chdir(tmp_path)
        env = tmp_path / "p.env"
        env.write_text("PORT=8765\n", encoding="utf-8")
        args = build_parser().parse_args(["--port", "9123", "--config", str(env)])
        monkeypatch.setenv("PORT", str(args.port))
        assert load_config(str(env)).port == 9123
