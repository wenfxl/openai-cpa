import os
import time
import random
import threading
from typing import Any, Dict, List, Optional
from curl_cffi import requests
from utils import db_manager
from utils import config as cfg
from utils.auth_core import generate_payload

class UserStoppedError(Exception): pass

def _ssl_verify() -> bool: return True

def _info(msg):
    print(f"[{cfg.ts()}] [SMSBOWER] {msg}")

def _warn(msg):
    print(f"[{cfg.ts()}] [SMSBOWER] {msg}")

def _raise_if_stopped() -> None:
    if getattr(cfg, 'GLOBAL_STOP', False):
        raise UserStoppedError("stopped")

def _sleep_interruptible(sec: float) -> bool:
    for _ in range(int(sec * 10)):
        if getattr(cfg, 'GLOBAL_STOP', False):
            return True
        time.sleep(0.1)
    return False

def _post_with_retry(session, url: str, headers: dict = None, json_body: dict = None, proxies: Any = None,
                     timeout: int = 30, retries: int = 1):
    for attempt in range(retries + 1):
        try:
            return session.post(url, headers=headers, json=json_body, proxies=proxies, timeout=timeout)
        except Exception as e:
            if attempt == retries:
                raise
            time.sleep(1.5)

def _extract_next_url(vj: dict) -> str:
    if not isinstance(vj, dict): return ""
    page = vj.get("page")
    if isinstance(page, dict) and page.get("url"): return str(page["url"])
    return str(vj.get("continue_url") or "")

def _follow_redirect_chain(session, url: str, proxies: Any):
    return "", url

def _smsbower_enabled() -> bool:
    return bool(getattr(cfg, 'SMSBOWER_ENABLED', False)) and bool(_smsbower_api_key())

def _smsbower_api_key() -> str:
    return str(getattr(cfg, 'SMSBOWER_API_KEY', '')).strip()

def _smsbower_base_url() -> str:
    url = str(getattr(cfg, 'SMSBOWER_BASE_URL', '')).strip()
    return url or "https://smsbower.page/stubs/handler_api.php"

def _smsbower_min_balance_limit() -> float:
    return float(getattr(cfg, 'SMSBOWER_MIN_BALANCE', 0.0))

def _smsbower_order_max_price() -> float:
    return float(getattr(cfg, 'SMSBOWER_MAX_PRICE', 0.0))

def _smsbower_order_min_price() -> float:
    return float(getattr(cfg, 'SMSBOWER_MIN_PRICE', 0.0))

def _smsbower_reuse_enabled() -> bool:
    return bool(getattr(cfg, 'SMSBOWER_REUSE_PHONE', True))

def _smsbower_auto_pick_country() -> bool:
    return bool(getattr(cfg, 'SMSBOWER_AUTO_PICK_COUNTRY', True))

def _smsbower_poll_timeout_sec() -> int:
    return int(getattr(cfg, 'SMSBOWER_POLL_TIMEOUT_SEC', 180))

def _smsbower_max_tries() -> int:
    return int(getattr(cfg, 'SMSBOWER_MAX_TRIES', 3))

def _smsbower_country_timeout_limit() -> int: return 2

def _smsbower_country_cooldown_sec() -> int: return 900

def _smsbower_price_cache_ttl_sec() -> int: return 90

def _smsbower_reuse_ttl_sec() -> int: return 1200

def _smsbower_reuse_max_uses() -> int:
    return int(getattr(cfg, 'SMSBOWER_REUSE_MAX', 2))

def _smsbower_mark_ready_enabled() -> bool: return True

_SMSBOWER_SERVICE_CACHE: str = ""
_SMSBOWER_COUNTRY_CACHE: dict[str, int] = {}
_SMSBOWER_VERIFY_LOCK = threading.Lock()
_SMSBOWER_STATS_LOCK = threading.Lock()
_SMSBOWER_RUNTIME: dict[str, float] = {
    "spent_total_usd": 0.0,
    "balance_last_usd": -1.0,
    "balance_start_usd": -1.0,
    "updated_at": 0.0,
}
_SMSBOWER_REUSE_LOCK = threading.Lock()
_SMSBOWER_REUSE_STATE: dict[str, Any] = {
    "activation_id": "", "phone": "", "service": "", "country": -1, "uses": 0, "updated_at": 0.0,
}
_SMSBOWER_COUNTRY_LOCK = threading.Lock()
_SMSBOWER_COUNTRY_TIMEOUTS: dict[int, int] = {}
_SMSBOWER_COUNTRY_COOLDOWN_UNTIL: dict[int, float] = {}
_SMSBOWER_COUNTRY_METRICS: dict[int, dict[str, float]] = {}
_SMSBOWER_PRICE_CACHE_LOCK = threading.Lock()
_SMSBOWER_PRICE_CACHE: dict[str, Any] = {"service": "", "updated_at": 0.0, "items": []}
_SMSBOWER_COUNTRY_NAME_CACHE: Dict[int, str] = {}
_SMSBOWER_COUNTRY_NAMES_MAP: dict[int, str] = {}

_OPENAI_SMS_BLOCKED_COUNTRY_IDS = {0, 3, 14, 20, 51, 57, 110, 113, 191}

def _load_reuse_state_from_db():
    global _SMSBOWER_REUSE_STATE
    saved = db_manager.get_sys_kv("smsbower_reuse_data")
    if saved and isinstance(saved, dict):
        with _SMSBOWER_REUSE_LOCK:
            _SMSBOWER_REUSE_STATE.update(saved)


_load_reuse_state_from_db()
def _sync_reuse_to_db():
    db_manager.set_sys_kv("smsbower_reuse_data", _SMSBOWER_REUSE_STATE)


def _smsbower_reuse_get(service: str, country: int) -> tuple[str, str, int]:
    now = time.time()
    ttl = _smsbower_reuse_ttl_sec()
    max_uses = _smsbower_reuse_max_uses()
    svc = str(service or "").strip()
    ctry = int(country)
    with _SMSBOWER_REUSE_LOCK:
        aid = str(_SMSBOWER_REUSE_STATE.get("activation_id") or "").strip()
        phone = str(_SMSBOWER_REUSE_STATE.get("phone") or "").strip()
        state_svc = str(_SMSBOWER_REUSE_STATE.get("service") or "").strip()
        try:
            state_country = int(_SMSBOWER_REUSE_STATE.get("country") or -1)
        except Exception:
            state_country = -1
        uses = int(_SMSBOWER_REUSE_STATE.get("uses") or 0)
        updated = float(_SMSBOWER_REUSE_STATE.get("updated_at") or 0.0)

        valid = bool(aid and phone) and (state_svc == svc) and (state_country == ctry) and (uses < max_uses) and (
                    updated > 0 and (now - updated) <= ttl)
        if not valid: return "", "", 0
        return aid, phone, uses


def _smsbower_reuse_set(activation_id: str, phone: str, service: str, country: int) -> None:
    aid = str(activation_id or "").strip()
    ph = str(phone or "").strip()
    if not aid or not ph: return
    with _SMSBOWER_REUSE_LOCK:
        _SMSBOWER_REUSE_STATE.update(
            {"activation_id": aid, "phone": ph, "service": str(service or "").strip(), "country": int(country),
             "uses": 0, "updated_at": time.time()})
    _sync_reuse_to_db()


def _smsbower_reuse_touch(increase: bool = False) -> None:
    with _SMSBOWER_REUSE_LOCK:
        if increase: _SMSBOWER_REUSE_STATE["uses"] = int(_SMSBOWER_REUSE_STATE.get("uses") or 0) + 1
        _SMSBOWER_REUSE_STATE["updated_at"] = time.time()
    _sync_reuse_to_db()


def _smsbower_reuse_clear() -> None:
    with _SMSBOWER_REUSE_LOCK:
        _SMSBOWER_REUSE_STATE.update(
            {"activation_id": "", "phone": "", "service": "", "country": -1, "uses": 0, "updated_at": 0.0})
    _sync_reuse_to_db()


def _smsbower_country_is_on_cooldown(country_id: int) -> bool:
    cid, now = int(country_id), time.time()
    with _SMSBOWER_COUNTRY_LOCK:
        until = float(_SMSBOWER_COUNTRY_COOLDOWN_UNTIL.get(cid) or 0.0)
        if until <= 0: return False
        if until <= now:
            _SMSBOWER_COUNTRY_COOLDOWN_UNTIL.pop(cid, None)
            _SMSBOWER_COUNTRY_TIMEOUTS.pop(cid, None)
            return False
        return True


def _smsbower_country_mark_success(country_id: int) -> None:
    with _SMSBOWER_COUNTRY_LOCK: _SMSBOWER_COUNTRY_TIMEOUTS.pop(int(country_id), None)


def _smsbower_country_mark_timeout(country_id: int) -> bool:
    cid, now = int(country_id), time.time()
    with _SMSBOWER_COUNTRY_LOCK:
        current = int(_SMSBOWER_COUNTRY_TIMEOUTS.get(cid) or 0) + 1
        _SMSBOWER_COUNTRY_TIMEOUTS[cid] = current
        if current < _smsbower_country_timeout_limit(): return False
        _SMSBOWER_COUNTRY_TIMEOUTS[cid] = 0
        _SMSBOWER_COUNTRY_COOLDOWN_UNTIL[cid] = now + float(_smsbower_country_cooldown_sec())
        return True


def _smsbower_country_record_result(country_id: int, success: bool, reason: str = "") -> None:
    cid, now, low = int(country_id), time.time(), str(reason or "").strip().lower()
    with _SMSBOWER_COUNTRY_LOCK:
        row = _SMSBOWER_COUNTRY_METRICS.setdefault(cid,
                                                   {"attempts": 0.0, "success": 0.0, "timeout": 0.0, "send_fail": 0.0,
                                                    "verify_fail": 0.0, "other_fail": 0.0, "last_used_at": 0.0,
                                                    "last_success_at": 0.0})
        row["attempts"] += 1.0
        row["last_used_at"] = now
        if success:
            row["success"] += 1.0
            row["last_success_at"] = now
            return
        if "接码超时" in low or "status_wait_code" in low or "timeout" in low:
            row["timeout"] += 1.0
        elif "发送手机验证码失败" in low:
            row["send_fail"] += 1.0
        elif "手机验证码校验失败" in low:
            row["verify_fail"] += 1.0
        else:
            row["other_fail"] += 1.0


def _smsbower_country_score(country_id: int, *, cost: float, count: int, preferred_country: int) -> float:
    cid = int(country_id)
    if cid in _OPENAI_SMS_BLOCKED_COUNTRY_IDS or count <= 0 or _smsbower_country_is_on_cooldown(cid): return -1e9
    now = time.time()
    with _SMSBOWER_COUNTRY_LOCK:
        stats = dict(_SMSBOWER_COUNTRY_METRICS.get(cid) or {})
        timeout_streak = int(_SMSBOWER_COUNTRY_TIMEOUTS.get(cid) or 0)

    attempts = max(0.0, float(stats.get("attempts") or 0.0))
    if attempts <= 0:
        score = 0.55 * 80.0 + 9.0
    else:
        score = (stats.get("success", 0) / attempts) * 80.0 - (stats.get("timeout", 0) / attempts) * 70.0 - (
                    stats.get("send_fail", 0) / attempts) * 45.0 - (stats.get("verify_fail", 0) / attempts) * 30.0 - (
                            stats.get("other_fail", 0) / attempts) * 20.0
        score += max(0.0, 6.0 - min(6.0, attempts))

    score -= float(timeout_streak) * 8.0
    if cost >= 0: score -= min(5.0, float(cost)) * 10.0
    score += min(20000, max(0, int(count))) / 2000.0
    if cid == int(preferred_country): score += 3.0
    last_succ = float(stats.get("last_success_at") or 0.0)
    if last_succ > 0:
        age = max(0.0, now - last_succ)
        if age < 900:
            score += 4.0
        elif age < 3600:
            score += 2.0
    return float(score)

def _smsbower_request(action: str, *, proxies: Any, params: Optional[Dict[str, Any]] = None, timeout: int = 25) -> \
tuple[bool, str, Any]:
    key = _smsbower_api_key()
    if not key: return False, "NO_KEY", None
    query: Dict[str, Any] = {"action": str(action or "").strip(), "api_key": key}
    if params:
        for k, v in params.items():
            if v is not None and str(v).strip() != "": query[str(k)] = str(v).strip() if isinstance(v, str) else v
    try:
        resp = requests.get(_smsbower_base_url(), params=query, proxies=proxies, verify=_ssl_verify(), timeout=timeout,
                            impersonate="chrome131")
    except Exception as e:
        return False, f"REQUEST_ERROR:{e}", None
    code, text = int(getattr(resp, "status_code", 0) or 0), str(getattr(resp, "text", "") or "").strip()
    try:
        data = resp.json()
    except:
        data = None
    if not (200 <= code < 300): return False, text or f"HTTP {code}", data
    return True, text, data


def smsbower_get_balance(proxies: Any = None) -> tuple[float, str]:
    _info("正在查询 SmsBower 账户余额...")
    ok, text, data = _smsbower_request("getBalance", proxies=proxies, timeout=20)
    if not ok:
        _warn(f"SmsBower 余额查询网络失败: {text}")
        return -1.0, str(text or "getBalance failed")
    if text.upper().startswith("ACCESS_BALANCE:"):
        try:
            val = float(text.split(":", 1)[1].strip())
            _smsbower_update_runtime(balance=val, init_start=True)
            _info(f"✅ SmsBower 当前余额: {val:.2f} $")
            return val, ""
        except:
            pass
    _warn(f"SmsBower 无法解析余额: {text}")
    return -1.0, text or "无法解析余额"


def _smsbower_update_runtime(*, spent_delta: float = 0.0, balance: Optional[float] = None,
                             init_start: bool = False) -> None:
    delta = max(0.0, float(spent_delta or 0.0))
    with _SMSBOWER_STATS_LOCK:
        if delta > 0: _SMSBOWER_RUNTIME["spent_total_usd"] = round(
            _SMSBOWER_RUNTIME.get("spent_total_usd", 0.0) + delta, 4)
        if balance is not None and balance >= 0:
            _SMSBOWER_RUNTIME["balance_last_usd"] = round(balance, 4)
            if init_start and _SMSBOWER_RUNTIME.get("balance_start_usd", -1.0) < 0:
                _SMSBOWER_RUNTIME["balance_start_usd"] = round(balance, 4)
        _SMSBOWER_RUNTIME["updated_at"] = time.time()


def _smsbower_resolve_service_code(proxies: Any) -> str:
    raw = str(getattr(cfg, 'SMSBOWER_SERVICE', 'dr')).strip()
    selected = raw if raw else "dr"

    _info(f"SmsBower 使用手动填写的服务代码: {selected}")
    return selected


def _smsbower_resolve_country_id(proxies: Any) -> int:
    raw = str(getattr(cfg, 'SMSBOWER_COUNTRY', '0')).strip()
    return max(0, int(raw)) if raw.isdigit() else 0


def _get_country_names_map(proxies: Any) -> dict[int, str]:
    global _SMSBOWER_COUNTRY_NAMES_MAP
    if _SMSBOWER_COUNTRY_NAMES_MAP:
        return _SMSBOWER_COUNTRY_NAMES_MAP

    _info("正在同步 SmsBower 全球国家名称对照表...")
    ok, text, data = _smsbower_request("getCountries", proxies=proxies, timeout=20)

    mapping = {}
    if ok and data:
        items = data.values() if isinstance(data, dict) else data if isinstance(data, list) else []
        for item in items:
            if isinstance(item, dict):
                try:
                    cid = int(item.get("id"))
                    name = item.get("chn") or item.get("eng") or item.get("rus") or f"国家{cid}"
                    mapping[cid] = name
                except Exception:
                    continue

    if mapping:
        _SMSBOWER_COUNTRY_NAMES_MAP = mapping
    return _SMSBOWER_COUNTRY_NAMES_MAP


def _smsbower_prices_by_service(service_code: str, proxies: Any, *, force_refresh: bool = False) -> list[
    dict[str, Any]]:
    svc = str(service_code or "dr").strip()
    now = time.time()

    with _SMSBOWER_PRICE_CACHE_LOCK:
        if not force_refresh and _SMSBOWER_PRICE_CACHE.get("service") == svc and (
                now - _SMSBOWER_PRICE_CACHE.get("updated_at", 0)) <= _smsbower_price_cache_ttl_sec():
            return [dict(x) for x in _SMSBOWER_PRICE_CACHE.get("items", [])]

    name_map = _get_country_names_map(proxies)
    ok, text, data = _smsbower_request("getPricesV3", proxies=proxies, params={"service": svc})

    if not ok or not isinstance(data, dict):
        return []

    country_groups = {}

    for cid, entry in data.items():
        if not str(cid).isdigit() or int(cid) in _OPENAI_SMS_BLOCKED_COUNTRY_IDS:
            continue

        providers = entry.get(svc)
        if not isinstance(providers, dict):
            continue

        for pid, info in providers.items():
            try:
                c_id = int(cid)
                c_cost = float(info.get("price", -1))
                c_count = int(info.get("count", 0))

                if c_count > 0:
                    if c_id not in country_groups:
                        country_groups[c_id] = {
                            "country": c_id,
                            "name": name_map.get(c_id, f"国家{c_id}"),
                            "total_count": 0,
                            "routes": []
                        }
                    country_groups[c_id]["routes"].append({
                        "provider": str(pid),
                        "cost": c_cost,
                        "count": c_count
                    })
                    country_groups[c_id]["total_count"] += c_count
            except:
                continue

    rows = list(country_groups.values())
    for r in rows:
        r["routes"].sort(key=lambda x: (x["cost"], -x["count"]))
    if rows:
        rows.sort(key=lambda x: (x["routes"][0]["cost"] if x["routes"] else 999, -x["total_count"]))
        with _SMSBOWER_PRICE_CACHE_LOCK:
            _SMSBOWER_PRICE_CACHE.update({"service": svc, "updated_at": now, "items": rows})
    return rows

def _smsbower_pick_country_id(proxies: Any, *, service_code: str, preferred_country: int,
                              exclude_country_ids: Optional[set[int]] = None, force_refresh: bool = False) -> int:
    if not _smsbower_auto_pick_country(): return preferred_country
    rows = _smsbower_prices_by_service(service_code, proxies, force_refresh=force_refresh)
    if not rows: return preferred_country
    scored = []
    excluded = exclude_country_ids or set()
    for r in rows:
        cid = int(r.get("country", -1))
        if cid < 0 or cid in excluded: continue
        score = _smsbower_country_score(cid, cost=r.get("cost", -1.0), count=r.get("count", 0),
                                        preferred_country=preferred_country)
        if score > -1e8: scored.append((score, cid, r.get("cost", -1.0), r.get("count", 0)))
    if not scored: return preferred_country
    scored.sort(key=lambda x: (-x[0], x[2] if x[2] >= 0 else 999999, -x[3], x[1]))
    return int(scored[0][1])


def _smsbower_set_status(activation_id: str, status: int, proxies: Any) -> str:
    if not activation_id: return ""
    _, text, _ = _smsbower_request("setStatus", proxies=proxies, params={"id": activation_id, "status": int(status)},
                                   timeout=20)
    return str(text or "")


def _smsbower_get_number(proxies: Any, *, service_code: str, country_id: int) -> tuple[str, str, str, str]:
    if country_id in _OPENAI_SMS_BLOCKED_COUNTRY_IDS:
        return "", "", f"COUNTRY_BLOCKED: 国家ID {country_id} 被拉黑", ""

    limit_max = _smsbower_order_max_price()
    limit_min = _smsbower_order_min_price()
    if limit_max > 0 or limit_min > 0:
        rows = _smsbower_prices_by_service(service_code, proxies)
        actual_cost = -1.0
        for r in rows:
            if r.get("country") == country_id:
                actual_cost = float(r.get("cost", -1.0))
                break

        if actual_cost > 0:
            if limit_max > 0 and actual_cost > limit_max:
                return "", "", f"价格拦截: 该国当前价格 ({actual_cost}$) 高于您的最高限价 ({limit_max}$)", ""
            if limit_min > 0 and actual_cost < limit_min:
                return "", "", f"价格拦截: 该国当前价格 ({actual_cost}$) 低于您的最低限价 ({limit_min}$)", ""

    params = {"service": service_code, "country": country_id}
    operator_id = str(getattr(cfg, 'SMSBOWER_OPERATOR', '')).strip()
    if operator_id:
        params["operator"] = operator_id
    if _smsbower_order_max_price() > 0: params["maxPrice"] = _smsbower_order_max_price()
    if _smsbower_order_min_price() > 0: params["minPrice"] = _smsbower_order_min_price()
    ok, text, data = _smsbower_request("getNumberV2", proxies=proxies, params=params, timeout=30)

    if ok and isinstance(data, dict):
        aid = str(data.get("activationId", ""))
        phone_raw = str(data.get("phoneNumber", ""))
        cost = str(data.get("activationCost", ""))

        if aid and phone_raw:
            phone = phone_raw if phone_raw.startswith("+") else f"+{phone_raw}"
            return aid, phone, "", cost

    elif ok and str(text).upper().startswith("ACCESS_NUMBER:"):
        parts = text.split(":", 2)
        if len(parts) >= 3:
            phone = parts[2].strip() if parts[2].strip().startswith("+") else f"+{parts[2].strip()}"
            return parts[1].strip(), phone, "", "未知"

    return "", "", text or str(data) or "NO_NUMBERS", ""


def _smsbower_poll_code(activation_id: str, proxies: Any) -> str:
    timeout_sec, started_at = _smsbower_poll_timeout_sec(), time.time()
    _info(f"⏳ 开始等待 SmsBower 短信验证码 (最大等待 {timeout_sec} 秒)...")
    last_print_time = time.time()

    while time.time() - started_at < timeout_sec:
        _raise_if_stopped()
        ok, text, data = _smsbower_request("getStatus", proxies=proxies, params={"id": activation_id}, timeout=20)
        upper = str(text or "").strip().upper()
        if time.time() - last_print_time > 8:
            _info(f"🔄 仍在等待短信中... (当前平台状态: {upper})")
            last_print_time = time.time()

        if ok and upper.startswith("STATUS_OK"):
            code = text.split(":", 1)[1].strip() if ":" in text else ""
            _info(f"🎉 成功接收到短信验证码: {code}")
            return code

        if any(x in upper for x in ["STATUS_CANCEL", "NO_ACTIVATION", "BAD_STATUS"]):
            _warn(f"❌ 接码被平台取消或状态异常: {text}")
            return ""

        _sleep_interruptible(3.0)

    _warn("⚠️ SmsBower 接码彻底超时！")
    return ""


def try_verify_phone_via_smsbower(session: requests.Session, *, proxies: Any, hint_url: str = "", device_id: str = "", user_agent: str = "", run_ctx: dict = None, proxy: Optional[str] = None) -> tuple[bool, str]:
    if not _smsbower_enabled():
        return False, "SmsBower 未开启或未配置 API Key"

    max_tries, last_reason, lock_acquired = _smsbower_max_tries(), "SmsBower 验证失败", False
    verify_balance_start = -1.0

    started = time.time()
    while True:
        _raise_if_stopped()
        if _SMSBOWER_VERIFY_LOCK.acquire(timeout=0.5):
            lock_acquired = True
            break
        if time.time() - started >= 180:
            return False, "SmsBower 手机验证排队超时"

    def _verify_once(activation_id: str, phone_number: str, *, source: str, close_on_success: bool,
                     cancel_on_fail: bool) -> tuple[bool, str, str]:
        finished, fail_reason = False, ""
        try:
            send_hdrs = {"referer": "https://auth.openai.com/add-phone", "accept": "application/json",
                         "content-type": "application/json"}

            send_sentinel = generate_payload(
                did=device_id,
                flow="authorize_continue",
                proxy=proxy,
                user_agent=user_agent,
                impersonate="chrome110",
                ctx=run_ctx
            )

            if send_sentinel: send_hdrs["openai-sentinel-token"] = send_sentinel

            if _smsbower_mark_ready_enabled(): _smsbower_set_status(activation_id, 1, proxies)

            _info(f"[{source}] 正在向 OpenAI 提交号码 {phone_number} 并请求发送短信...")
            send_resp = _post_with_retry(session, "https://auth.openai.com/api/accounts/add-phone/send",
                                         headers=send_hdrs, json_body={"phone_number": phone_number}, proxies=proxies)
            if send_resp.status_code != 200:
                fail_reason = f"发送失败 HTTP {send_resp.status_code}"
                _warn(f"[{source}] ❌ 号码提交被 OpenAI 拦截: {fail_reason}")
                return False, "", fail_reason

            _info(f"[{source}] ✅ OpenAI 接受了号码，开始轮询验证码...")
            sms_code = _smsbower_poll_code(activation_id, proxies)
            if not sms_code:
                return False, "", "接码超时"

            verify_hdrs = {"referer": "https://auth.openai.com/phone-verification", "accept": "application/json",
                           "content-type": "application/json"}

            send_sentinel = generate_payload(
                did=device_id,
                flow="authorize_continue",
                proxy=proxy,
                user_agent=user_agent,
                impersonate="chrome110",
                ctx=run_ctx
            )

            if send_sentinel: verify_hdrs["openai-sentinel-token"] = send_sentinel

            verify_resp = _post_with_retry(session, "https://auth.openai.com/api/accounts/phone-otp/validate",
                                           headers=verify_hdrs, json_body={"code": sms_code}, proxies=proxies)
            if verify_resp.status_code != 200:
                _warn(f"[{source}] ❌ 验证码校验失败 HTTP {verify_resp.status_code}")
                return False, "", f"校验失败 HTTP {verify_resp.status_code}"

            _info(f"[{source}] 🎊 验证码在 OpenAI 侧核验通过！")
            if close_on_success:
                _smsbower_set_status(activation_id, 6, proxies)
            else:
                _smsbower_set_status(activation_id, 3, proxies)
            finished = True

            vj = verify_resp.json() if verify_resp.status_code == 200 else {}
            next_url = _extract_next_url(vj)
            if not next_url: next_url = hint_url
            return True, next_url, ""
        except UserStoppedError:
            raise
        except Exception as e:
            return False, "", f"验证异常: {e}"
        finally:
            if not finished and cancel_on_fail: _smsbower_set_status(activation_id, 8, proxies)

    try:
        verify_balance_start, _ = smsbower_get_balance(proxies)
        service_code = _smsbower_resolve_service_code(proxies)
        pref_country = _smsbower_resolve_country_id(proxies)
        country_id = _smsbower_pick_country_id(proxies, service_code=service_code, preferred_country=pref_country)
        excluded_countries = set()
        reuse_on = _smsbower_reuse_enabled()

        _info(f"SmsBower 国家分配: 目标国家ID为 {country_id} (服务代码: {service_code})")

        if reuse_on:
            rid, rphone, rused = _smsbower_reuse_get(service_code, country_id)
            if rid and rphone:
                _info(f"♻️ 尝试复用旧号码: {rphone} (已使用 {rused} 次)")
                ok_r, next_r, reason_r = _verify_once(rid, rphone, source="复用号码", close_on_success=False,
                                                      cancel_on_fail=True)
                if ok_r:
                    _smsbower_country_mark_success(country_id)
                    _smsbower_country_record_result(country_id, True)
                    _smsbower_reuse_touch(increase=True)
                    return True, next_r
                _smsbower_country_record_result(country_id, False, reason_r)
                if "超时" in str(reason_r):
                    if _smsbower_country_mark_timeout(country_id):
                        country_id = _smsbower_pick_country_id(proxies, service_code=service_code,
                                                               preferred_country=pref_country)
                # _smsbower_set_status(rid, 8, proxies)
                _smsbower_reuse_clear()

        for attempt in range(1, max_tries + 1):
            _raise_if_stopped()
            _info(f"[{attempt}/{max_tries}] 正在向 SmsBower 请求新号码...")
            aid, phone, gerr, cost = _smsbower_get_number(proxies, service_code=service_code, country_id=country_id)
            if not aid:
                last_reason = f"取号失败 {gerr}"
                _warn(f"⚠️ 第 {attempt}/{max_tries} 次取号失败: {gerr}")
                if attempt < max_tries and _smsbower_auto_pick_country() and "NO_NUMBERS" in str(gerr).upper():
                    excluded_countries.add(country_id)
                    country_id = _smsbower_pick_country_id(proxies, service_code=service_code,
                                                           preferred_country=pref_country,
                                                           exclude_country_ids=excluded_countries, force_refresh=True)
                    _info(f"🔄 自动切换至备选国家 ID: {country_id}")

                _sleep_interruptible(2.0)
                continue

            cost_display = f"{cost} $" if cost and cost != "未知" else "未知"
            _info(f"📱 成功取到新号码: {phone} (订单ID: {aid} | 扣费: {cost_display})")
            ok_n, next_n, reason_n = _verify_once(aid, phone, source=f"新号#{attempt}", close_on_success=not reuse_on,
                                                  cancel_on_fail=True)
            if ok_n:
                _smsbower_country_mark_success(country_id)
                _smsbower_country_record_result(country_id, True)
                if reuse_on:
                    _smsbower_reuse_set(aid, phone, service_code, country_id)
                    _info(f"📥 验证成功！号码 {phone} 已挂起并存入复用池。")
                return True, next_n

            last_reason = reason_n
            _smsbower_country_record_result(country_id, False, last_reason)
            if reuse_on and "超时" in str(last_reason):
                if _smsbower_country_mark_timeout(country_id):
                    _smsbower_set_status(aid, 8, proxies)
                    country_id = _smsbower_pick_country_id(proxies, service_code=service_code,
                                                           preferred_country=pref_country)
                # else:
                #     _smsbower_reuse_set(aid, phone, service_code, country_id)
                #     _smsbower_set_status(aid, 3, proxies)
                #     _warn("⚠️ 新购号码接码超时，已保留该号码供下一次复用。")
                #     return False, "接码超时，保留复用"
            # if reuse_on: _smsbower_set_status(aid, 8, proxies)
            _sleep_interruptible(1.5)

        return False, last_reason
    finally:
        try:
            b_end, _ = smsbower_get_balance(proxies)
            if verify_balance_start >= 0 and b_end >= 0:
                _smsbower_update_runtime(spent_delta=max(0, verify_balance_start - b_end), balance=b_end)
        except:
            pass
        if lock_acquired: _SMSBOWER_VERIFY_LOCK.release()
def handle_smsbower_verification(session, proxies, hint_url="", device_id: str = "", user_agent: str = "", run_ctx: dict = None, proxy: Optional[str] = None):
    return try_verify_phone_via_smsbower(session, proxies=proxies, hint_url=hint_url, device_id=device_id, user_agent=user_agent, run_ctx=run_ctx, proxy=proxy)