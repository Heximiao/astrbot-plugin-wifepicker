"""通过 AstrBot 自带的鉴权插件 API 提供只读好感度控制台。"""

import asyncio
import time
from pathlib import Path

from .analytics import catalog, group_report, pair_report
from .marriage import marriage_report, today_groups
from .admin import ConsoleAdmin
from .profiles import GroupProfiles
from ..affinity.service import allowed_group, enabled, settings

PLUGIN_NAME = "astrbot_plugin_wifepicker"


class AffinityDashboard:
    def __init__(self, plugin, web=None):
        self.plugin = plugin
        self.profiles = GroupProfiles(plugin)
        if web is None:
            try:
                from astrbot.api.web import error_response, json_response, request
                web = (request, json_response, error_response)
            except ImportError:
                # AstrBot 4.29 的旧页面机制仍提供鉴权 Quart 请求上下文。
                from quart import jsonify, request
                web = (request, jsonify, lambda message, status_code=400: (jsonify({"status": "error", "message": message}), status_code))
        self.request, self.json_response, self.error_response = web
        self.admin = ConsoleAdmin(self)

    def register(self):
        for endpoint, handler in (("meta", self.meta), ("group", self.group), ("pair", self.pair), ("marriage", self.marriage),
                                  ("network-script", self.network_script)):
            self.plugin.context.register_web_api(f"/{PLUGIN_NAME}/affinity/{endpoint}", handler, ["GET"], "好感度可视化：" + endpoint)
        for endpoint, handler, methods in (
            ("logs", self.admin.logs, ["GET"]), ("config", self.admin.config, ["GET"]),
            ("config/save", self.admin.save_config, ["POST"]), ("storage", self.admin.storage, ["GET"]),
            ("table", self.admin.table, ["GET"]), ("json", self.admin.json_file, ["GET"]),
        ):
            self.plugin.context.register_web_api(f"/{PLUGIN_NAME}/affinity/admin/{endpoint}", handler, methods, "今日老婆控制台：" + endpoint)

    def close(self):
        self.admin.close()

    async def network_script(self):
        """Share the chat diagram's bundled engine through the authenticated bridge.

        AstrBot confines view assets to pages/affinity, so a relative ../../ URL
        cannot reach this file. The path is fixed and never supplied by clients.
        """
        try:
            path = Path(__file__).resolve().parents[2] / "vis-network.min.js"
            source = await asyncio.to_thread(path.read_text, encoding="utf-8")
            return self.json_response({"source": source})
        except Exception as exc:
            return self.error(exc)

    def param(self, name, default=""):
        query = getattr(self.request, "query", None)
        if query is None:
            query = self.request.args
        value = str(query.get(name, default)).strip()
        if len(value) > 200:
            raise ValueError("查询参数过长")
        return value

    def scope(self):
        scope = tuple(self.param(key) for key in ("platform", "bot", "group"))
        if not all(scope):
            raise ValueError("请选择平台、机器人和群聊")
        if not allowed_group(self.plugin, scope[2]):
            raise PermissionError("该群不在好感度允许范围内，请检查全局和好感度群黑白名单")
        return scope

    def error(self, exc):
        if isinstance(exc, PermissionError):
            return self.error_response(str(exc), status_code=403)
        if isinstance(exc, ValueError):
            return self.error_response(str(exc), status_code=400)
        self.plugin.affinity.logger.error(f"[wifepicker.affinity.page] 页面查询失败：{type(exc).__name__}: {exc}")
        return self.error_response("暂时无法读取好感度数据，请查看插件日志", status_code=500)

    async def meta(self):
        try:
            db = await self.plugin.affinity.database()
            groups = await asyncio.to_thread(catalog, db)
            groups = [row for row in groups if allowed_group(self.plugin, row["scope"][2])]
            for row in groups:
                row["affinity_allowed"] = True
            from ..utils import is_allowed_group
            known = {row["scope"][2] for row in groups}
            for gid, value in today_groups(self.plugin).items():
                gid = str(gid)
                if gid not in known and isinstance(value, dict) and value.get("records") and is_allowed_group(gid, self.plugin.config):
                    groups.append({"scope": ["", "", gid], "name": f"群 {gid}", "affinity_allowed": False})
            async def enrich(row):
                report = marriage_report(self.plugin, row["scope"][2])
                names = {member["id"]: member["name"] for member in report["members"]}
                metadata = await self.profiles.resolve(tuple(row["scope"]), names.keys(), names, row["name"])
                row["name"] = metadata["name"]
            await asyncio.gather(*(enrich(row) for row in groups))
            return self.json_response({"groups": groups, "enabled": enabled(self.plugin),
                                       "aggressive": bool(settings(self.plugin).get("aggressive", False)),
                                       "model": self.plugin.affinity.model.status, "retention_days": 60,
                                       "timezone": "UTC日汇总（北京时间08:00换日）"})
        except Exception as exc:
            return self.error(exc)

    async def marriage(self):
        try:
            from ..utils import is_allowed_group
            group = self.param("group")
            if not group:
                raise ValueError("请选择群聊")
            if not is_allowed_group(group, self.plugin.config):
                raise PermissionError("该群不在插件全局群黑白名单允许范围内")
            platform, bot = self.param("platform"), self.param("bot")
            result = marriage_report(self.plugin, group, user=self.param("user"))
            names = {member["id"]: member["name"] for member in result["members"]}
            metadata = await self.profiles.resolve((platform, bot, group), names.keys(), names)
            for member in result["members"]:
                member["name"] = metadata["names"].get(member["id"], member["name"])
            for edge in result["edges"]:
                edge["source_name"] = metadata["names"].get(edge["source"], edge["source_name"])
                edge["target_name"] = metadata["names"].get(edge["target"], edge["target_name"])
            result.update(name=metadata["name"], missing_names=metadata["missing"], names_status=metadata["status"],
                          group_name_missing=metadata["group_name_missing"])
            self.plugin.affinity.debug(f"page_marriage group={group} relations={result['totals']['relations']}")
            return self.json_response(result)
        except Exception as exc:
            return self.error(exc)

    async def group(self):
        try:
            started = time.monotonic()
            scope = self.scope()
            db = await self.plugin.affinity.database()
            result = await asyncio.to_thread(group_report, db, scope,
                                             bool(settings(self.plugin).get("aggressive", False)), self.param("user"))
            names = {member["id"]: member["name"] for member in result["members"]}
            metadata = await self.profiles.resolve(scope, names.keys(), names, result["name"])
            result["name"] = metadata["name"]
            for member in result["members"]:
                member["name"] = metadata["names"].get(member["id"], member["name"])
            for edge in result["relations"]:
                edge["source_name"] = metadata["names"].get(edge["source"], edge["source_name"])
                edge["target_name"] = metadata["names"].get(edge["target"], edge["target_name"])
            if not allowed_group(self.plugin, scope[2]):
                raise PermissionError("该群已被移出好感度允许范围")
            self.plugin.affinity.debug(f"page_group scope={scope} members={result['totals']['members']} "
                                       f"directions={len(result['relations'])} truncated={result['truncated']} "
                                       f"elapsed_ms={(time.monotonic() - started) * 1000:.1f}")
            return self.json_response(result)
        except Exception as exc:
            return self.error(exc)

    async def pair(self):
        try:
            started = time.monotonic()
            scope = self.scope()
            source, target = self.param("source"), self.param("target")
            if not source or not target or source == target:
                raise ValueError("请选择两位不同的群友，并确认好感度方向")
            days, page = int(self.param("days", "30")), int(self.param("page", "1"))
            if not 1 <= days <= 60 or not 1 <= page <= 10000:
                raise ValueError("时间范围为1～60天，页码须为正整数")
            db = await self.plugin.affinity.database()
            result = await asyncio.to_thread(pair_report, db, scope, source, target,
                                             bool(settings(self.plugin).get("aggressive", False)), days, page)
            metadata = await self.profiles.resolve(scope, (source, target), {source: result["source_name"], target: result["target_name"]})
            result["source_name"] = metadata["names"].get(source, result["source_name"])
            result["target_name"] = metadata["names"].get(target, result["target_name"])
            if not allowed_group(self.plugin, scope[2]):
                raise PermissionError("该群已被移出好感度允许范围")
            self.plugin.affinity.debug(f"page_pair scope={scope} direction={source}->{target} "
                                       f"score={result['current']['score'] if result['current'] else None} days={days} "
                                       f"page={page} entries={result['total']} reconstructed={result['has_reconstructed']} "
                                       f"elapsed_ms={(time.monotonic() - started) * 1000:.1f}")
            return self.json_response(result)
        except Exception as exc:
            return self.error(exc)
