from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from ..utils import save_json
from ..i18n import tr


async def cmd_reset_propose_cd(plugin_instance, event: AstrMessageEvent):
    """Reset propose cooldown records for the current group."""
    if event.is_private_chat():
        yield event.plain_result(tr(plugin_instance, "reset_propose_group_only"))
        return

    group_id = str(event.get_group_id())
    group_records = plugin_instance.marriage_action_records.get(group_id)

    if not isinstance(group_records, dict) or not group_records:
        yield event.plain_result(tr(plugin_instance, "reset_propose_empty"))
        return

    reset_count = 0
    for user_id, record in list(group_records.items()):
        if isinstance(record, dict) and record.get("action") == "propose":
            group_records.pop(user_id, None)
            reset_count += 1

    if not group_records:
        plugin_instance.marriage_action_records.pop(group_id, None)

    if reset_count == 0:
        yield event.plain_result(tr(plugin_instance, "reset_propose_empty"))
        return

    save_json(
        plugin_instance.marriage_action_file,
        plugin_instance.marriage_action_records,
    )
    logger.info(f"[Wife] reset propose cooldown for group {group_id}, count={reset_count}")
    yield event.plain_result(
        tr(plugin_instance, "reset_propose_done", count=reset_count)
    )
