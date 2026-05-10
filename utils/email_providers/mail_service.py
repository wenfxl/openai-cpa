import imaplib
import json
import random
import re
import socket
import string
import time
import threading
from email import message_from_string
from email.header import decode_header, make_header
from email.message import Message
from email.policy import default as email_policy
from html import unescape
from typing import Any, Optional
from urllib.parse import urlparse
import socks
from curl_cffi import requests
from utils import config as cfg
from utils.integrations.ai_service import AIService
from utils.email_providers.gmail_service import get_gmail_otp_via_oauth
from utils.email_providers.duckmail_service import DuckMailService
from utils.email_providers.postman_center import global_postman_fleet, wait_for_code

class ProxyIMAP4_SSL(imaplib.IMAP4_SSL):
    """支持 Socks5 和 HTTP 代理的局部 IMAP 客户端"""

    def __init__(self, host, port, proxy_url=None, **kwargs):
        self.proxy_url = proxy_url
        super().__init__(host, port, **kwargs)

    def _create_socket(self, timeout):
        if not self.proxy_url:
            return socket.create_connection((self.host, self.port), timeout)

        parsed = urlparse(self.proxy_url)
        if 'socks5' in parsed.scheme.lower():
            p_type = socks.SOCKS5
        else:
            p_type = socks.HTTP

        proxy_port = parsed.port or (1080 if p_type == socks.SOCKS5 else 8080)
        sock = socks.socksocket(socket.AF_INET, socket.SOCK_STREAM)
        sock.set_proxy(p_type, parsed.hostname, proxy_port, rdns=True)
        sock.settimeout(timeout)
        sock.connect((self.host, self.port))
        return sock

luckmail_lock = threading.Lock()
empty_retry_count = 0
empty_lock = threading.Lock()
_CM_TOKEN_CACHE: Optional[str] = None
_DOMAIN_RUNTIME_LOCK = threading.Lock()
_DOMAIN_RUNTIME_STATE = {}
_MAIL_DOMAIN_FAILURE_TYPES = {"discarded_email", "cloudflare_temp_email_network", "capacity_exceeded"}
_MAIL_DOMAIN_CONFIG_CACHE = {
    "key": None,
    "main_domains": (),
    "main_domain_set": frozenset(),
    "disabled_main_domains": frozenset(),
    "selected_failure_types": frozenset(),
    "effective_groups": (),
}
_DOMAIN_RUNTIME_SESSION = {
    "counting_enabled": False,
    "last_started_at": 0.0,
    "last_stopped_at": 0.0,
    "tie_break_cursor": 0,
    "group_cursor": 0,
    "group_sticky_cursor": 0,
}

_thread_data = threading.local()
_orig_sleep = time.sleep
LOCAL_USED_PIDS = set()
AI_NAME_POOL = []
AI_KW_POOL = []
FIRST_NAMES = [
    "james", "john", "robert", "michael", "william", "david", "richard", "joseph", "thomas", "charles",
    "christopher", "daniel", "matthew", "anthony", "mark", "donald", "steven", "paul", "andrew", "joshua"
]
LAST_NAMES = [
    "smith", "johnson", "williams", "brown", "jones", "garcia", "miller", "davis", "rodriguez", "martinez",
    "hernandez", "lopez", "gonzalez", "wilson", "anderson", "thomas", "taylor", "moore", "jackson", "martin"
]


def _safe_set_tag(lm_service, p_id, tag_id):
    """带重试机制的异步打标，防止网络波动导致打标失败变成死循环号"""
    for _ in range(3):
        try:
            if lm_service.set_email_tag(p_id, tag_id):
                return
        except Exception:
            pass
        time.sleep(2)


def clear_sticky_domain():
    """注册失败时调用"""
    if hasattr(_thread_data, 'sticky_domain'):
        _thread_data.sticky_domain = None


def set_last_email(email: str):
    _thread_data.last_attempt_email = email


def _set_last_domain_failure_event(domain: str, reason: str) -> None:
    normalized = _normalize_main_domain(domain)
    normalized_reason = str(reason or "").strip().lower()
    if not normalized or normalized_reason not in _MAIL_DOMAIN_FAILURE_TYPES:
        return
    _thread_data.last_domain_failure_event = {
        "domain": normalized,
        "reason": normalized_reason,
    }


def pop_last_domain_failure_event() -> dict:
    event = getattr(_thread_data, 'last_domain_failure_event', None)
    _thread_data.last_domain_failure_event = None
    return dict(event) if isinstance(event, dict) else {}


def _parse_mail_domain_items(raw_value: Any) -> list[str]:
    seen = set()
    domains = []
    for part in str(raw_value or '').split(','):
        root = str(part or '').strip().lower().strip('.')
        if root and root not in seen:
            seen.add(root)
            domains.append(root)
    return domains


def _get_mail_domain_config_cache() -> dict[str, Any]:
    mail_domains_raw = str(getattr(cfg, 'MAIL_DOMAINS', '') or '')
    disabled_raw = tuple(
        str(item or '').strip().lower().strip('.')
        for item in (getattr(cfg, 'DISABLED_MAIL_DOMAINS', []) or [])
    )
    selected_failure_raw = tuple(
        str(item or '').strip().lower()
        for item in (getattr(cfg, 'MAIL_DOMAIN_FAILURE_TYPES', []) or [])
    )
    grouping_enabled = bool(
        is_mail_domain_runtime_control_enabled()
        and getattr(cfg, 'ENABLE_MAIL_DOMAIN_GROUPING', False)
    )
    group_count = max(1, min(10, int(getattr(cfg, 'MAIL_DOMAIN_GROUP_COUNT', 2) or 2)))
    group_mode = str(getattr(cfg, 'MAIL_DOMAIN_GROUP_MODE', 'auto') or 'auto').strip().lower()
    group_strategy = str(getattr(cfg, 'MAIL_DOMAIN_GROUP_STRATEGY', 'round_robin') or 'round_robin').strip().lower()
    raw_groups = tuple(str(item or '') for item in (getattr(cfg, 'MAIL_DOMAIN_GROUPS', []) or []))

    cache_key = (
        mail_domains_raw,
        disabled_raw,
        selected_failure_raw,
        grouping_enabled,
        group_count,
        group_mode,
        group_strategy,
        raw_groups,
    )
    cached_key = _MAIL_DOMAIN_CONFIG_CACHE.get("key")
    if cached_key == cache_key:
        return _MAIL_DOMAIN_CONFIG_CACHE

    main_domains = tuple(_parse_mail_domain_items(mail_domains_raw))
    main_domain_set = frozenset(main_domains)

    disabled_main_domains = frozenset(
        domain
        for domain in disabled_raw
        if domain in main_domain_set
    )

    selected_failure_types = frozenset(
        item for item in selected_failure_raw
        if item in _MAIL_DOMAIN_FAILURE_TYPES
    )

    effective_groups: tuple[tuple[str, ...], ...]
    if not grouping_enabled:
        effective_groups = (main_domains,) if main_domains else ()
    elif group_mode == 'manual':
        groups = _build_manual_domain_groups(list(main_domains), list(raw_groups))
        effective_groups = tuple(tuple(group) for group in (groups if groups else ([list(main_domains)] if main_domains else [])))
    else:
        groups = _build_auto_domain_groups(list(main_domains), group_count)
        effective_groups = tuple(tuple(group) for group in (groups if groups else ([list(main_domains)] if main_domains else [])))

    _MAIL_DOMAIN_CONFIG_CACHE["key"] = cache_key
    _MAIL_DOMAIN_CONFIG_CACHE["main_domains"] = main_domains
    _MAIL_DOMAIN_CONFIG_CACHE["main_domain_set"] = main_domain_set
    _MAIL_DOMAIN_CONFIG_CACHE["disabled_main_domains"] = disabled_main_domains
    _MAIL_DOMAIN_CONFIG_CACHE["selected_failure_types"] = selected_failure_types
    _MAIL_DOMAIN_CONFIG_CACHE["effective_groups"] = effective_groups
    return _MAIL_DOMAIN_CONFIG_CACHE


def _get_configured_main_domains() -> list[str]:
    return list(_get_mail_domain_config_cache()["main_domains"])


def get_configured_main_domains_snapshot() -> list[str]:
    return list(_get_mail_domain_config_cache()["main_domains"])


def _is_mail_domain_grouping_enabled() -> bool:
    return bool(
        is_mail_domain_runtime_control_enabled()
        and getattr(cfg, 'ENABLE_MAIL_DOMAIN_GROUPING', False)
    )


def _get_mail_domain_group_strategy() -> str:
    strategy = str(getattr(cfg, 'MAIL_DOMAIN_GROUP_STRATEGY', 'round_robin') or 'round_robin').strip().lower()
    if strategy not in {'round_robin', 'exhaust_then_next'}:
        return 'round_robin'
    return strategy


def _build_auto_domain_groups(main_domains: list[str], group_count: int) -> list[list[str]]:
    if not main_domains or group_count <= 0:
        return []
    groups = [[] for _ in range(group_count)]
    for index, domain in enumerate(main_domains):
        groups[index % group_count].append(domain)
    return [group for group in groups if group]


def _build_manual_domain_groups(main_domains: list[str], raw_groups: list[Any]) -> list[list[str]]:
    master_set = set(main_domains)
    groups = []
    assigned = set()
    for raw_group in raw_groups:
        group = []
        for domain in _parse_mail_domain_items(raw_group):
            if domain in master_set and domain not in assigned:
                assigned.add(domain)
                group.append(domain)
        if group:
            groups.append(group)
    return groups


def _get_effective_domain_groups(main_domains: list[str]) -> list[list[str]]:
    normalized_domains = tuple(_normalize_main_domain(domain) for domain in main_domains)
    cache = _get_mail_domain_config_cache()
    cached_main_domains = cache["main_domains"]
    if normalized_domains == cached_main_domains:
        return [list(group) for group in cache["effective_groups"]]
    if not _is_mail_domain_grouping_enabled():
        return [list(normalized_domains)] if normalized_domains else []
    group_count = max(1, min(10, int(getattr(cfg, 'MAIL_DOMAIN_GROUP_COUNT', 2) or 2)))
    group_mode = str(getattr(cfg, 'MAIL_DOMAIN_GROUP_MODE', 'auto') or 'auto').strip().lower()
    main_domain_list = [domain for domain in normalized_domains if domain]
    if group_mode == 'manual':
        groups = _build_manual_domain_groups(main_domain_list, getattr(cfg, 'MAIL_DOMAIN_GROUPS', []) or [])
        return groups if groups else [main_domain_list]
    groups = _build_auto_domain_groups(main_domain_list, group_count)
    return groups if groups else [main_domain_list]


def _get_mail_domain_group_label(domain: str) -> str:
    normalized = _normalize_main_domain(domain)
    if not normalized or not getattr(cfg, 'ENABLE_MAIL_DOMAIN_GROUPING', False):
        return ""
    groups = _get_mail_domain_config_cache()["effective_groups"]
    for index, group in enumerate(groups):
        if normalized in group:
            return f"[{index + 1}]"
    return ""


def _format_grouped_mail_log(label: str, email: str) -> str:
    group_label = _get_mail_domain_group_label(label)
    masked_email = mask_email(email)
    return f"{group_label} {masked_email}" if group_label else masked_email


def _normalize_main_domain(domain: str) -> str:
    text = str(domain or "").strip().lower().strip(".")
    if not text:
        return ""
    if "@" in text:
        _, text = text.rsplit("@", 1)
        text = text.strip().strip(".")
        if not text:
            return ""

    configured = _get_mail_domain_config_cache()["main_domains"]
    for root in configured:
        if text == root or text.endswith(f".{root}"):
            return root
    return text if not configured else ""


def _get_disabled_main_domains() -> set[str]:
    return set(_get_mail_domain_config_cache()["disabled_main_domains"])


def _all_configured_main_domains_disabled() -> bool:
    configured = _get_configured_main_domains()
    if not configured:
        return False
    disabled = _get_disabled_main_domains()
    return bool(disabled) and all(domain in disabled for domain in configured)


def is_mail_domain_disabled(domain: str) -> bool:
    normalized = _normalize_main_domain(domain)
    return bool(normalized) and normalized in _get_disabled_main_domains()


def is_mail_domain_runtime_control_enabled(mode: Optional[str] = None) -> bool:
    current_mode = str(mode or getattr(cfg, 'EMAIL_API_MODE', '') or '').strip()
    if current_mode not in {"cloudflare_temp_email", "freemail", "cloudmail", "openai_cpa"}:
        return False
    return bool(getattr(cfg, 'ENABLE_MAIL_DOMAIN_RUNTIME_CONTROL', False))


def start_mail_domain_runtime_tracking() -> None:
    if not is_mail_domain_runtime_control_enabled():
        return
    now = time.time()
    with _DOMAIN_RUNTIME_LOCK:
        _DOMAIN_RUNTIME_SESSION["counting_enabled"] = True
        _DOMAIN_RUNTIME_SESSION["last_started_at"] = now
        _DOMAIN_RUNTIME_SESSION["last_stopped_at"] = 0.0


def stop_mail_domain_runtime_tracking() -> None:
    now = time.time()
    with _DOMAIN_RUNTIME_LOCK:
        _DOMAIN_RUNTIME_SESSION["counting_enabled"] = False
        _DOMAIN_RUNTIME_SESSION["last_stopped_at"] = now


def clear_mail_domain_runtime_stats() -> None:
    with _DOMAIN_RUNTIME_LOCK:
        _DOMAIN_RUNTIME_STATE.clear()
        _DOMAIN_RUNTIME_SESSION["counting_enabled"] = False
        _DOMAIN_RUNTIME_SESSION["last_started_at"] = 0.0
        _DOMAIN_RUNTIME_SESSION["last_stopped_at"] = 0.0
        _DOMAIN_RUNTIME_SESSION["tie_break_cursor"] = 0
        _DOMAIN_RUNTIME_SESSION["group_cursor"] = 0
        _DOMAIN_RUNTIME_SESSION["group_sticky_cursor"] = 0


def _is_mail_domain_runtime_tracking_active() -> bool:
    return bool(_DOMAIN_RUNTIME_SESSION.get("counting_enabled"))


def _new_domain_runtime_state() -> dict:
    return {
        "fail_count": 0,
        "success_count": 0,
        "pick_count": 0,
        "failure_counts": {},
        "last_failure_reason": "",
        "cooldown_until": 0.0,
        "cooldown_reason": "",
        "last_used_at": 0.0,
        "last_failure_at": 0.0,
        "last_success_at": 0.0,
    }


def _prune_expired_domain_records(now: float) -> None:
    expired_domains = []
    for domain, state in _DOMAIN_RUNTIME_STATE.items():
        if float(state.get("cooldown_until") or 0.0) > 0 and float(state.get("cooldown_until") or 0.0) <= now:
            expired_domains.append(domain)
    for domain in expired_domains:
        _DOMAIN_RUNTIME_STATE.pop(domain, None)


def _get_domain_state(domain: str) -> dict:
    now = time.time()
    normalized = _normalize_main_domain(domain)
    if not normalized or not is_mail_domain_runtime_control_enabled():
        return {}

    with _DOMAIN_RUNTIME_LOCK:
        _prune_expired_domain_records(now)
        state = _DOMAIN_RUNTIME_STATE.setdefault(normalized, _new_domain_runtime_state())
        return dict(state)

def _get_domain_selection_key(state: dict) -> tuple[int, float]:
    pick_count = max(0, int(state.get("pick_count") or 0))
    last_used_at = float(state.get("last_used_at") or 0.0)
    return pick_count, last_used_at


def _select_low_failure_domain(candidates: list[str]) -> Optional[str]:
    if not candidates:
        return None

    selected_failure_types = _get_selected_mail_domain_failure_types()
    prioritized_clean = True
    best_key = None
    best_domains: list[str] = []

    for domain in candidates:
        state = _DOMAIN_RUNTIME_STATE.setdefault(domain, _new_domain_runtime_state())
        fail_count = _recalculate_domain_fail_count(state, selected_failure_types)
        domain_is_clean = fail_count <= 0
        selection_key = _get_domain_selection_key(state)

        if best_key is None:
            prioritized_clean = domain_is_clean
            best_key = selection_key
            best_domains = [domain]
            continue

        if prioritized_clean and not domain_is_clean:
            continue
        if domain_is_clean and not prioritized_clean:
            prioritized_clean = True
            best_key = selection_key
            best_domains = [domain]
            continue
        if selection_key < best_key:
            best_key = selection_key
            best_domains = [domain]
            continue
        if selection_key == best_key:
            best_domains.append(domain)

    if not best_domains:
        return None
    if len(best_domains) == 1:
        return best_domains[0]

    cursor = int(_DOMAIN_RUNTIME_SESSION.get("tie_break_cursor", 0) or 0)
    selected = best_domains[cursor % len(best_domains)]
    _DOMAIN_RUNTIME_SESSION["tie_break_cursor"] = cursor + 1
    return selected


def _mark_selected_domain_used(selected: Optional[str], now: float, increment: int = 1) -> Optional[str]:
    if not selected:
        return None
    state = _DOMAIN_RUNTIME_STATE.setdefault(selected, _new_domain_runtime_state())
    state["last_used_at"] = now
    state["pick_count"] = max(0, int(state.get("pick_count") or 0)) + max(1, int(increment or 1))
    return selected


def _get_available_main_domain_candidates(main_domains: list[str], now: float) -> list[str]:
    disabled_domains = _get_disabled_main_domains()
    candidates = []
    for domain in main_domains:
        normalized = _normalize_main_domain(domain)
        if not normalized or normalized in disabled_domains:
            continue
        state = _DOMAIN_RUNTIME_STATE.setdefault(normalized, _new_domain_runtime_state())
        cooldown_until = float(state.get("cooldown_until") or 0.0)
        if cooldown_until > now:
            continue
        candidates.append(normalized)
    return candidates


def _select_round_robin_group_candidates(groups: list[list[str]], now: float) -> list[str]:
    if not groups:
        return []
    cursor = int(_DOMAIN_RUNTIME_SESSION.get("group_cursor", 0) or 0)
    if cursor >= len(groups) or cursor < 0:
        cursor = 0
    for offset in range(len(groups)):
        group_index = (cursor + offset) % len(groups)
        candidates = _get_available_main_domain_candidates(groups[group_index], now)
        if candidates:
            _DOMAIN_RUNTIME_SESSION["group_cursor"] = (group_index + 1) % len(groups)
            return candidates
    return []


def _select_exhaust_then_next_group_candidates(groups: list[list[str]], now: float) -> list[str]:
    if not groups:
        return []
    cursor = int(_DOMAIN_RUNTIME_SESSION.get("group_sticky_cursor", 0) or 0)
    if cursor >= len(groups) or cursor < 0:
        cursor = 0
    current_candidates = _get_available_main_domain_candidates(groups[cursor], now)
    if current_candidates:
        _DOMAIN_RUNTIME_SESSION["group_sticky_cursor"] = cursor
        return current_candidates
    for offset in range(1, len(groups) + 1):
        group_index = (cursor + offset) % len(groups)
        candidates = _get_available_main_domain_candidates(groups[group_index], now)
        if candidates:
            _DOMAIN_RUNTIME_SESSION["group_sticky_cursor"] = group_index
            return candidates
    return []


def _select_group_candidates_from_groups(groups: list[list[str]], now: float) -> list[str]:
    if not groups:
        return []
    if _get_mail_domain_group_strategy() == 'exhaust_then_next':
        return _select_exhaust_then_next_group_candidates(groups, now)
    return _select_round_robin_group_candidates(groups, now)


def _select_group_candidates(main_domains: list[str], now: float) -> list[str]:
    groups = _get_effective_domain_groups(main_domains)
    return _select_group_candidates_from_groups(groups, now)


def _select_first_available_main_domain(main_domains: list[str], now: float, batch_size: int = 1) -> Optional[str]:
    disabled_domains = _get_disabled_main_domains()
    for domain in main_domains:
        normalized = _normalize_main_domain(domain)
        if not normalized or normalized in disabled_domains:
            continue
        state = _DOMAIN_RUNTIME_STATE.setdefault(normalized, _new_domain_runtime_state())
        if float(state.get("cooldown_until") or 0.0) > now:
            continue
        return _mark_selected_domain_used(normalized, now, increment=batch_size)
    return None



def _select_main_domain_from_candidates(candidates: list[str]) -> Optional[str]:
    if not candidates:
        return None
    if getattr(cfg, 'MAIL_DOMAIN_PREFER_LOW_FAILURE_MODE', False):
        return _select_low_failure_domain(candidates)
    return random.choice(candidates)


def _preallocate_main_domains_locked(main_domains: list[str], batch_size: int, now: float) -> list[Optional[str]]:
    allocated: list[Optional[str]] = []
    batch_size = max(0, int(batch_size or 0))
    if getattr(cfg, 'MAIL_DOMAIN_PINPOINT_BURST_MODE', False):
        selected = _select_first_available_main_domain(main_domains, now, batch_size=batch_size)
        return [selected] * batch_size if selected else [None] * batch_size

    groups = _get_effective_domain_groups(main_domains) if _is_mail_domain_grouping_enabled() else []
    batch_candidates = _select_group_candidates_from_groups(groups, now) if groups else _get_available_main_domain_candidates(main_domains, now)
    enforce_unique_within_batch = len(set(batch_candidates)) >= batch_size
    used_in_batch: set[str] = set()

    for _ in range(batch_size):
        candidates = list(batch_candidates)
        if enforce_unique_within_batch:
            candidates = [domain for domain in candidates if domain not in used_in_batch]
        if not candidates:
            allocated.append(None)
            continue
        selected = _select_main_domain_from_candidates(candidates)
        marked = _mark_selected_domain_used(selected, now)
        if marked:
            used_in_batch.add(marked)
        allocated.append(marked)
    return allocated


def pick_available_main_domain(main_domains: list[str]) -> Optional[str]:
    disabled_domains = _get_disabled_main_domains()
    if not is_mail_domain_runtime_control_enabled():
        normalized_domains = [_normalize_main_domain(domain) for domain in main_domains]
        candidates = [domain for domain in normalized_domains if domain and domain not in disabled_domains]
        return random.choice(candidates) if candidates else None

    now = time.time()

    with _DOMAIN_RUNTIME_LOCK:
        _prune_expired_domain_records(now)
        candidates = _select_group_candidates(main_domains, now) if _is_mail_domain_grouping_enabled() else _get_available_main_domain_candidates(main_domains, now)
        if not candidates:
            return None
        selected = _select_main_domain_from_candidates(candidates)
        return _mark_selected_domain_used(selected, now)


def preallocate_main_domains_for_batch(main_domains: list[str], batch_size: int) -> list[Optional[str]]:
    if batch_size <= 0:
        return []
    if not is_mail_domain_runtime_control_enabled():
        return [None] * batch_size

    now = time.time()
    with _DOMAIN_RUNTIME_LOCK:
        _prune_expired_domain_records(now)
        return _preallocate_main_domains_locked(main_domains, batch_size, now)


def _apply_domain_cooldown(state: dict, reason: str, cooldown_sec: int) -> float:
    cooldown_until = time.time() + max(int(cooldown_sec or 0), 0)
    state["fail_count"] = 0
    state["cooldown_reason"] = reason
    state["cooldown_until"] = cooldown_until
    return cooldown_until


def _get_selected_mail_domain_failure_types() -> set[str]:
    return set(_get_mail_domain_config_cache()["selected_failure_types"])


def _recalculate_domain_fail_count(state: dict, selected_failure_types: Optional[set[str]] = None) -> int:
    failure_counts = state.get("failure_counts")
    if not isinstance(failure_counts, dict):
        failure_counts = {}
        state["failure_counts"] = failure_counts
    selected = selected_failure_types if selected_failure_types is not None else _get_selected_mail_domain_failure_types()
    fail_count = sum(
        max(0, int(failure_counts.get(reason) or 0))
        for reason in selected
    )
    state["fail_count"] = fail_count
    return fail_count


def _build_domain_result(domain: str, state: dict, cooldown_until: float, cooldown_triggered: bool) -> dict:
    return {
        "domain": domain,
        "fail_count": int(state.get("fail_count") or 0),
        "success_count": int(state.get("success_count") or 0),
        "pick_count": max(0, int(state.get("pick_count") or 0)),
        "failure_counts": dict(state.get("failure_counts") or {}),
        "last_failure_reason": str(state.get("last_failure_reason") or ""),
        "cooldown_reason": str(state.get("cooldown_reason") or ""),
        "cooldown_until": cooldown_until,
        "cooldown_triggered": cooldown_triggered,
    }


def record_domain_failure(domain: str, reason: str) -> dict:
    normalized = _normalize_main_domain(domain)
    normalized_reason = str(reason or "").strip().lower()
    if (
        not normalized
        or normalized_reason not in _MAIL_DOMAIN_FAILURE_TYPES
        or not is_mail_domain_runtime_control_enabled()
        or not _is_mail_domain_runtime_tracking_active()
    ):
        return {}

    threshold = int(getattr(cfg, 'MAIL_DOMAIN_FAIL_THRESHOLD', 0) or 0)
    cooldown_sec = int(getattr(cfg, 'MAIL_DOMAIN_FAIL_COOLDOWN_SEC', 0) or 0)
    selected_failure_types = _get_selected_mail_domain_failure_types()
    now = time.time()

    with _DOMAIN_RUNTIME_LOCK:
        _prune_expired_domain_records(now)
        state = _DOMAIN_RUNTIME_STATE.setdefault(normalized, _new_domain_runtime_state())
        failure_counts = state.get("failure_counts")
        if not isinstance(failure_counts, dict):
            failure_counts = {}
            state["failure_counts"] = failure_counts
        cooldown_until = float(state.get("cooldown_until") or 0.0)
        _recalculate_domain_fail_count(state, selected_failure_types)
        state["last_failure_at"] = now
        state["last_failure_reason"] = normalized_reason

        if cooldown_until > now:
            _recalculate_domain_fail_count(state, selected_failure_types)
            state["fail_count"] = 0
            if not state.get("cooldown_reason"):
                state["cooldown_reason"] = normalized_reason
            return _build_domain_result(normalized, state, cooldown_until, False)

        failure_counts[normalized_reason] = int(failure_counts.get(normalized_reason) or 0) + 1
        fail_count = _recalculate_domain_fail_count(state, selected_failure_types)
        cooldown_triggered = False
        if threshold > 0 and fail_count >= threshold:
            cooldown_until = _apply_domain_cooldown(state, normalized_reason, cooldown_sec)
            cooldown_triggered = True
        else:
            cooldown_until = float(state.get("cooldown_until") or 0.0)
        return _build_domain_result(normalized, state, cooldown_until, cooldown_triggered)


def record_domain_success(domain: str) -> dict:
    normalized = _normalize_main_domain(domain)
    if not normalized or not is_mail_domain_runtime_control_enabled() or not _is_mail_domain_runtime_tracking_active():
        return {}

    selected_failure_types = _get_selected_mail_domain_failure_types()
    now = time.time()

    with _DOMAIN_RUNTIME_LOCK:
        _prune_expired_domain_records(now)
        state = _DOMAIN_RUNTIME_STATE.setdefault(normalized, _new_domain_runtime_state())
        state["success_count"] = int(state.get("success_count") or 0) + 1
        state["last_success_at"] = now
        _recalculate_domain_fail_count(state, selected_failure_types)
        cooldown_until = float(state.get("cooldown_until") or 0.0)
        return _build_domain_result(normalized, state, cooldown_until, False)


def _build_domain_runtime_row(domain: str, state: dict, now: float) -> dict:
    cooldown_until = float(state.get("cooldown_until") or 0.0)
    is_disabled = domain in _get_disabled_main_domains()
    _recalculate_domain_fail_count(state, _get_selected_mail_domain_failure_types())
    return {
        "domain": domain,
        "fail_count": int(state.get("fail_count") or 0),
        "success_count": int(state.get("success_count") or 0),
        "pick_count": max(0, int(state.get("pick_count") or 0)),
        "failure_counts": dict(state.get("failure_counts") or {}),
        "last_failure_reason": str(state.get("last_failure_reason") or ""),
        "cooldown_until": cooldown_until,
        "cooldown_remaining_sec": max(0, int(cooldown_until - now)) if cooldown_until > now else 0,
        "cooldown_reason": str(state.get("cooldown_reason") or ""),
        "is_available": cooldown_until <= now,
        "is_disabled": is_disabled,
        "is_enabled": not is_disabled,
        "last_used_at": float(state.get("last_used_at") or 0.0),
        "last_failure_at": float(state.get("last_failure_at") or 0.0),
        "last_success_at": float(state.get("last_success_at") or 0.0),
    }


def _get_domain_runtime_row_locked(domain: str, now: float) -> dict:
    state = _DOMAIN_RUNTIME_STATE.get(domain)
    if not state:
        return {}
    return _build_domain_runtime_row(domain, state, now)


def get_mail_domain_runtime_summary() -> dict:
    if not is_mail_domain_runtime_control_enabled():
        return {"total_count": 0, "available_count": 0, "cooldown_count": 0}

    now = time.time()
    configured_domains = _get_configured_main_domains()
    with _DOMAIN_RUNTIME_LOCK:
        _prune_expired_domain_records(now)
        cooldown_domains = {
            domain for domain, state in _DOMAIN_RUNTIME_STATE.items()
            if float(state.get("cooldown_until") or 0.0) > now
        }
        total_count = len(configured_domains)
        cooldown_count = sum(1 for domain in configured_domains if domain in cooldown_domains)
        available_count = max(0, total_count - cooldown_count)
        return {
            "total_count": total_count,
            "available_count": available_count,
            "cooldown_count": cooldown_count,
        }


def sync_mail_domain_runtime_state_with_config() -> dict:
    configured_domains = _get_configured_main_domains()
    configured_set = set(configured_domains)
    now = time.time()

    with _DOMAIN_RUNTIME_LOCK:
        _prune_expired_domain_records(now)
        existing_domains = set(_DOMAIN_RUNTIME_STATE.keys())

        added_count = 0
        removed_count = 0

        for domain in configured_domains:
            if domain not in _DOMAIN_RUNTIME_STATE:
                _DOMAIN_RUNTIME_STATE[domain] = _new_domain_runtime_state()
                added_count += 1

        for domain in list(existing_domains):
            if domain not in configured_set:
                _DOMAIN_RUNTIME_STATE.pop(domain, None)
                removed_count += 1

        total_count = len(_DOMAIN_RUNTIME_STATE)

    return {
        "added_count": added_count,
        "removed_count": removed_count,
        "total_count": total_count,
    }


def clear_mail_domain_runtime_domain_counters(domain: str) -> dict:
    normalized = _normalize_main_domain(domain)
    if not normalized or not is_mail_domain_runtime_control_enabled():
        return {}

    now = time.time()
    with _DOMAIN_RUNTIME_LOCK:
        _prune_expired_domain_records(now)
        state = _DOMAIN_RUNTIME_STATE.get(normalized)
        if not state:
            return {}
        state["fail_count"] = 0
        state["failure_counts"] = {}
        state["last_failure_reason"] = ""
        state["last_failure_at"] = 0.0
        return _get_domain_runtime_row_locked(normalized, now)


def clear_mail_domain_runtime_domain_cooldown(domain: str) -> dict:
    normalized = _normalize_main_domain(domain)
    if not normalized or not is_mail_domain_runtime_control_enabled():
        return {}

    now = time.time()
    with _DOMAIN_RUNTIME_LOCK:
        _prune_expired_domain_records(now)
        state = _DOMAIN_RUNTIME_STATE.get(normalized)
        if not state:
            return {}
        state["fail_count"] = 0
        state["failure_counts"] = {}
        state["last_failure_reason"] = ""
        state["last_failure_at"] = 0.0
        state["cooldown_until"] = 0.0
        state["cooldown_reason"] = ""
        return _get_domain_runtime_row_locked(normalized, now)


def clear_all_mail_domain_runtime_cooldowns() -> int:
    if not is_mail_domain_runtime_control_enabled():
        return 0

    now = time.time()
    cleared_count = 0
    with _DOMAIN_RUNTIME_LOCK:
        _prune_expired_domain_records(now)
        for state in _DOMAIN_RUNTIME_STATE.values():
            if float(state.get("cooldown_until") or 0.0) > now:
                cleared_count += 1
            state["fail_count"] = 0
            state["failure_counts"] = {}
            state["last_failure_reason"] = ""
            state["last_failure_at"] = 0.0
            state["cooldown_until"] = 0.0
            state["cooldown_reason"] = ""
    return cleared_count


def get_mail_domain_runtime_stats() -> list[dict]:
    if not is_mail_domain_runtime_control_enabled():
        return []

    sync_mail_domain_runtime_state_with_config()
    selected_failure_types = _get_selected_mail_domain_failure_types()
    disabled_domains = _get_disabled_main_domains()
    now = time.time()
    rows = []
    with _DOMAIN_RUNTIME_LOCK:
        _prune_expired_domain_records(now)
        for domain in sorted(_DOMAIN_RUNTIME_STATE.keys()):
            state = _DOMAIN_RUNTIME_STATE[domain]
            cooldown_until = float(state.get("cooldown_until") or 0.0)
            _recalculate_domain_fail_count(state, selected_failure_types)
            rows.append({
                "domain": domain,
                "fail_count": int(state.get("fail_count") or 0),
                "success_count": int(state.get("success_count") or 0),
                "pick_count": max(0, int(state.get("pick_count") or 0)),
                "failure_counts": dict(state.get("failure_counts") or {}),
                "last_failure_reason": str(state.get("last_failure_reason") or ""),
                "cooldown_until": cooldown_until,
                "cooldown_remaining_sec": max(0, int(cooldown_until - now)) if cooldown_until > now else 0,
                "cooldown_reason": str(state.get("cooldown_reason") or ""),
                "is_available": cooldown_until <= now,
                "is_disabled": domain in disabled_domains,
                "is_enabled": domain not in disabled_domains,
                "last_used_at": float(state.get("last_used_at") or 0.0),
                "last_failure_at": float(state.get("last_failure_at") or 0.0),
                "last_success_at": float(state.get("last_success_at") or 0.0),
            })
    return rows



def get_last_email() -> Optional[str]:
    return getattr(_thread_data, 'last_attempt_email', None)


def _smart_sleep(secs):
    for _ in range(int(secs * 10)):
        if getattr(cfg, 'GLOBAL_STOP', False):
            return
        _orig_sleep(0.1)


time.sleep = _smart_sleep


def _ssl_verify() -> bool:
    import os
    flag = os.getenv("OPENAI_SSL_VERIFY", "1").strip().lower()
    return flag not in {"0", "false", "no", "off"}


def mask_email(text: str, force_mask: bool = False) -> str:
    """日志脱敏：隐藏邮箱域名部分。"""
    if not force_mask and not getattr(cfg, 'ENABLE_EMAIL_MASKING', False):
        return text if text else ""
    if not text:
        return ""
    if "@" in text:
        try:
            user_part, _ = text.split("@", 1)

            if "+" in user_part:
                main_acc, alias_suffix = user_part.split("+", 1)

                m_keep = 2 if len(main_acc) > 2 else 1
                masked_main = main_acc[:m_keep] + "***"

                a_keep = 2 if len(alias_suffix) > 2 else 1
                masked_alias = alias_suffix[:a_keep] + "***"

                return f"{masked_main}+{masked_alias}@***.***"
            else:
                u_keep = 2 if len(user_part) > 2 else 1
                return f"{user_part[:u_keep]}***@***.***"
        except:
            return "******@***.***"

    domain_match = re.match(r"^([a-zA-Z0-9.-]+\.[a-zA-Z]{2,}|\d{1,3}(?:\.\d{1,3}){3})(:\d+)?$", text)
    if domain_match:
        domain_or_ip = domain_match.group(1)
        port = domain_match.group(2) or ""
        keep = min(4, max(2, len(domain_or_ip) // 3))
        prefix = domain_or_ip[:keep]
        return f"{prefix}***.***{port}"

    match = re.match(r"token_(.+)_(\d{10,})\.json", text)
    if match:
        ep, ts_ = match.group(1), match.group(2)
        return f"token_{ep[:len(ep) // 2]}***_{ts_}.json"
    if len(text) > 8 and ".json" in text:
        name_part = text.replace(".json", "")
        return f"{name_part[:len(name_part) // 2]}***.json"
    return text


def _reset_cm_token_cache() -> None:
    global _CM_TOKEN_CACHE
    _CM_TOKEN_CACHE = None


def get_cm_token(proxies=None) -> Optional[str]:
    global _CM_TOKEN_CACHE
    if _CM_TOKEN_CACHE:
        return _CM_TOKEN_CACHE
    try:
        url = f"{cfg.CM_API_URL}/api/public/genToken"
        payload = {"email": cfg.CM_ADMIN_EMAIL, "password": cfg.CM_ADMIN_PASS}
        res = requests.post(url, json=payload, proxies=proxies,
                            verify=_ssl_verify(), timeout=15)
        data = res.json()
        if data.get("code") == 200:
            _CM_TOKEN_CACHE = data["data"]["token"]
            return _CM_TOKEN_CACHE
        print(f"[{cfg.ts()}] [ERROR] CloudMail Token 生成失败: {data.get('message')}")
    except Exception as e:
        print(f"[{cfg.ts()}] [ERROR] CloudMail 接口请求异常: {e}")
    return None


def _get_ai_data_package():
    global AI_NAME_POOL, AI_KW_POOL
    ai_enabled = getattr(cfg, 'AI_ENABLE_PROFILE', False)

    if ai_enabled:
        ai = AIService()
        if len(AI_NAME_POOL) < 5: AI_NAME_POOL.extend(ai.fetch_names())
        if len(AI_KW_POOL) < 10: AI_KW_POOL.extend(ai.fetch_keywords())
        if AI_NAME_POOL:
            return AI_NAME_POOL.pop(0), True

    letters = "".join(random.choices(string.ascii_lowercase, k=5))
    digits = "".join(random.choices(string.digits, k=3))
    return f"{letters}{digits}", False


# def get_email_and_token(proxies: Any = None) -> tuple:
#     """拦截器"""
#     result = _raw_get_email_and_token(proxies)
#     if result is None or not isinstance(result, tuple) or len(result) != 2:
#         print("\n" + "=" * 50)
#         print(f"[{cfg.ts()}] _raw_get_email_and_token 违规返回了单值: {result}")
#         print(f"[{cfg.ts()}] 当前系统的 cfg.EMAIL_API_MODE 是: '{getattr(cfg, 'EMAIL_API_MODE', '未知')}'")
#
#         import traceback
#         print("以下是问题时的调用路径：")
#         traceback.print_stack()
#         print("=" * 50 + "\n")
#         return None, None
#     return result

def get_email_and_token(
    proxies: Any = None,
    assigned_domain: Optional[str] = None,
    batch_id: Optional[int] = None,
    worker_index: Optional[int] = None,
) -> tuple:
# def _raw_get_email_and_token(proxies: Any = None) -> tuple:
    """兼容五种邮箱模式的地址创建，返回 (email, token_or_id)。"""
    if getattr(cfg, 'GLOBAL_STOP', False): return None, None
    _thread_data.last_attempt_email = None
    _thread_data.last_domain_failure_event = None

    mode = cfg.EMAIL_API_MODE
    mail_proxies = proxies if cfg.USE_PROXY_FOR_EMAIL else None

    if mode == "mail_curl":
        try:
            url = f"{cfg.MC_API_BASE}/api/remail?key={cfg.MC_KEY}"
            res = requests.post(url, proxies=mail_proxies, verify=_ssl_verify(), timeout=15)
            data = res.json()
            if data.get("email") and data.get("id"):
                email = data["email"]
                mailbox_id = data["id"]
                set_last_email(email)
                print(f"[{cfg.ts()}] [INFO] mail-curl 分配邮箱: ({mask_email(email)}) (BoxID: {mailbox_id})")
                return email, mailbox_id
        except Exception as e:
            print(f"[{cfg.ts()}] [ERROR] mail-curl 获取邮箱异常: {e}")
        return None, None

    if mode == "fvia":
        try:
            from utils.email_providers.fvia_service import FviaMailService
            current_token = getattr(cfg, 'FVIA_TOKEN', '')

            if not current_token:
                print(f"[{cfg.ts()}] [ERROR] 未在配置中检测到 Fvia Token，请前往前端填写！")
                return None, None

            fs = FviaMailService(token=current_token, proxies=mail_proxies)
            email, token = fs.create_email()

            if email and token:
                set_last_email(email)
                print(f"[{cfg.ts()}] [INFO] FviaInboxes 成功分配邮箱: ({mask_email(email)})")
                return email, token
            else:
                print(f"[{cfg.ts()}] [ERROR] FviaInboxes 获取域名列表失败。")
        except Exception as e:
            print(f"[{cfg.ts()}] [ERROR] FviaInboxes 流程异常: {e}")
        return None, None

    if mode == "tmailor":
        try:
            from utils.email_providers.tmailor_service import TmailorService
            current_token = getattr(cfg, 'TMAILOR_CURRENT_TOKEN', '')
            ts_service = TmailorService(current_token=current_token, proxies=mail_proxies)
            email, token = ts_service.create_email()

            if email and token:
                set_last_email(email)
                print(f"[{cfg.ts()}] [INFO] Tmailor 成功创建邮箱: ({mask_email(email)})")
                return email, token
            else:
                print(f"[{cfg.ts()}] [ERROR] Tmailor 获取邮箱失败，请检查 Token 是否过期。")
        except Exception as e:
            print(f"[{cfg.ts()}] [ERROR] Tmailor 流程异常: {e}")
        return None, None

    if mode == "inboxes":
        try:
            from utils.email_providers.inboxes_service import InboxesService
            ibs = InboxesService(proxies=mail_proxies)
            email, token = ibs.create_email()

            if email and token:
                set_last_email(email)
                print(f"[{cfg.ts()}] [INFO] Inboxes.com 成功分配邮箱: ({mask_email(email)})")
                return email, token
            else:
                print(f"[{cfg.ts()}] [ERROR] Inboxes.com 申请失败。")
        except Exception as e:
            print(f"[{cfg.ts()}] [ERROR] Inboxes.com 流程异常: {e}")
        return None, None

    if mode == "temporarymail":
        try:
            from utils.email_providers.temporarymail_service import TemporaryMailService
            tm_service = TemporaryMailService(proxies=mail_proxies)
            email, token = tm_service.create_email()

            if email and token:
                set_last_email(email)
                print(f"[{cfg.ts()}] [INFO] TemporaryMail 成功分配邮箱: ({mask_email(email)})")
                return email, token
            else:
                print(f"[{cfg.ts()}] [ERROR] TemporaryMail 申请失败。")
        except Exception as e:
            print(f"[{cfg.ts()}] [ERROR] TemporaryMail 流程异常: {e}")
        return None, None

    # if mode == "temporam":
    #     try:
    #         from utils.email_providers.temporam_service import TemporamService
    #         tp_service = TemporamService(proxies=mail_proxies)
    #         email, token = tp_service.create_email()
    #
    #         if email and token:
    #             set_last_email(email)
    #             print(f"[{cfg.ts()}] [INFO] Temporam 成功生成邮箱: ({mask_email(email)})")
    #             return email, token
    #         else:
    #             print(f"[{cfg.ts()}] [ERROR] Temporam 获取邮箱失败")
    #     except Exception as e:
    #         print(f"[{cfg.ts()}] [ERROR] Temporam 流程异常: {e}")
    #     return None, None

    if mode == "luckmail":
        try:
            from utils.email_providers.luckmail_service import LuckMailService
            lm_service = LuckMailService(
                api_key=cfg.LUCKMAIL_API_KEY,
                preferred_domain=getattr(cfg, 'LUCKMAIL_PREFERRED_DOMAIN', ""),
                proxies=mail_proxies,
                email_type=getattr(cfg, 'LUCKMAIL_EMAIL_TYPE', "ms_graph"),
                variant_mode=getattr(cfg, 'LUCKMAIL_VARIANT_MODE', "")
            )

            tag_id = getattr(cfg, 'LUCKMAIL_TAG_ID', None)
            if not tag_id:
                with luckmail_lock:
                    tag_id = getattr(cfg, 'LUCKMAIL_TAG_ID', None)
                    if not tag_id:
                        tag_id = lm_service.get_or_create_tag_id("已使用")
                        if tag_id:
                            cfg.LUCKMAIL_TAG_ID = tag_id
                            try:
                                import yaml
                                with cfg.CONFIG_FILE_LOCK:
                                    with open(cfg.CONFIG_PATH, "r", encoding="utf-8") as f:
                                        y = yaml.safe_load(f) or {}
                                    y.setdefault("luckmail", {})["tag_id"] = tag_id
                                    with open(cfg.CONFIG_PATH, "w", encoding="utf-8") as f:
                                        yaml.dump(y, f, allow_unicode=True, sort_keys=False)
                                print(f"[{cfg.ts()}] [系统] 标签 ID {tag_id} 已同步至配置文件")
                            except Exception as e:
                                print(f"[{cfg.ts()}] [WARNING] 配置文件写入失败: {e}")

            if getattr(cfg, 'LUCKMAIL_REUSE_PURCHASED', False):
                with luckmail_lock:
                    email, token, p_id = lm_service.get_random_purchased_email(tag_id=tag_id,
                                                                               local_used_pids=LOCAL_USED_PIDS)
                    if p_id:
                        LOCAL_USED_PIDS.add(p_id)

                if email and token:
                    print(f"[{cfg.ts()}] [SUCCESS] LuckMail 成功复用历史邮箱: ({mask_email(email)})")
                    if p_id and tag_id:
                        threading.Thread(target=_safe_set_tag, args=(lm_service, p_id, tag_id), daemon=True).start()
                    return email, token
                print(f"[{cfg.ts()}] [WARNING] 未找到符合条件的历史邮箱，准备购买新号...")

            email, token, p_id = lm_service.get_email_and_token(auto_tag=False)

            if email and token:
                if p_id:
                    with luckmail_lock:
                        LOCAL_USED_PIDS.add(p_id)

                print(f"[{cfg.ts()}] [INFO] LuckMail 成功购买新邮箱: ({mask_email(email)})")

                if p_id and tag_id:
                    threading.Thread(target=_safe_set_tag, args=(lm_service, p_id, tag_id), daemon=True).start()
                return email, token

        except Exception as e:
            print(f"[{cfg.ts()}] [ERROR] LuckMail 流程异常: {e}")
            return None, None

    if mode == "duckmail":
        try:
            from utils.email_providers.duckmail_service import DuckMailService
            duck_use_proxy = getattr(cfg, 'DUCK_USE_PROXY', True)
            duck_proxies = proxies if duck_use_proxy else None
            ds = DuckMailService(proxies=duck_proxies)
            email, token = ds.create_email()
            if email:
                set_last_email(email)
                print(f"[{cfg.ts()}] [INFO] DuckMail ({ds.mode}) 成功创建邮箱: {mask_email(email)}")
                return email, token
            else:
                print(f"[{cfg.ts()}] [ERROR] DuckMail 获取邮箱失败")
        except Exception as e:
            print(f"[{cfg.ts()}] [ERROR] DuckMail 流程异常: {e}")
        return None, None

    if mode == "generator_email":
        try:
            from utils.email_providers.generator_email_service import GeneratorEmailService
            ge_service = GeneratorEmailService(proxies=mail_proxies)
            email, token = ge_service.create_email()

            if email and token:
                set_last_email(email)
                print(f"[{cfg.ts()}] [INFO] GeneratorEmail 成功创建邮箱: ({mask_email(email)})")
                return email, token
            else:
                print(f"[{cfg.ts()}] [ERROR] GeneratorEmail 获取邮箱失败")
        except Exception as e:
            print(f"[{cfg.ts()}] [ERROR] GeneratorEmail 流程异常: {e}")
        return None, None

    if mode == "tempmail":
        try:
            from utils.email_providers.tempmail_service import TempmailService
            tm_service = TempmailService(proxies=mail_proxies)
            email, token = tm_service.create_email()

            if email and token:
                set_last_email(email)
                print(f"[{cfg.ts()}] [INFO] Tempmail 成功创建邮箱: ({mask_email(email)})")
                return email, token
            else:
                print(f"[{cfg.ts()}] [ERROR] Tempmail 获取邮箱失败")
        except Exception as e:
            print(f"[{cfg.ts()}] [ERROR] Tempmail 流程异常: {e}")
        return None, None
    if mode == "tempmail_org":
        try:
            from utils.email_providers.tempmail_org import TempMailOrgService
            tm_org = TempMailOrgService(proxies=mail_proxies)
            email, token = tm_org.create_email()

            if email and token:
                set_last_email(email)
                print(f"[{cfg.ts()}] [INFO] TempMail.org 成功创建邮箱: ({mask_email(email)})")
                return email, token
            else:
                print(f"[{cfg.ts()}] [ERROR] TempMail.org 获取邮箱失败")
        except Exception as e:
            print(f"[{cfg.ts()}] [ERROR] TempMail.org 流程异常: {e}")
        return None, None

    if mode == "local_microsoft":
        from utils.email_providers.local_microsoft_service import LocalMicrosoftService
        ms_service = LocalMicrosoftService(proxies=mail_proxies)

        mailbox_info = ms_service.get_unused_mailbox()

        if not mailbox_info:
            global empty_retry_count
            with empty_lock:
                empty_retry_count += 1
                if empty_retry_count >= cfg.REG_THREADS:
                    cfg.POOL_EXHAUSTED = True
                    print(f"[{cfg.ts()}] [WARNING] {cfg.REG_THREADS} 个线程全都没拿到邮箱，微软邮箱库已耗尽，程序将自动停止，请前往微软邮箱库导入更多账号！")
                else:
                    print(f"[{cfg.ts()}] [WARNING] 当前线程未拿到邮箱 (失败线程/总线程: {empty_retry_count}/{cfg.REG_THREADS})，将跳过等待下一轮。")
            return None, None

        with empty_lock:
            empty_retry_count = 0


        # if not mailbox_info:
        #     # cfg.POOL_EXHAUSTED = True
        #     print(f"[{cfg.ts()}] [WARNING] 微软邮箱库已耗尽，请前往前端导入更多账号。")
        #     return None, None

        email = mailbox_info["email"]
        set_last_email(email)
        print(f"[{cfg.ts()}] [INFO] 微软库分配并锁定账号: ({mask_email(email)})")
        global_postman_fleet.add_mailbox_listener(ms_service, mailbox_info)
        return email, json.dumps(mailbox_info, ensure_ascii=False)

    if mode == "gmail_fission":
        from utils.email_providers.gmail_fission_service import GmailFissionService
        gmail_service = GmailFissionService(proxies=mail_proxies)
        mailbox_info = gmail_service.get_unused_mailbox()

        if not mailbox_info:
            cfg.POOL_EXHAUSTED = True
            print(f"[{cfg.ts()}] [WARNING] Gmail 裂变池已耗尽或生成重复过多，停止派发。")
            return None, None

        target_email = mailbox_info["email"]
        set_last_email(target_email)
        print(f"[{cfg.ts()}] [INFO] Gmail 库分配并锁定账号: ({mask_email(target_email)})")
        global_postman_fleet.add_mailbox_listener(gmail_service, mailbox_info)
        return target_email, json.dumps(mailbox_info, ensure_ascii=False)

    prefix, ai_enabled = _get_ai_data_package()
    use_domain_runtime_control = is_mail_domain_runtime_control_enabled(mode)

    batch_preallocated = batch_id is not None and worker_index is not None
    skip_domain_fallback = batch_preallocated and assigned_domain is None

    if cfg.ENABLE_SUB_DOMAINS:
        # sticky = getattr(_thread_data, 'sticky_domain', None)
        # if sticky:
        #     selected_domain = sticky
        #     print(f"[{cfg.ts()}] [INFO] 多级域名模式 - 沿用上一轮成功域名: {mask_email(selected_domain)}")
        # else:
        main_list = [d.strip() for d in cfg.MAIL_DOMAINS.split(",") if d.strip()]
        if not main_list:
            print(f"[{cfg.ts()}] [ERROR] 未配置主域名池，无法捏造子域！")
            return None, None

        if skip_domain_fallback:
            return None, None

        selected_main = _normalize_main_domain(assigned_domain) if assigned_domain is not None else pick_available_main_domain(main_list)
        if not selected_main:
            if _all_configured_main_domains_disabled():
                print(f"[{cfg.ts()}] [ERROR] 所有主域名均已被手动禁用，当前无法继续生成邮箱！")
            elif use_domain_runtime_control:
                print(f"[{cfg.ts()}] [ERROR] 所有主域名均处于冷却中，当前无法继续生成邮箱！")
            else:
                print(f"[{cfg.ts()}] [ERROR] 未找到可用主域名，当前无法继续生成邮箱！")
            return None, None
        if getattr(cfg, 'RANDOM_SUB_DOMAIN_LEVEL', False):
            level = random.randint(1, 7)
        else:
            try:
                level = int(getattr(cfg, 'SUB_DOMAIN_LEVEL', 1))
            except:
                level = 1

        random_parts = []
        for _ in range(level):
            if ai_enabled and AI_KW_POOL:
                kw = AI_KW_POOL.pop(0)
                random_parts.append(f"{kw}-{''.join(random.choices(string.ascii_lowercase + string.digits, k=4))}")
            else:
                random_parts.append(''.join(random.choices(string.ascii_lowercase + string.digits, k=8)))

        selected_domain = ".".join(random_parts) + f".{selected_main}"
        _thread_data.sticky_domain = selected_domain
    else:
        domain_list = [d.strip() for d in cfg.MAIL_DOMAINS.split(",") if d.strip()]
        if not domain_list:
            print(f"[{cfg.ts()}] [ERROR] 域名池配置为空，无法生成邮箱！")
            return None, None
        if skip_domain_fallback:
            return None, None
        selected_domain = _normalize_main_domain(assigned_domain) if assigned_domain is not None else pick_available_main_domain(domain_list)
        if not selected_domain:
            if _all_configured_main_domains_disabled():
                print(f"[{cfg.ts()}] [ERROR] 所有主域名均已被手动禁用，当前无法继续生成邮箱！")
            elif use_domain_runtime_control:
                print(f"[{cfg.ts()}] [ERROR] 所有主域名均处于冷却中，当前无法继续生成邮箱！")
            else:
                print(f"[{cfg.ts()}] [ERROR] 域名池配置为空或无有效主域名，无法生成邮箱！")
            return None, None

    email_str = f"{prefix}@{selected_domain}"
    set_last_email(email_str)

    ai_switch_on = getattr(cfg, 'AI_ENABLE_PROFILE', False)
    if ai_switch_on:
        print(f"[{cfg.ts()}] [AI-状态] 已开启 （{mask_email(email_str)}） AI 智能邮箱域名信息增强...")

    if mode == "openai_cpa":
        if getattr(cfg, 'OPENAI_CPA_WEBHOOK_SECRET', ""):
            print(f"[{cfg.ts()}] [INFO] 成功通过 项目专属邮箱 OPENAI-CPA 指定创建邮箱: {mask_email(email_str)}")
            return email_str, ""
        else:
            print(f"[{cfg.ts()}] [ERROR] 项目专属邮箱 OPENAI-CPA 未填写通讯密钥，无法生成邮箱！")
            return None, None

    if mode == "cloudmail":
        if getattr(cfg, 'CM_LOCAL_WEBHOOK', False):
            print(
                f"[{cfg.ts()}] [INFO] 成功通过 本项目收件模式 cloudmail 指定创建邮箱: {mask_email(email_str)}"
            )
            return email_str, ""
        else:
            token = get_cm_token(mail_proxies)
            if not token:
                print(f"[{cfg.ts()}] [ERROR] 未能获取 CloudMail Token，跳过注册")
                return None, None
            try:
                res = requests.post(
                    f"{cfg.CM_API_URL}/api/public/addUser",
                    headers={"Authorization": token},
                    json={"list": [{"email": email_str}]},
                    proxies=mail_proxies, timeout=15,
                )
                if res.json().get("code") == 200:
                    print(f"[{cfg.ts()}] [INFO] CloudMail 成功创建邮箱: {mask_email(email_str)}")
                    return email_str, ""
                print(f"[{cfg.ts()}] [ERROR] CloudMail 邮箱创建失败: {res.text}")
            except Exception as e:
                print(f"[{cfg.ts()}] [ERROR] CloudMail 邮箱创建异常: {e}")
            return None, None

    if mode == "freemail":
        if getattr(cfg, 'FREEMAIL_LOCAL_WEBHOOK', False):
            print(f"[{cfg.ts()}] [INFO] 成功通过 本项目收件模式 Freemail 指定创建邮箱: {mask_email(email_str)}")
            return email_str, ""
        else:
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {cfg.FREEMAIL_API_TOKEN}"
            }
            for attempt in range(5):
                if getattr(cfg, 'GLOBAL_STOP', False): return None, None
                try:
                    res = requests.post(f"{cfg.FREEMAIL_API_URL}/api/create",
                                        json={"email": email_str}, headers=headers,
                                        proxies=mail_proxies, verify=_ssl_verify(), timeout=15)
                    res.raise_for_status()
                    print(
                        f"[{cfg.ts()}] [INFO] 成功通过 Freemail 指定创建邮箱: {mask_email(email_str)}"
                    )
                    return email_str, ""
                except Exception as e:
                    print(f"[{cfg.ts()}] [ERROR] Freemail 邮箱创建异常: {e}")
                    time.sleep(2)
            return None, None

    if mode == "Gmail_OAuth":
        print(f"[{cfg.ts()}] [INFO] Gmail_OAuth成功生成临时域名邮箱: {email_str}")
        return email_str, ""

    if mode == "imap":
        print(f"[{cfg.ts()}] [INFO] imap成功生成临时域名邮箱: {email_str}")
        return email_str, ""

    if mode == "cloudflare_temp_email":
        headers = {"x-admin-auth": cfg.ADMIN_AUTH, "Content-Type": "application/json"}
        body = {"enablePrefix": False, "name": prefix, "domain": selected_domain}
        terminal_failure_reason = ""
        for attempt in range(5):
            if getattr(cfg, 'GLOBAL_STOP', False): return None, None
            try:
                res = requests.post(
                    f"{cfg.GPTMAIL_BASE}/admin/new_address",
                    headers=headers, json=body,
                    proxies=mail_proxies, verify=_ssl_verify(), timeout=15,
                )
                status_code = int(getattr(res, 'status_code', 0) or 0)
                text = str(getattr(res, 'text', '') or '')
                quota_text = text.lower()
                if status_code in {403, 429, 507} or any(token in quota_text for token in ("quota", "limit", "capacity", "exceeded", "over limit", "full")):
                    terminal_failure_reason = "capacity_exceeded"
                    print(f"[{cfg.ts()}] [WARNING] cloudflare_temp_email邮箱容量疑似超限 (尝试 {attempt + 1}/5): {res.text}")
                    time.sleep(1)
                    continue
                res.raise_for_status()
                data = res.json()
                if data and data.get("address"):
                    email = data["address"].strip()
                    jwt = data.get("jwt", "").strip()
                    set_last_email(email)
                    print(
                        f"[{cfg.ts()}] [INFO] cloudflare_temp_email成功获取临时邮箱: {_format_grouped_mail_log(selected_domain, email)}"
                    )
                    return email, jwt
                terminal_failure_reason = "cloudflare_temp_email_network"
                print(f"[{cfg.ts()}] [WARNING] cloudflare_temp_email邮箱申请失败 (尝试 {attempt + 1}/5): {res.text}")
                time.sleep(1)
            except Exception as e:
                terminal_failure_reason = "cloudflare_temp_email_network"
                print(f"[{cfg.ts()}] [ERROR] cloudflare_temp_email邮箱注册网络异常，准备重试: {e}")
                time.sleep(2)
        if terminal_failure_reason:
            _set_last_domain_failure_event(selected_domain, terminal_failure_reason)
        return None, None


def _decode_mime_header(value: str) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _extract_body_from_message(message: Message) -> str:
    parts = []
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_maintype() == "multipart":
                continue
            ct = (part.get_content_type() or "").lower()
            if ct not in ("text/plain", "text/html"):
                continue
            try:
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or "utf-8"
                text = payload.decode(charset, errors="replace") if payload else ""
            except Exception:
                try:
                    text = part.get_content()
                except Exception:
                    text = ""
            if ct == "text/html":
                text = re.sub(r"<[^>]+>", " ", text)
            parts.append(text)
    else:
        try:
            payload = message.get_payload(decode=True)
            charset = message.get_content_charset() or "utf-8"
            body = payload.decode(charset, errors="replace") if payload else ""
        except Exception:
            try:
                body = message.get_content()
            except Exception:
                body = str(message.get_payload() or "")
        if "html" in (message.get_content_type() or "").lower():
            body = re.sub(r"<[^>]+>", " ", body)
        parts.append(body)
    return unescape("\n".join(p for p in parts if p).strip())


def _extract_mail_fields(mail: dict) -> dict:
    sender = str(
        mail.get("source") or mail.get("from") or
        mail.get("from_address") or mail.get("fromAddress") or ""
    ).strip()
    subject = str(mail.get("subject") or mail.get("title") or "").strip()
    body_text = str(
        mail.get("text") or mail.get("body") or
        mail.get("content") or mail.get("html") or ""
    ).strip()
    raw = str(mail.get("raw") or "").strip()
    if raw:
        try:
            msg = message_from_string(raw, policy=email_policy)
            sender = sender or _decode_mime_header(msg.get("From", ""))
            subject = subject or _decode_mime_header(msg.get("Subject", ""))
            parsed = _extract_body_from_message(msg)
            body_text = (f"{body_text}\n{parsed}".strip() if body_text else parsed) if parsed else body_text
        except Exception:
            body_text = f"{body_text}\n{raw}".strip() if body_text else raw
    body_text = unescape(re.sub(r"<[^>]+>", " ", body_text))
    return {"sender": sender, "subject": subject, "body": body_text, "raw": raw}


OTP_CODE_PATTERN = r"(?<!\d)(\d{6})(?!\d)"


def _extract_otp_code(content: str) -> str:
    if not content:
        return ""
    patterns = [
        r"(?i)Your (?:ChatGPT|OpenAI) code is\s*(\d{6})",
        r"(?i)(?:ChatGPT|OpenAI) code is\s*(\d{6})",
        r"(?i)verification code to continue:\s*(\d{6})",
        r"(?i)Subject:.*?(\d{6})",
        r"(?i)enter this code:\s*(\d{6})",
    ]
    for p in patterns:
        m = re.search(p, content)
        if m:
            return m.group(1)
    fallback = re.search(r"(?<!\d)(\d{6})(?!\d)", content)
    return fallback.group(1) if fallback else ""


def _create_imap_conn(proxy_str=None):
    """使用原生方式建立 IMAP 连接 (支持局部代理)"""
    if proxy_str:
        return ProxyIMAP4_SSL(cfg.IMAP_SERVER, cfg.IMAP_PORT, proxy_url=proxy_str, timeout=15)
    return imaplib.IMAP4_SSL(cfg.IMAP_SERVER, cfg.IMAP_PORT, timeout=15)

def get_oai_code(
        email: str,
        jwt: str = "",
        proxies: Any = None,
        processed_mail_ids: set = None,
        pattern: str = OTP_CODE_PATTERN,
        max_attempts: int = 20,
) -> str:
    """轮询各邮箱服务商收取 OpenAI 验证码，返回 6 位字符串或空串。"""
    max_attempts = getattr(cfg, 'OTP_POLL_MAX_ATTEMPTS', 20)
    mailbox_id = jwt
    mail_proxies = proxies if cfg.USE_PROXY_FOR_EMAIL else None
    proxy_str = None
    if mail_proxies:
        if isinstance(mail_proxies, dict):
            proxy_str = mail_proxies.get("https") or mail_proxies.get("http")
        else:
            proxy_str = str(mail_proxies)
    base_url = cfg.GPTMAIL_BASE.rstrip("/")
    mode = cfg.EMAIL_API_MODE

    print(f"\n[{cfg.ts()}] [INFO] 等待接收验证码 ({mask_email(email)})...")

    if processed_mail_ids is None:
        processed_mail_ids = set()

    mail_conn = None
    if mode == "imap":
        try:
            mail_conn = _create_imap_conn(proxy_str)
            mail_conn.login(cfg.IMAP_USER, cfg.IMAP_PASS.replace(" ", ""))
        except Exception as e:
            print(f"\n[{cfg.ts()}] [ERROR] IMAP 初始登录失败: {e}")
            mail_conn = None

    local_ms_account = None
    if mode == "local_microsoft":
        try:
            parsed_jwt = json.loads(jwt or "{}")
            local_ms_account = parsed_jwt if isinstance(parsed_jwt, dict) else None
        except:
            pass

        if local_ms_account:
            timeout = max_attempts * 3
            return wait_for_code(email, timeout=timeout)
        else:
            print(f"\n[{cfg.ts()}] [ERROR] 缺少微软邮箱凭据，无法收信。")
            return ""

    if mode == "gmail_fission":
        timeout = max_attempts * 3
        code = wait_for_code(email, timeout=timeout)
        if code:
            return code
        else:
            print(f"[{cfg.ts()}] [ERROR] ({mask_email(email)}) 邮递员等待超时，未收到验证码。")
            return ""

    for attempt in range(max_attempts):
        if getattr(cfg, 'GLOBAL_STOP', False): return ""
        try:
            if mode == "mail_curl":
                inbox_url = (f"{cfg.MC_API_BASE}/api/inbox"
                             f"?key={cfg.MC_KEY}&mailbox_id={mailbox_id}")
                res = requests.get(inbox_url, proxies=mail_proxies,
                                   verify=_ssl_verify(), timeout=10)
                if res.status_code == 200:
                    for mail_item in (res.json() or []):
                        m_id = mail_item.get("mail_id")
                        s_name = mail_item.get("sender_name", "").lower()
                        if m_id and m_id not in processed_mail_ids and "openai" in s_name:
                            detail_res = requests.get(
                                f"{cfg.MC_API_BASE}/api/mail"
                                f"?key={cfg.MC_KEY}&id={m_id}",
                                proxies=mail_proxies, verify=_ssl_verify(), timeout=10,
                            )
                            if detail_res.status_code == 200:
                                d = detail_res.json()
                                body = (f"{d.get('subject', '')}\n"
                                        f"{d.get('content', '')}\n"
                                        f"{d.get('html', '')}")
                                code = _extract_otp_code(body)
                                if code:
                                    processed_mail_ids.add(m_id)
                                    print(f"\n[{cfg.ts()}] [SUCCESS] mail_curl ({mask_email(email)})邮箱提取成功: {code}")
                                    return code
            elif mode == "fvia":
                from utils.email_providers.fvia_service import FviaMailService
                fs = FviaMailService(token=jwt, proxies=mail_proxies)
                msgs = fs.get_inbox(email)
                for m in msgs:
                    m_id = m.get("id")
                    if not m_id or m_id in processed_mail_ids:
                        continue

                    subject = str(m.get("subject", ""))
                    sender = str(m.get("from", "")).lower()

                    if "openai" in sender or "openai" in subject.lower() or "chatgpt" in subject.lower():
                        raw_body = fs.get_message_body(email, m_id)
                        clean_body = re.sub(r'<[^>]+>', ' ', raw_body)
                        combined_text = subject + " \n " + clean_body
                        code = None
                        new_format = re.findall(r"enter this code:\s*(\d{6})", combined_text, re.I)
                        if not new_format:
                            new_format = re.findall(r"verification code to continue:\s*(\d{6})", combined_text, re.I)

                        if new_format:
                            code = new_format[-1]
                        else:
                            direct = re.findall(r"Your (?:ChatGPT|OpenAI) code is (\d{6})", combined_text, re.I)
                            if direct:
                                code = direct[-1]
                            else:
                                generic = re.findall(r"\b(\d{6})\b", combined_text)
                                if generic:
                                    code = generic[-1]

                        if code:
                            processed_mail_ids.add(m_id)
                            print(
                                f"\n[{cfg.ts()}] [SUCCESS] Fvia ({mask_email(email)}) 邮箱提取成功: {code}")
                            return code

            elif mode == "temporarymail":
                if not jwt:
                    return ""
                try:
                    from utils.email_providers.temporarymail_service import TemporaryMailService
                    tm_service = TemporaryMailService(proxies=mail_proxies)
                    inbox_dict = tm_service.get_inbox_list(jwt)

                    for m_id, m_info in inbox_dict.items():
                        if m_id in processed_mail_ids:
                            continue

                        sender = str(m_info.get("from", "")).lower()
                        detail = tm_service.get_email_detail(m_id)
                        subject = str(detail.get("subject", ""))
                        a = detail.get("id", "")
                        if "openai" in sender or "openai" in subject.lower() or "chatgpt" in subject.lower():
                            raw_body = tm_service.get_message_body(detail.get("id", ""))
                            clean_body = re.sub(r'<[^>]+>', ' ', raw_body)
                            combined_text = subject + " \n " + clean_body

                            code = None
                            new_format = re.findall(r"enter this code:\s*(\d{6})", combined_text, re.I)
                            if not new_format:
                                new_format = re.findall(r"verification code to continue:\s*(\d{6})", combined_text,
                                                        re.I)

                            if new_format:
                                code = new_format[-1]
                            else:
                                direct = re.findall(r"Your (?:ChatGPT|OpenAI) code is (\d{6})", combined_text, re.I)
                                if direct:
                                    code = direct[-1]
                                else:
                                    generic = re.findall(r"\b(\d{6})\b", combined_text)
                                    if generic:
                                        code = generic[-1]
                            if code:
                                processed_mail_ids.add(m_id)
                                print(f"\n[{cfg.ts()}] [SUCCESS] TemporaryMail ({mask_email(email)}) 邮箱提取成功: {code}")
                                return code
                except Exception:
                    pass

            elif mode == "inboxes":
                if not jwt:
                    return ""
                try:
                    from utils.email_providers.inboxes_service import InboxesService
                    ibs = InboxesService(proxies=mail_proxies)
                    msgs = ibs.get_inbox(email, jwt)
                    for m in msgs:
                        m_id = str(m.get("uid", ""))
                        if not m_id or m_id in processed_mail_ids:
                            continue

                        subject = str(m.get("s", ""))
                        sender = str(m.get("f", "")).lower()

                        if "openai" in sender or "openai" in subject.lower() or "chatgpt" in subject.lower():
                            raw_body = ibs.get_message_body(m_id, user_id=jwt)
                            clean_body = re.sub(r'<[^>]+>', ' ', raw_body)

                            combined_text = subject + " \n " + clean_body

                            code = None
                            new_format = re.findall(r"enter this code:\s*(\d{6})", combined_text, re.I)
                            if not new_format:
                                new_format = re.findall(r"verification code to continue:\s*(\d{6})", combined_text,
                                                        re.I)

                            if new_format:
                                code = new_format[-1]
                            else:
                                direct = re.findall(r"Your (?:ChatGPT|OpenAI) code is (\d{6})", combined_text, re.I)
                                if direct:
                                    code = direct[-1]
                                else:
                                    generic = re.findall(r"\b(\d{6})\b", combined_text)
                                    if generic:
                                        code = generic[-1]
                            if code:
                                processed_mail_ids.add(m_id)
                                print(f"\n[{cfg.ts()}] [SUCCESS] Inboxes.com ({mask_email(email)}) 邮箱提取成功: {code}")
                                return code
                except Exception:
                    pass

            elif mode == "tmailor":
                if not jwt:
                    print(f"\n[{cfg.ts()}] [ERROR] Tmailor 缺少 token，无法提取验证码！")
                    return ""
                try:
                    from utils.email_providers.tmailor_service import TmailorService
                    current_token = getattr(cfg, 'TMAILOR_CURRENT_TOKEN', '')
                    if hasattr(cfg, 'tmailor') and isinstance(cfg.tmailor, dict):
                        current_token = cfg.tmailor.get('current_token', current_token)

                    ts_service = TmailorService(current_token=current_token, proxies=mail_proxies)
                    inbox_data = ts_service.get_inbox(jwt)

                    for mail_item in inbox_data.values():
                        msg_id = str(mail_item.get("uuid", ""))
                        if not msg_id or msg_id in processed_mail_ids:
                            continue

                        sender = str(mail_item.get("sender_name", "")).lower()
                        sender_email = str(mail_item.get("sender_email", "")).lower()
                        subject = str(mail_item.get("subject", ""))

                        if "openai" not in sender and "openai" not in sender_email and "openai" not in subject.lower():
                            continue

                        email_id = mail_item.get("email_id")
                        mail_body, real_subject = ts_service.read_email(jwt, msg_id, email_id)

                        if mail_body or real_subject:
                            clean_body = re.sub(r'<[^>]+>', ' ', str(mail_body))
                            combined_text = str(real_subject) + " \n " + clean_body
                            code = None
                            new_format = re.findall(r"enter this code:\s*(\d{6})", combined_text, re.I)
                            if not new_format:
                                new_format = re.findall(r"verification code to continue:\s*(\d{6})", combined_text,
                                                        re.I)

                            if new_format:
                                code = new_format[-1]
                            else:
                                direct = re.findall(r"Your (?:ChatGPT|OpenAI) code is (\d{6})", combined_text, re.I)
                                if direct:
                                    code = direct[-1]
                                else:
                                    generic = re.findall(r"\b(\d{6})\b", combined_text)
                                    if generic:
                                        code = generic[-1]
                            if code:
                                processed_mail_ids.add(msg_id)
                                print(f"\n[{cfg.ts()}] [SUCCESS] Tmailor ({mask_email(email)}) 提取成功: {code}")
                                return code
                except Exception as e:
                    pass

            # elif mode == "temporam":
            #     if not jwt:
            #         print(f"\n[{cfg.ts()}] [ERROR] Temporam 缺少 token(即邮箱号)，无法提取验证码！")
            #         return ""
            #     try:
            #         from utils.email_providers.temporam_service import TemporamService
            #         tp_service = TemporamService(proxies=mail_proxies)
            #         raw_data = tp_service.get_messages(jwt)
            #
            #         email_list = raw_data.get("data", []) if isinstance(raw_data, dict) else []
            #         for msg in email_list:
            #             msg_id = str(msg.get("id", msg.get("uuid", "")))
            #
            #             if not msg_id or msg_id in processed_mail_ids:
            #                 continue
            #             from_email = str(msg.get("fromEmail", "")).lower()
            #             subject = str(msg.get("subject", ""))
            #             summary = str(msg.get("summary", ""))
            #             full_text = f"{from_email}\n{subject}\n{summary}"
            #
            #
            #             if "openai" not in from_email and "openai" not in full_text.lower():
            #                 continue
            #                 raw_body = tp_service.get_messages_body(msg_id)
            #                 if not raw_body:
            #                     raw_body = str(msg.get("summary", ""))
            #                 clean_body = re.sub(r'<[^>]+>', ' ', raw_body)
            #                 combined_text = subject + " \n " + clean_body
            #
            #                 code = None
            #                 new_format = re.findall(r"enter this code:\s*(\d{6})", combined_text, re.I)
            #                 if not new_format:
            #                     new_format = re.findall(r"verification code to continue:\s*(\d{6})", combined_text,
            #                                             re.I)
            #                 if new_format:
            #                     code = new_format[-1]
            #                 else:
            #                     direct = re.findall(r"Your ChatGPT code is (\d{6})", combined_text, re.I)
            #                     if direct:
            #                         code = direct[-1]
            #                     else:
            #                         generic = re.findall(r"\b(\d{6})\b", combined_text)
            #                         if generic:
            #                             code = generic[-1]
            #             if code:
            #                 processed_mail_ids.add(msg_id)
            #                 print(f"\n[{cfg.ts()}] [SUCCESS] Temporam ({mask_email(email)})邮箱提取成功: {code}")
            #                 return code
            #
            #     except Exception as e:
            #         pass

            elif mode == "cloudmail":
                if getattr(cfg, 'CM_LOCAL_WEBHOOK', False):
                    try:
                        from utils.auth_core import code_pool
                        target_email = email.lower().strip()
                        if target_email in code_pool:
                            raw_text = code_pool.pop(target_email, "")
                            code = ""
                            m = re.search(r"(?<!\d)(\d{6})(?!\d)", raw_text)
                            if m:
                                code = m.group(1)

                            if not code:
                                try:
                                    code = _extract_otp_code(raw_text)
                                except Exception:
                                    pass
                            if code:
                                print(f"[{cfg.ts()}] [SUCCESS] cloudmail (本项目极速) ({mask_email(target_email)}) 提取成功: {code}")
                                return code

                    except ImportError:
                        print(f"[{cfg.ts()}] [ERROR] 无法导入内存池！")
                else:
                    token = get_cm_token(mail_proxies)
                    if token:
                        res = requests.post(
                            f"{cfg.CM_API_URL}/api/public/emailList",
                            headers={"Authorization": token},
                            json={"toEmail": email, "timeSort": "desc", "size": 10},
                            proxies=mail_proxies, timeout=15,
                        )
                        if res.status_code == 200:
                            for m in res.json().get("data", []):
                                m_id = str(m.get("emailId"))
                                if m_id in processed_mail_ids:
                                    continue
                                sender = str(m.get("sendEmail", "")).lower()
                                subject = str(m.get("subject", ""))

                                if "openai" not in sender and "openai" not in subject.lower() and "chatgpt" not in subject.lower():
                                    continue

                                raw_body = str(m.get("content", "") or m.get("text", ""))

                                clean_body = re.sub(r'<[^>]+>', ' ', raw_body)

                                combined_text = subject + " \n " + clean_body
                                code = None
                                new_format = re.findall(r"enter this code:\s*(\d{6})", combined_text, re.I)
                                if not new_format:
                                    new_format = re.findall(r"verification code to continue:\s*(\d{6})", combined_text,
                                                            re.I)

                                if new_format:
                                    code = new_format[-1]
                                else:
                                    direct = re.findall(r"Your (?:ChatGPT|OpenAI) code is (\d{6})", combined_text, re.I)
                                    if direct:
                                        code = direct[-1]
                                    else:
                                        generic = re.findall(r"\b(\d{6})\b", combined_text)
                                        if generic:
                                            code = generic[-1]
                                if code:
                                    processed_mail_ids.add(m_id)
                                    print(f"\n[{cfg.ts()}] [SUCCESS] CloudMail ({mask_email(email)})邮箱提取成功: {code}")
                                    return code
            elif mode == "duckmail":
                duck_use_proxy = getattr(cfg, 'DUCK_USE_PROXY', True)
                duck_proxies = proxies if duck_use_proxy else None
                ds = DuckMailService(proxies=duck_proxies)
                duck_run_mode = getattr(cfg, 'DUCKMAIL_MODE', 'duck_official')

                if duck_run_mode == "duck_official":
                    forward_mode = getattr(cfg, 'DUCKMAIL_FORWARD_MODE', 'Gmail_OAuth')
                    forward_email = getattr(cfg, 'DUCKMAIL_FORWARD_EMAIL', '')
                    if forward_mode == "Gmail_OAuth":
                        otp_code = get_gmail_otp_via_oauth(email, mail_proxies)
                        if otp_code:
                            print(
                                f"\n[{cfg.ts()}] [SUCCESS] Duck转发 (Gmail OAuth) ({mask_email(email)}) 提取成功: {otp_code}")
                            return otp_code

                    # elif forward_mode == "cloudmail":
                    #     if not forward_email:
                    #         print(
                    #             f"\n[{cfg.ts()}] [ERROR] Duckmail 运行失败: 未配置转发邮箱地址({forward_email})！")
                    #         return ""
                    #     token = get_cm_token(mail_proxies)
                    #     if token:
                    #         res = requests.post(
                    #             f"{cfg.CM_API_URL}/api/public/emailList",
                    #             headers={"Authorization": token},
                    #             json={"toEmail": forward_email, "timeSort": "desc", "size": 10},
                    #             proxies=mail_proxies, timeout=15,
                    #         )
                    #         if res.status_code == 200:
                    #             for m in res.json().get("data", []):
                    #                 m_id = str(m.get("emailId"))
                    #                 if m_id in processed_mail_ids:
                    #                     continue
                    #                 content = f"{m.get('subject', '')}\n{m.get('text', '')}"
                    #                 if "openai" not in m.get("sendEmail",
                    #                                          "").lower() and "openai" not in content.lower():
                    #                     continue
                    #
                    #                 target_email = email.lower()
                    #                 if target_email not in str(m).lower() and target_email not in content.lower():
                    #                     continue
                    #
                    #                 code = _extract_otp_code(content)
                    #                 if code:
                    #                     processed_mail_ids.add(m_id)
                    #                     print(f"\n[{cfg.ts()}] [SUCCESS] Duck转发 (CloudMail) 提取成功: {code}")
                    #                     return code
                    #
                    #
                    # elif forward_mode == "freemail":
                    #     if not forward_email:
                    #         print(f"\n[{cfg.ts()}] [ERROR] Duckmail 运行失败: 未配置转发邮箱地址(forward_email)！")
                    #         return ""
                    #     headers = {"Content-Type": "application/json",
                    #                "Authorization": f"Bearer {cfg.FREEMAIL_API_TOKEN}"}
                    #     res = requests.get(f"{cfg.FREEMAIL_API_URL}/api/emails",
                    #                        params={"mailbox": forward_email, "limit": 20},
                    #                        headers=headers, proxies=mail_proxies,
                    #                        verify=_ssl_verify(), timeout=15)
                    #     if res.status_code == 200:
                    #         raw_data = res.json()
                    #         emails_list = (
                    #             raw_data.get("data") or raw_data.get("emails") or raw_data.get("messages") or raw_data.get(
                    #                 "results") or []
                    #             if isinstance(raw_data, dict) else raw_data
                    #         )
                    #         if not isinstance(emails_list, list): emails_list = []
                    #         for mail in emails_list:
                    #             mail_id = str(mail.get("id") or mail.get("timestamp") or mail.get("subject") or "")
                    #             if not mail_id or mail_id in processed_mail_ids: continue
                    #             subject_text = str(mail.get("subject") or mail.get("title") or "")
                    #             if "openai" not in subject_text.lower() and "openai" not in str(mail).lower():
                    #                 continue
                    #             try:
                    #                 dr = requests.get(f"{cfg.FREEMAIL_API_URL}/api/email/{mail_id}",
                    #                                   headers=headers, proxies=mail_proxies,
                    #                                   verify=_ssl_verify(), timeout=15)
                    #                 if dr.status_code == 200:
                    #                     d = dr.json()
                    #                     content = "\n".join(filter(None, [str(d.get("subject") or ""),
                    #                                                       str(d.get("content") or ""),
                    #                                                       str(d.get("html_content") or "")]))
                    #
                    #                     target_email = email.lower()
                    #                     if target_email not in str(d).lower() and target_email not in content.lower():
                    #                         continue
                    #                     code = _extract_otp_code(content)
                    #                     if not code: code = str(d.get("code") or d.get("verification_code") or "")
                    #                     if code:
                    #                         processed_mail_ids.add(mail_id)
                    #                         print(f"[{cfg.ts()}] [SUCCESS] Duck转发 (Freemail) 提取成功: {code}")
                    #                         return code
                    #             except Exception:
                    #                 pass
                    #
                    # elif forward_mode == "mail_curl":
                    #     if not forward_email:
                    #         print(
                    #             f"\n[{cfg.ts()}] [ERROR] Duckmail 运行失败: 未配置转发邮箱地址(forward_email)！")
                    #         return ""
                    #     inbox_url = f"{cfg.MC_API_BASE}/api/inbox?key={cfg.MC_KEY}&mailbox_id={forward_email}"
                    #     res = requests.get(inbox_url, proxies=mail_proxies, verify=_ssl_verify(),
                    #                        timeout=10)
                    #     if res.status_code == 200:
                    #         for mail_item in (res.json() or []):
                    #             m_id = mail_item.get("mail_id")
                    #             s_name = mail_item.get("sender_name", "").lower()
                    #             if m_id and m_id not in processed_mail_ids and "openai" in s_name:
                    #                 detail_res = requests.get(
                    #                     f"{cfg.MC_API_BASE}/api/mail?key={cfg.MC_KEY}&id={m_id}",
                    #                     proxies=mail_proxies, verify=_ssl_verify(), timeout=10)
                    #                 if detail_res.status_code == 200:
                    #                     d = detail_res.json()
                    #                     body = f"{d.get('subject', '')}\n{d.get('content', '')}\n{d.get('html', '')}"
                    #                     target_email = email.lower()
                    #                     if target_email not in str(d).lower() and target_email not in body.lower():
                    #                         continue
                    #
                    #                     code = _extract_otp_code(body)
                    #                     if code:
                    #                         processed_mail_ids.add(m_id)
                    #                         print(f"\n[{cfg.ts()}] [SUCCESS] Duck转发 (mail_curl) 提取成功: {code}")
                    #                         return code
                    # elif forward_mode == "cloudflare_temp_email":
                    #     if not forward_email:
                    #         print(f"[{cfg.ts()}] [ERROR] Duckmail 运行失败: 未配置转发邮箱地址(forward_email)！")
                    #         return ""
                    #     res = requests.get(
                    #         f"{cfg.GPTMAIL_BASE}/admin/mails",
                    #         params={"limit": 20, "offset": 0, "address": forward_email},
                    #         headers={"x-admin-auth": cfg.ADMIN_AUTH},
                    #         verify=_ssl_verify(), timeout=15, proxies=mail_proxies,
                    #     )
                    #
                    #     if res.status_code == 200:
                    #         results = res.json().get("results", [])
                    #         for mail in results:
                    #             m_id = mail.get("id")
                    #             if not m_id or m_id in processed_mail_ids:
                    #                 continue
                    #             parsed = _extract_mail_fields(mail)
                    #             sender_lower = str(parsed.get("sender", "")).lower()
                    #             content = f"{parsed['subject']}\n{parsed['body']}".strip()
                    #             if "openai" not in sender_lower and "openai" not in content.lower():
                    #                 continue
                    #             target_prefix = email.lower().split('@')[0]
                    #             if target_prefix not in sender_lower and target_prefix not in content.lower():
                    #
                    #                 continue
                    #             code = _extract_otp_code(content)
                    #             if code:
                    #                 processed_mail_ids.add(m_id)
                    #                 print(f"\n[{cfg.ts()}] [SUCCESS] Duck转发 (CF 临时邮箱) 提取成功: {code}")
                    #                 return code
                    else:
                        pass

                else:
                    msgs = ds.get_messages(jwt)
                    for m in msgs:
                        content = f"{m.get('subject', '')}\n{m.get('text', '')}\n{ds.strip_html(m.get('html', ''))}"
                        if "openai" in content.lower() or "chatgpt" in content.lower():
                            code = _extract_otp_code(content)
                            if code:
                                print(
                                    f"\n[{cfg.ts()}] [SUCCESS] Duck API ({mask_email(email)}) 提取成功: {code}")
                                return code
            elif mode == "generator_email":
                if not jwt:
                    print(f"\n[{cfg.ts()}] [ERROR] GeneratorEmail 缺少凭证 (surl)，无法提取验证码！")
                    return ""
                try:
                    from utils.email_providers.generator_email_service import GeneratorEmailService
                    ge_service = GeneratorEmailService(proxies=mail_proxies)
                    mail_links = ge_service.get_inbox_links(jwt)

                    for item in mail_links:
                        m_id = item['id']
                        m_href = item['href']

                        if not m_id or m_id in processed_mail_ids:
                            continue

                        code = ge_service.get_code_from_detail(m_href, jwt)

                        if code:
                            processed_mail_ids.add(m_id)
                            print(
                                f"\n[{cfg.ts()}] [SUCCESS] GeneratorEmail ({mask_email(email)})邮箱提取成功: {code}")
                            return code

                except Exception as e:
                    pass

            elif mode == "tempmail":
                if not jwt:
                    print(f"\n[{cfg.ts()}] [ERROR] Tempmail 缺少 token，无法提取验证码！")
                    return ""
                try:
                    from utils.email_providers.tempmail_service import TempmailService
                    tm_service = TempmailService(proxies=mail_proxies)
                    email_list = tm_service.get_inbox(jwt)

                    for msg in email_list:
                        msg_date = str(msg.get("date", 0))
                        if not msg_date or msg_date in processed_mail_ids:
                            continue

                        sender = str(msg.get("from", "")).lower()
                        subject = str(msg.get("subject", ""))
                        body = str(msg.get("body", ""))
                        html = str(msg.get("html") or "")

                        content = "\n".join([sender, subject, body, html])

                        safe_content = re.sub(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", " ", content)

                        if "openai" not in sender and "openai" not in content.lower():
                            continue

                        code = _extract_otp_code(safe_content)
                        if code:
                            processed_mail_ids.add(msg_date)
                            print(f"\n[{cfg.ts()}] [SUCCESS] Tempmail ({mask_email(email)})邮箱提取成功: {code}")
                            return code
                except Exception as e:
                    pass

            elif mode == "tempmail_org":
                if not jwt:
                    print(f"\n[{cfg.ts()}] [ERROR] TempMail.org 缺少 token，无法提取验证码！")
                    return ""
                try:
                    from utils.email_providers.tempmail_org import TempMailOrgService
                    tm_org = TempMailOrgService(proxies=mail_proxies)
                    email_list = tm_org.get_inbox(jwt)

                    for msg in email_list:
                        msg_id = str(msg.get("_id", msg.get("id", "")))
                        if not msg_id or msg_id in processed_mail_ids:
                            continue

                        subject = str(msg.get("subject", ""))
                        bodyPreview = str(msg.get("bodyPreview", ""))
                        content = "\n".join([subject, bodyPreview])
                        code = ""
                        m = re.search(r"(?<!\d)(\d{6})(?!\d)", content)
                        if m:
                            code = m.group(1)

                        if code:
                            processed_mail_ids.add(msg_id)
                            print(f"\n[{cfg.ts()}] [SUCCESS] TempMail.org ({mask_email(email)})邮箱提取成功: {code}")
                            return code
                except Exception as e:
                    pass

            elif mode == "Gmail_OAuth":
                otp_code = get_gmail_otp_via_oauth(email, mail_proxies)
                if otp_code:
                    print(f"\n[{cfg.ts()}] [SUCCESS] Gmail OAuth ({mask_email(email)}) 提取成功: {otp_code}")
                    return otp_code

            elif mode == "imap":
                if not mail_conn:
                    try:
                        mail_conn = _create_imap_conn(proxy_str)
                        mail_conn.login(cfg.IMAP_USER, cfg.IMAP_PASS.replace(" ", ""))
                    except Exception:
                        time.sleep(5)
                        continue

                folders = ["INBOX", "Junk", '"Junk Email"', "Spam",
                           '"[Gmail]/Spam"', '"垃圾邮件"']
                found = False
                for folder in folders:
                    try:
                        mail_conn.noop()
                        status, _ = mail_conn.select(folder, readonly=True)
                        if status != "OK":
                            continue
                        status, messages = mail_conn.search(
                            None, f'(UNSEEN FROM "openai.com" TO "{email}")'
                        )
                        if status != "OK" or not messages[0]:
                            continue
                        for mail_id in reversed(messages[0].split()):
                            if mail_id in processed_mail_ids:
                                continue
                            res, data = mail_conn.fetch(mail_id, "(RFC822)")
                            for resp_part in data:
                                if not isinstance(resp_part, tuple):
                                    continue
                                import email as email_lib
                                msg = email_lib.message_from_bytes(resp_part[1])
                                subject = str(msg.get("Subject", ""))
                                if "=?UTF-8?" in subject:
                                    from email.header import decode_header as _dh
                                    dh = _dh(subject)
                                    subject = "".join(
                                        str(t[0].decode(t[1] or "utf-8")
                                            if isinstance(t[0], bytes) else t[0])
                                        for t in dh
                                    )
                                content = ""
                                if msg.is_multipart():
                                    for part in msg.walk():
                                        if part.get_content_type() == "text/plain":
                                            try:
                                                content += part.get_payload(decode=True).decode("utf-8", "ignore")
                                            except Exception:
                                                pass
                                else:
                                    content = msg.get_payload(decode=True).decode("utf-8", "ignore")
                                to_h = str(msg.get("To", "")).lower()
                                del_h = str(msg.get("Delivered-To", "")).lower()
                                tgt = email.lower()
                                if tgt not in to_h and tgt not in del_h and tgt not in content.lower():
                                    processed_mail_ids.add(mail_id)
                                    continue
                                code = _extract_otp_code(f"{subject}\n{content}")
                                if code:
                                    processed_mail_ids.add(mail_id)
                                    print(f"\n[{cfg.ts()}] [SUCCESS] IMAP ({mask_email(email)})邮箱提取成功: {code}")
                                    try:
                                        mail_conn.logout()
                                    except Exception:
                                        pass
                                    return code
                                processed_mail_ids.add(mail_id)
                        found = True
                        break
                    except imaplib.IMAP4.abort:
                        print(f"\n[{cfg.ts()}] [WARNING] IMAP 连接断开，将在下次循环重连...")
                        mail_conn = None
                        break
                    except Exception as e:
                        if "Spam" in folder:
                            print(f"\n[{cfg.ts()}] [DEBUG] 访问垃圾箱失败: {e}")
                if not found:
                    pass
            elif mode == "openai_cpa":
                if getattr(cfg, 'OPENAI_CPA_WEBHOOK_SECRET', ""):
                    try:
                        from utils.auth_core import code_pool
                        target_email = email.lower().strip()
                        if target_email in code_pool:
                            raw_text = code_pool.get(target_email, "")
                            code = _extract_otp_code(raw_text)
                            if code:
                                code_pool.pop(target_email, None)
                                print(
                                    f"[{cfg.ts()}] [SUCCESS] 项目专属邮箱 OPENAI-CPA ({mask_email(target_email)}) 提取成功: {code}")
                                return code
                    except ImportError:
                        print(f"[{cfg.ts()}] [ERROR] 无法导入内存池！")
            elif mode == "freemail":
                if getattr(cfg, 'FREEMAIL_LOCAL_WEBHOOK', False):
                    try:
                        from utils.auth_core import code_pool
                        target_email = email.lower().strip()
                        if target_email in code_pool:
                            raw_text = code_pool.pop(target_email, "")
                            code = ""
                            m = re.search(r"(?<!\d)(\d{6})(?!\d)", raw_text)
                            if m:
                                code = m.group(1)

                            if not code:
                                try:
                                    code = _extract_otp_code(raw_text)
                                except Exception:
                                    pass
                            if code:
                                print(f"[{cfg.ts()}] [SUCCESS] freemail (本项目极速) ({mask_email(target_email)}) 提取成功: {code}")
                                return code

                    except ImportError:
                        print(f"[{cfg.ts()}] [ERROR] 无法导入内存池！")
                else:
                    headers = {
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {cfg.FREEMAIL_API_TOKEN}"
                    }

                    res = requests.get(f"{cfg.FREEMAIL_API_URL}/api/emails",
                                       params={"mailbox": email, "limit": 20},
                                       headers=headers, proxies=mail_proxies, verify=_ssl_verify(), timeout=15)
                    if res.status_code == 200:
                        raw_data = res.json()
                        emails_list = (
                            raw_data.get("data") or raw_data.get("emails") or
                            raw_data.get("messages") or raw_data.get("results") or []
                            if isinstance(raw_data, dict) else raw_data
                        )
                        if not isinstance(emails_list, list):
                            emails_list = []
                        for mail in emails_list:
                            mail_id = str(mail.get("id") or mail.get("timestamp") or
                                          mail.get("subject") or "")
                            if not mail_id or mail_id in processed_mail_ids:
                                continue
                            subject_text = str(mail.get("subject") or mail.get("title") or "")
                            code = ""
                            m = re.search(r"(?<!\d)(\d{6})(?!\d)", subject_text)
                            if m:
                                code = m.group(1)
                            if not code:
                                code = str(mail.get("code") or mail.get("verification_code") or "")
                            if not code:
                                try:
                                    dr = requests.get(
                                        f"{cfg.FREEMAIL_API_URL}/api/email/{mail_id}",
                                        headers=headers, proxies=mail_proxies,
                                        verify=_ssl_verify(), timeout=15,
                                    )
                                    if dr.status_code == 200:
                                        d = dr.json()
                                        content = "\n".join(filter(None, [
                                            str(d.get("subject") or ""),
                                            str(d.get("content") or ""),
                                            str(d.get("html_content") or ""),
                                        ]))
                                        code = _extract_otp_code(content)
                                except Exception:
                                    pass
                            if code:
                                processed_mail_ids.add(mail_id)
                                print(f"[{cfg.ts()}] [SUCCESS] freemail ({mask_email(email)})邮箱提取成功: {code}")
                                return code
            elif mode == "luckmail":
                if not jwt:
                    print(f"\n[{cfg.ts()}] [ERROR] LuckMail 缺少 token，无法提取验证码！")
                    return ""
                try:
                    from utils.email_providers.luckmail_service import LuckMailService
                    lm_service = LuckMailService(api_key=cfg.LUCKMAIL_API_KEY)

                    code = lm_service.get_code(jwt)
                    if code:
                        processed_mail_ids.add(jwt)
                        print(f"\n[{cfg.ts()}] [SUCCESS] LuckMail ({mask_email(email)})邮箱提取验证码成功: {code}")
                        return code
                except Exception as e:
                    pass
            else:
                if jwt:
                    res = requests.get(
                        f"{base_url}/api/mails",
                        params={"limit": 20, "offset": 0},
                        headers={
                            "Authorization": f"Bearer {jwt}",
                            "Content-Type": "application/json",
                            "Accept": "application/json",
                        },
                        proxies=mail_proxies, verify=_ssl_verify(), timeout=15,
                    )
                else:
                    res = requests.get(
                        f"{base_url}/admin/mails",
                        params={"limit": 20, "offset": 0, "address": email},
                        headers={"x-admin-auth": cfg.ADMIN_AUTH},
                        proxies=mail_proxies, verify=_ssl_verify(), timeout=15,
                    )
                if res.status_code != 200:
                    print(f"\n[{cfg.ts()}] [ERROR] ({mask_email(email)})邮箱接口请求失败 (HTTP {res.status_code}): {res.text}")
                    time.sleep(3)
                    continue
                results = res.json().get("results")
                if results:
                    for mail in results:
                        mail_id = mail.get("id")
                        if not mail_id or mail_id in processed_mail_ids:
                            continue
                        parsed = _extract_mail_fields(mail)

                        content = f"{parsed['subject']}\n{parsed['body']}".strip()
                        if ("openai" not in parsed["sender"].lower() and
                                "openai" not in content.lower()):
                            continue
                        m = re.search(pattern, content)
                        if m:
                            processed_mail_ids.add(mail_id)
                            print(f"[{cfg.ts()}] [SUCCESS] ({mask_email(email)})邮箱提取成功: {m.group(1)}")
                            return m.group(1)
                    pass
                else:
                    pass

        except Exception as e:
            if getattr(cfg, 'GLOBAL_STOP', False):
                return None
            if "timeout" in str(e).lower() or "time out" in str(e).lower():
                print(f"[{cfg.ts()}] [ERROR] 代理节点严重超时，终止本次邮箱查询。")
                return ""
            print(f"[{cfg.ts()}] [ERROR] 邮件循环发生异常: {str(e)}")
            import traceback
            traceback.print_exc()

        if attempt > 0 and attempt % 3 == 0:
            print(f"[{cfg.ts()}] [INFO] 仍在查询({mask_email(email)})邮箱，暂未收到验证码 (已尝试 {attempt + 1}/{max_attempts})...")
        time.sleep(3)

    print(f"\n[{cfg.ts()}] [ERROR] ({mask_email(email)})邮箱接收验证码超时")
    return ""
