from __future__ import annotations

from typing import Any, Mapping


class ConfigAccessor:
    """Typed, defensive access to plugin configuration."""

    def __init__(self, config: Mapping[str, Any] | None):
        self._config: Mapping[str, Any] = config or {}

    def _get(self, key: str, default: Any) -> Any:
        return self._config.get(key, default)

    def _as_int(self, key: str, default: int, *, minimum: int | None = None) -> int:
        raw = self._get(key, default)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = default

        if minimum is not None:
            value = max(minimum, value)
        return value

    @staticmethod
    def normalize_user_id_set(values: object) -> set[str]:
        if not isinstance(values, (list, tuple, set)):
            return set()
        return {str(v) for v in values if str(v).strip()}

    def daily_limit(self) -> int:
        return self._as_int("daily_limit", 1, minimum=1)

    def force_marry_cd_days(self) -> int:
        return self._as_int("force_marry_cd", 3, minimum=1)

    def max_records(self) -> int:
        return self._as_int("max_records", 500, minimum=1)

    def iterations(self) -> int:
        return self._as_int("iterations", 150, minimum=1)

    def auto_set_other_half_enabled(self) -> bool:
        return bool(self._get("auto_set_other_half", False))

    def auto_withdraw_enabled(self) -> bool:
        return bool(self._get("auto_withdraw_enabled", False))

    def auto_withdraw_delay_seconds(self) -> int:
        return self._as_int("auto_withdraw_delay_seconds", 5, minimum=1)

    def keyword_trigger_enabled(self) -> bool:
        return bool(self._get("keyword_trigger_enabled", False))

    def keyword_trigger_mode(self) -> str:
        value = str(self._get("keyword_trigger_mode", "exact")).strip().lower()
        if value not in {"exact", "starts_with", "contains"}:
            return "exact"
        return value

    def draw_excluded_users(self) -> set[str]:
        return self.normalize_user_id_set(self._get("excluded_users", []))

    def force_marry_excluded_users(self) -> set[str]:
        return self.normalize_user_id_set(
            self._get("force_marry_excluded_users", []),
        )

    def whitelist_groups(self) -> set[str]:
        return self.normalize_user_id_set(self._get("whitelist_groups", []))

    def blacklist_groups(self) -> set[str]:
        return self.normalize_user_id_set(self._get("blacklist_groups", []))

    def is_allowed_group(self, group_id: str) -> bool:
        normalized_group_id = str(group_id)
        whitelist = self.whitelist_groups()
        blacklist = self.blacklist_groups()

        if normalized_group_id in blacklist:
            return False
        if whitelist and normalized_group_id not in whitelist:
            return False
        return True
