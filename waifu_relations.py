from __future__ import annotations

from typing import Any, MutableSequence


def maybe_add_other_half_record(
    *,
    records: MutableSequence[dict[str, Any]],
    user_id: str,
    user_name: str,
    wife_id: str,
    wife_name: str,
    enabled: bool,
    timestamp: str,
) -> bool:
    """Auto set selected waifu's waifu to the original user.

    Rules (minimal port from nonebot-plugin-today-waifu):
    - feature flag controlled by `enabled`
    - only set if the selected waifu has no record today (by `user_id`)
    """

    if not enabled:
        return False

    # 对方已经有老婆（或已抽过）则不覆盖。
    if any(str(r.get("user_id")) == str(wife_id) for r in records):
        return False

    records.append(
        {
            "user_id": str(wife_id),
            "wife_id": str(user_id),
            "wife_name": str(user_name),
            "timestamp": timestamp,
            "auto_set": True,
            "auto_set_target_name": str(wife_name),
        }
    )
    return True

