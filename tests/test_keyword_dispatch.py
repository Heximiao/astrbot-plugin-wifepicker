from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from keyword_trigger import KeywordRoute, KeywordRouter, MatchMode, PermissionLevel
from services import keyword_dispatch_service as keyword_dispatch_module
from services.keyword_dispatch_service import KeywordDispatchService


class _DummyEvent:
    def __init__(
        self,
        *,
        group_id="100",
        message_str="抽老婆",
        is_admin=False,
        is_at_or_wake_command=False,
    ):
        self._group_id = group_id
        self.message_str = message_str
        self._is_admin = is_admin
        self.is_at_or_wake_command = is_at_or_wake_command
        self.stopped = False

    def get_group_id(self):
        return self._group_id

    def is_admin(self):
        return self._is_admin

    def stop_event(self):
        self.stopped = True


class _FakeHandlerMd:
    def __init__(self, *, handler_name: str, enabled: bool, event_filters: list):
        self.handler_name = handler_name
        self.enabled = enabled
        self.event_filters = event_filters


class KeywordDispatchServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_dispatch_success_and_stop_event(self) -> None:
        recorded = []

        async def handler(_event):
            yield "ok"

        service = KeywordDispatchService(
            router=KeywordRouter([KeywordRoute(keyword="抽老婆", action="draw_wife")]),
            handlers={"draw_wife": handler},
            action_to_command_handler={"draw_wife": "draw_wife"},
            module_name="m",
            config={},
            is_allowed_group=lambda gid: gid == "100",
            keyword_trigger_enabled=lambda: True,
            keyword_trigger_mode=lambda: MatchMode.EXACT,
            record_active=lambda event: recorded.append(event),
        )

        original = keyword_dispatch_module.star_handlers_registry.get_handlers_by_module_name
        keyword_dispatch_module.star_handlers_registry.get_handlers_by_module_name = (
            lambda module_name: [
                _FakeHandlerMd(handler_name="draw_wife", enabled=True, event_filters=[])
            ]
        )
        try:
            event = _DummyEvent(message_str="抽老婆")
            results = [item async for item in service.dispatch(event)]
        finally:
            keyword_dispatch_module.star_handlers_registry.get_handlers_by_module_name = original

        self.assertEqual(results, ["ok"])
        self.assertEqual(len(recorded), 1)
        self.assertTrue(event.stopped)

    async def test_dispatch_ignores_prefixed_message(self) -> None:
        async def handler(_event):
            yield "should_not_happen"

        service = KeywordDispatchService(
            router=KeywordRouter([KeywordRoute(keyword="抽老婆", action="draw_wife")]),
            handlers={"draw_wife": handler},
            action_to_command_handler={"draw_wife": "draw_wife"},
            module_name="m",
            config={},
            is_allowed_group=lambda gid: True,
            keyword_trigger_enabled=lambda: True,
            keyword_trigger_mode=lambda: MatchMode.EXACT,
            record_active=lambda event: None,
        )

        event = _DummyEvent(message_str="/抽老婆")
        results = [item async for item in service.dispatch(event)]

        self.assertEqual(results, [])
        self.assertFalse(event.stopped)

    async def test_admin_route_fallback_check(self) -> None:
        async def handler(_event):
            yield "blocked"

        service = KeywordDispatchService(
            router=KeywordRouter(
                [
                    KeywordRoute(
                        keyword="重置记录",
                        action="reset_records",
                        permission=PermissionLevel.ADMIN,
                    )
                ]
            ),
            handlers={"reset_records": handler},
            action_to_command_handler={"reset_records": "reset_records"},
            module_name="m",
            config={},
            is_allowed_group=lambda gid: True,
            keyword_trigger_enabled=lambda: True,
            keyword_trigger_mode=lambda: MatchMode.EXACT,
            record_active=lambda event: None,
        )

        original = keyword_dispatch_module.star_handlers_registry.get_handlers_by_module_name
        keyword_dispatch_module.star_handlers_registry.get_handlers_by_module_name = (
            lambda module_name: []
        )
        try:
            event = _DummyEvent(message_str="重置记录", is_admin=False)
            results = [item async for item in service.dispatch(event)]
        finally:
            keyword_dispatch_module.star_handlers_registry.get_handlers_by_module_name = original

        self.assertEqual(results, [])
        self.assertFalse(event.stopped)


if __name__ == "__main__":
    unittest.main()
