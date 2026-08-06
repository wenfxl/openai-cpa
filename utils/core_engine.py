"""
调度层
配置热加载、CPA 仓管逻辑、主循环、RegEngine 控制类。
邮箱逻辑 → mail_service.py
注册流程 → register.py
配置变量 → config.py
"""

import argparse
import asyncio
import builtins
import io
import json
import os
import random
import re
import threading
import time
import string
import yaml
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional, Tuple
from curl_cffi import requests, CurlMime
import queue
from datetime import datetime, timezone, timedelta
from utils.email_providers import mail_service
from utils import config as cfg
from utils import db_manager
from utils.config import reload_all_configs, ts, format_docker_url
from utils.email_providers.mail_service import mask_email
from utils.auth_pipeline.register import run
from utils.auth_pipeline.oauth import refresh_oauth_token as _refresh_oauth_token

from utils.proxy_manager import smart_switch_node
from utils.integrations.sub2api_client import Sub2APIClient
from utils.integrations.tg_notifier import send_tg_msg_sync
from utils.email_providers.postman_center import global_postman_fleet

_stats_lock = threading.Lock()
sub_fail_counts = {}
_heal_lock = threading.Lock()
_oauth_revive_semaphore = threading.Semaphore(10)
DEFAULT_CLIPROXY_UA = "codex_cli_rs/0.76.0 (Debian 13.0.0; x86_64) WindowsTerminal"
run_stats = {
    "success": 0,
    "failed": 0,
    "retries": 0,
    "start_time": 0,
    "target": 0,
    "pwd_blocked": 0,
    "phone_verify": 0
}
KNOWN_CLIPROXY_ERROR_LABELS = {
    "usage_limit_reached":  "周限额已耗尽",
    "account_deactivated":  "账号已停用",
    "insufficient_quota":   "额度不足",
    "invalid_api_key":      "凭证无效",
    "unsupported_region":   "地区不支持",
}

_orig_print  = builtins.print
_thread_local = threading.local()
_print_lock   = threading.Lock()


class FakeLogQueue:
    def put_nowait(self, item):
        try:
            from global_state import append_log
            if isinstance(item, str):
                append_log(item.strip())
            else:
                append_log(str(item).strip())
        except Exception:
            pass
    def put(self, item, block=True, timeout=None):
        self.put_nowait(item)

    def empty(self):
        return True

    def qsize(self):
        return 0
log_queue = FakeLogQueue()

def web_print(*args, **kwargs):
    if "file" in kwargs and kwargs["file"] is not None:
        with _print_lock:
            _orig_print(*args, **kwargs)
        return
    if not hasattr(_thread_local, "buffer"):
        _thread_local.buffer = ""
    tmp = io.StringIO()
    _orig_print(*args, file=tmp, **kwargs)
    _thread_local.buffer += tmp.getvalue()
    if _thread_local.buffer.endswith("\n"):
        with _print_lock:
            msg = _thread_local.buffer.lstrip("\n")
            if msg and msg.strip() != ".":
                try:
                    from global_state import append_log
                    append_log(msg.strip())
                except Exception:
                    pass
        _thread_local.buffer = ""


builtins.print = web_print

def _load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for raw in handle:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if not key or key in os.environ:
                    continue
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                    value = value[1:-1]
                os.environ[key] = value
    except Exception:
        pass


_load_dotenv()

def _normalize_cpa_auth_files_url(api_url: str) -> str:
    normalized = (api_url or "").strip().rstrip("/")
    lower = normalized.lower()
    if not normalized:
        return ""
    if lower.endswith("/auth-files"):
        return normalized
    if lower.endswith("/v0/management") or lower.endswith("/management"):
        return f"{normalized}/auth-files"
    if lower.endswith("/v0"):
        return f"{normalized}/management/auth-files"
    return f"{normalized}/v0/management/auth-files"


def set_cpa_auth_file_status(
    api_url: str, api_token: str, filename: str, disabled: bool = True
) -> bool:
    status_url = f"{_normalize_cpa_auth_files_url(api_url)}/status"
    try:
        res = requests.patch(
            status_url,
            headers={"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"},
            json={"name": filename, "disabled": disabled},
            timeout=15, impersonate="chrome",
        )
        if res.status_code in (200, 204):
            return True
        print(f"[{ts()}] [ERROR] 切换凭证状态失败 (HTTP {res.status_code}): {res.text}")
        return False
    except Exception as e:
        print(f"[{ts()}] [ERROR] 切换凭证状态异常: {e}")
        return False



def _is_xai_like_token(token_or_item: Any) -> bool:
    """识别 Grok/xAI 账号（CPA 文件或 Sub2API 账号项）。"""
    if not isinstance(token_or_item, dict):
        return False
    for key in ("type", "provider", "platform", "status"):
        val = str(token_or_item.get(key) or "").lower()
        if "xai" in val or "grok" in val:
            return True
    creds = token_or_item.get("credentials")
    if isinstance(creds, dict):
        for key in ("type", "provider", "platform"):
            val = str(creds.get(key) or "").lower()
            if "xai" in val or "grok" in val:
                return True
    return False


def _is_codex_free_cpa_file(item: dict) -> bool:
    if not isinstance(item, dict):
        return False
    typ = str(item.get("type", "")).lower()
    provider = str(item.get("provider", "")).lower()
    if "codex" not in typ and "codex" not in provider:
        return False
    id_token = item.get("id_token") if isinstance(item.get("id_token"), dict) else {}
    plan = str(id_token.get("plan_type") or id_token.get("planType") or "").lower()
    return plan == "free"


def _filter_cpa_inventory_files(all_files: list) -> list:
    """按当前注册平台筛选 CPA 云端库存。"""
    files = all_files if isinstance(all_files, list) else []
    provider = str(getattr(cfg, "REG_PROVIDER", "openai") or "openai").strip().lower()
    if provider == "grok":
        return [f for f in files if _is_xai_like_token(f)]
    return [f for f in files if _is_codex_free_cpa_file(f)]


def _is_sub2api_openai_free_item(item: dict) -> bool:
    if not isinstance(item, dict):
        return False
    if str(item.get("platform") or "").lower() != "openai":
        return False
    plan = str((item.get("credentials") or {}).get("plan_type", "free")).lower()
    if plan != "free":
        return False
    extra = item.get("extra") or {}
    return int(extra.get("codex_5h_window_minutes", 0) or 0) == 0


def upload_to_cpa_integrated(
    token_data: dict, api_url: str, api_token: str, custom_filename: str = None
) -> Tuple[bool, str]:
    upload_url = _normalize_cpa_auth_files_url(api_url)
    filename   = custom_filename or f"{token_data.get('email', 'unknown')}.json"
    file_content = json.dumps(token_data, ensure_ascii=False, indent=2).encode("utf-8")
    try:
        mime = CurlMime()
        mime.addpart(name="file", data=file_content, filename=filename,
                     content_type="application/json")
        resp = requests.post(
            upload_url, multipart=mime,
            headers={"Authorization": f"Bearer {api_token}"},
            timeout=30, impersonate="chrome",
        )
        if resp.status_code in (200, 201):
            return True, "上传成功"
        if resp.status_code in (404, 405, 415):
            raw_url = f"{upload_url}?name={urllib.parse.quote(filename)}"
            fb = requests.post(
                raw_url, data=file_content,
                headers={"Authorization": f"Bearer {api_token}",
                         "Content-Type": "application/json"},
                timeout=30, impersonate="chrome",
            )
            if fb.status_code in (200, 201):
                return True, "上传成功"
            resp = fb
        return False, f"HTTP {resp.status_code}"
    except Exception as e:
        return False, str(e)


def _grok2api_import_expires_at(token_data: dict) -> str:
    """Return Grok2API import expires_at.

    Grok2API schedules OAuth refresh from this value. Build tokens imported with a
    future access-token expiry may sit unrefreshed and miss Build model discovery
    (for example grok-imagine-video-1.5). If a refresh_token exists, deliberately
    mark the imported access token as already expired so Grok2API refreshes it
    immediately and discovers capabilities from the fresh token/session.
    """
    if token_data.get("refresh_token"):
        return datetime.fromtimestamp(int(time.time()) - 60, timezone.utc).isoformat().replace("+00:00", "Z")

    exp = token_data.get("expires_at")
    expires_str = ""
    if exp is not None:
        try:
            if isinstance(exp, (int, float)):
                expires_str = datetime.fromtimestamp(int(exp), timezone.utc).isoformat().replace("+00:00", "Z")
            elif str(exp).isdigit():
                expires_str = datetime.fromtimestamp(int(str(exp)), timezone.utc).isoformat().replace("+00:00", "Z")
            else:
                expires_str = str(exp)
        except Exception:
            expires_str = str(exp) if exp else ""
    return expires_str


def _grok2api_import_payload(token_data: dict) -> dict:
    expires_str = _grok2api_import_expires_at(token_data)
    return {
        "provider": "grok_build",
        "name": token_data.get("email", "Grok Build account"),
        "client_id": token_data.get("client_id", ""),
        "access_token": token_data.get("access_token", ""),
        "refresh_token": token_data.get("refresh_token", ""),
        "id_token": token_data.get("id_token", ""),
        "token_type": token_data.get("token_type", "Bearer"),
        "email": token_data.get("email", ""),
        "user_id": token_data.get("user_id") or token_data.get("principal_id", ""),
        "team_id": token_data.get("team_id", ""),
        "expires_at": expires_str,
    }



def _grok2api_import_web_sso(sso: str, token_value: str) -> Tuple[bool, str]:
    """Import one SSO cookie through Grok2API's dedicated Grok Web endpoint."""
    sso = str(sso or "").strip()
    if not sso:
        return False, "缺少 sso"
    grok_url = (getattr(cfg, "GROK2API_URL", "") or "http://host.docker.internal:8000").rstrip("/")
    mime = CurlMime()
    mime.addpart(
        name="files",
        data=(sso + "\n").encode("utf-8"),
        filename="grok-web-sso-token.txt",
        content_type="text/plain",
    )
    try:
        resp = requests.post(
            f"{grok_url}/api/admin/v1/accounts/web/import",
            multipart=mime,
            headers={"Authorization": f"Bearer {token_value}"},
            timeout=180,
            impersonate="chrome",
        )
        if resp.status_code in (200, 201):
            return True, "Grok Web SSO 导入成功"
        return False, f"Grok Web SSO 导入失败 HTTP {resp.status_code}"
    except Exception as exc:
        return False, f"Grok Web SSO 导入异常: {exc}"


def import_to_grok2api(token_data: dict) -> Tuple[bool, str]:
    """Import one freshly registered xAI/Grok account into Grok2API.

    Keep logs secret-safe: callers should only print email masks/status codes, never token payloads.
    """
    grok_url = (getattr(cfg, "GROK2API_URL", "") or "http://host.docker.internal:8000").rstrip("/")
    grok_pass = getattr(cfg, "GROK2API_ADMIN_PASSWORD", "") or ""
    if not grok_pass:
        return False, "Grok2API admin_password 未配置"
    if not _is_xai_like_token(token_data) and str(getattr(cfg, "REG_PROVIDER", "openai")).lower() != "grok":
        return False, "非 Grok/xAI 账号"
    if not (token_data.get("access_token") or token_data.get("refresh_token")):
        return False, "缺少 access_token/refresh_token"
    try:
        login_resp = requests.post(
            f"{grok_url}/api/admin/v1/auth/login",
            json={"username": "admin", "password": grok_pass},
            timeout=20,
            impersonate="chrome",
        )
        if login_resp.status_code != 200:
            return False, f"Grok2API 登录失败 HTTP {login_resp.status_code}"
        grok_token = login_resp.json().get("data", {}).get("tokens", {}).get("accessToken", "")
        if not grok_token:
            return False, "Grok2API 登录未返回 accessToken"

        payload = _grok2api_import_payload(token_data)
        file_content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        mime = CurlMime()
        mime.addpart(name="file", data=file_content, filename="auth.json", content_type="application/json")
        import_resp = requests.post(
            f"{grok_url}/api/admin/v1/accounts/import",
            multipart=mime,
            headers={"Authorization": f"Bearer {grok_token}"},
            timeout=180,
            impersonate="chrome",
        )
        if import_resp.status_code in (200, 201):
            return True, "导入成功"
        return False, f"导入失败 HTTP {import_resp.status_code}"
    except Exception as e:
        return False, str(e)


def grok2api_admin_login() -> Tuple[bool, str, str]:
    """登录 Grok2API 管理端，供独立仓管巡检/补货使用。"""
    grok_url = (getattr(cfg, "GROK2API_URL", "") or "").rstrip("/")
    grok_pass = getattr(cfg, "GROK2API_ADMIN_PASSWORD", "") or ""
    if not grok_url:
        return False, "", "Grok2API api_url 未配置"
    if not grok_pass:
        return False, "", "Grok2API admin_password 未配置"
    try:
        resp = requests.post(
            f"{grok_url}/api/admin/v1/auth/login",
            json={"username": "admin", "password": grok_pass},
            timeout=30,
            impersonate="chrome",
        )
        if resp.status_code != 200:
            return False, "", f"Grok2API 登录失败 HTTP {resp.status_code}"
        token_value = resp.json().get("data", {}).get("tokens", {}).get("accessToken", "")
        if not token_value:
            return False, "", "Grok2API 登录未返回 accessToken"
        return True, token_value, "OK"
    except Exception as exc:
        return False, "", f"Grok2API 登录异常: {exc}"


def grok2api_admin_request(method: str, path: str, token_value: str, **kwargs):
    grok_url = (getattr(cfg, "GROK2API_URL", "") or "").rstrip("/")
    headers = kwargs.pop("headers", {}) or {}
    headers["Authorization"] = f"Bearer {token_value}"
    return requests.request(
        method,
        f"{grok_url}/api/admin/v1{path}",
        headers=headers,
        timeout=kwargs.pop("timeout", 60),
        impersonate="chrome",
        **kwargs,
    )


def grok2api_list_accounts(token_value: str, provider: str = None, page_size: int = 500) -> Tuple[bool, list, str]:
    items = []
    page = 1
    total = None
    try:
        while True:
            params = {"page": page, "pageSize": page_size}
            if provider:
                params["provider"] = provider
            resp = grok2api_admin_request("GET", "/accounts", token_value, params=params, timeout=30)
            if resp.status_code != 200:
                return False, items, f"Grok2API 账号列表 HTTP {resp.status_code}"
            data = resp.json().get("data", {})
            batch = data.get("items", []) or []
            items.extend(batch)
            total = data.get("total", total)
            if not batch or len(items) >= int(total or 0) or len(batch) < page_size:
                break
            page += 1
            if page > 50:
                break
        return True, items, "OK"
    except Exception as exc:
        return False, items, f"Grok2API 拉取账号异常: {exc}"


def _grok2api_provider(item: dict) -> str:
    return str((item or {}).get("provider") or "").strip().lower()


def _is_grok2api_inventory_item(item: dict) -> bool:
    provider = _grok2api_provider(item)
    if not provider:
        return True
    return provider.startswith("grok") or "xai" in provider


def _grok2api_account_label(item: dict) -> str:
    return str((item or {}).get("email") or (item or {}).get("name") or (item or {}).get("id") or "unknown")


def _grok2api_quota_remaining_percent(item: dict) -> Optional[float]:
    quota = (item or {}).get("quota") or {}
    if not isinstance(quota, dict):
        return None
    usage = quota.get("usagePercent")
    if isinstance(usage, (int, float)):
        return max(0.0, min(100.0, 100.0 - float(usage)))
    remaining = quota.get("remaining")
    limit = quota.get("limit")
    try:
        if remaining is not None and limit:
            return max(0.0, min(100.0, float(remaining) * 100.0 / float(limit)))
    except Exception:
        return None
    return None


def _grok2api_quota_exhausted(item: dict) -> bool:
    quota = (item or {}).get("quota") or {}
    if isinstance(quota, dict):
        status = str(quota.get("status") or "").lower()
        if status in {"exhausted", "limit_reached", "limited", "disabled"}:
            return True
        remaining = quota.get("remaining")
        try:
            if remaining is not None and float(remaining) <= 0:
                return True
        except Exception:
            pass
    pct = _grok2api_quota_remaining_percent(item)
    threshold = int(getattr(cfg, "GROK2API_MIN_REMAINING_WEEKLY_PERCENT", 0) or 0)
    return threshold > 0 and pct is not None and pct < threshold


def _set_grok2api_account_enabled(token_value: str, account_id: str, enabled: bool) -> Tuple[bool, str]:
    try:
        resp = grok2api_admin_request(
            "PATCH", f"/accounts/{account_id}", token_value,
            json={"enabled": enabled}, timeout=30,
        )
        return resp.status_code == 200, f"HTTP {resp.status_code}"
    except Exception as exc:
        return False, str(exc)


def _delete_grok2api_account(token_value: str, item: dict) -> Tuple[bool, str]:
    account_id = str((item or {}).get("id") or "")
    if not account_id:
        return False, "缺少账号 ID"
    provider = _grok2api_provider(item)
    body = {"provider": provider} if provider else {}
    try:
        resp = grok2api_admin_request("DELETE", f"/accounts/{account_id}", token_value, json=body, timeout=40)
        return resp.status_code in (200, 204), f"HTTP {resp.status_code}"
    except Exception as exc:
        return False, str(exc)


def process_grok2api_worker(i: int, total: int, item: dict, token_value: str, args: Any) -> bool:
    """Grok2API 仓管测活 Worker：Grok Web 刷额度，Build/Console 刷 token。"""
    if hasattr(args, 'check_stop') and args.check_stop():
        return False
    if not _is_grok2api_inventory_item(item):
        return True
    account_id = str((item or {}).get("id") or "")
    name = _grok2api_account_label(item)
    provider = _grok2api_provider(item)
    if not account_id:
        print(f"[{ts()}] [ERROR] Grok2API测活: {mask_email(name)} 缺少账号 ID")
        return False
    if item.get("enabled") is False:
        print(f"[{ts()}] [WARNING] Grok2API测活: {mask_email(name)} 当前已禁用，不计入有效库存")
        return False
    if _grok2api_quota_exhausted(item):
        if getattr(cfg, "GROK2API_REMOVE_ON_LIMIT_REACHED", True):
            ok, msg = _delete_grok2api_account(token_value, item)
            print(f"[{ts()}] [WARNING] Grok2API测活: {mask_email(name)} 额度不足，执行删除: {msg}")
        else:
            ok, msg = _set_grok2api_account_enabled(token_value, account_id, False)
            print(f"[{ts()}] [WARNING] Grok2API测活: {mask_email(name)} 额度不足，执行禁用: {msg}")
        return False

    check_path = f"/accounts/{account_id}/refresh-quota" if provider == "grok_web" else f"/accounts/{account_id}/refresh-token"
    try:
        resp = grok2api_admin_request("POST", check_path, token_value, timeout=180)
        if resp.status_code == 200:
            fresh_item = resp.json().get("data", {}) or item
            if _grok2api_quota_exhausted(fresh_item):
                if getattr(cfg, "GROK2API_REMOVE_ON_LIMIT_REACHED", True):
                    ok, msg = _delete_grok2api_account(token_value, fresh_item or item)
                    print(f"[{ts()}] [WARNING] Grok2API测活: {mask_email(name)} 刷新后额度不足，执行删除: {msg}")
                else:
                    ok, msg = _set_grok2api_account_enabled(token_value, account_id, False)
                    print(f"[{ts()}] [WARNING] Grok2API测活: {mask_email(name)} 刷新后额度不足，执行禁用: {msg}")
                return False
            try:
                db_manager.update_account_status_by_fuzzy_name(name, 1)
            except Exception:
                pass
            print(f"[{ts()}] [SUCCESS] Grok2API测活: {mask_email(name)} 状态健康")
            return True

        print(f"[{ts()}] [ERROR] Grok2API测活: {mask_email(name)} 失败 HTTP {resp.status_code}")
        try:
            db_manager.update_account_status_by_fuzzy_name(name, 0)
        except Exception:
            pass
        if getattr(cfg, "GROK2API_REMOVE_DEAD_ACCOUNTS", True):
            ok, msg = _delete_grok2api_account(token_value, item)
            print(f"[{ts()}] [ERROR] Grok2API测活: {mask_email(name)} 死号删除: {msg}")
        else:
            ok, msg = _set_grok2api_account_enabled(token_value, account_id, False)
            print(f"[{ts()}] [ERROR] Grok2API测活: {mask_email(name)} 死号禁用: {msg}")
        return False
    except Exception as exc:
        print(f"[{ts()}] [ERROR] Grok2API测活: {mask_email(name)} 异常: {exc}")
        return False


def _decode_possible_json_payload(payload: Any) -> Any:
    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            return payload
        try:
            return json.loads(text)
        except Exception:
            return payload
    return payload


def _extract_remaining_percent(window_info: Any) -> Optional[float]:
    if not isinstance(window_info, dict):
        return None
    remaining_percent = window_info.get("remaining_percent")
    if isinstance(remaining_percent, (int, float)):
        return max(0.0, min(100.0, float(remaining_percent)))
    used_percent = window_info.get("used_percent")
    if isinstance(used_percent, (int, float)):
        return max(0.0, min(100.0, 100.0 - float(used_percent)))
    return None


def _should_reenable_cpa_account(raw_usage: Any, threshold: int) -> Tuple[bool, str]:
    """
    Fail-closed 恢复判定：只有能明确确认额度高于阈值时才允许重新启用。
    返回 (可否启用, 原因描述)。
    """
    if not isinstance(raw_usage, dict):
        return False, "无法读取用量数据"
    payload = raw_usage
    body = raw_usage.get("body")
    if isinstance(body, str):
        try:
            payload = json.loads(body)
        except Exception:
            return False, "无法解析用量响应体"
    if not isinstance(payload, dict):
        return False, "用量数据格式异常"
    rate_limit = payload.get("rate_limit")
    if not isinstance(rate_limit, dict):
        return False, "缺少 rate_limit 数据"
    if rate_limit.get("allowed") is False or rate_limit.get("limit_reached") is True:
        return False, (
            f"限额标记未恢复（allowed={rate_limit.get('allowed')}, "
            f"limit_reached={rate_limit.get('limit_reached')}）"
        )
    pct = _extract_remaining_percent(rate_limit.get("primary_window"))
    if pct is None:
        return False, "无法确认剩余额度百分比（primary_window 缺失）"
    effective = max(threshold, 1)
    if pct < effective:
        pct_s = _format_percent(pct)
        detail = f"，低于阈值 {threshold}%" if threshold > 0 else ""
        return False, f"周限额剩余 {pct_s}%{detail}"
    return True, f"周限额剩余 {_format_percent(pct)}%"


def _format_percent(value: float) -> str:
    n = round(float(value), 2)
    return str(int(n)) if n.is_integer() else f"{n:.2f}".rstrip("0").rstrip(".")


def _format_known_cliproxy_error(error_type: str) -> str:
    label = KNOWN_CLIPROXY_ERROR_LABELS.get(error_type)
    return f"{label} ({error_type})" if label else f"错误类型: {error_type}"


def _extract_rate_limit_reason(
    rate_info: Any, key: str, min_remaining_weekly_percent: int = 0
) -> Optional[str]:
    if not isinstance(rate_info, dict):
        return None
    if rate_info.get("allowed") is False or rate_info.get("limit_reached") is True:
        label = {"rate_limit": "周限额已耗尽", "code_review_rate_limit": "代码审查周限额已耗尽"}.get(
            key, f"{key} 已耗尽"
        )
        return f"{label}（allowed={rate_info.get('allowed')}, limit_reached={rate_info.get('limit_reached')}）"
    if key == "rate_limit" and min_remaining_weekly_percent > 0:
        pct = _extract_remaining_percent(rate_info.get("primary_window"))
        if pct is not None and pct < min_remaining_weekly_percent:
            return f"周限额剩余 {_format_percent(pct)}%，低于阈值 {min_remaining_weekly_percent}%"
    return None


def _extract_cliproxy_failure_reason(
    payload: Any, min_remaining_weekly_percent: int = 0
) -> Optional[str]:
    data = _decode_possible_json_payload(payload)
    if isinstance(data, str):
        for kw in KNOWN_CLIPROXY_ERROR_LABELS:
            if kw in data:
                return _format_known_cliproxy_error(kw)
        return None
    if not isinstance(data, dict):
        return None
    error = data.get("error")
    if isinstance(error, dict):
        et = error.get("type")
        if et:
            return _format_known_cliproxy_error(et)
        msg = error.get("message")
        if msg:
            return str(msg)
    for key in ("rate_limit", "code_review_rate_limit"):
        pct = min_remaining_weekly_percent if key == "rate_limit" else 0
        reason = _extract_rate_limit_reason(data.get(key), key, pct)
        if reason:
            return reason
    arl = data.get("additional_rate_limits")
    if isinstance(arl, list):
        for i, ri in enumerate(arl):
            r = _extract_rate_limit_reason(ri, f"additional_rate_limits[{i}]", 0)
            if r:
                return r
    elif isinstance(arl, dict):
        for k, ri in arl.items():
            r = _extract_rate_limit_reason(ri, f"additional_rate_limits.{k}", 0)
            if r:
                return r
    for k in ("data", "body", "response", "text", "content", "status_message"):
        r = _extract_cliproxy_failure_reason(data.get(k), min_remaining_weekly_percent)
        if r:
            return r
    data_str = json.dumps(data, ensure_ascii=False)
    for kw in KNOWN_CLIPROXY_ERROR_LABELS:
        if kw in data_str:
            return _format_known_cliproxy_error(kw)
    return None


def refresh_oauth_token(refresh_token: str, proxies: Any = None) -> Tuple[bool, dict]:
    """刷新获取新的 access_token 等凭证"""
    return _refresh_oauth_token(refresh_token, proxies=proxies)



def test_cpa_xai_auth_file(item: dict) -> Tuple[bool, str]:
    """CPA 中的 Grok/xAI 凭证轻量测活（不走 ChatGPT）。"""
    if not isinstance(item, dict):
        return False, "无效凭证"
    access_token = str(item.get("access_token") or "").strip()
    if not access_token:
        return False, "缺少 access_token"
    base_url = str(item.get("base_url") or "https://cli-chat-proxy.grok.com/v1").strip().rstrip("/")
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    extra_headers = item.get("headers")
    if isinstance(extra_headers, dict):
        for k, v in extra_headers.items():
            if v is not None:
                headers[str(k)] = str(v)
    try:
        resp = requests.get(
            f"{base_url}/models",
            headers=headers,
            timeout=30,
            impersonate="chrome",
        )
        code = int(getattr(resp, "status_code", 0) or 0)
        if code == 200:
            return True, "正常"
        if code in (401, 403):
            return False, f"凭证失效 HTTP {code}"
        return False, f"HTTP {code}"
    except Exception as exc:
        return False, f"测活异常: {exc}"


def test_cliproxy_auth_file(item: dict, api_url: str, api_token: str) -> Tuple[bool, str]:
    """通过 CPA/CLIProxy 的 api-call 测活。OpenAI 走 ChatGPT ；Grok/xAI 走其 models。"""
    auth_index = item.get("auth_index")
    base_url   = api_url.strip().rstrip("/")
    call_url   = (
        base_url.replace("/auth-files", "/api-call")
        if "/auth-files" in base_url
        else f"{base_url}/v0/management/api-call"
    )

    is_xai = _is_xai_like_token(item)
    if is_xai:
        grok_base = str(item.get("base_url") or "https://cli-chat-proxy.grok.com/v1").strip().rstrip("/")
        target_url = f"{grok_base}/models"
        header = {
            "Authorization": "Bearer $TOKEN$",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        extra_headers = item.get("headers")
        if isinstance(extra_headers, dict):
            for k, v in extra_headers.items():
                key = str(k)
                if key.lower() == "authorization":
                    continue
                if v is not None:
                    header[key] = str(v)
    else:
        target_url = "https://chatgpt.com/backend-api/wham/usage"
        header = {
            "Authorization": "Bearer $TOKEN$",
            "Content-Type": "application/json",
            "User-Agent": DEFAULT_CLIPROXY_UA,
            "Chatgpt-Account-Id": str(item.get("account_id") or ""),
        }

    payload = {
        "authIndex": auth_index,
        "method": "GET",
        "url": target_url,
        "header": header,
    }
    try:
        resp = requests.post(
            call_url,
            headers={"Authorization": f"Bearer {api_token}"},
            json=payload, timeout=60, impersonate="chrome",
        )
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}"
        data = resp.json()
        item["_raw_usage"] = data
        status_code = data.get("status_code", 0)
        if is_xai:
            # Grok 无 ChatGPT 周限额结构，只按上游 HTTP 状态判断
            if int(status_code or 0) >= 400:
                return False, f"HTTP {status_code}"
            return True, "正常"

        reason = _extract_cliproxy_failure_reason(data, cfg.MIN_REMAINING_WEEKLY_PERCENT)
        if status_code >= 400 or reason:
            return False, reason or f"HTTP {status_code}"
        return True, "正常"
    except Exception:
        return False, "测活超时"

def test_sub2api_account_direct(item: dict, proxy: str) -> Tuple[bool, str]:
    """直连 OpenAI 接口进行 Sub2API 账号测活，并实时提取真实额度"""
    credentials = item.get("credentials", {})
    platform = item.get("platform", "")
    access_token = credentials.get("access_token")
    account_id = credentials.get("chatgpt_account_id", "")
    plan_type = credentials.get("plan_type", "")
    if platform != "openai" or plan_type != "free":
        return True, "非 OpenAI 免费号，跳过直连测活"

    if not access_token:
        return False, "缺少 access_token"

    headers = {
        "Authorization": f"Bearer {access_token}",
        "User-Agent": DEFAULT_CLIPROXY_UA,
        "Accept": "application/json"
    }
    if account_id:
        headers["Chatgpt-Account-Id"] = account_id

    try:
        proxies = {"http": proxy, "https": proxy} if proxy else None

        resp = requests.get(
            "https://chatgpt.com/backend-api/wham/usage",
            headers=headers,
            proxies=proxies,
            timeout=30,
            impersonate="chrome"
        )

        if resp.status_code != 200:
            if resp.status_code == 401: return False, "凭证无效 (HTTP 401)"
            if resp.status_code == 403: return False, "请求被拒绝 (HTTP 403)"
            return False, f"HTTP {resp.status_code}"

        data = resp.json()

        reason = _extract_cliproxy_failure_reason(data,0)
        if reason:
            return False, reason

        pct_str = "未知"
        rl_data = data.get("rate_limit", {})
        if isinstance(rl_data, dict):
            pct = _extract_remaining_percent(rl_data.get("primary_window"))
            if pct is not None:
                pct_str = f"{pct:.1f}%"

        return True, f"实时剩余: {pct_str}"
    except Exception as e:
        return False, f"测活异常: {e}"

def process_account_worker(i: int, total: int, item: dict, args: Any) -> bool:
    if hasattr(args, 'check_stop') and args.check_stop(): return False
    name        = item.get("name")
    email = name.replace(".json", "")
    is_disabled = item.get("disabled", False)
    is_xai = _is_xai_like_token(item)
    is_ok, msg  = test_cliproxy_auth_file(item, cfg.CPA_API_URL, cfg.CPA_API_TOKEN)

    if is_ok:
        try:
            db_manager.update_account_status([email], 1)
        except Exception:
            pass
        if is_disabled:
            if is_xai:
                # Grok/xAI 无 ChatGPT 周限额字段，测活通过即可恢复启用
                can_reenable, reason = True, "Grok/xAI 测活通过"
            else:
                can_reenable, reason = _should_reenable_cpa_account(
                    item.get("_raw_usage"), cfg.MIN_REMAINING_WEEKLY_PERCENT
                )
            if not can_reenable:
                print(f"[{ts()}] [INFO] 测活: {mask_email(name)} 额度尚未恢复（{reason}），继续保持禁用状态。")
                return False
            print(f"[{ts()}] [INFO] 测活: {mask_email(name)} 额度已恢复且有效，准备启用...")
            ok = set_cpa_auth_file_status(cfg.CPA_API_URL, cfg.CPA_API_TOKEN, name, disabled=False)
            print(
                f"[{ts()}] [{'SUCCESS' if ok else 'ERROR'}] 凭证 {mask_email(name)} "
                f"{'已成功启用！' if ok else '启用失败。'}"
            )
            return ok
        print(f"[{ts()}] [INFO] 测活: {mask_email(name)} 状态健康")
        return True

    print(f"[{ts()}] [WARNING] 测活: 凭证 {mask_email(name)} 失效，原因: {msg}")

    if is_xai:
        # Grok 不走 OpenAI refresh / OAuth 提权
        try:
            db_manager.update_account_status([email], 0)
        except Exception:
            pass
        _handle_dead_account(name, is_disabled)
        return False

    if "周限额" in msg or "usage_limit_reached" in msg:
        if cfg.REMOVE_ON_LIMIT_REACHED:
            try:
                db_manager.update_account_status([email], 0)
            except Exception:
                pass
            print(f"[{ts()}] [INFO] 触发限额剔除规则，执行物理剔除...")
            requests.delete(
                _normalize_cpa_auth_files_url(cfg.CPA_API_URL),
                headers={"Authorization": f"Bearer {cfg.CPA_API_TOKEN}"},
                params={"name": name},
            )
            try:
                db_manager.remove_account_push_platform(email, "CPA", exact_match=True)
                print(f"[{ts()}] [系统] 已同步清除 {mask_email(name)} 本地的 CPA 平台推送状态")
            except Exception:
                pass
        elif not is_disabled:
            print(f"[{ts()}] [INFO] 测活: 凭证额度耗尽，正在禁用...")
            ok = set_cpa_auth_file_status(cfg.CPA_API_URL, cfg.CPA_API_TOKEN, name, disabled=True)
            print(
                f"[{ts()}] [{'SUCCESS' if ok else 'ERROR'}] "
                f"测活: 凭证 {mask_email(name)} {'已成功禁用，等待额度重置。' if ok else '禁用失败！'}"
            )
        else:
            print(f"[{ts()}] [INFO] 测活: 账号额度尚未恢复，继续保持禁用状态。")
        return False

    if not cfg.ENABLE_TOKEN_REVIVE:
        try:
            db_manager.update_account_status([email], 0)
        except Exception:
            pass
        print(f"[{ts()}] [INFO] 检测到 Token 已失效，但【复活】已关闭，仅记录状态。")
        _handle_dead_account(name, is_disabled)
        return False

    print(f"[{ts()}] [INFO] 测活: 凭证 {mask_email(name)} 准备尝试刷新 Token 复活...")
    refresh_success = False

    if item.get("runtime_only") or item.get("source") == "memory":
        print(f"[{ts()}] [WARNING] {mask_email(name)} 属于纯内存凭据，跳过抢救。")
        full_item_data: dict = {}
    else:
        try:
            dl_url = f"{_normalize_cpa_auth_files_url(cfg.CPA_API_URL)}/download"
            content_resp = requests.get(
                dl_url, params={"name": name},
                headers={"Authorization": f"Bearer {cfg.CPA_API_TOKEN}"},
                timeout=20,
            )
            full_item_data = content_resp.json() if content_resp.status_code == 200 else {}
            if content_resp.status_code != 200:
                print(f"[{ts()}] [ERROR] 获取 {mask_email(name)} 完整内容失败 "
                      f"(HTTP {content_resp.status_code})")
        except Exception as e:
            print(f"[{ts()}] [ERROR] 获取 {mask_email(name)} 完整内容异常: {e}")
            full_item_data = {}

    refresh_token_val = full_item_data.get("refresh_token")
    if refresh_token_val:
        proxies = {"http": args.proxy, "https": args.proxy} if args.proxy else None
        ok, new_tokens = refresh_oauth_token(refresh_token_val, proxies=proxies)
        if ok:
            print(f"[{ts()}] [INFO] {mask_email(name)} Token 刷新成功，正在同步至CPA...")
            full_item_data.update(new_tokens)
            if "email" not in full_item_data:
                full_item_data["email"] = name.replace(".json", "")
            up_ok, up_msg = upload_to_cpa_integrated(
                full_item_data, cfg.CPA_API_URL, cfg.CPA_API_TOKEN, custom_filename=name
            )
            if up_ok:
                time.sleep(3)
                is_ok2, msg2 = test_cliproxy_auth_file(item, cfg.CPA_API_URL, cfg.CPA_API_TOKEN)
                if is_ok2:
                    refresh_success = True
                    print(f"[{ts()}] [SUCCESS] 测活: {mask_email(name)} 刷新后复活成功！")
                    try:
                        db_manager.update_account_status([email], 1)
                    except Exception:
                        pass
                else:
                    print(f"[{ts()}] [WARNING] {mask_email(name)} 刷新后二次测活依然失败({msg2})")
            else:
                print(f"[{ts()}] [ERROR] 刷新后覆盖CPA失败: {up_msg}")
        else:
            print(f"[{ts()}] [WARNING] {mask_email(name)} Token 复活请求被拒绝: "
                  f"{new_tokens.get('error','未知错误')}")
    else:
        print(f"[{ts()}] [WARNING] {mask_email(name)} 未找到有效数据，无法抢救")

    if not refresh_success:
        if getattr(cfg, 'CPA_AUTO_RE_OAUTH', False):
            print(f"[{ts()}] [INFO] 测活: {mask_email(name)} 尝试终极抢救 -> 自动重走 OAuth 提权流程")
            jitter = random.uniform(1.0, 3.0)
            time.sleep(jitter)
            with _oauth_revive_semaphore:
                print(f"[{ts()}] [INFO] 测活: {mask_email(name)} 获取到抢救队列执行权，开始提权流程")
                full_info = db_manager.get_account_full_info(email)
                if full_info:
                    password = full_info.get("password")
                    if not password:
                        print(f"[{ts()}] [INFO] 测活: {mask_email(name)} 无密码记录，将尝试 [无密码 OTP] 通道提取")
                        password = "Takeover_NoPassword"

                    raw_token = full_info.get("token_data", {})
                    acc_token = raw_token.get("access_token", "") if isinstance(raw_token, dict) else ""
                    device_id = raw_token.get("device_id", "") if isinstance(raw_token, dict) else ""
                    user_agent = raw_token.get("user_agent", "") if isinstance(raw_token, dict) else ""

                    res = run_oauth_only_and_sync(email, password, args.proxy, args, access_token=acc_token,
                                                  device_id=device_id, user_agent=user_agent)
                    time.sleep(random.uniform(1.0, 3.0))
                    if res == "success":
                        return True
                else:
                    print(f"[{ts()}] [WARNING] 测活: {mask_email(name)} 本地库彻底查无此号，放弃抢救")
        try:
            db_manager.update_account_status([email], 0)
        except Exception:
            pass
        _handle_dead_account(name, is_disabled)
    return refresh_success


def _handle_dead_account(name: str, is_disabled: bool) -> None:
    """统一处理彻底死亡账号（删除或禁用）。"""
    clean_email = name.replace(".json", "").strip()
    if cfg.REMOVE_DEAD_ACCOUNTS:
        print(f"[{ts()}] [WARNING] 凭证 {mask_email(name)} 彻底死亡，执行物理剔除...")
        requests.delete(
            _normalize_cpa_auth_files_url(cfg.CPA_API_URL),
            headers={"Authorization": f"Bearer {cfg.CPA_API_TOKEN}"},
            params={"name": name},
        )
        try:
            db_manager.remove_account_push_platform(clean_email, "CPA", exact_match=True)
            print(f"[{ts()}] [系统] 已同步清除 {mask_email(name)} 本地的 CPA 平台推送状态")
        except Exception:
            pass
    elif not is_disabled:
        print(f"[{ts()}] [INFO] 凭证 {mask_email(name)} 死亡，根据配置保留，正在禁用...")
        if set_cpa_auth_file_status(cfg.CPA_API_URL, cfg.CPA_API_TOKEN, name, disabled=True):
            print(f"[{ts()}] [SUCCESS] 死亡凭证 {mask_email(name)} 已成功禁用。")
    else:
        print(f"[{ts()}] [WARNING] 凭证 {mask_email(name)} 已死亡，当前已是禁用状态，根据配置保留不删除。")

def handle_registration_result(result: Any, cpa_upload: bool = False, run_ctx: dict = None, grok2api_upload: bool = False) -> str:
    def _format_cooldown_time(cooldown_until: float) -> str:
        if not cooldown_until:
            return ""
        try:
            return datetime.fromtimestamp(float(cooldown_until)).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return ""

    if getattr(cfg, 'GLOBAL_STOP', False):
        return "stopped"
    global run_stats

    last_email = mail_service.get_last_email()
    if (not last_email or "@" not in last_email) and result and isinstance(result, (tuple, list)) and len(result) >= 1:
        try:
            _tmp = json.loads(result[0]) if result[0] and result[0] != "retry_403" else {}
            if isinstance(_tmp, dict) and "@" in str(_tmp.get("email") or ""):
                last_email = str(_tmp.get("email"))
                mail_service.set_last_email(last_email)
        except Exception:
            pass
    if not last_email or "@" not in last_email:
        return "failed"

    if "+" in last_email:
        u_part, d_part = last_email.split("@")
        master_email = f"{u_part.split('+')[0]}@{d_part}"
        is_raw = False
    else:
        master_email = last_email
        is_raw = True

    is_dead = False
    if run_ctx:
        if run_ctx.get('pwd_blocked'):
            with _stats_lock: run_stats["pwd_blocked"] += 1
            is_dead = True
        if run_ctx.get('phone_verify'):
            with _stats_lock: run_stats["phone_verify"] += 1
            is_dead = True
    signup_blocked = run_ctx.get('signup_blocked', False) if run_ctx else False
    if (signup_blocked or is_dead) and getattr(cfg, "EMAIL_API_MODE", "") == "local_microsoft":
        if getattr(cfg, "LOCAL_MS_POOL_FISSION", False):
            db_manager.update_pool_fission_result(master_email, is_blocked=True, is_raw=is_raw)
        elif not getattr(cfg, "LOCAL_MS_ENABLE_FISSION", False):
            db_manager.update_local_mailbox_status(master_email, 3)
            print(f"[{ts()}] [WARNING] 触发风控，已将主号标记为死号: {mask_email(master_email)}")

    cur_dom = last_email.split("@")[-1] if last_email and "@" in last_email else None

    token_json_str = None
    password = None
    if result and isinstance(result, (tuple, list)) and len(result) >= 2:
        token_json_str, password = result

    ret_status = "success"
    discarded_email_failure = run_ctx.get('discarded_email_failure', False) if run_ctx else False
    domain_failure_reason = str(run_ctx.get('mail_domain_failure_reason', '') or '').strip().lower() if run_ctx else ''
    domain_failure_event = mail_service.pop_last_domain_failure_event()

    if not token_json_str or token_json_str == "retry_403":
        if token_json_str == "retry_403":
            with _stats_lock: run_stats["retries"] += 1
            print(f"[{ts()}] [WARNING] 检测到 403 频率限制，挂起重试...")
            ret_status = "retry_403"
        else:
            with _stats_lock: run_stats["failed"] += 1
            failure_domain = cur_dom
            failure_reason = domain_failure_reason
            if not failure_reason and discarded_email_failure:
                failure_reason = 'discarded_email'
            if not failure_reason and domain_failure_event:
                failure_reason = str(domain_failure_event.get('reason') or '').strip().lower()
                failure_domain = domain_failure_event.get('domain') or failure_domain
            if failure_reason:
                domain_result = mail_service.record_domain_failure(failure_domain, failure_reason)
                if domain_result:
                    cooldown_text = _format_cooldown_time(domain_result.get("cooldown_until", 0.0))
                    extra_text = f"，冷却结束时间: {cooldown_text}" if cooldown_text else ""
                    print(f"[{ts()}] [INFO] 失败域名 {mask_email(domain_result.get('domain', failure_domain or ''))} -> 异常 {domain_result.get('fail_count', 0)} / 成功 {domain_result.get('success_count', 0)} / 原因 {failure_reason}{extra_text}")
            ret_status = "failed"
        if cfg.ENABLE_SUB_DOMAINS:
            mail_service.clear_sticky_domain()
            print(f"[{ts()}] [系统] 域名 {mask_email(cur_dom or '')} 注册失败，下一轮重新生成。")

    else:
        with _stats_lock: run_stats["success"] += 1
        token_data    = json.loads(token_json_str)
        account_email = token_data.get("email", "unknown")

        if "agentIdentity" in token_data or token_data.get("auth_mode") == "agentIdentity":
            account_email = token_data.get("agentIdentity", {}).get("email", account_email)
            token_data = {
                "email": account_email,
                "status": "Codex_Identity",
                "codex_agent": token_data.copy()
            }
            token_json_str = json.dumps(token_data, ensure_ascii=False)

        if run_ctx and run_ctx.get('device_id') and run_ctx.get('user_agent'):
            token_data['device_id'] = run_ctx['device_id']
            token_data['user_agent'] = run_ctx['user_agent']
            token_json_str = json.dumps(token_data, ensure_ascii=False)

        domain_result = mail_service.record_domain_success(account_email if account_email and "@" in account_email else cur_dom)
        if domain_result:
            cooldown_text = _format_cooldown_time(domain_result.get("cooldown_until", 0.0))
            extra_text = f"，冷却结束时间: {cooldown_text}" if cooldown_text else ""
            print(f"[{ts()}] [INFO] 成功域名 {mask_email(domain_result.get('domain', cur_dom or ''))} -> 失败 {domain_result.get('fail_count', 0)} / 成功 {domain_result.get('success_count', 0)}{extra_text}")

        is_grok_token = (
            str(token_data.get("type", "")).lower() == "xai"
            or str(token_data.get("provider", "")).lower() == "grok"
            or str(getattr(cfg, "REG_PROVIDER", "openai")).lower() == "grok"
        )
        if cpa_upload:
            should_sync = cfg.SAVE_TO_LOCAL_IN_CPA_MODE
            mode_label = "CPA模式"
        elif grok2api_upload:
            should_sync = getattr(cfg, "SAVE_TO_LOCAL_IN_GROK2API_MODE", True)
            mode_label = "Grok2API模式"
        elif cfg.ENABLE_SUB2API_MODE:
            should_sync = cfg.SUB2API_SAVE_TO_LOCAL
            mode_label = "Sub2API模式"
        else:
            should_sync = True
            mode_label = "常规模式"
        if is_grok_token:
            # 仅调整展示名；是否入库由上面仓管模式开关决定
            mode_label = f"Grok/{mode_label}"

        if should_sync:
            if db_manager.save_account_to_db(account_email, password, token_json_str):
                print(f"[{ts()}] [SUCCESS] [{mode_label}] 账号密码与 Token 已安全存入: {mask_email(account_email)}")

        # CPA 云端上传
        if cpa_upload:
            current_status = token_data.get("status", "")
            if current_status in ["image2api", "仅注册成功"]:
                print(f"[{ts()}] [INFO] 当前账号状态为 [{current_status}]，跳过云端同步。")
                ret_status = "half_finished"
            else:
                success, up_msg = upload_to_cpa_integrated(token_data, cfg.CPA_API_URL, cfg.CPA_API_TOKEN)
                if success:
                    platform_tag = "Grok/CPA" if is_grok_token else "OpenAI"
                    print(f"[{ts()}] [SUCCESS] [{platform_tag}] 补货凭证 {mask_email(account_email)} 已自动同步至 CPA！")
                    try:
                        db_manager.update_account_push_info([account_email], "CPA", mode="sync")
                    except Exception:
                        pass
                else:
                    print(f"[{ts()}] [ERROR] CPA 云端上传失败: {up_msg}")

        # Grok/xAI 注册完成后导入 Grok2API。仓管补货模式下，导入失败不计作本轮补货成功。
        if is_grok_token and (grok2api_upload or getattr(cfg, "GROK2API_AUTO_IMPORT_AFTER_REGISTER", False)):
            ok, grok_msg = import_to_grok2api(token_data)
            if ok:
                print(f"[{ts()}] [SUCCESS] [Grok2API] 注册账号 {mask_email(account_email)} 已自动导入 Grok2API！")
                try:
                    db_manager.update_account_push_info([account_email], "GROK2API", mode="sync")
                except Exception:
                    pass
            else:
                print(f"[{ts()}] [ERROR] [Grok2API] 注册账号 {mask_email(account_email)} 自动导入失败: {grok_msg}")
                if grok2api_upload:
                    ret_status = "failed"

        if getattr(cfg, "LOCAL_MS_POOL_FISSION", False) and cfg.EMAIL_API_MODE == "local_microsoft":
            db_manager.update_pool_fission_result(master_email, is_blocked=False, is_raw=is_raw)
        elif not getattr(cfg, "LOCAL_MS_ENABLE_FISSION", False) and cfg.EMAIL_API_MODE == "local_microsoft":
            db_manager.update_local_mailbox_status(master_email, 2)

        safe_pwd = str(password) if password else ""
        orig_masked_email = mail_service.mask_email(account_email, force_mask=True)
        orig_masked_password = f"{safe_pwd[:2]}****{safe_pwd[-2:]}" if len(safe_pwd) > 4 else "****"

        final_email = orig_masked_email if getattr(cfg, 'TG_BOT', {}).get("mask_email", False) else account_email
        final_password = orig_masked_password if getattr(cfg, 'TG_BOT', {}).get("mask_password", False) else safe_pwd

        template_str = getattr(cfg, 'TG_BOT', {}).get("template_success", "成功: {email} / {password} 时间: {time}")
        beijing_tz = timezone(timedelta(hours=8))
        current_time = datetime.now(beijing_tz).strftime("%Y-%m-%d %H:%M:%S")
        try:
            success_text = template_str.format(email=final_email, password=final_password, time=current_time)
        except Exception:
            success_text = f"🎉 注册成功\n账号: {final_email}\n密码: {final_password}\n时间: {current_time}\n(温馨提示: 您的TG单号自定义模板配置有误)"

        send_tg_msg_sync(success_text)
    return ret_status


def dispatch_register(proxy, run_ctx=None, assigned_domain=None, batch_id=None, worker_index=None):
    """按 reg_provider 分发注册实现。默认 openai，grok 走 xAI 协议注册。"""
    if run_ctx is None:
        run_ctx = {}
    provider = str(getattr(cfg, "REG_PROVIDER", "openai") or "openai").strip().lower()
    if provider == "grok":
        from utils.grok_auth.register import run as grok_run
        return grok_run(
            proxy,
            run_ctx=run_ctx,
            assigned_domain=assigned_domain,
            batch_id=batch_id,
            worker_index=worker_index,
        )
    return run(
        proxy,
        run_ctx=run_ctx,
        assigned_domain=assigned_domain,
        batch_id=batch_id,
        worker_index=worker_index,
    )


def run_and_refresh(proxy, args, cpa_upload=False, skip_switch=False, assigned_domain=None, batch_id=None, worker_index=None, grok2api_upload=False):
    proxy = format_docker_url(proxy)
    """切节点 → 注册 → 处理结果。"""
    if not skip_switch:
        if not smart_switch_node(proxy):
            print(f"[{ts()}] [WARNING] {proxy} 节点切换失败，将使用当前 IP 继续尝试...")

    result = None
    run_ctx = {}
    try:
        result = dispatch_register(
            proxy,
            run_ctx=run_ctx,
            assigned_domain=assigned_domain,
            batch_id=batch_id,
            worker_index=worker_index,
        )
    except Exception as e:
        print(f"[{ts()}] [ERROR] 注册线程发生未捕获异常{e}")
        import traceback
        traceback.print_exc()

    return handle_registration_result(result, cpa_upload=cpa_upload, run_ctx=run_ctx, grok2api_upload=grok2api_upload)

# def auto_heal_subdomain(failed_domain: str):
    # print(f"[{ts()}] [自愈] 域名 {failed_domain} 达到失败阈值，触发更替程序...")
    # import wfxl_openai_regst
    # cf_cfg = getattr(cfg, '_c', {})
    # api_email = cf_cfg.get("cf_api_email")
    # api_key = cf_cfg.get("cf_api_key")
    # root_str = cf_cfg.get("mail_domains", "")
    # root_domains = [d.strip() for d in root_str.split(",") if d.strip()]

    # main_dom = None
    # for root in root_domains:
        # if failed_domain.endswith(root):
            # main_dom = root
            # break
    # if not main_dom:
        # print(f"[{ts()}] [ERROR] 无法识别 {failed_domain} 所属的主域，请检查配置！")
        # return


    # level = cf_cfg.get("sub_domain_level", 1)

    # try:
        # from cloudflare import Cloudflare
        # cf = Cloudflare(api_email=api_email, api_key=api_key)
        # zones = cf.zones.list(name=main_dom)
        # if zones.result:
            # zone_id = zones.result[0].id
            # url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/email/routing/dns"
            # headers = {"X-Auth-Email": api_email, "X-Auth-Key": api_key, "Content-Type": "application/json"}
            # payload = json.dumps({"name": failed_domain}).encode('utf-8')
            # requests.delete(url, data=payload, headers=headers, impersonate="chrome")
            # wfxl_openai_regst.dispatch_email_backend_delete(failed_domain, cf_cfg)
            # print(f"[{ts()}] [自愈] 已成功注销失效域名: {mask_email(failed_domain)}")
    # except Exception as e:
        # print(f"[{ts()}] [ERROR] 销毁失效域名异常: {e}")
        # return

    # refill_num = int(getattr(cfg, 'SUB_DOMAIN_REFILL_COUNT', 1))
    # new_domains = []
    # for _ in range(refill_num):
        # random_parts = []
        # for _ in range(level):
            # random_parts.append(''.join(random.choices(string.ascii_lowercase + string.digits, k=8)))
        # new_domains.append(".".join(random_parts) + f".{main_dom}")

    # with _heal_lock:
        # current_list = [d.strip() for d in cfg.SUB_DOMAINS_LIST.split(",") if d.strip()]
        # if failed_domain in current_list:
            # current_list.remove(failed_domain)
        # current_list.extend(new_domains)
        
        # config_path = "config.yaml"
        # try:
            # with open(config_path, "r", encoding="utf-8") as f:
                # y = yaml.safe_load(f) or {}
            # y["sub_domains_list"] = ",".join(current_list)
            # y["sub_domain_fail_threshold"] = cfg.SUB_DOMAIN_FAIL_THRESHOLD
            # y["sub_domain_refill_count"] = cfg.SUB_DOMAIN_REFILL_COUNT
            
            # with open(config_path, "w", encoding="utf-8") as f:
                # yaml.dump(y, f, allow_unicode=True, sort_keys=False)
            # reload_all_configs()
        # except Exception as e:
            # print(f"[{ts()}] [ERROR] 自愈配置保存失败: {e}")

    # for ns in new_domains:
        # try:
            # cf.email_routing.dns.create(zone_id=zone_id, name=ns)
            # wfxl_openai_regst.dispatch_email_backend_add(ns, cf_cfg)
            # print(f"[{ts()}] [自愈] 已补货新域名 {ns}，等待生效...")
        # except: pass

    # print(f"[{ts()}] [自愈] 正在进入状态监控，等待 Cloudflare 激活路由...")
    # retry_count = 0
    # while True:
        # try:
            # info = cf.email_routing.get(zone_id=zone_id)
            # res_data = getattr(info, 'result', info)
            # status = getattr(res_data, 'status', 'unknown')
            # synced = getattr(res_data, 'synced', False)

            # retry_count += 1
            
            # print(f"[{ts()}] [监控] (等待中...)")
            
            # if status == 'ready':
                # if synced is True or retry_count > 20: 
                    # print(f"[{ts()}] [SUCCESS] 域名池状态确认完成，准备恢复业务线程。")
                    # break
                    
        # except Exception as e:
            # print(f"[{ts()}] [WARNING] 状态监控请求异常 (重试中): {e}")
            # if retry_count > 6: break
            
        # time.sleep(10)
        
# def auto_heal_subdomain(failed_domain: str):
#     """
#     功能：仅销毁本地失效域名记录。
#     """
#     print(f"[{ts()}] [自愈] 域名 {failed_domain} 达到失败阈值，启动快速更替程序...")
#
#     cf_cfg = getattr(cfg, '_c', {})
#     root_str = cf_cfg.get("mail_domains", "")
#     root_domains = [d.strip() for d in root_str.split(",") if d.strip()]
#
#     main_dom = None
#     for root in root_domains:
#         if failed_domain.endswith(root):
#             main_dom = root
#             break
#
#     if not main_dom:
#         print(f"[{ts()}] [ERROR] 无法识别 {failed_domain} 所属的主域，跳过自愈。")
#         return
#
#     level = cf_cfg.get("sub_domain_level", 1)
#     refill_num = int(getattr(cfg, 'SUB_DOMAIN_REFILL_COUNT', 1))
#     new_domains = []
#     for _ in range(refill_num):
#         random_parts = []
#         for _ in range(level):
#             random_parts.append(''.join(random.choices(string.ascii_lowercase + string.digits, k=8)))
#         new_domains.append(".".join(random_parts) + f".{main_dom}")
#
#     with _heal_lock:
#         current_list = [d.strip() for d in cfg.SUB_DOMAINS_LIST.split(",") if d.strip()]
#         if failed_domain in current_list:
#             current_list.remove(failed_domain)
#         current_list.extend(new_domains)
#
#         config_path = "config.yaml"
#         try:
#             with open(config_path, "r", encoding="utf-8") as f:
#                 y = yaml.safe_load(f) or {}
#
#             y["sub_domains_list"] = ",".join(current_list)
#             y["sub_domain_fail_threshold"] = cfg.SUB_DOMAIN_FAIL_THRESHOLD
#             y["sub_domain_refill_count"] = cfg.SUB_DOMAIN_REFILL_COUNT
#
#             with open(config_path, "w", encoding="utf-8") as f:
#                 yaml.dump(y, f, allow_unicode=True, sort_keys=False)
#
#             reload_all_configs()
#             for ns in new_domains:
#                 print(f"[{ts()}] [自愈] 已成功补货新域名: {ns}")
#
#             print(f"[{ts()}] [SUCCESS] 配置文件已更新，业务线程将无缝切换新域名。")
#         except Exception as e:
#             print(f"[{ts()}] [ERROR] 自愈配置保存失败: {e}")

def _handle_sub2api_dead_account(item: dict, client: Any, is_disabled: bool) -> None:
    """统一处理 Sub2API 彻底死亡账号（删除或禁用）"""
    name = item.get("name", "unknown")
    account_id = item.get("id") 

    if cfg.SUB2API_REMOVE_DEAD_ACCOUNTS:
        print(f"[{ts()}] [ERROR] 凭证 {mask_email(name)} 彻底死亡，执行物理剔除...")
        if hasattr(client, "delete_account") and account_id:
            client.delete_account(account_id)
        try:
            db_manager.remove_account_push_platform(name, "SUB2API", exact_match=False)
            print(f"[{ts()}] [系统] 已同步清除 {mask_email(name)} 本地的 Sub2API 平台推送状态")
        except Exception:
            pass
    elif not is_disabled:
        print(f"[{ts()}] [ERROR] 凭证 {mask_email(name)} 死亡，根据配置保留，正在禁用...")
        if hasattr(client, "set_account_status") and account_id:
            client.set_account_status(account_id, disabled=True)
    else:
        print(f"[{ts()}] [ERROR] 凭证 {mask_email(name)} 已死亡，当前已是禁用状态，根据配置保留不删除。")


def process_sub2api_worker(i: int, total: int, item: dict, client: Any, args: Any) -> bool:
    """Sub2API 测活 Worker（使用 Sub2API /test SSE 接口）"""
    if hasattr(args, 'check_stop') and args.check_stop(): return False
    is_grok = _is_xai_like_token(item)
    if not is_grok and not _is_sub2api_openai_free_item(item):
        return True
    name = item.get("name", "unknown")
    account_id = item.get("id")
    # Grok 用 grok-4.5；OpenAI 继续用配置的测活模型
    test_model = "grok-4.5" if is_grok else None
    result, reason = client.test_account(account_id, model_id=test_model)

    if result == "ok":
        print(f"[{ts()}] [SUCCESS] Sub2API测活: {mask_email(name)} 状态健康")
        client.set_account_status(account_id, disabled=False)
        try:
            db_manager.update_account_status_by_truncated_name(name, 1)
        except Exception:
            pass
        return True

    if result == "quota":
        try:
            db_manager.update_account_status_by_truncated_name(name, 0)
        except Exception:
            pass
        if cfg.SUB2API_REMOVE_ON_LIMIT_REACHED:
            print(f"[{ts()}] [WARNING] Sub2API测活: {mask_email(name)} 额度耗尽，执行物理删除...")
            if account_id:
                client.delete_account(account_id)
            return False
        print(f"[{ts()}] [WARNING] Sub2API测活: {mask_email(name)} 额度限流，暂不计入有效库存，Sub2API 自动管理")
        return False

    print(f"[{ts()}] [ERROR] Sub2API测活: {mask_email(name)} 测活失败 ({reason})")
    refresh_success = False
    if is_grok:
        # Grok 不走 OpenAI refresh / OAuth 提权
        try:
            db_manager.update_account_status_by_fuzzy_name(name, 0)
        except Exception:
            pass
        _handle_sub2api_dead_account(item, client, is_disabled=False)
        return False
    if not cfg.SUB2API_ENABLE_TOKEN_REVIVE:
        print(f"[{ts()}] [ERROR] Token 普通复活已关闭。")
    else:
        refresh_token_val = item.get("credentials", {}).get("refresh_token")
        if not refresh_token_val:
            print(f"[{ts()}] [ERROR] {mask_email(name)} 无 refresh_token，跳过普通刷新")
        else:
            print(f"[{ts()}] [INFO] {mask_email(name)} 尝试刷新 Token...")
            proxies = {"http": args.proxy, "https": args.proxy} if args.proxy else None
            ok, new_tokens = refresh_oauth_token(refresh_token_val, proxies=proxies)

            if not ok:
                err_info = new_tokens.get('error', '未知') if isinstance(new_tokens, dict) else str(new_tokens)
                print(f"[{ts()}] [ERROR] {mask_email(name)} Token 刷新失败: {err_info}")
            else:
                print(f"[{ts()}] [INFO] {mask_email(name)} Token 刷新成功，同步至 Sub2API...")
                item.setdefault("credentials", {}).update(new_tokens)
                up_ok, up_msg = client.update_account(account_id, item)

                if not up_ok:
                    print(f"[{ts()}] [ERROR] {mask_email(name)} 更新回 Sub2API 失败: {up_msg}")
                else:
                    print(f"[{ts()}] [INFO] {mask_email(name)} Token 已更新，二次验证中...")
                    result2, reason2 = client.test_account(account_id)

                    if result2 == "ok":
                        print(f"[{ts()}] [SUCCESS] {mask_email(name)} 刷新复活成功，二次验证通过！")
                        try:
                            db_manager.update_account_status_by_truncated_name(name, 1)
                        except Exception:
                            pass
                        refresh_success = True
                    else:
                        print(f"[{ts()}] [ERROR] {mask_email(name)} 二次验证失败 ({reason2})，账号确认已死")

    if not refresh_success:
        if getattr(cfg, 'SUB2API_AUTO_RE_OAUTH', False):
            print(f"[{ts()}] [INFO] Sub2API测活: {mask_email(name)} 尝试终极抢救 -> 重走 OAuth 提权流程")
            time.sleep(random.uniform(1.0, 3.0))
            with _oauth_revive_semaphore:
                print(f"[{ts()}] [INFO] Sub2API测活: {mask_email(name)} 开始执行 OAuth 提权流程")
                full_info = db_manager.get_account_full_info(name)

                if full_info:
                    password = full_info.get("password")
                    if not password:
                        print(f"[{ts()}] [INFO] Sub2API测活: {mask_email(name)} 无密码记录，将尝试 [无密码 OTP] 通道提取")
                        password = "Takeover_NoPassword"

                    raw_token = full_info.get("token_data", {})
                    acc_token = raw_token.get("access_token", "") if isinstance(raw_token, dict) else ""
                    device_id = raw_token.get("device_id", "") if isinstance(raw_token, dict) else ""
                    user_agent = raw_token.get("user_agent", "") if isinstance(raw_token, dict) else ""

                    res = run_oauth_only_and_sync(name, password, args.proxy, args, access_token=acc_token,
                                                  device_id=device_id, user_agent=user_agent)
                    time.sleep(random.uniform(1.0, 3.0))

                    if res == "success":
                        return True
                else:
                    print(f"[{ts()}] [WARNING] Sub2API测活: {mask_email(name)} 本地库彻底查无此号，放弃抢救")
        try:
            db_manager.update_account_status_by_truncated_name(name, 0)
        except Exception:
            pass
        _handle_sub2api_dead_account(item, client, is_disabled=False)
        return False

    return refresh_success

def normal_main_loop(args, stop_event: threading.Event, executor=None):
    """常规量产模式（纯数据库保存）"""
    sleep_min    = max(1, cfg.NORMAL_SLEEP_MIN)
    sleep_max    = max(sleep_min, cfg.NORMAL_SLEEP_MAX)
    target_count = cfg.NORMAL_TARGET_COUNT

    print(f"\n[{ts()}] [系统] >>> 启动常规量产模式 <<<")
    if target_count > 0:
        print(f"[{ts()}] [系统] 任务目标: 注册 {target_count} 个账号后自动停止")
    else:
        print(f"[{ts()}] [系统] 任务目标: 无限挂机注册 (按 Ctrl+C 停止)")

    success_count  = 0
    total_attempts = 0

    while not stop_event.is_set() and not cfg.POOL_EXHAUSTED:
        if target_count > 0 and success_count >= target_count:
            print(f"\n[{ts()}] [SUCCESS] 已达到目标注册数量 ({target_count})，任务圆满结束！")
            break

        total_attempts += 1
        print(f"\n[{ts()}] [系统] 开始第 {total_attempts} 次注册 (已成功: {success_count}) ---")
        if stop_event.wait(1.0):
            break

        try:
            if cfg._clash_enable and not cfg._clash_pool_mode:
                print(f"[{ts()}] [INFO] 触发单端口共享模式，正在进行全局节点切换...")
                if not smart_switch_node(args.proxy):
                    print(f"[{ts()}] [WARNING] 全局节点切换失败，将使用当前 IP 继续尝试...")

            if cfg.ENABLE_MULTI_THREAD_REG:
                current_batch = (
                    min(cfg.REG_THREADS, target_count - success_count)
                    if target_count > 0 else cfg.REG_THREADS
                )
                print(f"[{ts()}] [INFO] 启用多线程并发 ({current_batch} 条通道)")

                should_preallocate_domains = (
                    current_batch > 1
                    and getattr(cfg, 'ENABLE_MAIL_DOMAIN_RUNTIME_CONTROL', False)
                )
                preallocated_domains = []
                batch_id = None
                if should_preallocate_domains:
                    batch_id = int(time.time() * 1000)
                    domain_pool = mail_service.get_configured_main_domains_snapshot()
                    preallocated_domains = mail_service.preallocate_main_domains_for_batch(domain_pool, current_batch)

                def _worker(worker_index=0, assigned_domain=None):
                    if stop_event.is_set(): return "stopped"
                    if cfg.is_raw_proxy_pool_enabled():
                        borrowed_generation, p = cfg.unpack_proxy_queue_item(cfg.PROXY_QUEUE.get())
                        try:
                            return run_and_refresh(
                                p,
                                args,
                                False,
                                skip_switch=True,
                                assigned_domain=assigned_domain,
                                batch_id=batch_id,
                                worker_index=worker_index,
                            )
                        finally:
                            if cfg.should_return_pooled_proxy(borrowed_generation):
                                cfg.PROXY_QUEUE.put(cfg.make_proxy_queue_item(p, borrowed_generation))
                                cfg.PROXY_QUEUE.task_done()
                    if cfg._clash_enable and cfg._clash_pool_mode:
                        p = cfg.PROXY_QUEUE.get()
                        proxy_url = p[-1] if isinstance(p, tuple) else p
                        try:
                            return run_and_refresh(
                                proxy_url,
                                args,
                                False,
                                skip_switch=False,
                                assigned_domain=assigned_domain,
                                batch_id=batch_id,
                                worker_index=worker_index,
                            )
                        finally:
                            cfg.PROXY_QUEUE.put(p)
                            cfg.PROXY_QUEUE.task_done()
                    return run_and_refresh(
                        args.proxy,
                        args,
                        False,
                        skip_switch=True,
                        assigned_domain=assigned_domain,
                        batch_id=batch_id,
                        worker_index=worker_index,
                    )

                if executor is not None:
                    futures = [
                        executor.submit(_worker, idx, preallocated_domains[idx] if idx < len(preallocated_domains) else None)
                        for idx in range(current_batch)
                    ]
                    for f in futures:
                        if f.result() == "success":
                            success_count += 1
                else:
                    with ThreadPoolExecutor(max_workers=current_batch) as ex:
                        futures = [
                            ex.submit(_worker, idx, preallocated_domains[idx] if idx < len(preallocated_domains) else None)
                            for idx in range(current_batch)
                        ]
                        for f in futures:
                            if f.result() == "success":
                                success_count += 1
            else:
                if cfg.is_raw_proxy_pool_enabled():
                    borrowed_generation, p = cfg.unpack_proxy_queue_item(cfg.PROXY_QUEUE.get())
                    try:
                        status = run_and_refresh(p, args, False, skip_switch=True)
                    finally:
                        if cfg.should_return_pooled_proxy(borrowed_generation):
                            cfg.PROXY_QUEUE.put(cfg.make_proxy_queue_item(p, borrowed_generation))
                            cfg.PROXY_QUEUE.task_done()
                elif cfg._clash_enable and cfg._clash_pool_mode:
                    p = cfg.PROXY_QUEUE.get()
                    proxy_url = p[-1] if isinstance(p, tuple) else p
                    try:
                        status = run_and_refresh(proxy_url, args, False, skip_switch=False)
                    finally:
                        cfg.PROXY_QUEUE.put(p)
                        cfg.PROXY_QUEUE.task_done()
                else:
                    status = run_and_refresh(args.proxy, args, False, skip_switch=True)

                if status == "success":
                    success_count += 1
            if cfg.EMAIL_API_MODE in ["local_microsoft", "gmail_fission"]:
                global_postman_fleet.clear_fleet()

        except Exception as e:
            print(f"[{ts()}] [ERROR] 发生未捕获全局异常: {e}")

        if target_count > 0 and success_count >= target_count:
            print(f"\n[{ts()}] [SUCCESS] 已达到目标注册数量 ({target_count})，任务圆满结束！")
            break

        if getattr(args, 'once', False):
            break

        wait_time = random.randint(sleep_min, sleep_max)
        print(f"[{ts()}] [INFO] 缓冲防风控，等待 {wait_time} 秒后继续...")
        if stop_event.wait(wait_time):
            break


async def perform_cpa_check(args, async_stop_event, loop, executor=None):
    print(f"[{ts()}] [INFO] 开始执行 CPA 仓库全量测活巡检...")
    res = requests.get(
        _normalize_cpa_auth_files_url(cfg.CPA_API_URL),
        headers={"Authorization": f"Bearer {cfg.CPA_API_TOKEN}"},
        timeout=20,
    )
    all_files = res.json().get("files", [])
    inventory_files = _filter_cpa_inventory_files(all_files)
    # codex 与 xai 都走 CPA api-call（test_cliproxy_auth_file 内部分流）
    test_files = list(inventory_files)
    total_files = len(test_files)
    xai_count = sum(1 for f in test_files if _is_xai_like_token(f))
    if xai_count:
        print(f"[{ts()}] [INFO] CPA 库存含 Grok/xAI {xai_count} 个，将通过 CPA api-call 测活")

    if executor is not None:
        futures = [
            loop.run_in_executor(executor, process_account_worker, i, len(test_files), item, args)
            for i, item in enumerate(test_files, 1)
        ]
        results = await asyncio.gather(*futures) if futures else []
    else:
        with ThreadPoolExecutor(max_workers=cfg.CPA_THREADS) as _ex:
            futures = [
                loop.run_in_executor(_ex, process_account_worker, i, len(test_files), item, args)
                for i, item in enumerate(test_files, 1)
            ]
            results = await asyncio.gather(*futures) if futures else []

    valid_count = sum(1 for r in results if r)
    print(f"[{ts()}] [INFO] CPA 测活结束，当前有效数: {valid_count} / {total_files}")
    return valid_count, total_files


async def perform_sub2api_check(args, async_stop_event, loop, client, executor=None):
    print(f"[{ts()}] [INFO] 开始执行 Sub2API 仓库全量测活巡检...")
    success, account_list = client.get_all_accounts()
    if not success:
        print(f"[{ts()}] [ERROR] 获取 Sub2API 全量库存失败: {account_list}")
        return 0, 0

    filtered_list = [
        item for item in account_list
        if _is_xai_like_token(item) or _is_sub2api_openai_free_item(item)
    ]

    total_files = len(filtered_list)

    if executor is not None:
        futures = [
            loop.run_in_executor(executor, process_sub2api_worker, i, total_files, item, client, args)
            for i, item in enumerate(filtered_list, 1)
        ]
        results = await asyncio.gather(*futures)
    else:
        with ThreadPoolExecutor(max_workers=cfg.SUB2API_THREADS) as _ex:
            futures = [
                loop.run_in_executor(_ex, process_sub2api_worker, i, total_files, item, client, args)
                for i, item in enumerate(filtered_list, 1)
            ]
            results = await asyncio.gather(*futures)

    valid_count = sum(1 for r in results if r)
    print(f"[{ts()}] [INFO] Sub2API 测活结束，当前有效数: {valid_count} / {total_files}")
    return valid_count, total_files


async def manual_check_main_loop(args, async_stop_event: asyncio.Event, executor=None):
    print("=" * 60)
    print(f"\n[{ts()}] [系统] >>> 启动独立测活清理任务 <<<")
    print("=" * 60)
    loop = asyncio.get_running_loop()

    check_task = None

    if cfg.ENABLE_CPA_MODE:
        check_task = asyncio.create_task(perform_cpa_check(args, async_stop_event, loop, executor=executor))
    elif cfg.ENABLE_SUB2API_MODE:
        client = Sub2APIClient(api_url=cfg.SUB2API_URL, api_key=cfg.SUB2API_KEY)
        check_task = asyncio.create_task(perform_sub2api_check(args, async_stop_event, loop, client, executor=executor))
    elif getattr(cfg, "ENABLE_GROK2API_MODE", False):
        ok_login, grok_token, login_msg = grok2api_admin_login()
        if ok_login:
            check_task = asyncio.create_task(perform_grok2api_check(args, async_stop_event, loop, grok_token, executor=executor))
        else:
            print(f"[{ts()}] [WARNING] Grok2API 登录失败，无法执行仓管测活: {login_msg}")
    else:
        print(f"[{ts()}] [WARNING] 当前未开启 CPA、Sub2API 或 Grok2API 模式，无法执行仓管测活。")

    if check_task:
        stop_task = asyncio.create_task(async_stop_event.wait())
        done, pending = await asyncio.wait(
            [check_task, stop_task],
            return_when=asyncio.FIRST_COMPLETED
        )

        if stop_task in done:
            print(f"\n[{ts()}] [INFO] 🛑 接收到强制停止信号，已瞬间中断测活任务！")
            check_task.cancel()
        else:
            print(f"\n[{ts()}] [SUCCESS] 独立测活任务执行完毕！")

    cfg.GLOBAL_STOP = True
    async_stop_event.set()


async def cpa_main_loop(args, async_stop_event: asyncio.Event, executor=None):
    """CPA 智能仓管模式（接入发牌器，防止撞车）。"""
    print("=" * 60)
    print(f"\n[{ts()}] [系统] 目标库存阈值: {cfg.MIN_ACCOUNTS_THRESHOLD} | 单次补发量: {cfg.BATCH_REG_COUNT}")
    print(
        f"\n[{ts()}] [系统] 周限额剔除规则: 剩余低于 {cfg.MIN_REMAINING_WEEKLY_PERCENT}%"
        if cfg.MIN_REMAINING_WEEKLY_PERCENT > 0
        else f"\n[{ts()}] [系统] 周限额剔除规则: 完全耗尽才剔除"
    )
    print("=" * 60)

    loop = asyncio.get_running_loop()

    while not async_stop_event.is_set() and not cfg.POOL_EXHAUSTED:
        try:
            if cfg.MIN_ACCOUNTS_THRESHOLD <= 0:
                total_files = 0
                valid_count = 0
                print(f"\n[{ts()}] [INFO] CPA 库存报警阈值为 0，跳过云端库存获取，直接按单次补发量执行补货。")
            elif cfg.CPA_AUTO_CHECK:
                valid_count, total_files = await perform_cpa_check(args, async_stop_event, loop, executor=executor)
            else:
                print(f"\n[{ts()}] [INFO] 自动测活已关闭，直接读取云端列表进行补发判断...")
                res = requests.get(
                    _normalize_cpa_auth_files_url(cfg.CPA_API_URL),
                    headers={"Authorization": f"Bearer {cfg.CPA_API_TOKEN}"},
                    timeout=20,
                )
                all_files = res.json().get("files", [])
                inventory_files = _filter_cpa_inventory_files(all_files)
                total_files = len(inventory_files)
                valid_count = total_files
                print(f"[{ts()}] [INFO] 当前云端总数: {total_files} (未开启自动巡检，默认全部视为有效)")


            if cfg.MIN_ACCOUNTS_THRESHOLD <= 0 or valid_count < cfg.MIN_ACCOUNTS_THRESHOLD:
                need_to_reg          = cfg.BATCH_REG_COUNT
                global run_stats
                run_stats["target"] += need_to_reg
                success_in_this_cycle = 0
                if cfg.MIN_ACCOUNTS_THRESHOLD <= 0:
                    print(f"[{ts()}] [INFO] 已禁用库存判断，直接启动补货 {need_to_reg} 个...")
                else:
                    print(f"[{ts()}] [INFO] 库存不足 ({valid_count} < {cfg.MIN_ACCOUNTS_THRESHOLD})，启动补货...")
                await asyncio.sleep(1)

                def _cpa_worker(worker_index=0, assigned_domain=None, batch_id=None):
                    if async_stop_event.is_set(): return "stopped"
                    if cfg.is_raw_proxy_pool_enabled():
                        borrowed_generation, p = cfg.unpack_proxy_queue_item(cfg.PROXY_QUEUE.get())
                        try:
                            return run_and_refresh(
                                p,
                                args,
                                cpa_upload=True,
                                skip_switch=True,
                                assigned_domain=assigned_domain,
                                batch_id=batch_id,
                                worker_index=worker_index,
                            )
                        finally:
                            if cfg.should_return_pooled_proxy(borrowed_generation):
                                cfg.PROXY_QUEUE.put(cfg.make_proxy_queue_item(p, borrowed_generation))
                                cfg.PROXY_QUEUE.task_done()
                    if cfg._clash_enable and cfg._clash_pool_mode:
                        p = cfg.PROXY_QUEUE.get()
                        proxy_url = p[-1] if isinstance(p, tuple) else p
                        try:
                            return run_and_refresh(
                                proxy_url,
                                args,
                                cpa_upload=True,
                                skip_switch=False,
                                assigned_domain=assigned_domain,
                                batch_id=batch_id,
                                worker_index=worker_index,
                            )
                        finally:
                            cfg.PROXY_QUEUE.put(p)
                            cfg.PROXY_QUEUE.task_done()
                    return run_and_refresh(
                        args.proxy,
                        args,
                        cpa_upload=True,
                        skip_switch=True,
                        assigned_domain=assigned_domain,
                        batch_id=batch_id,
                        worker_index=worker_index,
                    )

                while success_in_this_cycle < need_to_reg and not async_stop_event.is_set() and not cfg.POOL_EXHAUSTED:
                    remaining  = need_to_reg - success_in_this_cycle
                    batch_size = min(cfg.REG_THREADS, remaining)
                    preallocated_domains = []
                    batch_id = None

                    if cfg._clash_enable and not cfg._clash_pool_mode:
                        print(f"[{ts()}] [INFO] [CPA补货] 切换全局节点...")
                        if not smart_switch_node(args.proxy):
                            print(f"[{ts()}] [WARNING] [CPA补货] 全局节点切换失败，使用当前 IP 继续...")

                    if (
                        cfg.ENABLE_MULTI_THREAD_REG
                        and batch_size > 1
                        and getattr(cfg, 'ENABLE_MAIL_DOMAIN_RUNTIME_CONTROL', False)
                    ):
                        batch_id = int(time.time() * 1000)
                        domain_pool = mail_service.get_configured_main_domains_snapshot()
                        preallocated_domains = mail_service.preallocate_main_domains_for_batch(domain_pool, batch_size)

                    if cfg.ENABLE_MULTI_THREAD_REG:
                        print(f"[{ts()}] [INFO] 多线程补货: {success_in_this_cycle}/{need_to_reg} "
                              f"({batch_size} 线程)")
                        if executor is not None:
                            reg_futures = [
                                loop.run_in_executor(
                                    executor,
                                    _cpa_worker,
                                    idx,
                                    preallocated_domains[idx] if idx < len(preallocated_domains) else None,
                                    batch_id,
                                )
                                for idx in range(batch_size)
                            ]
                            reg_results = await asyncio.gather(*reg_futures)
                        else:
                            with ThreadPoolExecutor(max_workers=batch_size) as ex:
                                reg_futures = [
                                    loop.run_in_executor(
                                        ex,
                                        _cpa_worker,
                                        idx,
                                        preallocated_domains[idx] if idx < len(preallocated_domains) else None,
                                        batch_id,
                                    )
                                    for idx in range(batch_size)
                                ]
                                reg_results = await asyncio.gather(*reg_futures)
                        for status in reg_results:
                            if status == "success":
                                success_in_this_cycle += 1
                            elif status == "retry_403":
                                print(f"[{ts()}] [WARNING] 遇到 403 频率限制，给服务器 15 秒冷却时间...")
                                await asyncio.sleep(15)
                    else:
                        print(f"[{ts()}] [INFO] 单线程补货: {success_in_this_cycle}/{need_to_reg}")
                        if cfg.is_raw_proxy_pool_enabled():
                            borrowed_generation, p = cfg.unpack_proxy_queue_item(cfg.PROXY_QUEUE.get())
                            try:
                                status = await loop.run_in_executor(None, run_and_refresh, p, args, True, True)
                            finally:
                                if cfg.should_return_pooled_proxy(borrowed_generation):
                                    cfg.PROXY_QUEUE.put(cfg.make_proxy_queue_item(p, borrowed_generation))
                                    cfg.PROXY_QUEUE.task_done()
                        elif cfg._clash_enable and cfg._clash_pool_mode:
                            p = cfg.PROXY_QUEUE.get()
                            proxy_url = p[-1] if isinstance(p, tuple) else p
                            try:
                                status = await loop.run_in_executor(None, run_and_refresh, proxy_url, args, True, False)
                            finally:
                                cfg.PROXY_QUEUE.put(p)
                                cfg.PROXY_QUEUE.task_done()
                        else:
                            status = await loop.run_in_executor(
                                None, run_and_refresh, args.proxy, args, True, True
                            )
                        if status == "success":
                            success_in_this_cycle += 1
                        elif status == "retry_403":
                            await asyncio.sleep(10)
                        await asyncio.sleep(5)
                    if cfg.EMAIL_API_MODE in ["local_microsoft", "gmail_fission"]:
                        global_postman_fleet.clear_fleet()
                print(f"[{ts()}] [SUCCESS] 本轮补货完成！累计入库: {success_in_this_cycle} 个。")
            else:
                print(f"[{ts()}] [INFO] 仓库存量充足，无需补发。")

            if async_stop_event.is_set() or getattr(cfg, 'GLOBAL_STOP', False):
                print(f"[{ts()}] [系统] 主调度循环已彻底退出。")
                break

            print(f"[{ts()}] [INFO] 维护周期结束，{cfg.CHECK_INTERVAL_MINUTES} 分钟后进行下一次巡检...")
            try:
                await asyncio.wait_for(
                    async_stop_event.wait(),
                    timeout=cfg.CHECK_INTERVAL_MINUTES * 60,
                )
            except asyncio.TimeoutError:
                pass

        except Exception as e:
            print(f"[{ts()}] [ERROR] 主循环异常: {e}")
            try:
                await asyncio.wait_for(async_stop_event.wait(), timeout=60)
            except asyncio.TimeoutError:
                pass

async def sub2api_main_loop(args, async_stop_event: asyncio.Event, executor=None):
    """Sub2API 智能仓管模式"""
    print("=" * 60)
    print(f"\n[{ts()}] [系统] Sub2API 目标库存阈值: {cfg.SUB2API_MIN_THRESHOLD} | 单次补发量: {cfg.SUB2API_BATCH_COUNT}")
    print(f"\n[{ts()}] [系统] Sub2API 限额处理: 仅在真实耗尽后禁用或剔除")
    print("=" * 60)

    loop = asyncio.get_running_loop()
    client = Sub2APIClient(api_url=cfg.SUB2API_URL, api_key=cfg.SUB2API_KEY)

    while not async_stop_event.is_set() and not cfg.POOL_EXHAUSTED:

        try:
            if cfg.SUB2API_MIN_THRESHOLD <= 0:
                total_files = 0
                valid_count = 0
                print(f"\n[{ts()}] [INFO] Sub2API 库存报警阈值为 0，跳过云端库存获取，直接按单次补发量执行补货。")
            elif cfg.SUB2API_AUTO_CHECK:
                print(f"\n[{ts()}] [INFO] 开始执行 Sub2API 仓库例行巡检与测活...")
                success, account_list = client.get_all_accounts()
                if not success:
                    print(f"[{ts()}] [ERROR] 获取 Sub2API 全量库存失败: {account_list}")
                    try: await asyncio.wait_for(async_stop_event.wait(), timeout=60)
                    except asyncio.TimeoutError: pass
                    continue

                filtered_list = [
                    item for item in account_list
                    if item.get("platform") == "openai"
                       and str(item.get("credentials", {}).get("plan_type", "free")).lower() == "free"
                       and (item.get("extra") or {}).get("codex_5h_window_minutes", 0) == 0
                ]

                total_files = len(filtered_list)

                if executor is not None:
                    futures = [
                        loop.run_in_executor(executor, process_sub2api_worker, i, total_files, item, client, args)
                        for i, item in enumerate(filtered_list, 1)
                    ]
                    results = await asyncio.gather(*futures)
                else:
                    with ThreadPoolExecutor(max_workers=cfg.SUB2API_THREADS) as _ex:
                        futures = [
                            loop.run_in_executor(_ex, process_sub2api_worker, i, total_files, item, client, args)
                            for i, item in enumerate(filtered_list, 1)
                        ]
                        results = await asyncio.gather(*futures)

                valid_count = sum(1 for r in results if r)
                print(f"[{ts()}] [INFO] 巡检结束，当前 Sub2API 仓库有效数: {valid_count}")
            else:
                print(f"[{ts()}] [INFO] Sub2API 自动测活已关闭，直接读取云端列表进行补发判断...")
                success, account_list = client.get_all_accounts()
                if not success:
                    print(f"[{ts()}] [ERROR] 获取 Sub2API 全量库存失败: {account_list}")
                    try:
                        await asyncio.wait_for(async_stop_event.wait(), timeout=60)
                    except asyncio.TimeoutError:
                        pass
                    continue

                filtered_list = [
                    item for item in account_list
                    if item.get("platform") == "openai"
                       and str(item.get("credentials", {}).get("plan_type", "free")).lower() == "free"
                       and (item.get("extra") or {}).get("codex_5h_window_minutes", 0) == 0
                ]
                total_files = len(filtered_list)
                valid_count = total_files
                print(f"[{ts()}] [INFO] 当前云端总数: {total_files} (未开启自动巡检，默认全部视为有效)")

            if cfg.SUB2API_MIN_THRESHOLD <= 0 or valid_count < cfg.SUB2API_MIN_THRESHOLD:
                need_to_reg          = cfg.SUB2API_BATCH_COUNT
                global run_stats
                run_stats["target"] += need_to_reg
                success_in_this_cycle = 0
                if cfg.SUB2API_MIN_THRESHOLD <= 0:
                    print(f"[{ts()}] [INFO] 已禁用库存判断，直接启动 Sub2API 补货 {need_to_reg} 个...")
                else:
                    print(f"[{ts()}] [INFO] 库存不足 ({valid_count} < {cfg.SUB2API_MIN_THRESHOLD})，启动补货...")
                await asyncio.sleep(1)

                def _sub2api_run_wrapper(p, skip_switch, assigned_domain=None, batch_id=None, worker_index=None):
                    p = format_docker_url(p)
                    if not skip_switch:
                        if not smart_switch_node(p):
                            print(f"[{ts()}] [WARNING] [Sub2API补货] 全局节点切换失败...")
                    run_ctx = {}
                    result = dispatch_register(
                        p,
                        run_ctx=run_ctx,
                        assigned_domain=assigned_domain,
                        batch_id=batch_id,
                        worker_index=worker_index,
                    )
                    status = handle_registration_result(result, cpa_upload=False, run_ctx=run_ctx)

                    if status == "success":
                        token_dict = json.loads(result[0])
                        current_status = token_dict.get("status", "")
                        if current_status in ["image2api", "仅注册成功"]:
                            print(f"[{ts()}] [INFO] 当前为 [{current_status}]，跳过云端补货推送。")
                            return "half_finished"
                        else:
                            if hasattr(client, "add_account"):
                                ok, msg = client.add_account(token_dict)
                                if ok:
                                    print(f"[{ts()}] [SUCCESS] Sub2API 入库成功")
                                    try:
                                        db_manager.update_account_push_info([token_dict.get("email", "")], "SUB2API", mode="sync")
                                    except Exception:
                                        pass
                                else: print(f"[{ts()}] [ERROR] Sub2API 补货入库失败: {msg}")
                    return status

                def _sub2api_worker(worker_index=0, assigned_domain=None, batch_id=None):
                    if async_stop_event.is_set(): return "stopped"
                    if cfg.is_raw_proxy_pool_enabled():
                        borrowed_generation, p = cfg.unpack_proxy_queue_item(cfg.PROXY_QUEUE.get())
                        try:
                            return _sub2api_run_wrapper(
                                p,
                                True,
                                assigned_domain=assigned_domain,
                                batch_id=batch_id,
                                worker_index=worker_index,
                            )
                        finally:
                            if cfg.should_return_pooled_proxy(borrowed_generation):
                                cfg.PROXY_QUEUE.put(cfg.make_proxy_queue_item(p, borrowed_generation))
                                cfg.PROXY_QUEUE.task_done()
                    if cfg._clash_enable and cfg._clash_pool_mode:
                        p = cfg.PROXY_QUEUE.get()
                        proxy_url = p[-1] if isinstance(p, tuple) else p
                        try:
                            return _sub2api_run_wrapper(
                                proxy_url,
                                False,
                                assigned_domain=assigned_domain,
                                batch_id=batch_id,
                                worker_index=worker_index,
                            )
                        finally:
                            cfg.PROXY_QUEUE.put(p)
                            cfg.PROXY_QUEUE.task_done()
                    return _sub2api_run_wrapper(
                        args.proxy,
                        True,
                        assigned_domain=assigned_domain,
                        batch_id=batch_id,
                        worker_index=worker_index,
                    )

                while success_in_this_cycle < need_to_reg and not async_stop_event.is_set() and not cfg.POOL_EXHAUSTED:
                    remaining  = need_to_reg - success_in_this_cycle
                    batch_size = min(cfg.REG_THREADS, remaining)
                    preallocated_domains = []
                    batch_id = None

                    if cfg._clash_enable and not cfg._clash_pool_mode:
                        print(f"[{ts()}] [INFO] [Sub2API补货] 切换全局节点...")
                        if not smart_switch_node(args.proxy):
                            print(f"[{ts()}] [WARNING] [Sub2API补货] 全局节点切换失败，使用当前 IP 继续...")

                    if (
                        cfg.ENABLE_MULTI_THREAD_REG
                        and batch_size > 1
                        and getattr(cfg, 'ENABLE_MAIL_DOMAIN_RUNTIME_CONTROL', False)
                    ):
                        batch_id = int(time.time() * 1000)
                        domain_pool = mail_service.get_configured_main_domains_snapshot()
                        preallocated_domains = mail_service.preallocate_main_domains_for_batch(domain_pool, batch_size)

                    if cfg.ENABLE_MULTI_THREAD_REG:
                        print(f"[{ts()}] [INFO] 多线程补货: {success_in_this_cycle}/{need_to_reg} "
                              f"({batch_size} 线程)")
                        if executor is not None:
                            reg_futures = [
                                loop.run_in_executor(
                                    executor,
                                    _sub2api_worker,
                                    idx,
                                    preallocated_domains[idx] if idx < len(preallocated_domains) else None,
                                    batch_id,
                                )
                                for idx in range(batch_size)
                            ]
                            reg_results = await asyncio.gather(*reg_futures)
                        else:
                            with ThreadPoolExecutor(max_workers=batch_size) as ex:
                                reg_futures = [
                                    loop.run_in_executor(
                                        ex,
                                        _sub2api_worker,
                                        idx,
                                        preallocated_domains[idx] if idx < len(preallocated_domains) else None,
                                        batch_id,
                                    )
                                    for idx in range(batch_size)
                                ]
                                reg_results = await asyncio.gather(*reg_futures)

                        for status in reg_results:
                            if status == "success":
                                success_in_this_cycle += 1
                            elif status == "retry_403":
                                print(f"[{ts()}] [WARNING] 遇到 403 频率限制，给服务器 15 秒冷却时间...")
                                try: await asyncio.wait_for(async_stop_event.wait(), timeout=15)
                                except asyncio.TimeoutError: pass

                    else:
                        print(f"[{ts()}] [INFO] 单线程补货: {success_in_this_cycle}/{need_to_reg}")
                        if cfg.is_raw_proxy_pool_enabled():
                            borrowed_generation, p = cfg.unpack_proxy_queue_item(cfg.PROXY_QUEUE.get())
                            try:
                                status = await loop.run_in_executor(None, _sub2api_run_wrapper, p, True)
                            finally:
                                if cfg.should_return_pooled_proxy(borrowed_generation):
                                    cfg.PROXY_QUEUE.put(cfg.make_proxy_queue_item(p, borrowed_generation))
                                    cfg.PROXY_QUEUE.task_done()
                        elif cfg._clash_enable and cfg._clash_pool_mode:
                            p = cfg.PROXY_QUEUE.get()
                            proxy_url = p[-1] if isinstance(p, tuple) else p
                            try:
                                status = await loop.run_in_executor(None, _sub2api_run_wrapper, proxy_url, False)
                            finally:
                                cfg.PROXY_QUEUE.put(p)
                                cfg.PROXY_QUEUE.task_done()
                        else:
                            status = await loop.run_in_executor(
                                None, _sub2api_run_wrapper, args.proxy, True
                            )

                        if status == "success":
                            success_in_this_cycle += 1
                        elif status == "retry_403":
                            try: await asyncio.wait_for(async_stop_event.wait(), timeout=10)
                            except asyncio.TimeoutError: pass

                        try: await asyncio.wait_for(async_stop_event.wait(), timeout=5)
                        except asyncio.TimeoutError: pass
                    if cfg.EMAIL_API_MODE in ["local_microsoft", "gmail_fission"]:
                        global_postman_fleet.clear_fleet()
                print(f"[{ts()}] [SUCCESS] 本轮补货完成！累计入库 Sub2API: {success_in_this_cycle} 个。")
            else:
                print(f"[{ts()}] [INFO] 仓库存量充足，无需补发。")

            if async_stop_event.is_set() or getattr(cfg, 'GLOBAL_STOP', False):
                print(f"[{ts()}] [系统] 主调度循环已彻底退出。")
                break
            print(f"[{ts()}] [INFO] 维护周期结束，{cfg.SUB2API_CHECK_INTERVAL} 分钟后进行下一次巡检...")
            try:
                await asyncio.wait_for(
                    async_stop_event.wait(),
                    timeout=cfg.SUB2API_CHECK_INTERVAL * 60,
                )
            except asyncio.TimeoutError:
                pass

        except Exception as e:
            # import traceback
            # err_trace = traceback.format_exc()
            print(f"[{ts()}] [ERROR] Sub2API 循环发生致命异常: {e}")
            # for line in err_trace.split('\n'):
            #     if line.strip():
            #         print(f"[{ts()}] [ERROR] 堆栈追踪 -> {line.strip()}")
            print(f"[{ts()}] [INFO] 触发安全保护，系统已自动停止运行。")
            async_stop_event.set()
            break


async def perform_grok2api_check(args, async_stop_event, loop, token_value: str, executor=None):
    print(f"[{ts()}] [INFO] 开始执行 Grok2API 仓库全量测活巡检...")
    ok, account_list, msg = grok2api_list_accounts(token_value)
    if not ok:
        print(f"[{ts()}] [ERROR] 获取 Grok2API 全量库存失败: {msg}")
        return 0, 0

    filtered_list = [item for item in account_list if _is_grok2api_inventory_item(item)]
    total_files = len(filtered_list)
    if not filtered_list:
        print(f"[{ts()}] [INFO] Grok2API 当前无可管理账号")
        return 0, 0

    if executor is not None:
        futures = [
            loop.run_in_executor(executor, process_grok2api_worker, i, total_files, item, token_value, args)
            for i, item in enumerate(filtered_list, 1)
        ]
        results = await asyncio.gather(*futures)
    else:
        with ThreadPoolExecutor(max_workers=cfg.GROK2API_THREADS) as _ex:
            futures = [
                loop.run_in_executor(_ex, process_grok2api_worker, i, total_files, item, token_value, args)
                for i, item in enumerate(filtered_list, 1)
            ]
            results = await asyncio.gather(*futures)

    valid_count = sum(1 for r in results if r)
    print(f"[{ts()}] [INFO] Grok2API 测活结束，当前有效数: {valid_count} / {total_files}")
    return valid_count, total_files


async def grok2api_main_loop(args, async_stop_event: asyncio.Event, executor=None):
    """Grok2API 智能仓管模式：独立库存巡检、补货、导入。"""
    print("=" * 60)
    print(f"\n[{ts()}] [系统] Grok2API 目标库存阈值: {cfg.GROK2API_MIN_THRESHOLD} | 单次补发量: {cfg.GROK2API_BATCH_COUNT}")
    print(f"\n[{ts()}] [系统] Grok2API 限额处理: {'删除' if cfg.GROK2API_REMOVE_ON_LIMIT_REACHED else '禁用保留'}")
    print("=" * 60)

    loop = asyncio.get_running_loop()

    while not async_stop_event.is_set() and not cfg.POOL_EXHAUSTED:
        try:
            ok_login, grok_token, login_msg = grok2api_admin_login()
            if not ok_login:
                print(f"[{ts()}] [ERROR] Grok2API 登录失败，仓管暂停: {login_msg}")
                try:
                    await asyncio.wait_for(async_stop_event.wait(), timeout=60)
                except asyncio.TimeoutError:
                    pass
                continue

            if cfg.GROK2API_MIN_THRESHOLD <= 0:
                total_files = 0
                valid_count = 0
                print(f"\n[{ts()}] [INFO] Grok2API 库存报警阈值为 0，跳过云端库存获取，直接按单次补发量执行补货。")
            elif cfg.GROK2API_AUTO_CHECK:
                valid_count, total_files = await perform_grok2api_check(args, async_stop_event, loop, grok_token, executor=executor)
            else:
                print(f"\n[{ts()}] [INFO] Grok2API 自动测活已关闭，直接读取云端列表进行补发判断...")
                ok_list, account_list, list_msg = grok2api_list_accounts(grok_token)
                if not ok_list:
                    print(f"[{ts()}] [ERROR] 获取 Grok2API 全量库存失败: {list_msg}")
                    try:
                        await asyncio.wait_for(async_stop_event.wait(), timeout=60)
                    except asyncio.TimeoutError:
                        pass
                    continue
                filtered_list = [
                    item for item in account_list
                    if _is_grok2api_inventory_item(item) and item.get("enabled", True) and not _grok2api_quota_exhausted(item)
                ]
                total_files = len(filtered_list)
                valid_count = total_files
                print(f"[{ts()}] [INFO] 当前 Grok2API 有效库存: {valid_count} (未开启自动巡检，按列表状态估算)")

            if cfg.GROK2API_MIN_THRESHOLD <= 0 or valid_count < cfg.GROK2API_MIN_THRESHOLD:
                need_to_reg = cfg.GROK2API_BATCH_COUNT
                global run_stats
                run_stats["target"] += need_to_reg
                success_in_this_cycle = 0
                if cfg.GROK2API_MIN_THRESHOLD <= 0:
                    print(f"[{ts()}] [INFO] 已禁用库存判断，直接启动 Grok2API 补货 {need_to_reg} 个...")
                else:
                    print(f"[{ts()}] [INFO] Grok2API 库存不足 ({valid_count} < {cfg.GROK2API_MIN_THRESHOLD})，启动补货...")
                await asyncio.sleep(1)

                def _grok2api_run_wrapper(p, skip_switch, assigned_domain=None, batch_id=None, worker_index=None):
                    p = format_docker_url(p)
                    if not skip_switch:
                        if not smart_switch_node(p):
                            print(f"[{ts()}] [WARNING] [Grok2API补货] 全局节点切换失败...")
                    run_ctx = {}
                    result = dispatch_register(
                        p,
                        run_ctx=run_ctx,
                        assigned_domain=assigned_domain,
                        batch_id=batch_id,
                        worker_index=worker_index,
                    )
                    return handle_registration_result(result, cpa_upload=False, run_ctx=run_ctx, grok2api_upload=True)

                def _grok2api_worker(worker_index=0, assigned_domain=None, batch_id=None):
                    if async_stop_event.is_set():
                        return "stopped"
                    if cfg.is_raw_proxy_pool_enabled():
                        borrowed_generation, p = cfg.unpack_proxy_queue_item(cfg.PROXY_QUEUE.get())
                        try:
                            return _grok2api_run_wrapper(p, True, assigned_domain=assigned_domain, batch_id=batch_id, worker_index=worker_index)
                        finally:
                            if cfg.should_return_pooled_proxy(borrowed_generation):
                                cfg.PROXY_QUEUE.put(cfg.make_proxy_queue_item(p, borrowed_generation))
                                cfg.PROXY_QUEUE.task_done()
                    if cfg._clash_enable and cfg._clash_pool_mode:
                        p = cfg.PROXY_QUEUE.get()
                        proxy_url = p[-1] if isinstance(p, tuple) else p
                        try:
                            return _grok2api_run_wrapper(proxy_url, False, assigned_domain=assigned_domain, batch_id=batch_id, worker_index=worker_index)
                        finally:
                            cfg.PROXY_QUEUE.put(p)
                            cfg.PROXY_QUEUE.task_done()
                    return _grok2api_run_wrapper(args.proxy, True, assigned_domain=assigned_domain, batch_id=batch_id, worker_index=worker_index)

                while success_in_this_cycle < need_to_reg and not async_stop_event.is_set() and not cfg.POOL_EXHAUSTED:
                    remaining = need_to_reg - success_in_this_cycle
                    batch_size = min(cfg.REG_THREADS, remaining)
                    preallocated_domains = []
                    batch_id = None

                    if cfg._clash_enable and not cfg._clash_pool_mode:
                        print(f"[{ts()}] [INFO] [Grok2API补货] 切换全局节点...")
                        if not smart_switch_node(args.proxy):
                            print(f"[{ts()}] [WARNING] [Grok2API补货] 全局节点切换失败，使用当前 IP 继续...")

                    if (
                        cfg.ENABLE_MULTI_THREAD_REG
                        and batch_size > 1
                        and getattr(cfg, 'ENABLE_MAIL_DOMAIN_RUNTIME_CONTROL', False)
                    ):
                        batch_id = int(time.time() * 1000)
                        domain_pool = mail_service.get_configured_main_domains_snapshot()
                        preallocated_domains = mail_service.preallocate_main_domains_for_batch(domain_pool, batch_size)

                    if cfg.ENABLE_MULTI_THREAD_REG:
                        print(f"[{ts()}] [INFO] Grok2API 多线程补货: {success_in_this_cycle}/{need_to_reg} ({batch_size} 线程)")
                        if executor is not None:
                            reg_futures = [
                                loop.run_in_executor(
                                    executor,
                                    _grok2api_worker,
                                    idx,
                                    preallocated_domains[idx] if idx < len(preallocated_domains) else None,
                                    batch_id,
                                )
                                for idx in range(batch_size)
                            ]
                            reg_results = await asyncio.gather(*reg_futures)
                        else:
                            with ThreadPoolExecutor(max_workers=batch_size) as ex:
                                reg_futures = [
                                    loop.run_in_executor(
                                        ex,
                                        _grok2api_worker,
                                        idx,
                                        preallocated_domains[idx] if idx < len(preallocated_domains) else None,
                                        batch_id,
                                    )
                                    for idx in range(batch_size)
                                ]
                                reg_results = await asyncio.gather(*reg_futures)
                        for status in reg_results:
                            if status == "success":
                                success_in_this_cycle += 1
                            elif status == "retry_403":
                                print(f"[{ts()}] [WARNING] 遇到 403 频率限制，给服务器 15 秒冷却时间...")
                                try:
                                    await asyncio.wait_for(async_stop_event.wait(), timeout=15)
                                except asyncio.TimeoutError:
                                    pass
                    else:
                        print(f"[{ts()}] [INFO] Grok2API 单线程补货: {success_in_this_cycle}/{need_to_reg}")
                        if cfg.is_raw_proxy_pool_enabled():
                            borrowed_generation, p = cfg.unpack_proxy_queue_item(cfg.PROXY_QUEUE.get())
                            try:
                                status = await loop.run_in_executor(None, _grok2api_run_wrapper, p, True)
                            finally:
                                if cfg.should_return_pooled_proxy(borrowed_generation):
                                    cfg.PROXY_QUEUE.put(cfg.make_proxy_queue_item(p, borrowed_generation))
                                    cfg.PROXY_QUEUE.task_done()
                        elif cfg._clash_enable and cfg._clash_pool_mode:
                            p = cfg.PROXY_QUEUE.get()
                            proxy_url = p[-1] if isinstance(p, tuple) else p
                            try:
                                status = await loop.run_in_executor(None, _grok2api_run_wrapper, proxy_url, False)
                            finally:
                                cfg.PROXY_QUEUE.put(p)
                                cfg.PROXY_QUEUE.task_done()
                        else:
                            status = await loop.run_in_executor(None, _grok2api_run_wrapper, args.proxy, True)

                        if status == "success":
                            success_in_this_cycle += 1
                        elif status == "retry_403":
                            try:
                                await asyncio.wait_for(async_stop_event.wait(), timeout=10)
                            except asyncio.TimeoutError:
                                pass
                        try:
                            await asyncio.wait_for(async_stop_event.wait(), timeout=5)
                        except asyncio.TimeoutError:
                            pass

                    if cfg.EMAIL_API_MODE in ["local_microsoft", "gmail_fission"]:
                        global_postman_fleet.clear_fleet()
                print(f"[{ts()}] [SUCCESS] 本轮补货完成！累计入库 Grok2API: {success_in_this_cycle} 个。")
            else:
                print(f"[{ts()}] [INFO] Grok2API 仓库存量充足，无需补发。")

            if async_stop_event.is_set() or getattr(cfg, 'GLOBAL_STOP', False):
                print(f"[{ts()}] [系统] Grok2API 主调度循环已彻底退出。")
                break
            print(f"[{ts()}] [INFO] 维护周期结束，{cfg.GROK2API_CHECK_INTERVAL} 分钟后进行下一次巡检...")
            try:
                await asyncio.wait_for(async_stop_event.wait(), timeout=cfg.GROK2API_CHECK_INTERVAL * 60)
            except asyncio.TimeoutError:
                pass

        except Exception as e:
            print(f"[{ts()}] [ERROR] Grok2API 循环发生致命异常: {e}")
            async_stop_event.set()
            break

# 独立OAuth
def handle_oauth_upgrade_result(email: str, result: Any, run_ctx: dict = None) -> str:

    if getattr(cfg, 'GLOBAL_STOP', False):
        return "stopped"
    global run_stats
    fail_reason = "提权失败/被风控"
    if run_ctx:
        if run_ctx.get('pwd_blocked'):
            with _stats_lock: run_stats["pwd_blocked"] += 1
        if run_ctx.get('phone_verify'):
            with _stats_lock: run_stats["phone_verify"] += 1

    if not result or not isinstance(result, (tuple, list)) or len(result) < 2:
        with _stats_lock:
            run_stats["failed"] += 1
        try:
            db_manager.update_account_status([email], 0)
            print(f"[{ts()}] [WARNING] [提权] {mask_email(email)} 提权失败，已禁用")
        except:
            pass
        return "failed"

    token_json_str, password = result
    if not token_json_str or token_json_str == "retry_403":
        with _stats_lock:
            run_stats["failed"] += 1
        try:
            db_manager.update_account_status([email], 0)
            print(f"[{ts()}] [WARNING] [提权] {mask_email(email)} 提权失败，已标记为禁用")
        except:
            pass
        return "failed"

    with _stats_lock:
        run_stats["success"] += 1

    token_data = json.loads(token_json_str)
    if "email" not in token_data:
        token_data["email"] = email

    if run_ctx and run_ctx.get('device_id') and run_ctx.get('user_agent'):
        token_data['device_id'] = run_ctx['device_id']
        token_data['user_agent'] = run_ctx['user_agent']

    token_json_str = json.dumps(token_data, ensure_ascii=False)

    try:
        db_manager.update_account_token_only(email, token_json_str)
        db_manager.update_account_status([email], 1)
        print(f"[{ts()}] [SUCCESS] [提权] {mask_email(email)} 本地有效凭证已同步覆盖更新")
    except Exception as e:
        print(f"[{ts()}] [ERROR] 本地库更新失败: {e}")

    cpa_upload = getattr(cfg, 'ENABLE_CPA_MODE', False)
    sub2api_upload = getattr(cfg, 'ENABLE_SUB2API_MODE', False)

    if cpa_upload:
        current_status = token_data.get("status", "")
        if current_status in ["image2api", "仅注册成功"]:
            print(f"[{ts()}] [INFO] 当前账号状态为 [{current_status}]，跳过云端同步。")
        else:
            success, up_msg = upload_to_cpa_integrated(token_data, cfg.CPA_API_URL, cfg.CPA_API_TOKEN)
            if success:
                print(f"[{ts()}] [SUCCESS] [提权] 凭证 {mask_email(email)} 已同步至 CPA 云端！")
                try:
                    db_manager.update_account_push_info([email], "CPA", mode="sync")
                except Exception:
                    pass
            else:
                print(f"[{ts()}] [ERROR] [提权] 云端上传失败: {up_msg}")

    elif sub2api_upload:
        client = Sub2APIClient(api_url=cfg.SUB2API_URL, api_key=cfg.SUB2API_KEY)
        current_status = token_data.get("status", "")
        if current_status in ["image2api", "仅注册成功"]:
            print(f"[{ts()}] [INFO] 当前为 [{current_status}]，跳过云端补货推送。")
        else:
            if hasattr(client, "add_account"):
                ok, msg = client.add_account(token_data)
                if ok:
                    print(f"[{ts()}] [SUCCESS] [提权] 凭证 {mask_email(email)} 已同步至 Sub2API")
                    try:
                        db_manager.update_account_push_info([email], "SUB2API", mode="sync")
                    except Exception:
                        pass
                else:
                    print(f"[{ts()}] [ERROR] [提权] Sub2API 补货入库失败: {msg}")

    try:
        safe_pwd = str(password) if password else ""
        orig_masked_email = mask_email(email, force_mask=True)
        orig_masked_password = f"{safe_pwd[:2]}****{safe_pwd[-2:]}" if len(safe_pwd) > 4 else "****"

        final_email = orig_masked_email if getattr(cfg, 'TG_BOT', {}).get("mask_email", False) else email
        final_password = orig_masked_password if getattr(cfg, 'TG_BOT', {}).get("mask_password", False) else safe_pwd

        template_str = getattr(cfg, 'TG_BOT', {}).get("template_success", "成功: {email} / {password} 时间: {time}")
        beijing_tz = timezone(timedelta(hours=8))
        current_time = datetime.now(beijing_tz).strftime("%Y-%m-%d %H:%M:%S")
        success_text = template_str.format(email=final_email, password=final_password, time=current_time)
        success_text = "🚀 [OAuth提权] " + success_text
        send_tg_msg_sync(success_text)
    except Exception as e:
        pass

    return "success"


def run_oauth_only_and_sync(email, password, proxy, args, access_token="", device_id="", user_agent=""):
    proxy = format_docker_url(proxy)
    if not smart_switch_node(proxy):
        print(f"[{ts()}] [WARNING] {proxy} 节点切换失败...")

    run_ctx = {
        'pwd_blocked': False,
        'phone_verify': False
    }

    try:
        from utils.auth_pipeline.register import run_oauth_only
        result = run_oauth_only(email, password, proxy, run_ctx=run_ctx, access_token=access_token, device_id=device_id, user_agent=user_agent)
    except Exception as e:
        print(f"[{ts()}] [ERROR] 提权线程发生异常 {e}")
        import traceback
        traceback.print_exc()
        result = None
    return handle_oauth_upgrade_result(email, result, run_ctx=run_ctx)


def oauth_upgrade_main_loop(args, target_accounts: list, stop_event: threading.Event, executor=None):
    total_tasks = len(target_accounts)
    print(f"\n[{ts()}] [系统] >>> 启动独立 OAuth 批量提取任务 <<<")
    print(f"[{ts()}] [系统] 目标队列共计 {total_tasks} 个半成品账号待处理。")
    global run_stats
    with _stats_lock:
        run_stats["success"] = 0
        run_stats["failed"] = 0
        run_stats["retries"] = 0
        run_stats["pwd_blocked"] = 0
        run_stats["phone_verify"] = 0
        run_stats["start_time"] = time.time()
        run_stats["target"] = total_tasks

    max_workers = getattr(cfg, 'REG_THREADS', 4)

    def _worker(acc):
        if stop_event.is_set() or getattr(cfg, 'GLOBAL_STOP', False):
            return "stopped"

        acc_token = acc.get('access_token', '')
        device_id = acc.get('device_id', '')
        user_agent = acc.get('user_agent', '')
        if cfg.is_raw_proxy_pool_enabled():
            borrowed_generation, p = cfg.unpack_proxy_queue_item(cfg.PROXY_QUEUE.get())
            try:
                return run_oauth_only_and_sync(acc['email'], acc['password'], p, args, acc_token, device_id, user_agent)
            finally:
                if cfg.should_return_pooled_proxy(borrowed_generation):
                    cfg.PROXY_QUEUE.put(cfg.make_proxy_queue_item(p, borrowed_generation))
                    cfg.PROXY_QUEUE.task_done()
        elif cfg._clash_enable and getattr(cfg, '_clash_pool_mode', False):
            p = cfg.PROXY_QUEUE.get()
            proxy_url = p[-1] if isinstance(p, tuple) else p
            try:
                return run_oauth_only_and_sync(acc['email'], acc['password'], proxy_url, args, acc_token, device_id, user_agent)
            finally:
                cfg.PROXY_QUEUE.put(p)
                cfg.PROXY_QUEUE.task_done()
        else:
            return run_oauth_only_and_sync(acc['email'], acc['password'], args.proxy, args, acc_token, device_id, user_agent)

    try:
        if executor is not None:
            futures = [executor.submit(_worker, acc) for acc in target_accounts]
        else:
            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                futures = [ex.submit(_worker, acc) for acc in target_accounts]

        import concurrent.futures
        for f in concurrent.futures.as_completed(futures):
            pass

    except Exception as e:
        print(f"[{ts()}] [ERROR] 提权调度发生异常: {e}")

    print(f"\n[{ts()}] [系统] <<< OAuth 批量提取任务结束 >>>")

def main() -> None:
    reload_all_configs()
    parser = argparse.ArgumentParser(description="OpenAI 自动注册 & CPA 检测一体")
    parser.add_argument("--proxy", default=None, help="代理地址")
    # parser.add_argument("--once", action="store_true", help="只运行一次")
    args       = parser.parse_args()
    args.proxy = cfg.DEFAULT_PROXY if cfg.DEFAULT_PROXY.strip() else None

    if cfg.ENABLE_CPA_MODE:
        print("   当前状态: [ CPA 智能仓管模式 ] 已开启")
    elif getattr(cfg, "ENABLE_GROK2API_MODE", False):
        print("   当前状态: [ Grok2API 智能仓管模式 ] 已开启")
    elif cfg.ENABLE_SUB2API_MODE:
        print("   当前状态: [ Sub2API 智能仓管模式 ] 已开启")
    else:
        print("   当前状态: [ 常规量产模式 ] 已开启")
    print("=" * 65)

    if cfg.ENABLE_CPA_MODE:
        try:
            asyncio.run(cpa_main_loop(args, asyncio.Event()))
        except KeyboardInterrupt:
            print(f"\n[{ts()}] [INFO] 用户终止了系统运行。")
    elif getattr(cfg, "ENABLE_GROK2API_MODE", False):
        try:
            asyncio.run(grok2api_main_loop(args, asyncio.Event()))
        except KeyboardInterrupt:
            print(f"\n[{ts()}] [INFO] 用户终止了系统运行。")
    else:
        stop_event = threading.Event()
        try:
            normal_main_loop(args, stop_event)
        except KeyboardInterrupt:
            print(f"\n[{ts()}] [INFO] 用户终止了系统运行。")


class RegEngine:
    """GUI 用控制类，封装线程/协程生命周期。"""
    def __init__(self):
        self.thread_stop_event = threading.Event()
        self.async_stop_event  = None
        self.current_thread    = None
        self.loop              = None
        self._force_stopped    = False
        self._executor         = None

    def _ensure_executor(self, max_workers=None):
        if self._executor is None:
            workers = max_workers or max(cfg.REG_THREADS, getattr(cfg, 'CPA_THREADS', 4), getattr(cfg, 'SUB2API_THREADS', 4), getattr(cfg, 'GROK2API_THREADS', 4))
            self._executor = ThreadPoolExecutor(max_workers=workers)
        return self._executor

    def _shutdown_executor(self):
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

    def _finalize_thread_run(self):
        if self.loop is not None:
            self.loop.close()
            self.loop = None
        self.async_stop_event = None
        if self.current_thread is threading.current_thread():
            self._shutdown_executor()

    def start_normal(self, args):
        if self.is_running():
            return
        self._force_stopped = False
        cfg.GLOBAL_STOP = False
        cfg.POOL_EXHAUSTED = False
        self.thread_stop_event = threading.Event()

        current_evt = self.thread_stop_event
        args.check_stop = lambda: current_evt.is_set()
        self._ensure_executor()
        self.current_thread = threading.Thread(
            target=self._run_normal_in_thread,
            args=(args,),
            daemon=True,
        )
        self.current_thread.start()

    def start_cpa(self, args):
        if self.is_running():
            return
        self._force_stopped = False
        cfg.GLOBAL_STOP = False
        cfg.POOL_EXHAUSTED = False
        self.thread_stop_event = threading.Event()

        self._ensure_executor()
        self.current_thread = threading.Thread(
            target=self._run_cpa_in_thread, args=(args,), daemon=True
        )
        self.current_thread.start()

    def start_sub2api(self, args):
        if self.is_running():
            return
        self._force_stopped = False
        cfg.GLOBAL_STOP = False
        cfg.POOL_EXHAUSTED = False
        self.thread_stop_event = threading.Event()
        self._ensure_executor()
        self.current_thread = threading.Thread(
            target=self._run_sub2api_in_thread, args=(args,), daemon=True
        )
        self.current_thread.start()

    def start_grok2api(self, args):
        if self.is_running():
            return
        self._force_stopped = False
        cfg.GLOBAL_STOP = False
        cfg.POOL_EXHAUSTED = False
        self.thread_stop_event = threading.Event()
        self._ensure_executor()
        self.current_thread = threading.Thread(
            target=self._run_grok2api_in_thread, args=(args,), daemon=True
        )
        self.current_thread.start()

    def _run_cpa_in_thread(self, args):
        self._perform_initial_cleanup()
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._cpa_wrapper(args))
        finally:
            self._finalize_thread_run()

    def _run_normal_in_thread(self, args):
        self._perform_initial_cleanup()
        try:
            normal_main_loop(args, self.thread_stop_event, executor=self._executor)
        except Exception as e:
            print(f"\n[{ts()}] [CRITICAL] 引擎主线程发生致命崩溃: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self._finalize_thread_run()

    def _run_sub2api_in_thread(self, args):
        self._perform_initial_cleanup()
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.async_stop_event = asyncio.Event()
            self.loop.run_until_complete(sub2api_main_loop(args, self.async_stop_event, executor=self._executor))
        finally:
            self._finalize_thread_run()

    def _run_grok2api_in_thread(self, args):
        self._perform_initial_cleanup()
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.async_stop_event = asyncio.Event()
            self.loop.run_until_complete(grok2api_main_loop(args, self.async_stop_event, executor=self._executor))
        finally:
            self._finalize_thread_run()

    async def _cpa_wrapper(self, args):
        self.async_stop_event = asyncio.Event()
        await cpa_main_loop(args, self.async_stop_event, executor=self._executor)

    def stop(self):
        self._force_stopped = True
        cfg.GLOBAL_STOP = True
        cfg.POOL_EXHAUSTED = True
        self.thread_stop_event.set()
        if self.loop and self.async_stop_event:
            self.loop.call_soon_threadsafe(self.async_stop_event.set)
        # 停止时立刻关掉 Grok 过盾浏览器，避免窗口/进程残留
        try:
            from utils.grok_auth.embedded_turnstile import stop_embedded_solver
            stop_embedded_solver(timeout=5.0)
        except Exception:
            pass
        try:
            from utils.grok_auth.local_solver_manager import stop_local_solver_if_owned
            stop_local_solver_if_owned(timeout=5.0)
        except Exception:
            pass
        time.sleep(0.5)
        self._shutdown_executor()
        if cfg.EMAIL_API_MODE in ["local_microsoft", "gmail_fission"]:
            try:
                from utils.email_providers.postman_center import global_postman_fleet
                global_postman_fleet.clear_fleet()
            except Exception:
                pass

    def is_running(self) -> bool:
        if self._force_stopped:
            return False
        return self.current_thread is not None and self.current_thread.is_alive()

    def start_check(self, args):
        if self.is_running(): return
        self._force_stopped = False
        cfg.GLOBAL_STOP = False
        cfg.POOL_EXHAUSTED = False
        self.thread_stop_event = threading.Event()
        self._ensure_executor()
        self.current_thread = threading.Thread(
            target=self._run_check_in_thread, args=(args,), daemon=True
        )
        self.current_thread.start()

    def _run_check_in_thread(self, args):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.async_stop_event = asyncio.Event()
            self.loop.run_until_complete(manual_check_main_loop(args, self.async_stop_event, executor=self._executor))
        finally:
            self._finalize_thread_run()
            self._force_stopped = True

    def _perform_initial_cleanup(self):
        if not getattr(cfg, 'TEAM_MODE_ENABLE', False):
            return

        print(f"[{cfg.ts()}] [系统] 🚀 正在执行开局环境初始化，请不要着急耐心等待...")
        from utils.auth_core import sys_node_bulk_silent

        raw_proxy_item = None
        clash_proxy_item = None
        borrowed_generation = None
        proxy_url = getattr(cfg, 'DEFAULT_PROXY', None)
        try:
            if cfg.is_raw_proxy_pool_enabled() and not cfg.PROXY_QUEUE.empty():
                raw_proxy_item = cfg.PROXY_QUEUE.get_nowait()
                borrowed_generation, p_url = cfg.unpack_proxy_queue_item(raw_proxy_item)
                proxy_url = p_url
            elif getattr(cfg, '_clash_enable', False) and getattr(cfg, '_clash_pool_mode',
                                                                  False) and not cfg.PROXY_QUEUE.empty():
                clash_proxy_item = cfg.PROXY_QUEUE.get_nowait()
                proxy_url = clash_proxy_item[-1] if isinstance(clash_proxy_item, tuple) else clash_proxy_item

            if proxy_url and not proxy_url.startswith(("http://", "https://", "socks4://", "socks5://")):
                proxy_url = f"http://{proxy_url}"

            proxies_dict = {"http": proxy_url, "https": proxy_url} if proxy_url else None
            sys_node_bulk_silent(proxies=proxies_dict, force_all=True)
            print(f"[{cfg.ts()}] [系统] ✨ 开局清理完毕。")
        except Exception as e:
            print(f"[{cfg.ts()}] [ERROR] 开局清理异常: {e}")
        finally:
            if raw_proxy_item is not None:
                if cfg.should_return_pooled_proxy(borrowed_generation):
                    cfg.PROXY_QUEUE.put(cfg.make_proxy_queue_item(proxy_url, borrowed_generation))
                cfg.PROXY_QUEUE.task_done()
            elif clash_proxy_item is not None:
                cfg.PROXY_QUEUE.put(clash_proxy_item)
                cfg.PROXY_QUEUE.task_done()

    def start_oauth_upgrade(self, args, target_accounts: list):
        if self.is_running():
            return False, "引擎当前正在运行其他任务，请先点击停止"

        self._force_stopped = False
        cfg.GLOBAL_STOP = False
        cfg.POOL_EXHAUSTED = False
        self.thread_stop_event = threading.Event()

        current_evt = self.thread_stop_event
        args.check_stop = lambda: current_evt.is_set()

        self._ensure_executor()
        self.current_thread = threading.Thread(
            target=self._run_oauth_upgrade_in_thread,
            args=(args, target_accounts),
            daemon=True,
        )
        self.current_thread.start()
        return True, "提权任务启动成功"

    def _run_oauth_upgrade_in_thread(self, args, target_accounts):
        self._perform_initial_cleanup()
        try:
            oauth_upgrade_main_loop(args, target_accounts, self.thread_stop_event, executor=self._executor)
        except Exception as e:
            print(f"[{ts()}] [CRITICAL] 提权引擎主线程发生崩溃: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self._finalize_thread_run()



if __name__ == "__main__":
    main()