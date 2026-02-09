import os
import json
import re
from astrbot.api import logger
import astrbot.api.message_components as Comp
from astrbot.api.event import AstrMessageEvent

def load_json(path: str, default: object):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path: str, data: object, records_file: str = None, config: object = None):
    try:
        # === 全局记录总量清理逻辑 ===
        # 这里的 config 传入的是插件的 self.config 对象
        if records_file and path == records_file and "groups" in data:
            max_total = config.get("max_records", 500) if config else 500
            all_recs = []
            # 展平所有记录
            for gid, gdata in data["groups"].items():
                for r in gdata.get("records", []):
                    r["_gid"] = gid  # 临时记录所属群
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
                    if gid not in new_groups:
                        new_groups[gid] = {"records": []}
                    new_groups[gid]["records"].append(r)
                data["groups"] = new_groups

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存数据失败: {e}")

def normalize_user_id_set(values: object) -> set[str]:
    if not isinstance(values, (list, tuple, set)):
        return set()
    return {str(v) for v in values if str(v).strip()}

def extract_target_id_from_message(event: AstrMessageEvent) -> str | None:
    for component in event.message_obj.message:
        if isinstance(component, Comp.At):
            return str(component.qq)

    raw_text = str(getattr(event, "message_str", "") or "")
    cq_at = re.search(r"\[CQ:at,qq=(\d+)\]", raw_text)
    if cq_at:
        return cq_at.group(1)

    plain_at = re.search(r"@(\d{5,12})", raw_text)
    if plain_at:
        return plain_at.group(1)

    return None
def is_allowed_group(group_id: str, config: object) -> bool:
    whitelist = config.get("whitelist_groups", [])
    blacklist = config.get("blacklist_groups", [])
    gid_str = str(group_id)
    if gid_str in {str(g) for g in blacklist}:
        return False
    if whitelist and gid_str not in {str(g) for g in whitelist}:
        return False
    return True

def resolve_member_name(members: list[dict], user_id: str, fallback: str) -> str:
    for m in members:
        if str(m.get("user_id")) == str(user_id):
            return m.get("card") or m.get("nickname") or fallback
    return fallback