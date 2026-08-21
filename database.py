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
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 2


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


@dataclass(frozen=True)
class QueuedItem:
    """A feed item eligible to become the active topic."""

    link: str
    title: str
    summary: str


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
        """Open the connection and create tables / sequences.

        Calling this twice must not strand the previous connection, which
        would leak the handle and, for a file database, its WAL lock.
        """
        self.close()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._create_schema()
        self._migrate()
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

            CREATE TABLE IF NOT EXISTS app_state (
                key         TEXT PRIMARY KEY,
                value       TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_messages_topic ON messages (topic_id);
            CREATE INDEX IF NOT EXISTS idx_topics_active ON topics (active);
            """
        )

    def _columns(self, table: str) -> List[str]:
        rows = self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        return [r["name"] for r in rows]

    def _migrate(self) -> None:
        """Add columns introduced after the first release.

        Safe to run repeatedly: each column is added only when absent, so
        databases created by an older build keep their existing rows.
        """
        c = self._conn
        item_cols = self._columns("known_feed_items")
        if "times_discussed" not in item_cols:
            c.execute(
                "ALTER TABLE known_feed_items "
                "ADD COLUMN times_discussed INTEGER NOT NULL DEFAULT 0"
            )
        if "last_discussed" not in item_cols:
            c.execute(
                "ALTER TABLE known_feed_items "
                "ADD COLUMN last_discussed REAL NOT NULL DEFAULT 0"
            )
        if "discuss_seq" not in item_cols:
            # Ordering counter for "least recently discussed". A timestamp
            # cannot order two items discussed within one clock tick.
            c.execute(
                "ALTER TABLE known_feed_items "
                "ADD COLUMN discuss_seq INTEGER NOT NULL DEFAULT 0"
            )
        topic_cols = self._columns("topics")
        if "turns" not in topic_cols:
            c.execute("ALTER TABLE topics ADD COLUMN turns INTEGER NOT NULL DEFAULT 0")
        if "activation_seq" not in topic_cols:
            # Recency of activation, which differs from creation order once a
            # topic can be revisited and reused. A counter rather than a
            # timestamp: the system clock granularity (~16ms on Windows) is
            # coarser than consecutive activations, which would tie.
            c.execute(
                "ALTER TABLE topics ADD COLUMN activation_seq INTEGER NOT NULL DEFAULT 0"
            )
            c.execute("UPDATE topics SET activation_seq = id")
        c.execute(
            "INSERT INTO app_state (key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(SCHEMA_VERSION),),
        )

    # ------------------------------------------------------------------
    # generic key/value state
    # ------------------------------------------------------------------
    async def get_state(self, key: str, default: Optional[str] = None) -> Optional[str]:
        async with self._lock:
            row = self._conn.execute(
                "SELECT value FROM app_state WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else default

    async def set_state(self, key: str, value: str) -> None:
        async with self._lock:
            self._conn.execute(
                "INSERT INTO app_state (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )
            self._conn.commit()

    # ------------------------------------------------------------------
    # messages
    # ------------------------------------------------------------------
    async def next_message_id(self) -> int:
        """Return the next monotonic message id."""
        async with self._lock:
            return self._next_message_id_locked()

    def _next_message_id_locked(self) -> int:
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
        """Persist a message and return its id. Never stores malformed text.

        The id is allocated inside the same lock acquisition that performs
        the insert, so concurrent callers cannot be handed the same id.
        """
        ts = float(created_at if created_at is not None else time.time())
        async with self._lock:
            mid = self._next_message_id_locked()
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

    async def get_latest_messages(self, limit: int = 100) -> List[StoredMessage]:
        """Return the newest ``limit`` messages, oldest first."""
        async with self._lock:
            rows = self._conn.execute(
                "SELECT id, topic_id, speaker, text, created_at "
                "FROM messages ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        rows = list(reversed(rows))
        return [StoredMessage(r["id"], r["topic_id"], r["speaker"], r["text"], r["created_at"]) for r in rows]

    async def count_messages(self) -> int:
        async with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()
        return int(row["n"])

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
    def _next_activation_seq_locked(self) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(activation_seq), 0) AS s FROM topics"
        ).fetchone()
        return int(row["s"]) + 1

    async def add_topic(self, title: str, link: str, summary: str) -> int:
        ts = time.time()
        async with self._lock:
            seq = self._next_activation_seq_locked()
            self._conn.execute("UPDATE topics SET active = 0")
            cur = self._conn.execute(
                "INSERT INTO topics (title, link, summary, created_at, active, turns, activation_seq) "
                "VALUES (?, ?, ?, ?, 1, 0, ?)",
                (title, link, summary, ts, seq),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    async def get_or_create_topic(self, title: str, link: str, summary: str) -> int:
        """Activate the existing topic row for ``link``, or create one.

        Revisiting an earlier headline must not accumulate duplicate topic
        rows, otherwise stored topic memory for that headline is orphaned.
        """
        ts = time.time()
        async with self._lock:
            row = self._conn.execute(
                "SELECT id FROM topics WHERE link = ? ORDER BY id DESC LIMIT 1",
                (link,),
            ).fetchone()
            seq = self._next_activation_seq_locked()
            self._conn.execute("UPDATE topics SET active = 0")
            if row:
                topic_id = int(row["id"])
                self._conn.execute(
                    "UPDATE topics SET active = 1, title = ?, summary = ?, activation_seq = ? "
                    "WHERE id = ?",
                    (title, summary, seq, topic_id),
                )
            else:
                cur = self._conn.execute(
                    "INSERT INTO topics (title, link, summary, created_at, active, turns, activation_seq) "
                    "VALUES (?, ?, ?, ?, 1, 0, ?)",
                    (title, link, summary, ts, seq),
                )
                topic_id = int(cur.lastrowid)
            self._conn.commit()
        return topic_id

    async def get_active_topic(self) -> Optional[StoredTopic]:
        async with self._lock:
            row = self._conn.execute(
                "SELECT id, title, link, summary, created_at FROM topics "
                "WHERE active = 1 ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if not row:
            return None
        return StoredTopic(row["id"], row["title"], row["link"], row["summary"], row["created_at"])

    async def get_topic_turns(self, topic_id: int) -> int:
        async with self._lock:
            row = self._conn.execute(
                "SELECT turns FROM topics WHERE id = ?", (topic_id,)
            ).fetchone()
        return int(row["turns"]) if row else 0

    async def increment_topic_turns(self, topic_id: int) -> int:
        async with self._lock:
            self._conn.execute(
                "UPDATE topics SET turns = turns + 1 WHERE id = ?", (topic_id,)
            )
            row = self._conn.execute(
                "SELECT turns FROM topics WHERE id = ?", (topic_id,)
            ).fetchone()
            self._conn.commit()
        return int(row["turns"]) if row else 0

    async def reset_topic_turns(self, topic_id: int) -> None:
        async with self._lock:
            self._conn.execute(
                "UPDATE topics SET turns = 0 WHERE id = ?", (topic_id,)
            )
            self._conn.commit()

    async def get_recent_topic_links(self, n: int = 3) -> List[str]:
        """Return the links of the ``n`` most recently activated topics.

        Ordered by activation sequence, not by id: a revisited headline
        reuses its original row, so creation order no longer tracks recency.
        """
        async with self._lock:
            rows = self._conn.execute(
                "SELECT link FROM topics ORDER BY activation_seq DESC, id DESC LIMIT ?",
                (n,),
            ).fetchall()
        return [r["link"] for r in rows]

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

    async def get_unseen_items(self, limit: int = 50) -> List[QueuedItem]:
        """Return never-discussed items, newest batch first, feed order within."""
        async with self._lock:
            rows = self._conn.execute(
                "SELECT link, title, summary FROM known_feed_items "
                "WHERE times_discussed = 0 "
                "ORDER BY first_seen DESC, rowid ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return [QueuedItem(r["link"], r["title"], r["summary"]) for r in rows]

    async def get_revisit_item(self, exclude_links: List[str]) -> Optional[QueuedItem]:
        """Return the least recently discussed item not in ``exclude_links``."""
        async with self._lock:
            rows = self._conn.execute(
                "SELECT link, title, summary FROM known_feed_items "
                "WHERE times_discussed > 0 ORDER BY discuss_seq ASC, rowid ASC"
            ).fetchall()
        excluded = set(exclude_links)
        for r in rows:
            if r["link"] not in excluded:
                return QueuedItem(r["link"], r["title"], r["summary"])
        return None

    def _next_discuss_seq_locked(self) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(discuss_seq), 0) AS s FROM known_feed_items"
        ).fetchone()
        return int(row["s"]) + 1

    async def mark_item_discussed(self, link: str) -> None:
        async with self._lock:
            seq = self._next_discuss_seq_locked()
            self._conn.execute(
                "UPDATE known_feed_items "
                "SET times_discussed = times_discussed + 1, last_discussed = ?, "
                "discuss_seq = ? WHERE link = ?",
                (time.time(), seq, link),
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

    async def append_topic_point(self, topic_id: int, point: str, max_points: int = 20) -> List[str]:
        """Append one discussion point to a topic's memory and return the list.

        Read and write happen under a single lock acquisition so two
        concurrent appends cannot lose one another's point.
        """
        async with self._lock:
            row = self._conn.execute(
                "SELECT memory FROM topic_memory WHERE topic_id = ?", (topic_id,)
            ).fetchone()
            memory: Dict[str, Any] = {}
            if row and row["memory"]:
                try:
                    loaded = json.loads(row["memory"])
                    if isinstance(loaded, dict):
                        memory = loaded
                except Exception:
                    memory = {}
            points = memory.get("points")
            if not isinstance(points, list):
                points = []
            points.append(point)
            if len(points) > max_points:
                points = points[-max_points:]
            memory["points"] = points
            self._conn.execute(
                "INSERT INTO topic_memory (topic_id, memory, updated_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(topic_id) DO UPDATE SET memory = excluded.memory, "
                "updated_at = excluded.updated_at",
                (topic_id, json.dumps(memory, ensure_ascii=False), time.time()),
            )
            self._conn.commit()
        return list(points)
