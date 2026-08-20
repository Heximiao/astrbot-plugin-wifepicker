import asyncio
import os
import random
import time
from datetime import datetime

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

from .keyword_trigger import KeywordRouter, MatchMode
from .waifu_relations import maybe_add_other_half_record
from .src.command.help import cmd_show_help
from .src.command.breakup import cmd_breakup
from .src.command.forced_marriage import cmd_force_marry
from .src.command.my_wife import cmd_show_history
from .src.command.pick_wife import cmd_pick_wife, handle_pick_response
from .src.command.propose import cmd_propose, handle_propose_response
from .src.command.relationdiagram import cmd_show_graph
from .src.command.rbqrank import cmd_rbq_ranking
from .src.command.reset_propose_cd import cmd_reset_propose_cd
from .src.user_profiles import (
    get_avatar_url,
    get_display_name,
    remember_official_profile,
)

from .src.constants import _DEFAULT_KEYWORD_ROUTES
from .src.utils import (
    load_json, 
    save_json, 
    extract_target_id_from_message,
    is_allowed_group,           # 新增
    resolve_member_name,        # 新增
)

from .src.debug import debug_log
from .src.debug_utils import run_debug_graph
# 新增：导入 core helpers
from .src.core import (
    ACTIVE_USERS_SAVE_INTERVAL_SECONDS,
    ACTIVE_USERS_TRIM_INTERVAL_SECONDS,
    count_active_users,
    send_onebot_message,
    schedule_onebot_delete_msg,
    record_active,
    clean_rbq_stats,
    draw_excluded_users,
    force_marry_excluded_users,
    get_group_records,
    upsert_user_wife_record,
    get_force_marry_cooldown_status,
    get_propose_cooldown_status,
    get_active_user_days,
    auto_set_other_half_enabled,
    can_onebot_withdraw,
    cleanup_inactive,
    save_active_users,
    start_active_users_save_task,
    stop_active_users_save_task,
)


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
        self.official_profiles_file = os.path.join(
            self.data_dir, "qq_official_profiles.json"
        )
        self.forced_file = os.path.join(self.data_dir, "forced_marriage.json")
        self.breakup_file = os.path.join(self.data_dir, "breakup_cooldowns.json")
        self.marriage_action_file = os.path.join(self.data_dir, "marriage_action_today.json")
        self.rbq_stats_file = os.path.join(self.data_dir, "rbq_stats.json")
        
        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir, exist_ok=True)
            
        self.records = load_json(self.records_file, {"date": "", "groups": {}})
        self.active_users = load_json(self.active_file, {})
        self.official_profiles = load_json(self.official_profiles_file, {})
        self.forced_records = load_json(self.forced_file, {})
        self.breakup_records = load_json(self.breakup_file, {})
        self.marriage_action_records = load_json(self.marriage_action_file, {})
        self.rbq_stats = load_json(self.rbq_stats_file, {})
        self._active_user_count = count_active_users(self.active_users)
        self._active_users_dirty = False
        self._active_users_last_save_at = time.time()
        self._active_users_save_interval_seconds = ACTIVE_USERS_SAVE_INTERVAL_SECONDS
        self._active_users_save_task: asyncio.Task | None = None
        self._active_users_last_trim_at = 0.0
        self._active_users_trim_interval_seconds = ACTIVE_USERS_TRIM_INTERVAL_SECONDS
        start_active_users_save_task(self)

        self._keyword_router = KeywordRouter(routes=_DEFAULT_KEYWORD_ROUTES)
        self._keyword_handlers = {
            "draw_wife": self._cmd_draw_wife,
            "show_history": self._cmd_show_history,
            "force_marry": cmd_force_marry,
            "show_graph": self._cmd_show_graph,
            "rbq_ranking": self.rbq_ranking,
            "show_help": self._cmd_show_help,
            "reset_records": self._cmd_reset_records,
            "reset_force_cd": self._cmd_reset_force_cd,
            "propose_command": self.propose_command,
            "pick_wife": self._cmd_pick_wife,
            "breakup": self._cmd_breakup,
        }
        self._keyword_trigger_block_prefixes = ("/", "!", "！")
        logger.info(f"抽老婆插件已加载。数据目录: {self.data_dir}")
        debug_log(
            self,
            "debug",
            f"debug enabled active_days={self.config.get('active_user_days', 30)} "
            f"active_file={self.active_file}",
        )

    def _get_keyword_trigger_mode(self) -> MatchMode:
        """从配置中获取匹配模式，默认为包含匹配"""
        # 这里的 config.get 会读取插件配置，建议在控制面板设置里加上这个 key
        raw = self.config.get("keyword_trigger_mode", "contains")
        try:
            return MatchMode(str(raw))
        except ValueError:
            return MatchMode.CONTAINS

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
        #    activated_handlers；命令 handler 需要停止事件，避免后续 LLM 阶段继续处理。
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
            # 关键词命中后立即停止事件，避免插件回复后继续进入 LLM。
            event.stop_event()
            # 记录活跃（既然说话了就要进池子）
            record_active(self, event)
            # 找到对应的函数，比如 _cmd_draw_wife
            handler = self._keyword_handlers.get(route.action)
            if handler:
                # 核心：手动运行你的函数并获取结果
                async for result in handler(event):
                    yield result
                
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def track_active(self, event: AstrMessageEvent):
        remember_official_profile(self, event)
        record_active(self, event)
        # 在这里触发挑选回复检查钩子，因为它能捕获所有群内纯文本
        if not event.is_private_chat():
            async for result in handle_pick_response(self, event):
                yield result
        # 在这里触发求婚回复检查钩子，因为它能捕获所有群内纯文本
        if not event.is_private_chat():
            async for result in handle_propose_response(self, event):
                yield result

    @filter.command("今日老婆", alias={"抽老婆", "jrlp"})
    async def draw_wife(self, event: AstrMessageEvent):
        event.stop_event()
        async for result in self._cmd_draw_wife(event):
            yield result

    async def _cmd_draw_wife(self, event: AstrMessageEvent):
        # 清理完不在群的人后
        
        if event.is_private_chat():
            yield event.plain_result("此功能仅在群聊中可用哦~")
            return

        group_id = str(event.get_group_id())
        debug_log(
            self,
            "draw",
            f"start group={group_id} user={event.get_sender_id()} "
            f"platform={event.get_platform_name()} active_days={get_active_user_days(self)}",
        )
        save_active_users(self, force_trim=True)
        if not is_allowed_group(group_id, self.config):
            debug_log(self, "draw", f"skip disallowed group={group_id}")
            return

        user_id, bot_id = str(event.get_sender_id()), str(event.get_self_id())
        cleanup_inactive(self, group_id)

        daily_limit = self.config.get("daily_limit", 1)
        group_records = get_group_records(self, group_id)
        user_recs = [r for r in group_records if r["user_id"] == user_id]
        today_count = len(user_recs)
        debug_log(
            self,
            "draw",
            f"daily group={group_id} user={user_id} today_count={today_count} limit={daily_limit}",
        )

        if today_count >= daily_limit:
            debug_log(self, "draw", f"hit daily limit group={group_id} user={user_id}")
            if daily_limit == 1:
                wife_record = user_recs[0]
                wife_name, wife_id = wife_record["wife_name"], wife_record["wife_id"]
                wife_name = get_display_name(self, event, wife_id, fallback=wife_name)
                wife_avatar = get_avatar_url(self, event, wife_id)
                if can_onebot_withdraw(self, event):
                    message_id = await send_onebot_message(
                        self,
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
                        schedule_onebot_delete_msg(self, event.bot, message_id=message_id)
                    return

                chain = [
                    Comp.At(qq=user_id),
                    Comp.Plain(f" 你今天已经有老婆了哦❤️~\n她是：【{wife_name}】\n"),
                ]
                if wife_avatar:
                    chain.append(Comp.Image.fromURL(wife_avatar))
                yield event.chain_result(chain)
            else:
                text = f"你今天已经抽了{today_count}次老婆了，明天再来吧！"
                if can_onebot_withdraw(self, event):
                    message_id = await send_onebot_message(
                        self, event, message=[{"type": "text", "data": {"text": text}}]
                    )
                    if message_id is not None:
                        schedule_onebot_delete_msg(self, event.bot, message_id=message_id)
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
                debug_log(
                    self,
                    "draw",
                    f"member_list group={group_id} members={len(current_member_ids)}",
                )
        except Exception as e:
            logger.error(f"获取群成员列表失败，将使用缓存池: {e}")
            debug_log(self, "draw", f"member_list failed group={group_id} error={e}")

        active_pool = self.active_users.get(group_id, {})
        excluded = draw_excluded_users(self)
        if not self.config.get("allow_marry_bot", False):
            excluded.add(bot_id)
        excluded.update([user_id, "0"])

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
                self._active_user_count = max(
                    0, self._active_user_count - len(removed_uids)
                )
                debug_log(
                    self,
                    "draw",
                    f"removed_left_group group={group_id} count={len(removed_uids)}",
                )
                save_active_users(self)
        else:
            pool = [uid for uid in active_pool.keys() if uid not in excluded]

        debug_log(
            self,
            "draw",
            f"pool group={group_id} active_pool={len(active_pool)} "
            f"excluded={len(excluded)} candidates={len(pool)}",
        )
        if not pool:
            yield event.plain_result(f"老婆池为空（需有人在{get_active_user_days(self)}天内发言）。")
            return

        wife_id = random.choice(pool)
        wife_name = get_display_name(self, event, wife_id)
        user_name = get_display_name(
            self, event, user_id, fallback=event.get_sender_name() or f"用户({user_id})"
        )

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
        debug_log(self, "draw", f"selected group={group_id} user={user_id} wife={wife_id}")
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
            enabled=auto_set_other_half_enabled(self),
            timestamp=timestamp,
        )

        save_json(self.records_file, self.records, self.records_file, self.config)

        avatar_url = get_avatar_url(self, event, wife_id)
        suffix_text = (
            "\n请好好对待她哦❤️~ \n"
            f"剩余抽取次数：{max(0, daily_limit - today_count - 1)}次"
        )
        
        at_waifu_enabled = self.config.get("at_waifu", False)
        if can_onebot_withdraw(self, event):
            # --- OneBot 路径改动 ---
            msg_list = [
                {"type": "at", "data": {"qq": user_id}},
                {"type": "text", "data": {"text": f" 你的今日老婆是：\n\n【{wife_name}】\n"}},
            ]
            
            # 如果开启了艾特老婆，就把老婆的 at 加进去
            if at_waifu_enabled:
                msg_list.append({"type": "at", "data": {"qq": wife_id}})
                msg_list.append({"type": "text", "data": {"text": " "}}) # 加个空格美化

            msg_list.extend([
                {"type": "image", "data": {"file": avatar_url}},
                {"type": "text", "data": {"text": suffix_text}},
            ])

            message_id = await send_onebot_message(self, event, message=msg_list)
            if message_id is not None:
                schedule_onebot_delete_msg(self, event.bot, message_id=message_id)
            return

        # --- AstrBot 标准路径改动 ---
        chain = [
            Comp.At(qq=user_id),
            Comp.Plain(f" 你的今日老婆是：\n\n【{wife_name}】\n"),
        ]
        
        if at_waifu_enabled:
            chain.append(Comp.At(qq=wife_id))
        
        if avatar_url:
            chain.append(Comp.Image.fromURL(avatar_url))
        chain.append(Comp.Plain(suffix_text))
        yield event.chain_result(chain)

    @filter.command("我的老婆", alias={"抽取历史", "wdlp"})
    async def show_history(self, event: AstrMessageEvent):
        event.stop_event()
        async for result in self._cmd_show_history(event):
            yield result

    async def _cmd_show_history(self, event: AstrMessageEvent):
        async for result in cmd_show_history(self, event):
            yield result

    @filter.command("分手", alias={"fs"})
    async def breakup(self, event: AstrMessageEvent):
        event.stop_event()
        async for result in self._cmd_breakup(event):
            yield result

    async def _cmd_breakup(self, event: AstrMessageEvent):
        async for result in cmd_breakup(self, event):
            yield result

    @filter.command("强娶", alias={"qiangqu"})
    async def force_marry(self, event: AstrMessageEvent):
        """强娶 + @要娶的那个人"""
        event.stop_event()
        async for result in cmd_force_marry(self, event):
            yield result

    @filter.command("关系图", alias={"gxt"})
    async def show_graph(self, event: AstrMessageEvent):
        event.stop_event()
        async for result in cmd_show_graph(self, event):
            yield result

    async def _cmd_show_graph(self, event: AstrMessageEvent):
        async for result in cmd_show_graph(self, event):
            yield result

    @filter.command("rbq排行", alias={"rbqph"})
    async def rbq_ranking(self, event: AstrMessageEvent):
        event.stop_event()
        async for result in cmd_rbq_ranking(self, event):
            yield result

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("重置记录", alias={"czjl"})
    async def reset_records(self, event: AstrMessageEvent):
        event.stop_event()
        async for result in self._cmd_reset_records(event):
            yield result

    async def _cmd_reset_records(self, event: AstrMessageEvent):
        self.records = {"date": datetime.now().strftime("%Y-%m-%d"), "groups": {}}
        self.breakup_records = {}
        save_json(self.records_file, self.records)
        save_json(self.breakup_file, self.breakup_records)
        yield event.plain_result("今日抽取记录和分手冷却时间已重置！")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("重置强娶时间", alias={"czqqsj"})
    async def reset_force_cd(self, event: AstrMessageEvent):
        event.stop_event()
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

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("重置求婚时间", alias={"czqhsj"})
    async def reset_propose_cd(self, event: AstrMessageEvent):
        event.stop_event()
        async for result in cmd_reset_propose_cd(self, event):
            yield result

    @filter.command("抽老婆帮助", alias={"老婆插件帮助", "clpbz"})
    async def show_help(self, event: AstrMessageEvent):
        event.stop_event()
        async for result in cmd_show_help(self, event):
            yield result

    async def _cmd_show_help(self, event: AstrMessageEvent):
        async for result in cmd_show_help(self, event):
            yield result

    @filter.command("debug_graph")
    async def debug_graph(self, event: AstrMessageEvent):
        event.stop_event()
        '''
        调试关系图渲染
        '''
        # 直接调用外部函数，将 self (插件实例) 和 event 传进去
        async for result in run_debug_graph(self, event):
            yield result
        
    @filter.command("求婚", alias={"qh"})
    async def propose_command(self, event: AstrMessageEvent):
        event.stop_event()
        # 调用外部的发起求婚逻辑
        async for result in cmd_propose(self, event):
            yield result

    @filter.command("挑选老婆", alias={"txlp"})
    async def pick_wife(self, event: AstrMessageEvent):
        event.stop_event()
        async for result in self._cmd_pick_wife(event):
            yield result

    async def _cmd_pick_wife(self, event: AstrMessageEvent):
        async for result in cmd_pick_wife(self, event):
            yield result

    async def terminate(self):
        await stop_active_users_save_task(self)
        save_json(self.records_file, self.records)
        save_active_users(self, force_trim=True, force=True)
        save_json(self.forced_file, self.forced_records)
        save_json(self.breakup_file, self.breakup_records)
        save_json(self.marriage_action_file, self.marriage_action_records)
        save_json(self.rbq_stats_file, self.rbq_stats)

        # 取消尚未执行的撤回任务，避免插件卸载后仍调用协议端。
        for task in tuple(self._withdraw_tasks):
            task.cancel()
        self._withdraw_tasks.clear()
