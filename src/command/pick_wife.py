import random
import time
from dataclasses import dataclass
from datetime import datetime
from math import ceil

import astrbot.api.message_components as Comp
from astrbot.api.event import AstrMessageEvent

from ..platforms.user_profiles import mention_user, avatar_image, platform_result
from ..platforms.telegram_support import is_telegram_event, self_user_id
from ..core import (
    auto_set_other_half_enabled,
    can_onebot_withdraw,
    cleanup_inactive,
    draw_excluded_users,
    get_active_user_days,
    get_group_records,
    schedule_onebot_delete_msg,
    send_onebot_message,
)
from ..utils import is_allowed_group, resolve_member_name, save_json
from ..platforms.user_profiles import get_avatar_source, get_display_name, get_platform_members
from ...waifu_relations import maybe_add_other_half_record
from ..i18n import tr
from ..debug import debug_log
from .qq_official.pick_wife_official import (
    is_official_group, send_candidate_list, send_pick_confirmation,
)

PICK_RESPONSE_SECONDS = 30
PICK_COOLDOWN_SECONDS = 24 * 60 * 60
MAX_CANDIDATES = 6
DEFAULT_CANDIDATE_COUNT = 3
DEFAULT_RETRY_LIMIT = 2

RESELECT_KEYWORDS = ("重新挑选", "重新抽", "reselect", "選び直す")
ABANDON_KEYWORDS = ("放弃", "取消挑选", "give up", "cancel", "やめる")


@dataclass(frozen=True)
class PickRequest:
    """一次进行中的「挑选老婆」请求状态。"""

    group_id: str
    user_id: str
    candidates: list[str]
    candidate_names: list[str]
    created_at: float
    expire_at: float
    retry_count: int = 0


pick_requests: dict[str, dict[str, PickRequest]] = {}


def has_pending_pick(group_id: str, user_id: str) -> bool:
    """Whether this user still has an active candidate list in the group."""
    _cleanup_expired_requests(group_id)
    return isinstance(pick_requests.get(group_id, {}).get(user_id), PickRequest)


def _get_pick_cooldowns(plugin_instance) -> dict[str, dict[str, float]]:
    """Return persisted per-group cooldown records for the pick command."""
    cooldowns = plugin_instance.records.get("pick_cooldowns")
    if not isinstance(cooldowns, dict):
        cooldowns = {}
        plugin_instance.records["pick_cooldowns"] = cooldowns
    return cooldowns


def _get_pick_cooldown_remaining(
    plugin_instance, group_id: str, user_id: str
) -> float:
    """Return the remaining rolling 24-hour cooldown in seconds."""
    group_cooldowns = _get_pick_cooldowns(plugin_instance).get(group_id)
    if not isinstance(group_cooldowns, dict):
        return 0

    try:
        remaining = (
            float(group_cooldowns.get(user_id))
            + PICK_COOLDOWN_SECONDS
            - time.time()
        )
    except (TypeError, ValueError):
        return 0
    return max(0, remaining)


def _set_pick_cooldown(plugin_instance, group_id: str, user_id: str) -> None:
    """Start and persist the rolling cooldown after candidates are displayed."""
    cooldowns = _get_pick_cooldowns(plugin_instance)
    group_cooldowns = cooldowns.get(group_id)
    if not isinstance(group_cooldowns, dict):
        group_cooldowns = {}
        cooldowns[group_id] = group_cooldowns
    group_cooldowns[user_id] = time.time()
    save_json(
        plugin_instance.records_file,
        plugin_instance.records,
        plugin_instance.records_file,
        plugin_instance.config,
    )


def _pick_cooldown_started_today(
    plugin_instance, group_id: str, user_id: str
) -> bool:
    group_cooldowns = _get_pick_cooldowns(plugin_instance).get(group_id)
    if not isinstance(group_cooldowns, dict):
        return False
    try:
        started_at = datetime.fromtimestamp(float(group_cooldowns.get(user_id)))
    except (TypeError, ValueError, OSError):
        return False
    return started_at.date() == datetime.now().date()


def _get_retry_counts(plugin_instance, group_id: str) -> dict[str, int]:
    """Return today's persisted pick retry counts for a group."""
    get_group_records(plugin_instance, group_id)
    group_data = plugin_instance.records["groups"][group_id]
    counts = group_data.get("pick_retry_counts")
    if not isinstance(counts, dict):
        counts = {}
        group_data["pick_retry_counts"] = counts
    return counts


def _get_used_retry_count(plugin_instance, group_id: str, user_id: str) -> int:
    raw = _get_retry_counts(plugin_instance, group_id).get(user_id, 0)
    try:
        return max(0, int(raw))
    except Exception:
        return 0


def _consume_retry(plugin_instance, group_id: str, user_id: str) -> int:
    """记录今天已经展示的一批候选。"""
    limit = _get_retry_limit(plugin_instance)
    if limit <= 0:
        return 0

    counts = _get_retry_counts(plugin_instance, group_id)
    used = min(limit, _get_used_retry_count(plugin_instance, group_id, user_id) + 1)
    counts[user_id] = used
    save_json(
        plugin_instance.records_file,
        plugin_instance.records,
        plugin_instance.records_file,
        plugin_instance.config,
    )
    return used

# 得先看看配置里设置了多少候选人，才能决定抽几个人出来，不够的就只能抽现有的了

def _get_candidate_count(plugin_instance) -> int:
    """读取候选人数配置，自动收敛到 [1, MAX_CANDIDATES]。"""
    raw = plugin_instance.config.get("pick_candidate_count", DEFAULT_CANDIDATE_COUNT)
    try:
        count = int(raw)
    except Exception:
        count = DEFAULT_CANDIDATE_COUNT
    return max(1, min(MAX_CANDIDATES, count))

# 这个防止你一直换一批，怕你太挑

def _get_retry_limit(plugin_instance) -> int:
    """读取重新挑选次数上限配置，0 表示不限次数。"""
    raw = plugin_instance.config.get("pick_retry_limit", DEFAULT_RETRY_LIMIT)
    try:
        limit = int(raw)
    except Exception:
        limit = DEFAULT_RETRY_LIMIT
    return max(0, limit)


def _can_retry(plugin_instance, group_id: str, user_id: str) -> bool:
    """判断用户今天是否还能获取一批候选。"""
    limit = _get_retry_limit(plugin_instance)
    if limit <= 0:
        return True
    return _get_used_retry_count(plugin_instance, group_id, user_id) < limit

# 这个函数用于清理过期的挑选请求，防止内存泄漏和数据混乱（人话：挑选的时间到了，没选就算了）

def _cleanup_expired_requests(group_id: str) -> None:
    """清理该群内所有已过期的挑选请求。"""
    group_requests = pick_requests.get(group_id)
    if not isinstance(group_requests, dict):
        return

    now = time.time()
    expired_user_ids = []
    for user_id, req in group_requests.items():
        if not isinstance(req, PickRequest) or req.expire_at <= now:
            expired_user_ids.append(user_id)
    for user_id in expired_user_ids:
        group_requests.pop(user_id, None)

    if not group_requests:
        pick_requests.pop(group_id, None)

# 这个函数用于删除某个用户在某个群的挑选请求（人话：你挑选的时间到了，没选就算了）

def _delete_request(group_id: str, user_id: str) -> None:
    """删除某用户在该群的挑选请求。"""
    group_requests = pick_requests.get(group_id)
    if not isinstance(group_requests, dict):
        return

    group_requests.pop(user_id, None)
    if not group_requests:
        pick_requests.pop(group_id, None)

# 这个函数用于生成候选列表的文本展示（人话：给你看看有谁可以选，你挑一个）

def _format_candidate_list(plugin_instance, req: PickRequest) -> str:
    """生成纯文本候选编号列表（不带头像）。"""
    lines = [
        tr(plugin_instance, "pick_list_title"),
    ]
    for index, name in enumerate(req.candidate_names, 1):
        lines.append(f"{index}. {name}")
    lines.append(tr(plugin_instance, "pick_list_footer"))
    return "\n".join(lines)


async def _send_candidate_list(plugin_instance, event, req):
    text = _format_candidate_list(plugin_instance, req)
    if not await send_candidate_list(plugin_instance, event, req, text):
        yield event.plain_result(text)


# 从活跃池里捞一批人来当候选（人话：先给你物色几个对象看看，有没有顺眼的）
async def _draw_candidates(
    plugin_instance, event: AstrMessageEvent, user_id: str, count: int
) -> list[tuple[str, str]]:
    """从活跃池抽取候选，返回 [(用户ID, 用户名字)]，按展示顺序排列。"""
    group_id = str(event.get_group_id())
    cleanup_inactive(plugin_instance, group_id)

    current_member_ids: list[str] = []
    members = []
    try:
        members = await get_platform_members(plugin_instance, event)
        current_member_ids = [
            str(m.get("user_id")) for m in members if not m.get("is_bot", False)
        ]
    except Exception as e:
        debug_log(plugin_instance, "pick", f"member_lookup_failed group={group_id} error={e}")
        members = []

    bot_id = self_user_id(event)
    active_pool = plugin_instance.active_users.get(group_id, {})
    if not isinstance(active_pool, dict):
        active_pool = {}

    excluded = draw_excluded_users(plugin_instance)
    if not plugin_instance.config.get("allow_marry_bot", False):
        excluded.add(bot_id)
    excluded.update([user_id, "0"])

    if current_member_ids or is_telegram_event(event):
        pool = [
            uid
            for uid in active_pool.keys()
            if uid not in excluded and uid in current_member_ids
        ]
    else:
        pool = [uid for uid in active_pool.keys() if uid not in excluded]

    debug_log(plugin_instance, "pick", f"pool group={group_id} user={user_id} active={len(active_pool)} members={len(members)} current_member_ids={len(current_member_ids)} member_filter={bool(current_member_ids) or is_telegram_event(event)} excluded={sorted(excluded)} eligible={len(pool)} requested={count}")
    random.shuffle(pool)
    chosen = pool[:count]

    result: list[tuple[str, str]] = []
    for uid in chosen:
        name = get_display_name(plugin_instance, event, uid)
        try:
            name = resolve_member_name(members, user_id=uid, fallback=name)
        except Exception:
            pass
        result.append((uid, name))
    debug_log(plugin_instance, "pick", f"candidates group={group_id} user={user_id} selected={result}")
    return result


# 这个命令用于触发挑选老婆的逻辑（人话：相亲）
async def cmd_pick_wife(plugin_instance, event: AstrMessageEvent):
    """「挑选老婆」命令入口：校验后抽取候选并展示编号列表。"""
    debug_log(plugin_instance, "pick", f"start group={event.get_group_id()} user={event.get_sender_id()} private={event.is_private_chat()}")
    if event.is_private_chat():
        yield event.plain_result(tr(plugin_instance, "group_only"))
        return

    group_id = str(event.get_group_id())
    if not is_allowed_group(group_id, plugin_instance.config):
        debug_log(plugin_instance, "pick", f"skip group={group_id} reason=disallowed_group")
        return

    user_id = str(event.get_sender_id())
    group_records = get_group_records(plugin_instance, group_id)
    today_count = len([r for r in group_records if r["user_id"] == user_id])
    daily_limit = plugin_instance.config.get("daily_limit", 1)
    debug_log(plugin_instance, "pick", f"daily group={group_id} user={user_id} today_count={today_count} limit={daily_limit}")
    if today_count >= daily_limit:
        yield event.plain_result(tr(plugin_instance, "pick_already_done"))
        return

    _cleanup_expired_requests(group_id)
    if isinstance(pick_requests.get(group_id, {}).get(user_id), PickRequest):
        debug_log(plugin_instance, "pick", f"blocked group={group_id} user={user_id} reason=pending_request")
        yield event.plain_result(tr(plugin_instance, "pick_in_progress"))
        return

    cooldown_remaining = _get_pick_cooldown_remaining(
        plugin_instance, group_id, user_id
    )
    debug_log(plugin_instance, "pick", f"cooldown group={group_id} user={user_id} remaining={cooldown_remaining} started_today={_pick_cooldown_started_today(plugin_instance, group_id, user_id)}")
    # 当天的后续批次由每日额度控制，避免放弃或超时后被首批冷却拦住。
    if cooldown_remaining > 0 and not _pick_cooldown_started_today(
        plugin_instance, group_id, user_id
    ):
        remaining_minutes = max(1, ceil(cooldown_remaining / 60))
        yield event.plain_result(
            tr(plugin_instance, "pick_cooldown", minutes=remaining_minutes)
        )
        return

    retry_limit = _get_retry_limit(plugin_instance)
    used_retry_count = _get_used_retry_count(plugin_instance, group_id, user_id)
    debug_log(plugin_instance, "pick", f"batches group={group_id} user={user_id} used={used_retry_count} limit={retry_limit}")
    if retry_limit > 0 and used_retry_count >= retry_limit:
        yield event.plain_result(
            tr(plugin_instance, "pick_batches_used", limit=retry_limit)
        )
        return

    count = _get_candidate_count(plugin_instance)
    candidates = await _draw_candidates(plugin_instance, event, user_id, count)
    if not candidates:
        yield event.plain_result(
            tr(
                plugin_instance,
                "wife_pool_empty",
                days=get_active_user_days(plugin_instance),
            )
        )
        return

    used_retry_count = _consume_retry(plugin_instance, group_id, user_id)
    _set_pick_cooldown(plugin_instance, group_id, user_id)
    now = time.time()
    req = PickRequest(
        group_id=group_id,
        user_id=user_id,
        candidates=[cid for cid, _ in candidates],
        candidate_names=[name for _, name in candidates],
        created_at=now,
        expire_at=now + PICK_RESPONSE_SECONDS,
        retry_count=used_retry_count,
    )
    pick_requests.setdefault(group_id, {})[user_id] = req
    debug_log(plugin_instance, "pick", f"request_created group={group_id} user={user_id} candidates={req.candidates} batch={req.retry_count} expire={req.expire_at}")

    async for result in _send_candidate_list(plugin_instance, event, req):
        yield result


# 把选中的老婆发出来（人话：官宣，你挑中Ta了）
async def _send_pick_confirmation(
    plugin_instance, event: AstrMessageEvent, user_id: str, wife_id: str, wife_name: str
):
    """发送挑选确认结果（含所选老婆头像）。"""
    avatar_url = await get_avatar_source(plugin_instance, event, wife_id)
    text = tr(plugin_instance, "pick_success", wife_name=wife_name)
    if is_official_group(event):
        async for result in send_pick_confirmation(event, user_id, text, avatar_url):
            yield result
        return
    if can_onebot_withdraw(plugin_instance, event):
        message_id = await send_onebot_message(
            plugin_instance,
            event,
            message=[
                {"type": "at", "data": {"qq": user_id}},
                {"type": "text", "data": {"text": text}},
                {"type": "image", "data": {"file": avatar_url}},
            ],
        )
        if message_id is not None:
            schedule_onebot_delete_msg(plugin_instance, event.bot, message_id=message_id)
        return

    chain = [
        mention_user(plugin_instance, event, user_id),
        Comp.Plain(text),
    ]
    if avatar_url:
        chain.append(avatar_image(avatar_url))
    yield platform_result(event, chain)


# 处理挑选期间的回复（人话：等着看你回数字/重新挑选/放弃）
async def handle_pick_response(plugin_instance, event: AstrMessageEvent):
    """处理挑选期间的回复：编号选择、重新挑选、放弃。"""
    if event.is_private_chat():
        return

    group_id = str(event.get_group_id())
    user_id = str(event.get_sender_id())
    msg = event.message_str.strip()

    previous_req = pick_requests.get(group_id, {}).get(user_id)
    if isinstance(previous_req, PickRequest) and previous_req.expire_at <= time.time():
        debug_log(plugin_instance, "pick", f"request_expired group={group_id} user={user_id} expire={previous_req.expire_at}")
    _cleanup_expired_requests(group_id)
    req = pick_requests.get(group_id, {}).get(user_id)
    if not isinstance(req, PickRequest):
        return

    debug_log(plugin_instance, "pick", f"response group={group_id} user={user_id} reply={msg!r} batch={req.retry_count} expire={req.expire_at}")
    # 用户觉得这批都不行，重新抽一批（人话：没一个顺眼的，换个组相亲）
    if msg in RESELECT_KEYWORDS:
        if not _can_retry(plugin_instance, group_id, user_id):
            debug_log(plugin_instance, "pick", f"retry_blocked group={group_id} user={user_id} used={_get_used_retry_count(plugin_instance, group_id, user_id)} limit={_get_retry_limit(plugin_instance)}")
            event.stop_event()
            yield event.plain_result(
                tr(
                    plugin_instance,
                    "pick_retry_limit",
                    limit=_get_retry_limit(plugin_instance),
                )
            )
            return

        count = _get_candidate_count(plugin_instance)
        candidates = await _draw_candidates(plugin_instance, event, user_id, count)
        if not candidates:
            _delete_request(group_id, user_id)
            event.stop_event()
            yield event.plain_result(tr(plugin_instance, "wife_pool_retry"))
            return

        used_retry_count = _consume_retry(plugin_instance, group_id, user_id)
        new_req = PickRequest(
            group_id=req.group_id,
            user_id=req.user_id,
            candidates=[cid for cid, _ in candidates],
            candidate_names=[name for _, name in candidates],
            created_at=time.time(),
            expire_at=time.time() + PICK_RESPONSE_SECONDS,
            retry_count=used_retry_count,
        )
        pick_requests[group_id][user_id] = new_req
        debug_log(plugin_instance, "pick", f"reselected group={group_id} user={user_id} candidates={new_req.candidates} batch={new_req.retry_count} expire={new_req.expire_at}")
        event.stop_event()
        async for result in _send_candidate_list(plugin_instance, event, new_req):
            yield result
        return

    # 放弃仅结束当前请求，不退还已经展示的候选批次。
    if msg in ABANDON_KEYWORDS:
        debug_log(plugin_instance, "pick", f"abandoned group={group_id} user={user_id}")
        used_retry_count = _get_used_retry_count(plugin_instance, group_id, user_id)
        _delete_request(group_id, user_id)
        event.stop_event()
        retry_limit = _get_retry_limit(plugin_instance)
        if retry_limit > 0 and used_retry_count >= retry_limit:
            yield event.plain_result(tr(plugin_instance, "pick_abandon_used"))
        else:
            remaining_text = (
                tr(plugin_instance, "unlimited")
                if retry_limit <= 0
                else str(max(0, retry_limit - used_retry_count))
            )
            yield event.plain_result(
                tr(plugin_instance, "pick_abandon", remaining=remaining_text)
            )
        return

    if not msg.isdigit():
        return

    # 用户回了个数字，校验一下再选中（人话：看看你选的是几号）

    index = int(msg)
    if not (1 <= index <= len(req.candidates)):
        debug_log(plugin_instance, "pick", f"invalid_number group={group_id} user={user_id} index={index} max={len(req.candidates)}")
        event.stop_event()
        yield event.plain_result(
            tr(plugin_instance, "pick_invalid_number", max=len(req.candidates))
        )
        return

    wife_id = req.candidates[index - 1]
    wife_name = req.candidate_names[index - 1]
    group_records = get_group_records(plugin_instance, group_id)
    daily_limit = plugin_instance.config.get("daily_limit", 1)
    debug_log(plugin_instance, "pick", f"confirm_check group={group_id} user={user_id} index={index} wife={wife_id} today_count={sum(r['user_id'] == user_id for r in group_records)} limit={daily_limit}")
    if sum(r["user_id"] == user_id for r in group_records) >= daily_limit:
        debug_log(plugin_instance, "pick", f"confirm_blocked group={group_id} user={user_id} reason=daily_limit")
        _delete_request(group_id, user_id)
        event.stop_event()
        yield event.plain_result(tr(plugin_instance, "pick_already_done"))
        return
    _delete_request(group_id, user_id)

    timestamp = datetime.now().isoformat()
    user_name = get_display_name(
        plugin_instance,
        event,
        user_id,
        fallback=event.get_sender_name() or f"用户({user_id})",
    )
    group_records.append(
        {
            "user_id": user_id,
            "wife_id": wife_id,
            "wife_name": wife_name,
            "timestamp": timestamp,
            "picked": True,
        }
    )

    maybe_add_other_half_record(
        records=group_records,
        user_id=user_id,
        user_name=user_name,
        wife_id=wife_id,
        wife_name=wife_name,
        enabled=auto_set_other_half_enabled(plugin_instance),
        timestamp=timestamp,
    )

    save_json(
        plugin_instance.records_file,
        plugin_instance.records,
        plugin_instance.records_file,
        plugin_instance.config,
    )

    debug_log(plugin_instance, "pick", f"confirmed group={group_id} user={user_id} wife={wife_id} wife_name={wife_name}")
    event.stop_event()
    async for result in _send_pick_confirmation(
        plugin_instance, event, user_id, wife_id, wife_name
    ):
        yield result
