from __future__ import annotations

import json
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit

from curl_cffi import requests

from utils import config as cfg


DEFAULT_API_BASE = "https://remail.aishop6.com"
REQUEST_TIMEOUT = 20
ORDER_WAIT_SECONDS = 30
_CONTEXT_LOCK = threading.RLock()
_CONTEXT_CACHE: dict[str, dict[str, Any]] = {}
_FINAL_ORDER_STATUSES = {"failed", "refunded", "closed", "cancelled", "canceled"}


class RemailError(RuntimeError):
    pass


class RemailAPIError(RemailError):
    def __init__(
        self,
        status_code: int,
        raw_message: str,
        *,
        code: str = "",
        request_id: str = "",
        method: str = "",
        path: str = "",
    ):
        self.status_code = int(status_code or 0)
        self.raw_message = str(raw_message or "").strip()
        self.code = str(code or "").strip()
        self.request_id = str(request_id or "").strip()
        self.method = str(method or "").strip().upper()
        self.path = str(path or "").strip()
        super().__init__(_format_api_error(self))


@dataclass
class RemailAccount:
    email: str
    service_token: str
    order_no: str
    project_id: int
    email_suffix: str


def _cache_key(email: str) -> str:
    return str(email or "").strip().lower()


def _context_dir() -> Path:
    root = Path(getattr(cfg, "BASE_DIR", Path(__file__).resolve().parents[2]))
    target = root / "data" / "remail_contexts"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _context_path(email: str) -> Path:
    safe = re.sub(r"[^a-z0-9_.-]+", "_", _cache_key(email))
    return _context_dir() / f"{safe}.json"


def _save_context(account: RemailAccount) -> None:
    payload = asdict(account)
    payload["source"] = "remail"
    path = _context_path(account.email)
    temp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
    with _CONTEXT_LOCK:
        _CONTEXT_CACHE[_cache_key(account.email)] = payload


def _load_context(email: str) -> Optional[RemailAccount]:
    key = _cache_key(email)
    if not key:
        return None
    with _CONTEXT_LOCK:
        cached = _CONTEXT_CACHE.get(key)
    payload = dict(cached) if isinstance(cached, dict) else None
    if payload is None:
        path = _context_path(email)
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    payload = loaded
            except Exception:
                payload = None
    if not isinstance(payload, dict):
        return None
    service_token = str(payload.get("service_token") or "").strip()
    account_email = str(payload.get("email") or "").strip()
    if not service_token or not account_email:
        return None
    try:
        project_id = int(payload.get("project_id") or 0)
    except (TypeError, ValueError):
        project_id = 0
    account = RemailAccount(
        email=account_email,
        service_token=service_token,
        order_no=str(payload.get("order_no") or "").strip(),
        project_id=project_id,
        email_suffix=str(payload.get("email_suffix") or "").strip(),
    )
    with _CONTEXT_LOCK:
        _CONTEXT_CACHE[key] = asdict(account)
    return account


def _base_url() -> str:
    raw = str(getattr(cfg, "REMAIL_API_BASE", "") or DEFAULT_API_BASE).strip()
    if not raw:
        raw = DEFAULT_API_BASE
    if not re.match(r"^https?://", raw, re.IGNORECASE):
        raw = f"https://{raw}"
    parsed = urlsplit(raw.rstrip("/"))
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise RemailError("Remail API 地址无效")
    path = parsed.path.rstrip("/")
    if path.lower() == "/docs":
        path = ""
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", "")).rstrip("/")


def _request_timeout() -> int:
    try:
        value = int(getattr(cfg, "REMAIL_REQUEST_TIMEOUT", REQUEST_TIMEOUT) or REQUEST_TIMEOUT)
    except (TypeError, ValueError):
        value = REQUEST_TIMEOUT
    return max(1, min(120, value))


def _proxies(proxies: Any = None) -> Any:
    return proxies if getattr(cfg, "USE_PROXY_FOR_EMAIL", False) else None


def _api_key() -> str:
    value = str(getattr(cfg, "REMAIL_API_KEY", "") or "").strip()
    if not value:
        raise RemailError("Remail API Key 未配置")
    return value


def _operation_label(method: str, path: str) -> str:
    normalized_method = str(method or "").strip().upper()
    normalized_path = str(path or "").strip()
    if normalized_path == "/v1/pickup":
        return "读取 Remail 邮箱"
    if normalized_path == "/v1/open/orders" and normalized_method == "POST":
        return "创建 Remail 邮箱订单"
    if normalized_path == "/v1/open/orders":
        return "查询 Remail 邮箱订单"
    if normalized_path.startswith("/v1/open/orders/"):
        return "查询 Remail 邮箱订单"
    return "请求 Remail 服务"


def _error_text(value: Any, depth: int = 0) -> str:
    if depth > 3 or value in (None, ""):
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        for item in value:
            text = _error_text(item, depth + 1)
            if text:
                return text
        return ""
    if isinstance(value, dict):
        for key in (
            "message",
            "detail",
            "reason",
            "description",
            "error_description",
            "error",
            "msg",
        ):
            text = _error_text(value.get(key), depth + 1)
            if text:
                return text
        for key in ("code", "errorCode", "error_code", "type"):
            text = _error_text(value.get(key), depth + 1)
            if text:
                return text
    return ""


def _error_code(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    for source in (payload, payload.get("error"), payload.get("data")):
        if not isinstance(source, dict):
            continue
        for key in ("code", "errorCode", "error_code", "type"):
            value = source.get(key)
            if value not in (None, "") and not isinstance(value, (dict, list)):
                return str(value).strip()
    return ""


def _response_request_id(payload: Any, response: Any) -> str:
    headers = getattr(response, "headers", None)
    if headers is not None:
        for key in ("x-request-id", "request-id", "x-trace-id"):
            try:
                value = headers.get(key)
            except Exception:
                value = None
            if value:
                return str(value).strip()
    if isinstance(payload, dict):
        for source in (payload, payload.get("error"), payload.get("data")):
            if not isinstance(source, dict):
                continue
            for key in ("requestId", "request_id", "traceId", "trace_id"):
                value = source.get(key)
                if value not in (None, ""):
                    return str(value).strip()
    return ""


def _classify_api_error(
    status_code: int,
    raw_message: str,
    code: str,
    *,
    method: str = "",
    path: str = "",
) -> tuple[str, str]:
    combined = " ".join((raw_message, code)).lower()
    normalized = re.sub(r"[\s_-]+", " ", combined).strip()
    is_pickup = str(path or "").strip() == "/v1/pickup"

    if (
        "insufficient balance" in normalized
        or "not enough balance" in normalized
        or "balance insufficient" in normalized
        or "余额不足" in combined
    ):
        return (
            "账户余额不足",
            "请登录 Remail 控制台充值，并确认当前 API Key 对应的账户和项目计费正常后重试。",
        )
    if is_pickup and (status_code == 401 or "invalid token" in normalized):
        return (
            "邮箱 service token 无效或已过期",
            "请重新创建 Remail 邮箱订单，或删除本地 Remail 上下文后重新取信。",
        )
    if is_pickup and (status_code == 403 or "forbidden" in normalized):
        return (
            "当前邮箱 service token 无权读取邮件",
            "请确认订单仍有效；必要时重新创建订单。",
        )
    if "invalid api key" in normalized or "invalid token" in normalized or status_code == 401:
        return (
            "API Key 无效或已过期",
            "请检查邮箱配置中的 rk- Key 是否完整、是否已被删除，并确认它属于正确的 Remail 账户。",
        )
    if "permission" in normalized or "forbidden" in normalized or status_code == 403:
        return (
            "当前 API Key 没有操作权限",
            "请确认 Key 权限、项目访问范围和账户状态。",
        )
    if (
        "out of stock" in normalized
        or "no stock" in normalized
        or "sold out" in normalized
        or "inventory" in normalized
        or "库存不足" in combined
    ):
        return (
            "项目库存不足",
            "请到 Remail 控制台确认项目 ID、邮箱后缀和库存模式，切换可用项目或库存后重试。",
        )
    if "rate limit" in normalized or "too many requests" in normalized or status_code == 429:
        return (
            "请求过于频繁",
            "Remail 已触发限流，请降低注册并发或稍后重试。",
        )
    if (
        "project not found" in normalized
        or "invalid project" in normalized
        or "unknown project" in normalized
    ):
        return (
            "项目 ID 不存在或不可用",
            "请到 Remail 控制台核对项目 ID 是否支持当前邮箱后缀和服务模式。",
        )
    if (
        "email suffix" in normalized
        or "domain not supported" in normalized
        or "unsupported domain" in normalized
    ):
        return (
            "邮箱后缀不受支持",
            "请选择该项目实际支持的邮箱后缀。",
        )
    if (
        "maintenance" in normalized
        or "service unavailable" in normalized
        or status_code in {500, 502, 503, 504}
    ):
        return (
            "Remail 服务暂时不可用",
            "这是上游服务异常，请稍后重试；持续出现时联系 Remail 服务方。",
        )
    if status_code == 400:
        return (
            "请求参数无效",
            "请检查项目 ID、邮箱后缀、服务模式和库存策略。",
        )
    if status_code == 404:
        return (
            "请求的资源不存在",
            "请检查 API 地址和项目配置。",
        )
    if status_code == 409:
        return (
            "订单状态冲突",
            "请稍后重试；如果已有进行中的订单，请先在 Remail 控制台处理。",
        )
    if status_code == 422:
        return (
            "业务请求被拒绝",
            "请根据接口原文检查账户余额、项目库存、项目 ID 和邮箱后缀。",
        )
    return (
        "请求失败",
        "请根据接口原文检查 Remail 配置和账户状态。",
    )


def _format_api_error(error: RemailAPIError) -> str:
    category, suggestion = _classify_api_error(
        error.status_code,
        error.raw_message,
        error.code,
        method=error.method,
        path=error.path,
    )
    parts = [
        f"{_operation_label(error.method, error.path)}失败"
        f"（HTTP {error.status_code}）：{category}"
    ]
    if suggestion:
        parts.append(suggestion)
    if error.raw_message:
        parts.append(f"接口原文: {error.raw_message}")
    if error.code:
        parts.append(f"业务码: {error.code}")
    if error.request_id:
        parts.append(f"requestId: {error.request_id}")
    return "；".join(parts)


def _request(
    method: str,
    path: str,
    *,
    params: Optional[dict[str, Any]] = None,
    json_body: Optional[dict[str, Any]] = None,
    proxies: Any = None,
    authenticated: bool = True,
    idempotency_key: str = "",
) -> Any:
    headers = {"Accept": "application/json"}
    if authenticated:
        headers["Authorization"] = f"Bearer {_api_key()}"
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    url = _base_url() + (path if path.startswith("/") else f"/{path}")
    try:
        response = requests.request(
            method.upper(),
            url,
            params=params,
            json=json_body,
            headers=headers,
            proxies=_proxies(proxies),
            timeout=_request_timeout(),
        )
    except Exception as exc:
        raise RemailError(f"Remail 请求失败: {type(exc).__name__}: {exc}") from exc

    status = int(getattr(response, "status_code", 0) or 0)
    try:
        payload = response.json()
    except Exception:
        payload = None
    if status >= 400:
        message = _error_text(payload)
        if not message:
            message = str(getattr(response, "text", "") or "").strip()[:500]
        raise RemailAPIError(
            status,
            message or "请求失败",
            code=_error_code(payload),
            request_id=_response_request_id(payload, response),
            method=method,
            path=path,
        )
    if payload is None:
        raise RemailError("Remail 返回了非 JSON 响应")
    return payload


def _data(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def _first_value(source: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = source.get(key)
        if value not in (None, ""):
            return value
    return None


def _build_account(payload: Any, project_id: int, suffix: str) -> Optional[RemailAccount]:
    data = _data(payload)
    email = str(
        _first_value(data, "deliveryEmail", "delivery_email", "email", "address") or ""
    ).strip()
    token = str(
        _first_value(data, "serviceToken", "service_token", "token") or ""
    ).strip()
    order_no = str(_first_value(data, "orderNo", "order_no", "id") or "").strip()
    if not email or not token:
        return None
    return RemailAccount(
        email=email,
        service_token=token,
        order_no=order_no,
        project_id=project_id,
        email_suffix=suffix,
    )


def _wait_for_account(
    initial: Any,
    project_id: int,
    suffix: str,
    proxies: Any = None,
) -> RemailAccount:
    account = _build_account(initial, project_id, suffix)
    if account:
        return account
    data = _data(initial)
    order_no = str(_first_value(data, "orderNo", "order_no", "id") or "").strip()
    try:
        wait_seconds = int(
            getattr(cfg, "REMAIL_ORDER_WAIT_SECONDS", ORDER_WAIT_SECONDS) or 0
        )
    except (TypeError, ValueError):
        wait_seconds = ORDER_WAIT_SECONDS
    deadline = time.monotonic() + max(0, wait_seconds)
    last_status = ""
    last_error = ""
    while order_no and time.monotonic() <= deadline:
        try:
            payload = _request("GET", f"/v1/open/orders/{order_no}", proxies=proxies)
        except RemailError as exc:
            last_error = str(exc)
            time.sleep(1)
            continue
        detail = _data(payload)
        last_status = str(_first_value(detail, "status", "state") or "").strip().lower()
        account = _build_account(payload, project_id, suffix)
        if account:
            return account
        if last_status in _FINAL_ORDER_STATUSES:
            break
        time.sleep(1)
    raise RemailError(
        "Remail 订单未返回邮箱或 service token"
        + (f" (order={order_no}, status={last_status})" if order_no else "")
        + (f"；最近一次查询错误: {last_error}" if last_error else "")
    )


def pick_account(proxies: Any = None) -> RemailAccount:
    _api_key()
    try:
        project_id = int(getattr(cfg, "REMAIL_PROJECT_ID", 0) or 0)
    except (TypeError, ValueError):
        project_id = 0
    if project_id <= 0:
        raise RemailError("Remail 项目 ID 未配置")
    suffix = str(getattr(cfg, "REMAIL_EMAIL_SUFFIX", "outlook.com") or "").strip()
    service_mode = str(getattr(cfg, "REMAIL_SERVICE_MODE", "purchase") or "purchase").strip().lower()
    if service_mode not in {"code", "purchase"}:
        service_mode = "purchase"
    supply = str(getattr(cfg, "REMAIL_SUPPLY_POLICY", "public_only") or "public_only").strip().lower()
    if supply not in {"private_first", "public_only"}:
        supply = "public_only"
    payload = _request(
        "POST",
        "/v1/open/orders",
        params={"serviceMode": service_mode, "supply": supply},
        json_body={"projectId": project_id, "emailSuffix": suffix},
        proxies=proxies,
        idempotency_key=f"openai-cpa-{uuid.uuid4().hex}",
    )
    account = _wait_for_account(payload, project_id, suffix, proxies)
    _save_context(account)
    return account


def get_account_context(email: str) -> Optional[RemailAccount]:
    return _load_context(email)


def restore_account_context(email: str, proxies: Any = None) -> Optional[RemailAccount]:
    target = _cache_key(email)
    if not target:
        return None
    try:
        payload = _request(
            "GET",
            "/v1/open/orders",
            params={"offset": 0, "limit": 100, "search": email},
            proxies=proxies,
        )
    except Exception:
        return None

    raw_items: list[Any] = []
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            raw_items = data["items"]
        elif isinstance(data, list):
            raw_items = data
        elif isinstance(payload.get("items"), list):
            raw_items = payload["items"]
    elif isinstance(payload, list):
        raw_items = payload

    candidates: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        item_email = str(
            _first_value(item, "deliveryEmail", "delivery_email", "email", "address") or ""
        ).strip().lower()
        if item_email and item_email == target:
            candidates.append(item)

    for order in candidates:
        account = _build_account(order, 0, "")
        if account is not None:
            _save_context(account)
            return account

    for order in candidates[:5]:
        order_no = str(_first_value(order, "orderNo", "order_no", "id") or "").strip()
        if not order_no:
            continue
        try:
            detail = _request("GET", f"/v1/open/orders/{order_no}", proxies=proxies)
        except Exception:
            continue
        account = _build_account(detail, 0, "")
        if account is not None and account.email.strip().lower() == target:
            _save_context(account)
            return account
    return None


def release_account(email: str, status: str = "released", note: str = "") -> bool:
    del status, note
    with _CONTEXT_LOCK:
        _CONTEXT_CACHE.pop(_cache_key(email), None)
    try:
        path = _context_path(email)
        if path.is_file():
            path.unlink()
    except Exception:
        return False
    return True


def _parse_timestamp(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number / 1000.0 if number > 10_000_000_000 else number
    text = str(value).strip()
    try:
        return float(text)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("items", "messages", "results", "data", "list"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = _items(value)
            if nested:
                return nested
    return [payload] if payload.get("subject") or payload.get("bodyPreview") else []


def _message_code(message: dict[str, Any]) -> str:
    explicit = str(
        _first_value(message, "verificationCode", "verification_code", "code", "otp") or ""
    ).strip()
    if re.fullmatch(r"\d{6}", explicit):
        return explicit
    content = "\n".join(
        str(message.get(key) or "")
        for key in (
            "subject",
            "bodyPreview",
            "body_preview",
            "body",
            "text",
            "content",
            "html",
            "html_content",
        )
    )
    match = re.search(r"(?<!\d)(\d{6})(?!\d)", content)
    return match.group(1) if match else ""


def fetch_latest_otp(
    email: str,
    after_ts: Optional[float] = None,
    max_wait: int = 90,
    poll_interval: int = 3,
    settle_seconds: int = 2,
    proxies: Any = None,
) -> str:
    account = get_account_context(email) or restore_account_context(email, proxies=proxies)
    if account is None:
        raise RemailError("Remail 找不到该邮箱的 service token")
    deadline = time.monotonic() + max(0, int(max_wait))
    interval = max(1, int(poll_interval))
    settle = max(0, int(settle_seconds))
    best_code = ""
    best_ts = float("-inf")
    settle_until: Optional[float] = None
    last_error = "收件箱为空或尚未出现验证码"
    while time.monotonic() <= deadline:
        try:
            payload = _request(
                "GET",
                "/v1/pickup",
                params={"email": account.email, "token": account.service_token},
                proxies=proxies,
                authenticated=False,
            )
            candidates: list[tuple[float, str]] = []
            for message in _items(payload):
                timestamp = _parse_timestamp(
                    _first_value(
                        message,
                        "receivedAt",
                        "received_at",
                        "timestamp",
                        "createdAt",
                        "created_at",
                    )
                )
                if after_ts is not None and (
                    timestamp is None or timestamp < float(after_ts) - 30
                ):
                    continue
                code = _message_code(message)
                if code:
                    candidates.append((timestamp or float("-inf"), code))
            candidates.sort(reverse=True)
            if candidates and (
                candidates[0][0] > best_ts
                or (candidates[0][0] == best_ts and candidates[0][1] != best_code)
            ):
                best_ts, best_code = candidates[0]
                settle_until = time.monotonic() + settle
            if best_code and settle_until is not None and time.monotonic() >= settle_until:
                return best_code
        except Exception as exc:
            last_error = str(exc)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(interval, remaining))
    if best_code:
        return best_code
    raise RemailError(f"等待 Remail 验证码超时: {email}; {last_error}")
