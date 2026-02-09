from __future__ import annotations

import asyncio
import re
from typing import Any, Callable, Iterable, Mapping

try:
    from ..onebot_api import extract_message_id
except ImportError:  # pragma: no cover - used by direct local imports in tests
    from onebot_api import extract_message_id


_CQ_AT_RE = re.compile(r"\[CQ:at,qq=(\d+)\]")
_PLAIN_AT_RE = re.compile(r"@(\d{5,12})")


def unwrap_onebot_data(resp: Any) -> Any:
    if isinstance(resp, Mapping) and "data" in resp:
        return resp.get("data")
    return resp


class OneBotGateway:
    """Thin wrapper around OneBot actions used by this plugin."""

    def __init__(
        self,
        *,
        withdraw_tasks: set[asyncio.Task] | None = None,
        delete_error_handler: Callable[[Exception], None] | None = None,
    ):
        self._withdraw_tasks = withdraw_tasks if withdraw_tasks is not None else set()
        self._delete_error_handler = delete_error_handler

    async def send_message(self, event: Any, *, message: list[dict[str, Any]]) -> Any:
        group_id = event.get_group_id()
        if group_id:
            resp = await event.bot.api.call_action(
                "send_group_msg",
                group_id=int(group_id),
                message=message,
            )
        else:
            resp = await event.bot.api.call_action(
                "send_private_msg",
                user_id=int(event.get_sender_id()),
                message=message,
            )

        return extract_message_id(resp)

    async def fetch_group_member_list(
        self,
        event: Any,
        *,
        group_id: str | int | None = None,
    ) -> list[dict[str, Any]]:
        target_group_id = group_id if group_id is not None else event.get_group_id()
        if not target_group_id:
            return []

        resp = await event.bot.api.call_action(
            "get_group_member_list",
            group_id=int(target_group_id),
        )
        data = unwrap_onebot_data(resp)
        if not isinstance(data, list):
            return []

        return [item for item in data if isinstance(item, Mapping)]

    async def fetch_group_info(
        self,
        event: Any,
        *,
        group_id: str | int | None = None,
    ) -> dict[str, Any]:
        target_group_id = group_id if group_id is not None else event.get_group_id()
        if not target_group_id:
            return {}

        resp = await event.bot.api.call_action(
            "get_group_info",
            group_id=int(target_group_id),
        )
        data = unwrap_onebot_data(resp)
        if isinstance(data, Mapping):
            return dict(data)
        return {}

    def schedule_delete_msg(
        self,
        client: Any,
        *,
        message_id: Any,
        delay_seconds: int,
    ) -> None:
        async def runner() -> None:
            await asyncio.sleep(max(1, int(delay_seconds)))
            try:
                await client.api.call_action("delete_msg", message_id=message_id)
            except Exception as exc:  # pragma: no cover - async side effect
                if self._delete_error_handler is not None:
                    self._delete_error_handler(exc)

        task = asyncio.create_task(runner())
        self._withdraw_tasks.add(task)
        task.add_done_callback(self._withdraw_tasks.discard)

    def cancel_pending_withdraw_tasks(self) -> None:
        for task in tuple(self._withdraw_tasks):
            task.cancel()
        self._withdraw_tasks.clear()

    @staticmethod
    def resolve_member_name(
        members: Iterable[Mapping[str, Any]],
        *,
        user_id: str,
        fallback: str,
    ) -> str:
        for member in members:
            if str(member.get("user_id")) == str(user_id):
                return str(member.get("card") or member.get("nickname") or fallback)
        return fallback

    @staticmethod
    def extract_target_id_from_message(event: Any) -> str | None:
        message_obj = getattr(event, "message_obj", None)
        message_chain = getattr(message_obj, "message", []) if message_obj else []

        for component in message_chain:
            qq = getattr(component, "qq", None)
            if qq is not None:
                return str(qq)

        raw_text = str(getattr(event, "message_str", "") or "")
        cq_at = _CQ_AT_RE.search(raw_text)
        if cq_at:
            return cq_at.group(1)

        plain_at = _PLAIN_AT_RE.search(raw_text)
        if plain_at:
            return plain_at.group(1)

        return None
