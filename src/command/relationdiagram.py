import asyncio
import base64
import os
from collections import OrderedDict
from time import monotonic

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from ..core import get_group_records
from ..utils import is_allowed_group
from ..debug import debug_log
from ..i18n import tr
from ..platforms.user_profiles import (
    get_avatar_sources,
    get_display_name,
    get_platform_group_name,
    get_platform_members,
)


_AVATAR_CACHE_TTL = 10800
_AVATAR_CACHE_MAX_ENTRIES = 256
_AVATAR_CACHE_MAX_ENTRY_SIZE = 128 * 1024
_AVATAR_CACHE: OrderedDict[str, tuple[float, str]] = OrderedDict()


async def _inline_avatars(avatars):
    """把头像下载并转成 data URI。

    部分 T2I 渲染器（例如本地快照式渲染）只等待固定时间就截图，
    而 vis-network 的头像是外链异步加载的，往往来不及绘制，
    节点上就只剩边框和一个空心圆。转成 data URI 后，页面解码
    头像时无需再等待外链请求。
    """
    result = {}
    if not avatars:
        return result

    try:
        import aiohttp
    except Exception:
        return {k: v for k, v in avatars.items() if v}

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        ),
        "Referer": "https://qzone.qq.com/",
    }
    timeout = aiohttp.ClientTimeout(total=10)

    pending = {}
    for uid, url in avatars.items():
        if not url:
            continue
        if url.startswith("data:image/"):
            result[uid] = url
            continue
        cached = _AVATAR_CACHE.get(url)
        if cached and cached[0] > monotonic():
            _AVATAR_CACHE.move_to_end(url)
            result[uid] = cached[1]
        else:
            _AVATAR_CACHE.pop(url, None)
            pending.setdefault(url, []).append(uid)

    if not pending:
        return result

    sem = asyncio.Semaphore(8)

    async def download(session, url, user_ids):
        uid = user_ids[0]
        try:
            async with sem:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        logger.warning(f"[avatar] {uid} HTTP {resp.status}")
                        return
                    raw_bytes = await resp.read()
                    if len(raw_bytes) < 100:
                        logger.warning(f"[avatar] {uid} 内容过短 {len(raw_bytes)}B")
                        return
                    mime = resp.headers.get("Content-Type", "image/jpeg")
                    if "image" not in mime:
                        mime = "image/jpeg"
            data_uri = f"data:{mime};base64,{base64.b64encode(raw_bytes).decode()}"
            if len(data_uri) <= _AVATAR_CACHE_MAX_ENTRY_SIZE:
                _AVATAR_CACHE[url] = (monotonic() + _AVATAR_CACHE_TTL, data_uri)
                _AVATAR_CACHE.move_to_end(url)
                if len(_AVATAR_CACHE) > _AVATAR_CACHE_MAX_ENTRIES:
                    _AVATAR_CACHE.popitem(last=False)
            for uid in user_ids:
                result[uid] = data_uri
        except Exception as exc:
            logger.warning(f"[avatar] {uid} 下载失败: {exc}")

    async with aiohttp.ClientSession(
        timeout=timeout, headers=headers, cookie_jar=aiohttp.DummyCookieJar()
    ) as session:
        await asyncio.gather(
            *(download(session, url, user_ids) for url, user_ids in pending.items())
        )
    logger.info(f"[avatar] inline {len(result)}/{len(avatars)} 成功")
    return result


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

    group_name = tr(plugin_instance, "proposal_image_group")
    user_map = {}
    avatar_map = {}
    try:
        group_name = await get_platform_group_name(event, group_name, plugin=plugin_instance)
        members = await get_platform_members(plugin_instance, event)
        for member in members:
            uid = str(member.get("user_id"))
            name = member.get("card") or member.get("nickname") or uid
            user_map[uid] = name

    except Exception as e:
        logger.warning(f"获取群组或成员信息失败: {e}")

    debug_log(plugin_instance, "relationdiagram", f"group={group_id} group_name={group_name!r}")

    # 3. 渲染图片
    unique_nodes = set()
    for r in group_data:
        unique_nodes.add(str(r.get("user_id")))
        unique_nodes.add(str(r.get("wife_id")))
    avatars = await get_avatar_sources(plugin_instance, event, unique_nodes)
    if plugin_instance.config.get("avatar_inline", False):
        avatars = await _inline_avatars(avatars)
    for uid in unique_nodes:
        user_map.setdefault(uid, get_display_name(plugin_instance, event, uid))
        avatar_url = avatars.get(uid)
        if avatar_url:
            avatar_map[uid] = avatar_url

    # Keep small graphs in landscape; grow the canvas for larger groups so
    # fitting all nodes does not make avatars and labels too small to read.
    render_width = 1280
    render_height = 720 + max(0, len(unique_nodes) - 20) * 24

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
                "render_width": render_width,
                "render_height": render_height,
            },
            options={
                "type": "png",
                "quality": None,
                # Match the template's dynamic canvas. A screenshot clip
                # does not set the viewport used by vis-network for layout.
                "viewport_width": render_width,
                "viewport_height": render_height,
                "full_page": True,
                # Layout stays in CSS pixels; device scaling increases the
                # canvas and screenshot resolution without changing its bounds.
                "scale": "device",
                "device_scale_factor_level": "ultra",
            },
        )
        yield event.image_result(url)
    except Exception as e:
        logger.error(f"渲染失败: {e}")
