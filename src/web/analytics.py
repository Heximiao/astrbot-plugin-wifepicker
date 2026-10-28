"""只读可视化数据。当前分数、历史回算和聊天命令共用同一计分器。"""

import json
import time
from collections import Counter, defaultdict
from contextlib import closing

from ..db.affinity import DAY, RETENTION, SCOPE_SQL

COMPONENTS = {"familiarity": "熟悉积累", "friendliness": "情绪倾向", "game": "婚姻互动", "poke": "戳一戳"}
REASONS = {"mention": "提及互动", "reply": "引用回复", "poke": "戳一戳", "accepted": "求婚同意",
           "rejected": "求婚被拒", "forced": "强娶", "manual": "手动调整", "sentiment": "情绪证据更新",
           "time": "时间流逝与证据到期", "mode": "计分模式变化"}


def components(row):
    if not row:
        return []
    result = [{"key": "base", "label": "基础值", "value": row.get("base", 30)}]
    multiplier = row.get("multiplier", 1)
    result += [{"key": key, "label": label, "value": row.get(key, 0) * multiplier} for key, label in COMPONENTS.items()]
    result.append({"key": "manual_adjustment", "label": "手动修正", "value": row.get("manual_adjustment", 0)})
    result.append({"key": "clamp", "label": "0～100分上限修正", "value": row["score"] - sum(item["value"] for item in result)})
    return result


def change(before, after, reason):
    old = {row["key"]: row["value"] for row in components(before)}
    new = {row["key"]: row["value"] for row in components(after)}
    parts = [{"key": key, "label": label, "delta": new.get(key, 0) - old.get(key, 0)}
             for key, label in {**COMPONENTS, "manual_adjustment": "手动修正", "clamp": "计分上限"}.items()]
    parts = [part for part in parts if abs(part["delta"]) > 0.000001]
    delta = (after["score"] - before["score"]) if before and after else 0
    if not before and after:
        explanation = "首次形成该方向的记录，以30分为基础；以下为此时的贡献。"
    elif not after and before:
        explanation = "可用证据已到期，该方向恢复为无数据，而不是0分。"
    elif not after:
        explanation = "这条证据尚未形成该方向的可计算分数。"
    elif reason in {"mention", "reply"} and any(part["key"] == "familiarity" and part["delta"] < 0 for part in parts):
        explanation = "双方当天互动次数比例发生变化，熟悉度重新估算；不是收到新的负面评价。"
    elif reason == "time":
        explanation = "这段时间的自然衰减、60天证据退出或未单独记录的规则重算，不是新的拒绝或攻击事件。"
    elif reason == "mode":
        explanation = "普通/激进模式改变，自动贡献按当前倍率重新计算。"
    elif not parts or abs(delta) < 0.000001:
        explanation = "证据已记录，但可能尚未达到双向/情绪门槛，或贡献已达到上限，当前分数不变。"
    else:
        explanation = "、".join(f"{part['label']} {part['delta']:+.4f}" for part in parts)
    return delta, parts, explanation


def catalog(store, now=None):
    now = time.time() if now is None else now
    with closing(store.connect()) as db:
        unions = " UNION ".join(f"SELECT platform,bot_id,group_id FROM {table} WHERE created_at BETWEEN ? AND ?"
                                for table in ("affinity_messages", "affinity_interactions", "affinity_game_events", "affinity_manual"))
        rows = db.execute(unions, (now - RETENTION, now) * 4).fetchall()
        names = {tuple(row[:3]): row["name"] for row in db.execute(
            "SELECT * FROM affinity_group_names WHERE created_at>=?", (now - RETENTION,))}
        return [{"scope": list(row), "name": names.get(tuple(row), f"群 {row['group_id']}")} for row in rows]


def _profiles(db, scope, now):
    return {row["user_id"]: row["name"] for row in db.execute(
        f"SELECT user_id,name FROM affinity_profiles WHERE {SCOPE_SQL} AND created_at>=?", (*scope, now - RETENTION))}


def group_report(store, scope, aggressive=False, user="", now=None):
    now = time.time() if now is None else now
    lower = now - RETENTION
    with closing(store.connect()) as db:
        db.execute("BEGIN")
        names = _profiles(db, scope, now)
        group_name = db.execute(f"SELECT name FROM affinity_group_names WHERE {SCOPE_SQL} AND created_at>=?",
                                (*scope, lower)).fetchone()
        pair_counts, members = defaultdict(Counter), set()
        kinds = Counter()
        for row in db.execute(f"""SELECT source_id,target_id,kind,COUNT(*) AS n FROM affinity_interactions
            WHERE {SCOPE_SQL} AND created_at BETWEEN ? AND ? GROUP BY source_id,target_id,kind""", (*scope, lower, now)):
            a, b = row["source_id"], row["target_id"]
            pair_counts[(a, b)][row["kind"]] += row["n"]
            kinds[row["kind"]] += row["n"]
            members.update((a, b))
        for row in db.execute(f"""SELECT actor_id,target_id,kind,COUNT(*) AS n FROM affinity_game_events
            WHERE {SCOPE_SQL} AND created_at BETWEEN ? AND ? GROUP BY actor_id,target_id,kind""", (*scope, lower, now)):
            a, b = row["actor_id"], row["target_id"]
            kinds[row["kind"]] += row["n"]
            pair_counts[(a, b)][row["kind"]] += row["n"]
            pair_counts[(b, a)]["game_related"] += row["n"]
            members.update((a, b))
        for row in db.execute(f"SELECT source_id,target_id FROM affinity_manual WHERE {SCOPE_SQL} AND created_at BETWEEN ? AND ?", (*scope, lower, now)):
            pair_counts[(row["source_id"], row["target_id"])]["manual"] += 1
            members.update((row["source_id"], row["target_id"]))
        for row in db.execute(f"SELECT DISTINCT sender_id FROM affinity_messages WHERE {SCOPE_SQL} AND created_at BETWEEN ? AND ?", (*scope, lower, now)):
            members.add(row[0])
        # An incoming chat observation may establish a baseline in the reverse
        # direction too; calculate both directions using the canonical scorer.
        for a, b in list(pair_counts):
            pair_counts[(b, a)]
        ordered = sorted(pair_counts, key=lambda pair: sum(pair_counts[pair].values()), reverse=True)
        filtered = [pair for pair in ordered if not user or user in pair]
        relations = []
        for a, b in filtered[:120]:
            score = store._score(db, scope, a, b, now, aggressive)
            if score:
                relations.append({"source": a, "target": b, "source_name": names.get(a, a), "target_name": names.get(b, b),
                                  "score": score["score"], "days": score["mutual_days"], "counts": dict(pair_counts[(a, b)])})
        activity = defaultdict(Counter)
        for table, label in (("affinity_messages", "messages"), ("affinity_interactions", "interactions"), ("affinity_game_events", "games")):
            for row in db.execute(f"""SELECT CAST(created_at / ? AS INTEGER) AS day,COUNT(*) AS n FROM {table}
                WHERE {SCOPE_SQL} AND created_at BETWEEN ? AND ? GROUP BY day""", (DAY, *scope, lower, now)):
                activity[row["day"]][label] = row["n"]
        nodes = sorted(members, key=lambda uid: (names.get(uid, uid), uid))
        message_count = sum(day["messages"] for day in activity.values())
        return {"scope": list(scope), "name": group_name[0] if group_name else f"群 {scope[2]}",
                "at": now, "members": [{"id": uid, "name": names.get(uid, uid)} for uid in nodes],
                "totals": {"members": len(members), "messages": message_count,
                           "chat": kinds["mention"] + kinds["reply"], "poke": kinds["poke"],
                           "accepted": kinds["accepted"], "rejected": kinds["rejected"], "forced": kinds["forced"]},
                "activity": [{"at": day * DAY, **values} for day, values in sorted(activity.items())],
                "relations": relations, "pair_candidates": len(filtered), "truncated": len(filtered) > 120}


def pair_report(store, scope, source, target, aggressive=False, days=30, page=1, now=None):
    now = time.time() if now is None else now
    days = max(1, min(60, int(days)))
    start = now - days * DAY
    page = max(1, min(10000, int(page)))
    store.score(scope, source, target, aggressive, now)
    with closing(store.connect()) as db:
        db.execute("BEGIN")
        current = store._score(db, scope, source, target, now, aggressive)
        names = _profiles(db, scope, now)
        args = (*scope, now - RETENTION, now, source, target, target, source)
        clause = f"{SCOPE_SQL} AND created_at BETWEEN ? AND ? AND ((source_id=? AND target_id=?) OR (source_id=? AND target_id=?))"
        interactions = db.execute(f"SELECT * FROM affinity_interactions WHERE {clause} ORDER BY created_at,id", args).fetchall()
        games = db.execute(f"SELECT * FROM affinity_game_events WHERE {clause.replace('source_id', 'actor_id')} ORDER BY created_at,event_id", args).fetchall()
        manual = db.execute(f"SELECT * FROM affinity_manual WHERE {SCOPE_SQL} AND source_id=? AND target_id=? AND created_at BETWEEN ? AND ?",
                            (*scope, source, target, now - RETENTION, now)).fetchone()
        audits = db.execute(f"""SELECT * FROM affinity_score_audit WHERE {SCOPE_SQL} AND source_id=? AND target_id=?
            AND created_at BETWEEN ? AND ? ORDER BY created_at,id""", (*scope, source, target, start, now)).fetchall()
        manual_history = db.execute(f"""SELECT created_at,after_json FROM affinity_score_audit
            WHERE {SCOPE_SQL} AND source_id=? AND target_id=? AND reason='manual'
            AND created_at BETWEEN ? AND ? ORDER BY created_at,id""", (*scope, source, target, now - RETENTION, now)).fetchall()
        manual_versions = [{"created_at": row["created_at"], "offset": json.loads(row["after_json"])["manual_adjustment"]}
                           for row in manual_history if row["after_json"]]

        def historical_score(at):
            # The live table keeps only the latest manual setting. The audit
            # preserves older settings so later changes do not erase the curve.
            versions = [row for row in manual_versions if row["created_at"] <= at]
            correction = versions[-1] if versions else manual
            return store._score(db, scope, source, target, at, aggressive, (interactions, games, correction))
        represented = {(row["reason"], row["evidence_id"]) for row in audits}
        entries = []
        for row in audits:
            entries.append({"at": row["created_at"], "kind": row["reason"], "id": row["id"],
                            "before": json.loads(row["before_json"]) if row["before_json"] else None,
                            "after": json.loads(row["after_json"]) if row["after_json"] else None,
                            "basis": "recorded", "evidence_id": row["evidence_id"]})
        for row in interactions:
            if row["kind"] == "poke" and row["source_id"] != source:
                continue
            if row["created_at"] >= start and (row["kind"], str(row["id"])) not in represented:
                entries.append({"at": row["created_at"], "kind": row["kind"], "basis": "reconstructed",
                                "id": 0, "evidence_id": str(row["id"])})
        for row in games:
            relevant = (row["kind"] == "accepted" or (row["kind"] == "forced" and row["actor_id"] == source)
                        or (row["kind"] == "rejected" and row["target_id"] == source))
            if not relevant:
                continue
            if row["created_at"] >= start and (row["kind"], row["event_id"]) not in represented:
                entries.append({"at": row["created_at"], "kind": row["kind"], "basis": "reconstructed",
                                "id": 0, "evidence_id": row["event_id"]})
        if manual and manual["created_at"] >= start and not any(row["kind"] == "manual" for row in entries):
            entries.append({"at": manual["created_at"], "kind": "manual", "basis": "reconstructed", "id": 0, "evidence_id": ""})
        entries.sort(key=lambda row: (row["at"], row["id"]), reverse=True)
        selected = entries[(page - 1) * 40:page * 40]
        for row in selected:
            if row["basis"] == "reconstructed":
                row["before"] = historical_score(row["at"] - 0.00001)
                row["after"] = historical_score(row["at"])
            delta, parts, explanation = change(row["before"], row["after"], row["kind"])
            row.update(delta=delta, parts=parts, explanation=explanation, label=REASONS.get(row["kind"], row["kind"]))
        # Daily samples are recalculations with today's rules. Actual observed
        # audit points are kept distinct, including old modes/manual settings.
        curve = {}
        times = [start + i * DAY for i in range(days)] + [now]
        times += [row["at"] for row in entries[:160]]
        for at in sorted(set(times)):
            score = historical_score(at)
            curve[at] = {"at": at, "score": score["score"] if score else None, "basis": "reconstructed"}
        for row in audits[-160:]:
            after = json.loads(row["after_json"]) if row["after_json"] else None
            curve[row["created_at"]] = {"at": row["created_at"], "score": after["score"] if after else None, "basis": "recorded"}
        reverse = store._score(db, scope, target, source, now, aggressive)
        counts = Counter(row["kind"] for row in interactions if row["source_id"] == source)
        counts.update(row["kind"] for row in games if row["actor_id"] == source or row["kind"] == "accepted")
        return {"scope": list(scope), "source": source, "target": target, "source_name": names.get(source, source),
                "target_name": names.get(target, target), "at": now, "current": current, "reverse": reverse,
                "components": components(current), "curve": [curve[key] for key in sorted(curve)],
                "ledger": selected, "total": len(entries), "page": page, "page_size": 40, "counts": dict(counts),
                "has_reconstructed": any(row["basis"] == "reconstructed" for row in entries),
                "history_note": "实测点保存当时计算结果；其他曲线点和旧事件按当前规则回算。旧版未保留的手动调整及当时模型状态无法完整还原。"}
