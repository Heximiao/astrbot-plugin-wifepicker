from astrbot.api.event import AstrMessageEvent

from ..utils import is_allowed_group
from ..core import get_active_user_days


async def cmd_show_help(plugin_instance, event: AstrMessageEvent):
    if not is_allowed_group(str(event.get_group_id()), plugin_instance.config):
        return
    daily_limit = plugin_instance.config.get("daily_limit", 3)
    active_user_days = get_active_user_days(plugin_instance)
    help_text = (
        "===== 🌸 抽老婆帮助 =====\n"
        "1. 【抽老婆】：随机抽取今日老婆\n"
        "2. 【强娶@某人】或【强娶 @某人】：强行更换今日老婆（有冷却期）\n"
        "3. 【我的老婆】：查看今日历史与次数\n"
        "4. 【重置记录】：(管理员) 清空数据（强娶记录不会清除）\n"
        "5. 【关系图】：查看群友老婆的关系\n"
        "6. 【rbq排行】：展示近30天被强娶的次数排行\n"
        "7. 【求婚】：向群友求婚\n"
        "8. 【挑选老婆】：从随机抽取的候选群友中选择一位成为今日老婆\n"
        "9. 【分手】：解除普通老婆关系（强娶关系不可解除，冷却 72 小时）\n"
        f"当前每日上限：{daily_limit}次\n"
        "提示：可在配置开启“关键词触发”，直接发送关键词无需 / 前缀。\n"
        "提示：可在配置开启“自动设置对方老婆 / 定时自动撤回”。\n"
        f"注：仅限{active_user_days}天内发言且当前在群的活跃群友。"
    )
    yield event.plain_result(help_text)
