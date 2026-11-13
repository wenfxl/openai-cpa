"""Backward-compatible entry point for CloakBrowser registration drivers.

Platform-specific flows live in ``cloak_chatgpt`` and ``cloak_x_com``.  This
module keeps the old import path working for callers and tests while exposing
the shared runtime helpers under their historical private names.
"""
from __future__ import annotations

from utils import config as cfg

from .cloak_chatgpt import (
    PASSWORD_URL,
    START_URL,
    _can_open_chatgpt_home,
    _classify_page_state,
    _page_state,
    run,
)
from .cloak_runtime import (
    GEOIP_DB_FILENAME,
    GEOIP_METADATA_MARKER,
    GEOIP_MIN_BYTES,
    LOCAL_RUNTIME_DIRNAME,
    PROJECT_ROOT,
    action_locator,
    action_timeout_ms,
    clear_action_marker,
    cloudflare_timeout_seconds,
    configure_runtime,
    human_click,
    human_click_marked,
    human_fill,
    human_fill_verified,
    human_pause,
    human_press,
    human_select,
    human_input_value,
    human_type,
    is_valid_geoip_db,
    log,
    navigation_timeout_ms,
    new_action_marker,
    safe_proxy_for_log,
    safe_url_for_log,
    visible_locator,
)
from .cloak_x_com import (
    X_COM_START_URL,
    _x_com_page_state,
    _x_com_wait_for_step,
    run as run_x_com,
)


def _configure_cloak_runtime() -> None:
    configure_runtime()


def _launch_browser(proxy, x_com: bool = False):
    """Legacy launcher; import the platform modules for new code."""
    from . import cloak_runtime as runtime

    if x_com:
        from utils import config as cfg

        locale = (
            str(getattr(cfg, "CLOAK_LOCALE", "") or "").strip()
            or str(getattr(cfg, "CLOAK_X_COM_LOCALE", "") or "").strip()
            or "en-US"
        )
        timezone_name = str(getattr(cfg, "CLOAK_TIMEZONE", "") or "").strip()
        return runtime.launch_browser(
            proxy,
            locale=locale,
            timezone_name=timezone_name,
            geoip=bool(getattr(cfg, "CLOAK_GEOIP", True)),
        )
    return runtime.launch_browser(
        proxy,
        locale=str(getattr(cfg, "CLOAK_LOCALE", "") or "").strip() or "en-US",
        geoip=bool(getattr(cfg, "CLOAK_GEOIP", True)),
    )


_log = log
_action_timeout_ms = action_timeout_ms
_navigation_timeout_ms = navigation_timeout_ms
_human_pause = human_pause
_safe_url_for_log = safe_url_for_log
_safe_proxy_for_log = safe_proxy_for_log
_visible_locator = visible_locator
_human_fill = human_fill
_human_click = human_click
_cloudflare_timeout_seconds = cloudflare_timeout_seconds
_is_valid_geoip_db = is_valid_geoip_db

__all__ = [
    "PASSWORD_URL",
    "START_URL",
    "X_COM_START_URL",
    "GEOIP_DB_FILENAME",
    "GEOIP_METADATA_MARKER",
    "GEOIP_MIN_BYTES",
    "LOCAL_RUNTIME_DIRNAME",
    "PROJECT_ROOT",
    "_action_timeout_ms",
    "_can_open_chatgpt_home",
    "_classify_page_state",
    "_cloudflare_timeout_seconds",
    "_configure_cloak_runtime",
    "action_locator",
    "clear_action_marker",
    "_human_click",
    "human_click_marked",
    "_human_fill",
    "human_fill_verified",
    "human_input_value",
    "_human_pause",
    "_human_press",
    "_human_select",
    "_human_type",
    "_is_valid_geoip_db",
    "_launch_browser",
    "_log",
    "_navigation_timeout_ms",
    "_page_state",
    "_safe_proxy_for_log",
    "_safe_url_for_log",
    "_visible_locator",
    "_x_com_page_state",
    "_x_com_wait_for_step",
    "new_action_marker",
    "run",
    "run_x_com",
]
