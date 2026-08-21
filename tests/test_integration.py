"""
Integration tests: HTTP server endpoints, SQLite persistence, and rendered
HTML fixture inspection. No real RSS feed or real model API required.
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from database import Database
from engine import ConversationEngine
from web_server import WebServer, build_html_page
from tests import fixtures


@pytest_asyncio.fixture
async def setup():
    db = Database(":memory:")
    db.initialize()
    feed = MagicMock()
    feed.fetch_items = AsyncMock(return_value=[])
    cfg = MagicMock()
    cfg.has_api_key = False
    cfg.message_min_delay_seconds = 0
    cfg.message_max_delay_seconds = 0
    cfg.rss_refresh_interval_seconds = 9999
    eng = ConversationEngine(db, feed, None, cfg)
    web_srv = WebServer(db, eng)
    server = TestServer(web_srv._app)
    client = TestClient(server)
    await client.start_server()
    yield db, eng, client
    await client.close()
    db.close()


class TestEndpoints:
    @pytest.mark.asyncio
    async def test_index_returns_html(self, setup):
        db, eng, client = setup
        resp = await client.get("/")
        assert resp.status == 200
        text = await resp.text()
        assert "<html" in text.lower()
        assert "AI Parody" in text
        assert "disclaimer" in text.lower()

    @pytest.mark.asyncio
    async def test_index_has_top_and_bottom_disclaimers(self, setup):
        db, eng, client = setup
        text = await (await client.get("/")).text()
        # Two disclaimer divs.
        assert text.count('class="disclaimer') >= 2
        assert "disclaimer top" in text
        assert "disclaimer bottom" in text

    @pytest.mark.asyncio
    async def test_messages_endpoint(self, setup):
        db, eng, client = setup
        await db.add_message("potus", "Test message.", topic_id=1)
        resp = await client.get("/api/messages")
        assert resp.status == 200
        data = await resp.json()
        assert "messages" in data
        assert len(data["messages"]) == 1
        assert data["messages"][0]["text"] == "Test message."

    @pytest.mark.asyncio
    async def test_messages_since(self, setup):
        db, eng, client = setup
        await db.add_message("potus", "First.", topic_id=1)
        mid2 = await db.add_message("yoda", "Second, it is.", topic_id=1)
        resp = await client.get(f"/api/messages?since={mid2 - 1}")
        data = await resp.json()
        assert len(data["messages"]) == 1
        assert data["messages"][0]["text"] == "Second, it is."

    @pytest.mark.asyncio
    async def test_healthz(self, setup):
        db, eng, client = setup
        resp = await client.get("/healthz")
        assert resp.status in (200, 503)
        data = await resp.json()
        assert "rss_healthy" in data
        assert "model_healthy" in data


class TestPersistence:
    @pytest.mark.asyncio
    async def test_messages_persisted(self, tmp_path):
        db = Database(str(tmp_path / "p.db"))
        db.initialize()
        await db.add_message("potus", "Persisted.", topic_id=1)
        mid = await db.add_message("yoda", "Also persisted, this is.", topic_id=1)
        msgs = await db.get_messages()
        assert len(msgs) == 2
        assert msgs[1].id == mid
        db.close()

    @pytest.mark.asyncio
    async def test_topics_persisted(self, tmp_path):
        db = Database(str(tmp_path / "t.db"))
        db.initialize()
        tid = await db.add_topic("Headline.", "http://x", "Summary.")
        topic = await db.get_active_topic()
        assert topic.id == tid
        assert topic.title == "Headline."
        db.close()

    @pytest.mark.asyncio
    async def test_known_items_persisted(self, tmp_path):
        db = Database(str(tmp_path / "k.db"))
        db.initialize()
        assert not await db.is_known_item("http://a")
        await db.add_known_item("http://a", "Title", "Summary")
        assert await db.is_known_item("http://a")
        db.close()

    @pytest.mark.asyncio
    async def test_speaker_history_persisted(self, tmp_path):
        db = Database(str(tmp_path / "s.db"))
        db.initialize()
        await db.add_message("potus", "Hi.", topic_id=1)
        await db.add_message("yoda", "Hi too.", topic_id=1)
        last = await db.get_last_speaker()
        assert last == "yoda"
        recent = await db.get_recent_speakers(2)
        assert recent == ["yoda", "potus"]
        db.close()

    @pytest.mark.asyncio
    async def test_topic_memory_persisted(self, tmp_path):
        db = Database(str(tmp_path / "m.db"))
        db.initialize()
        await db.update_topic_memory(1, {"key": "value"})
        mem = await db.get_topic_memory(1)
        assert mem == {"key": "value"}
        db.close()


class TestRenderedHTML:
    def test_html_contains_persona_cards(self):
        html = build_html_page()
        assert "persona-card" in html
        assert "POTUS" in html
        assert "Gronk Vellumthud" in html
        assert "Yoda" in html
        assert "European Commission" in html

    def test_html_contains_scroll_logic(self):
        html = build_html_page()
        assert "isNearBottom" in html
        assert "scrollToBottom" in html
        assert "jumpBtn" in html
        assert "Jump to latest" in html

    def test_html_responsive_media_query(self):
        html = build_html_page()
        assert "@media (max-width: 768px)" in html
        assert "flex-direction: column" in html

    def test_html_no_api_key_leak(self):
        html = build_html_page()
        # No placeholder key text should appear.
        assert "replace-with-your-real-api-key" not in html
        assert "Bearer" not in html

    def test_html_transcript_container_id(self):
        html = build_html_page()
        assert 'id="transcript"' in html
        assert 'overflow-y: auto' in html or "overflow-y:auto" in html

    def test_html_uses_textContent_not_innerHTML_for_body(self):
        """Ensure message bodies use textContent to prevent HTML/JSON injection."""
        html = build_html_page()
        assert "body.textContent = msg.text" in html

    def test_html_no_dead_escapeHtml_function(self):
        """The buggy dead escapeHtml function must not be present."""
        html = build_html_page()
        assert "escapeHtml" not in html

    def test_html_no_innerHTML_for_message_content(self):
        """No use of innerHTML for message bodies (XSS prevention).

        The sidebar legitimately uses 'container.innerHTML = \"\"' to clear
        cards; that is safe. Only message-content innerHTML is forbidden.
        """
        html = build_html_page()
        assert "innerHTML = msg" not in html
        assert ".innerHTML = msg.text" not in html



class TestNoStructuredArtifactsInFixture:
    def test_fixture_messages_clean(self):
        """All valid fixture messages contain no JSON/fences/field names."""
        import re
        all_msgs = [
            fixtures.VALID_POTUS, fixtures.VALID_EU,
            fixtures.VALID_GRONK, fixtures.VALID_YODA,
            fixtures.VALID_POTUS_2, fixtures.VALID_EU_2,
            fixtures.VALID_GRONK_2, fixtures.VALID_YODA_2,
        ]
        for msg in all_msgs:
            assert "```" not in msg
            assert "~~~" not in msg
            assert '"text"' not in msg
            assert '"speaker"' not in msg
        for msg in all_msgs:
            assert "```" not in msg
            assert "~~~" not in msg
            assert '"text"' not in msg
            assert '"speaker"' not in msg
            assert not re.search(r'^\s*[\{\[]', msg)


class TestStartupFailureCleanup:
    """Verify that the aiohttp ClientSession is cleanly closed when the
    web server fails to bind (e.g. port already in use)."""

    @pytest.mark.asyncio
    async def test_engine_session_closed_on_web_start_failure(self):
        """If web.start() raises after engine.start(), the engine's
        ClientSession must be closed, not left dangling."""
        from live_news_wall import Application

        app = Application.__new__(Application)
        app._cfg = MagicMock()
        app._cfg.db_path = ":memory:"
        app._cfg.rss_feed_url = "http://localhost/dummy"
        app._cfg.has_api_key = False
        app._cfg.host = "127.0.0.1"
        app._cfg.port = 1  # invalid port to trigger bind failure
        app._cfg.llm_base_url = ""
        app._cfg.llm_model = ""
        app._cfg.llm_temperature = 0.5
        app._cfg.llm_max_tokens = 100
        app._cfg.llm_timeout_seconds = 10

        app._db = Database(":memory:")
        app._db.initialize()
        app._feed = MagicMock()
        app._feed.fetch_items = AsyncMock(return_value=[])
        app._cfg.message_min_delay_seconds = 0
        app._cfg.message_max_delay_seconds = 0
        app._cfg.rss_refresh_interval_seconds = 9999
        app._llm = None
        app._engine = ConversationEngine(app._db, app._feed, None, app._cfg)

        # Use a real WebServer but with a port that will fail to bind.
        app._web = WebServer(app._db, app._engine)

        with pytest.raises(Exception):
            await app.run()

        # The engine's HTTP session must have been closed.
        assert app._engine._http_session is None or app._engine._http_session.closed
        app._db.close()
