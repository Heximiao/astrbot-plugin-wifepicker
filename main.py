import asyncio
import os

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

from .constants import DEFAULT_KEYWORD_ROUTES
from .core import ConfigAccessor, DataStore, OneBotGateway
from .keyword_trigger import KeywordRouter, MatchMode
from .services import ActivityService, KeywordDispatchService, WifeCommandService

class RandomWifePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or {}
        self._config = ConfigAccessor(self.config)

        self.curr_dir = os.path.dirname(__file__)

        self._withdraw_tasks: set[asyncio.Task] = set()

        # 数据存储相对路径
        self.data_dir = os.path.join(get_astrbot_plugin_data_path(), "random_wife")
        self._store = DataStore(
            self.data_dir,
            max_records_supplier=self._config.max_records,
        )
        self._gateway = OneBotGateway(
            withdraw_tasks=self._withdraw_tasks,
            delete_error_handler=self._handle_withdraw_error,
        )

        self.records_file = str(self._store.records_file)
        self.active_file = str(self._store.active_file)
        self.forced_file = str(self._store.forced_file)
        self.rbq_stats_file = str(self._store.rbq_stats_file)

        self.records = self._store.records
        self.active_users = self._store.active_users
        self.forced_records = self._store.forced_records
        self.rbq_stats = self._store.rbq_stats

        self._keyword_router = KeywordRouter(routes=DEFAULT_KEYWORD_ROUTES)
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
        self._activity_service = ActivityService(
            is_allowed_group=self._is_allowed_group,
            persist_active_users=self._persist_active_users,
        )
        self._keyword_dispatch_service = KeywordDispatchService(
            router=self._keyword_router,
            handlers=self._keyword_handlers,
            action_to_command_handler=self._keyword_action_to_command_handler,
            module_name=self.__class__.__module__,
            config=self.config,
            is_allowed_group=self._is_allowed_group,
            keyword_trigger_enabled=self._config.keyword_trigger_enabled,
            keyword_trigger_mode=self._get_keyword_trigger_mode,
            record_active=self._record_active,
            block_prefixes=self._keyword_trigger_block_prefixes,
        )
        self._command_service = WifeCommandService(self)
        logger.info(f"抽老婆插件已加载。数据目录: {self.data_dir}")

    @staticmethod
    def _handle_withdraw_error(error: Exception) -> None:
        logger.warning(f"自动撤回失败: {error}")

    def _persist_active_users(self, active_users: dict[str, dict[str, float]]) -> None:
        self.active_users = active_users
        self._save_json(self.active_file, self.active_users)

    def _clean_rbq_stats(self):
        self._store.sync_refs(
            records=self.records,
            active_users=self.active_users,
            forced_records=self.forced_records,
            rbq_stats=self.rbq_stats,
        )
        self._store.clean_rbq_stats()
        self.rbq_stats = self._store.rbq_stats

    def _load_json(self, path: str, default: object):
        return self._store.load_json(path, default)

    def _save_json(self, path: str, data: object):
        try:
            self._store.sync_refs(
                records=self.records,
                active_users=self.active_users,
                forced_records=self.forced_records,
                rbq_stats=self.rbq_stats,
            )
            self._store.save_json(path, data)
        except Exception as e:
            logger.error(f"保存数据失败: {e}")

    @staticmethod
    def _normalize_user_id_set(values: object) -> set[str]:
        return ConfigAccessor.normalize_user_id_set(values)

    def _draw_excluded_users(self) -> set[str]:
        return self._config.draw_excluded_users()

    def _force_marry_excluded_users(self) -> set[str]:
        return self._config.force_marry_excluded_users()

    def _is_allowed_group(self, group_id: str) -> bool:
        return self._config.is_allowed_group(str(group_id))

    def _ensure_today_records(self) -> None:
        self._store.sync_refs(records=self.records)
        self._store.ensure_today_records()
        self.records = self._store.records

    def _get_group_records(self, group_id: str) -> list[dict]:
        self._store.sync_refs(records=self.records)
        records = self._store.get_group_records(str(group_id))
        self.records = self._store.records
        return records

    def _auto_set_other_half_enabled(self) -> bool:
        return self._config.auto_set_other_half_enabled()

    def _auto_withdraw_enabled(self) -> bool:
        return self._config.auto_withdraw_enabled()

    def _auto_withdraw_delay_seconds(self) -> int:
        return self._config.auto_withdraw_delay_seconds()

    def _can_onebot_withdraw(self, event: AstrMessageEvent) -> bool:
        return self._auto_withdraw_enabled() and event.get_platform_name() == "aiocqhttp"

    async def _send_onebot_message(
        self,
        event: AstrMessageEvent,
        *,
        message: list[dict],
    ) -> object:
        assert isinstance(event, AiocqhttpMessageEvent)
        message_id = await self._gateway.send_message(event, message=message)
        if message_id is None:
            logger.warning("无法解析 send_*_msg 返回的 message_id")
        return message_id

    def _schedule_onebot_delete_msg(self, client, *, message_id: object) -> None:
        self._gateway.schedule_delete_msg(
            client,
            message_id=message_id,
            delay_seconds=self._auto_withdraw_delay_seconds(),
        )

    @staticmethod
    def _resolve_member_name(
        members: list[dict],
        *,
        user_id: str,
        fallback: str,
    ) -> str:
        return OneBotGateway.resolve_member_name(
            members,
            user_id=user_id,
            fallback=fallback,
        )

    def _record_active(self, event: AstrMessageEvent) -> None:
        self._activity_service.record_active(event, self.active_users)

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def track_active(self, event: AstrMessageEvent):
        self._record_active(event)

    def _get_keyword_trigger_mode(self) -> MatchMode:
        raw = self._config.keyword_trigger_mode()
        try:
            return MatchMode(str(raw))
        except ValueError:
            logger.warning(f"未知 keyword_trigger_mode={raw!r}，将回退为 exact")
            return MatchMode.EXACT

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def keyword_trigger(self, event: AstrMessageEvent):
        async for result in self._keyword_dispatch_service.dispatch(event):
            yield result

    def _cleanup_inactive(self, group_id: str):
        self._activity_service.cleanup_inactive(group_id, self.active_users)

    @filter.command("今日老婆", alias={"抽老婆"})
    async def draw_wife(self, event: AstrMessageEvent):
        async for result in self._cmd_draw_wife(event):
            yield result

    async def _cmd_draw_wife(self, event: AstrMessageEvent):
        async for result in self._command_service.cmd_draw_wife(event):
            yield result

    @filter.command("我的老婆", alias={"抽取历史"})
    async def show_history(self, event: AstrMessageEvent):
        async for result in self._cmd_show_history(event):
            yield result

    async def _cmd_show_history(self, event: AstrMessageEvent):
        async for result in self._command_service.cmd_show_history(event):
            yield result

    @filter.command("强娶")
    async def force_marry(self, event: AstrMessageEvent):
        async for result in self._cmd_force_marry(event):
            yield result

    async def _cmd_force_marry(self, event: AstrMessageEvent):
        async for result in self._command_service.cmd_force_marry(event):
            yield result

    @staticmethod
    def _extract_target_id_from_message(event: AstrMessageEvent) -> str | None:
        return OneBotGateway.extract_target_id_from_message(event)

    @filter.command("关系图")
    async def show_graph(self, event: AstrMessageEvent):
        async for result in self._cmd_show_graph(event):
            yield result

    async def _cmd_show_graph(self, event: AstrMessageEvent):
        async for result in self._command_service.cmd_show_graph(event):
            yield result

    @filter.command("rbq排行")
    async def rbq_ranking(self, event: AstrMessageEvent):
        async for result in self._command_service.cmd_rbq_ranking(event):
            yield result

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("重置记录")
    async def reset_records(self, event: AstrMessageEvent):
        async for result in self._cmd_reset_records(event):
            yield result

    async def _cmd_reset_records(self, event: AstrMessageEvent):
        async for result in self._command_service.cmd_reset_records(event):
            yield result

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("重置强娶时间")
    async def reset_force_cd(self, event: AstrMessageEvent):
        async for result in self._cmd_reset_force_cd(event):
            yield result

    async def _cmd_reset_force_cd(self, event: AstrMessageEvent):
        async for result in self._command_service.cmd_reset_force_cd(event):
            yield result

    @filter.command("抽老婆帮助", alias={"老婆插件帮助"})
    async def show_help(self, event: AstrMessageEvent):
        async for result in self._cmd_show_help(event):
            yield result

    async def _cmd_show_help(self, event: AstrMessageEvent):
        async for result in self._command_service.cmd_show_help(event):
            yield result

    @filter.command("debug_graph")
    async def debug_graph(self, event: AstrMessageEvent):
        async for result in self._command_service.cmd_debug_graph(event):
            yield result

    async def terminate(self):
        self._store.sync_refs(
            records=self.records,
            active_users=self.active_users,
            forced_records=self.forced_records,
            rbq_stats=self.rbq_stats,
        )
        self._store.save_all()

        # 取消尚未执行的撤回任务，避免插件卸载后仍调用协议端。
        self._gateway.cancel_pending_withdraw_tasks()
