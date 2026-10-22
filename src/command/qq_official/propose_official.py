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


async def _send_menu(event, user_id: str, text: str, keyboard: dict) -> bool:
    """Send a menu, leaving the caller's original reply as the fallback."""
    if not is_official_group(event):
        return False
    try:
        await post_markdown(
            event, f'<qqbot-at-user id="{user_id}" />\n\n{escape_markdown(text)}',
            keyboard=keyboard,
        )
    except Exception as exc:
        logger.warning(f"[wifepicker] 官机求婚交互按钮发送失败，回退文字提示: {exc}")
        return False
    return True


async def send_proposal(plugin, event, target_id: str, text: str) -> bool:
    """Only the proposal recipient may accept or reject."""
    return await _send_menu(event, target_id, text, build_keyboard(
        target_id, accept=tr(plugin, "propose_button_accept"),
        reject=tr(plugin, "propose_button_reject"),
    ))


async def send_force_confirmation(plugin, event, proposer_id: str, text: str) -> bool:
    """Only the proposer may choose whether to continue after rejection."""
    keyboard = {"content": {"rows": [{"buttons": [
        command_button(
            "force_confirm", proposer_id, "是", tr(plugin, "force_button_confirm"),
            tips="请回复是或取消",
        ),
        command_button(
            "force_abandon", proposer_id, "取消", tr(plugin, "force_button_abandon"),
            tips="请回复是或取消",
        ),
    ]}]}}
    return await _send_menu(event, proposer_id, text, keyboard)
