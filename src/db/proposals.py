"""Proposal history. Each operation owns its connection and runs off the event loop."""

import asyncio
import sqlite3
import time
from pathlib import Path
from uuid import uuid4

DAY = 86400
RETENTION_DAYS = 60
RANKING_DAYS = 30


def proposal_scope(event) -> tuple[str, str, str]:
    # Platform instance IDs distinguish multiple adapters of the same type.
    platform = str(event.get_platform_id() or event.get_platform_name())
    return platform, str(event.get_self_id()), str(event.get_group_id())


class ProposalStore:
    def __init__(self, path: str):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self):
        connection = self._connect()
        try:
            with connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
                row = connection.execute("SELECT version FROM schema_version").fetchone()
                if row is not None and row[0] != 1:
                    raise RuntimeError(f"Unsupported database schema version: {row[0]}")
                connection.execute("""
                    CREATE TABLE IF NOT EXISTS proposal_events (
                        id TEXT PRIMARY KEY,
                        platform TEXT NOT NULL,
                        bot_id TEXT NOT NULL,
                        group_id TEXT NOT NULL,
                        proposer_id TEXT NOT NULL,
                        target_id TEXT NOT NULL,
                        proposer_name TEXT NOT NULL,
                        target_name TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending'
                            CHECK(status IN ('pending','accepted','rejected','expired','cancelled')),
                        resolved_at REAL
                    )
                """)
                connection.execute("""CREATE INDEX IF NOT EXISTS proposal_scope_time
                    ON proposal_events(platform, bot_id, group_id, created_at)""")
                connection.execute("""CREATE INDEX IF NOT EXISTS proposal_time
                    ON proposal_events(created_at)""")
                if row is None:
                    connection.execute("INSERT INTO schema_version VALUES (1)")
        finally:
            connection.close()
        self._cleanup(time.time(), expire_pending=True)

    async def create(self, scope, proposer_id, target_id, proposer_name, target_name, *, now=None):
        return await asyncio.to_thread(
            self._create, scope, proposer_id, target_id, proposer_name, target_name,
            time.time() if now is None else now,
        )

    def _create(self, scope, proposer_id, target_id, proposer_name, target_name, now):
        request_id = uuid4().hex
        connection = self._connect()
        try:
            with connection:
                connection.execute("""INSERT INTO proposal_events
                    (id, platform, bot_id, group_id, proposer_id, target_id,
                     proposer_name, target_name, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (request_id, *scope, str(proposer_id), str(target_id),
                     str(proposer_name), str(target_name), now))
        finally:
            connection.close()
        return request_id

    async def resolve(self, request_id, status, *, now=None):
        if status not in {"accepted", "rejected", "expired", "cancelled"}:
            raise ValueError(f"Invalid proposal result: {status}")
        await asyncio.to_thread(self._resolve, request_id, status, time.time() if now is None else now)

    def _resolve(self, request_id, status, now):
        connection = self._connect()
        try:
            with connection:
                connection.execute("""UPDATE proposal_events SET status=?, resolved_at=?
                    WHERE id=? AND status='pending'""", (status, now, request_id))
        finally:
            connection.close()

    async def cleanup(self, *, now=None):
        await asyncio.to_thread(self._cleanup, time.time() if now is None else now)

    def _cleanup(self, now, *, expire_pending=False):
        connection = self._connect()
        try:
            with connection:
                connection.execute("DELETE FROM proposal_events WHERE created_at < ?",
                                   (now - RETENTION_DAYS * DAY,))
                # At startup only: a live, timely acceptance may still be
                # waiting for its DB write when periodic cleanup runs.
                if expire_pending:
                    connection.execute("""UPDATE proposal_events
                        SET status='expired', resolved_at=created_at+30
                        WHERE status='pending' AND created_at+30 < ?""", (now,))
        finally:
            connection.close()

    async def ranking(self, scope, *, now=None, limit=10):
        return await asyncio.to_thread(self._ranking, scope, time.time() if now is None else now, limit)

    def _ranking(self, scope, now, limit):
        self._cleanup(now)
        connection = self._connect()
        try:
            rows = connection.execute("""
                SELECT proposer_id, COUNT(DISTINCT target_id) AS proposal_count,
                    COUNT(DISTINCT CASE WHEN status='accepted' THEN target_id END) AS success_count
                FROM proposal_events
                WHERE platform=? AND bot_id=? AND group_id=? AND created_at>=? AND created_at<=?
                GROUP BY proposer_id
                ORDER BY proposal_count DESC, proposer_id ASC LIMIT ?
            """, (*scope, now - RANKING_DAYS * DAY, now, limit)).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                name = connection.execute("""SELECT proposer_name FROM proposal_events
                    WHERE platform=? AND bot_id=? AND group_id=? AND proposer_id=?
                        AND created_at>=? AND created_at<=?
                    ORDER BY created_at DESC, id DESC LIMIT 1""",
                    (*scope, item["proposer_id"], now - RANKING_DAYS * DAY, now)).fetchone()
                item["proposer_name"] = name[0] if name else item["proposer_id"]
                result.append(item)
            return result
        finally:
            connection.close()
