"""Tiny SQLite-backed store for processed-message IDs.

Avoids re-processing the same email if the agent restarts or the message
isn't successfully marked as read upstream.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


class StateStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._init()

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS processed_messages (
                    message_id TEXT PRIMARY KEY,
                    processed_at TEXT NOT NULL,
                    is_meeting INTEGER NOT NULL,
                    confidence REAL,
                    calendar_event_id TEXT,
                    notified INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.commit()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
        finally:
            conn.close()

    def already_processed(self, message_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT 1 FROM processed_messages WHERE message_id = ?",
                (message_id,),
            )
            return cur.fetchone() is not None

    def mark_processed(
        self,
        message_id: str,
        *,
        is_meeting: bool,
        confidence: float | None,
        calendar_event_id: str | None,
        notified: bool,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO processed_messages
                    (message_id, processed_at, is_meeting, confidence, calendar_event_id, notified)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    datetime.now(timezone.utc).isoformat(),
                    1 if is_meeting else 0,
                    confidence,
                    calendar_event_id,
                    1 if notified else 0,
                ),
            )
            conn.commit()
