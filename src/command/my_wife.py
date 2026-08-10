from datetime import datetime

from astrbot.api.event import AstrMessageEvent

from ..utils import is_allowed_group
from ..user_profiles import get_display_name


async def cmd_show_history(plugin_instance, event: AstrMessageEvent):
    group_id = str(event.get_group_id())
    if not is_allowed_group(group_id, plugin_instance.config):
        return

    user_id = str(event.get_sender_id())
    today = datetime.now().strftime("%Y-%m-%d")
    if plugin_instance.records.get("date") != today:
        yield event.plain_result("你今天还没有抽过老婆哦~")
        return

    group_recs = (
        plugin_instance.records.get("groups", {})
        .get(group_id, {})
        .get("records", [])
    )
    user_recs = [r for r in group_recs if r["user_id"] == user_id]
    if not user_recs:
        yield event.plain_result("你今天还没有抽过老婆哦~")
        return

    daily_limit = plugin_instance.config.get("daily_limit", 3)
    res = [f"🌸 你今日的老婆记录 ({len(user_recs)}/{daily_limit})："]
    for i, r in enumerate(user_recs, 1):
        time_str = datetime.fromisoformat(r["timestamp"]).strftime("%H:%M")
        wife_name = get_display_name(
            plugin_instance, event, r["wife_id"], fallback=r["wife_name"]
        )
        res.append(f"{i}. 【{wife_name}】 ({time_str})")
    res.append(f"\n剩余次数：{max(0, daily_limit - len(user_recs))}次")
    yield event.plain_result("\n".join(res))
