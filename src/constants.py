# src/constants.py
from ..keyword_trigger import KeywordRoute, PermissionLevel

_DEFAULT_KEYWORD_ROUTES: tuple[KeywordRoute, ...] = (
    KeywordRoute(keyword="今日老婆", action="draw_wife"),
    KeywordRoute(keyword="抽老婆", action="draw_wife"),
    KeywordRoute(keyword="我的老婆", action="show_history"),
    KeywordRoute(keyword="抽取历史", action="show_history"),
    KeywordRoute(keyword="强娶", action="force_marry"),
    KeywordRoute(keyword="关系图", action="show_graph"),
    KeywordRoute(keyword="羁绊图谱", action="show_graph"),
    KeywordRoute(keyword="rbq排行", action="rbq_ranking"),
    KeywordRoute(keyword="抽老婆帮助", action="show_help"),
    KeywordRoute(keyword="老婆插件帮助", action="show_help"),
    KeywordRoute(
        keyword="重置记录",
        action="reset_records",
        permission=PermissionLevel.ADMIN,
    ),
    KeywordRoute(
        keyword="重置强娶时间",
        action="reset_force_cd",
        permission=PermissionLevel.ADMIN,
    ),
)