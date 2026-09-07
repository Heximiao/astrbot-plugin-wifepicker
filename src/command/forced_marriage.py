from datetime import datetime
import time

import astrbot.api.message_components as Comp
from astrbot.api.event import AstrMessageEvent

from ..platforms.user_profiles import mention_user, avatar_image, platform_chain
from ..platforms.telegram_support import self_user_id
from ..core import (
    auto_set_other_half_enabled,
    can_onebot_withdraw,
    clean_rbq_stats,
    force_marry_excluded_users,
    get_force_marry_cooldown_status,
    get_group_records,
    get_propose_cooldown_status,
    send_onebot_message,
    schedule_onebot_delete_msg,
    upsert_user_wife_record,
)
from ...waifu_relations import maybe_add_other_half_record
from ..platforms.user_profiles import get_avatar_source, get_display_name, get_platform_members
from ..utils import (
    extract_target_id_from_message,
    is_allowed_group,
    resolve_member_name,
    save_json,
)
from ..i18n import format_duration, tr


def _format_remaining_seconds(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    mins = (total_seconds % 3600) // 60

    if days > 0:
        return f"{days}天{hours}小时{mins}分"
    if hours > 0:
        return f"{hours}小时{mins}分"
    if mins > 0:
        secs = total_seconds % 60
        return f"{mins}分{secs}秒"
    return f"{total_seconds}秒"


async def cmd_force_marry(
    plugin_instance, event: AstrMessageEvent, target_id_override: str | None = None
):
    """强娶 + @要娶的那个人"""
    if event.is_private_chat():
        yield event.plain_result(tr(plugin_instance, "group_only"))
        return

    user_id = str(event.get_sender_id())
    bot_id = self_user_id(event)
    group_id = str(event.get_group_id())
    if not is_allowed_group(group_id, plugin_instance.config):
        return

    now = time.time()
    user_propose_cd = get_propose_cooldown_status(plugin_instance, group_id, user_id)
    if user_propose_cd:
        remaining_text = format_duration(plugin_instance, user_propose_cd["remaining"])
        yield event.plain_result(
            tr(plugin_instance, "force_propose_cd", remaining=remaining_text)
        )
        return

    user_force_cd = get_force_marry_cooldown_status(plugin_instance, group_id, user_id)
    if user_force_cd:
        remaining_text = format_duration(plugin_instance, user_force_cd["remaining"])
        reset_text = user_force_cd["reset_dt"].strftime("%m-%d %H:%M")
        yield event.plain_result(
            tr(
                plugin_instance,
                "force_self_cd",
                remaining=remaining_text,
                reset_time=reset_text,
            )
        )
        return

    target_id = (
        str(target_id_override)
        if target_id_override
        else extract_target_id_from_message(event, plugin_instance)
    )

    if not target_id or target_id == "all":
        yield event.plain_result(tr(plugin_instance, "force_need_target"))
        return

    if target_id == user_id:
        yield event.plain_result(tr(plugin_instance, "marry_self"))
        return

    target_propose_cd = get_propose_cooldown_status(
        plugin_instance, group_id, target_id
    )
    if target_propose_cd:
        remaining_text = format_duration(plugin_instance, target_propose_cd["remaining"])
        yield event.plain_result(
            tr(plugin_instance, "force_target_propose_cd", remaining=remaining_text)
        )
        return

    force_excluded = force_marry_excluded_users(plugin_instance)
    if not plugin_instance.config.get("allow_marry_bot", False):
        force_excluded.add(bot_id)
    force_excluded.add("0")
    if target_id in force_excluded:
        yield event.plain_result(tr(plugin_instance, "force_excluded"))
        return

    target_name = get_display_name(plugin_instance, event, target_id)
    user_name = get_display_name(
        plugin_instance,
        event,
        user_id,
        fallback=event.get_sender_name() or f"用户({user_id})",
    )
    members = []
    try:
        members = await get_platform_members(plugin_instance, event)
        target_name = resolve_member_name(
            members, user_id=target_id, fallback=target_name
        )
        user_name = resolve_member_name(
            members, user_id=user_id, fallback=user_name
        )
    except Exception:
        pass

    group_records = get_group_records(plugin_instance, group_id)

    if group_id not in plugin_instance.rbq_stats:
        plugin_instance.rbq_stats[group_id] = {}
    if target_id not in plugin_instance.rbq_stats[group_id]:
        plugin_instance.rbq_stats[group_id][target_id] = []

    plugin_instance.rbq_stats[group_id][target_id].append(time.time())
    clean_rbq_stats(plugin_instance)
    save_json(plugin_instance.rbq_stats_file, plugin_instance.rbq_stats)

    timestamp = datetime.now().isoformat()
    upsert_user_wife_record(
        group_records,
        user_id=user_id,
        wife_id=target_id,
        wife_name=target_name,
        timestamp=timestamp,
        daily_limit=plugin_instance.config.get("daily_limit", 1),
    )

    maybe_add_other_half_record(
        records=group_records,
        user_id=user_id,
        user_name=user_name,
        wife_id=target_id,
        wife_name=target_name,
        enabled=auto_set_other_half_enabled(plugin_instance),
        timestamp=timestamp,
    )

    plugin_instance.forced_records.setdefault(group_id, {})[user_id] = now

    save_json(plugin_instance.records_file, plugin_instance.records)
    save_json(plugin_instance.forced_file, plugin_instance.forced_records)

    avatar_url = await get_avatar_source(plugin_instance, event, target_id)
    result_text = tr(plugin_instance, "force_success", target_name=target_name)
    if can_onebot_withdraw(plugin_instance, event):
        message_id = await send_onebot_message(
            plugin_instance,
            event,
            message=[
                {"type": "at", "data": {"qq": user_id}},
                {"type": "text", "data": {"text": result_text}},
                {"type": "image", "data": {"file": avatar_url}},
            ],
        )
        if message_id is not None:
            schedule_onebot_delete_msg(
                plugin_instance, event.bot, message_id=message_id
            )
        return

    chain = [
        mention_user(plugin_instance, event, user_id),
        Comp.Plain(result_text),
    ]
    if avatar_url:
        chain.append(avatar_image(avatar_url))
    yield event.chain_result(platform_chain(event, chain))
