from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path

from reviewbot.models import ReviewJob


class QueueStore:
    """SQLite persistence for idempotent webhook jobs and review results."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path
        self._database_path.parent.mkdir(parents=True, exist_ok=True)

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    delivery_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    action TEXT NOT NULL,
                    repository TEXT NOT NULL,
                    pull_request_number INTEGER NOT NULL,
                    webhook_head_sha TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN ('queued', 'running', 'succeeded', 'failed', 'skipped')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_events_state_created
                    ON events (state, created_at);
                CREATE TABLE IF NOT EXISTS reviews (
                    repository TEXT NOT NULL,
                    pull_request_number INTEGER NOT NULL,
                    head_sha TEXT NOT NULL,
                    comment_id INTEGER,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (repository, pull_request_number, head_sha)
                );
                """
            )
            connection.execute(
                "UPDATE events SET state = 'queued', updated_at = CURRENT_TIMESTAMP WHERE state = 'running'"
            )

    def enqueue(self, job: ReviewJob) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO events (
                    delivery_id, event_type, action, repository, pull_request_number,
                    webhook_head_sha, state
                ) VALUES (?, ?, ?, ?, ?, ?, 'queued')
                """,
                (
                    job.delivery_id,
                    job.event_type,
                    job.action,
                    job.repository.lower(),
                    job.pull_request_number,
                    job.webhook_head_sha,
                ),
            )
            return cursor.rowcount == 1

    def claim_next(self) -> tuple[ReviewJob, int] | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT delivery_id, event_type, action, repository,
                       pull_request_number, webhook_head_sha, attempts
                FROM events
                WHERE state = 'queued'
                ORDER BY created_at, delivery_id
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            attempts = int(row[6]) + 1
            connection.execute(
                """
                UPDATE events
                SET state = 'running', attempts = ?, updated_at = CURRENT_TIMESTAMP
                WHERE delivery_id = ? AND state = 'queued'
                """,
                (attempts, row[0]),
            )
            connection.commit()
            return (
                ReviewJob(
                    delivery_id=str(row[0]),
                    event_type=str(row[1]),
                    action=str(row[2]),
                    repository=str(row[3]),
                    pull_request_number=int(row[4]),
                    webhook_head_sha=str(row[5] or ""),
                ),
                attempts,
            )

    def mark_succeeded(self, delivery_id: str) -> None:
        self._set_event_state(delivery_id, "succeeded", None)

    def mark_skipped(self, delivery_id: str, reason: str) -> None:
        self._set_event_state(delivery_id, "skipped", reason)

    def mark_failed(self, delivery_id: str, error: str, *, retry: bool) -> None:
        state = "queued" if retry else "failed"
        self._set_event_state(delivery_id, state, error)

    def has_review(self, repository: str, number: int, head_sha: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM reviews WHERE repository = ? AND pull_request_number = ? AND head_sha = ?",
                (repository.lower(), number, head_sha),
            ).fetchone()
            return row is not None

    def record_review(self, repository: str, number: int, head_sha: str, comment_id: int | None) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO reviews (
                    repository, pull_request_number, head_sha, comment_id
                ) VALUES (?, ?, ?, ?)
                """,
                (repository.lower(), number, head_sha, comment_id),
            )

    def event_state(self, delivery_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute("SELECT state FROM events WHERE delivery_id = ?", (delivery_id,)).fetchone()
            return str(row[0]) if row else None

    def counts(self) -> dict[str, int]:
        with self._connect() as connection:
            rows: Iterable[tuple[str, int]] = connection.execute(
                "SELECT state, COUNT(*) FROM events GROUP BY state"
            ).fetchall()
            return {str(state): int(count) for state, count in rows}

    def _set_event_state(self, delivery_id: str, state: str, error: str | None) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE events
                SET state = ?, last_error = ?, updated_at = CURRENT_TIMESTAMP
                WHERE delivery_id = ?
                """,
                (state, error, delivery_id),
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection
