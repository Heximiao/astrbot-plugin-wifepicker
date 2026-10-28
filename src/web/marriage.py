"""Read the same current-day records as the chat relationship diagram."""

from collections import Counter
from datetime import datetime


def today_groups(plugin):
    records = getattr(plugin, "records", {})
    if records.get("date") != datetime.now().strftime("%Y-%m-%d"):
        return {}
    groups = records.get("groups", {})
    return groups if isinstance(groups, dict) else {}


def marriage_report(plugin, group_id, names=None, user=""):
    names = dict(names or {})
    group = today_groups(plugin).get(group_id, {})
    records = [dict(row) for row in group.get("records", []) if isinstance(row, dict)] if isinstance(group, dict) else []
    # The existing per-platform profile cache supplies current names without
    # downloading avatars. Only use an unambiguous platform for legacy groups.
    candidates = [groups[group_id] for groups in getattr(plugin, "official_profiles", {}).values()
                  if isinstance(groups, dict) and isinstance(groups.get(group_id), dict)]
    if len(candidates) == 1:
        for uid, profile in candidates[0].items():
            if isinstance(profile, dict):
                name = profile.get("card") or profile.get("nickname")
                if name:
                    names.setdefault(str(uid), str(name))
    for row in records:
        if row.get("wife_name"):
            names.setdefault(str(row.get("wife_id", "")), str(row["wife_name"]))
    edges, members = [], set()
    forced_times = getattr(plugin, "forced_records", {}).get(group_id, {})
    pairs = {(str(row.get("user_id", "")), str(row.get("wife_id", "")), row.get("timestamp")) for row in records}
    for row in records:
        source, target = str(row.get("user_id", "")), str(row.get("wife_id", ""))
        if not source or not target or source in {"0", "None"} or target in {"0", "None"}:
            continue
        members.update((source, target))
        kind = "picked" if row.get("picked") else "drawn"
        if row.get("forced") and not row.get("picked"):
            kind = "linked"
            try:
                stamp = datetime.fromisoformat(row["timestamp"]).timestamp()
                forced = forced_times.get(source)
                if isinstance(forced, (int, float)) and abs(stamp - forced) <= 10:
                    kind = "forced"
                elif (target, source, row.get("timestamp")) in pairs:
                    kind = "proposed"
            except (ValueError, TypeError, KeyError, OverflowError, OSError):
                pass
        edges.append({"source": source, "target": target, "source_name": names.get(source, source),
                      "target_name": names.get(target, target), "kind": kind,
                      "label": {"picked": "挑中", "drawn": "抽中", "forced": "强娶", "proposed": "求婚成功", "linked": "结缘"}[kind],
                      "timestamp": str(row.get("timestamp", ""))})
    totals = dict(Counter(row["kind"] for row in edges))
    filtered = [row for row in edges if not user or user in (row["source"], row["target"])]
    return {"date": datetime.now().strftime("%Y-%m-%d"), "group": group_id, "edges": filtered,
            "members": [{"id": uid, "name": names.get(uid, uid)} for uid in sorted(members)],
            "totals": {"relations": len(edges), "members": len(members), **totals},
            "note": "使用与群内关系图相同的今日关系记录；求婚成功包含双方两个方向，分手、重置和换日后随原记录更新。"}
