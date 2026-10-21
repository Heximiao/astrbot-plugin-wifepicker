"""QQ Official pick menus; replies are handled by the shared pick_wife logic."""

from urllib.parse import quote

from astrbot.api import logger

from ...i18n import tr
from .message import command_button, escape_markdown, is_official_group, post_markdown


async def send_pick_confirmation(event, user_id: str, text: str, avatar_url: str | None):
    """QQ fetches Markdown images; a failed avatar must not hide the result."""
    content = f'<qqbot-at-user id="{user_id}" /> {escape_markdown(text)}'
    if avatar_url:
        url = quote(avatar_url, safe=":/?&=%+#@!$*,;~-._")
        try:
            await post_markdown(
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
        return command_button(
            button_id, req.user_id, text, label, tips="请发送编号、重新挑选或放弃",
        )

    choices = [
        button(f"pick_{i}", str(i), f"{i}. {name}")
        for i, name in enumerate(req.candidate_names, 1)
    ]
    rows = [{"buttons": [choice]} for choice in choices]
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
        await post_markdown(
            event, f'<qqbot-at-user id="{req.user_id}" />\n\n{escape_markdown(text)}',
            keyboard=build_keyboard(
                req, reselect=tr(plugin, "pick_button_reselect"),
                abandon=tr(plugin, "pick_button_abandon"),
            ),
        )
    except Exception as exc:
        logger.warning(f"[wifepicker] 官机候选按钮发送失败，回退文字列表: {exc}")
        return False
    return True
