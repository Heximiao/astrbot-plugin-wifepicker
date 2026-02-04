from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Optional, Sequence


class MatchMode(str, Enum):
    """Keyword matching strategy."""

    EXACT = "exact"
    STARTS_WITH = "starts_with"
    CONTAINS = "contains"


@dataclass(frozen=True, slots=True)
class KeywordRoute:
    """Maps a keyword to an action identifier."""

    keyword: str
    action: str


class KeywordRouter:
    """Routes message strings to actions based on keyword rules.

    This module is intentionally framework-agnostic so it can be unit-tested
    without AstrBot runtime dependencies.
    """

    def __init__(self, routes: Sequence[KeywordRoute]):
        self._routes = list(routes)
        self._routes_by_keyword_len_desc = sorted(
            self._routes, key=lambda r: len(r.keyword), reverse=True
        )

    def match(self, message: str, *, mode: MatchMode) -> Optional[str]:
        text = message.strip()
        if not text:
            return None

        routes: Iterable[KeywordRoute] = self._routes
        if mode in (MatchMode.CONTAINS, MatchMode.STARTS_WITH):
            routes = self._routes_by_keyword_len_desc

        for route in routes:
            if self._matches(text, route.keyword, mode):
                return route.action
        return None

    @staticmethod
    def _matches(text: str, keyword: str, mode: MatchMode) -> bool:
        if mode == MatchMode.EXACT:
            return text == keyword
        if mode == MatchMode.STARTS_WITH:
            return text.startswith(keyword)
        if mode == MatchMode.CONTAINS:
            return keyword in text
        raise ValueError(f"Unknown MatchMode: {mode}")

