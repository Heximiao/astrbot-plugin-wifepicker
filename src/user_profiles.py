import hashlib
import time
from typing import Any
from urllib.parse import quote

from .utils import save_json


QQ_OFFICIAL_PLATFORMS = frozenset({"qq_official", "qq_official_webhook"})
DISCORD_PLATFORMS = frozenset({"discord", "discord_bot"})
_PLACEHOLDER_NAMES = frozenset({"unknown", "none", "null", "nil", "undefined"})


def _platform_name(event) -> str:
    try:
        return str(event.get_platform_name() or "").strip().lower()
    except Exception:
        return ""


def is_qq_official_event(event) -> bool:
    return _platform_name(event) in QQ_OFFICIAL_PLATFORMS


def is_discord_event(event) -> bool:
    return _platform_name(event) in DISCORD_PLATFORMS


def _platform_id(event) -> str:
    try:
        return str(event.get_platform_id() or event.get_platform_name() or "unknown")
    except Exception:
        return _platform_name(event) or "unknown"


def _valid_name(name: object, user_id: str) -> str:
    value = str(name or "").strip()
    if not value or value.lower() in _PLACEHOLDER_NAMES or value == str(user_id):
        return ""
    return value


def _raw_author(event) -> Any:
    raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
    if isinstance(raw, dict):
        return raw.get("author") or {}
    return getattr(raw, "author", None)


def _author_value(author: Any, key: str) -> str:
    if isinstance(author, dict):
        return str(author.get(key, "") or "").strip()
    return str(getattr(author, key, "") or "").strip()


def _asset_url(asset: Any, *, size: int | None = None) -> str:
    if asset is None:
        return ""
    if size is not None and hasattr(asset, "with_size"):
        try:
            asset = asset.with_size(size)
        except Exception:
            pass
    value = getattr(asset, "url", asset)
    url = str(value or "").strip()
    return url if url.startswith(("https://", "http://")) else ""


def _resolve_appid(plugin, event) -> str:
    platform = getattr(getattr(event, "bot", None), "platform", None)
    platform_config = getattr(platform, "config", None)
    if isinstance(platform_config, dict):
        return str(platform_config.get("appid", "") or "").strip()
    return ""


def _group_profiles(plugin, event, *, create: bool = False) -> dict:
    group_id = str(event.get_group_id() or "")
    if not group_id:
        return {}
    platform_id = _platform_id(event)
    if create:
        return plugin.official_profiles.setdefault(platform_id, {}).setdefault(
            group_id, {}
        )
    return plugin.official_profiles.get(platform_id, {}).get(group_id, {})


def _save_profiles(plugin) -> None:
    save_json(plugin.official_profiles_file, plugin.official_profiles)


def _remember_profile(
    plugin,
    event,
    user_id: str,
    *,
    nickname: object = "",
    avatar_url: object = "",
    appid: object = "",
) -> bool:
    user_id = str(user_id or "").strip()
    if not user_id or not event.get_group_id():
        return False
    profiles = _group_profiles(plugin, event, create=True)
    old = profiles.get(user_id, {})
    updated = dict(old)
    valid_name = _valid_name(nickname, user_id)
    valid_avatar = _asset_url(avatar_url)
    if valid_name:
        updated["nickname"] = valid_name
    if valid_avatar:
        updated["avatar_url"] = valid_avatar
    if appid:
        updated["appid"] = str(appid)
    if updated == old:
        return False
    updated["updated_at"] = int(time.time())
    profiles[user_id] = updated
    return True


def remember_user_profile(plugin, event) -> None:
    """Cache sender metadata supplied by QQ Official and Discord events."""
    if event.is_private_chat() or not (
        is_qq_official_event(event) or is_discord_event(event)
    ):
        return
    user_id = str(event.get_sender_id() or "").strip()
    if not user_id:
        return
    author = _raw_author(event)
    sender = getattr(getattr(event, "message_obj", None), "sender", None)
    nickname = (
        _author_value(author, "display_name")
        or _author_value(author, "global_name")
        or _author_value(author, "username")
        or _author_value(author, "name")
        or getattr(sender, "nickname", "")
        or event.get_sender_name()
    )
    avatar_url = (
        _asset_url(getattr(author, "display_avatar", None), size=512)
        or _asset_url(getattr(author, "avatar", None), size=512)
        or _author_value(author, "avatar_url")
        or _author_value(author, "avatar")
    )
    appid = _resolve_appid(plugin, event) if is_qq_official_event(event) else ""
    if _remember_profile(
        plugin,
        event,
        user_id,
        nickname=nickname,
        avatar_url=avatar_url,
        appid=appid,
    ):
        _save_profiles(plugin)


# Backwards-compatible name for older imports.
remember_official_profile = remember_user_profile


def _discord_client(event) -> Any:
    candidate = getattr(event, "client", None) or getattr(event, "bot", None)
    if candidate is None:
        return None
    if hasattr(candidate, "get_channel"):
        return candidate
    for attr in ("client", "_client", "discord_client", "_discord_client"):
        client = getattr(candidate, attr, None)
        if client is not None and hasattr(client, "get_channel"):
            return client
    return None


async def get_platform_members(plugin, event) -> list[dict]:
    """Return normalized member dictionaries, or an empty list when unsupported."""
    platform = _platform_name(event)
    group_id = str(event.get_group_id() or "")
    if not group_id:
        return []
    if platform == "aiocqhttp":
        members = await event.bot.api.call_action(
            "get_group_member_list", group_id=int(group_id)
        )
        if isinstance(members, dict) and isinstance(members.get("data"), list):
            members = members["data"]
        return members if isinstance(members, list) else []
    if not is_discord_event(event):
        return []
    client = _discord_client(event)
    if client is None:
        return []
    channel = client.get_channel(int(group_id))
    if channel is None and hasattr(client, "fetch_channel"):
        channel = await client.fetch_channel(int(group_id))
    guild = getattr(channel, "guild", None)
    if guild is None:
        return []
    normalized = []
    changed = False
    for member in getattr(guild, "members", []) or []:
        user_id = str(getattr(member, "id", "") or "")
        if not user_id:
            continue
        nickname = (
            getattr(member, "display_name", "")
            or getattr(member, "nick", "")
            or getattr(member, "global_name", "")
            or getattr(member, "name", "")
        )
        avatar_url = _asset_url(getattr(member, "display_avatar", None), size=512)
        normalized.append(
            {
                "user_id": user_id,
                "nickname": str(getattr(member, "name", "") or nickname),
                "card": str(nickname or ""),
                "avatar_url": avatar_url,
                "is_bot": bool(getattr(member, "bot", False)),
            }
        )
        changed = _remember_profile(
            plugin,
            event,
            user_id,
            nickname=nickname,
            avatar_url=avatar_url,
        ) or changed
    if changed:
        _save_profiles(plugin)
    return normalized


async def get_platform_group_name(event, fallback: str = "未命名群聊") -> str:
    platform = _platform_name(event)
    group_id = str(event.get_group_id() or "")
    try:
        if platform == "aiocqhttp":
            info = await event.bot.api.call_action(
                "get_group_info", group_id=int(group_id)
            )
            if isinstance(info, dict) and isinstance(info.get("data"), dict):
                info = info["data"]
            if isinstance(info, dict):
                return str(info.get("group_name") or fallback)
        if is_discord_event(event):
            client = _discord_client(event)
            if client is not None:
                channel = client.get_channel(int(group_id))
                if channel is None and hasattr(client, "fetch_channel"):
                    channel = await client.fetch_channel(int(group_id))
                guild = getattr(channel, "guild", None)
                channel_name = str(getattr(channel, "name", "") or "")
                guild_name = str(getattr(guild, "name", "") or "")
                if guild_name and channel_name:
                    return f"{guild_name} / #{channel_name}"
                return channel_name or guild_name or fallback
    except Exception:
        pass
    return fallback


def get_display_name(plugin, event, user_id: str, fallback: str | None = None) -> str:
    user_id = str(user_id)
    if is_qq_official_event(event) or is_discord_event(event):
        nickname = _valid_name(
            _group_profiles(plugin, event).get(user_id, {}).get("nickname"), user_id
        )
        if nickname:
            return nickname
        if fallback:
            return fallback
        if is_qq_official_event(event):
            digest = hashlib.sha256(
                f"{event.get_group_id()}\0{user_id}".encode("utf-8")
            ).hexdigest()[:8]
            return f"群友-{digest.upper()}"
    return fallback or f"用户({user_id})"


def get_avatar_url(plugin, event, user_id: str, size: int = 640) -> str | None:
    user_id = str(user_id or "").strip()
    if not user_id:
        return None
    profile = _group_profiles(plugin, event).get(user_id, {})
    avatar_url = str(profile.get("avatar_url", "") or "").strip()
    if avatar_url.startswith(("https://", "http://")):
        return avatar_url
    if is_discord_event(event):
        return None
    if not is_qq_official_event(event):
        return f"https://q4.qlogo.cn/headimg_dl?dst_uin={quote(user_id, safe='')}&spec={size}"
    appid = str(profile.get("appid", "") or _resolve_appid(plugin, event)).strip()
    if not appid:
        return None
    return (
        "https://thirdqq.qlogo.cn/qqapp/"
        f"{quote(appid, safe='')}/{quote(user_id, safe='')}/640"
    )
