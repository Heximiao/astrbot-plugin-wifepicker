import asyncio
import time
from datetime import datetime

import astrbot.api.message_components as Comp
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)

from ..core import (
    get_force_marry_cooldown_status,
    get_group_records,
    get_propose_cooldown_status,
    set_propose_cooldown,
    upsert_user_wife_record,
)
from .forced_marriage import cmd_force_marry
from ..utils import extract_target_id_from_message, resolve_member_name, save_json
from ..user_profiles import get_display_name
from ..i18n import format_duration, tr

# 群内待处理的求婚请求
propose_requests = {}

# 群内待确认的拒绝后强娶请求
force_confirm_requests = {}

PROPOSE_RESPONSE_SECONDS = 30
FORCE_CONFIRM_SECONDS = 30


def _cleanup_expired_requests(group_id: str) -> None:
    group_requests = propose_requests.get(group_id)
    if not isinstance(group_requests, dict):
        return

    expired_target_ids = [
        target_id
        for target_id, req in group_requests.items()
        if not isinstance(req, dict)
        or not isinstance(req.get("expire"), (int, float))
    ]
    for target_id in expired_target_ids:
        group_requests.pop(target_id, None)

    if not group_requests:
        propose_requests.pop(group_id, None)


def _cleanup_expired_force_confirmations(group_id: str) -> None:
    group_requests = force_confirm_requests.get(group_id)
    if not isinstance(group_requests, dict):
        return

    now = time.time()
    expired_user_ids = [
        user_id
        for user_id, req in group_requests.items()
        if not isinstance(req, dict)
        or not isinstance(req.get("expire"), (int, float))
        or req["expire"] <= now
    ]
    for user_id in expired_user_ids:
        group_requests.pop(user_id, None)

    if not group_requests:
        force_confirm_requests.pop(group_id, None)


def _is_request_expired(req: dict, now: float | None = None) -> bool:
    expire_at = req.get("expire")
    if not isinstance(expire_at, (int, float)):
        return True
    current_ts = time.time() if now is None else now
    return expire_at <= current_ts


def _get_pending_request_by_proposer(
    group_id: str, proposer_id: str
) -> tuple[str | None, dict | None]:
    _cleanup_expired_requests(group_id)
    group_requests = propose_requests.get(group_id, {})
    for target_id, req in group_requests.items():
        if (
            isinstance(req, dict)
            and req.get("proposer_id") == proposer_id
            and not _is_request_expired(req)
        ):
            return target_id, req
    return None, None


def _delete_request(group_id: str, target_id: str) -> None:
    group_requests = propose_requests.get(group_id)
    if not isinstance(group_requests, dict):
        return

    group_requests.pop(target_id, None)
    if not group_requests:
        propose_requests.pop(group_id, None)


def _delete_requests_by_proposer(group_id: str, proposer_id: str) -> None:
    group_requests = propose_requests.get(group_id)
    if not isinstance(group_requests, dict):
        return

    target_ids = [
        target_id
        for target_id, req in group_requests.items()
        if isinstance(req, dict) and req.get("proposer_id") == proposer_id
    ]
    for target_id in target_ids:
        group_requests.pop(target_id, None)

    if not group_requests:
        propose_requests.pop(group_id, None)


def _delete_force_confirmation(group_id: str, proposer_id: str) -> None:
    group_requests = force_confirm_requests.get(group_id)
    if not isinstance(group_requests, dict):
        return

    group_requests.pop(proposer_id, None)
    if not group_requests:
        force_confirm_requests.pop(group_id, None)


def _format_remaining_seconds(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    if hours > 0:
        return f"{hours}小时{minutes}分"
    if minutes > 0:
        return f"{minutes}分{secs}秒"
    return f"{secs}秒"


async def cmd_propose(plugin_instance, event: AstrMessageEvent):
    """发起求婚指令的逻辑"""
    if event.is_private_chat():
        yield event.plain_result(tr(plugin_instance, "propose_group_only"))
        return

    user_id = str(event.get_sender_id())
    group_id = str(event.get_group_id())
    target_id = extract_target_id_from_message(event)

    if not target_id or target_id == "all":
        yield event.plain_result(tr(plugin_instance, "propose_need_target"))
        return
    if target_id == user_id:
        yield event.plain_result(tr(plugin_instance, "propose_self"))
        return

    user_force_cd = get_force_marry_cooldown_status(plugin_instance, group_id, user_id)
    if user_force_cd:
        yield event.plain_result(tr(plugin_instance, "propose_force_cd_self"))
        return

    target_force_cd = get_force_marry_cooldown_status(
        plugin_instance, group_id, target_id
    )
    if target_force_cd:
        yield event.plain_result(tr(plugin_instance, "propose_force_cd_target"))
        return

    user_propose_cd = get_propose_cooldown_status(plugin_instance, group_id, user_id)
    if user_propose_cd:
        remaining_text = format_duration(plugin_instance, user_propose_cd["remaining"])
        yield event.plain_result(
            tr(plugin_instance, "propose_cd_self", remaining=remaining_text)
        )
        return

    target_propose_cd = get_propose_cooldown_status(
        plugin_instance, group_id, target_id
    )
    if target_propose_cd:
        remaining_text = format_duration(plugin_instance, target_propose_cd["remaining"])
        yield event.plain_result(
            tr(plugin_instance, "propose_cd_target", remaining=remaining_text)
        )
        return

    now = time.time()
    target_name = get_display_name(plugin_instance, event, target_id)
    try:
        if event.get_platform_name() == "aiocqhttp" and isinstance(
            event, AiocqhttpMessageEvent
        ):
            members = await event.bot.api.call_action(
                "get_group_member_list", group_id=int(group_id)
            )
            if isinstance(members, dict) and "data" in members:
                members = members["data"]
            target_name = resolve_member_name(
                members, user_id=target_id, fallback=target_name
            )
    except Exception:
        pass

    pending_target_id, _ = _get_pending_request_by_proposer(group_id, user_id)
    if pending_target_id is not None:
        yield event.plain_result(tr(plugin_instance, "propose_pending"))
        return

    if group_id not in propose_requests:
        propose_requests[group_id] = {}

    propose_requests[group_id][target_id] = {
        "proposer_id": user_id,
        "proposer_name": get_display_name(
            plugin_instance,
            event,
            user_id,
            fallback=event.get_sender_name() or f"用户({user_id})",
        ),
        "target_name": target_name,
        "expire": now + PROPOSE_RESPONSE_SECONDS,
        "umo": event.unified_msg_origin,
    }

    yield event.plain_result(
        tr(
            plugin_instance,
            "propose_sent",
            sender_name=event.get_sender_name(),
            target_name=target_name,
        )
    )

    await asyncio.sleep(PROPOSE_RESPONSE_SECONDS)
    if group_id in propose_requests and target_id in propose_requests[group_id]:
        req = propose_requests[group_id][target_id]
        if req["proposer_id"] == user_id:
            chain_obj = MessageChain()
            chain_obj.chain = [
                Comp.At(qq=user_id),
                Comp.Plain(text=tr(plugin_instance, "propose_timeout")),
            ]

            try:
                await plugin_instance.context.send_message(req["umo"], chain_obj)
            except Exception as e:
                from astrbot.api import logger

                logger.error(f"[propose] 发送超时提醒失败: {e}")

            _delete_request(group_id, target_id)


async def handle_propose_response(plugin_instance, event: AstrMessageEvent):
    """处理求婚回复和拒绝后的强娶确认。"""
    group_id = str(event.get_group_id())
    user_id = str(event.get_sender_id())
    msg = event.message_str.strip()

    _cleanup_expired_force_confirmations(group_id)
    force_req = force_confirm_requests.get(group_id, {}).get(user_id)
    if isinstance(force_req, dict):
        if _is_request_expired(force_req):
            _delete_force_confirmation(group_id, user_id)
        elif msg.lower() in ["是", "确认", "强娶", "要", "yes", "confirm", "はい"]:
            target_id = str(force_req["target_id"])
            _delete_force_confirmation(group_id, user_id)
            event.stop_event()
            async for result in cmd_force_marry(
                plugin_instance,
                event, target_id_override=target_id
            ):
                yield result
            return
        elif msg.lower() in ["否", "不", "不要", "算了", "取消", "no", "cancel", "いいえ"]:
            _delete_force_confirmation(group_id, user_id)
            event.stop_event()
            yield event.plain_result(tr(plugin_instance, "force_cancelled"))
            return

    _cleanup_expired_requests(group_id)
    if group_id in propose_requests and user_id in propose_requests[group_id]:
        req = propose_requests[group_id][user_id]

        if _is_request_expired(req):
            _delete_request(group_id, user_id)
            return

        if msg.lower() in ["同意求婚", "我同意", "同意", "accept", "yes", "同意します"]:
            proposer_id = req["proposer_id"]
            proposer_name = req["proposer_name"]
            target_name = req["target_name"]

            proposer_force_cd = get_force_marry_cooldown_status(
                plugin_instance, group_id, proposer_id
            )
            target_force_cd = get_force_marry_cooldown_status(
                plugin_instance, group_id, user_id
            )
            if proposer_force_cd or target_force_cd:
                _delete_requests_by_proposer(group_id, proposer_id)
                yield event.plain_result(tr(plugin_instance, "propose_invalid"))
                return

            timestamp = datetime.now().isoformat()
            group_records = get_group_records(plugin_instance, group_id)
            daily_limit = plugin_instance.config.get("daily_limit", 1)
            upsert_user_wife_record(
                group_records,
                user_id=proposer_id,
                wife_id=user_id,
                wife_name=target_name,
                timestamp=timestamp,
                daily_limit=daily_limit,
            )
            upsert_user_wife_record(
                group_records,
                user_id=user_id,
                wife_id=proposer_id,
                wife_name=proposer_name,
                timestamp=timestamp,
                daily_limit=daily_limit,
            )

            now = time.time()
            set_propose_cooldown(
                plugin_instance,
                group_id,
                proposer_id,
                related_user_id=user_id,
                role="proposer",
                now=now,
            )
            set_propose_cooldown(
                plugin_instance,
                group_id,
                user_id,
                related_user_id=proposer_id,
                role="target",
                now=now,
            )

            save_json(plugin_instance.records_file, plugin_instance.records)
            save_json(
                plugin_instance.marriage_action_file,
                plugin_instance.marriage_action_records,
            )

            _delete_requests_by_proposer(group_id, proposer_id)

            event.stop_event()
            yield event.plain_result(
                tr(
                    plugin_instance,
                    "propose_accepted",
                    target_name=target_name,
                    proposer_name=proposer_name,
                )
            )
        elif msg.lower() in ["拒绝求婚", "我拒绝", "拒绝", "不同意", "reject", "no", "拒否"]:
            proposer_id = req["proposer_id"]
            target_name = req["target_name"]
            _delete_request(group_id, user_id)

            if group_id not in force_confirm_requests:
                force_confirm_requests[group_id] = {}
            force_confirm_requests[group_id][proposer_id] = {
                "target_id": user_id,
                "target_name": target_name,
                "expire": time.time() + FORCE_CONFIRM_SECONDS,
                "umo": event.unified_msg_origin,
            }

            event.stop_event()
            chain = [
                Comp.At(qq=proposer_id),
                Comp.Plain(
                    tr(plugin_instance, "propose_rejected", target_name=target_name)
                ),
            ]
            yield event.chain_result(chain)
