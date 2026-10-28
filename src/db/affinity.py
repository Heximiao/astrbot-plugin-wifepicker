"""Rolling, evidence-based affinity. All timestamps are UTC Unix seconds.

No permanent score is carried forward: every automatic contribution is rebuilt
from at most 60 days of evidence. Connections belong to individual operations.
"""

import hashlib
import math
import sqlite3
import time
from contextlib import closing
from pathlib import Path

DAY = 86400
RETENTION = 60 * DAY
BASE_SCORE = 30.0
POKE_COOLDOWN = 30 * 60
POKE_PAIR_DAILY_LIMIT = 3
POKE_WEIGHT = 0.1
SCOPE_SQL = "platform=? AND bot_id=? AND group_id=?"


class AffinityStore:
    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS affinity_messages (
                    platform TEXT NOT NULL, bot_id TEXT NOT NULL, group_id TEXT NOT NULL,
                    message_id TEXT NOT NULL, sender_id TEXT NOT NULL,
                    body TEXT NOT NULL, created_at REAL NOT NULL,
                    PRIMARY KEY(platform, bot_id, group_id, message_id)
                );
                CREATE INDEX IF NOT EXISTS affinity_messages_time ON affinity_messages(created_at);
                CREATE INDEX IF NOT EXISTS affinity_messages_scope_time
                    ON affinity_messages(platform,bot_id,group_id,created_at);
                CREATE TABLE IF NOT EXISTS affinity_interactions (
                    id INTEGER PRIMARY KEY,
                    platform TEXT NOT NULL, bot_id TEXT NOT NULL, group_id TEXT NOT NULL,
                    message_id TEXT NOT NULL, source_id TEXT NOT NULL, target_id TEXT NOT NULL,
                    body_hash TEXT NOT NULL, kind TEXT NOT NULL,
                    sentiment REAL NOT NULL DEFAULT 0, created_at REAL NOT NULL,
                    UNIQUE(platform, bot_id, group_id, message_id, target_id)
                );
                CREATE INDEX IF NOT EXISTS affinity_interactions_pair
                    ON affinity_interactions(platform,bot_id,group_id,source_id,target_id,created_at);
                CREATE INDEX IF NOT EXISTS affinity_interactions_time ON affinity_interactions(created_at);
                CREATE TABLE IF NOT EXISTS affinity_game_events (
                    platform TEXT NOT NULL, bot_id TEXT NOT NULL, group_id TEXT NOT NULL,
                    event_id TEXT NOT NULL, kind TEXT NOT NULL,
                    actor_id TEXT NOT NULL, target_id TEXT NOT NULL, created_at REAL NOT NULL,
                    PRIMARY KEY(platform, bot_id, group_id, event_id, kind)
                );
                CREATE INDEX IF NOT EXISTS affinity_games_scope_time
                    ON affinity_game_events(platform,bot_id,group_id,created_at);
                CREATE INDEX IF NOT EXISTS affinity_games_time ON affinity_game_events(created_at);
                CREATE TABLE IF NOT EXISTS affinity_manual (
                    platform TEXT NOT NULL, bot_id TEXT NOT NULL, group_id TEXT NOT NULL,
                    source_id TEXT NOT NULL, target_id TEXT NOT NULL,
                    offset REAL NOT NULL, created_at REAL NOT NULL,
                    PRIMARY KEY(platform, bot_id, group_id, source_id, target_id)
                );
                CREATE INDEX IF NOT EXISTS affinity_manual_time ON affinity_manual(created_at);
            """)
        self.cleanup()

    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        return db

    def cleanup(self, now=None):
        now = time.time() if now is None else now
        deleted = {}
        with closing(self.connect()) as db, db:
            for table in ("affinity_messages", "affinity_interactions", "affinity_game_events", "affinity_manual"):
                deleted[table] = db.execute(f"DELETE FROM {table} WHERE created_at < ?", (now - RETENTION,)).rowcount
            # Reuse freed pages and truncate the WAL; no expensive full VACUUM.
            db.commit()
            db.execute("PRAGMA wal_checkpoint(PASSIVE)")
        return deleted

    def message(self, scope, message_id, sender, body, now):
        """Keep bounded text for reply resolution, including non-directed messages.

        At most 2,000 messages per group per UTC day, 512 characters per message.
        No attachments, quoted bodies or names are retained.
        """
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute(
                f"SELECT COUNT(*) FROM affinity_messages WHERE {SCOPE_SQL} AND created_at>=?",
                (*scope, math.floor(now / DAY) * DAY),
            ).fetchone()[0]
            if count >= 2000:
                return False
            return db.execute(
                "INSERT OR IGNORE INTO affinity_messages VALUES (?,?,?,?,?,?,?)",
                (*scope, message_id, sender, body[:512], now),
            ).rowcount > 0

    def reply_sender(self, scope, message_id, now):
        with closing(self.connect()) as db:
            row = db.execute(
                f"SELECT sender_id FROM affinity_messages WHERE {SCOPE_SQL} AND message_id=? AND created_at>=?",
                (*scope, message_id, now - RETENTION),
            ).fetchone()
            return row[0] if row else None

    def interaction(self, scope, message_id, source, target, body, kind, sentiment, now):
        digest = hashlib.sha256("".join(body.casefold().split()).encode()).hexdigest()
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if kind == "poke":
                # Pair-wide rolling-day quota, directional cooldown. Reading
                # and inserting under the same transaction survives restarts
                # and concurrent deliveries without issuing extra credit.
                recent = db.execute(
                    f"""SELECT source_id,created_at FROM affinity_interactions
                        WHERE {SCOPE_SQL} AND kind='poke' AND created_at>? AND created_at<=?
                        AND ((source_id=? AND target_id=?) OR (source_id=? AND target_id=?))""",
                    (*scope, now - DAY, now, source, target, target, source),
                ).fetchall()
                if len(recent) >= POKE_PAIR_DAILY_LIMIT or any(
                    row["source_id"] == source and now - row["created_at"] < POKE_COOLDOWN for row in recent
                ):
                    return None
                sentiment = 0
            # One directional observation per ten minutes. Repeating the same
            # text within a day also earns no additional observation. Pokes
            # have their own quota and never suppress normal chat evidence.
            exists = None if kind == "poke" else db.execute(
                f"""SELECT 1 FROM affinity_interactions WHERE {SCOPE_SQL}
                    AND kind!='poke' AND source_id=? AND target_id=? AND
                    created_at<=? AND (created_at>? OR (body_hash=? AND created_at>?)) LIMIT 1""",
                (*scope, source, target, now, now - 600, digest, now - DAY),
            ).fetchone()
            if exists:
                return None
            cursor = db.execute(
                """INSERT OR IGNORE INTO affinity_interactions
                (platform,bot_id,group_id,message_id,source_id,target_id,body_hash,kind,sentiment,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (*scope, message_id, source, target, digest, kind, sentiment, now),
            )
            return cursor.lastrowid if cursor.rowcount else None

    def set_sentiment(self, interaction_id, sentiment):
        with closing(self.connect()) as db, db:
            db.execute("UPDATE affinity_interactions SET sentiment=? WHERE id=?",
                       (max(-1.0, min(1.0, sentiment)), interaction_id))

    def game(self, scope, event_id, kind, actor, target, now):
        if kind not in {"accepted", "rejected", "forced"}:
            raise ValueError("Unknown affinity game event")
        with closing(self.connect()) as db, db:
            db.execute("INSERT OR IGNORE INTO affinity_game_events VALUES (?,?,?,?,?,?,?,?)",
                       (*scope, event_id, kind, actor, target, now))

    def _automatic(self, db, scope, source, target, now, aggressive):
        rows = db.execute(
            f"""SELECT source_id,kind,sentiment,created_at FROM affinity_interactions
                WHERE {SCOPE_SQL} AND created_at BETWEEN ? AND ? AND
                ((source_id=? AND target_id=?) OR (source_id=? AND target_id=?))
                ORDER BY created_at""",
            (*scope, now - RETENTION, now, source, target, target, source),
        ).fetchall()
        pokes = [row for row in rows if row["kind"] == "poke" and row["source_id"] == source]
        rows = [row for row in rows if row["kind"] != "poke"]
        poke_score = min(3, sum(POKE_WEIGHT * 2 ** (-max(0, now - row["created_at"]) / DAY / 14) for row in pokes))
        days = {}
        for row in rows:
            bucket = days.setdefault(int(row["created_at"] // DAY), [0, 0, [], row["created_at"]])
            direction = 0 if row["source_id"] == source else 1
            bucket[direction] += 1
            if direction == 0 and row["sentiment"]:
                bucket[2].append(row["sentiment"])
        familiarity = friendliness = 0.0
        mutual_days = 0
        for outgoing, incoming, sentiments, at in days.values():
            age = max(0, now - at) / DAY
            if outgoing and incoming:
                mutual_days += 1
                # Mutual days matter more than a burst of one-sided mentions.
                balance = min(outgoing, incoming) / max(outgoing, incoming)
                familiarity += (1 + min(outgoing + incoming, 12) / 12) * (0.5 + 0.5 * balance) * 2 ** (-age / 30)
            # A single emotional sentence cannot independently swing a score.
            if len(sentiments) >= 2:
                friendliness += max(-2, min(2, sum(sentiments))) * 2 ** (-age / 14)
        familiarity = min(30, familiarity)
        friendliness = max(-20, min(20, friendliness))
        games = db.execute(
            f"""SELECT kind,actor_id,target_id,created_at FROM affinity_game_events
                WHERE {SCOPE_SQL} AND created_at BETWEEN ? AND ? AND
                ((actor_id=? AND target_id=?) OR (actor_id=? AND target_id=?))
                ORDER BY created_at""",
            (*scope, now - RETENTION, now, source, target, target, source),
        ).fetchall()
        game_days = {}
        rejected = []
        relevant_games = 0
        for row in games:
            kind, actor, at = row["kind"], row["actor_id"], row["created_at"]
            if kind == "accepted" or (kind == "forced" and actor == source) or (kind == "rejected" and row["target_id"] == source):
                relevant_games += 1
            day = int(at // DAY)
            contribution = 0.0
            if kind == "accepted":
                contribution = 5
            elif kind == "forced" and actor == source:
                contribution = 2
            elif kind == "rejected" and row["target_id"] == source:
                rejected = [t for t in rejected if at - t <= 7 * DAY]
                rejected.append(at)
                # First two explicit rejections in a rolling week do not hurt.
                if len(rejected) >= 3:
                    contribution = -0.5
            if contribution:
                slot = game_days.setdefault(day, [0.0, 0.0, at])
                slot[0 if contribution > 0 else 1] += contribution
        game_score = sum((min(8, pos) + max(-2, neg)) * 2 ** (-max(0, now - at) / DAY / 30)
                         for pos, neg, at in game_days.values())
        game_score = max(-10, min(30, game_score))
        multiplier = 1.6 if aggressive else 1.0
        score = max(0, min(100, BASE_SCORE + multiplier * (familiarity + friendliness + game_score + poke_score)))
        return {"score": score, "familiarity": familiarity, "friendliness": friendliness,
                "game": game_score, "mutual_days": mutual_days, "observations": len(rows),
                "poke": poke_score, "poke_observations": len(pokes),
                "has_data": bool(rows or relevant_games or pokes), "manual": False}

    def score(self, scope, source, target, aggressive=False, now=None):
        now = time.time() if now is None else now
        with closing(self.connect()) as db:
            result = self._automatic(db, scope, source, target, now, aggressive)
            row = db.execute(
                f"SELECT offset,created_at FROM affinity_manual WHERE {SCOPE_SQL} AND source_id=? AND target_id=? AND created_at BETWEEN ? AND ?",
                (*scope, source, target, now - RETENTION, now),
            ).fetchone()
            if row:
                result["score"] = max(0, min(100, result["score"] + row["offset"] * 2 ** (-max(0, now - row["created_at"]) / DAY / 45)))
                result.update(has_data=True, manual=True)
            return result if result["has_data"] else None

    def adjust(self, scope, source, target, value, relative=False, aggressive=False, now=None):
        if not math.isfinite(value):
            raise ValueError("Adjustment must be finite")
        now = time.time() if now is None else now
        # Serialize adjustments with observations and other adjustments.
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            auto = self._automatic(db, scope, source, target, now, aggressive)["score"]
            row = db.execute(
                f"SELECT offset,created_at FROM affinity_manual WHERE {SCOPE_SQL} AND source_id=? AND target_id=? AND created_at BETWEEN ? AND ?",
                (*scope, source, target, now - RETENTION, now),
            ).fetchone()
            old = auto + (row["offset"] * 2 ** (-max(0, now - row["created_at"]) / DAY / 45) if row else 0)
            wanted = max(0, min(100, max(0, min(100, old)) + value if relative else value))
            db.execute("""INSERT INTO affinity_manual VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(platform,bot_id,group_id,source_id,target_id)
                DO UPDATE SET offset=excluded.offset,created_at=excluded.created_at""",
                (*scope, source, target, wanted - auto, now))
            return wanted
