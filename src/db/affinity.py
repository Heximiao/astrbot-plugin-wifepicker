"""Rolling, evidence-based affinity. All timestamps are UTC Unix seconds.

No permanent score is carried forward: every automatic contribution is rebuilt
from at most 60 days of evidence. Connections belong to individual operations.
"""

import hashlib
import json
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
EMOTION_HALF_LIFE_DAYS = 20
POKE_HALF_LIFE_DAYS = 20
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
                CREATE TABLE IF NOT EXISTS affinity_score_audit (
                    id INTEGER PRIMARY KEY,
                    platform TEXT NOT NULL, bot_id TEXT NOT NULL, group_id TEXT NOT NULL,
                    source_id TEXT NOT NULL, target_id TEXT NOT NULL,
                    reason TEXT NOT NULL, evidence_id TEXT NOT NULL,
                    before_json TEXT, after_json TEXT, created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS affinity_audit_pair
                    ON affinity_score_audit(platform,bot_id,group_id,source_id,target_id,created_at);
                CREATE INDEX IF NOT EXISTS affinity_audit_time ON affinity_score_audit(created_at);
                CREATE TABLE IF NOT EXISTS affinity_profiles (
                    platform TEXT NOT NULL, bot_id TEXT NOT NULL, group_id TEXT NOT NULL,
                    user_id TEXT NOT NULL, name TEXT NOT NULL, created_at REAL NOT NULL,
                    PRIMARY KEY(platform,bot_id,group_id,user_id)
                );
                CREATE INDEX IF NOT EXISTS affinity_profiles_time ON affinity_profiles(created_at);
                CREATE TABLE IF NOT EXISTS affinity_group_names (
                    platform TEXT NOT NULL, bot_id TEXT NOT NULL, group_id TEXT NOT NULL,
                    name TEXT NOT NULL, created_at REAL NOT NULL,
                    PRIMARY KEY(platform,bot_id,group_id)
                );
                CREATE TABLE IF NOT EXISTS web_group_profiles (
                    platform TEXT NOT NULL, bot_id TEXT NOT NULL, group_id TEXT NOT NULL,
                    group_name TEXT NOT NULL, names_json TEXT NOT NULL,
                    checked_at REAL NOT NULL, created_at REAL NOT NULL, status TEXT NOT NULL,
                    PRIMARY KEY(platform,bot_id,group_id)
                );
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
            for table in ("affinity_messages", "affinity_interactions", "affinity_game_events", "affinity_manual",
                          "affinity_score_audit", "affinity_profiles", "affinity_group_names", "web_group_profiles"):
                deleted[table] = db.execute(f"DELETE FROM {table} WHERE created_at < ?", (now - RETENTION,)).rowcount
            # Reuse freed pages and truncate the WAL; no expensive full VACUUM.
            db.commit()
            db.execute("PRAGMA wal_checkpoint(PASSIVE)")
        return deleted

    def profiles(self, scope, names, now, group_name="", *, authoritative_ids=None):
        """Keep current display names by immutable IDs, without alias history.

        Sender profiles and member-list results may update existing names.
        Names copied from mentions/replies only fill an unknown profile.
        """
        authoritative_ids = None if authoritative_ids is None else set(map(str, authoritative_ids))
        with closing(self.connect()) as db, db:
            for user_id, name in names.items():
                name = " ".join(str(name or "").split())[:100]
                if not name or name.casefold() in {"unknown", "none", "null"} or name in {str(user_id), f"用户({user_id})"}:
                    continue
                if authoritative_ids is not None and str(user_id) not in authoritative_ids:
                    db.execute("""INSERT INTO affinity_profiles VALUES (?,?,?,?,?,?)
                        ON CONFLICT(platform,bot_id,group_id,user_id)
                        DO UPDATE SET name=excluded.name,created_at=excluded.created_at
                        WHERE affinity_profiles.created_at<?""", (*scope, str(user_id), name, now, now - RETENTION))
                    continue
                db.execute("""INSERT INTO affinity_profiles VALUES (?,?,?,?,?,?)
                    ON CONFLICT(platform,bot_id,group_id,user_id)
                    DO UPDATE SET name=excluded.name,created_at=excluded.created_at
                    WHERE excluded.created_at>=affinity_profiles.created_at""", (*scope, str(user_id), name, now))
            if group_name:
                db.execute("""INSERT INTO affinity_group_names VALUES (?,?,?,?,?)
                    ON CONFLICT(platform,bot_id,group_id)
                    DO UPDATE SET name=excluded.name,created_at=excluded.created_at
                    WHERE excluded.created_at>=affinity_group_names.created_at""",
                    (*scope, " ".join(str(group_name).split())[:100], now))

    def _pair_before(self, db, scope, source, target, now, aggressive):
        return {(a, b): self._score(db, scope, a, b, now, aggressive) for a, b in ((source, target), (target, source))}

    def _audit(self, db, scope, source, target, before, after, reason, evidence_id, now):
        keys = ("score", "base", "multiplier", "familiarity", "friendliness", "game", "poke",
                "manual_adjustment", "automatic_score", "mutual_days", "observations", "poke_observations")
        def pack(row):
            return json.dumps({key: row.get(key, 0) for key in keys}, ensure_ascii=False) if row else None
        if reason not in {"time", "mode"}:
            previous = db.execute(f"""SELECT after_json,created_at FROM affinity_score_audit
                WHERE {SCOPE_SQL} AND source_id=? AND target_id=? ORDER BY created_at DESC,id DESC LIMIT 1""",
                (*scope, source, target)).fetchone()
            old = json.loads(previous["after_json"]) if previous and previous["after_json"] else None
            if previous and previous["created_at"] < now and self._different(old, before):
                gap_reason = "mode" if old and before and old.get("multiplier") != before["multiplier"] else "time"
                self._audit(db, scope, source, target, old, before, gap_reason, "", now)
        db.execute("""INSERT INTO affinity_score_audit
            (platform,bot_id,group_id,source_id,target_id,reason,evidence_id,before_json,after_json,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (*scope, source, target, reason, str(evidence_id), pack(before), pack(after), now))

    @staticmethod
    def _different(before, after):
        if before is None or after is None:
            return (before is None) != (after is None)
        return any(abs(float(before.get(key, 0)) - float(after.get(key, 0))) > 0.000001
                   for key in ("score", "multiplier", "familiarity", "friendliness", "game", "poke", "manual_adjustment"))

    def _audit_pair(self, db, scope, before, reason, evidence_id, now, aggressive):
        for (source, target), previous in before.items():
            current = self._score(db, scope, source, target, now, aggressive)
            if previous is not None or current is not None:
                self._audit(db, scope, source, target, previous, current, reason, evidence_id, now)

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

    def interaction(self, scope, message_id, source, target, body, kind, sentiment, now, aggressive=False):
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
            before = self._pair_before(db, scope, source, target, now, aggressive)
            cursor = db.execute(
                """INSERT OR IGNORE INTO affinity_interactions
                (platform,bot_id,group_id,message_id,source_id,target_id,body_hash,kind,sentiment,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (*scope, message_id, source, target, digest, kind, sentiment, now),
            )
            if cursor.rowcount:
                self._audit_pair(db, scope, before, kind, cursor.lastrowid, now, aggressive)
                return cursor.lastrowid
            return None

    def set_sentiment(self, interaction_id, sentiment, aggressive=False):
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM affinity_interactions WHERE id=?", (interaction_id,)).fetchone()
            if row is None:
                return
            scope = (row["platform"], row["bot_id"], row["group_id"])
            now = time.time()
            before = self._pair_before(db, scope, row["source_id"], row["target_id"], now, aggressive)
            db.execute("UPDATE affinity_interactions SET sentiment=? WHERE id=?",
                       (max(-1.0, min(1.0, sentiment)), interaction_id))
            self._audit_pair(db, scope, before, "sentiment", interaction_id, now, aggressive)

    def game(self, scope, event_id, kind, actor, target, now, aggressive=False):
        if kind not in {"accepted", "rejected", "forced"}:
            raise ValueError("Unknown affinity game event")
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            before = self._pair_before(db, scope, actor, target, now, aggressive)
            cursor = db.execute("INSERT OR IGNORE INTO affinity_game_events VALUES (?,?,?,?,?,?,?,?)",
                                (*scope, event_id, kind, actor, target, now))
            if cursor.rowcount:
                self._audit_pair(db, scope, before, kind, event_id, now, aggressive)

    def _automatic(self, db, scope, source, target, now, aggressive, evidence=None):
        rows = db.execute(
            f"""SELECT source_id,kind,sentiment,created_at FROM affinity_interactions
                WHERE {SCOPE_SQL} AND created_at BETWEEN ? AND ? AND
                ((source_id=? AND target_id=?) OR (source_id=? AND target_id=?))
                ORDER BY created_at""",
            (*scope, now - RETENTION, now, source, target, target, source),
        ).fetchall() if evidence is None else [row for row in evidence[0] if now - RETENTION <= row["created_at"] <= now]
        pokes = [row for row in rows if row["kind"] == "poke" and row["source_id"] == source]
        rows = [row for row in rows if row["kind"] != "poke"]
        poke_score = min(3, sum(POKE_WEIGHT * 2 ** (-max(0, now - row["created_at"]) / DAY / POKE_HALF_LIFE_DAYS) for row in pokes))
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
                friendliness += max(-2, min(2, sum(sentiments))) * 2 ** (-age / EMOTION_HALF_LIFE_DAYS)
        familiarity = min(30, familiarity)
        friendliness = max(-20, min(20, friendliness))
        games = db.execute(
            f"""SELECT kind,actor_id,target_id,created_at FROM affinity_game_events
                WHERE {SCOPE_SQL} AND created_at BETWEEN ? AND ? AND
                ((actor_id=? AND target_id=?) OR (actor_id=? AND target_id=?))
                ORDER BY created_at""",
            (*scope, now - RETENTION, now, source, target, target, source),
        ).fetchall() if evidence is None else [row for row in evidence[1] if now - RETENTION <= row["created_at"] <= now]
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
        return {"score": score, "automatic_score": score, "base": BASE_SCORE, "multiplier": multiplier,
                "manual_adjustment": 0.0, "familiarity": familiarity, "friendliness": friendliness,
                "game": game_score, "mutual_days": mutual_days, "observations": len(rows),
                "poke": poke_score, "poke_observations": len(pokes),
                "has_data": bool(rows or relevant_games or pokes), "manual": False}

    def _score(self, db, scope, source, target, now, aggressive, evidence=None):
        result = self._automatic(db, scope, source, target, now, aggressive, evidence)
        row = db.execute(
                f"SELECT offset,created_at FROM affinity_manual WHERE {SCOPE_SQL} AND source_id=? AND target_id=? AND created_at BETWEEN ? AND ?",
                (*scope, source, target, now - RETENTION, now),
            ).fetchone() if evidence is None else evidence[2]
        if row and now - RETENTION <= row["created_at"] <= now:
            result["manual_adjustment"] = row["offset"] * 2 ** (-max(0, now - row["created_at"]) / DAY / 45)
            result["score"] = max(0, min(100, result["score"] + result["manual_adjustment"]))
            result.update(has_data=True, manual=True)
        return result if result["has_data"] else None

    def score(self, scope, source, target, aggressive=False, now=None):
        now = time.time() if now is None else now
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            result = self._score(db, scope, source, target, now, aggressive)
            # Passive changes (decay, expiry, config) need no timer per pair.
            # Record them at most every ten minutes when actually observed.
            previous = db.execute(f"""SELECT after_json,created_at FROM affinity_score_audit
                WHERE {SCOPE_SQL} AND source_id=? AND target_id=? ORDER BY created_at DESC,id DESC LIMIT 1""",
                (*scope, source, target)).fetchone()
            before = json.loads(previous["after_json"]) if previous and previous["after_json"] else None
            mode_changed = bool(before and result and before.get("multiplier") != result["multiplier"])
            if previous and (now - previous["created_at"] >= 600 or mode_changed):
                if self._different(before, result):
                    reason = "mode" if mode_changed else "time"
                    self._audit(db, scope, source, target, before, result, reason, "", now)
            return result

    def adjust(self, scope, source, target, value, relative=False, aggressive=False, now=None):
        if not math.isfinite(value):
            raise ValueError("Adjustment must be finite")
        now = time.time() if now is None else now
        # Serialize adjustments with observations and other adjustments.
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            before = self._score(db, scope, source, target, now, aggressive)
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
            self._audit(db, scope, source, target, before,
                        self._score(db, scope, source, target, now, aggressive), "manual", "", now)
            return wanted
