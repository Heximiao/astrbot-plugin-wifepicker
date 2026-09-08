import time
from datetime import datetime

from astrbot.api.event import AstrMessageEvent

from ..core import get_group_records
from ..platforms.user_profiles import get_display_name
from ..utils import is_allowed_group, save_json
from ..i18n import format_duration, tr


BREAKUP_COOLDOWN_SECONDS = 72 * 60 * 60
FORCE_RECORD_TIME_TOLERANCE_SECONDS = 10
BREAKUP_RESPONSE_SECONDS = 60


def _requests(plugin_instance):
    if not hasattr(plugin_instance, "_breakup_requests"):
        plugin_instance._breakup_requests = {}
    requests = plugin_instance._breakup_requests
    now = time.time()
    for key, request in list(requests.items()):
        if request["expire_at"] <= now:
            del requests[key]
    return requests


def _format_remaining_seconds(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60

    parts = []
    if days:
        parts.append(f"{days}天")
    if hours:
        parts.append(f"{hours}小时")
    if minutes or not parts:
        parts.append(f"{minutes}分钟")
    return "".join(parts)


def _record_timestamp(record: dict) -> float | None:
    raw = record.get("timestamp")
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw).timestamp()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _is_force_marriage(plugin_instance, group_id: str, user_id: str, record: dict) -> bool:
    if not record.get("forced"):
        return False

    forced_at = plugin_instance.forced_records.get(group_id, {}).get(user_id)
    record_at = _record_timestamp(record)
    if not isinstance(forced_at, (int, float)) or record_at is None:
        return False

    return abs(record_at - forced_at) <= FORCE_RECORD_TIME_TOLERANCE_SECONDS


async def cmd_breakup(plugin_instance, event: AstrMessageEvent, *, selection=None):
    """解除当前用户的普通老婆关系，成功后进入 72 小时冷却。"""
    if event.is_private_chat():
        yield event.plain_result(tr(plugin_instance, "breakup_group_only"))
        return

    group_id = str(event.get_group_id())
    if not is_allowed_group(group_id, plugin_instance.config):
        return

    user_id = str(event.get_sender_id())
    now = time.time()
    last_breakup_at = plugin_instance.breakup_records.get(group_id, {}).get(user_id)
    if isinstance(last_breakup_at, (int, float)):
        remaining = last_breakup_at + BREAKUP_COOLDOWN_SECONDS - now
        if remaining > 0:
            yield event.plain_result(
                tr(
                    plugin_instance,
                    "breakup_cooldown",
                    remaining=format_duration(plugin_instance, remaining),
                )
            )
            return

    group_records = get_group_records(plugin_instance, group_id)
    user_records = [
        record
        for record in group_records
        if str(record.get("user_id")) == user_id
    ]
    if not user_records:
        yield event.plain_result(tr(plugin_instance, "breakup_no_wife"))
        return

    if selection is None and plugin_instance.config.get("daily_limit", 1) > 1:
        wife_ids = list(dict.fromkeys(str(r.get("wife_id")) for r in user_records))
        _requests(plugin_instance)[(group_id, user_id)] = {
            "expire_at": now + BREAKUP_RESPONSE_SECONDS,
            "records": [dict(r) for r in user_records],
            "wife_ids": wife_ids,
        }
        lines = [tr(plugin_instance, "breakup_choose")]
        for index, wife_id in enumerate(wife_ids, 1):
            record = next(r for r in user_records if str(r.get("wife_id")) == wife_id)
            name = get_display_name(plugin_instance, event, wife_id,
                                    fallback=str(record.get("wife_name") or wife_id))
            lines.append(f"{index}. {name}（{wife_id}）")
        lines.append(f"{len(wife_ids) + 1}. " + tr(plugin_instance, "breakup_all"))
        lines.append(tr(plugin_instance, "breakup_choose_hint"))
        yield event.plain_result("\n".join(lines))
        return

    if selection is not None:
        if user_records != selection["records"]:
            yield event.plain_result(tr(plugin_instance, "breakup_changed"))
            return
        if selection["wife_id"] is not None:
            user_records = [r for r in user_records
                            if str(r.get("wife_id")) == selection["wife_id"]]

    if any(
        _is_force_marriage(plugin_instance, group_id, user_id, record)
        for record in user_records
    ):
        yield event.plain_result(tr(plugin_instance, "breakup_forced"))
        return

    wife_ids = {str(record.get("wife_id")) for record in user_records}
    wife_names = [
        get_display_name(
            plugin_instance,
            event,
            str(record.get("wife_id")),
            fallback=str(record.get("wife_name") or "对方"),
        )
        for record in user_records
    ]

    # 同时移除同一时刻自动建立或求婚建立的反向关系，避免留下单向记录。
    relationship_keys = {
        (str(record.get("wife_id")), str(record.get("timestamp")))
        for record in user_records
        if record.get("timestamp") is not None
    }
    group_records[:] = [
        record
        for record in group_records
        if not (
            (str(record.get("user_id")) == user_id
             and str(record.get("wife_id")) in wife_ids)
            or (
                str(record.get("user_id")) in wife_ids
                and str(record.get("wife_id")) == user_id
                and (str(record.get("user_id")), str(record.get("timestamp")))
                in relationship_keys
            )
        )
    ]

    plugin_instance.breakup_records.setdefault(group_id, {})[user_id] = now
    save_json(plugin_instance.records_file, plugin_instance.records)
    save_json(plugin_instance.breakup_file, plugin_instance.breakup_records)

    wife_text = "、".join(f"【{name}】" for name in wife_names)
    yield event.plain_result(
        tr(plugin_instance, "breakup_success", wives=wife_text)
    )


async def handle_breakup_response(plugin_instance, event: AstrMessageEvent):
    """仅接受发起人在原群聊的选择，优先于其他数字回复处理。"""
    if event.is_private_chat() or getattr(event, "_wifepicker_breakup_handled", False):
        return
    key = (str(event.get_group_id()), str(event.get_sender_id()))
    requests = _requests(plugin_instance)
    request = requests.get(key)
    if request is None:
        return
    msg = event.message_str.strip()
    cancel = msg in {"取消", "放弃", "cancel"}
    choose_all = msg in {"全部离婚", "全部分手", "all"}
    if not (cancel or choose_all or msg.isdecimal()):
        return
    event._wifepicker_breakup_handled = True
    event.stop_event()
    if not is_allowed_group(key[0], plugin_instance.config):
        requests.pop(key, None)
        return
    if cancel:
        requests.pop(key, None)
        yield event.plain_result(tr(plugin_instance, "breakup_cancelled"))
        return
    maximum = len(request["wife_ids"]) + 1
    index = maximum if choose_all else (int(msg) if len(msg) < 10 else 0)
    if not 1 <= index <= maximum:
        yield event.plain_result(tr(plugin_instance, "pick_invalid_number", max=maximum))
        return
    requests.pop(key, None)
    selection = {"records": request["records"],
                 "wife_id": None if index == maximum else request["wife_ids"][index - 1]}
    async for result in cmd_breakup(plugin_instance, event, selection=selection):
        yield result
