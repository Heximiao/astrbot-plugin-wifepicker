"""Opt-in affinity collection, bounded background processing and local scoring."""

import asyncio
import re
import time
from pathlib import Path
from uuid import uuid4

from .model import LocalSentiment
from ..db.affinity import AffinityStore
from ..db.proposals import proposal_scope


def settings(plugin):
    config = getattr(plugin, "config", None)
    result = config.get("group_affinity", {}) if config is not None else {}
    return result if isinstance(result, dict) else {}


def enabled(plugin):
    return bool(settings(plugin).get("enabled", False))


def allowed_group(plugin, group_id):
    """The feature list can narrow global access, never widen it."""
    from ..utils import is_allowed_group

    if not is_allowed_group(str(group_id), plugin.config):
        return False
    return is_allowed_group(str(group_id), settings(plugin))


def value(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def component_kind(component):
    kind = value(component, "type", "")
    return str(value(kind, "value", kind)).lower() or type(component).__name__.lower()


def clean_id(user_id):
    user_id = str(user_id or "").strip()
    return user_id if user_id.casefold() not in {"", "0", "all", "none", "null"} else ""


COMMAND = re.compile(
    r"^\s*[/!！]|^\s*(?:查看好感度|调整好感度|affinity|setaffinity|"
    r"求婚|强娶|今日老婆|抽老婆|挑选老婆|我的老婆|分手|离婚|关系图|"
    r"抽老婆帮助|老婆插件帮助|重置记录|重置强娶时间|重置求婚时间|"
    r"花心大萝卜榜|海王榜|花心榜|rbq排行|qh|propose|qiangqu|forcemarry|"
    r"jrlp|dailywife|wife|pickwife|mywife|breakup|relations|wifehelp)(?=\s|@|<|\[|$)", re.I,
)


def snapshot(plugin, event, debug=None):
    """Read explicit transport metadata only; never infer adjacent speakers."""
    def skip(reason):
        if debug:
            debug(f"capture_skipped reason={reason}")
        return None

    if not enabled(plugin) or event.is_private_chat() or not event.get_group_id():
        return skip("disabled_or_private_or_missing_group")
    # Imported here to keep the scoring/storage layer independent of AstrBot.
    if not allowed_group(plugin, event.get_group_id()):
        return skip("group_access_denied")
    sender = clean_id(event.get_sender_id())
    bot = clean_id(event.get_self_id())
    if not sender or sender == bot:
        return skip("invalid_sender_or_bot_self")
    obj = event.message_obj
    raw = value(obj, "raw_message")
    author = value(raw, "author") or {}
    if value(author, "bot", False):
        return skip("bot_author")
    components = value(obj, "message", []) or []
    sender_profile = value(obj, "sender")
    sender_name = value(sender_profile, "card") or value(sender_profile, "nickname")
    if not sender_name and callable(getattr(event, "get_sender_name", None)):
        sender_name = event.get_sender_name()
    names = {sender: sender_name} if sender_name else {}
    group_name = value(value(obj, "group"), "group_name") or value(raw, "group_name", "")
    plains, targets, replies = [], set(), []
    has_poke, poke_targets = False, set()
    for component in components:
        kind = component_kind(component)
        if kind == "plain":
            plains.append(str(value(component, "text", "")))
        elif kind == "at":
            target = clean_id(value(component, "qq"))
            if target:
                targets.add(target)
                if target != sender and value(component, "name"):
                    names.setdefault(target, value(component, "name"))
        elif kind == "reply":
            target = clean_id(value(component, "sender_id"))
            if target:
                targets.add(target)
                if target != sender and value(component, "sender_nickname"):
                    names.setdefault(target, value(component, "sender_nickname"))
            elif value(component, "id") is not None:
                replies.append(str(value(component, "id")))
        elif kind == "poke":
            has_poke = True
            target = clean_id(value(component, "id")) or clean_id(value(component, "qq"))
            if target:
                poke_targets.add(target)
    if value(raw, "notice_type") == "notify" and value(raw, "sub_type") == "poke":
        has_poke = True
        target = clean_id(value(raw, "target_id"))
        if target:
            poke_targets.add(target)
    if has_poke:
        if str(event.get_platform_name()).lower() != "aiocqhttp":
            return skip("unsupported_poke_platform")
        if len(poke_targets) != 1 or poke_targets & {sender, bot}:
            return skip("invalid_or_self_or_bot_poke_target")
        return {"scope": proposal_scope(event), "message_id": str(value(obj, "message_id") or uuid4().hex),
                "sender": sender, "body": "", "targets": poke_targets, "replies": [],
                "kind": "poke", "now": time.time(), "bot": bot, "names": names, "group_name": group_name}
    body = " ".join(plains).strip() if components else str(getattr(event, "message_str", "") or "").strip()
    # Official QQ may encode mentions in text instead of At components.
    for target in re.findall(r"<@!?([A-Za-z0-9_-]+)>|\[CQ:at,qq=([^,\]]+)[^\]]*\]", body):
        targets.update(filter(None, map(clean_id, target)))
    body = re.sub(r"<@!?[A-Za-z0-9_-]+>|\[CQ:[^\]]+\]", "", body).strip()
    if str(event.get_platform_name()).lower() == "telegram":
        message = value(raw, "effective_message") or value(raw, "message") or raw
        author = value(message, "from_user") or value(message, "from")
        if value(message, "sender_chat") or value(author, "is_bot", False):
            return skip("telegram_channel_or_bot_author")
        bot = clean_id(value(getattr(event, "client", None), "id", bot))
        reply = value(message, "reply_to_message")
        if reply and not value(reply, "sender_chat"):
            reply_author = value(reply, "from_user") or value(reply, "from")
            if not value(reply_author, "is_bot", False):
                target = clean_id(value(reply_author, "id"))
                if target:
                    targets.add(target)
        for entity in value(message, "entities", []) or []:
            if value(entity, "type") == "text_mention":
                target = clean_id(value(value(entity, "user"), "id"))
                if target:
                    targets.add(target)
            elif value(entity, "type") == "mention":
                text = value(message, "text", "") or ""
                start, length = value(entity, "offset", 0), value(entity, "length", 0)
                username = text.encode("utf-16-le")[start * 2:(start + length) * 2].decode("utf-16-le").lstrip("@").lower()
                group = getattr(plugin, "official_profiles", {}).get(proposal_scope(event)[0], {}).get(str(event.get_group_id()), {})
                matches = [uid for uid, profile in group.items() if str(profile.get("username", "")).lower() == username]
                if len(matches) == 1:
                    targets.add(matches[0])
        body = re.sub(r"(?<!\S)@\S+", "", body).strip()
    reference = value(raw, "message_reference") or value(raw, "reference")
    reference_id = value(reference, "message_id")
    if reference_id:
        replies.append(str(reference_id))
    targets.discard(bot)
    targets.discard(sender)
    if sender == bot or not body or COMMAND.search(body):
        return skip("bot_self" if sender == bot else "empty_body" if not body else "command")
    message_id = str(value(obj, "message_id") or uuid4().hex)
    return {"scope": proposal_scope(event), "message_id": message_id, "sender": sender,
            "body": body[:512], "targets": targets, "replies": replies[:2],
            "kind": "reply" if replies or any(component_kind(c) == "reply" for c in components) else "mention",
            "now": time.time(), "bot": bot, "names": names, "group_name": group_name}


def directed_sentiment(body, probabilities):
    """Only let polarity affect relationships when a person-directed cue exists.

    Deliberately abstains on complaints about food/work and ambiguous sarcasm.
    This is a conservative game heuristic, not a claim about anyone's feelings.
    """
    if not probabilities:
        return 0.0
    text = body.casefold()
    positive = bool(re.search(r"谢谢你|感谢你|多亏你|辛苦你|有你真好|喜欢你|爱你|你真好|thank you|thanks for|love you", text))
    negative = bool(re.search(r"讨厌你|别烦我|别找我|你有病|你烦不烦|你闭嘴|hate you|leave me alone|shut up", text))
    if positive and not negative and probabilities.get("positive", 0) >= 0.75:
        return probabilities["positive"]
    if negative and not positive and probabilities.get("negative", 0) >= 0.8:
        return -probabilities["negative"]
    return 0.0


class AffinityService:
    def __init__(self, plugin, logger):
        self.plugin = plugin
        self.logger = logger
        self.path = Path(plugin.data_dir) / "wifepicker.db"
        self.store = None
        self.init_lock = asyncio.Lock()
        self.queue = asyncio.Queue(maxsize=256)
        self.model_queue = asyncio.Queue(maxsize=32)
        self.model = LocalSentiment(plugin.data_dir, self.logger, self.debug)
        self.last_cleanup = 0
        self.last_overflow = 0
        self.task = asyncio.create_task(self._run())
        self.model_task = asyncio.create_task(self._classify_loop())
        self.logger.info(f"[wifepicker.affinity] 实验性功能 enabled={enabled(plugin)} "
                         f"small_model={bool(settings(plugin).get('use_small_model', False))} "
                         f"aggressive={bool(settings(plugin).get('aggressive', False))}")

    def debug(self, message):
        if self.plugin.config is not None and self.plugin.config.get("debug_enabled", False):
            # Match the plugin's existing debug_log: INFO is visible in AstrBot's console.
            self.logger.info(f"[wifepicker.affinity.debug] {message}")

    async def database(self):
        async with self.init_lock:
            if self.store is None:
                self.store = await asyncio.to_thread(AffinityStore, self.path)
                self.debug(f"database_ready path={self.path} retention_days=60")
            return self.store

    def observe(self, event):
        if getattr(event, "_wifepicker_affinity_seen", False):
            return
        try:
            item = snapshot(self.plugin, event, self.debug)
            if item:
                event._wifepicker_affinity_seen = True
                self.queue.put_nowait(item)
                self.debug(f"queued scope={item['scope']} message={item['message_id']} sender={item['sender']} "
                           f"targets={sorted(item['targets'])} replies={item['replies']} chars={len(item['body'])} queue={self.queue.qsize()}")
        except asyncio.QueueFull:
            if time.monotonic() - self.last_overflow > 60:
                self.logger.warning("[wifepicker] 好感度消息队列已满，跳过部分消息")
                self.last_overflow = time.monotonic()
        except Exception as exc:
            self.logger.warning(f"[wifepicker] 好感度消息识别失败: {type(exc).__name__}")

    async def _process(self, item):
        if not enabled(self.plugin):
            self.debug("process_skipped reason=feature_disabled_after_enqueue")
            return
        if not allowed_group(self.plugin, item["scope"][2]):
            self.debug(f"process_skipped scope={item['scope']} reason=group_access_denied_after_enqueue")
            return
        db = await self.database()
        if item.get("names") or item.get("group_name"):
            await asyncio.to_thread(db.profiles, item["scope"], item.get("names", {}), item["now"], item.get("group_name", ""),
                                    authoritative_ids=(item["sender"],))
        if item["kind"] == "poke":
            targets = item["targets"]
            if len(targets) != 1 or targets & {item["sender"], item["bot"]}:
                return
            target = next(iter(targets))
            interaction_id = await asyncio.to_thread(
                db.interaction, item["scope"], item["message_id"], item["sender"], target,
                "", "poke", 0, item["now"], bool(settings(self.plugin).get("aggressive", False)),
            )
            self.debug(f"poke scope={item['scope']} source={item['sender']} target={target} "
                       f"saved_id={interaction_id} reason={'saved' if interaction_id else 'duplicate_or_30min_cooldown_or_pair_daily_limit'}")
            return
        args = (item["scope"], item["message_id"], item["sender"], item["body"], item["now"])
        if not await asyncio.to_thread(db.message, *args):
            self.debug(f"message_skipped id={item['message_id']} reason=duplicate_or_daily_limit")
            return
        targets = set(item["targets"])
        for reply in item["replies"]:
            sender = await asyncio.to_thread(db.reply_sender, item["scope"], reply, item["now"])
            self.debug(f"reply_resolved message={item['message_id']} reply={reply} sender={sender}")
            if sender and sender not in {item["sender"], item["bot"]}:
                targets.add(sender)
        # Group-addressed messages are not assigned to an arbitrary first user.
        if len(targets) != 1 or len("".join(item["body"].split())) < 2:
            self.debug(f"interaction_skipped message={item['message_id']} reason=ambiguous_or_no_target_or_short targets={sorted(targets)}")
            return
        target = next(iter(targets))
        interaction_id = await asyncio.to_thread(
            db.interaction, item["scope"], item["message_id"], item["sender"], target,
            item["body"], item["kind"], 0, item["now"], bool(settings(self.plugin).get("aggressive", False)),
        )
        self.debug(f"interaction message={item['message_id']} source={item['sender']} target={target} "
                   f"kind={item['kind']} saved_id={interaction_id} reason={'saved' if interaction_id else 'deduplicated_or_ten_minute_cooldown'}")
        if interaction_id and settings(self.plugin).get("use_small_model", False) and self.model.ready:
            try:
                self.model_queue.put_nowait((interaction_id, item["body"], item["scope"]))
            except asyncio.QueueFull:
                self.debug("sentiment_skipped reason=model_queue_full; basic_evidence_saved=true")

    async def _classify_loop(self):
        while True:
            interaction_id, body, scope = await self.model_queue.get()
            try:
                if (enabled(self.plugin) and settings(self.plugin).get("use_small_model", False)
                        and allowed_group(self.plugin, scope[2])):
                    probabilities = await self.model.classify(body)
                    sentiment = directed_sentiment(body, probabilities)
                    self.debug(f"sentiment interaction={interaction_id} probabilities={probabilities} directed_contribution={sentiment}")
                    # Config may change while inference is awaiting the worker.
                    if (sentiment and enabled(self.plugin) and settings(self.plugin).get("use_small_model", False)
                            and allowed_group(self.plugin, scope[2])):
                        db = await self.database()
                        await asyncio.to_thread(db.set_sentiment, interaction_id, sentiment,
                                                bool(settings(self.plugin).get("aggressive", False)))
                else:
                    self.debug(f"sentiment_skipped interaction={interaction_id} scope={scope} reason=disabled_or_group_access_denied")
            except Exception as exc:
                self.logger.warning(f"[wifepicker] 好感度情绪处理失败: {type(exc).__name__}")
            finally:
                self.model_queue.task_done()

    async def _run(self):
        while True:
            try:
                config = settings(self.plugin)
                if config.get("enabled") and config.get("use_small_model"):
                    self.model.ensure(config)
                elif self.model.task or self.model.ready:
                    await self.model.close()
                if time.monotonic() - self.last_cleanup >= 3600:
                    # Retention maintenance continues even after disabling collection.
                    if self.store or self.path.exists():
                        db = await self.database()
                        deleted = await asyncio.to_thread(db.cleanup)
                        self.debug(f"retention_cleanup deleted={deleted}")
                        if any(deleted.values()):
                            self.logger.info(f"[wifepicker.affinity] 已清理超过60天的数据: {deleted}")
                    self.last_cleanup = time.monotonic()
                try:
                    item = await asyncio.wait_for(self.queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    continue
                try:
                    await self._process(item)
                finally:
                    self.queue.task_done()
            except Exception as exc:
                self.logger.warning(f"[wifepicker] 好感度后台处理失败: {type(exc).__name__}")
                await asyncio.sleep(1)

    async def game(self, event, kind, actor, target, event_id):
        if not enabled(self.plugin):
            return
        if event.is_private_chat() or not allowed_group(self.plugin, event.get_group_id()):
            self.debug(f"game_skipped group={event.get_group_id()} kind={kind} reason=private_or_group_access_denied")
            return
        if not clean_id(actor) or not clean_id(target) or str(actor) == str(target):
            return
        try:
            db = await self.database()
            await asyncio.to_thread(db.game, proposal_scope(event), str(event_id), kind, str(actor), str(target), time.time(),
                                    bool(settings(self.plugin).get("aggressive", False)))
            self.debug(f"game scope={proposal_scope(event)} kind={kind} actor={actor} target={target} event={event_id}")
        except Exception as exc:
            self.logger.warning(f"[wifepicker] 好感度游戏事件保存失败: {type(exc).__name__}")

    async def query(self, event, source, target):
        if not enabled(self.plugin) or event.is_private_chat() or not allowed_group(self.plugin, event.get_group_id()):
            self.debug(f"query_skipped group={event.get_group_id()} reason=disabled_or_group_access_denied")
            return None
        db = await self.database()
        result = await asyncio.to_thread(db.score, proposal_scope(event), source, target,
                                        bool(settings(self.plugin).get("aggressive", False)))
        self.debug(f"query scope={proposal_scope(event)} direction={source}->{target} result={result}")
        return result

    async def remember_names(self, event, names):
        if enabled(self.plugin) and not event.is_private_chat() and allowed_group(self.plugin, event.get_group_id()):
            db = await self.database()
            await asyncio.to_thread(db.profiles, proposal_scope(event), names, time.time())

    async def adjust(self, event, source, target, value, relative):
        if not enabled(self.plugin) or event.is_private_chat() or not allowed_group(self.plugin, event.get_group_id()):
            self.debug(f"manual_adjust_denied group={event.get_group_id()} reason=disabled_or_group_access_denied")
            raise PermissionError("Affinity is disabled or the group is not allowed")
        db = await self.database()
        result = await asyncio.to_thread(db.adjust, proposal_scope(event), source, target, value, relative,
                                        bool(settings(self.plugin).get("aggressive", False)))
        self.debug(f"manual_adjust scope={proposal_scope(event)} direction={source}->{target} "
                   f"value={value} relative={relative} score={result}")
        return result

    async def close(self):
        try:
            await asyncio.wait_for(self.queue.join(), timeout=5)
        except asyncio.TimeoutError:
            pass
        for task in (self.task, self.model_task):
            task.cancel()
        await asyncio.gather(self.task, self.model_task, return_exceptions=True)
        await self.model.close()
