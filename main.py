import asyncio
import json
import os
import random
import re
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
from astrbot.core.star.filter.permission import PermissionTypeFilter
from astrbot.core.star.star_handler import star_handlers_registry
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

from .keyword_trigger import KeywordRoute, KeywordRouter, MatchMode, PermissionLevel
from .onebot_api import extract_message_id
from .waifu_relations import maybe_add_other_half_record

from .src.constants import _DEFAULT_KEYWORD_ROUTES
from .src.utils import (
    load_json, 
    save_json, 
    normalize_user_id_set, 
    extract_target_id_from_message,
    is_allowed_group,           # 新增
    resolve_member_name,        # 新增
)

from .src.debug_utils import run_debug_graph
# 新增：导入 core helpers
from .src.core import (
    send_onebot_message,
    schedule_onebot_delete_msg,
    record_active,
    clean_rbq_stats,
    draw_excluded_users,
    force_marry_excluded_users,
    ensure_today_records,
    get_group_records,
    auto_set_other_half_enabled,
    auto_withdraw_enabled,
    auto_withdraw_delay_seconds,
    can_onebot_withdraw,
    cleanup_inactive,
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
            
        self.records = load_json(self.records_file, {"date": "", "groups": {}})
        self.active_users = load_json(self.active_file, {})
        self.forced_records = load_json(self.forced_file, {})
        self.rbq_stats = load_json(self.rbq_stats_file, {})

        self._keyword_router = KeywordRouter(routes=_DEFAULT_KEYWORD_ROUTES)
        self._keyword_handlers = {
            "draw_wife": self._cmd_draw_wife,
            "show_history": self._cmd_show_history,
            "force_marry": self._cmd_force_marry,
            "show_graph": self._cmd_show_graph,
            "rbq_ranking": self.rbq_ranking,
            "show_help": self._cmd_show_help,
            "reset_records": self._cmd_reset_records,
            "reset_force_cd": self._cmd_reset_force_cd,
        }
        self._keyword_action_to_command_handler = {
            "draw_wife": "draw_wife",
            "show_history": "show_history",
            "force_marry": "force_marry",
            "show_graph": "show_graph",
            "rbq_ranking": "rbq_ranking",
            "show_help": "show_help",
            "reset_records": "reset_records",
            "reset_force_cd": "reset_force_cd",
        }
        self._keyword_trigger_block_prefixes = ("/", "!", "！")
        logger.info(f"抽老婆插件已加载。数据目录: {self.data_dir}")

    def _get_keyword_trigger_mode(self) -> MatchMode:
        """从配置中获取匹配模式，默认为包含匹配"""
        # 这里的 config.get 会读取插件配置，建议在控制面板设置里加上这个 key
        raw = self.config.get("keyword_trigger_mode", "contains")
        try:
            return MatchMode(str(raw))
        except ValueError:
            return MatchMode.CONTAINS

    def _clean_rbq_stats(self):
        return clean_rbq_stats(self)

    def _draw_excluded_users(self) -> set[str]:
        return draw_excluded_users(self)

    def _force_marry_excluded_users(self) -> set[str]:
        return force_marry_excluded_users(self)

    def _ensure_today_records(self) -> None:
        return ensure_today_records(self)

    def _get_group_records(self, group_id: str) -> list[dict]:
        return get_group_records(self, group_id)

    def _auto_set_other_half_enabled(self) -> bool:
        return auto_set_other_half_enabled(self)

    def _auto_withdraw_enabled(self) -> bool:
        return auto_withdraw_enabled(self)

    def _auto_withdraw_delay_seconds(self) -> int:
        return auto_withdraw_delay_seconds(self)

    def _can_onebot_withdraw(self, event: AstrMessageEvent) -> bool:
        return can_onebot_withdraw(self, event)

    async def _send_onebot_message(
        self, event: AstrMessageEvent, *, message: list[dict]
    ) -> object:
        return await send_onebot_message(self, event, message=message)

    def _schedule_onebot_delete_msg(self, client, *, message_id: object) -> None:
        return schedule_onebot_delete_msg(self, client, message_id=message_id)

    def _record_active(self, event: AstrMessageEvent) -> None:
        return record_active(self, event)

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def keyword_trigger(self, event: AstrMessageEvent):
        # 1. 检查开关
        if not self.config.get("keyword_trigger_enabled", False):
            return

        message_str = event.message_str
        if not message_str: return

        # 2. @bot / 唤醒前缀场景下跳过，交给 @filter.command 处理。
        #    原因：WakingCheckStage 会把 keyword_trigger（EventMessageTypeFilter 不检查
        #    is_at_or_wake_command）和对应的 CommandFilter handler 同时加入
        #    activated_handlers；而 StarRequestSubStage 在每个 handler 执行后调用
        #    event.clear_result() 会清掉 stop_event() 的标志，导致两个 handler
        #    依次执行造成双重触发。
        if event.is_at_or_wake_command:
            return

        # 3. 如果消息本身就带了 / 或 !，说明是正规指令，交给 @filter.command 去处理
        if message_str.startswith(self._keyword_trigger_block_prefixes):
            return
        # 3. 开始匹配关键词（例如：今日老婆）
        mode = self._get_keyword_trigger_mode()
        route = self._keyword_router.match_route(message_str, mode=mode)
        # 兼容模式：如果没有精准匹配，尝试命令式匹配
        if route is None:
            route = self._keyword_router.match_command_route(message_str)
        if route:
            # 记录活跃（既然说话了就要进池子）
            self._record_active(event)
            # 找到对应的函数，比如 _cmd_draw_wife
            handler = self._keyword_handlers.get(route.action)
            if handler:
                # 核心：手动运行你的函数并获取结果
                async for result in handler(event):
                    yield result
                
                # 处理完了，停止事件，防止再触发别的
                event.stop_event()
   
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def track_active(self, event: AstrMessageEvent):
        self._record_active(event)

    def _cleanup_inactive(self, group_id: str):
        return cleanup_inactive(self, group_id)

    @filter.command("今日老婆", alias={"抽老婆"})
    async def draw_wife(self, event: AstrMessageEvent):
        async for result in self._cmd_draw_wife(event):
            yield result

    async def _cmd_draw_wife(self, event: AstrMessageEvent):
        # 清理完不在群的人后
        
        if event.is_private_chat():
            yield event.plain_result("此功能仅在群聊中可用哦~")
            return

        group_id = str(event.get_group_id())
        save_json(self.active_file, self.active_users, self.active_file, self.config)
        if not is_allowed_group(group_id, self.config):
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
        excluded = self._draw_excluded_users()
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
                save_json(self.active_file, self.active_users)
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
                wife_name = resolve_member_name(
                    members, user_id=wife_id, fallback=wife_name
                )
                user_name = resolve_member_name(
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

        save_json(self.records_file, self.records, self.records_file, self.config)

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
        if not is_allowed_group(group_id, self.config):
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
        bot_id = str(event.get_self_id())
        group_id = str(event.get_group_id())
        if not is_allowed_group(group_id, self.config):
            return

        now = time.time()
        
        # 获取上次强娶的时间戳和日期
        last_time = self.forced_records.setdefault(group_id, {}).get(user_id, 0)
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

        target_id = extract_target_id_from_message(event)

        if not target_id or target_id == "all":
            yield event.plain_result("请 @ 一个你想强娶的人。")
            return

        if target_id == user_id:
            yield event.plain_result("不能娶自己！")
            return

        force_excluded = self._force_marry_excluded_users()
        force_excluded.update({bot_id, "0"})
        if target_id in force_excluded:
            yield event.plain_result("该用户在强娶排除列表中，无法被强娶。")
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

                target_name = resolve_member_name(
                    members, user_id=target_id, fallback=target_name
                )
                user_name = resolve_member_name(
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
        save_json(self.rbq_stats_file, self.rbq_stats)

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

        save_json(self.records_file, self.records)
        save_json(self.forced_file, self.forced_records)

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
        if not is_allowed_group(group_id, self.config):
            return

        iter_count = self.config.get("iterations", 140)

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
                    "type": "png",
                    "quality": None,
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
        except Exception:
            pass

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
            rank_width = 400

            dynamic_height = header_h + (len(top_10) * item_h) + footer_h
            # 渲染图片
            url = await self.html_render(template_content, {
                "group_id": group_id,
                "ranking": top_10,
                "title": "❤️ 群rbq月榜 ❤️"
            }, 
            options={
                "type": "png",
                "quality": None,
                "full_page": False, # 关闭全页面，配合 clip 使用
                "clip": {
                    "x": 0,
                    "y": 0,
                    "width": rank_width,
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
        async for result in self._cmd_reset_records(event):
            yield result

    async def _cmd_reset_records(self, event: AstrMessageEvent):
        self.records = {"date": datetime.now().strftime("%Y-%m-%d"), "groups": {}}
        save_json(self.records_file, self.records)
        yield event.plain_result("今日抽取记录已重置！")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("重置强娶时间")
    async def reset_force_cd(self, event: AstrMessageEvent):
        async for result in self._cmd_reset_force_cd(event):
            yield result

    async def _cmd_reset_force_cd(self, event: AstrMessageEvent):
        group_id = str(event.get_group_id())

        if hasattr(self, "forced_records") and group_id in self.forced_records:
            self.forced_records[group_id] = {}
            save_json(self.forced_file, self.forced_records)

            logger.info(f"[Wife] 已重置群 {group_id} 的强娶冷却时间")
            yield event.plain_result("✅ 本群强娶冷却时间已重置！现在大家可以再次强娶了。")
        else:
            yield event.plain_result("💡 本群目前没有人在冷却期内。")

    @filter.command("抽老婆帮助", alias={"老婆插件帮助"})
    async def show_help(self, event: AstrMessageEvent):
        async for result in self._cmd_show_help(event):
            yield result

    async def _cmd_show_help(self, event: AstrMessageEvent):
        if not is_allowed_group(str(event.get_group_id()), self.config):
            return
        daily_limit = self.config.get("daily_limit", 3)
        help_text = (
            "===== 🌸 抽老婆帮助 =====\n"
            "1. 【抽老婆】：随机抽取今日老婆\n"
            "2. 【强娶@某人】或【强娶 @某人】：强行更换今日老婆（有冷却期）\n"
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
        # 直接调用外部函数，将 self (插件实例) 和 event 传进去
        async for result in run_debug_graph(self, event):
            yield result

    async def terminate(self):
        save_json(self.records_file, self.records)
        save_json(self.active_file, self.active_users)
        save_json(self.forced_file, self.forced_records)
        save_json(self.rbq_stats_file, self.rbq_stats)

        # 取消尚未执行的撤回任务，避免插件卸载后仍调用协议端。
        for task in tuple(self._withdraw_tasks):
            task.cancel()
        self._withdraw_tasks.clear()
