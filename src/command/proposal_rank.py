"""Rolling proposal leaderboard with HTML rendering and a text fallback."""

import os
import random

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from ..db import proposal_scope
from ..i18n import tr
from ..platforms.user_profiles import (
    get_avatar_sources, get_display_name, get_platform_group_name, get_platform_members,
)
from ..utils import is_allowed_group


async def cmd_proposal_ranking(plugin, event: AstrMessageEvent):
    if event.is_private_chat():
        yield event.plain_result(tr(plugin, "ranking_private"))
        return
    if not is_allowed_group(str(event.get_group_id()), plugin.config):
        return
    try:
        store = getattr(plugin, "proposal_store", None)
        if store is None:
            raise RuntimeError("Proposal database unavailable")
        rows = await store.ranking(proposal_scope(event))
    except Exception as exc:
        logger.error(f"[wifepicker] 查询求婚榜单失败: {exc}")
        yield event.plain_result(tr(plugin, "proposal_ranking_error"))
        return
    if not rows:
        yield event.plain_result(tr(plugin, "proposal_ranking_empty"))
        return

    members = {}
    try:
        for member in await get_platform_members(plugin, event):
            uid = str(member.get("user_id"))
            members[uid] = member.get("card") or member.get("nickname") or ""
    except Exception:
        pass

    lines = [tr(plugin, "proposal_ranking_title")]
    ranking = []
    previous_count = None
    rank = 0
    for index, row in enumerate(rows, 1):
        if row["proposal_count"] != previous_count:
            rank = index
        previous_count = row["proposal_count"]
        uid = row["proposer_id"]
        name = members.get(uid) or get_display_name(
            plugin, event, uid, fallback=row["proposer_name"] or uid,
        )
        # Keep nicknames on one line so they cannot inject fake ranking rows.
        name = " ".join(str(name).split())
        lines.append(tr(plugin, "proposal_ranking_item", rank=rank, name=name,
                        count=row["proposal_count"], success=row["success_count"]))
        ranking.append(dict(row, name=name, rank=rank, avatar_url=None))

    try:
        try:
            avatars = await get_avatar_sources(plugin, event, [user["proposer_id"] for user in ranking])
        except Exception as exc:
            logger.warning(f"[wifepicker] 榜单头像获取失败，使用昵称占位: {exc}")
            avatars = {}
        for user in ranking:
            user["avatar_url"] = avatars.get(user["proposer_id"])
        group_name = await get_platform_group_name(
            event, fallback=tr(plugin, "proposal_image_group"), plugin=plugin,
        )
        labels = {
            key: tr(plugin, f"proposal_image_{key}")
            for key in ("title", "eyebrow", "tagline", "period", "winner", "stamp",
                        "players", "proposals", "successes", "unit", "signoff", "imprint")
        }
        quote = tr(plugin, f"proposal_winner_quote_{random.randrange(1, 7)}")
        template_path = os.path.join(plugin.curr_dir, "template", "proposal_ranking.html")
        with open(template_path, encoding="utf-8") as template:
            template_content = template.read()
        url = await plugin.html_render(
            template_content,
            {"winner": ranking[0], "others": ranking[1:], "group_name": group_name,
             "labels": labels, "winner_quote": quote},
            options={
                # Let the renderer measure the whole document. A short initial
                # viewport avoids adding blank space below a small leaderboard.
                "type": "png", "quality": None, "full_page": True,
                "viewport_width": 540, "viewport_height": 1,
                "scale": "device", "device_scale_factor_level": "ultra",
            },
        )
        yield event.image_result(url)
        return
    except Exception as exc:
        logger.error(f"[wifepicker] 渲染求婚榜单失败，回退文字: {exc}")
    yield event.plain_result("\n".join(lines))
