from __future__ import annotations

import pathlib
import sys
import unittest
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from services.wife_command_service import (
    build_rbq_top_ranking,
    compute_force_marry_reset,
    select_draw_pool,
)


class WifeRulesTest(unittest.TestCase):
    def test_select_draw_pool_filters_members_and_excluded(self) -> None:
        pool, removed = select_draw_pool(
            active_user_ids=["1", "2", "3"],
            current_member_ids=["1", "3"],
            excluded={"3"},
        )

        self.assertEqual(pool, ["1"])
        self.assertEqual(removed, ["2"])

    def test_compute_force_marry_reset_uses_midnight_plus_cd_days(self) -> None:
        last_time = datetime(2026, 2, 6, 16, 0, 0).timestamp()
        now_ts = datetime(2026, 2, 8, 12, 0, 0).timestamp()

        target_reset_dt, remaining = compute_force_marry_reset(
            last_time=last_time,
            now_ts=now_ts,
            cd_days=3,
        )

        self.assertEqual(target_reset_dt, datetime(2026, 2, 9, 0, 0, 0))
        self.assertGreater(remaining, 0)

    def test_build_rbq_top_ranking_with_ties(self) -> None:
        ranking = build_rbq_top_ranking(
            {
                "100": [1, 2, 3],
                "101": [1, 2, 3],
                "102": [1],
            },
            {
                "100": "Alice",
                "101": "Bob",
                "102": "Carol",
            },
            top_n=10,
        )

        self.assertEqual(ranking[0]["rank"], 1)
        self.assertEqual(ranking[1]["rank"], 1)
        self.assertEqual(ranking[2]["rank"], 3)


if __name__ == "__main__":
    unittest.main()
