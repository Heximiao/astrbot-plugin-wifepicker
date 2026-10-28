"""Incoming affinity queries and outgoing self-adjustments."""

import asyncio
import re

from ..affinity import allowed_group, component_kind, enabled, settings, value
from ..i18n import tr
from ..platforms.user_profiles import get_display_name, get_platform_members
from ..utils import extract_target_id_from_message


def adjustment_value(event):
    # Use current Plain components only: neither quoted text nor a numeric
    # mention ID may accidentally become the requested score.
    components = getattr(event.message_obj, "message", []) or []
    text = " ".join(str(value(c, "text", "")) for c in components if component_kind(c) == "plain")
    if not components:
        text = str(event.message_str)
    text = re.sub(r"<@!?[^>]+>|\[CQ:[^\]]+\]|@\d+", " ", text)
    text = re.sub(r"^\s*[/!！]*\s*(?:调整好感度|setaffinity)\s*", "", text, flags=re.I)
    match = re.fullmatch(r"\s*([+-]?\d{1,3}(?:\.\d)?)\s*", text)
    if not match:
        return None
    token = match[1]
    number = float(token)
    relative = token.startswith(("+", "-"))
    if (relative and abs(number) > 100) or (not relative and not 0 <= number <= 100):
        return None
    return number, relative


def nickname(text, user_id):
    text = " ".join(str(text or "").split())
    if text in {str(user_id), f"用户({user_id})"} or text.casefold() in {"", "unknown", "none", "null"}:
        return ""
    return text


async def affinity_names(plugin, event, target):
    """Resolve both group cards in one bounded platform lookup, then use profiles."""
    sender = str(event.get_sender_id())
    members = []
    try:
        members = await asyncio.wait_for(get_platform_members(plugin, event), timeout=3)
    except Exception as exc:
        if plugin.config.get("debug_enabled", False):
            plugin.affinity.logger.info(f"[wifepicker.affinity.debug] nickname_lookup_failed reason={type(exc).__name__}; using_event_or_profile=true")
    names = {}
    for member in members:
        user_id = str(member.get("user_id", ""))
        if user_id in {sender, target}:
            names[user_id] = nickname(member.get("card"), user_id) or nickname(member.get("nickname"), user_id)
    event_sender = value(event.message_obj, "sender")
    sender_fallback = nickname(value(event_sender, "card"), sender) or nickname(value(event_sender, "nickname"), sender)
    if not sender_fallback and callable(getattr(event, "get_sender_name", None)):
        sender_fallback = nickname(event.get_sender_name(), sender)
    sender_name = names.get(sender) or sender_fallback or nickname(get_display_name(plugin, event, sender), sender)
    target_name = names.get(target) or nickname(get_display_name(plugin, event, target), target)
    return target_name or tr(plugin, "affinity_member"), sender_name or tr(plugin, "affinity_member")


async def cmd_affinity(plugin, event, adjust=False):
    if event.is_private_chat():
        yield event.plain_result(tr(plugin, "group_only"))
        return
    if not allowed_group(plugin, event.get_group_id()):
        return
    if not enabled(plugin):
        yield event.plain_result(tr(plugin, "affinity_disabled"))
        return
    target = extract_target_id_from_message(event, plugin)
    sender = str(event.get_sender_id())
    bot = str(value(getattr(event, "client", None), "id", event.get_self_id()))
    if not target or target in {"all", "0", sender, str(event.get_self_id()), bot}:
        yield event.plain_result(tr(plugin, "affinity_target"))
        return
    service = getattr(plugin, "affinity", None)
    if service is None:
        yield event.plain_result(tr(plugin, "affinity_error"))
        return
    try:
        if adjust:
            parsed = adjustment_value(event)
            if parsed is None:
                yield event.plain_result(tr(plugin, "affinity_adjust_usage"))
                return
            score = await service.adjust(event, sender, target, *parsed)
            name, sender_name = await affinity_names(plugin, event, target)
            yield event.plain_result(tr(plugin, "affinity_adjusted", name=name, sender_name=sender_name, score=f"{score:.1f}"))
        else:
            row = await service.query(event, target, sender)
            if row is None:
                yield event.plain_result(tr(plugin, "affinity_empty"))
                return
            name, sender_name = await affinity_names(plugin, event, target)
            hint = tr(plugin, "affinity_chat_days" if row["mutual_days"] else "affinity_chat_start", days=row["mutual_days"])
            text = tr(plugin, "affinity_result", name=name, sender_name=sender_name,
                      score=f"{row['score']:.1f}", hint=hint)
            if settings(plugin).get("use_small_model") and not service.model.ready:
                text += "\n" + tr(plugin, "affinity_model_pending")
            yield event.plain_result(text)
    except Exception as exc:
        service.logger.warning(f"[wifepicker] 好感度命令失败: {type(exc).__name__}")
        yield event.plain_result(tr(plugin, "affinity_error"))
