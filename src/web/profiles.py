"""Cache-first display metadata, with persistent once-per-day miss filling."""

import asyncio
import json
import time
from contextlib import closing

from ..db.affinity import DAY, RETENTION, SCOPE_SQL


def display_name(value, identifier=""):
    text = " ".join(str(value or "").split())[:100]
    return "" if text.casefold() in {"", "unknown", "none", "null"} or text in {"这位群友", "未知用户", str(identifier), f"用户({identifier})", f"用户{identifier}", f"群 {identifier}"} else text


def unwrap(value):
    return value.get("data", value) if isinstance(value, dict) else value


class GroupProfiles:
    def __init__(self, plugin):
        self.plugin = plugin
        self.discovery_lock = asyncio.Lock()
        self.discovered_at = 0
        self.owners = {}
        self.locks = {}
        self.live = {}

    def observe(self, event):
        """Reuse sender metadata already received; no API or database per message."""
        from ..utils import is_allowed_group
        if event.is_private_chat() or not event.get_group_id() or not is_allowed_group(str(event.get_group_id()), self.plugin.config):
            return
        scope = (str(event.get_platform_id()), str(event.get_self_id()), str(event.get_group_id()))
        uid = str(event.get_sender_id())
        if uid in {scope[1], "", "0", "None"}:
            return
        obj = getattr(event, "message_obj", None)
        sender = getattr(obj, "sender", None)
        def field(value, key):
            return value.get(key) if isinstance(value, dict) else getattr(value, key, None)
        name = display_name(field(sender, "card"), uid) or display_name(field(sender, "nickname"), uid)
        group_name = display_name(field(getattr(obj, "group", None), "group_name"), scope[2])
        if not name and not group_name:
            return
        if scope not in self.live and len(self.live) >= 128:
            self.live.pop(next(iter(self.live)))
        item = self.live.setdefault(scope, {"names": {}, "name": ("", 0)})
        if name:
            if uid not in item["names"] and len(item["names"]) >= 512:
                item["names"].pop(next(iter(item["names"])))
            item["names"][uid] = (name, time.time())
        if group_name:
            item["name"] = (group_name, time.time())

    def platforms(self):
        manager = getattr(self.plugin.context, "platform_manager", None)
        return list(getattr(manager, "platform_insts", []) or [])

    def load(self, store, scope, seeds, group_name, now):
        with closing(store.connect()) as db:
            cached = db.execute(f"SELECT * FROM web_group_profiles WHERE {SCOPE_SQL} AND created_at>=?", (*scope, now - RETENTION)).fetchone()
            checked = cached["checked_at"] if cached else 0
            names = json.loads(cached["names_json"]) if cached else {}
            title = cached["group_name"] if cached else ""
            if scope[0] and scope[1]:
                for row in db.execute(f"SELECT user_id,name,created_at FROM affinity_profiles WHERE {SCOPE_SQL} AND created_at>=?", (*scope, now - RETENTION)):
                    if row["created_at"] >= checked or row["user_id"] not in names:
                        name = display_name(row["name"], row["user_id"])
                        if name:
                            names[row["user_id"]] = name
                row = db.execute(f"SELECT name,created_at FROM affinity_group_names WHERE {SCOPE_SQL} AND created_at>=?", (*scope, now - RETENTION)).fetchone()
                if row and (not title or row["created_at"] >= checked):
                    title = display_name(row["name"], scope[2]) or title
            for uid, name in seeds.items():
                name = display_name(name, uid)
                if name:
                    names.setdefault(str(uid), name)
            profiles = getattr(self.plugin, "official_profiles", {})
            candidates = [profiles.get(scope[0], {}).get(scope[2], {})] if scope[0] else [groups[scope[2]] for groups in profiles.values()
                          if isinstance(groups, dict) and isinstance(groups.get(scope[2]), dict)]
            if len(candidates) == 1:
                for uid, profile in candidates[0].items():
                    if isinstance(profile, dict):
                        name = display_name(profile.get("card"), uid) or display_name(profile.get("nickname"), uid)
                        if name:
                            names[str(uid)] = name
            entry = self.live.get(scope)
            live = [entry["names"].copy()] if entry else []
            titles = [entry["name"]] if entry else []
            if not scope[0]:
                matches = [item for key, item in self.live.copy().items() if key[2] == scope[2]]
                if len(matches) == 1:
                    live, titles = [dict(matches[0]["names"])], [matches[0]["name"]]
            for item in live:
                for uid, (name, at) in item.items():
                    if at >= checked and now - at < DAY:
                        names[uid] = name
            for name, at in titles:
                if name and at >= checked and now - at < DAY:
                    title = name
            return {"name": title or display_name(group_name, scope[2]), "names": names, "checked_at": checked,
                    "status": cached["status"] if cached else "cached"}

    def claim(self, store, scope, result, now):
        with closing(store.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(f"SELECT checked_at FROM web_group_profiles WHERE {SCOPE_SQL}", scope).fetchone()
            if row and now - row[0] < DAY:
                return False
            db.execute("""INSERT INTO web_group_profiles VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(platform,bot_id,group_id) DO UPDATE SET group_name=excluded.group_name,
                names_json=excluded.names_json,checked_at=excluded.checked_at,created_at=excluded.created_at,status=excluded.status""",
                (*scope, result["name"], json.dumps(result["names"], ensure_ascii=False), now, now, "pending"))
            return True

    def save(self, store, scope, result, started):
        encoded = json.dumps(result["names"], ensure_ascii=False)
        with closing(store.connect()) as db, db:
            db.execute(f"""UPDATE web_group_profiles SET group_name=?,names_json=?,status=?
                WHERE {SCOPE_SQL} AND checked_at=? AND status IN (?, 'pending')
                AND (group_name!=? OR names_json!=? OR status!=?)""",
                (result["name"], encoded, result["status"], *scope, started, result["status"], result["name"], encoded, result["status"]))

    def seed_cache(self, store, scope, result, now):
        with closing(store.connect()) as db, db:
            db.execute("INSERT OR IGNORE INTO web_group_profiles VALUES (?,?,?,?,?,?,?,?)",
                       (*scope, result["name"], json.dumps(result["names"], ensure_ascii=False), 0, now, "cached"))
        self.save(store, scope, result, 0)

    async def discover(self):
        async with self.discovery_lock:
            now = time.time()
            if self.discovered_at and now - self.discovered_at < DAY:
                return self.owners
            self.discovered_at = now
            async def one(platform):
                if platform.meta().name != "aiocqhttp":
                    return []
                try:
                    client = platform.get_client()
                    login, groups = await asyncio.wait_for(asyncio.gather(
                        client.api.call_action("get_login_info"), client.api.call_action("get_group_list")), timeout=3)
                    login, groups = unwrap(login), unwrap(groups)
                    if not isinstance(login, dict) or not login.get("user_id") or not isinstance(groups, list):
                        return []
                    return [(str(row["group_id"]), (platform, str(login["user_id"]), display_name(row.get("group_name"), row["group_id"])))
                            for row in groups if isinstance(row, dict) and row.get("group_id")]
                except Exception as exc:
                    self.plugin.affinity.debug(f"page_profiles_discovery_failed platform={platform.meta().id} reason={type(exc).__name__}")
                    return []
            owners = {}
            for rows in await asyncio.gather(*(one(platform) for platform in self.platforms())):
                for gid, owner in rows:
                    owners.setdefault(gid, []).append(owner)
            self.owners = owners
            return owners

    async def fetch(self, scope, result):
        group = scope[2]
        platform = None
        lookup = getattr(self.plugin.context, "get_platform_inst", None)
        if scope[0] and callable(lookup):
            platform = lookup(scope[0])
        if platform is None and not scope[0]:
            owners = (await self.discover()).get(group, []) if group.isdigit() else []
            if owners:
                platform, _, title = owners[0]
                result["name"] = title or result["name"]
            else:
                profiles = getattr(self.plugin, "official_profiles", {})
                candidates = [p for p in self.platforms() if p.meta().name in {"qq_official", "qq_official_webhook"}]
                known = [p for p in candidates if group in profiles.get(p.meta().id, {})]
                candidates = known or (candidates if not group.isdigit() else [])
                if len(candidates) == 1:
                    platform = candidates[0]
        if platform is None:
            result["status"] = "adapter_unavailable"
            return
        kind = platform.meta().name
        if kind == "aiocqhttp":
            client = platform.get_client()
            # Scoped affinity data must never use another bot's member list.
            if scope[0] and scope[1]:
                login = unwrap(await asyncio.wait_for(client.api.call_action("get_login_info"), timeout=3))
                if not isinstance(login, dict) or str(login.get("user_id")) != scope[1]:
                    result["status"] = "bot_mismatch"
                    return
            async def request(action):
                return unwrap(await asyncio.wait_for(client.api.call_action(action, group_id=int(group)), timeout=3))
            members, info = await asyncio.gather(request("get_group_member_list"), request("get_group_info"), return_exceptions=True)
            if isinstance(members, list):
                for row in members:
                    if isinstance(row, dict) and row.get("user_id"):
                        uid = str(row["user_id"])
                        name = display_name(row.get("card"), uid) or display_name(row.get("nickname"), uid)
                        if name:
                            result["names"][uid] = name
            if isinstance(info, dict):
                result["name"] = display_name(info.get("group_name"), group) or result["name"]
            result["status"] = "ready" if isinstance(members, list) else "members_unavailable"
        elif kind in {"qq_official", "qq_official_webhook"}:
            from ..platforms.qq_official_groups import _request_group_info
            info = unwrap(await asyncio.wait_for(_request_group_info(platform.get_client().api, group), timeout=3))
            if isinstance(info, dict):
                result["name"] = display_name(info.get("group_name"), group) or result["name"]
            result["status"] = "official_cached_members"
        else:
            result["status"] = "adapter_unavailable"

    async def resolve(self, scope, members=(), seeds=None, group_name=""):
        async with self.locks.setdefault(scope, asyncio.Lock()):
            return await self._resolve(scope, members, seeds, group_name)

    async def _resolve(self, scope, members, seeds, group_name):
        from ..utils import is_allowed_group
        if not is_allowed_group(scope[2], self.plugin.config):
            raise PermissionError("该群不在插件全局群黑白名单允许范围内")
        members = tuple(str(uid) for uid in members)
        store = await self.plugin.affinity.database()
        now = time.time()
        result = await asyncio.to_thread(self.load, store, scope, seeds or {}, group_name, now)
        def missing():
            return sum(not display_name(result["names"].get(uid), uid) for uid in members)
        if (not result["name"] or missing()) and now - result["checked_at"] >= DAY:
            if await asyncio.to_thread(self.claim, store, scope, result, now):
                try:
                    await self.fetch(scope, result)
                except Exception as exc:
                    result["status"] = type(exc).__name__
                    self.plugin.affinity.debug(f"page_profiles_lookup_failed scope={scope} reason={type(exc).__name__}")
                if not is_allowed_group(scope[2], self.plugin.config):
                    raise PermissionError("该群已被移出插件全局允许范围")
                result["checked_at"] = now
                await asyncio.to_thread(self.save, store, scope, result, now)
                self.plugin.affinity.debug(f"page_profiles_lookup scope={scope} status={result['status']} names={len(result['names'])} interval_hours=24")
            else:
                result = await asyncio.to_thread(self.load, store, scope, seeds or {}, group_name, now)
        result["missing"] = missing()
        result["group_name_missing"] = not bool(result["name"])
        if result["checked_at"]:
            # Persist newly received display names without renewing API credit
            # or the cache's original 60-day retention timestamp.
            await asyncio.to_thread(self.save, store, scope, result, result["checked_at"])
        elif result["name"] and not result["missing"]:
            await asyncio.to_thread(self.seed_cache, store, scope, result, now)
        result["name"] = result["name"] or f"群 {scope[2]}"
        return result
