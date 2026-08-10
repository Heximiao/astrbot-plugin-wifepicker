import hashlib
import time
from urllib.parse import quote

from .utils import save_json


QQ_OFFICIAL_PLATFORMS = frozenset({"qq_official", "qq_official_webhook"})
_PLACEHOLDER_NAMES = frozenset({"unknown", "none", "null", "nil", "undefined"})


def is_qq_official_event(event) -> bool:
    try:
        name = str(event.get_platform_name() or "").strip().lower()
        return name in QQ_OFFICIAL_PLATFORMS
    except Exception:
        return False


def _platform_id(event) -> str:
    try:
        return str(event.get_platform_id() or event.get_platform_name() or "qq_official")
    except Exception:
        return "qq_official"


def _valid_name(name: object, user_id: str) -> str:
    value = str(name or "").strip()
    if not value or value.lower() in _PLACEHOLDER_NAMES or value == str(user_id):
        return ""
    return value


def _raw_author(event):
    raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
    if isinstance(raw, dict):
        return raw.get("author") or {}
    return getattr(raw, "author", None)


def _author_value(author, key: str) -> str:
    if isinstance(author, dict):
        return str(author.get(key, "") or "").strip()
    return str(getattr(author, key, "") or "").strip()


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


def remember_official_profile(plugin, event) -> None:
    """Cache profiles only when a QQ Official group message is received."""
    if not is_qq_official_event(event) or event.is_private_chat():
        return

    user_id = str(event.get_sender_id() or "").strip()
    if not user_id:
        return
    author = _raw_author(event)
    nickname = _valid_name(_author_value(author, "username"), user_id)
    if not nickname:
        try:
            nickname = _valid_name(event.get_sender_name(), user_id)
        except Exception:
            nickname = ""
    avatar_url = _author_value(author, "avatar")
    if not avatar_url.startswith(("https://", "http://")):
        avatar_url = ""
    appid = _resolve_appid(plugin, event)

    profiles = _group_profiles(plugin, event, create=True)
    old = profiles.get(user_id, {})
    updated = dict(old)
    if nickname:
        updated["nickname"] = nickname
    if avatar_url:
        updated["avatar_url"] = avatar_url
    if appid:
        updated["appid"] = appid

    if updated == old:
        return
    updated["updated_at"] = int(time.time())
    profiles[user_id] = updated
    save_json(plugin.official_profiles_file, plugin.official_profiles)


def get_display_name(plugin, event, user_id: str, fallback: str | None = None) -> str:
    user_id = str(user_id)
    if not is_qq_official_event(event):
        return fallback or f"用户({user_id})"

    nickname = _valid_name(
        _group_profiles(plugin, event).get(user_id, {}).get("nickname"), user_id
    )
    if nickname:
        return nickname
    digest = hashlib.sha256(
        f"{event.get_group_id()}\0{user_id}".encode("utf-8")
    ).hexdigest()[:8]
    return f"群友-{digest.upper()}"


def get_avatar_url(plugin, event, user_id: str, size: int = 640) -> str | None:
    user_id = str(user_id or "").strip()
    if not user_id:
        return None
    if not is_qq_official_event(event):
        return f"https://q4.qlogo.cn/headimg_dl?dst_uin={quote(user_id, safe='')}&spec={size}"

    profile = _group_profiles(plugin, event).get(user_id, {})
    avatar_url = str(profile.get("avatar_url", "") or "").strip()
    if avatar_url.startswith(("https://", "http://")):
        return avatar_url
    appid = str(profile.get("appid", "") or _resolve_appid(plugin, event)).strip()
    if not appid:
        return None
    return (
        "https://thirdqq.qlogo.cn/qqapp/"
        f"{quote(appid, safe='')}/{quote(user_id, safe='')}/640"
    )
