from astrbot.api import logger


def debug_enabled(plugin) -> bool:
    return bool(plugin.config.get("debug_enabled", False))


def debug_log(plugin, area: str, message: str) -> None:
    if debug_enabled(plugin):
        logger.info(f"[wifepicker.{area}] {message}")


def log_command_platform(plugin, event) -> None:
    if not debug_enabled(plugin) or getattr(event, "_wifepicker_platform_logged", False):
        return
    debug_log(
        plugin,
        "platform",
        f"当前识别的平台={event.get_platform_name()} "
        f"group={event.get_group_id()} user={event.get_sender_id()}",
    )
    event._wifepicker_platform_logged = True
