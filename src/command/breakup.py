import time
from datetime import datetime

from astrbot.api.event import AstrMessageEvent

from ..core import get_group_records
from ..user_profiles import get_display_name
from ..utils import is_allowed_group, save_json


BREAKUP_COOLDOWN_SECONDS = 72 * 60 * 60
FORCE_RECORD_TIME_TOLERANCE_SECONDS = 10


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


async def cmd_breakup(plugin_instance, event: AstrMessageEvent):
    """解除当前用户的普通老婆关系，成功后进入 72 小时冷却。"""
    if event.is_private_chat():
        yield event.plain_result("分手只能在群聊中进行哦~")
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
                f"你还在分手冷却期内，请等待 {_format_remaining_seconds(remaining)} 后再试。"
            )
            return

    group_records = get_group_records(plugin_instance, group_id)
    user_records = [
        record
        for record in group_records
        if str(record.get("user_id")) == user_id
    ]
    if not user_records:
        yield event.plain_result("你现在还没有老婆，无法分手哦。")
        return

    if any(
        _is_force_marriage(plugin_instance, group_id, user_id, record)
        for record in user_records
    ):
        yield event.plain_result("强娶的老婆不能分手！")
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
    relationship_timestamps = {
        str(record.get("timestamp"))
        for record in user_records
        if record.get("timestamp") is not None
    }
    group_records[:] = [
        record
        for record in group_records
        if not (
            str(record.get("user_id")) == user_id
            or (
                str(record.get("user_id")) in wife_ids
                and str(record.get("wife_id")) == user_id
                and str(record.get("timestamp")) in relationship_timestamps
            )
        )
    ]

    plugin_instance.breakup_records.setdefault(group_id, {})[user_id] = now
    save_json(plugin_instance.records_file, plugin_instance.records)
    save_json(plugin_instance.breakup_file, plugin_instance.breakup_records)

    wife_text = "、".join(f"【{name}】" for name in wife_names)
    yield event.plain_result(
        f"你已经和{wife_text}分手了。分手指令将在 72 小时后恢复使用。"
    )
