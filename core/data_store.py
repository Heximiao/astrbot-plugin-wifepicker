from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


class DataStore:
    """Persistence and pruning for plugin JSON data files."""

    def __init__(
        self,
        data_dir: str | Path,
        *,
        max_records_supplier: Callable[[], int],
    ):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.records_file = self.data_dir / "wife_records.json"
        self.active_file = self.data_dir / "active_users.json"
        self.forced_file = self.data_dir / "forced_marriage.json"
        self.rbq_stats_file = self.data_dir / "rbq_stats.json"

        self._max_records_supplier = max_records_supplier

        self.records = self.load_json(self.records_file, {"date": "", "groups": {}})
        self.active_users = self.load_json(self.active_file, {})
        self.forced_records = self.load_json(self.forced_file, {})
        self.rbq_stats = self.load_json(self.rbq_stats_file, {})

    @staticmethod
    def load_json(path: str | Path, default: Any) -> Any:
        file_path = Path(path)
        if not file_path.exists():
            return default
        try:
            with file_path.open("r", encoding="utf-8") as file:
                return json.load(file)
        except Exception:
            return default

    def save_json(self, path: str | Path, data: Any) -> None:
        file_path = Path(path)
        self._apply_trim_policy_if_needed(file_path, data)
        with file_path.open("w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)

    def _apply_trim_policy_if_needed(self, path: Path, data: Any) -> None:
        if path != self.records_file:
            return
        if not isinstance(data, dict) or "groups" not in data:
            return

        groups = data.get("groups")
        if not isinstance(groups, dict):
            return

        flattened_records: list[tuple[str, dict[str, Any]]] = []
        for group_id, group_data in groups.items():
            if not isinstance(group_data, dict):
                continue
            records = group_data.get("records", [])
            if not isinstance(records, list):
                continue
            for record in records:
                if isinstance(record, dict):
                    flattened_records.append((str(group_id), record))

        max_total = self._max_records_supplier()
        if len(flattened_records) <= max_total:
            return

        flattened_records.sort(key=lambda item: item[1].get("timestamp", ""))
        keep_records = flattened_records[-max_total:]

        new_groups: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for group_id, record in keep_records:
            if group_id not in new_groups:
                new_groups[group_id] = {"records": []}
            new_groups[group_id]["records"].append(record)

        data["groups"] = new_groups

    def ensure_today_records(self, today: str | None = None) -> None:
        if today is None:
            today = datetime.now().strftime("%Y-%m-%d")
        if self.records.get("date") != today:
            self.records = {"date": today, "groups": {}}

    def get_group_records(self, group_id: str) -> list[dict[str, Any]]:
        self.ensure_today_records()
        groups = self.records.setdefault("groups", {})
        group_data = groups.setdefault(str(group_id), {"records": []})
        records = group_data.setdefault("records", [])
        return records

    def cleanup_inactive_group(self, group_id: str, *, now_ts: float | None = None) -> None:
        group_key = str(group_id)
        if group_key not in self.active_users:
            return

        now = now_ts if now_ts is not None else time.time()
        limit = 30 * 24 * 3600
        active_group = self.active_users[group_key]
        new_active = {
            uid: ts
            for uid, ts in active_group.items()
            if (now - ts < limit) and str(uid) != "0"
        }
        if len(active_group) != len(new_active):
            self.active_users[group_key] = new_active
            self.save_json(self.active_file, self.active_users)

    def clean_rbq_stats(self, *, now_ts: float | None = None) -> None:
        now = now_ts if now_ts is not None else time.time()
        thirty_days = 30 * 24 * 3600
        seven_days = 7 * 24 * 3600

        new_stats: dict[str, dict[str, list[float]]] = {}
        for group_id, users in self.rbq_stats.items():
            if not isinstance(users, dict):
                continue

            new_users: dict[str, list[float]] = {}
            active_group = self.active_users.get(str(group_id), {})

            for user_id, timestamps in users.items():
                if not isinstance(timestamps, list):
                    continue

                valid_timestamps = [
                    ts
                    for ts in timestamps
                    if isinstance(ts, (int, float)) and now - float(ts) < thirty_days
                ]
                count = len(valid_timestamps)

                is_in_active = str(user_id) in active_group
                last_active_ts = active_group.get(str(user_id), 0)

                should_keep = True
                if count == 0:
                    should_keep = False
                elif not is_in_active and count <= 4 and (now - last_active_ts > seven_days):
                    should_keep = False

                if should_keep:
                    new_users[str(user_id)] = valid_timestamps

            if new_users:
                new_stats[str(group_id)] = new_users

        self.rbq_stats = new_stats
        self.save_json(self.rbq_stats_file, self.rbq_stats)

    def reset_today_records(self, *, today: str | None = None) -> None:
        if today is None:
            today = datetime.now().strftime("%Y-%m-%d")
        self.records = {"date": today, "groups": {}}
        self.save_json(self.records_file, self.records)

    def reset_force_cd_for_group(self, group_id: str) -> bool:
        group_key = str(group_id)
        if group_key not in self.forced_records:
            return False

        self.forced_records[group_key] = {}
        self.save_json(self.forced_file, self.forced_records)
        return True

    def sync_refs(
        self,
        *,
        records: dict[str, Any] | None = None,
        active_users: dict[str, Any] | None = None,
        forced_records: dict[str, Any] | None = None,
        rbq_stats: dict[str, Any] | None = None,
    ) -> None:
        if records is not None:
            self.records = records
        if active_users is not None:
            self.active_users = active_users
        if forced_records is not None:
            self.forced_records = forced_records
        if rbq_stats is not None:
            self.rbq_stats = rbq_stats

    def save_all(self) -> None:
        self.save_json(self.records_file, self.records)
        self.save_json(self.active_file, self.active_users)
        self.save_json(self.forced_file, self.forced_records)
        self.save_json(self.rbq_stats_file, self.rbq_stats)
