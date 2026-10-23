"""Best-effort QQ Official group names, including older botpy versions."""

import asyncio
import logging
import time

logger = logging.getLogger(__name__)

# Metadata cache only, not business history. Cache failures briefly as this API
# may require a platform whitelist, and must not run for every image request.
_GROUP_NAMES: dict[tuple[str, str, str], tuple[float, str]] = {}


def _value(source, key):
    return source.get(key) if isinstance(source, dict) else getattr(source, key, None)


def _name(source, group_id):
    value = _value(source, "group_name")
    if not isinstance(value, str):
        return ""
    value = " ".join(value.split())
    return value if value and value != group_id else ""


async def _request_group_info(api, group_id):
    method = getattr(api, "get_group_info", None)
    if callable(method):
        return await method(group_openid=group_id)
    http = getattr(api, "_http", None)
    if http is None or not callable(getattr(http, "request", None)):
        return None
    # qq-botpy 1.2.1 has no convenience method for this endpoint. Reuse its
    # authenticated HTTP client instead of managing tokens in the plugin.
    from botpy.http import Route

    return await http.request(Route("GET", "/v2/groups/{group_openid}/info", group_openid=group_id))


async def get_official_group_name(event, fallback, *, debug=None):
    log = debug or (lambda message: None)
    group_id = str(event.get_group_id() or "")
    if not group_id:
        log(f"source=fallback reason=empty_group_id group_name={fallback!r}")
        return fallback
    key = (str(event.get_platform_id()), str(event.get_self_id()), group_id)
    now = time.monotonic()
    message = getattr(event, "message_obj", None)
    raw = _value(message, "raw_message")
    sources = (_value(message, "group"), raw, _value(raw, "raw_data"))
    log(f"group={group_id} event_group_name={_value(sources[0], 'group_name')!r} "
        f"raw_group_name={_value(raw, 'group_name')!r} "
        f"raw_data_group_name={_value(sources[2], 'group_name')!r}")
    # Evict the oldest entry to keep caches bounded on large deployments.
    if key not in _GROUP_NAMES and len(_GROUP_NAMES) >= 256:
        _GROUP_NAMES.pop(next(iter(_GROUP_NAMES)))
    for source_label, source in zip(("event.group", "raw_message", "raw_data"), sources):
        name = _name(source, group_id)
        if name:
            _GROUP_NAMES[key] = (now + 3600, name)
            log(f"group={group_id} source={source_label} group_name={name!r}")
            return name

    cached = _GROUP_NAMES.get(key)
    if cached and cached[0] > now:
        log(f"group={group_id} source=cache cached_name={cached[1]!r} "
            f"group_name={(cached[1] or fallback)!r}")
        return cached[1] or fallback
    name = ""
    try:
        api = getattr(getattr(event, "bot", None), "api", None)
        info = await asyncio.wait_for(_request_group_info(api, group_id), timeout=3)
        log(f"group={group_id} source=api response={info!r}")
        # Support the SDK's direct dictionary and adapters wrapping it in data.
        name = _name(info, group_id) or _name(_value(info, "data"), group_id)
    except Exception as exc:
        log(f"group={group_id} source=api error={exc!r}")
        logger.warning("[wifepicker] QQ官方群名获取失败，可能未开放群资料权限: %s", exc)
    ttl = 3600 if name else 300
    name = name or (cached[1] if cached else "")
    _GROUP_NAMES[key] = (now + ttl, name)
    log(f"group={group_id} source={'api' if ttl == 3600 else 'fallback'} "
        f"group_name={(name or fallback)!r}")
    return name or fallback
