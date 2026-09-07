"""Telegram transport helpers; gameplay and quotas stay in the command modules."""

import asyncio
import base64
import hashlib
import os
import re
import time
from pathlib import Path


def value(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def is_telegram_event(event) -> bool:
    return str(event.get_platform_name()).lower() == "telegram"


def telegram_message(event):
    raw = getattr(event.message_obj, "raw_message", None)
    return value(raw, "effective_message") or value(raw, "message") or raw


def telegram_chat_id(event) -> int:
    # AstrBot keeps forum topics separate in storage, but the API needs the chat ID.
    return int(str(event.get_group_id()).split("#", 1)[0])


def self_user_id(event) -> str:
    if is_telegram_event(event):
        return str(value(getattr(event, "client", None), "id", event.get_self_id()))
    return str(event.get_self_id())


def profiles(plugin, event):
    try:
        platform_id = event.get_platform_id() or event.get_platform_name()
    except Exception:
        platform_id = event.get_platform_name()
    return plugin.official_profiles.setdefault(str(platform_id), {}).setdefault(
        str(event.get_group_id()), {}
    )


def remember_user(plugin, event, user) -> str | None:
    uid = str(value(user, "id", ""))
    if not uid.isdigit():
        return None
    group = profiles(plugin, event)
    old = group.get(uid, {})
    name = value(user, "full_name") or " ".join(
        str(value(user, key, "") or "") for key in ("first_name", "last_name")
    ).strip()
    updated = {
        **old,
        "nickname": name or value(user, "username") or f"用户({uid})",
        "username": value(user, "username") or "",
        "is_bot": bool(value(user, "is_bot", False)),
    }
    if updated != old:
        updated["updated_at"] = int(time.time())
        group[uid] = updated
        plugin._telegram_profiles_dirty = True
    return uid


def save_profiles(plugin):
    from ..utils import save_json

    if getattr(plugin, "_telegram_profiles_dirty", False):
        if save_json(plugin.official_profiles_file, plugin.official_profiles):
            plugin._telegram_profiles_dirty = False


def remember_event(plugin, event):
    msg = telegram_message(event)
    if not value(msg, "sender_chat"):
        uid = remember_user(plugin, event, value(msg, "from_user") or value(msg, "from"))
        cache = getattr(plugin, "_telegram_member_cache", {})
        key = (str(event.get_platform_id()), telegram_chat_id(event), uid)
        # A fresh message proves this sender is back in the group.
        if key in cache and not cache[key][1]:
            cache.pop(key)
    reply = value(msg, "reply_to_message")
    if reply and not value(reply, "sender_chat"):
        remember_user(plugin, event, value(reply, "from_user") or value(reply, "from"))
    for entity in value(msg, "entities") or value(msg, "caption_entities") or ():
        if value(entity, "type") == "text_mention":
            remember_user(plugin, event, value(entity, "user"))
    save_profiles(plugin)


def resolve_target(plugin, event) -> str | None:
    """Resolve explicit mentions first, then a replied-to user; never store usernames as IDs."""
    import astrbot.api.message_components as Comp

    remember_event(plugin, event)
    msg = telegram_message(event)
    group = profiles(plugin, event)
    bot = getattr(event, "client", None)
    bot_id = str(value(bot, "id", ""))
    bot_name = str(value(bot, "username", "") or event.get_self_id()).lstrip("@").lower()
    targets = []
    explicit = False
    mentioned_bot = False
    text = value(msg, "text") or value(msg, "caption") or event.message_str
    entities = value(msg, "entities") or value(msg, "caption_entities") or ()
    for entity in entities:
        kind = value(entity, "type")
        if kind == "text_mention":
            targets.append(str(value(value(entity, "user"), "id", "")))
        elif kind == "mention":
            # Telegram entity offsets count UTF-16 code units, including emoji.
            offset, length = value(entity, "offset", 0), value(entity, "length", 0)
            target = text.encode("utf-16-le")[offset * 2:(offset + length) * 2]
            targets.append(target.decode("utf-16-le").lstrip("@"))
    if not targets:
        targets = [
            str(c.qq).lstrip("@")
            for c in event.message_obj.message
            if isinstance(c, Comp.At)
        ]
    for target in targets:
        if target.lower() == bot_name or target == bot_id:
            mentioned_bot = True
            continue
        explicit = True
        if target.isdigit():
            return target
        matches = [
            uid for uid, p in group.items()
            if str(p.get("username", "")).lower() == target.lower()
        ]
        if len(matches) == 1:
            return matches[0]
    if explicit:
        return None
    reply = value(msg, "reply_to_message")
    if reply and not value(reply, "sender_chat"):
        author = value(reply, "from_user") or value(reply, "from")
        uid = str(value(author, "id", ""))
        if uid.isdigit() and uid != bot_id:
            return uid
    return bot_id if mentioned_bot and bot_id.isdigit() else None


async def get_members(plugin, event) -> list[dict]:
    """Check known active users, with cached statuses and a bounded API wait."""
    remember_event(plugin, event)
    group = profiles(plugin, event)
    active = plugin.active_users.get(str(event.get_group_id()), {})
    client = getattr(event, "client", None)
    cache = getattr(plugin, "_telegram_member_cache", None)
    if cache is None:
        cache = plugin._telegram_member_cache = {}
    semaphore = asyncio.Semaphore(6)
    chat_id = telegram_chat_id(event)
    platform_id = str(event.get_platform_id())
    known_ids = set(group) | set(active)

    async def check(uid):
        if not uid.isdigit() or client is None:
            return
        key = (platform_id, chat_id, uid)
        cached = cache.get(key)
        if cached and time.time() - cached[0] < 300:
            return
        async with semaphore:
            try:
                member = await client.get_chat_member(chat_id=chat_id, user_id=int(uid))
                user = value(member, "user")
                if user:
                    remember_user(plugin, event, user)
                status = value(member, "status")
                present = status not in ("left", "kicked", "banned") and not (
                    status == "restricted" and not value(member, "is_member", True)
                )
                cache[key] = (time.time(), present)
            except Exception:
                # Permission/network failures do not mean the user left the group.
                cache[key] = (time.time(), cached[1] if cached else None)

    try:
        await asyncio.wait_for(asyncio.gather(*(check(uid) for uid in known_ids)), timeout=8)
    except asyncio.TimeoutError:
        pass
    save_profiles(plugin)
    result = []
    for uid in known_ids:
        cached = cache.get((platform_id, chat_id, uid))
        if cached and cached[1] is False:
            continue
        profile = group.get(uid, {})
        result.append({
            "user_id": uid,
            "nickname": profile.get("nickname", f"用户({uid})"),
            "is_bot": profile.get("is_bot", False),
        })
    return result


async def avatar_source(plugin, event, user_id: str) -> str | None:
    """Cache image bytes, never Telegram file URLs containing the bot token."""
    if not str(user_id).isdigit():
        return None
    key = hashlib.sha256(f"{event.get_platform_id()}:{user_id}".encode()).hexdigest()
    path = Path(plugin.data_dir) / "telegram_avatars" / f"{key}.jpg"
    now = time.time()
    attempts = getattr(plugin, "_telegram_avatar_attempts", None)
    if attempts is None:
        attempts = plugin._telegram_avatar_attempts = {}
    stale = not path.exists() or now - path.stat().st_mtime > 86400
    if stale and now - attempts.get(key, 0) > 300:
        attempts[key] = now
        try:
            async def download():
                photos = await event.client.get_user_profile_photos(user_id=int(user_id), limit=1)
                if not photos.photos:
                    if path.exists():
                        path.unlink()
                    return
                file = await event.client.get_file(photos.photos[0][-1].file_id)
                data = await file.download_as_bytearray()
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix(".tmp")
                try:
                    temporary.write_bytes(data)
                    os.replace(temporary, path)
                finally:
                    temporary.unlink(missing_ok=True)
            await asyncio.wait_for(download(), timeout=5)
        except Exception:
            pass
    try:
        if path.exists():
            return "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        pass
    return None


def mention(plugin, event, user_id: str):
    import astrbot.api.message_components as Comp

    profile = profiles(plugin, event).get(str(user_id), {})
    name = profile.get("nickname") or str(user_id)
    name = re.sub(r"([\\\[\]])", r"\\\1", name)
    return Comp.Plain(f"[{name}](tg://user?id={int(user_id)}) ")
