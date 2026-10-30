import os

from astrbot.api import logger

async def run_debug_graph(plugin_instance, event):
    '''
    独立出的调试关系图渲染逻辑
    '''
    curr_dir = plugin_instance.curr_dir

    mock_records = [
            {"user_id": "1001", "wife_id": "1002", "wife_name": "User B", "forced": False},
            {"user_id": "1002", "wife_id": "1003", "wife_name": "User C", "forced": True},
            {"user_id": "1003", "wife_id": "1001", "wife_name": "User A", "forced": False},
            {"user_id": "1004", "wife_id": "1005", "wife_name": "User E", "forced": False},
            {"user_id": "1005", "wife_id": "1004", "wife_name": "User D", "forced": True},
            {"user_id": "1006", "wife_id": "1007", "wife_name": "User F", "forced": False},
            {"user_id": "1007", "wife_id": "1006", "wife_name": "User G", "forced": True},
            {"user_id": "1008", "wife_id": "1006", "wife_name": "User G", "forced": True},
            {"user_id": "1009", "wife_id": "1006", "wife_name": "User G", "forced": True},
            {"user_id": "1010", "wife_id": "1006", "wife_name": "User G", "forced": True},
            {"user_id": "1011", "wife_id": "1006", "wife_name": "User G", "forced": True},
            {"user_id": "1012", "wife_id": "1011", "wife_name": "User G", "forced": True},
            {"user_id": "1013", "wife_id": "1012", "wife_name": "User G", "forced": True},
            {"user_id": "1014", "wife_id": "1013", "wife_name": "User G", "forced": True},
            {"user_id": "1015", "wife_id": "1014", "wife_name": "User G", "forced": True},
            {"user_id": "1016", "wife_id": "1015", "wife_name": "User G", "forced": True},
            {"user_id": "1017", "wife_id": "1016", "wife_name": "User G", "forced": True},
            {"user_id": "1018", "wife_id": "1009", "wife_name": "User G", "forced": True},
            {"user_id": "1019", "wife_id": "1006", "wife_name": "User G", "forced": True},
            {"user_id": "1020", "wife_id": "1010", "wife_name": "User G", "forced": True},
            {"user_id": "1021", "wife_id": "1011", "wife_name": "User G", "forced": True},
            {"user_id": "1022", "wife_id": "1012", "wife_name": "User G", "forced": True},
            {"user_id": "1023", "wife_id": "1013", "wife_name": "User G", "forced": True},
            {"user_id": "1024", "wife_id": "1014", "wife_name": "User G", "forced": True},
            {"user_id": "1025", "wife_id": "1015", "wife_name": "User G", "forced": True},
            {"user_id": "1026", "wife_id": "1016", "wife_name": "User G", "forced": True},
            {"user_id": "1027", "wife_id": "1010", "wife_name": "User G", "forced": True},


        ]

    mock_user_map = {
            "1001": "Alice (1001)",
            "1002": "Bob (1002)", 
            "1003": "Charlie (1003)",
            "1004": "David (1004)",
            "1005": "Eve (1005)",
            "1006": "Frank (1006)",
            "1007": "Grace (1007)",
            "1008": "Hank (1008)",
            "1009": "Ivy (1009)",
            "1010": "Jack (1010)",
            "1011": "Jill (1011)",
            "1012": "John (1012)",
            "1013": "Julia (1013)",
            "1014": "Juliet (1014)",
            "1015": "Justin (1015)",
            "1016": "Katie (1016)",
            "1017": "Kevin (1017)",
            "1018": "Katie (1018)",
            "1019": "Katie (1019)",
            "1020": "Katie (1020)",
            "1021": "Kaie (1021)",
            "1022": "Katie (1022)",
            "1023": "Katie (1023)",
            "1024": "Katie (1024)",
            "1025": "Katie (1025)",
            "1026": "Katie (1026)",
            "1027": "Katie (1027)",
        }
    
    # 1. 只读取模板和脚本，直接交给渲染服务，不保存调试 HTML。
    template_path = os.path.join(curr_dir, "template", "graph_template.html")
    if not os.path.exists(template_path):
        yield event.plain_result(f"错误：找不到模板文件 {template_path}")
        return

    with open(template_path, "r", encoding="utf-8") as f:
        template_content = f.read()

    vis_js_path = os.path.join(curr_dir, "vis-network.min.js")
    if not os.path.exists(vis_js_path):
        yield event.plain_result(f"错误：找不到 JS 文件 {vis_js_path}")
        return
    with open(vis_js_path, "r", encoding="utf-8") as f:
        vis_js_content = f.read()

    # 2. 计算动态高度
    unique_nodes = set()
    for r in mock_records:
        unique_nodes.add(str(r.get("user_id")))
        unique_nodes.add(str(r.get("wife_id")))
    node_count = len(unique_nodes)
    
    render_width = 1280
    render_height = 720 + max(0, node_count - 20) * 24

    # 3. 调用插件实例的渲染 API
    try:
        url = await plugin_instance.html_render(template_content, {
            "vis_js_content": vis_js_content,
            "group_name": "Debug Group",
            "records": mock_records,
            "user_map": mock_user_map,
            "avatar_map": {},
            "iterations": 150,
            "render_width": render_width,
            "render_height": render_height,
        }, options={
            "viewport_width": render_width,
            "viewport_height": render_height,
            "full_page": True,
            "type": "png",
            "quality": None,
            "scale": "device",
            "device_scale_factor_level": "ultra",
        })
        yield event.image_result(url)
    except Exception as e:
        logger.error(f"Debug render failed: {e}")
        yield event.plain_result(f"渲染失败: {e}")
