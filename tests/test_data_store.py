from __future__ import annotations

import pathlib
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from core.data_store import DataStore


class DataStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = DataStore(self.temp_dir.name, max_records_supplier=lambda: 2)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_trim_records_by_max_total(self) -> None:
        data = {
            "date": "2026-02-08",
            "groups": {
                "1": {
                    "records": [
                        {"timestamp": "2026-02-08T08:00:00", "value": "a"},
                        {"timestamp": "2026-02-08T09:00:00", "value": "b"},
                    ]
                },
                "2": {
                    "records": [
                        {"timestamp": "2026-02-08T10:00:00", "value": "c"},
                    ]
                },
            },
        }

        self.store.save_json(self.store.records_file, data)
        reloaded = self.store.load_json(self.store.records_file, {})

        self.assertEqual(len(reloaded["groups"]), 2)
        values = [
            item["value"]
            for group in reloaded["groups"].values()
            for item in group.get("records", [])
        ]
        self.assertEqual(sorted(values), ["b", "c"])

    def test_clean_rbq_stats_filters_expired_and_inactive(self) -> None:
        now = time.time()
        self.store.active_users = {
            "100": {
                "u_keep": now,
            }
        }
        self.store.rbq_stats = {
            "100": {
                "u_keep": [now - 100],
                "u_expired": [now - 31 * 24 * 3600],
                "u_inactive_low": [now - 50],
            }
        }

        self.store.clean_rbq_stats(now_ts=now)

        self.assertIn("u_keep", self.store.rbq_stats["100"])
        self.assertNotIn("u_expired", self.store.rbq_stats["100"])
        self.assertNotIn("u_inactive_low", self.store.rbq_stats["100"])

    def test_get_group_records_ensures_today(self) -> None:
        self.store.records = {"date": "2000-01-01", "groups": {}}
        records = self.store.get_group_records("7788")
        self.assertIsInstance(records, list)
        self.assertEqual(self.store.records["date"], time.strftime("%Y-%m-%d"))


if __name__ == "__main__":
    unittest.main()
