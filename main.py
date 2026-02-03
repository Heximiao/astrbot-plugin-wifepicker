import os
import json
import random
import time
from datetime import datetime
from typing import List, Dict, Any
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent 
import astrbot.api.message_components as Comp
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

class RandomWifePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None): 
        super().__init__(context)
        self.config = config 

        self.curr_dir = os.path.dirname(__file__)
        
        # 数据存储相对路径
        self.data_dir = os.path.join(get_astrbot_plugin_data_path(), "random_wife")
        self.records_file = os.path.join(self.data_dir, "wife_records.json")
        self.active_file = os.path.join(self.data_dir, "active_users.json") 
        self.forced_file = os.path.join(self.data_dir, "forced_marriage.json")
        
        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir, exist_ok=True)
            
        self.records = self._load_json(self.records_file, {"date": "", "groups": {}})
        self.active_users = self._load_json(self.active_file, {})
        self.forced_records = self._load_json(self.forced_file, {})
        logger.info(f"抽老婆插件已加载。数据目录: {self.data_dir}")

    def _load_json(self, path, default):
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f: return json.load(f)
            except: return default
        return default

    def _save_json(self, path, data):
        try:
            # === 全局记录总量清理逻辑 ===
            if path == self.records_file and "groups" in data:
                max_total = self.config.get("max_records", 500)
                all_recs = []
                # 展平所有记录
                for gid, gdata in data["groups"].items():
                    for r in gdata.get("records", []):
                        r["_gid"] = gid # 临时记录所属群
                        all_recs.append(r)
                
                # 如果超过全局上限
                if len(all_recs) > max_total:
                    # 按时间戳排序（最早的在前面）
                    all_recs.sort(key=lambda x: x.get("timestamp", ""))
                    # 只保留最后的 max_total 条
                    keep_recs = all_recs[-max_total:]
                    
                    # 重新归类到各个群
                    new_groups = {}
                    for r in keep_recs:
                        gid = r.pop("_gid")
                        if gid not in new_groups: new_groups[gid] = {"records": []}
                        new_groups[gid]["records"].append(r)
                    data["groups"] = new_groups

            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存数据失败: {e}")

    def _is_allowed_group(self, group_id: str) -> bool:
        whitelist = self.config.get("whitelist_groups", [])
        blacklist = self.config.get("blacklist_groups", [])
        if str(group_id) in [str(g) for g in blacklist]: return False
        if whitelist and str(group_id) not in [str(g) for g in whitelist]: return False
        return True

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def track_active(self, event: AstrMessageEvent):
        group_id = event.get_group_id()
        if not group_id or not self._is_allowed_group(str(group_id)): return

        user_id, bot_id = str(event.get_sender_id()), str(event.get_self_id())
        # 排除 ID 为 "0" 的记录
        if user_id == bot_id or user_id == "0": return
        
        if str(group_id) not in self.active_users:
            self.active_users[str(group_id)] = {}
        self.active_users[str(group_id)][user_id] = time.time()
        self._save_json(self.active_file, self.active_users)

    def _cleanup_inactive(self, group_id: str):
        if group_id not in self.active_users: return
        now, limit = time.time(), 30 * 24 * 3600
        active_group = self.active_users[group_id]
        # 过滤过时数据和 ID 为 "0" 的数据
        new_active = {uid: ts for uid, ts in active_group.items() if (now - ts < limit) and uid != "0"}
        if len(active_group) != len(new_active):
            self.active_users[group_id] = new_active
            self._save_json(self.active_file, self.active_users)

    @filter.command("今日老婆", alias={'抽老婆'})
    async def draw_wife(self, event: AstrMessageEvent):
        if event.is_private_chat():
            yield event.plain_result("此功能仅在群聊中可用哦~")
            return
        
        group_id = str(event.get_group_id())
        if not self._is_allowed_group(group_id): return

        user_id, bot_id = str(event.get_sender_id()), str(event.get_self_id())
        self._cleanup_inactive(group_id)

        today = datetime.now().strftime("%Y-%m-%d")
        if self.records.get("date") != today:
            self.records = {"date": today, "groups": {}}

        daily_limit = self.config.get("daily_limit", 3)
        group_data = self.records.get("groups", {}).get(group_id, {"records": []})
        user_recs = [r for r in group_data["records"] if r["user_id"] == user_id]
        today_count = len(user_recs)

        if today_count >= daily_limit:
            if daily_limit == 1:
                wife_record = user_recs[0]
                wife_name, wife_id = wife_record["wife_name"], wife_record["wife_id"]
                wife_avatar = f"https://q4.qlogo.cn/headimg_dl?dst_uin={wife_id}&spec=640"
                chain = [Comp.At(qq=user_id), Comp.Plain(f" 你今天已经有老婆了哦❤️~\n她是：【{wife_name}】\n"), Comp.Image.fromURL(wife_avatar)]
                yield event.chain_result(chain)
            else:
                yield event.plain_result(f"你今天已经抽了{today_count}次老婆了，明天再来吧！")
            return

        # --- 增强：获取最新的群成员列表以过滤退群者 ---
        current_member_ids = []
        try:
            if event.get_platform_name() == "aiocqhttp":
                assert isinstance(event, AiocqhttpMessageEvent)
                members = await event.bot.api.call_action('get_group_member_list', group_id=int(group_id))
                current_member_ids = [str(m.get("user_id")) for m in members]
        except Exception as e:
            logger.error(f"获取群成员列表失败，将使用缓存池: {e}")

        active_pool = self.active_users.get(group_id, {})
        excluded = {str(uid) for uid in self.config.get("excluded_users", [])}
        excluded.update([bot_id, user_id, "0"]) 
        
        # 核心逻辑：如果在 aiocqhttp 平台，只从【当前还在群里】的人中抽取
        if current_member_ids:
            pool = [uid for uid in active_pool.keys() if uid not in excluded and uid in current_member_ids]
            # 同时顺便清理一下 active_users，把不在群里的人删掉
            removed_uids = [uid for uid in active_pool.keys() if uid not in current_member_ids]
            if removed_uids:
                for r_uid in removed_uids: del self.active_users[group_id][r_uid]
                self._save_json(self.active_file, self.active_users)
        else:
            pool = [uid for uid in active_pool.keys() if uid not in excluded]
        
        if not pool:
            yield event.plain_result("老婆池为空（需有人在30天内发言）。")
            return
        
        wife_id = random.choice(pool)
        wife_name = f"用户({wife_id})"
        
        try:
            if event.get_platform_name() == "aiocqhttp":
                # 这里已经有 members 列表了，直接查名字
                for m in members:
                    if str(m.get("user_id")) == wife_id:
                        wife_name = m.get("card") or m.get("nickname") or wife_name
                        break
        except: pass

        if group_id not in self.records["groups"]: self.records["groups"][group_id] = {"records": []}
        self.records["groups"][group_id]["records"].append({
            "user_id": user_id, "wife_id": wife_id, "wife_name": wife_name,
            "timestamp": datetime.now().isoformat()
        })
        self._save_json(self.records_file, self.records)

        avatar_url = f"https://q4.qlogo.cn/headimg_dl?dst_uin={wife_id}&spec=640"
        chain = [
            Comp.At(qq=user_id),
            Comp.Plain(f" 你的今日老婆是：\n\n【{wife_name}】\n"),
            Comp.Image.fromURL(avatar_url),
            Comp.Plain(f"\n剩余抽取次数：{max(0, daily_limit - today_count - 1)}次")
        ]
        yield event.chain_result(chain)

    @filter.command("我的老婆", alias={'抽取历史'})
    async def show_history(self, event: AstrMessageEvent):
        group_id = str(event.get_group_id())
        if not self._is_allowed_group(group_id): return

        user_id = str(event.get_sender_id())
        today = datetime.now().strftime("%Y-%m-%d")
        if self.records.get("date") != today:
            yield event.plain_result("你今天还没有抽过老婆哦~")
            return
        group_recs = self.records.get("groups", {}).get(group_id, {}).get("records", [])
        user_recs = [r for r in group_recs if r["user_id"] == user_id]
        if not user_recs:
            yield event.plain_result("你今天还没有抽过老婆哦~")
            return
        daily_limit = self.config.get("daily_limit", 3)
        res = [f"🌸 你今日的老婆记录 ({len(user_recs)}/{daily_limit})："]
        for i, r in enumerate(user_recs, 1):
            time_str = datetime.fromisoformat(r["timestamp"]).strftime("%H:%M")
            res.append(f"{i}. 【{r['wife_name']}】 ({time_str})")
        res.append(f"\n剩余次数：{max(0, daily_limit - len(user_recs))}次")
        yield event.plain_result("\n".join(res))


    @filter.command("强娶")
    async def force_marry(self, event: AstrMessageEvent):
        '''
        强娶+@要娶的那个人
        '''
        if event.is_private_chat():
            yield event.plain_result("此功能仅在群聊中可用哦~")
            return

        user_id = str(event.get_sender_id())
        group_id = str(event.get_group_id())
        now = time.time()
        
        # 从配置读取 CD 天数
        cd_days = self.config.get("force_marry_cd", 3)
        cool_down = cd_days * 24 * 3600 
        
        # --- 分群冷却核心逻辑 ---
        if group_id not in self.forced_records:
            self.forced_records[group_id] = {}
        
        last_time = self.forced_records[group_id].get(user_id, 0)
        
        if now - last_time < cool_down:
            remaining = cool_down - (now - last_time)
            days = int(remaining // 86400)
            hours = int((remaining % 86400) // 3600)
            mins = int((remaining % 3600) // 60)
            yield event.plain_result(f"你已经强娶过啦！\n请等待：{days}天{hours}小时{mins}分后再试。")
            return

        # 获取目标
        target_id = None
        for component in event.message_obj.message:
            if isinstance(component, Comp.At):
                target_id = str(component.qq)
                break
        
        if not target_id or target_id == "all":
            yield event.plain_result("请 @ 一个你想强娶的人。")
            return
        
        if target_id == user_id:
            yield event.plain_result("不能娶自己！")
            return

        # 获取名字
        target_name = f"用户({target_id})"
        try:
            if event.get_platform_name() == "aiocqhttp":
                assert isinstance(event, AiocqhttpMessageEvent)
                members = await event.bot.api.call_action('get_group_member_list', group_id=int(group_id))
                for m in members:
                    if str(m.get("user_id")) == target_id:
                        target_name = m.get("card") or m.get("nickname") or target_name
                        break
        except: pass

        # 覆盖今日记录
        today = datetime.now().strftime("%Y-%m-%d")
        if self.records.get("date") != today:
            self.records = {"date": today, "groups": {}}
        
        if group_id not in self.records["groups"]: 
            self.records["groups"][group_id] = {"records": []}
        
        # 移除该群该用户今日的其他老婆记录
        self.records["groups"][group_id]["records"] = [
            r for r in self.records["groups"][group_id]["records"] if r["user_id"] != user_id
        ]
        
        # 插入强娶记录
        self.records["groups"][group_id]["records"].append({
            "user_id": user_id, "wife_id": target_id, "wife_name": target_name,
            "timestamp": datetime.now().isoformat(), "forced": True
        })
        
        # --- 更新该群的强娶冷却时间 ---
        self.forced_records[group_id][user_id] = now
        
        self._save_json(self.records_file, self.records)
        self._save_json(self.forced_file, self.forced_records)

        avatar_url = f"https://q4.qlogo.cn/headimg_dl?dst_uin={target_id}&spec=640"
        chain = [
            Comp.At(qq=user_id),
            Comp.Plain(f" 你今天强娶了【{target_name}】哦❤️~\n请对她好一点哦~。\n"),
            Comp.Image.fromURL(avatar_url)
        ]
        yield event.chain_result(chain)


    @filter.command("关系图")
    async def show_graph(self, event: AstrMessageEvent):
        group_id = str(event.get_group_id())
        
        # 1. 读取模板文件内容
        template_path = os.path.join(self.curr_dir, "graph_template.html")
        if not os.path.exists(template_path):
            yield event.plain_result(f"错误：找不到模板文件 {template_path}")
            return
            
        with open(template_path, "r", encoding="utf-8") as f:
            graph_html = f.read()

        # 2. 获取数据 (假设你已经从 self.records 获取了 group_data)
        group_data = self.records.get("groups", {}).get(group_id, {}).get("records", [])

        group_name = "未命名群聊"
        user_map = {}
        try:
            if event.get_platform_name() == "aiocqhttp":
                # 获取群信息
                info = await event.bot.api.call_action('get_group_info', group_id=int(group_id))
                if isinstance(info, dict) and "data" in info and isinstance(info["data"], dict):
                    info = info["data"]
                group_name = info.get("group_name", "未命名群聊")

                # 获取群成员列表构建映射
                members = await event.bot.api.call_action('get_group_member_list', group_id=int(group_id))
                if isinstance(members, dict) and "data" in members and isinstance(members["data"], list):
                    members = members["data"]
                
                if isinstance(members, list):
                    for m in members:
                        uid = str(m.get("user_id"))
                        name = m.get("card") or m.get("nickname") or uid
                        user_map[uid] = name

        except Exception as e:
            logger.warning(f"获取群信息失败: {e}")

        # 3. 渲染图片
        # 根据节点数量动态计算高度，避免拥挤
        # 动态计算你想要裁剪的区域大小
        unique_nodes = set()
        for r in group_data:
            unique_nodes.add(str(r.get("user_id")))
            unique_nodes.add(str(r.get("wife_id")))
        node_count = len(unique_nodes)
        
        # 假设我们想要从左上角 (0,0) 开始，裁剪一个动态高度的区域
        clip_width = 1920
        clip_height = 1080 + (max(0, node_count - 10) * 60)

        try:
            url = await self.html_render(graph_html, {
                "group_id": group_id,
                "group_name": group_name,
                "user_map": user_map,
                "records": group_data,
                "iterations": 150
            }, options={
                "type": "jpeg",
                "quality": 100,
                "device_scale_factor": 2,
                "scale": "device",
                # 必须传齐这四个参数，且必须是 int 或 float，不能是字符串
                "clip": {
                    "x": 0,
                    "y": 0,
                    "width": clip_width,
                    "height": clip_height
                },
                "full_page": False, # 注意：使用 clip 时通常建议将 full_page 设为 False
                "device_scale_factor_level": "ultra"
            })
            yield event.image_result(url)
        except Exception as e:
            logger.error(f"渲染失败: {e}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("重置记录")
    async def reset_records(self, event: AstrMessageEvent):
        self.records = {"date": datetime.now().strftime("%Y-%m-%d"), "groups": {}}
        self._save_json(self.records_file, self.records)
        yield event.plain_result("今日抽取记录已重置！")

    @filter.command("抽老婆帮助", alias={'老婆插件帮助'})
    async def show_help(self, event: AstrMessageEvent):
        if not self._is_allowed_group(str(event.get_group_id())): return
        daily_limit = self.config.get("daily_limit", 3)
        help_text = (
           "===== 🌸 抽老婆帮助 =====\n"
            "1. 【抽老婆】：随机抽取今日老婆\n"
            "2. 【强娶 @某人】：强行更换今日老婆（3天冷却）\n"
            "3. 【我的老婆】：查看今日历史与次数\n"
            "4. 【重置记录】：(管理员) 清空数据\n"
            "5. 【关系图】：查看群友老婆的关系\n"
            f"当前每日上限：{daily_limit}次\n"
            "注：仅限30天内发言且当前在群的活跃群友。"
        )
        yield event.plain_result(help_text)

    @filter.command("debug_graph")
    async def debug_graph(self, event: AstrMessageEvent):
        '''
        调试关系图渲染
        '''
        # Mock Data
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

        # 1. Save HTML for inspection
        with open(os.path.join(self.curr_dir, "graph_template.html"), "r", encoding="utf-8") as f:
            template_content = f.read()

        import jinja2
        env = jinja2.Environment()
        template = env.from_string(template_content)
        html_content = template.render(
            group_name="Debug Group",
            records=mock_records,
            user_map=mock_user_map,
            iterations=1000 # Debug default to strict
        )
        
        debug_html_path = os.path.join(self.curr_dir, "debug_output.html")
        with open(debug_html_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        
        yield event.plain_result(f"Debugging... HTML saved to {debug_html_path}")

        # 2. Render Image using AstrBot internal API
        # Calculate dynamic height based on node count to prevent overcrowding
        unique_nodes = set()
        for r in mock_records:
            unique_nodes.add(str(r.get("user_id")))
            unique_nodes.add(str(r.get("wife_id")))
        node_count = len(unique_nodes)
        
        # Base height 1080, add 60px for every node above 10
        view_height = 1080
        if node_count > 10:
            view_height = 1080 + (node_count - 10) * 60

        try:
            url = await self.html_render(template_content, {
                "group_name": "Debug Group",
                "records": mock_records,
                "user_map": mock_user_map,
                "iterations": 1000
            }, options={
                "viewport": {"width": 1920, "height": view_height},
                "device_scale_factor": 2,
                "type": "jpeg",
                "quality": 100,
                "device_scale_factor_level": "ultra",
            })
            yield event.image_result(url)
        except Exception as e:
            logger.error(f"Debug render failed: {e}")
            yield event.plain_result(f"Render failed: {e}")

    async def terminate(self):
        self._save_json(self.records_file, self.records)
        self._save_json(self.active_file, self.active_users)
        self._save_json(self.forced_file, self.forced_records)