from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from core.config_accessor import ConfigAccessor


class ConfigAccessorTest(unittest.TestCase):
    def test_numeric_defaults_and_bounds(self) -> None:
        accessor = ConfigAccessor(
            {
                "daily_limit": "0",
                "force_marry_cd": -3,
                "max_records": "bad",
                "iterations": 0,
                "auto_withdraw_delay_seconds": "-2",
            }
        )

        self.assertEqual(accessor.daily_limit(), 1)
        self.assertEqual(accessor.force_marry_cd_days(), 1)
        self.assertEqual(accessor.max_records(), 500)
        self.assertEqual(accessor.iterations(), 1)
        self.assertEqual(accessor.auto_withdraw_delay_seconds(), 1)

    def test_group_allow_rules(self) -> None:
        accessor = ConfigAccessor(
            {
                "whitelist_groups": ["100", 200],
                "blacklist_groups": ["300", 200],
            }
        )

        self.assertTrue(accessor.is_allowed_group("100"))
        self.assertFalse(accessor.is_allowed_group("200"))
        self.assertFalse(accessor.is_allowed_group("300"))
        self.assertFalse(accessor.is_allowed_group("999"))

    def test_normalize_user_id_set(self) -> None:
        accessor = ConfigAccessor({"excluded_users": [123, "", "456"]})
        self.assertEqual(accessor.draw_excluded_users(), {"123", "456"})


if __name__ == "__main__":
    unittest.main()
