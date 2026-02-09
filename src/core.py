import asyncio
import time
import os
from datetime import datetime, timedelta
from typing import Set

from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)

from ..onebot_api import extract_message_id
from .utils import (
    save_json,
    normalize_user_id_set,
    is_allowed_group,
    resolve_member_name,
)


async def send_onebot_message(plugin, event, *, message: list[dict]) -> object:
    assert isinstance(event, AiocqhttpMessageEvent)

    group_id = event.get_group_id()
    if group_id:
        resp = await event.bot.api.call_action(
            "send_group_msg", group_id=int(group_id), message=message
        )
    else:
        resp = await event.bot.api.call_action(
            "send_private_msg",
            user_id=int(event.get_sender_id()),
            message=message,
        )

    message_id = extract_message_id(resp)
    if message_id is None:
        plugin.logger = getattr(plugin, "logger", None)
        if plugin.logger:
            plugin.logger.warning(f"无法解析 send_*_msg 返回的 message_id: {resp!r}")
    return message_id


def schedule_onebot_delete_msg(plugin, client, *, message_id: object) -> None:
    delay = auto_withdraw_delay_seconds(plugin)

    async def _runner():
        await asyncio.sleep(delay)
        try:
            await client.api.call_action("delete_msg", message_id=message_id)
        except Exception as e:
            plugin.logger = getattr(plugin, "logger", None)
            if plugin.logger:
                plugin.logger.warning(f"自动撤回失败: {e}")

    task = asyncio.create_task(_runner())
    plugin._withdraw_tasks.add(task)
    task.add_done_callback(plugin._withdraw_tasks.discard)


def record_active(plugin, event) -> None:
    group_id = event.get_group_id()
    if not group_id or not is_allowed_group(str(group_id), plugin.config):
        return

    user_id, bot_id = str(event.get_sender_id()), str(event.get_self_id())
    if user_id == bot_id or user_id == "0":
        return

    group_key = str(group_id)
    if group_key not in plugin.active_users:
        plugin.active_users[group_key] = {}
    plugin.active_users[group_key][user_id] = time.time()
    # preserve original save_json call as-is
    save_json(plugin.active_file, plugin.active_users, plugin.records_file, plugin.config)


def clean_rbq_stats(plugin) -> None:
    now = time.time()
    thirty_days = 30 * 24 * 3600
    seven_days = 7 * 24 * 3600
    five_days = 5 * 24 * 3600 # 新增 5 天逻辑

    new_stats = {}
    for gid, users in plugin.rbq_stats.items():
        new_users = {}
        active_group = plugin.active_users.get(gid, {})

        for uid, timestamps in users.items():
            # 1. 只保留 30 天内的强娶记录
            valid_ts = [ts for ts in timestamps if now - ts < thirty_days]
            count = len(valid_ts)

            if count == 0:
                continue # 没有记录直接跳过，不加入 new_users

            # 获取最后一次被强娶的时间（用于没查到活跃记录时的兜底判断）
            last_forced_ts = max(valid_ts) if valid_ts else 0
            
            # 活跃状态检查
            is_in_active = uid in active_group
            last_active_ts = active_group.get(uid, 0)

            should_keep = True
            
            if not is_in_active:
                # --- 核心逻辑修改 ---
                if last_active_ts == 0:
                    # 情况 A: active_users 里完全没记录，根据最后一次强娶时间判断
                    # 如果距离最后一次被强娶已经超过 5 天，则清理
                    if now - last_forced_ts > five_days:
                        should_keep = False
                else:
                    # 情况 B: active_users 有记录，但该用户已经一个月没说话了 (原来的逻辑)
                    if count <= 4 and (now - last_active_ts > seven_days):
                        should_keep = False
                # --------------------

            if should_keep:
                new_users[uid] = valid_ts

        if new_users:
            new_stats[gid] = new_users

    plugin.rbq_stats = new_stats
    save_json(plugin.rbq_stats_file, plugin.rbq_stats)


def draw_excluded_users(plugin) -> Set[str]:
    return normalize_user_id_set(plugin.config.get("excluded_users", []))


def force_marry_excluded_users(plugin) -> Set[str]:
    return normalize_user_id_set(plugin.config.get("force_marry_excluded_users", []))


def ensure_today_records(plugin) -> None:
    today = datetime.now().strftime("%Y-%m-%d")
    if plugin.records.get("date") != today:
        plugin.records = {"date": today, "groups": {}}


def get_group_records(plugin, group_id: str) -> list:
    ensure_today_records(plugin)
    if group_id not in plugin.records["groups"]:
        plugin.records["groups"][group_id] = {"records": []}
    return plugin.records["groups"][group_id]["records"]


def auto_set_other_half_enabled(plugin) -> bool:
    return bool(plugin.config.get("auto_set_other_half", False))


def auto_withdraw_enabled(plugin) -> bool:
    return bool(plugin.config.get("auto_withdraw_enabled", False))


def auto_withdraw_delay_seconds(plugin) -> int:
    raw = plugin.config.get("auto_withdraw_delay_seconds", 5)
    try:
        delay = int(raw)
    except Exception:
        delay = 5
    return max(1, delay)


def can_onebot_withdraw(plugin, event) -> bool:
    return auto_withdraw_enabled(plugin) and event.get_platform_name() == "aiocqhttp"


def cleanup_inactive(plugin, group_id: str):
    if group_id not in plugin.active_users:
        return
    now, limit = time.time(), 30 * 24 * 3600
    active_group = plugin.active_users[group_id]
    new_active = {uid: ts for uid, ts in active_group.items() if (now - ts < limit) and uid != "0"}
    if len(active_group) != len(new_active):
        plugin.active_users[group_id] = new_active
        save_json(plugin.active_file, plugin.active_users)
