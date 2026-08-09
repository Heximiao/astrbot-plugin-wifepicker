import random
import time
from dataclasses import dataclass
from datetime import datetime

import astrbot.api.message_components as Comp
from astrbot.api.event import AstrMessageEvent
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)

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
from ...waifu_relations import maybe_add_other_half_record

PICK_RESPONSE_SECONDS = 30
MAX_CANDIDATES = 6
DEFAULT_CANDIDATE_COUNT = 3
DEFAULT_RETRY_LIMIT = 2

RESELECT_KEYWORDS = ("重新挑选", "重新抽")
ABANDON_KEYWORDS = ("放弃", "取消挑选")


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

def _format_candidate_list(req: PickRequest) -> str:
    """生成纯文本候选编号列表（不带头像）。"""
    lines = [
        "🌹 请选择你的老婆（回复编号，30秒内有效）：",
    ]
    for index, name in enumerate(req.candidate_names, 1):
        lines.append(f"{index}. {name}")
    lines.append("\n回复「重新挑选」换一批，回复「放弃」取消。")
    return "\n".join(lines)


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
        if event.get_platform_name() == "aiocqhttp":
            assert isinstance(event, AiocqhttpMessageEvent)
            members = await event.bot.api.call_action(
                "get_group_member_list", group_id=int(group_id)
            )
            if isinstance(members, dict) and isinstance(members.get("data"), list):
                members = members["data"]
            current_member_ids = [str(m.get("user_id")) for m in members]
    except Exception:
        members = []

    bot_id = str(event.get_self_id())
    active_pool = plugin_instance.active_users.get(group_id, {})
    if not isinstance(active_pool, dict):
        active_pool = {}

    excluded = draw_excluded_users(plugin_instance)
    if not plugin_instance.config.get("allow_marry_bot", False):
        excluded.add(bot_id)
    excluded.update([user_id, "0"])

    if current_member_ids:
        pool = [
            uid
            for uid in active_pool.keys()
            if uid not in excluded and uid in current_member_ids
        ]
    else:
        pool = [uid for uid in active_pool.keys() if uid not in excluded]

    random.shuffle(pool)
    chosen = pool[:count]

    result: list[tuple[str, str]] = []
    for uid in chosen:
        name = f"用户({uid})"
        try:
            name = resolve_member_name(members, user_id=uid, fallback=name)
        except Exception:
            pass
        result.append((uid, name))
    return result


# 这个命令用于触发挑选老婆的逻辑（人话：相亲）
async def cmd_pick_wife(plugin_instance, event: AstrMessageEvent):
    """「挑选老婆」命令入口：校验后抽取候选并展示编号列表。"""
    if event.is_private_chat():
        yield event.plain_result("此功能仅在群聊中可用哦~")
        return

    group_id = str(event.get_group_id())
    if not is_allowed_group(group_id, plugin_instance.config):
        return

    user_id = str(event.get_sender_id())
    group_records = get_group_records(plugin_instance, group_id)
    today_count = len([r for r in group_records if r["user_id"] == user_id])
    daily_limit = plugin_instance.config.get("daily_limit", 1)
    if today_count >= daily_limit:
        yield event.plain_result("你今天已经抽过/挑选过老婆了，明天再来吧！")
        return

    _cleanup_expired_requests(group_id)
    if isinstance(pick_requests.get(group_id, {}).get(user_id), PickRequest):
        yield event.plain_result("你已经在挑选中了，请回复编号、重新挑选或放弃。")
        return

    retry_limit = _get_retry_limit(plugin_instance)
    used_retry_count = _get_used_retry_count(plugin_instance, group_id, user_id)
    if retry_limit > 0 and used_retry_count >= retry_limit:
        yield event.plain_result(
            f"你今天的候选批次已用完（{retry_limit}批），可以使用「今日老婆」随机抽取。"
        )
        return

    count = _get_candidate_count(plugin_instance)
    candidates = await _draw_candidates(plugin_instance, event, user_id, count)
    if not candidates:
        yield event.plain_result(
            f"老婆池为空（需有人在{get_active_user_days(plugin_instance)}天内发言）。"
        )
        return

    used_retry_count = _consume_retry(plugin_instance, group_id, user_id)
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

    yield event.plain_result(_format_candidate_list(req))


# 把选中的老婆发出来（人话：官宣，你挑中Ta了）
async def _send_pick_confirmation(
    plugin_instance, event: AstrMessageEvent, user_id: str, wife_id: str, wife_name: str
):
    """发送挑选确认结果（含所选老婆头像）。"""
    avatar_url = f"https://q4.qlogo.cn/headimg_dl?dst_uin={wife_id}&spec=640"
    text = f" 你挑选了【{wife_name}】作为你的今日老婆！❤️\n请好好对待她哦~"
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
        Comp.At(qq=user_id),
        Comp.Plain(text),
        Comp.Image.fromURL(avatar_url),
    ]
    yield event.chain_result(chain)


# 处理挑选期间的回复（人话：等着看你回数字/重新挑选/放弃）
async def handle_pick_response(plugin_instance, event: AstrMessageEvent):
    """处理挑选期间的回复：编号选择、重新挑选、放弃。"""
    if event.is_private_chat():
        return

    group_id = str(event.get_group_id())
    user_id = str(event.get_sender_id())
    msg = event.message_str.strip()

    _cleanup_expired_requests(group_id)
    req = pick_requests.get(group_id, {}).get(user_id)
    if not isinstance(req, PickRequest):
        return

    # 用户觉得这批都不行，重新抽一批（人话：没一个顺眼的，换个组相亲）
    if msg in RESELECT_KEYWORDS:
        if not _can_retry(plugin_instance, group_id, user_id):
            event.stop_event()
            yield event.plain_result(
                f"已达到候选批次上限（{_get_retry_limit(plugin_instance)}批），请从当前候选中选择或放弃。"
            )
            return

        count = _get_candidate_count(plugin_instance)
        candidates = await _draw_candidates(plugin_instance, event, user_id, count)
        if not candidates:
            _delete_request(group_id, user_id)
            event.stop_event()
            yield event.plain_result("老婆池为空，请稍后再试。")
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
        event.stop_event()
        yield event.plain_result(_format_candidate_list(new_req))
        return

    # 用户放弃挑选，把名额还回去（人话：这婚不结了，下次再说）
    if msg in ABANDON_KEYWORDS:
        used_retry_count = _get_used_retry_count(plugin_instance, group_id, user_id)
        _delete_request(group_id, user_id)
        event.stop_event()
        retry_limit = _get_retry_limit(plugin_instance)
        if retry_limit > 0 and used_retry_count >= retry_limit:
            yield event.plain_result(
                "已放弃本次挑选，今天的候选批次已用完；仍可使用「今日老婆」随机抽取。"
            )
        else:
            remaining_text = (
                "不限"
                if retry_limit <= 0
                else str(max(0, retry_limit - used_retry_count))
            )
            yield event.plain_result(
                f"已放弃本次挑选，本次不占每日抽取名额；剩余候选批次：{remaining_text}。"
            )
        return

    if not msg.isdigit():
        return

    # 用户回了个数字，校验一下再选中（人话：看看你选的是几号）

    index = int(msg)
    if not (1 <= index <= len(req.candidates)):
        event.stop_event()
        yield event.plain_result(f"请输入有效的编号（1-{len(req.candidates)}）哦~")
        return

    wife_id = req.candidates[index - 1]
    wife_name = req.candidate_names[index - 1]
    _delete_request(group_id, user_id)

    timestamp = datetime.now().isoformat()
    user_name = event.get_sender_name() or f"用户({user_id})"
    group_records = get_group_records(plugin_instance, group_id)
    group_records.append(
        {
            "user_id": user_id,
            "wife_id": wife_id,
            "wife_name": wife_name,
            "timestamp": timestamp,
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

    event.stop_event()
    async for result in _send_pick_confirmation(
        plugin_instance, event, user_id, wife_id, wife_name
    ):
        yield result
