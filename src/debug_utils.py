import os
import time

import logging
from datetime import datetime

logger = logging.getLogger("astrbot")

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
    
    # 1. 渲染并保存 HTML 供检查
    template_path = os.path.join(curr_dir, "graph_template.html")
    if not os.path.exists(template_path):
        yield event.plain_result(f"错误：找不到模板文件 {template_path}")
        return

    with open(template_path, "r", encoding="utf-8") as f:
        template_content = f.read()

    import jinja2
    env = jinja2.Environment()
    template = env.from_string(template_content)
    html_content = template.render(
        group_name="Debug Group",
        records=mock_records,
        user_map=mock_user_map,
        iterations=150
    )
    
    debug_html_path = os.path.join(curr_dir, "debug_output.html")
    with open(debug_html_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    
    yield event.plain_result(f"调试中... HTML 已保存至 {debug_html_path}")

    # 2. 计算动态高度
    unique_nodes = set()
    for r in mock_records:
        unique_nodes.add(str(r.get("user_id")))
        unique_nodes.add(str(r.get("wife_id")))
    node_count = len(unique_nodes)
    
    view_height = 1080
    if node_count > 10:
        view_height = 1080 + (node_count - 10) * 60

    # 3. 调用插件实例的渲染 API
    try:
        url = await plugin_instance.html_render(template_content, {
            "group_name": "Debug Group",
            "records": mock_records,
            "user_map": mock_user_map,
            "iterations": 150
        }, options={
            "viewport": {"width": 1920, "height": view_height},
            "type": "jpeg",
            "quality": 100,
            "device_scale_factor_level": "ultra",
        })
        yield event.image_result(url)
    except Exception as e:
        logger.error(f"Debug render failed: {e}")
        yield event.plain_result(f"渲染失败: {e}")
