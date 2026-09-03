import os

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from ..core import clean_rbq_stats
from ..user_profiles import get_avatar_url, get_display_name
from ..i18n import tr


async def cmd_rbq_ranking(plugin_instance, event: AstrMessageEvent):
    if event.is_private_chat():
        yield event.plain_result(tr(plugin_instance, "ranking_private"))
        return

    group_id = str(event.get_group_id())
    clean_rbq_stats(plugin_instance)

    group_data = plugin_instance.rbq_stats.get(group_id, {})
    if not group_data:
        yield event.plain_result(tr(plugin_instance, "ranking_empty"))
        return

    user_map = {}
    try:
        if event.get_platform_name() == "aiocqhttp":
            members = await event.bot.api.call_action(
                "get_group_member_list", group_id=int(group_id)
            )
            for m in members:
                uid = str(m.get("user_id"))
                user_map[uid] = m.get("card") or m.get("nickname") or uid
    except Exception:
        pass

    sorted_list = []
    for uid, ts_list in group_data.items():
        sorted_list.append(
            {
                "uid": uid,
                "name": user_map.get(
                    uid, get_display_name(plugin_instance, event, uid)
                ),
                "avatar_url": get_avatar_url(plugin_instance, event, uid),
                "count": len(ts_list),
            }
        )

    sorted_list.sort(key=lambda x: x["count"], reverse=True)
    top_10 = sorted_list[:10]

    current_rank = 1
    for i, user in enumerate(top_10):
        if i > 0 and user["count"] < top_10[i - 1]["count"]:
            current_rank = i + 1
        user["rank"] = current_rank

    template_path = os.path.join(
        plugin_instance.curr_dir, "template", "rbq_ranking.html"
    )
    if not os.path.exists(template_path):
        yield event.plain_result(tr(plugin_instance, "ranking_template_missing"))
        return

    with open(template_path, "r", encoding="utf-8") as f:
        template_content = f.read()

    try:
        header_h = 100
        item_h = 60
        footer_h = 50
        rank_width = 400
        dynamic_height = header_h + (len(top_10) * item_h) + footer_h

        url = await plugin_instance.html_render(
            template_content,
            {
                "group_id": group_id,
                "ranking": top_10,
                "title": tr(plugin_instance, "ranking_title"),
            },
            options={
                "type": "png",
                "quality": None,
                "full_page": False,
                "clip": {
                    "x": 0,
                    "y": 0,
                    "width": rank_width,
                    "height": dynamic_height,
                },
                "scale": "device",
                "device_scale_factor_level": "ultra",
            },
        )
        yield event.image_result(url)
    except Exception as e:
        logger.error(f"渲染RBQ排行失败: {e}")
