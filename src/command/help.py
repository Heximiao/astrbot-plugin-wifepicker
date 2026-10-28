from astrbot.api.event import AstrMessageEvent

from ..utils import is_allowed_group
from ..core import get_active_user_days
from ..i18n import tr
from ..affinity import allowed_group


async def cmd_show_help(plugin_instance, event: AstrMessageEvent):
    if not is_allowed_group(str(event.get_group_id()), plugin_instance.config):
        return
    daily_limit = plugin_instance.config.get("daily_limit", 3)
    active_user_days = get_active_user_days(plugin_instance)
    help_text = tr(
        plugin_instance,
        "help",
        limit=daily_limit,
        days=active_user_days,
    )
    config = plugin_instance.config.get("group_affinity", {})
    if isinstance(config, dict) and config.get("enabled", False) and allowed_group(plugin_instance, event.get_group_id()):
        help_text += "\n" + tr(plugin_instance, "affinity_help")
    yield event.plain_result(help_text)
