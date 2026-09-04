import os

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from ..core import get_group_records
from ..utils import is_allowed_group
from ..user_profiles import (
    get_avatar_url,
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

    group_name = "未命名群聊"
    user_map = {}
    avatar_map = {}
    try:
        group_name = await get_platform_group_name(event, group_name)
        members = await get_platform_members(plugin_instance, event)
        for member in members:
            uid = str(member.get("user_id"))
            name = member.get("card") or member.get("nickname") or uid
            user_map[uid] = name

    except Exception as e:
        logger.warning(f"获取群组或成员信息失败: {e}")

    # 3. 渲染图片
    # 根据节点数量动态计算高度，避免拥挤
    # 动态计算你想要裁剪的区域大小
    unique_nodes = set()
    for r in group_data:
        unique_nodes.add(str(r.get("user_id")))
        unique_nodes.add(str(r.get("wife_id")))
    for uid in unique_nodes:
        user_map.setdefault(uid, get_display_name(plugin_instance, event, uid))
        avatar_url = get_avatar_url(plugin_instance, event, uid)
        if avatar_url:
            avatar_map[uid] = avatar_url
    node_count = len(unique_nodes)

    # 假设我们想要从左上角 (0,0) 开始，裁剪一个动态高度的区域
    clip_width = 1920
    clip_height = 1080 + (max(0, node_count - 10) * 60)

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
            },
            options={
                "type": "png",
                "quality": None,
                "scale": "device",
                # 必须传齐这四个参数，且必须是 int 或 float，不能是字符串
                "clip": {
                    "x": 0,
                    "y": 0,
                    "width": clip_width,
                    "height": clip_height,
                },
                # 注意：使用 clip 时通常建议将 full_page 设为 False
                "full_page": False,
                "device_scale_factor_level": "ultra",
            },
        )
        yield event.image_result(url)
    except Exception as e:
        logger.error(f"渲染失败: {e}")
