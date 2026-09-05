import json
import os
import re
import tempfile

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent


def load_json(path: str, default: object):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"读取 JSON 数据失败，使用默认值: path={path}, error={e}")
        return default


def save_json(path: str, data: object, records_file: str = None, config: object = None):
    temp_path = None
    try:
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)

        # Write beside the destination so os.replace remains atomic on macOS,
        # Linux and Windows, even when the data directory is on another volume.
        fd, temp_path = tempfile.mkstemp(
            prefix=f".{os.path.basename(path)}.",
            suffix=".tmp",
            dir=directory,
        )
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())

        os.replace(temp_path, path)
        temp_path = None
        return True
    except Exception as e:
        logger.error(f"保存 JSON 数据失败: path={path}, error={e}")
        return False
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def normalize_user_id_set(values: object) -> set[str]:
    if not isinstance(values, (list, tuple, set)):
        return set()
    return {str(v) for v in values if str(v).strip()}


def extract_target_id_from_message(event: AstrMessageEvent, plugin=None) -> str | None:
    from .platforms.telegram_support import is_telegram_event, resolve_target

    if is_telegram_event(event):
        return resolve_target(plugin, event) if plugin is not None else None
    self_id = str(event.get_self_id() or "")
    mentions: list[str] = []

    for component in getattr(event.message_obj, "message", []):
        if isinstance(component, Comp.At):
            qq = str(component.qq)
            if qq:
                mentions.append(qq)

    if mentions:
        for qq in mentions:
            if qq != self_id:
                if len(mentions) > 1 or mentions[0] == self_id:
                    logger.info(
                        f"[wifepicker] mention target resolved: mentions={mentions}, "
                        f"self_id={self_id}, selected={qq}"
                    )
                return qq

        logger.info(
            f"[wifepicker] mention target resolved to bot/self only: "
            f"mentions={mentions}, self_id={self_id}"
        )
        return mentions[0]

    raw_text = str(getattr(event, "message_str", "") or "")
    cq_mentions = re.findall(r"\[CQ:at,qq=(\d+)\]", raw_text)
    if cq_mentions:
        for qq in cq_mentions:
            if qq != self_id:
                if len(cq_mentions) > 1 or cq_mentions[0] == self_id:
                    logger.info(
                        f"[wifepicker] cq target resolved: mentions={cq_mentions}, "
                        f"self_id={self_id}, selected={qq}"
                    )
                return qq

        logger.info(
            f"[wifepicker] cq target resolved to bot/self only: "
            f"mentions={cq_mentions}, self_id={self_id}"
        )
        return cq_mentions[0]

    plain_mentions = re.findall(r"@(\d{5,12})", raw_text)
    if plain_mentions:
        for qq in plain_mentions:
            if qq != self_id:
                if len(plain_mentions) > 1 or plain_mentions[0] == self_id:
                    logger.info(
                        f"[wifepicker] plain target resolved: mentions={plain_mentions}, "
                        f"self_id={self_id}, selected={qq}"
                    )
                return qq

        logger.info(
            f"[wifepicker] plain target resolved to bot/self only: "
            f"mentions={plain_mentions}, self_id={self_id}"
        )
        return plain_mentions[0]

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
