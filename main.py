import asyncio
import json
import os
import random
import time
#from datetime import datetime
from datetime import datetime, timedelta

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

try:
    # 插件目录下的同级模块（推荐）。正常情况下 AstrBot 会将插件目录加入 sys.path。
    from keyword_trigger import KeywordRoute, KeywordRouter, MatchMode
except ModuleNotFoundError:
    # 兼容性兜底：某些部署方式可能只同步 main.py，或未将插件目录加入 sys.path，
    # 从而导致同级模块无法导入。为避免插件直接载入失败，这里提供最小实现作为 fallback。
    from dataclasses import dataclass
    from enum import Enum
    from typing import Iterable, Optional, Sequence

    class MatchMode(str, Enum):
        EXACT = "exact"
        STARTS_WITH = "starts_with"
        CONTAINS = "contains"

    @dataclass(frozen=True, slots=True)
    class KeywordRoute:
        keyword: str
        action: str

    class KeywordRouter:
        def __init__(self, routes: Sequence[KeywordRoute]):
            self._routes = list(routes)
            self._routes_by_keyword_len_desc = sorted(
                self._routes, key=lambda r: len(r.keyword), reverse=True
            )

        def match(self, message: str, *, mode: MatchMode) -> Optional[str]:
            text = message.strip()
            if not text:
                return None

            routes: Iterable[KeywordRoute] = self._routes
            if mode in (MatchMode.CONTAINS, MatchMode.STARTS_WITH):
                routes = self._routes_by_keyword_len_desc

            for route in routes:
                if self._matches(text, route.keyword, mode):
                    return route.action
            return None

        @staticmethod
        def _matches(text: str, keyword: str, mode: MatchMode) -> bool:
            if mode == MatchMode.EXACT:
                return text == keyword
            if mode == MatchMode.STARTS_WITH:
                return text.startswith(keyword)
            if mode == MatchMode.CONTAINS:
                return keyword in text
            raise ValueError(f"Unknown MatchMode: {mode}")

try:
    from onebot_api import extract_message_id
except ModuleNotFoundError:
    from typing import Any, Mapping

    def extract_message_id(resp: Any) -> Any:
        if not isinstance(resp, Mapping):
            return None
        if "message_id" in resp:
            return resp.get("message_id")
        data = resp.get("data")
        if isinstance(data, Mapping) and "message_id" in data:
            return data.get("message_id")
        return None

try:
    from waifu_relations import maybe_add_other_half_record
except ModuleNotFoundError:
    from typing import Any, MutableSequence

    def maybe_add_other_half_record(
        *,
        records: MutableSequence[dict[str, Any]],
        user_id: str,
        user_name: str,
        wife_id: str,
        wife_name: str,
        enabled: bool,
        timestamp: str,
    ) -> bool:
        if not enabled:
            return False
        if any(str(r.get("user_id")) == str(wife_id) for r in records):
            return False
        records.append(
            {
                "user_id": str(wife_id),
                "wife_id": str(user_id),
                "wife_name": str(user_name),
                "timestamp": timestamp,
                "auto_set": True,
                "auto_set_target_name": str(wife_name),
            }
        )
        return True


_DEFAULT_KEYWORD_ROUTES: tuple[KeywordRoute, ...] = (
    KeywordRoute(keyword="今日老婆", action="draw_wife"),
    KeywordRoute(keyword="抽老婆", action="draw_wife"),
    KeywordRoute(keyword="我的老婆", action="show_history"),
    KeywordRoute(keyword="抽取历史", action="show_history"),
    KeywordRoute(keyword="强娶", action="force_marry"),
    KeywordRoute(keyword="关系图", action="show_graph"),
    KeywordRoute(keyword="rbq排行", action="rbq_ranking"),
    KeywordRoute(keyword="抽老婆帮助", action="show_help"),
    KeywordRoute(keyword="老婆插件帮助", action="show_help"),
)

class RandomWifePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config

        self.curr_dir = os.path.dirname(__file__)

        self._withdraw_tasks: set[asyncio.Task] = set()
        
        # 数据存储相对路径
        self.data_dir = os.path.join(get_astrbot_plugin_data_path(), "random_wife")
        self.records_file = os.path.join(self.data_dir, "wife_records.json")
        self.active_file = os.path.join(self.data_dir, "active_users.json") 
        self.forced_file = os.path.join(self.data_dir, "forced_marriage.json")
        self.rbq_stats_file = os.path.join(self.data_dir, "rbq_stats.json")
        
        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir, exist_ok=True)
            
        self.records = self._load_json(self.records_file, {"date": "", "groups": {}})
        self.active_users = self._load_json(self.active_file, {})
        self.forced_records = self._load_json(self.forced_file, {})
        self.rbq_stats = self._load_json(self.rbq_stats_file, {})

        self._keyword_router = KeywordRouter(routes=_DEFAULT_KEYWORD_ROUTES)
        self._keyword_handlers = {
            "draw_wife": self._cmd_draw_wife,
            "show_history": self._cmd_show_history,
            "force_marry": self._cmd_force_marry,
            "show_graph": self._cmd_show_graph,
            "rbq_ranking": self.rbq_ranking,
            "show_help": self._cmd_show_help,
        }
        self._keyword_trigger_block_prefixes = ("/", "!", "！")
        logger.info(f"抽老婆插件已加载。数据目录: {self.data_dir}")

    def _clean_rbq_stats(self):
        """
        清理逻辑：
        1. 移除 30 天前的强娶时间戳记录。
        2. 若 30 天内次数为 0，直接删掉该用户。
        3. 如果用户不在 active_users（一个月没说话）：
           - 若次数 <= 4 且 距离最后一次发言已过 7 天，则删除。
           - 若次数 > 4，则保留。
        """
        now = time.time()
        thirty_days = 30 * 24 * 3600
        seven_days = 7 * 24 * 3600
        
        new_stats = {}
        for gid, users in self.rbq_stats.items():
            new_users = {}
            # 获取该群的活跃用户映射 {uid: last_ts}
            active_group = self.active_users.get(gid, {})
            
            for uid, timestamps in users.items():
                # 1. 只保留 30 天内的记录
                valid_ts = [ts for ts in timestamps if now - ts < thirty_days]
                count = len(valid_ts)
                
                # 2. 检查活跃状态删除规则
                is_in_active = uid in active_group
                last_active_ts = active_group.get(uid, 0)
                
                should_keep = True
                if count == 0:
                    should_keep = False
                elif not is_in_active: # 不在活跃列表（即超过1个月没说话）
                    # 如果次数不多(<=4) 且 距离最后一次说话已经超过7天
                    if count <= 4 and (now - last_active_ts > seven_days):
                        should_keep = False
                
                if should_keep:
                    new_users[uid] = valid_ts
            
            if new_users:
                new_stats[gid] = new_users
        
        self.rbq_stats = new_stats
        self._save_json(self.rbq_stats_file, self.rbq_stats)

    def _load_json(self, path: str, default: object):
        if not os.path.exists(path):
            return default
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default

    def _save_json(self, path: str, data: object):
        try:
            # === 全局记录总量清理逻辑 ===
            if path == self.records_file and "groups" in data:
                max_total = self.config.get("max_records", 500)
                all_recs = []
                # 展平所有记录
                for gid, gdata in data["groups"].items():
                    for r in gdata.get("records", []):
                        r["_gid"] = gid  # 临时记录所属群
                        all_recs.append(r)
                
                # 如果超过全局上限
                if len(all_recs) > max_total:
                    # 按时间戳排序（最早的在前面）
                    all_recs.sort(key=lambda x: x.get("timestamp", ""))
                    # 只保留最后的 max_total 条
                    keep_recs = all_recs[-max_total:]
                    
                    # 重新归类到各个群
                    new_groups = {}
                    for r in keep_recs:
                        gid = r.pop("_gid")
                        if gid not in new_groups:
                            new_groups[gid] = {"records": []}
                        new_groups[gid]["records"].append(r)
                    data["groups"] = new_groups

            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存数据失败: {e}")

    def _is_allowed_group(self, group_id: str) -> bool:
        whitelist = self.config.get("whitelist_groups", [])
        blacklist = self.config.get("blacklist_groups", [])
        group_id = str(group_id)

        if group_id in {str(g) for g in blacklist}:
            return False
        if whitelist and group_id not in {str(g) for g in whitelist}:
            return False
        return True

    def _ensure_today_records(self) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        if self.records.get("date") != today:
            self.records = {"date": today, "groups": {}}

    def _get_group_records(self, group_id: str) -> list[dict]:
        self._ensure_today_records()
        if group_id not in self.records["groups"]:
            self.records["groups"][group_id] = {"records": []}
        return self.records["groups"][group_id]["records"]

    def _auto_set_other_half_enabled(self) -> bool:
        return bool(self.config.get("auto_set_other_half", False))

    def _auto_withdraw_enabled(self) -> bool:
        return bool(self.config.get("auto_withdraw_enabled", False))

    def _auto_withdraw_delay_seconds(self) -> int:
        raw = self.config.get("auto_withdraw_delay_seconds", 5)
        try:
            delay = int(raw)
        except Exception:
            delay = 5
        return max(1, delay)

    def _can_onebot_withdraw(self, event: AstrMessageEvent) -> bool:
        return self._auto_withdraw_enabled() and event.get_platform_name() == "aiocqhttp"

    async def _send_onebot_message(
        self, event: AstrMessageEvent, *, message: list[dict]
    ) -> object:
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
            logger.warning(f"无法解析 send_*_msg 返回的 message_id: {resp!r}")
        return message_id

    def _schedule_onebot_delete_msg(self, client, *, message_id: object) -> None:
        delay = self._auto_withdraw_delay_seconds()

        async def _runner():
            await asyncio.sleep(delay)
            try:
                await client.api.call_action("delete_msg", message_id=message_id)
            except Exception as e:
                logger.warning(f"自动撤回失败: {e}")

        task = asyncio.create_task(_runner())
        self._withdraw_tasks.add(task)
        task.add_done_callback(self._withdraw_tasks.discard)

    @staticmethod
    def _resolve_member_name(
        members: list[dict], *, user_id: str, fallback: str
    ) -> str:
        for m in members:
            if str(m.get("user_id")) == str(user_id):
                return m.get("card") or m.get("nickname") or fallback
        return fallback

    def _record_active(self, event: AstrMessageEvent) -> None:
        group_id = event.get_group_id()
        if not group_id or not self._is_allowed_group(str(group_id)):
            return

        user_id, bot_id = str(event.get_sender_id()), str(event.get_self_id())
        if user_id == bot_id or user_id == "0":
            return

        group_key = str(group_id)
        if group_key not in self.active_users:
            self.active_users[group_key] = {}
        self.active_users[group_key][user_id] = time.time()
        self._save_json(self.active_file, self.active_users)

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def track_active(self, event: AstrMessageEvent):
        self._record_active(event)

    def _get_keyword_trigger_mode(self) -> MatchMode:
        raw = self.config.get("keyword_trigger_mode", MatchMode.EXACT.value)
        try:
            return MatchMode(str(raw))
        except ValueError:
            logger.warning(f"未知 keyword_trigger_mode={raw!r}，将回退为 exact")
            return MatchMode.EXACT

    def _should_ignore_keyword_trigger(self, message: str) -> bool:
        stripped = message.lstrip()
        return stripped.startswith(self._keyword_trigger_block_prefixes)

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def keyword_trigger(self, event: AstrMessageEvent):
        if not self.config.get("keyword_trigger_enabled", False):
            return

        group_id = event.get_group_id()
        if not group_id or not self._is_allowed_group(str(group_id)):
            return

        message_str = event.message_str
        if not message_str or self._should_ignore_keyword_trigger(message_str):
            return

        mode = self._get_keyword_trigger_mode()
        action = self._keyword_router.match(message_str, mode=mode)
        if not action:
            return

        # 由于 stop_event() 会阻止后续 handler 执行，这里手动记录一次活跃度，
        # 以避免仅通过“关键词指令”互动的群友永远不进入老婆池。
        self._record_active(event)

        handler = self._keyword_handlers.get(action)
        if handler is None:
            logger.warning(f"关键词路由命中未知 action={action!r}，已忽略。")
            return

        async for result in handler(event):
            yield result

        event.stop_event()

    def _cleanup_inactive(self, group_id: str):
        if group_id not in self.active_users:
            return
        now, limit = time.time(), 30 * 24 * 3600
        active_group = self.active_users[group_id]
        # 过滤过时数据和 ID 为 "0" 的数据
        new_active = {uid: ts for uid, ts in active_group.items() if (now - ts < limit) and uid != "0"}
        if len(active_group) != len(new_active):
            self.active_users[group_id] = new_active
            self._save_json(self.active_file, self.active_users)

    @filter.command("今日老婆", alias={"抽老婆"})
    async def draw_wife(self, event: AstrMessageEvent):
        async for result in self._cmd_draw_wife(event):
            yield result

    async def _cmd_draw_wife(self, event: AstrMessageEvent):
        if event.is_private_chat():
            yield event.plain_result("此功能仅在群聊中可用哦~")
            return

        group_id = str(event.get_group_id())
        if not self._is_allowed_group(group_id):
            return

        user_id, bot_id = str(event.get_sender_id()), str(event.get_self_id())
        self._cleanup_inactive(group_id)

        daily_limit = self.config.get("daily_limit", 1)
        group_records = self._get_group_records(group_id)
        user_recs = [r for r in group_records if r["user_id"] == user_id]
        today_count = len(user_recs)

        if today_count >= daily_limit:
            if daily_limit == 1:
                wife_record = user_recs[0]
                wife_name, wife_id = wife_record["wife_name"], wife_record["wife_id"]
                wife_avatar = (
                    f"https://q4.qlogo.cn/headimg_dl?dst_uin={wife_id}&spec=640"
                )
                if self._can_onebot_withdraw(event):
                    message_id = await self._send_onebot_message(
                        event,
                        message=[
                            {"type": "at", "data": {"qq": user_id}},
                            {
                                "type": "text",
                                "data": {
                                    "text": f" 你今天已经有老婆了哦❤️~\n她是：【{wife_name}】\n"
                                },
                            },
                            {"type": "image", "data": {"file": wife_avatar}},
                        ],
                    )
                    if message_id is not None:
                        self._schedule_onebot_delete_msg(event.bot, message_id=message_id)
                    return

                chain = [
                    Comp.At(qq=user_id),
                    Comp.Plain(f" 你今天已经有老婆了哦❤️~\n她是：【{wife_name}】\n"),
                    Comp.Image.fromURL(wife_avatar),
                ]
                yield event.chain_result(chain)
            else:
                text = f"你今天已经抽了{today_count}次老婆了，明天再来吧！"
                if self._can_onebot_withdraw(event):
                    message_id = await self._send_onebot_message(
                        event, message=[{"type": "text", "data": {"text": text}}]
                    )
                    if message_id is not None:
                        self._schedule_onebot_delete_msg(event.bot, message_id=message_id)
                    return

                yield event.plain_result(text)
            return

        # --- 增强：获取最新的群成员列表以过滤退群者 ---
        current_member_ids: list[str] = []
        members = []
        try:
            if event.get_platform_name() == "aiocqhttp":
                assert isinstance(event, AiocqhttpMessageEvent)
                members = await event.bot.api.call_action(
                    "get_group_member_list", group_id=int(group_id)
                )
                if (
                    isinstance(members, dict)
                    and "data" in members
                    and isinstance(members["data"], list)
                ):
                    members = members["data"]
                current_member_ids = [str(m.get("user_id")) for m in members]
        except Exception as e:
            logger.error(f"获取群成员列表失败，将使用缓存池: {e}")

        active_pool = self.active_users.get(group_id, {})
        excluded = {str(uid) for uid in self.config.get("excluded_users", [])}
        excluded.update([bot_id, user_id, "0"])

        # 核心逻辑：如果在 aiocqhttp 平台，只从【当前还在群里】的人中抽取
        if current_member_ids:
            pool = [
                uid
                for uid in active_pool.keys()
                if uid not in excluded and uid in current_member_ids
            ]

            # 同时顺便清理一下 active_users，把不在群里的人删掉
            removed_uids = [
                uid for uid in active_pool.keys() if uid not in current_member_ids
            ]
            if removed_uids:
                for r_uid in removed_uids:
                    del self.active_users[group_id][r_uid]
                self._save_json(self.active_file, self.active_users)
        else:
            pool = [uid for uid in active_pool.keys() if uid not in excluded]

        if not pool:
            yield event.plain_result("老婆池为空（需有人在30天内发言）。")
            return

        wife_id = random.choice(pool)
        wife_name = f"用户({wife_id})"
        user_name = event.get_sender_name() or f"用户({user_id})"

        try:
            if event.get_platform_name() == "aiocqhttp":
                wife_name = self._resolve_member_name(
                    members, user_id=wife_id, fallback=wife_name
                )
                user_name = self._resolve_member_name(
                    members, user_id=user_id, fallback=user_name
                )
        except Exception:
            pass

        timestamp = datetime.now().isoformat()
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
            enabled=self._auto_set_other_half_enabled(),
            timestamp=timestamp,
        )

        self._save_json(self.records_file, self.records)

        avatar_url = f"https://q4.qlogo.cn/headimg_dl?dst_uin={wife_id}&spec=640"
        suffix_text = (
            "\n请好好对待她哦❤️~ \n"
            f"剩余抽取次数：{max(0, daily_limit - today_count - 1)}次"
        )
        if self._can_onebot_withdraw(event):
            message_id = await self._send_onebot_message(
                event,
                message=[
                    {"type": "at", "data": {"qq": user_id}},
                    {
                        "type": "text",
                        "data": {"text": f" 你的今日老婆是：\n\n【{wife_name}】\n"},
                    },
                    {"type": "image", "data": {"file": avatar_url}},
                    {"type": "text", "data": {"text": suffix_text}},
                ],
            )
            if message_id is not None:
                self._schedule_onebot_delete_msg(event.bot, message_id=message_id)
            return

        chain = [
            Comp.At(qq=user_id),
            Comp.Plain(f" 你的今日老婆是：\n\n【{wife_name}】\n"),
            Comp.Image.fromURL(avatar_url),
            Comp.Plain(suffix_text),
        ]
        yield event.chain_result(chain)

    @filter.command("我的老婆", alias={"抽取历史"})
    async def show_history(self, event: AstrMessageEvent):
        async for result in self._cmd_show_history(event):
            yield result

    async def _cmd_show_history(self, event: AstrMessageEvent):
        group_id = str(event.get_group_id())
        if not self._is_allowed_group(group_id):
            return

        user_id = str(event.get_sender_id())
        today = datetime.now().strftime("%Y-%m-%d")
        if self.records.get("date") != today:
            yield event.plain_result("你今天还没有抽过老婆哦~")
            return

        group_recs = self.records.get("groups", {}).get(group_id, {}).get("records", [])
        user_recs = [r for r in group_recs if r["user_id"] == user_id]
        if not user_recs:
            yield event.plain_result("你今天还没有抽过老婆哦~")
            return

        daily_limit = self.config.get("daily_limit", 3)
        res = [f"🌸 你今日的老婆记录 ({len(user_recs)}/{daily_limit})："]
        for i, r in enumerate(user_recs, 1):
            time_str = datetime.fromisoformat(r["timestamp"]).strftime("%H:%M")
            res.append(f"{i}. 【{r['wife_name']}】 ({time_str})")
        res.append(f"\n剩余次数：{max(0, daily_limit - len(user_recs))}次")
        yield event.plain_result("\n".join(res))

    @filter.command("强娶")
    async def force_marry(self, event: AstrMessageEvent):
        async for result in self._cmd_force_marry(event):
            yield result

    async def _cmd_force_marry(self, event: AstrMessageEvent):
        """强娶 + @要娶的那个人"""
        if event.is_private_chat():
            yield event.plain_result("此功能仅在群聊中可用哦~")
            return

        user_id = str(event.get_sender_id())
        group_id = str(event.get_group_id())
        if not self._is_allowed_group(group_id):
            return

        now = time.time()
        now_dt = datetime.now()
        
        # 获取上次强娶的时间戳和日期
        last_time = self.forced_records[group_id].get(user_id, 0)
        last_dt = datetime.fromtimestamp(last_time)
        
        # 从配置读取 CD 天数
        cd_days = self.config.get("force_marry_cd", 3)

        # --- 核心逻辑：计算目标重置日期 ---
        # 逻辑是：取上次强娶那一天的 00:00，加上 cd_days 天。
        # 比如 2.6 16:00 强娶，CD 3天，重置时间就是 2.6 00:00 + 3天 = 2.9 00:00
        last_midnight = datetime.combine(last_dt.date(), datetime.min.time())
        target_reset_dt = last_midnight + timedelta(days=cd_days)
        target_reset_ts = target_reset_dt.timestamp()

        # 计算距离目标重置时刻还剩多少秒
        remaining = target_reset_ts - now

        if remaining > 0:
            # 这里的计算会非常符合直觉：
            # 只要没到那天的 00:00，就会显示剩余的天/时/分
            days = int(remaining // 86400)
            hours = int((remaining % 86400) // 3600)
            mins = int((remaining % 3600) // 60)
            
            yield event.plain_result(
                f"你已经强娶过啦！\n请等待：{days}天{hours}小时{mins}分后再试。\n"
                f"(重置时间：{target_reset_dt.strftime('%m-%d %H:%M')})"
            )
            return

        # 获取目标
        target_id = None
        for component in event.message_obj.message:
            if isinstance(component, Comp.At):
                target_id = str(component.qq)
                break

        if not target_id or target_id == "all":
            yield event.plain_result("请 @ 一个你想强娶的人。")
            return

        if target_id == user_id:
            yield event.plain_result("不能娶自己！")
            return

        # 获取名字
        target_name = f"用户({target_id})"
        user_name = event.get_sender_name() or f"用户({user_id})"
        members = []
        try:
            if event.get_platform_name() == "aiocqhttp":
                assert isinstance(event, AiocqhttpMessageEvent)
                members = await event.bot.api.call_action(
                    "get_group_member_list", group_id=int(group_id)
                )
                if (
                    isinstance(members, dict)
                    and "data" in members
                    and isinstance(members["data"], list)
                ):
                    members = members["data"]

                target_name = self._resolve_member_name(
                    members, user_id=target_id, fallback=target_name
                )
                user_name = self._resolve_member_name(
                    members, user_id=user_id, fallback=user_name
                )
        except Exception:
            pass

        group_records = self._get_group_records(group_id)

        # 记录被强娶者的信息（rbq 统计）
        if group_id not in self.rbq_stats:
            self.rbq_stats[group_id] = {}
        if target_id not in self.rbq_stats[group_id]:
            self.rbq_stats[group_id][target_id] = []

        self.rbq_stats[group_id][target_id].append(time.time())
        self._clean_rbq_stats()  # 记录时顺便清理
        self._save_json(self.rbq_stats_file, self.rbq_stats)

        # 移除该群该用户今日的其他老婆记录
        group_records[:] = [r for r in group_records if r["user_id"] != user_id]

        # 插入强娶记录
        timestamp = datetime.now().isoformat()
        group_records.append(
            {
                "user_id": user_id,
                "wife_id": target_id,
                "wife_name": target_name,
                "timestamp": timestamp,
                "forced": True,
            }
        )

        maybe_add_other_half_record(
            records=group_records,
            user_id=user_id,
            user_name=user_name,
            wife_id=target_id,
            wife_name=target_name,
            enabled=self._auto_set_other_half_enabled(),
            timestamp=timestamp,
        )

        # --- 更新该群的强娶冷却时间 ---
        self.forced_records[group_id][user_id] = now

        self._save_json(self.records_file, self.records)
        self._save_json(self.forced_file, self.forced_records)

        avatar_url = f"https://q4.qlogo.cn/headimg_dl?dst_uin={target_id}&spec=640"
        text = f" 你今天强娶了【{target_name}】哦❤️~\n请对她好一点哦~。\n"
        if self._can_onebot_withdraw(event):
            message_id = await self._send_onebot_message(
                event,
                message=[
                    {"type": "at", "data": {"qq": user_id}},
                    {"type": "text", "data": {"text": text}},
                    {"type": "image", "data": {"file": avatar_url}},
                ],
            )
            if message_id is not None:
                self._schedule_onebot_delete_msg(event.bot, message_id=message_id)
            return

        chain = [
            Comp.At(qq=user_id),
            Comp.Plain(text),
            Comp.Image.fromURL(avatar_url),
        ]
        yield event.chain_result(chain)

    @filter.command("关系图")
    async def show_graph(self, event: AstrMessageEvent):
        async for result in self._cmd_show_graph(event):
            yield result

    async def _cmd_show_graph(self, event: AstrMessageEvent):
        group_id = str(event.get_group_id())
        if not self._is_allowed_group(group_id):
            return

        iter_count = self.config.get("iterations", 150)

        # --- 新增：读取 JS 文件内容 ---
        vis_js_path = os.path.join(self.curr_dir, "vis-network.min.js")
        vis_js_content = ""
        if os.path.exists(vis_js_path):
            with open(vis_js_path, "r", encoding="utf-8") as f:
                vis_js_content = f.read()
        else:
            logger.error(f"找不到 JS 文件: {vis_js_path}")
        # ---------------------------

        # 1. 读取模板文件内容
        template_path = os.path.join(self.curr_dir, "graph_template.html")
        if not os.path.exists(template_path):
            yield event.plain_result(f"错误：找不到模板文件 {template_path}")
            return

        with open(template_path, "r", encoding="utf-8") as f:
            graph_html = f.read()

        # 2. 获取数据 (假设你已经从 self.records 获取了 group_data)
        group_data = self.records.get("groups", {}).get(group_id, {}).get("records", [])

        group_name = "未命名群聊"
        user_map = {}
        try:
            if event.get_platform_name() == "aiocqhttp":
                # 获取群信息
                info = await event.bot.api.call_action(
                    "get_group_info", group_id=int(group_id)
                )
                if isinstance(info, dict) and "data" in info and isinstance(info["data"], dict):
                    info = info["data"]
                group_name = info.get("group_name", "未命名群聊")

                # 获取群成员列表构建映射
                members = await event.bot.api.call_action(
                    "get_group_member_list", group_id=int(group_id)
                )
                if isinstance(members, dict) and "data" in members and isinstance(members["data"], list):
                    members = members["data"]

                if isinstance(members, list):
                    for m in members:
                        uid = str(m.get("user_id"))
                        name = m.get("card") or m.get("nickname") or uid
                        user_map[uid] = name

        except Exception as e:
            logger.warning(f"获取群信息失败: {e}")

        # 3. 渲染图片
        # 根据节点数量动态计算高度，避免拥挤
        # 动态计算你想要裁剪的区域大小
        unique_nodes = set()
        for r in group_data:
            unique_nodes.add(str(r.get("user_id")))
            unique_nodes.add(str(r.get("wife_id")))
        node_count = len(unique_nodes)

        # 假设我们想要从左上角 (0,0) 开始，裁剪一个动态高度的区域
        clip_width = 1920
        clip_height = 1080 + (max(0, node_count - 10) * 60)

        try:
            url = await self.html_render(
                graph_html,
                {
                    "vis_js_content": vis_js_content,
                    "group_id": group_id,
                    "group_name": group_name,
                    "user_map": user_map,
                    "records": group_data,
                    "iterations": iter_count,
                },
                options={
                    "type": "jpeg",
                    "quality": 100,
                    "device_scale_factor": 2,
                    "scale": "device",
                    # 必须传齐这四个参数，且必须是 int 或 float，不能是字符串
                    "clip": {
                        "x": 0,
                        "y": 0,
                        "width": clip_width,
                        "height": clip_height,
                    },
                    # 注意：使用 clip 时通常建议将 full_page 设为 False
                    "full_page": False,
                    "device_scale_factor_level": "ultra",
                },
            )
            yield event.image_result(url)
        except Exception as e:
            logger.error(f"渲染失败: {e}")

    @filter.command("rbq排行")
    async def rbq_ranking(self, event: AstrMessageEvent):
        if event.is_private_chat():
            yield event.plain_result("私聊看不了榜单哦~")
            return
            
        group_id = str(event.get_group_id())
        self._clean_rbq_stats() # 渲染前强制清理一次过期数据
        
        group_data = self.rbq_stats.get(group_id, {})
        if not group_data:
            yield event.plain_result("本群近30天还没有人被强娶过，大家都很有礼貌呢。")
            return

        # 获取群成员名字映射 (仿照关系图逻辑)
        user_map = {}
        try:
            if event.get_platform_name() == "aiocqhttp":
                members = await event.bot.api.call_action('get_group_member_list', group_id=int(group_id))
                for m in members:
                    uid = str(m.get("user_id"))
                    user_map[uid] = m.get("card") or m.get("nickname") or uid
        except: pass

        # 构造排序数据
        sorted_list = []
        for uid, ts_list in group_data.items():
            sorted_list.append({
                "uid": uid,
                "name": user_map.get(uid, f"用户({uid})"),
                "count": len(ts_list)
            })
        
        # 按次数从大到小排，取前10
        sorted_list.sort(key=lambda x: x["count"], reverse=True)
        top_10 = sorted_list[:10]

        current_rank = 1
        for i, user in enumerate(top_10):
            if i > 0 and user["count"] < top_10[i-1]["count"]:
                current_rank = i + 1  # 排名跳跃到当前位置
            user["rank"] = current_rank

        # 读取新模板
        template_path = os.path.join(self.curr_dir, "rbq_ranking.html")
        if not os.path.exists(template_path):
            yield event.plain_result("错误：找不到排行模板 rbq_ranking.html")
            return
            
        with open(template_path, "r", encoding="utf-8") as f:
            template_content = f.read()

        try:
            # 计算数据行数，动态调整高度（10人大约550px就够了）
            #dynamic_height = 160 + (len(top_10) * 85) 
            
            header_h = 100 
            item_h = 60 
            footer_h = 50

            dynamic_height = header_h + (len(top_10) * item_h) + footer_h
            # 渲染图片
            url = await self.html_render(template_content, {
                "group_id": group_id,
                "ranking": top_10,
                "title": "❤️ 群rbq月榜 ❤️"
            }, 
            options={
                "type": "jpeg",
                "quality": 100,
                "full_page": False, # 关闭全页面，配合 clip 使用
                "clip": {
                    "x": 0,
                    "y": 0,
                    "width": 400,  # 这里的宽度就是你想要的图片宽度
                    "height": dynamic_height # 裁切的高度
                },
                "scale": "device",
                "device_scale_factor_level": "ultra"
            }
            )
            yield event.image_result(url)
        except Exception as e:
            logger.error(f"渲染RBQ排行失败: {e}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("重置记录")
    async def reset_records(self, event: AstrMessageEvent):
        self.records = {"date": datetime.now().strftime("%Y-%m-%d"), "groups": {}}
        self._save_json(self.records_file, self.records)
        yield event.plain_result("今日抽取记录已重置！")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("重置强娶时间")
    async def reset_force_cd(self, event: AstrMessageEvent):
        group_id = str(event.get_group_id())
        
        # 逻辑：删除 forced_records 中当前群的数据
        if hasattr(self, 'forced_records') and group_id in self.forced_records:
            # 清空该群所有人的 CD 记录
            self.forced_records[group_id] = {} 
            
            # 保存到 forced_marriage.json
            self._save_json(self.forced_file, self.forced_records)
            
            logger.info(f"[Wife] 已重置群 {group_id} 的强娶冷却时间")
            yield event.plain_result("✅ 本群强娶冷却时间已重置！现在大家可以再次强娶了。")
        else:
            yield event.plain_result("💡 本群目前没有人在冷却期内。")

    @filter.command("抽老婆帮助", alias={"老婆插件帮助"})
    async def show_help(self, event: AstrMessageEvent):
        async for result in self._cmd_show_help(event):
            yield result

    async def _cmd_show_help(self, event: AstrMessageEvent):
        if not self._is_allowed_group(str(event.get_group_id())):
            return
        daily_limit = self.config.get("daily_limit", 3)
        help_text = (
            "===== 🌸 抽老婆帮助 =====\n"
            "1. 【抽老婆】：随机抽取今日老婆\n"
            "2. 【强娶 @某人】：强行更换今日老婆（有冷却期）\n"
            "3. 【我的老婆】：查看今日历史与次数\n"
            "4. 【重置记录】：(管理员) 清空数据（强娶记录不会清除）\n"
            "5. 【关系图】：查看群友老婆的关系\n"
            "6. 【rbq排行】：展示近30天被强娶的次数排行\n"
            f"当前每日上限：{daily_limit}次\n"
            "提示：可在配置开启“关键词触发”，直接发送关键词无需 / 前缀。\n"
            "提示：可在配置开启“自动设置对方老婆 / 定时自动撤回”。\n"
            "注：仅限30天内发言且当前在群的活跃群友。"
        )
        yield event.plain_result(help_text)

    @filter.command("debug_graph")
    async def debug_graph(self, event: AstrMessageEvent):
        '''
        调试关系图渲染
        '''
        # Mock Data
        mock_records = [
            {"user_id": "1001", "wife_id": "1002", "wife_name": "User B", "forced": False},
            {"user_id": "1002", "wife_id": "1003", "wife_name": "User C", "forced": True},
            {"user_id": "1003", "wife_id": "1001", "wife_name": "User A", "forced": False},
            {"user_id": "1004", "wife_id": "1005", "wife_name": "User E", "forced": False},
            {"user_id": "1005", "wife_id": "1004", "wife_name": "User D", "forced": True},
            {"user_id": "1006", "wife_id": "1007", "wife_name": "User F", "forced": False},
            {"user_id": "1007", "wife_id": "1006", "wife_name": "User G", "forced": True},
            {"user_id": "1008", "wife_id": "1006", "wife_name": "User G", "forced": True},
            {"user_id": "1009", "wife_id": "1006", "wife_name": "User G", "forced": True},
            {"user_id": "1010", "wife_id": "1006", "wife_name": "User G", "forced": True},
            {"user_id": "1011", "wife_id": "1006", "wife_name": "User G", "forced": True},
            {"user_id": "1012", "wife_id": "1011", "wife_name": "User G", "forced": True},
            {"user_id": "1013", "wife_id": "1012", "wife_name": "User G", "forced": True},
            {"user_id": "1014", "wife_id": "1013", "wife_name": "User G", "forced": True},
            {"user_id": "1015", "wife_id": "1014", "wife_name": "User G", "forced": True},
            {"user_id": "1016", "wife_id": "1015", "wife_name": "User G", "forced": True},
            {"user_id": "1017", "wife_id": "1016", "wife_name": "User G", "forced": True},
            {"user_id": "1018", "wife_id": "1009", "wife_name": "User G", "forced": True},
            {"user_id": "1019", "wife_id": "1006", "wife_name": "User G", "forced": True},
            {"user_id": "1020", "wife_id": "1010", "wife_name": "User G", "forced": True},
            {"user_id": "1021", "wife_id": "1011", "wife_name": "User G", "forced": True},
            {"user_id": "1022", "wife_id": "1012", "wife_name": "User G", "forced": True},
            {"user_id": "1023", "wife_id": "1013", "wife_name": "User G", "forced": True},
            {"user_id": "1024", "wife_id": "1014", "wife_name": "User G", "forced": True},
            {"user_id": "1025", "wife_id": "1015", "wife_name": "User G", "forced": True},
            {"user_id": "1026", "wife_id": "1016", "wife_name": "User G", "forced": True},
            {"user_id": "1027", "wife_id": "1010", "wife_name": "User G", "forced": True},


        ]

        mock_user_map = {
            "1001": "Alice (1001)",
            "1002": "Bob (1002)", 
            "1003": "Charlie (1003)",
            "1004": "David (1004)",
            "1005": "Eve (1005)",
            "1006": "Frank (1006)",
            "1007": "Grace (1007)",
            "1008": "Hank (1008)",
            "1009": "Ivy (1009)",
            "1010": "Jack (1010)",
            "1011": "Jill (1011)",
            "1012": "John (1012)",
            "1013": "Julia (1013)",
            "1014": "Juliet (1014)",
            "1015": "Justin (1015)",
            "1016": "Katie (1016)",
            "1017": "Kevin (1017)",
            "1018": "Katie (1018)",
            "1019": "Katie (1019)",
            "1020": "Katie (1020)",
            "1021": "Kaie (1021)",
            "1022": "Katie (1022)",
            "1023": "Katie (1023)",
            "1024": "Katie (1024)",
            "1025": "Katie (1025)",
            "1026": "Katie (1026)",
            "1027": "Katie (1027)",
        }

        # 1. Save HTML for inspection
        with open(os.path.join(self.curr_dir, "graph_template.html"), "r", encoding="utf-8") as f:
            template_content = f.read()

        import jinja2
        env = jinja2.Environment()
        template = env.from_string(template_content)
        html_content = template.render(
            group_name="Debug Group",
            records=mock_records,
            user_map=mock_user_map,
            iterations=1000 # Debug default to strict
        )
        
        debug_html_path = os.path.join(self.curr_dir, "debug_output.html")
        with open(debug_html_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        
        yield event.plain_result(f"Debugging... HTML saved to {debug_html_path}")

        # 2. Render Image using AstrBot internal API
        # Calculate dynamic height based on node count to prevent overcrowding
        unique_nodes = set()
        for r in mock_records:
            unique_nodes.add(str(r.get("user_id")))
            unique_nodes.add(str(r.get("wife_id")))
        node_count = len(unique_nodes)
        
        # Base height 1080, add 60px for every node above 10
        view_height = 1080
        if node_count > 10:
            view_height = 1080 + (node_count - 10) * 60

        try:
            url = await self.html_render(template_content, {
                "group_name": "Debug Group",
                "records": mock_records,
                "user_map": mock_user_map,
                "iterations": 1000
            }, options={
                "viewport": {"width": 1920, "height": view_height},
                "device_scale_factor": 2,
                "type": "jpeg",
                "quality": 100,
                "device_scale_factor_level": "ultra",
            })
            yield event.image_result(url)
        except Exception as e:
            logger.error(f"Debug render failed: {e}")
            yield event.plain_result(f"Render failed: {e}")

    async def terminate(self):
        self._save_json(self.records_file, self.records)
        self._save_json(self.active_file, self.active_users)
        self._save_json(self.forced_file, self.forced_records)
        self._save_json(self.rbq_stats_file, self.rbq_stats)

        # 取消尚未执行的撤回任务，避免插件卸载后仍调用协议端。
        for task in tuple(self._withdraw_tasks):
            task.cancel()
        self._withdraw_tasks.clear()
