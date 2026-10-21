"""Shared QQ Official Markdown transport and command buttons."""

import re

from ...platforms.user_profiles import is_qq_official_event


def escape_markdown(text: str) -> str:
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|>~<])", r"\\\1", text)


def is_official_group(event) -> bool:
    if not is_qq_official_event(event):
        return False
    raw = getattr(event.message_obj, "raw_message", None)
    group_id = raw.get("group_openid") if isinstance(raw, dict) else getattr(raw, "group_openid", None)
    return bool(group_id) and str(group_id) == str(event.get_group_id())


async def post_markdown(event, content: str, *, keyboard=None, verify_image=False):
    sequence = getattr(event, "_wifepicker_keyboard_seq", 10000) + 1
    event._wifepicker_keyboard_seq = sequence
    markdown = {"content": content}
    if verify_image:
        markdown["force_verify_image_resource"] = True
    payload = {
        "group_openid": str(event.get_group_id()),
        "msg_type": 2, "markdown": markdown,
        "msg_id": str(event.message_obj.message_id), "msg_seq": sequence,
    }
    if keyboard is not None:
        payload["keyboard"] = keyboard
    response = await event.bot.api.post_group_message(**payload)
    if response is None:
        raise RuntimeError("QQ API returned no message")


def command_button(button_id: str, user_id: str, text: str, label: str, *, tips: str) -> dict:
    """Fill the specified user's input box; no callback or automatic send."""
    return {
        "id": button_id,
        "render_data": {"label": label[:10], "style": 1},
        "action": {
            "type": 2,
            "permission": {"type": 0, "specify_user_ids": [user_id]},
            "data": text, "enter": False, "reply": False,
            "unsupport_tips": tips,
        },
    }
