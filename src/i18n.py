"""Runtime translations for chat replies.

AstrBot's plugin i18n files localize Dashboard metadata and configuration UI.
Chat messages are localized here using the plugin's explicit language setting.
"""

from __future__ import annotations

from typing import Any


SUPPORTED_LANGUAGES = {"zh-CN", "en-US", "ja-JP"}
DEFAULT_LANGUAGE = "zh-CN"


MESSAGES: dict[str, dict[str, str]] = {
    "zh-CN": {
        "group_only": "此功能仅在群聊中可用哦~",
        "wife_existing": " 你今天已经有老婆了哦❤️~\n她是：【{wife_name}】\n",
        "daily_limit": "你今天已经抽了{count}次老婆了，明天再来吧！",
        "wife_pool_empty": "老婆池为空（需有人在{days}天内发言）。",
        "draw_result": " 你的今日老婆是：\n\n【{wife_name}】\n",
        "draw_suffix": "\n请好好对待她哦❤️~ \n剩余抽取次数：{remaining}次",
        "history_empty": "你今天还没有抽过老婆哦~",
        "history_title": "🌸 你今日的老婆记录 ({used}/{limit})：",
        "history_item": "{index}. 【{wife_name}】 ({time})",
        "history_remaining": "\n剩余次数：{remaining}次",
        "pick_already_done": "你今天已经抽过/挑选过老婆了，明天再来吧！",
        "pick_in_progress": "你已经在挑选中了，请回复编号、重新挑选或放弃。",
        "pick_cooldown": "「挑选老婆」每24小时只能使用一次，还剩 {minutes} 分钟，请稍后再试。",
        "pick_batches_used": "你今天的候选批次已用完（{limit}批），可以使用「今日老婆」随机抽取。",
        "pick_list_title": "🌹 请选择你的老婆（回复编号，30秒内有效）：",
        "pick_list_footer": "\n回复「重新挑选」换一批，回复「放弃」取消。",
        "pick_success": " 你挑选了【{wife_name}】作为你的今日老婆！❤️\n请好好对待她哦~",
        "pick_retry_limit": "已达到候选批次上限（{limit}批），请从当前候选中选择或放弃。",
        "wife_pool_retry": "老婆池为空，请稍后再试。",
        "pick_abandon_used": "已放弃本次挑选，今天的候选批次已用完；仍可使用「今日老婆」随机抽取。",
        "pick_abandon": "已放弃本次挑选，本次不占每日抽取名额；剩余候选批次：{remaining}。",
        "unlimited": "不限",
        "pick_invalid_number": "请输入有效的编号（1-{max}）哦~",
        "breakup_group_only": "分手只能在群聊中进行哦~",
        "breakup_cooldown": "你还在分手冷却期内，请等待 {remaining} 后再试。",
        "breakup_no_wife": "你现在还没有老婆，无法分手哦。",
        "breakup_forced": "强娶的老婆不能分手！",
        "breakup_success": "你已经和{wives}分手了。分手指令将在 72 小时后恢复使用。",
        "force_propose_cd": "你还在求婚冷却期内，请等待 {remaining} 后再强娶。",
        "force_self_cd": "你已经强娶过啦！\n请等待：{remaining}后再试。\n(重置时间：{reset_time})",
        "force_need_target": "请 @ 一个你想强娶的人。",
        "marry_self": "不能娶自己！",
        "force_target_propose_cd": "对方还在求婚冷却期内，请等待 {remaining} 后再强娶。",
        "force_excluded": "该用户在强娶排除列表中，无法被强娶。",
        "force_success": " 你今天强娶了【{target_name}】哦❤️~\n请对她好一点哦~。\n",
        "propose_group_only": "求婚只能在群聊中进行哦~",
        "propose_need_target": "请 @ 一个你想求婚的人。",
        "propose_self": "不能向自己求婚哦！",
        "propose_force_cd_self": "你还在强娶冷却期内，暂时不能求婚。",
        "propose_force_cd_target": "对方还在强娶冷却期内，暂时不能接受求婚。",
        "propose_cd_self": "你还在求婚冷却期内，请等待 {remaining} 后再试。",
        "propose_cd_target": "对方还在求婚冷却期内，请等待 {remaining} 后再试。",
        "propose_pending": "你已经有一个待处理的求婚了，请等待对方回复或 30 秒后再试。",
        "propose_sent": "🌹 @{sender_name} 向【{target_name}】发起了求婚！\n请在 30 秒内回复“同意”来接受，或回复“拒绝”来拒绝。",
        "propose_timeout": " ...很遗憾，求婚超时了，对方似乎没有答应...",
        "force_cancelled": "已取消强娶。",
        "propose_invalid": "求婚已失效：你们中有人进入了强娶冷却期。",
        "propose_accepted": "🎉 恭喜！{target_name} 接受了 {proposer_name} 的求婚！\n你们已正式结为夫妻！",
        "propose_rejected": " 很遗憾，【{target_name}】拒绝了你的求婚。\n是否强娶？请在 30 秒内回复“是”，否则不会进入强娶逻辑。",
        "reset_records": "今日抽取记录、分手冷却时间和本群挑选老婆冷却已重置！",
        "reset_force_done": "✅ 本群强娶冷却时间已重置！现在大家可以再次强娶了。",
        "reset_force_empty": "💡 本群目前没有人在冷却期内。",
        "reset_propose_group_only": "求婚冷却时间只能在群聊中重置哦~",
        "reset_propose_empty": "💡 本群目前没有人在求婚冷却期内。",
        "reset_propose_done": "✅ 本群求婚冷却时间已重置！已清除 {count} 条求婚冷却记录。",
        "ranking_private": "私聊看不了榜单哦~",
        "ranking_empty": "本群近30天还没有人被强娶过，大家都很有礼貌呢。",
        "ranking_template_missing": "错误：找不到排行模板 rbq_ranking.html",
        "ranking_title": "❤️ 群rbq月榜 ❤️",
        "help": "===== 🌸 抽老婆帮助 =====\n1. 【抽老婆】：随机抽取今日老婆\n2. 【强娶@某人】：强行更换今日老婆（有冷却期）\n3. 【我的老婆】：查看今日历史与次数\n4. 【重置记录】：(管理员) 清空数据（强娶记录不会清除）\n5. 【关系图】：查看群友老婆的关系\n6. 【rbq排行】：展示近30天被强娶的次数排行\n7. 【求婚】：向群友求婚\n8. 【挑选老婆】：从候选群友中选择一位今日老婆\n9. 【分手】：解除普通老婆关系（72小时冷却）\n当前每日上限：{limit}次\n提示：可在配置中开启关键词触发、自动设置对方老婆和定时自动撤回。\n注：仅限{days}天内发言且当前在群的活跃群友。",
    },
    "en-US": {
        "group_only": "This feature is only available in group chats.",
        "wife_existing": " You already have a wife today ❤️\nShe is: 【{wife_name}】\n",
        "daily_limit": "You have already drawn a wife {count} time(s) today. Come back tomorrow!",
        "wife_pool_empty": "The wife pool is empty (members must have spoken within the last {days} days).",
        "draw_result": " Your wife of the day is:\n\n【{wife_name}】\n",
        "draw_suffix": "\nPlease treat her well ❤️\nRemaining draws: {remaining}",
        "history_empty": "You haven't drawn a wife today yet.",
        "history_title": "🌸 Today's wife history ({used}/{limit}):",
        "history_item": "{index}. 【{wife_name}】 ({time})",
        "history_remaining": "\nRemaining draws: {remaining}",
        "pick_already_done": "You have already drawn or picked a wife today. Come back tomorrow!",
        "pick_in_progress": "You are already choosing. Reply with a number, “reselect”, or “give up”.",
        "pick_cooldown": "You can use “Pick Wife” only once every 24 hours. Try again in {minutes} minute(s).",
        "pick_batches_used": "You have used all {limit} candidate batch(es) today. You can still use “Wife of the Day”.",
        "pick_list_title": "🌹 Choose your wife (reply with a number within 30 seconds):",
        "pick_list_footer": "\nReply “reselect” for another batch or “give up” to cancel.",
        "pick_success": " You chose 【{wife_name}】 as your wife of the day! ❤️\nPlease treat her well!",
        "pick_retry_limit": "You have reached the {limit}-batch limit. Choose from the current candidates or give up.",
        "wife_pool_retry": "The wife pool is empty. Please try again later.",
        "pick_abandon_used": "Selection cancelled. Today's candidate batches are used up, but you can still use “Wife of the Day”.",
        "pick_abandon": "Selection cancelled without using a daily draw. Candidate batches remaining: {remaining}.",
        "unlimited": "unlimited",
        "pick_invalid_number": "Please enter a valid number (1-{max}).",
        "breakup_group_only": "You can only break up in a group chat.",
        "breakup_cooldown": "You are still in the breakup cooldown. Try again in {remaining}.",
        "breakup_no_wife": "You don't have a wife to break up with.",
        "breakup_forced": "You cannot break up with a wife acquired through forced marriage!",
        "breakup_success": "You have broken up with {wives}. The breakup command will be available again in 72 hours.",
        "force_propose_cd": "You are still in the proposal cooldown. Try forced marriage in {remaining}.",
        "force_self_cd": "You have already used forced marriage!\nTry again in {remaining}.\n(Resets at {reset_time})",
        "force_need_target": "Please @mention someone you want to forcibly marry.",
        "marry_self": "You cannot marry yourself!",
        "force_target_propose_cd": "That person is still in the proposal cooldown. Try again in {remaining}.",
        "force_excluded": "That user is excluded from forced marriage.",
        "force_success": " You forcibly married 【{target_name}】 today ❤️\nPlease treat her well!\n",
        "propose_group_only": "Proposals are only available in group chats.",
        "propose_need_target": "Please @mention someone you want to propose to.",
        "propose_self": "You cannot propose to yourself!",
        "propose_force_cd_self": "You are in the forced-marriage cooldown and cannot propose yet.",
        "propose_force_cd_target": "That person is in the forced-marriage cooldown and cannot accept a proposal yet.",
        "propose_cd_self": "You are still in the proposal cooldown. Try again in {remaining}.",
        "propose_cd_target": "That person is still in the proposal cooldown. Try again in {remaining}.",
        "propose_pending": "You already have a pending proposal. Wait for a reply or try again in 30 seconds.",
        "propose_sent": "🌹 @{sender_name} proposed to 【{target_name}】!\nReply “accept” or “reject” within 30 seconds.",
        "propose_timeout": " ...Sadly, the proposal timed out without an answer...",
        "force_cancelled": "Forced marriage cancelled.",
        "propose_invalid": "The proposal is no longer valid because one of you entered the forced-marriage cooldown.",
        "propose_accepted": "🎉 Congratulations! {target_name} accepted {proposer_name}'s proposal!\nYou are officially married!",
        "propose_rejected": " Sadly, 【{target_name}】 rejected your proposal.\nForce the marriage? Reply “yes” within 30 seconds; otherwise it will be cancelled.",
        "reset_records": "Today's draws, breakup cooldowns, and this group's wife-picking cooldowns have been reset!",
        "reset_force_done": "✅ This group's forced-marriage cooldowns have been reset!",
        "reset_force_empty": "💡 Nobody in this group is currently in cooldown.",
        "reset_propose_group_only": "Proposal cooldowns can only be reset in a group chat.",
        "reset_propose_empty": "💡 Nobody in this group is currently in the proposal cooldown.",
        "reset_propose_done": "✅ Proposal cooldowns reset. Cleared {count} record(s).",
        "ranking_private": "The ranking is not available in private chats.",
        "ranking_empty": "Nobody in this group has been forcibly married in the last 30 days. How polite!",
        "ranking_template_missing": "Error: ranking template rbq_ranking.html was not found.",
        "ranking_title": "❤️ Monthly Group RBQ Ranking ❤️",
        "help": "===== 🌸 Wife Picker Help =====\n1. 【今日老婆 / jrlp】: draw a random wife\n2. 【强娶 @user / qiangqu】: replace today's wife (cooldown applies)\n3. 【我的老婆 / wdlp】: view today's history and remaining draws\n4. 【重置记录 / czjl】: admin-only data reset\n5. 【关系图 / gxt】: view the group's wife relationships\n6. 【rbq排行 / rbqph】: forced-marriage ranking for the last 30 days\n7. 【求婚 / qh】: propose to a group member\n8. 【挑选老婆 / txlp】: choose from a list of candidates\n9. 【分手 / fs】: end a regular wife relationship (72-hour cooldown)\nDaily limit: {limit}\nTip: keyword triggering, reciprocal wife assignment, and automatic message withdrawal are available in settings.\nOnly current group members active within {days} days are eligible.",
    },
    "ja-JP": {
        "group_only": "この機能はグループチャットでのみ利用できます。",
        "wife_existing": " 今日はもうお嫁さんがいますよ❤️\nお相手は【{wife_name}】です！\n",
        "daily_limit": "今日はすでに{count}回お嫁さんを引きました。また明日挑戦してください！",
        "wife_pool_empty": "お嫁さん候補がいません（過去{days}日以内に発言したメンバーが必要です）。",
        "draw_result": " あなたの今日のお嫁さんは……\n\n【{wife_name}】です！\n",
        "draw_suffix": "\n大切にしてあげてくださいね❤️\n残り抽選回数：{remaining}回",
        "history_empty": "今日はまだお嫁さんを引いていません。",
        "history_title": "🌸 今日のお嫁さん履歴（{used}/{limit}）：",
        "history_item": "{index}. 【{wife_name}】（{time}）",
        "history_remaining": "\n残り回数：{remaining}回",
        "pick_already_done": "今日はすでにお嫁さんを抽選・選択済みです。また明日どうぞ！",
        "pick_in_progress": "すでに選択中です。番号、「選び直す」または「やめる」と返信してください。",
        "pick_cooldown": "「お嫁さんを選ぶ」は24時間に1回のみ利用できます。あと{minutes}分お待ちください。",
        "pick_batches_used": "今日の候補枠（{limit}組）は使い切りました。「今日のお嫁さん」の抽選は利用できます。",
        "pick_list_title": "🌹 お嫁さんを選んでください（30秒以内に番号で返信）：",
        "pick_list_footer": "\n「選び直す」で別の候補、「やめる」でキャンセルできます。",
        "pick_success": " 【{wife_name}】を今日のお嫁さんに選びました！❤️\n大切にしてあげてくださいね。",
        "pick_retry_limit": "候補枠の上限（{limit}組）に達しました。現在の候補から選ぶか、やめてください。",
        "wife_pool_retry": "お嫁さん候補がいません。しばらくしてからお試しください。",
        "pick_abandon_used": "今回の選択をやめました。今日の候補枠は使い切りましたが、「今日のお嫁さん」の抽選は利用できます。",
        "pick_abandon": "今回の選択をやめました。抽選回数は消費しません。残り候補枠：{remaining}。",
        "unlimited": "無制限",
        "pick_invalid_number": "有効な番号（1～{max}）を入力してください。",
        "breakup_group_only": "別れ話はグループチャットでのみ行えます。",
        "breakup_cooldown": "まだ別れのクールダウン中です。あと{remaining}お待ちください。",
        "breakup_no_wife": "現在お嫁さんがいないため、別れることはできません。",
        "breakup_forced": "強引にお迎えしたお嫁さんとは別れられません！",
        "breakup_success": "{wives}とお別れしました。このコマンドは72時間後に再び利用できます。",
        "force_propose_cd": "まだプロポーズのクールダウン中です。あと{remaining}お待ちください。",
        "force_self_cd": "今日はもう強引なお迎えをしています！\nあと{remaining}お待ちください。\n（リセット：{reset_time}）",
        "force_need_target": "強引にお迎えしたい相手を @メンションしてください。",
        "marry_self": "自分自身とは結婚できません！",
        "force_target_propose_cd": "相手はまだプロポーズのクールダウン中です。あと{remaining}お待ちください。",
        "force_excluded": "このユーザーは強引なお迎えの対象外です。",
        "force_success": " 今日、【{target_name}】をお嫁さんとしてお迎えしました❤️\n大切にしてあげてくださいね。\n",
        "propose_group_only": "プロポーズはグループチャットでのみ行えます。",
        "propose_need_target": "プロポーズしたい相手を @メンションしてください。",
        "propose_self": "自分自身にはプロポーズできません！",
        "propose_force_cd_self": "強引なお迎えのクールダウン中はプロポーズできません。",
        "propose_force_cd_target": "相手は強引なお迎えのクールダウン中で、プロポーズを受けられません。",
        "propose_cd_self": "まだプロポーズのクールダウン中です。あと{remaining}お待ちください。",
        "propose_cd_target": "相手はまだプロポーズのクールダウン中です。あと{remaining}お待ちください。",
        "propose_pending": "処理中のプロポーズがあります。相手の返信を待つか、30秒後にお試しください。",
        "propose_sent": "🌹 @{sender_name}さんが【{target_name}】さんにプロポーズしました！\n30秒以内に「同意」または「拒否」と返信してください。",
        "propose_timeout": " ...残念ながら、返事がないままプロポーズが時間切れになりました...",
        "force_cancelled": "強引なお迎えをキャンセルしました。",
        "propose_invalid": "どちらかが強引なお迎えのクールダウンに入ったため、プロポーズは無効になりました。",
        "propose_accepted": "🎉 おめでとうございます！{target_name}さんが{proposer_name}さんのプロポーズを受け入れました！\nお二人は正式に夫婦になりました！",
        "propose_rejected": " 残念ながら、【{target_name}】さんにプロポーズを断られました。\n強引にお迎えしますか？30秒以内に「はい」と返信してください。",
        "reset_records": "今日の抽選履歴、別れのクールダウン、このグループの選択クールダウンをリセットしました！",
        "reset_force_done": "✅ このグループの強引なお迎えのクールダウンをリセットしました！",
        "reset_force_empty": "💡 このグループには現在クールダウン中の人はいません。",
        "reset_propose_group_only": "プロポーズのクールダウンはグループチャットでのみリセットできます。",
        "reset_propose_empty": "💡 このグループには現在プロポーズのクールダウン中の人はいません。",
        "reset_propose_done": "✅ プロポーズのクールダウンをリセットし、{count}件の記録を削除しました。",
        "ranking_private": "個人チャットではランキングを表示できません。",
        "ranking_empty": "過去30日間、このグループで強引にお迎えされた人はいません。皆さん礼儀正しいですね。",
        "ranking_template_missing": "エラー：ランキングテンプレート rbq_ranking.html が見つかりません。",
        "ranking_title": "❤️ グループRBQ月間ランキング ❤️",
        "help": "===== 🌸 今日のお嫁さん ヘルプ =====\n1. 【今日老婆 / jrlp】：ランダムに今日のお嫁さんを抽選\n2. 【强娶 @相手 / qiangqu】：今日のお嫁さんを変更（クールダウンあり）\n3. 【我的老婆 / wdlp】：今日の履歴と残り回数を確認\n4. 【重置记录 / czjl】：管理者用のデータリセット\n5. 【关系图 / gxt】：グループのお嫁さん関係を表示\n6. 【rbq排行 / rbqph】：過去30日間のランキング\n7. 【求婚 / qh】：グループメンバーにプロポーズ\n8. 【挑选老婆 / txlp】：候補から今日のお嫁さんを選択\n9. 【分手 / fs】：通常のお嫁さん関係を解除（72時間クールダウン）\n1日の上限：{limit}回\nヒント：キーワード起動、自動相互設定、自動メッセージ削除は設定から有効にできます。\n過去{days}日以内に発言し、現在もグループにいるメンバーのみが対象です。",
    },
}


def get_language(plugin_instance: Any) -> str:
    config = getattr(plugin_instance, "config", None)
    language = config.get("language", DEFAULT_LANGUAGE) if config else DEFAULT_LANGUAGE
    language = str(language)
    return language if language in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


def tr(plugin_instance: Any, key: str, **kwargs: Any) -> str:
    language = get_language(plugin_instance)
    template = MESSAGES.get(language, {}).get(key)
    if template is None:
        template = MESSAGES[DEFAULT_LANGUAGE].get(key, key)
    try:
        return template.format(**kwargs)
    except (KeyError, ValueError):
        return template


def is_language(plugin_instance: Any, language: str) -> bool:
    return get_language(plugin_instance) == language


def format_duration(plugin_instance: Any, seconds: float) -> str:
    total = max(0, int(seconds))
    days, remainder = divmod(total, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    language = get_language(plugin_instance)
    units = {
        "zh-CN": ("天", "小时", "分", "秒"),
        "en-US": ("d", "h", "m", "s"),
        "ja-JP": ("日", "時間", "分", "秒"),
    }[language]
    values = ((days, units[0]), (hours, units[1]), (minutes, units[2]))
    parts = [f"{value}{unit}" for value, unit in values if value]
    if secs or not parts:
        parts.append(f"{secs}{units[3]}")
    return " ".join(parts) if language == "en-US" else "".join(parts)
