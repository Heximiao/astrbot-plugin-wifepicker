from datetime import datetime

from astrbot.api.event import AstrMessageEvent

from ..utils import is_allowed_group
from ..user_profiles import get_display_name
from ..i18n import tr


async def cmd_show_history(plugin_instance, event: AstrMessageEvent):
    group_id = str(event.get_group_id())
    if not is_allowed_group(group_id, plugin_instance.config):
        return

    user_id = str(event.get_sender_id())
    today = datetime.now().strftime("%Y-%m-%d")
    if plugin_instance.records.get("date") != today:
        yield event.plain_result(tr(plugin_instance, "history_empty"))
        return

    group_recs = (
        plugin_instance.records.get("groups", {})
        .get(group_id, {})
        .get("records", [])
    )
    user_recs = [r for r in group_recs if r["user_id"] == user_id]
    if not user_recs:
        yield event.plain_result(tr(plugin_instance, "history_empty"))
        return

    daily_limit = plugin_instance.config.get("daily_limit", 3)
    res = [
        tr(
            plugin_instance,
            "history_title",
            used=len(user_recs),
            limit=daily_limit,
        )
    ]
    for i, r in enumerate(user_recs, 1):
        time_str = datetime.fromisoformat(r["timestamp"]).strftime("%H:%M")
        wife_name = get_display_name(
            plugin_instance, event, r["wife_id"], fallback=r["wife_name"]
        )
        res.append(
            tr(
                plugin_instance,
                "history_item",
                index=i,
                wife_name=wife_name,
                time=time_str,
            )
        )
    res.append(
        tr(
            plugin_instance,
            "history_remaining",
            remaining=max(0, daily_limit - len(user_recs)),
        )
    )
    yield event.plain_result("\n".join(res))
