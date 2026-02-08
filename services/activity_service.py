from __future__ import annotations

import time
from typing import Callable, MutableMapping


class ActivityService:
    """Tracks group member activity and performs lightweight cleanup."""

    def __init__(
        self,
        *,
        is_allowed_group: Callable[[str], bool],
        persist_active_users: Callable[[MutableMapping[str, object]], None],
        now_provider: Callable[[], float] = time.time,
    ):
        self._is_allowed_group = is_allowed_group
        self._persist_active_users = persist_active_users
        self._now_provider = now_provider

    def record_active(
        self,
        event,
        active_users: MutableMapping[str, MutableMapping[str, float]],
    ) -> None:
        group_id = event.get_group_id()
        if not group_id or not self._is_allowed_group(str(group_id)):
            return

        user_id = str(event.get_sender_id())
        bot_id = str(event.get_self_id())
        if user_id == bot_id or user_id == "0":
            return

        group_key = str(group_id)
        if group_key not in active_users:
            active_users[group_key] = {}
        active_users[group_key][user_id] = self._now_provider()
        self._persist_active_users(active_users)

    def cleanup_inactive(
        self,
        group_id: str,
        active_users: MutableMapping[str, MutableMapping[str, float]],
    ) -> None:
        group_key = str(group_id)
        if group_key not in active_users:
            return

        now = self._now_provider()
        limit = 30 * 24 * 3600
        source = active_users[group_key]
        new_active = {
            uid: ts
            for uid, ts in source.items()
            if (now - ts < limit) and str(uid) != "0"
        }

        if len(source) != len(new_active):
            active_users[group_key] = new_active
            self._persist_active_users(active_users)
