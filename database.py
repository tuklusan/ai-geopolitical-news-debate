"""
SQLite persistence layer for Live News Debate Wall.

Stores messages, topics, known feed items, recent speaker history, topic
memory, and monotonically increasing message IDs. Uses a single connection
guarded by an :class:`asyncio.Lock` for cooperative async safety.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True)
class StoredMessage:
    """A persisted conversation message."""

    id: int
    topic_id: Optional[int]
    speaker: str
    text: str
    created_at: float

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "topic_id": self.topic_id,
            "speaker": self.speaker,
            "text": self.text,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class StoredTopic:
    """A persisted RSS headline used as a discussion topic."""

    id: int
    title: str
    link: str
    summary: str
    created_at: float


class Database:
    """Async-friendly SQLite persistence with monotonic message IDs."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def initialize(self) -> None:
        """Open the connection and create tables / sequences."""
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._create_schema()
        self._conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # schema
    # ------------------------------------------------------------------
    def _create_schema(self) -> None:
        c = self._conn
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id          INTEGER PRIMARY KEY,
                topic_id    INTEGER,
                speaker     TEXT NOT NULL,
                text        TEXT NOT NULL,
                created_at  REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS topics (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT NOT NULL,
                link        TEXT NOT NULL,
                summary     TEXT NOT NULL,
                created_at  REAL NOT NULL,
                active      INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS known_feed_items (
                link        TEXT PRIMARY KEY,
                title       TEXT NOT NULL,
                summary     TEXT NOT NULL,
                first_seen  REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS speaker_history (
                seq         INTEGER PRIMARY KEY AUTOINCREMENT,
                speaker     TEXT NOT NULL,
                created_at  REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS topic_memory (
                topic_id    INTEGER PRIMARY KEY,
                memory      TEXT NOT NULL DEFAULT '{}',
                updated_at  REAL NOT NULL
            );
            """
        )

    # ------------------------------------------------------------------
    # messages
    # ------------------------------------------------------------------
    async def next_message_id(self) -> int:
        """Return the next monotonic message id."""
        async with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(id), 0) AS m FROM messages"
            ).fetchone()
            return int(row["m"]) + 1

    async def add_message(
        self,
        speaker: str,
        text: str,
        topic_id: Optional[int] = None,
        created_at: Optional[float] = None,
    ) -> int:
        """Persist a message and return its id. Never stores malformed text."""
        mid = await self.next_message_id()
        ts = float(created_at if created_at is not None else time.time())
        async with self._lock:
            self._conn.execute(
                "INSERT INTO messages (id, topic_id, speaker, text, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (mid, topic_id, speaker, text, ts),
            )
            self._conn.execute(
                "INSERT INTO speaker_history (speaker, created_at) VALUES (?, ?)",
                (speaker, ts),
            )
            self._conn.commit()
        return mid

    async def get_messages(self, since_id: int = 0, limit: int = 1000) -> List[StoredMessage]:
        """Return messages with id > since_id ordered by id ascending."""
        async with self._lock:
            rows = self._conn.execute(
                "SELECT id, topic_id, speaker, text, created_at "
                "FROM messages WHERE id > ? ORDER BY id ASC LIMIT ?",
                (since_id, limit),
            ).fetchall()
        return [StoredMessage(r["id"], r["topic_id"], r["speaker"], r["text"], r["created_at"]) for r in rows]

    async def get_last_speaker(self) -> Optional[str]:
        async with self._lock:
            row = self._conn.execute(
                "SELECT speaker FROM speaker_history ORDER BY seq DESC LIMIT 1"
            ).fetchone()
        return row["speaker"] if row else None

    async def get_recent_speakers(self, n: int = 3) -> List[str]:
        async with self._lock:
            rows = self._conn.execute(
                "SELECT speaker FROM speaker_history ORDER BY seq DESC LIMIT ?", (n,)
            ).fetchall()
        return [r["speaker"] for r in rows]

    async def clear_speaker_history(self) -> None:
        async with self._lock:
            self._conn.execute("DELETE FROM speaker_history")
            self._conn.commit()

    # ------------------------------------------------------------------
    # topics
    # ------------------------------------------------------------------
    async def add_topic(self, title: str, link: str, summary: str) -> int:
        ts = time.time()
        async with self._lock:
            self._conn.execute(
                "UPDATE topics SET active = 0"
            )
            cur = self._conn.execute(
                "INSERT INTO topics (title, link, summary, created_at, active) "
                "VALUES (?, ?, ?, ?, 1)",
                (title, link, summary, ts),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    async def get_active_topic(self) -> Optional[StoredTopic]:
        async with self._lock:
            row = self._conn.execute(
                "SELECT id, title, link, summary, created_at FROM topics "
                "WHERE active = 1 ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if not row:
            return None
        return StoredTopic(row["id"], row["title"], row["link"], row["summary"], row["created_at"])

    # ------------------------------------------------------------------
    # known feed items
    # ------------------------------------------------------------------
    async def is_known_item(self, link: str) -> bool:
        async with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM known_feed_items WHERE link = ?", (link,)
            ).fetchone()
        return row is not None

    async def add_known_item(self, link: str, title: str, summary: str) -> None:
        async with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO known_feed_items (link, title, summary, first_seen) "
                "VALUES (?, ?, ?, ?)",
                (link, title, summary, time.time()),
            )
            self._conn.commit()

    # ------------------------------------------------------------------
    # topic memory
    # ------------------------------------------------------------------
    async def get_topic_memory(self, topic_id: int) -> dict:
        async with self._lock:
            row = self._conn.execute(
                "SELECT memory FROM topic_memory WHERE topic_id = ?", (topic_id,)
            ).fetchone()
        if not row:
            return {}
        try:
            return json.loads(row["memory"]) if row["memory"] else {}
        except Exception:
            return {}

    async def update_topic_memory(self, topic_id: int, memory: dict) -> None:
        data = json.dumps(memory, ensure_ascii=False)
        async with self._lock:
            self._conn.execute(
                "INSERT INTO topic_memory (topic_id, memory, updated_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(topic_id) DO UPDATE SET memory = excluded.memory, "
                "updated_at = excluded.updated_at",
                (topic_id, data, time.time()),
            )
            self._conn.commit()
