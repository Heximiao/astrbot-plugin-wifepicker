from __future__ import annotations

import os
import random
import time
from datetime import datetime, timedelta
from typing import Any, Iterable

import logging

try:
    import astrbot.api.message_components as Comp
except ImportError:  # pragma: no cover - test fallback
    class _ImageStub:
        @staticmethod
        def fromURL(url: str):
            return url

    class _CompStub:
        At = dict
        Plain = str
        Image = _ImageStub

    Comp = _CompStub()

try:
    from astrbot.api import logger
except ImportError:  # pragma: no cover - test fallback
    logger = logging.getLogger(__name__)

try:
    from ..waifu_relations import maybe_add_other_half_record
except ImportError:  # pragma: no cover - test fallback
    from waifu_relations import maybe_add_other_half_record


def select_draw_pool(
    *,
    active_user_ids: Iterable[str],
    current_member_ids: list[str],
    excluded: set[str],
) -> tuple[list[str], list[str]]:
    active_ids = [str(uid) for uid in active_user_ids]
    if current_member_ids:
        pool = [
            uid
            for uid in active_ids
            if uid not in excluded and uid in set(current_member_ids)
        ]
        removed = [uid for uid in active_ids if uid not in set(current_member_ids)]
        return pool, removed

    return [uid for uid in active_ids if uid not in excluded], []


def compute_force_marry_reset(*, last_time: float, now_ts: float, cd_days: int) -> tuple[datetime, float]:
    last_dt = datetime.fromtimestamp(last_time)
    last_midnight = datetime.combine(last_dt.date(), datetime.min.time())
    target_reset_dt = last_midnight + timedelta(days=max(1, int(cd_days)))
    remaining = target_reset_dt.timestamp() - now_ts
    return target_reset_dt, remaining


def build_rbq_top_ranking(
    group_data: dict[str, list[float]],
    user_map: dict[str, str],
    *,
    top_n: int = 10,
) -> list[dict[str, Any]]:
    sorted_list = [
        {
            "uid": uid,
            "name": user_map.get(uid, f"用户({uid})"),
            "count": len(ts_list),
        }
        for uid, ts_list in group_data.items()
    ]
    sorted_list.sort(key=lambda x: x["count"], reverse=True)
    top_users = sorted_list[:top_n]

    current_rank = 1
    for index, user in enumerate(top_users):
        if index > 0 and user["count"] < top_users[index - 1]["count"]:
            current_rank = index + 1
        user["rank"] = current_rank

    return top_users


class WifeCommandService:
    """Business command implementations extracted from plugin entrypoint."""

    def __init__(self, runtime: Any):
        self._runtime = runtime

    async def cmd_draw_wife(self, event):
        runtime = self._runtime

        if event.is_private_chat():
            yield event.plain_result("此功能仅在群聊中可用哦~")
            return

        group_id = str(event.get_group_id())
        if not runtime._is_allowed_group(group_id):
            return

        user_id = str(event.get_sender_id())
        bot_id = str(event.get_self_id())
        runtime._cleanup_inactive(group_id)

        daily_limit = runtime._config.daily_limit()
        group_records = runtime._get_group_records(group_id)
        user_recs = [record for record in group_records if record["user_id"] == user_id]
        today_count = len(user_recs)

        if today_count >= daily_limit:
            if daily_limit == 1:
                wife_record = user_recs[0]
                wife_name = wife_record["wife_name"]
                wife_id = wife_record["wife_id"]
                wife_avatar = f"https://q4.qlogo.cn/headimg_dl?dst_uin={wife_id}&spec=640"
                if runtime._can_onebot_withdraw(event):
                    message_id = await runtime._send_onebot_message(
                        event,
                        message=[
                            {"type": "at", "data": {"qq": user_id}},
                            {
                                "type": "text",
                                "data": {
                                    "text": f" 你今天已经有老婆了哦❤️~\n她是：【{wife_name}】\n"
                                },
                            },
                            {"type": "image", "data": {"file": wife_avatar}},
                        ],
                    )
                    if message_id is not None:
                        runtime._schedule_onebot_delete_msg(event.bot, message_id=message_id)
                    return

                chain = [
                    Comp.At(qq=user_id),
                    Comp.Plain(f" 你今天已经有老婆了哦❤️~\n她是：【{wife_name}】\n"),
                    Comp.Image.fromURL(wife_avatar),
                ]
                yield event.chain_result(chain)
            else:
                text = f"你今天已经抽了{today_count}次老婆了，明天再来吧！"
                if runtime._can_onebot_withdraw(event):
                    message_id = await runtime._send_onebot_message(
                        event,
                        message=[{"type": "text", "data": {"text": text}}],
                    )
                    if message_id is not None:
                        runtime._schedule_onebot_delete_msg(event.bot, message_id=message_id)
                    return

                yield event.plain_result(text)
            return

        current_member_ids: list[str] = []
        members: list[dict] = []
        try:
            if event.get_platform_name() == "aiocqhttp":
                members = await runtime._gateway.fetch_group_member_list(event)
                current_member_ids = [str(member.get("user_id")) for member in members]
        except Exception as error:
            logger.error(f"获取群成员列表失败，将使用缓存池: {error}")

        active_pool = runtime.active_users.get(group_id, {})
        excluded = runtime._draw_excluded_users()
        excluded.update([bot_id, user_id, "0"])

        pool, removed_uids = select_draw_pool(
            active_user_ids=active_pool.keys(),
            current_member_ids=current_member_ids,
            excluded=excluded,
        )
        if removed_uids and group_id in runtime.active_users:
            for removed_uid in removed_uids:
                runtime.active_users[group_id].pop(removed_uid, None)
            runtime._save_json(runtime.active_file, runtime.active_users)

        if not pool:
            yield event.plain_result("老婆池为空（需有人在30天内发言）。")
            return

        wife_id = random.choice(pool)
        wife_name = f"用户({wife_id})"
        user_name = event.get_sender_name() or f"用户({user_id})"

        try:
            if event.get_platform_name() == "aiocqhttp":
                wife_name = runtime._resolve_member_name(
                    members,
                    user_id=wife_id,
                    fallback=wife_name,
                )
                user_name = runtime._resolve_member_name(
                    members,
                    user_id=user_id,
                    fallback=user_name,
                )
        except Exception:
            pass

        timestamp = datetime.now().isoformat()
        group_records.append(
            {
                "user_id": user_id,
                "wife_id": wife_id,
                "wife_name": wife_name,
                "timestamp": timestamp,
            }
        )

        maybe_add_other_half_record(
            records=group_records,
            user_id=user_id,
            user_name=user_name,
            wife_id=wife_id,
            wife_name=wife_name,
            enabled=runtime._auto_set_other_half_enabled(),
            timestamp=timestamp,
        )

        runtime._save_json(runtime.records_file, runtime.records)

        avatar_url = f"https://q4.qlogo.cn/headimg_dl?dst_uin={wife_id}&spec=640"
        suffix_text = (
            "\n请好好对待她哦❤️~ \n"
            f"剩余抽取次数：{max(0, daily_limit - today_count - 1)}次"
        )
        if runtime._can_onebot_withdraw(event):
            message_id = await runtime._send_onebot_message(
                event,
                message=[
                    {"type": "at", "data": {"qq": user_id}},
                    {
                        "type": "text",
                        "data": {"text": f" 你的今日老婆是：\n\n【{wife_name}】\n"},
                    },
                    {"type": "image", "data": {"file": avatar_url}},
                    {"type": "text", "data": {"text": suffix_text}},
                ],
            )
            if message_id is not None:
                runtime._schedule_onebot_delete_msg(event.bot, message_id=message_id)
            return

        chain = [
            Comp.At(qq=user_id),
            Comp.Plain(f" 你的今日老婆是：\n\n【{wife_name}】\n"),
            Comp.Image.fromURL(avatar_url),
            Comp.Plain(suffix_text),
        ]
        yield event.chain_result(chain)

    async def cmd_show_history(self, event):
        runtime = self._runtime
        group_id = str(event.get_group_id())
        if not runtime._is_allowed_group(group_id):
            return

        user_id = str(event.get_sender_id())
        today = datetime.now().strftime("%Y-%m-%d")
        if runtime.records.get("date") != today:
            yield event.plain_result("你今天还没有抽过老婆哦~")
            return

        group_recs = runtime.records.get("groups", {}).get(group_id, {}).get("records", [])
        user_recs = [record for record in group_recs if record["user_id"] == user_id]
        if not user_recs:
            yield event.plain_result("你今天还没有抽过老婆哦~")
            return

        daily_limit = runtime._config.daily_limit()
        lines = [f"🌸 你今日的老婆记录 ({len(user_recs)}/{daily_limit})："]
        for index, record in enumerate(user_recs, 1):
            time_str = datetime.fromisoformat(record["timestamp"]).strftime("%H:%M")
            lines.append(f"{index}. 【{record['wife_name']}】 ({time_str})")
        lines.append(f"\n剩余次数：{max(0, daily_limit - len(user_recs))}次")
        yield event.plain_result("\n".join(lines))

    async def cmd_force_marry(self, event):
        runtime = self._runtime
        if event.is_private_chat():
            yield event.plain_result("此功能仅在群聊中可用哦~")
            return

        user_id = str(event.get_sender_id())
        bot_id = str(event.get_self_id())
        group_id = str(event.get_group_id())
        if not runtime._is_allowed_group(group_id):
            return

        now = time.time()
        last_time = runtime.forced_records.setdefault(group_id, {}).get(user_id, 0)
        cd_days = runtime._config.force_marry_cd_days()

        target_reset_dt, remaining = compute_force_marry_reset(
            last_time=last_time,
            now_ts=now,
            cd_days=cd_days,
        )

        if remaining > 0:
            days = int(remaining // 86400)
            hours = int((remaining % 86400) // 3600)
            mins = int((remaining % 3600) // 60)
            yield event.plain_result(
                f"你已经强娶过啦！\n请等待：{days}天{hours}小时{mins}分后再试。\n"
                f"(重置时间：{target_reset_dt.strftime('%m-%d %H:%M')})"
            )
            return

        target_id = runtime._extract_target_id_from_message(event)

        if not target_id or target_id == "all":
            yield event.plain_result("请 @ 一个你想强娶的人。")
            return

        if target_id == user_id:
            yield event.plain_result("不能娶自己！")
            return

        force_excluded = runtime._force_marry_excluded_users()
        force_excluded.update({bot_id, "0"})
        if target_id in force_excluded:
            yield event.plain_result("该用户在强娶排除列表中，无法被强娶。")
            return

        target_name = f"用户({target_id})"
        user_name = event.get_sender_name() or f"用户({user_id})"
        members: list[dict] = []
        try:
            if event.get_platform_name() == "aiocqhttp":
                members = await runtime._gateway.fetch_group_member_list(event)
                target_name = runtime._resolve_member_name(
                    members,
                    user_id=target_id,
                    fallback=target_name,
                )
                user_name = runtime._resolve_member_name(
                    members,
                    user_id=user_id,
                    fallback=user_name,
                )
        except Exception:
            pass

        group_records = runtime._get_group_records(group_id)

        if group_id not in runtime.rbq_stats:
            runtime.rbq_stats[group_id] = {}
        if target_id not in runtime.rbq_stats[group_id]:
            runtime.rbq_stats[group_id][target_id] = []

        runtime.rbq_stats[group_id][target_id].append(time.time())
        runtime._clean_rbq_stats()
        runtime._save_json(runtime.rbq_stats_file, runtime.rbq_stats)

        group_records[:] = [record for record in group_records if record["user_id"] != user_id]

        timestamp = datetime.now().isoformat()
        group_records.append(
            {
                "user_id": user_id,
                "wife_id": target_id,
                "wife_name": target_name,
                "timestamp": timestamp,
                "forced": True,
            }
        )

        maybe_add_other_half_record(
            records=group_records,
            user_id=user_id,
            user_name=user_name,
            wife_id=target_id,
            wife_name=target_name,
            enabled=runtime._auto_set_other_half_enabled(),
            timestamp=timestamp,
        )

        runtime.forced_records[group_id][user_id] = now

        runtime._save_json(runtime.records_file, runtime.records)
        runtime._save_json(runtime.forced_file, runtime.forced_records)

        avatar_url = f"https://q4.qlogo.cn/headimg_dl?dst_uin={target_id}&spec=640"
        text = f" 你今天强娶了【{target_name}】哦❤️~\n请对她好一点哦~。\n"
        if runtime._can_onebot_withdraw(event):
            message_id = await runtime._send_onebot_message(
                event,
                message=[
                    {"type": "at", "data": {"qq": user_id}},
                    {"type": "text", "data": {"text": text}},
                    {"type": "image", "data": {"file": avatar_url}},
                ],
            )
            if message_id is not None:
                runtime._schedule_onebot_delete_msg(event.bot, message_id=message_id)
            return

        chain = [Comp.At(qq=user_id), Comp.Plain(text), Comp.Image.fromURL(avatar_url)]
        yield event.chain_result(chain)

    async def cmd_show_graph(self, event):
        runtime = self._runtime
        group_id = str(event.get_group_id())
        if not runtime._is_allowed_group(group_id):
            return

        iter_count = runtime._config.iterations()

        vis_js_path = os.path.join(runtime.curr_dir, "vis-network.min.js")
        vis_js_content = ""
        if os.path.exists(vis_js_path):
            with open(vis_js_path, "r", encoding="utf-8") as file:
                vis_js_content = file.read()
        else:
            logger.error(f"找不到 JS 文件: {vis_js_path}")

        template_path = os.path.join(runtime.curr_dir, "graph_template.html")
        if not os.path.exists(template_path):
            yield event.plain_result(f"错误：找不到模板文件 {template_path}")
            return

        with open(template_path, "r", encoding="utf-8") as file:
            graph_html = file.read()

        group_data = runtime.records.get("groups", {}).get(group_id, {}).get("records", [])

        group_name = "未命名群聊"
        user_map: dict[str, str] = {}
        try:
            if event.get_platform_name() == "aiocqhttp":
                info = await runtime._gateway.fetch_group_info(event)
                group_name = str(info.get("group_name") or "未命名群聊")

                members = await runtime._gateway.fetch_group_member_list(event)
                for member in members:
                    uid = str(member.get("user_id"))
                    name = member.get("card") or member.get("nickname") or uid
                    user_map[uid] = str(name)
        except Exception as error:
            logger.warning(f"获取群信息失败: {error}")

        unique_nodes = set()
        for record in group_data:
            unique_nodes.add(str(record.get("user_id")))
            unique_nodes.add(str(record.get("wife_id")))
        node_count = len(unique_nodes)

        clip_width = 1920
        clip_height = 1080 + (max(0, node_count - 10) * 60)

        try:
            url = await runtime.html_render(
                graph_html,
                {
                    "vis_js_content": vis_js_content,
                    "group_id": group_id,
                    "group_name": group_name,
                    "user_map": user_map,
                    "records": group_data,
                    "iterations": iter_count,
                },
                options={
                    "type": "png",
                    "quality": None,
                    "scale": "device",
                    "clip": {
                        "x": 0,
                        "y": 0,
                        "width": clip_width,
                        "height": clip_height,
                    },
                    "full_page": False,
                    "device_scale_factor_level": "ultra",
                },
            )
            yield event.image_result(url)
        except Exception as error:
            logger.error(f"渲染失败: {error}")

    async def cmd_rbq_ranking(self, event):
        runtime = self._runtime
        if event.is_private_chat():
            yield event.plain_result("私聊看不了榜单哦~")
            return

        group_id = str(event.get_group_id())
        runtime._clean_rbq_stats()

        group_data = runtime.rbq_stats.get(group_id, {})
        if not group_data:
            yield event.plain_result("本群近30天还没有人被强娶过，大家都很有礼貌呢。")
            return

        user_map: dict[str, str] = {}
        try:
            if event.get_platform_name() == "aiocqhttp":
                members = await runtime._gateway.fetch_group_member_list(event)
                for member in members:
                    uid = str(member.get("user_id"))
                    user_map[uid] = str(member.get("card") or member.get("nickname") or uid)
        except Exception:
            pass

        top_10 = build_rbq_top_ranking(group_data, user_map, top_n=10)

        template_path = os.path.join(runtime.curr_dir, "rbq_ranking.html")
        if not os.path.exists(template_path):
            yield event.plain_result("错误：找不到排行模板 rbq_ranking.html")
            return

        with open(template_path, "r", encoding="utf-8") as file:
            template_content = file.read()

        try:
            header_h = 100
            item_h = 60
            footer_h = 50
            rank_width = 400

            dynamic_height = header_h + (len(top_10) * item_h) + footer_h
            url = await runtime.html_render(
                template_content,
                {
                    "group_id": group_id,
                    "ranking": top_10,
                    "title": "❤️ 群rbq月榜 ❤️",
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
        except Exception as error:
            logger.error(f"渲染RBQ排行失败: {error}")

    async def cmd_reset_records(self, event):
        runtime = self._runtime
        runtime.records = {"date": datetime.now().strftime("%Y-%m-%d"), "groups": {}}
        runtime._save_json(runtime.records_file, runtime.records)
        yield event.plain_result("今日抽取记录已重置！")

    async def cmd_reset_force_cd(self, event):
        runtime = self._runtime
        group_id = str(event.get_group_id())

        if hasattr(runtime, "forced_records") and group_id in runtime.forced_records:
            runtime.forced_records[group_id] = {}
            runtime._save_json(runtime.forced_file, runtime.forced_records)
            logger.info(f"[Wife] 已重置群 {group_id} 的强娶冷却时间")
            yield event.plain_result("✅ 本群强娶冷却时间已重置！现在大家可以再次强娶了。")
        else:
            yield event.plain_result("💡 本群目前没有人在冷却期内。")

    async def cmd_show_help(self, event):
        runtime = self._runtime
        if not runtime._is_allowed_group(str(event.get_group_id())):
            return

        daily_limit = runtime._config.daily_limit()
        help_text = (
            "===== 🌸 抽老婆帮助 =====\n"
            "1. 【抽老婆】：随机抽取今日老婆\n"
            "2. 【强娶@某人】或【强娶 @某人】：强行更换今日老婆（有冷却期）\n"
            "3. 【我的老婆】：查看今日历史与次数\n"
            "4. 【重置记录】：(管理员) 清空数据（强娶记录不会清除）\n"
            "5. 【关系图】：查看群友老婆的关系\n"
            "6. 【rbq排行】：展示近30天被强娶的次数排行\n"
            f"当前每日上限：{daily_limit}次\n"
            "提示：可在配置开启“关键词触发”，直接发送关键词无需 / 前缀。\n"
            "提示：可在配置开启“自动设置对方老婆 / 定时自动撤回”。\n"
            "注：仅限30天内发言且当前在群的活跃群友。"
        )
        yield event.plain_result(help_text)

    async def cmd_debug_graph(self, event):
        runtime = self._runtime

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

        with open(os.path.join(runtime.curr_dir, "graph_template.html"), "r", encoding="utf-8") as file:
            template_content = file.read()

        import jinja2

        env = jinja2.Environment()
        template = env.from_string(template_content)
        html_content = template.render(
            group_name="Debug Group",
            records=mock_records,
            user_map=mock_user_map,
            iterations=1000,
        )

        debug_html_path = os.path.join(runtime.curr_dir, "debug_output.html")
        with open(debug_html_path, "w", encoding="utf-8") as file:
            file.write(html_content)

        yield event.plain_result(f"Debugging... HTML saved to {debug_html_path}")

        unique_nodes = set()
        for record in mock_records:
            unique_nodes.add(str(record.get("user_id")))
            unique_nodes.add(str(record.get("wife_id")))
        node_count = len(unique_nodes)

        view_height = 1080
        if node_count > 10:
            view_height = 1080 + (node_count - 10) * 60

        try:
            url = await runtime.html_render(
                template_content,
                {
                    "group_name": "Debug Group",
                    "records": mock_records,
                    "user_map": mock_user_map,
                    "iterations": 1000,
                },
                options={
                    "viewport": {"width": 1920, "height": view_height},
                    "type": "jpeg",
                    "quality": 100,
                    "device_scale_factor_level": "ultra",
                },
            )
            yield event.image_result(url)
        except Exception as error:
            logger.error(f"Debug render failed: {error}")
            yield event.plain_result(f"Render failed: {error}")
