from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from services.activity_service import ActivityService


class _DummyEvent:
    def __init__(self, *, group_id="100", sender_id="200", self_id="999"):
        self._group_id = group_id
        self._sender_id = sender_id
        self._self_id = self_id

    def get_group_id(self):
        return self._group_id

    def get_sender_id(self):
        return self._sender_id

    def get_self_id(self):
        return self._self_id


class ActivityServiceTest(unittest.TestCase):
    def test_record_active_updates_state_and_persists(self) -> None:
        persisted = []

        def persist(active_users):
            persisted.append(dict(active_users))

        service = ActivityService(
            is_allowed_group=lambda gid: gid == "100",
            persist_active_users=persist,
            now_provider=lambda: 1234.0,
        )

        state = {}
        service.record_active(_DummyEvent(), state)

        self.assertEqual(state["100"]["200"], 1234.0)
        self.assertEqual(len(persisted), 1)

    def test_record_active_ignores_bot_or_disallowed_group(self) -> None:
        persisted = []

        service = ActivityService(
            is_allowed_group=lambda gid: False,
            persist_active_users=lambda active_users: persisted.append(active_users),
        )

        state = {}
        service.record_active(_DummyEvent(), state)
        self.assertEqual(state, {})
        self.assertEqual(persisted, [])

    def test_cleanup_inactive_removes_expired_and_zero(self) -> None:
        persisted = []

        service = ActivityService(
            is_allowed_group=lambda gid: True,
            persist_active_users=lambda active_users: persisted.append(dict(active_users)),
            now_provider=lambda: 1000.0,
        )

        state = {
            "100": {
                "u_recent": 999.0,
                "u_old": 1000.0 - 31 * 24 * 3600,
                "0": 999.0,
            }
        }

        service.cleanup_inactive("100", state)

        self.assertEqual(state["100"], {"u_recent": 999.0})
        self.assertEqual(len(persisted), 1)


if __name__ == "__main__":
    unittest.main()
