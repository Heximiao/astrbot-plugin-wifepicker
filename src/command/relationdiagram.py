import os

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from ..core import get_group_records
from ..utils import is_allowed_group
from ..debug import debug_log
from ..i18n import tr
from ..platforms.user_profiles import (
    get_avatar_sources,
    get_display_name,
    get_platform_group_name,
    get_platform_members,
)


async def cmd_show_graph(plugin_instance, event: AstrMessageEvent):
    group_id = str(event.get_group_id())
    if not is_allowed_group(group_id, plugin_instance.config):
        return

    iter_count = plugin_instance.config.get("iterations", 140)

    # --- 新增：读取 JS 文件内容 ---
    vis_js_path = os.path.join(plugin_instance.curr_dir, "vis-network.min.js")
    vis_js_content = ""
    if os.path.exists(vis_js_path):
        with open(vis_js_path, "r", encoding="utf-8") as f:
            vis_js_content = f.read()
    else:
        logger.error(f"找不到 JS 文件: {vis_js_path}")
    # ---------------------------

    # 1. 读取模板文件内容
    template_path = os.path.join(
        plugin_instance.curr_dir, "template", "graph_template.html"
    )
    if not os.path.exists(template_path):
        yield event.plain_result(f"错误：找不到模板文件 {template_path}")
        return

    with open(template_path, "r", encoding="utf-8") as f:
        graph_html = f.read()

    # 2. 获取当前群的今日关系记录
    group_data = get_group_records(plugin_instance, group_id)

    group_name = tr(plugin_instance, "proposal_image_group")
    user_map = {}
    avatar_map = {}
    try:
        group_name = await get_platform_group_name(event, group_name, plugin=plugin_instance)
        members = await get_platform_members(plugin_instance, event)
        for member in members:
            uid = str(member.get("user_id"))
            name = member.get("card") or member.get("nickname") or uid
            user_map[uid] = name

    except Exception as e:
        logger.warning(f"获取群组或成员信息失败: {e}")

    debug_log(plugin_instance, "relationdiagram", f"group={group_id} group_name={group_name!r}")

    # 3. 渲染图片
    unique_nodes = set()
    for r in group_data:
        unique_nodes.add(str(r.get("user_id")))
        unique_nodes.add(str(r.get("wife_id")))
    avatars = await get_avatar_sources(plugin_instance, event, unique_nodes)
    for uid in unique_nodes:
        user_map.setdefault(uid, get_display_name(plugin_instance, event, uid))
        avatar_url = avatars.get(uid)
        if avatar_url:
            avatar_map[uid] = avatar_url

    # Keep small graphs in landscape; grow the canvas for larger groups so
    # fitting all nodes does not make avatars and labels too small to read.
    render_width = 1280
    render_height = 720 + max(0, len(unique_nodes) - 20) * 24

    try:
        url = await plugin_instance.html_render(
            graph_html,
            {
                "vis_js_content": vis_js_content,
                "group_id": group_id,
                "group_name": group_name,
                "user_map": user_map,
                "avatar_map": avatar_map,
                "records": group_data,
                "iterations": iter_count,
                "render_width": render_width,
                "render_height": render_height,
            },
            options={
                "type": "png",
                "quality": None,
                # Match the template's dynamic canvas. A screenshot clip
                # does not set the viewport used by vis-network for layout.
                "viewport_width": render_width,
                "viewport_height": render_height,
                "full_page": True,
                # Layout stays in CSS pixels; device scaling increases the
                # canvas and screenshot resolution without changing its bounds.
                "scale": "device",
                "device_scale_factor_level": "ultra",
            },
        )
        yield event.image_result(url)
    except Exception as e:
        logger.error(f"渲染失败: {e}")
