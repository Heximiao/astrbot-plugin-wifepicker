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

@register("random_wife", "Gemini", "活跃成员抽老婆(全局限制版)", "2.6.0")
class RandomWifePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None): 
        super().__init__(context)
        self.config = config 
        
        # 数据存储相对路径
        self.data_dir = os.path.join("data", "plugin_data", "random_wife")
        self.records_file = os.path.join(self.data_dir, "wife_records.json")
        self.active_file = os.path.join(self.data_dir, "active_users.json") 
        
        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir, exist_ok=True)
            
        self.records = self._load_json(self.records_file, {"date": "", "groups": {}})
        self.active_users = self._load_json(self.active_file, {})
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

        active_pool = self.active_users.get(group_id, {})
        excluded = {str(uid) for uid in self.config.get("excluded_users", [])}
        excluded.update([bot_id, user_id, "0"]) # 确保不抽中 "0"
        pool = [uid for uid in active_pool.keys() if uid not in excluded]
        
        if not pool:
            yield event.plain_result("老婆池为空（需有人在30天内发言）。")
            return
        
        wife_id = random.choice(pool)
        wife_name = f"用户({wife_id})"
        
        try:
            if event.get_platform_name() == "aiocqhttp":
                assert isinstance(event, AiocqhttpMessageEvent)
                members = await event.bot.api.call_action('get_group_member_list', group_id=int(group_id))
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
            "2. 【我的老婆】：查看今日历史与次数\n"
            "3. 【重置记录】：(管理员) 清空数据\n"
            f"当前每日上限：{daily_limit}次\n"
            "注：仅限30天内发言的活跃群友。"
        )
        yield event.plain_result(help_text)

    async def terminate(self):
        self._save_json(self.records_file, self.records)
        self._save_json(self.active_file, self.active_users)