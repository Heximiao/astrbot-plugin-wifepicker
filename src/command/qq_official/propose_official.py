"""QQ Official proposal display; shared propose.py handles all decisions."""

from astrbot.api import logger

from ...i18n import tr
from .message import command_button, escape_markdown, is_official_group, post_markdown


def build_keyboard(target_id: str, *, accept: str, reject: str) -> dict:
    # These labels are already accepted by handle_propose_response. English
    # actions use lowercase, just like the shared text parser.
    return {"content": {"rows": [{"buttons": [
        command_button("propose_accept", target_id, accept.lower(), accept, tips=f"请回复 {accept} 或 {reject}"),
        command_button("propose_reject", target_id, reject.lower(), reject, tips=f"请回复 {accept} 或 {reject}"),
    ]}]}}


async def send_proposal(plugin, event, target_id: str, text: str) -> bool:
    """Show a Markdown invitation, falling back to the original text on failure."""
    if not is_official_group(event):
        return False
    try:
        await post_markdown(
            event, f'<qqbot-at-user id="{target_id}" />\n\n{escape_markdown(text)}',
            keyboard=build_keyboard(
                target_id, accept=tr(plugin, "propose_button_accept"),
                reject=tr(plugin, "propose_button_reject"),
            ),
        )
    except Exception as exc:
        logger.warning(f"[wifepicker] 官机求婚按钮发送失败，回退文字邀请: {exc}")
        return False
    return True
