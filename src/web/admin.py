"""Authenticated native-console logs, configuration and bounded storage views."""

import asyncio
import copy
import hashlib
import json
import logging
import math
import sqlite3
import time
from collections import deque
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JSON_FILES = {
    "wife_records.json": "今日老婆记录", "active_users.json": "活跃群友候选池",
    "qq_official_profiles.json": "群友资料缓存", "forced_marriage.json": "强娶冷却",
    "breakup_cooldowns.json": "分手冷却", "marriage_action_today.json": "求婚冷却",
    "rbq_stats.json": "被强娶统计",
}
TABLES = {
    "proposal_events": "求婚事件", "schema_version": "数据库版本",
    "affinity_messages": "有限消息缓存", "affinity_interactions": "有效定向互动",
    "affinity_game_events": "好感度游戏事件", "affinity_manual": "当前手动修正",
    "affinity_score_audit": "分数变化账本", "affinity_profiles": "最新群昵称",
    "affinity_group_names": "最新群名称",
    "web_group_profiles": "页面群名与昵称缓存（24小时补查）",
}


class PluginLogBuffer(logging.Handler):
    def __init__(self):
        super().__init__()
        self.rows = deque(maxlen=1000)
        self.sequence = 0

    def emit(self, record):
        try:
            # Older AstrBot versions share a logger. Verify the source path,
            # rather than capturing every other plugin on that shared logger.
            own = Path(record.pathname).resolve().is_relative_to(ROOT)
            if not own:
                return
            self.sequence += 1
            self.rows.append({"id": self.sequence, "at": record.created, "level": record.levelname,
                              "source": f"{Path(record.pathname).name}:{record.lineno}",
                              "message": self.format(record)[:8192]})
        except Exception:
            self.handleError(record)

    def snapshot(self, level, search, after):
        self.acquire()
        try:
            rows = [row for row in self.rows if row["at"] >= time.time() - 60 * 86400]
            reset = after > self.sequence
            selected = [row for row in rows if (reset or row["id"] > after)
                        and (level == "ALL" or row["level"] == level)
                        and search.casefold() in (row["message"] + row["source"]).casefold()]
            return {"rows": selected[-500:], "cursor": self.sequence, "reset": reset,
                    "capacity": 1000, "note": "仅本插件启动后的日志，内存最多保留1000条；重载后重新开始，不修改AstrBot日志级别。"}
        finally:
            self.release()


def revision(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def validate_patch(schema, changes, path=""):
    if not isinstance(changes, dict):
        raise ValueError("配置修改必须是对象")
    for key, value in changes.items():
        if key not in schema:
            raise ValueError(f"未知配置项：{path}{key}")
        item, name = schema[key], path + key
        kind = item["type"]
        if kind == "object":
            validate_patch(item.get("items", {}), value, name + ".")
        elif kind == "bool":
            if not isinstance(value, bool):
                raise ValueError(f"{name} 必须是开关值")
        elif kind in {"int", "float"}:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or (kind == "int" and not isinstance(value, int)):
                raise ValueError(f"{name} 必须是有效数字")
            limits = item.get("slider", {})
            if value < limits.get("min", 0) or value > limits.get("max", 100000000):
                raise ValueError(f"{name} 超出允许范围")
        elif kind == "string":
            if not isinstance(value, str) or len(value) > 4096:
                raise ValueError(f"{name} 必须是有效文本")
            if "options" in item and value not in item["options"]:
                raise ValueError(f"{name} 不在可选值中")
            if key in {"model_mirror", "pip_mirror"}:
                from ..affinity.model import https_endpoint
                https_endpoint(value)
        elif kind == "list":
            if not isinstance(value, list) or len(value) > 5000 or any(isinstance(v, bool) or not isinstance(v, (str, int)) or len(str(v)) > 200 for v in value):
                raise ValueError(f"{name} 必须是账号或群号列表")
        else:
            raise ValueError(f"不支持修改配置类型：{name}")


def merge_config(current, patch):
    result = copy.deepcopy(current)
    for key, value in patch.items():
        result[key] = merge_config(result.get(key, {}), value) if isinstance(value, dict) else copy.deepcopy(value)
    return result


class ConsoleAdmin:
    def __init__(self, dashboard):
        self.dashboard, self.plugin = dashboard, dashboard.plugin
        self.lock = asyncio.Lock()
        self.logs_buffer = PluginLogBuffer()
        self.logger = self.plugin.affinity.logger
        self.logger.addHandler(self.logs_buffer)

    def close(self):
        self.logger.removeHandler(self.logs_buffer)
        self.logs_buffer.close()

    def schema(self):
        return json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8-sig"))

    def file(self, name):
        root = Path(self.plugin.data_dir).resolve()
        path = (root / name).resolve()
        if path.parent != root:
            raise ValueError("文件不在插件数据目录内")
        return path

    def db(self):
        path = self.file("wifepicker.db")
        if not path.is_file():
            raise ValueError("数据库尚未创建")
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    async def logs(self):
        try:
            api = self.dashboard
            level, after = api.param("level", "ALL"), int(api.param("after", "0"))
            if level not in {"ALL", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"} or after < 0:
                raise ValueError("无效日志筛选条件")
            return api.json_response(self.logs_buffer.snapshot(level, api.param("q"), after))
        except Exception as exc:
            return self.dashboard.error(exc)

    async def config(self):
        try:
            current = copy.deepcopy(dict(self.plugin.config))
            return self.dashboard.json_response({"schema": self.schema(), "config": current, "revision": revision(current)})
        except Exception as exc:
            return self.dashboard.error(exc)

    async def save_config(self):
        api = self.dashboard
        try:
            getter = getattr(api.request, "get_json", None)
            body = await getter() if callable(getter) else await api.request.json(default={})
            if not isinstance(body, dict) or len(json.dumps(body)) > 262144:
                raise ValueError("配置请求格式不正确或过大")
            changes = body.get("changes")
            validate_patch(self.schema(), changes)
            async with self.lock:
                current = copy.deepcopy(dict(self.plugin.config))
                if body.get("revision") != revision(current):
                    raise ValueError("配置已在其他页面更新，请重新读取后再保存")
                updated = merge_config(current, changes)
                saver = getattr(self.plugin.config, "save_config", None)
                if not callable(saver):
                    raise ValueError("当前AstrBot未提供配置保存接口，请使用原生插件配置页")
                # Framework saves atomically; passing a snapshot also updates
                # its in-memory dict. Restore on failure rather than pretending.
                try:
                    await asyncio.to_thread(saver, updated)
                except Exception:
                    self.plugin.config.clear()
                    self.plugin.config.update(current)
                    raise
            self.logger.info("[wifepicker.console] 插件控制台已保存配置")
            return api.json_response({"saved": True, "revision": revision(dict(self.plugin.config)),
                                      "message": "配置已保存；多数选项立即生效，镜像地址等标有重载要求的项目需重载插件。"})
        except Exception as exc:
            return api.error(exc)

    def storage_snapshot(self):
        root = Path(self.plugin.data_dir).resolve()
        partitions = {key: {"key": key, "files": 0, "bytes": 0} for key in ("database", "json", "model", "runtime", "other")}
        checked = 0
        if root.is_dir():
            stack = [root]
            while stack and checked < 20000:
                directory = stack.pop()
                try:
                    children = list(directory.iterdir())
                except OSError:
                    continue
                for path in children:
                    if path.is_symlink() or not path.resolve().is_relative_to(root):
                        continue
                    if path.is_dir():
                        stack.append(path)
                        continue
                    checked += 1
                    parts = path.relative_to(root).parts
                    key = "runtime" if parts[:2] == ("affinity_model", "runtime") else "model" if parts[0] == "affinity_model" else "database" if path.name in {"wifepicker.db", "wifepicker.db-wal", "wifepicker.db-shm"} else "json" if path.suffix == ".json" else "other"
                    try:
                        partitions[key]["bytes"] += path.stat().st_size
                        partitions[key]["files"] += 1
                    except OSError:
                        pass
                    if checked >= 20000:
                        break
        tables = []
        if self.file("wifepicker.db").is_file():
            with closing(self.db()) as connection:
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
                    name = row[0]
                    if name in TABLES:
                        count = connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                        tables.append({"name": name, "label": TABLES[name], "rows": count})
        files = []
        for name, label in JSON_FILES.items():
            path = self.file(name)
            files.append({"name": name, "label": label, "exists": path.is_file(), "bytes": path.stat().st_size if path.is_file() else 0})
        return {"partitions": list(partitions.values()), "tables": tables, "files": files, "partial": checked >= 20000,
                "bytes": sum(row["bytes"] for row in partitions.values()), "count": sum(row["files"] for row in partitions.values())}

    async def storage(self):
        try:
            return self.dashboard.json_response(await asyncio.to_thread(self.storage_snapshot))
        except Exception as exc:
            return self.dashboard.error(exc)

    def table_snapshot(self, name, page):
        if name not in TABLES:
            raise ValueError("未知数据库表")
        with closing(self.db()) as connection:
            connection.execute("BEGIN")
            columns = [row[1] for row in connection.execute("SELECT * FROM pragma_table_info(?)", (name,))]
            total = connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            rows = [dict(row) for row in connection.execute(f'SELECT * FROM "{name}" LIMIT 50 OFFSET ?', ((page - 1) * 50,))]
        return {"name": name, "columns": columns, "rows": rows, "total": total, "page": page, "page_size": 50}

    async def table(self):
        try:
            page = int(self.dashboard.param("page", "1"))
            if not 1 <= page <= 100000:
                raise ValueError("页码超出范围")
            return self.dashboard.json_response(await asyncio.to_thread(self.table_snapshot, self.dashboard.param("table"), page))
        except Exception as exc:
            return self.dashboard.error(exc)

    def json_snapshot(self, name, pointer, page):
        if name not in JSON_FILES:
            raise ValueError("未知JSON文件")
        path = self.file(name)
        if not path.is_file() or path.stat().st_size > 20 * 1024 * 1024:
            raise ValueError("文件不存在或超过20MiB的在线预览限制")
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        if pointer:
            if not pointer.startswith("/"):
                raise ValueError("无效JSON路径")
            for raw in pointer[1:].split("/"):
                key = raw.replace("~1", "/").replace("~0", "~")
                if isinstance(value, dict):
                    value = value[key]
                elif isinstance(value, list) and key.isdigit():
                    value = value[int(key)]
                else:
                    raise ValueError("该JSON节点不能继续展开")
        items = list(value.items()) if isinstance(value, dict) else list(enumerate(value)) if isinstance(value, list) else [("值", value)]
        rows = []
        for key, child in items[(page - 1) * 50:page * 50]:
            branch = isinstance(child, (dict, list))
            kind = "object" if isinstance(child, dict) else "array" if isinstance(child, list) else "null" if child is None else "boolean" if isinstance(child, bool) else "number" if isinstance(child, (int, float)) else "string"
            rows.append({"key": str(key), "branch": branch, "type": kind,
                         "preview": f"{len(child)} 项" if branch else json.dumps(child, ensure_ascii=False)[:4096],
                         "pointer": pointer + "/" + str(key).replace("~", "~0").replace("/", "~1")})
        return {"name": name, "pointer": pointer, "rows": rows, "total": len(items), "page": page, "page_size": 50}

    async def json_file(self):
        try:
            page = int(self.dashboard.param("page", "1"))
            if not 1 <= page <= 100000:
                raise ValueError("页码超出范围")
            return self.dashboard.json_response(await asyncio.to_thread(self.json_snapshot, self.dashboard.param("file"), self.dashboard.param("pointer"), page))
        except (KeyError, IndexError) as exc:
            return self.dashboard.error(ValueError(f"JSON节点不存在：{exc}"))
        except Exception as exc:
            return self.dashboard.error(exc)
