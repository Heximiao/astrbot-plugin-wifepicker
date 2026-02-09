from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

try:
    from astrbot.api import logger
except ImportError:  # pragma: no cover - test fallback
    logger = logging.getLogger(__name__)

try:
    from astrbot.core.star.filter.permission import PermissionTypeFilter
except ImportError:  # pragma: no cover - test fallback
    class PermissionTypeFilter:  # type: ignore[override]
        pass

try:
    from astrbot.core.star.star_handler import star_handlers_registry
except ImportError:  # pragma: no cover - test fallback
    class _StarHandlerRegistry:
        @staticmethod
        def get_handlers_by_module_name(_module_name: str):
            return []

    star_handlers_registry = _StarHandlerRegistry()

try:
    from ..keyword_trigger import KeywordRoute, KeywordRouter, MatchMode, PermissionLevel
except ImportError:  # pragma: no cover - used by direct local imports in tests
    from keyword_trigger import KeywordRoute, KeywordRouter, MatchMode, PermissionLevel


HandlerCallable = Callable[[Any], AsyncIterator[Any]]


class KeywordDispatchService:
    """Decouples keyword route matching and permission checks from plugin entrypoint."""

    def __init__(
        self,
        *,
        router: KeywordRouter,
        handlers: dict[str, HandlerCallable],
        action_to_command_handler: dict[str, str],
        module_name: str,
        config: Any,
        is_allowed_group: Callable[[str], bool],
        keyword_trigger_enabled: Callable[[], bool],
        keyword_trigger_mode: Callable[[], MatchMode],
        record_active: Callable[[Any], None],
        block_prefixes: tuple[str, ...] = ("/", "!", "！"),
    ):
        self._router = router
        self._handlers = handlers
        self._action_to_command_handler = action_to_command_handler
        self._module_name = module_name
        self._config = config
        self._is_allowed_group = is_allowed_group
        self._keyword_trigger_enabled = keyword_trigger_enabled
        self._keyword_trigger_mode = keyword_trigger_mode
        self._record_active = record_active
        self._block_prefixes = block_prefixes

    def _find_command_handler_metadata(self, action: str):
        handler_name = self._action_to_command_handler.get(action)
        if not handler_name:
            return None

        for handler in star_handlers_registry.get_handlers_by_module_name(self._module_name):
            if handler.handler_name == handler_name:
                return handler
        return None

    def _can_trigger_keyword_route(self, event: Any, route: KeywordRoute) -> bool:
        handler_md = self._find_command_handler_metadata(route.action)
        if handler_md is None:
            if route.permission == PermissionLevel.ADMIN and not event.is_admin():
                return False
            return True

        if not handler_md.enabled:
            return False

        for event_filter in handler_md.event_filters:
            if isinstance(event_filter, PermissionTypeFilter) and not event_filter.filter(
                event,
                self._config,
            ):
                return False

        return True

    def _should_ignore_keyword_trigger(self, message: str) -> bool:
        stripped = message.lstrip()
        return stripped.startswith(self._block_prefixes)

    async def dispatch(self, event: Any) -> AsyncIterator[Any]:
        if not self._keyword_trigger_enabled():
            return

        group_id = event.get_group_id()
        if not group_id or not self._is_allowed_group(str(group_id)):
            return

        message_str = event.message_str
        if not message_str:
            return

        if event.is_at_or_wake_command:
            return

        if self._should_ignore_keyword_trigger(message_str):
            return

        mode = self._keyword_trigger_mode()
        route = self._router.match_route(message_str, mode=mode)
        if route is None:
            route = self._router.match_command_route(message_str)
        if route is None:
            return

        if not self._can_trigger_keyword_route(event, route):
            return

        self._record_active(event)

        handler = self._handlers.get(route.action)
        if handler is None:
            logger.warning(f"关键词路由命中未知 action={route.action!r}，已忽略。")
            return

        async for result in handler(event):
            yield result

        event.stop_event()
