# src/constants.py
from ..keyword_trigger import KeywordRoute, PermissionLevel

_DEFAULT_KEYWORD_ROUTES: tuple[KeywordRoute, ...] = (
    KeywordRoute(keyword="今日老婆", action="draw_wife"),
    KeywordRoute(keyword="jrlp", action="draw_wife"),
    KeywordRoute(keyword="抽老婆", action="draw_wife"),
    
    KeywordRoute(keyword="我的老婆", action="show_history"),
    KeywordRoute(keyword="wdlp", action="show_history"),
    KeywordRoute(keyword="抽取历史", action="show_history"),

    KeywordRoute(keyword="分手", action="breakup"),
    KeywordRoute(keyword="fs", action="breakup"),
    
    KeywordRoute(keyword="强娶", action="force_marry"),
    KeywordRoute(keyword="qiangqu", action="force_marry"),
    
    KeywordRoute(keyword="关系图", action="show_graph"),
    KeywordRoute(keyword="羁绊图谱", action="show_graph"),
    KeywordRoute(keyword="gxt", action="show_graph"),
    
    KeywordRoute(keyword="rbq排行", action="rbq_ranking"),
    KeywordRoute(keyword="rbqph", action="rbq_ranking"),
    
    KeywordRoute(keyword="抽老婆帮助", action="show_help"),
    KeywordRoute(keyword="老婆插件帮助", action="show_help"),
    KeywordRoute(keyword="clpbz", action="show_help"),

    KeywordRoute(keyword="求婚", action="propose_command"),
    KeywordRoute(keyword="qh", action="propose_command"),

    KeywordRoute(keyword="挑选老婆", action="pick_wife"),
    KeywordRoute(keyword="txlp", action="pick_wife"),

)
