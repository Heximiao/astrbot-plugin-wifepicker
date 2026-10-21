"""QQ Official pick menus; replies are handled by the shared pick_wife logic."""

import re
from urllib.parse import quote

from astrbot.api import logger

from ...i18n import tr
from ...platforms.user_profiles import is_qq_official_event


def _escape_markdown(text: str) -> str:
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|>~<])", r"\\\1", text)


def is_official_group(event) -> bool:
    if not is_qq_official_event(event):
        return False
    raw = getattr(event.message_obj, "raw_message", None)
    group_id = raw.get("group_openid") if isinstance(raw, dict) else getattr(raw, "group_openid", None)
    return bool(group_id) and str(group_id) == str(event.get_group_id())


async def _post_markdown(event, content: str, *, keyboard=None, verify_image=False):
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


async def send_pick_confirmation(event, user_id: str, text: str, avatar_url: str | None):
    """QQ fetches Markdown images; a failed avatar must not hide the result."""
    content = f'<qqbot-at-user id="{user_id}" /> {_escape_markdown(text)}'
    if avatar_url:
        url = quote(avatar_url, safe=":/?&=%+#@!$*,;~-._")
        try:
            await _post_markdown(
                event, f"{content}\n\n![avatar #224px #224px]({url})",
                verify_image=True,
            )
            return
        except Exception as exc:
            logger.warning(f"[wifepicker] 官机挑选确认图片发送失败，回退文字确认: {exc}")
    # Yield a text-only result to the ordinary pipeline. No Image component is
    # left for AstrBot to download from a failing avatar host.
    result = event.plain_result(content)
    result.use_markdown_ = True
    result.use_t2i_ = False
    yield result


def build_keyboard(req, *, reselect: str, abandon: str) -> dict:
    """Command buttons fill the input box; group users send the text themselves."""
    def button(button_id, text, label):
        return {
            "id": button_id,
            "render_data": {"label": label[:10], "style": 1},
            "action": {
                "type": 2,
                "permission": {"type": 0, "specify_user_ids": [req.user_id]},
                "data": text,
                "enter": False,
                "reply": False,
                "unsupport_tips": "请发送编号、重新挑选或放弃",
            },
        }

    choices = [
        button(f"pick_{i}", str(i), f"{i}. {name}")
        for i, name in enumerate(req.candidate_names, 1)
    ]
    rows = [{"buttons": choices[i:i + 3]} for i in range(0, len(choices), 3)]
    rows.append({"buttons": [
        button("pick_reselect", reselect, reselect),
        button("pick_abandon", abandon, abandon),
    ]})
    return {"content": {"rows": rows}}


async def send_candidate_list(plugin, event, req, text: str) -> bool:
    """Send the official group menu, or let the caller use its normal text list."""
    if not is_official_group(event):
        return False
    try:
        await _post_markdown(
            event, f'<qqbot-at-user id="{req.user_id}" />\n\n{_escape_markdown(text)}',
            keyboard=build_keyboard(
                req, reselect=tr(plugin, "pick_button_reselect"),
                abandon=tr(plugin, "pick_button_abandon"),
            ),
        )
    except Exception as exc:
        logger.warning(f"[wifepicker] 官机候选按钮发送失败，回退文字列表: {exc}")
        return False
    return True
