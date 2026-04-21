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
from utils.register import run, refresh_oauth_token as _refresh_oauth_token
from utils.proxy_manager import smart_switch_node
from utils.integrations.sub2api_client import Sub2APIClient
from utils.integrations.tg_notifier import send_tg_msg_sync
from utils.email_providers.postman_center import global_postman_fleet

_stats_lock = threading.Lock()
sub_fail_counts = {}
_heal_lock = threading.Lock()
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
            timeout=15, impersonate="chrome110",
        )
        if res.status_code in (200, 204):
            return True
        print(f"[{ts()}] [ERROR] 切换凭证状态失败 (HTTP {res.status_code}): {res.text}")
        return False
    except Exception as e:
        print(f"[{ts()}] [ERROR] 切换凭证状态异常: {e}")
        return False


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
            timeout=30, impersonate="chrome110",
        )
        if resp.status_code in (200, 201):
            return True, "上传成功"
        if resp.status_code in (404, 405, 415):
            raw_url = f"{upload_url}?name={urllib.parse.quote(filename)}"
            fb = requests.post(
                raw_url, data=file_content,
                headers={"Authorization": f"Bearer {api_token}",
                         "Content-Type": "application/json"},
                timeout=30, impersonate="chrome110",
            )
            if fb.status_code in (200, 201):
                return True, "上传成功"
            resp = fb
        return False, f"HTTP {resp.status_code}"
    except Exception as e:
        return False, str(e)


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


def test_cliproxy_auth_file(item: dict, api_url: str, api_token: str) -> Tuple[bool, str]:
    auth_index = item.get("auth_index")
    base_url   = api_url.strip().rstrip("/")
    call_url   = (
        base_url.replace("/auth-files", "/api-call")
        if "/auth-files" in base_url
        else f"{base_url}/v0/management/api-call"
    )
    payload = {
        "authIndex": auth_index,
        "method":    "GET",
        "url":       "https://chatgpt.com/backend-api/wham/usage",
        "header": {
            "Authorization":     "Bearer $TOKEN$",
            "Content-Type":      "application/json",
            "User-Agent":        DEFAULT_CLIPROXY_UA,
            "Chatgpt-Account-Id": str(item.get("account_id") or ""),
        },
    }
    try:
        resp = requests.post(
            call_url,
            headers={"Authorization": f"Bearer {api_token}"},
            json=payload, timeout=60, impersonate="chrome110",
        )
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}"
        data        = resp.json()
        item['_raw_usage'] = data
        status_code = data.get("status_code", 0)
        reason      = _extract_cliproxy_failure_reason(data, cfg.MIN_REMAINING_WEEKLY_PERCENT)
        if status_code >= 400 or reason:
            return False, reason or f"HTTP {status_code}"
        return True, "正常"
    except Exception:
        return False, "测活超时"

def test_sub2api_account_direct(item: dict, proxy: str) -> Tuple[bool, str]:
    """直连 OpenAI 接口进行 Sub2API 账号测活，并实时提取真实额度"""
    credentials = item.get("credentials", {})
    access_token = credentials.get("access_token")
    account_id = credentials.get("chatgpt_account_id", "")
    
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
            impersonate="chrome110"
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
    is_disabled = item.get("disabled", False)
    is_ok, msg  = test_cliproxy_auth_file(item, cfg.CPA_API_URL, cfg.CPA_API_TOKEN)

    if is_ok:
        if is_disabled:
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

    if "周限额" in msg or "usage_limit_reached" in msg:
        if cfg.REMOVE_ON_LIMIT_REACHED:
            print(f"[{ts()}] [INFO] 触发限额剔除规则，执行物理剔除...")
            requests.delete(
                _normalize_cpa_auth_files_url(cfg.CPA_API_URL),
                headers={"Authorization": f"Bearer {cfg.CPA_API_TOKEN}"},
                params={"name": name},
            )
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
        _handle_dead_account(name, is_disabled)
    return refresh_success


def _handle_dead_account(name: str, is_disabled: bool) -> None:
    """统一处理彻底死亡账号（删除或禁用）。"""
    if cfg.REMOVE_DEAD_ACCOUNTS:
        print(f"[{ts()}] [WARNING] 凭证 {mask_email(name)} 彻底死亡，执行物理剔除...")
        requests.delete(
            _normalize_cpa_auth_files_url(cfg.CPA_API_URL),
            headers={"Authorization": f"Bearer {cfg.CPA_API_TOKEN}"},
            params={"name": name},
        )
    elif not is_disabled:
        print(f"[{ts()}] [INFO] 凭证 {mask_email(name)} 死亡，根据配置保留，正在禁用...")
        if set_cpa_auth_file_status(cfg.CPA_API_URL, cfg.CPA_API_TOKEN, name, disabled=True):
            print(f"[{ts()}] [SUCCESS] 死亡凭证 {mask_email(name)} 已成功禁用。")
    else:
        print(f"[{ts()}] [WARNING] 凭证 {mask_email(name)} 已死亡，当前已是禁用状态，根据配置保留不删除。")

def handle_registration_result(result: Any, cpa_upload: bool = False, run_ctx: dict = None) -> str:
    if getattr(cfg, 'GLOBAL_STOP', False):
        return "stopped"
    global run_stats

    last_email = mail_service.get_last_email()
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
     
    if not token_json_str or token_json_str == "retry_403":
        if token_json_str == "retry_403":
            with _stats_lock: run_stats["retries"] += 1
            print(f"[{ts()}] [WARNING] 检测到 403 频率限制，挂起重试...")
            ret_status = "retry_403"
        else:
            with _stats_lock: run_stats["failed"] += 1
            ret_status = "failed"
        if cfg.ENABLE_SUB_DOMAINS:
            mail_service.clear_sticky_domain() 
            print(f"[{ts()}] [系统] 域名 {mask_email(cur_dom or '')} 注册失败，下一轮重新生成。")
            
    else:
        with _stats_lock: run_stats["success"] += 1
        token_data    = json.loads(token_json_str)
        account_email = token_data.get("email", "unknown")

        # 存入本地数据库
        if cpa_upload:
            should_sync = cfg.SAVE_TO_LOCAL_IN_CPA_MODE
            mode_label = "CPA模式"
        elif cfg.ENABLE_SUB2API_MODE:
            should_sync = cfg.SUB2API_SAVE_TO_LOCAL
            mode_label = "Sub2API模式"
        else:
            should_sync = True
            mode_label = "常规模式"

        if should_sync:
            if db_manager.save_account_to_db(account_email, password, token_json_str):
                print(f"[{ts()}] [SUCCESS] [{mode_label}] 账号密码与 Token 已安全存入: {mask_email(account_email)}")

        # CPA 云端上传
        if cpa_upload:
            success, up_msg = upload_to_cpa_integrated(token_data, cfg.CPA_API_URL, cfg.CPA_API_TOKEN)
            if success:
                print(f"[{ts()}] [SUCCESS] 补货凭证 {mask_email(account_email)} 云端上传成功！")
            else:
                print(f"[{ts()}] [ERROR] 云端上传失败: {up_msg}")

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

def run_and_refresh(proxy, args, cpa_upload=False, skip_switch=False):
    proxy = format_docker_url(proxy)
    """切节点 → 注册 → 处理结果。"""
    if not skip_switch:
        if not smart_switch_node(proxy):
            print(f"[{ts()}] [WARNING] {proxy} 节点切换失败，将使用当前 IP 继续尝试...")
    
    result = None
    run_ctx = {}
    try:
        result = run(proxy, run_ctx=run_ctx)
    except Exception as e:
        print(f"[{ts()}] [ERROR] 注册线程发生未捕获异常{e}")
        import traceback
        traceback.print_exc()

    return handle_registration_result(result, cpa_upload=cpa_upload, run_ctx=run_ctx)

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
            # requests.delete(url, data=payload, headers=headers, impersonate="chrome110")
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
    elif not is_disabled:
        print(f"[{ts()}] [ERROR] 凭证 {mask_email(name)} 死亡，根据配置保留，正在禁用...")
        if hasattr(client, "set_account_status") and account_id:
            client.set_account_status(account_id, disabled=True)
    else:
        print(f"[{ts()}] [ERROR] 凭证 {mask_email(name)} 已死亡，当前已是禁用状态，根据配置保留不删除。")

def process_sub2api_worker(i: int, total: int, item: dict, client: Any, args: Any) -> bool:
    """Sub2API 测活 Worker（使用 Sub2API /test SSE 接口）"""
    if hasattr(args, 'check_stop') and args.check_stop(): return False
    name = item.get("name", "unknown")
    account_id = item.get("id")
    result, reason = client.test_account(account_id)

    if result == "ok":
        print(f"[{ts()}] [SUCCESS] Sub2API测活: {mask_email(name)} 状态健康")
        client.set_account_status(account_id, disabled=False)
        return True

    if result == "quota":
        if cfg.SUB2API_REMOVE_ON_LIMIT_REACHED:
            print(f"[{ts()}] [WARNING] Sub2API测活: {mask_email(name)} 额度耗尽，执行物理删除...")
            if account_id:
                client.delete_account(account_id)
            return False
        print(f"[{ts()}] [WARNING] Sub2API测活: {mask_email(name)} 额度限流，暂不计入有效库存，Sub2API 自动管理")
        return False

    print(f"[{ts()}] [ERROR] Sub2API测活: {mask_email(name)} 测活失败 ({reason})")

    if not cfg.SUB2API_ENABLE_TOKEN_REVIVE:
        print(f"[{ts()}] [ERROR] Token 复活已关闭，直接执行死亡处理")
        _handle_sub2api_dead_account(item, client, is_disabled=False)
        return False

    refresh_token_val = item.get("credentials", {}).get("refresh_token")
    if not refresh_token_val:
        print(f"[{ts()}] [ERROR] {mask_email(name)} 无 refresh_token，执行死亡处理")
        _handle_sub2api_dead_account(item, client, is_disabled=False)
        return False

    print(f"[{ts()}] [INFO] {mask_email(name)} 尝试刷新 Token...")
    proxies = {"http": args.proxy, "https": args.proxy} if args.proxy else None
    ok, new_tokens = refresh_oauth_token(refresh_token_val, proxies=proxies)

    if not ok:
        err_info = new_tokens.get('error', '未知') if isinstance(new_tokens, dict) else str(new_tokens)
        print(f"[{ts()}] [ERROR] {mask_email(name)} Token 刷新失败: {err_info}")
        _handle_sub2api_dead_account(item, client, is_disabled=False)
        return False

    print(f"[{ts()}] [INFO] {mask_email(name)} Token 刷新成功，同步至 Sub2API...")
    item.setdefault("credentials", {}).update(new_tokens)
    up_ok, up_msg = client.update_account(account_id, item)
    if not up_ok:
        print(f"[{ts()}] [ERROR] {mask_email(name)} 更新回 Sub2API 失败: {up_msg}")
        _handle_sub2api_dead_account(item, client, is_disabled=False)
        return False

    print(f"[{ts()}] [INFO] {mask_email(name)} Token 已更新，二次验证中...")
    result2, reason2 = client.test_account(account_id)

    if result2 == "ok":
        print(f"[{ts()}] [SUCCESS] {mask_email(name)} 刷新复活成功，二次验证通过！")
        return True

    print(f"[{ts()}] [ERROR] {mask_email(name)} 二次验证失败 ({reason2})，账号确认已死")
    _handle_sub2api_dead_account(item, client, is_disabled=False)
    return False

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

                def _worker():
                    if stop_event.is_set(): return "stopped"
                    if cfg.is_raw_proxy_pool_enabled():
                        borrowed_generation, p = cfg.unpack_proxy_queue_item(cfg.PROXY_QUEUE.get())
                        try:
                            return run_and_refresh(p, args, False, skip_switch=True)
                        finally:
                            if cfg.should_return_pooled_proxy(borrowed_generation):
                                cfg.PROXY_QUEUE.put(cfg.make_proxy_queue_item(p, borrowed_generation))
                                cfg.PROXY_QUEUE.task_done()
                    if cfg._clash_enable and cfg._clash_pool_mode:
                        p = cfg.PROXY_QUEUE.get()
                        proxy_url = p[-1] if isinstance(p, tuple) else p
                        try:
                            return run_and_refresh(proxy_url, args, False, skip_switch=False)
                        finally:
                            cfg.PROXY_QUEUE.put(p)
                            cfg.PROXY_QUEUE.task_done()
                    return run_and_refresh(args.proxy, args, False, skip_switch=True)

                if executor is not None:
                    futures = [executor.submit(_worker) for _ in range(current_batch)]
                    for f in futures:
                        if f.result() == "success":
                            success_count += 1
                else:
                    with ThreadPoolExecutor(max_workers=current_batch) as ex:
                        futures = [ex.submit(_worker) for _ in range(current_batch)]
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
    codex_files = [
        f for f in all_files
        if "codex" in str(f.get("type", "")).lower()
           or "codex" in str(f.get("provider", "")).lower()
    ]
    total_files = len(codex_files)

    if executor is not None:
        futures = [
            loop.run_in_executor(executor, process_account_worker, i, total_files, item, args)
            for i, item in enumerate(codex_files, 1)
        ]
        results = await asyncio.gather(*futures)
    else:
        with ThreadPoolExecutor(max_workers=cfg.CPA_THREADS) as _ex:
            futures = [
                loop.run_in_executor(_ex, process_account_worker, i, total_files, item, args)
                for i, item in enumerate(codex_files, 1)
            ]
            results = await asyncio.gather(*futures)

    valid_count = sum(1 for r in results if r)
    print(f"[{ts()}] [INFO] CPA 测活结束，当前有效数: {valid_count} / {total_files}")
    return valid_count, total_files


async def perform_sub2api_check(args, async_stop_event, loop, client, executor=None):
    print(f"[{ts()}] [INFO] 开始执行 Sub2API 仓库全量测活巡检...")
    success, account_list = client.get_all_accounts()
    if not success:
        print(f"[{ts()}] [ERROR] 获取 Sub2API 全量库存失败: {account_list}")
        return 0, 0

    total_files = len(account_list)

    if executor is not None:
        futures = [
            loop.run_in_executor(executor, process_sub2api_worker, i, total_files, item, client, args)
            for i, item in enumerate(account_list, 1)
        ]
        results = await asyncio.gather(*futures)
    else:
        with ThreadPoolExecutor(max_workers=cfg.SUB2API_THREADS) as _ex:
            futures = [
                loop.run_in_executor(_ex, process_sub2api_worker, i, total_files, item, client, args)
                for i, item in enumerate(account_list, 1)
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
    else:
        print(f"[{ts()}] [WARNING] 当前未开启 CPA 或 Sub2API 模式，无法执行仓管测活。")

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
            if cfg.CPA_AUTO_CHECK:
                valid_count, total_files = await perform_cpa_check(args, async_stop_event, loop, executor=executor)
            else:
                print(f"\n[{ts()}] [INFO] 自动测活已关闭，直接读取云端列表进行补发判断...")
                res = requests.get(
                    _normalize_cpa_auth_files_url(cfg.CPA_API_URL),
                    headers={"Authorization": f"Bearer {cfg.CPA_API_TOKEN}"},
                    timeout=20,
                )
                all_files = res.json().get("files", [])
                codex_files = [
                    f for f in all_files
                    if "codex" in str(f.get("type", "")).lower()
                       or "codex" in str(f.get("provider", "")).lower()
                ]
                total_files = len(codex_files)
                valid_count = total_files
                print(f"[{ts()}] [INFO] 当前云端总数: {total_files} (未开启自动巡检，默认全部视为有效)")

            if valid_count < cfg.MIN_ACCOUNTS_THRESHOLD:
                need_to_reg          = cfg.BATCH_REG_COUNT
                global run_stats
                run_stats["target"] += need_to_reg
                success_in_this_cycle = 0
                print(f"[{ts()}] [INFO] 库存不足 ({valid_count} < {cfg.MIN_ACCOUNTS_THRESHOLD})，启动补货...")
                await asyncio.sleep(1)

                def _cpa_worker():
                    if async_stop_event.is_set(): return "stopped"
                    if cfg.is_raw_proxy_pool_enabled():
                        borrowed_generation, p = cfg.unpack_proxy_queue_item(cfg.PROXY_QUEUE.get())
                        try:
                            return run_and_refresh(p, args, cpa_upload=True, skip_switch=True)
                        finally:
                            if cfg.should_return_pooled_proxy(borrowed_generation):
                                cfg.PROXY_QUEUE.put(cfg.make_proxy_queue_item(p, borrowed_generation))
                                cfg.PROXY_QUEUE.task_done()
                    if cfg._clash_enable and cfg._clash_pool_mode:
                        p = cfg.PROXY_QUEUE.get()
                        proxy_url = p[-1] if isinstance(p, tuple) else p
                        try:
                            return run_and_refresh(proxy_url, args, cpa_upload=True, skip_switch=False)
                        finally:
                            cfg.PROXY_QUEUE.put(p)
                            cfg.PROXY_QUEUE.task_done()
                    return run_and_refresh(args.proxy, args, cpa_upload=True, skip_switch=True)

                while success_in_this_cycle < need_to_reg and not async_stop_event.is_set() and not cfg.POOL_EXHAUSTED:
                    remaining  = need_to_reg - success_in_this_cycle
                    batch_size = min(cfg.REG_THREADS, remaining)

                    if cfg._clash_enable and not cfg._clash_pool_mode:
                        print(f"[{ts()}] [INFO] [CPA补货] 切换全局节点...")
                        if not smart_switch_node(args.proxy):
                            print(f"[{ts()}] [WARNING] [CPA补货] 全局节点切换失败，使用当前 IP 继续...")

                    if cfg.ENABLE_MULTI_THREAD_REG:
                        print(f"[{ts()}] [INFO] 多线程补货: {success_in_this_cycle}/{need_to_reg} "
                              f"({batch_size} 线程)")
                        if executor is not None:
                            reg_futures = [
                                loop.run_in_executor(executor, _cpa_worker)
                                for _ in range(batch_size)
                            ]
                            reg_results = await asyncio.gather(*reg_futures)
                        else:
                            with ThreadPoolExecutor(max_workers=batch_size) as ex:
                                reg_futures = [
                                    loop.run_in_executor(ex, _cpa_worker)
                                    for _ in range(batch_size)
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
            if cfg.SUB2API_AUTO_CHECK:
                print(f"\n[{ts()}] [INFO] 开始执行 Sub2API 仓库例行巡检与测活...")
                success, account_list = client.get_all_accounts()
                if not success:
                    print(f"[{ts()}] [ERROR] 获取 Sub2API 全量库存失败: {account_list}")
                    try: await asyncio.wait_for(async_stop_event.wait(), timeout=60)
                    except asyncio.TimeoutError: pass
                    continue

                total_files = len(account_list)

                if executor is not None:
                    futures = [
                        loop.run_in_executor(executor, process_sub2api_worker, i, total_files, item, client, args)
                        for i, item in enumerate(account_list, 1)
                    ]
                    results = await asyncio.gather(*futures)
                else:
                    with ThreadPoolExecutor(max_workers=cfg.SUB2API_THREADS) as _ex:
                        futures = [
                            loop.run_in_executor(_ex, process_sub2api_worker, i, total_files, item, client, args)
                            for i, item in enumerate(account_list, 1)
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
                total_files = len(account_list)
                valid_count = total_files
                print(f"[{ts()}] [INFO] 当前云端总数: {total_files} (未开启自动巡检，默认全部视为有效)")

            if valid_count < cfg.SUB2API_MIN_THRESHOLD:
                need_to_reg          = cfg.SUB2API_BATCH_COUNT
                global run_stats
                run_stats["target"] += need_to_reg
                success_in_this_cycle = 0
                print(f"[{ts()}] [INFO] 库存不足 ({valid_count} < {cfg.SUB2API_MIN_THRESHOLD})，启动补货...")
                await asyncio.sleep(1)

                def _sub2api_run_wrapper(p, skip_switch):
                    p = format_docker_url(p)
                    if not skip_switch:
                        if not smart_switch_node(p):
                            print(f"[{ts()}] [WARNING] [Sub2API补货] 全局节点切换失败...")
                    run_ctx = {}
                    result = run(p, run_ctx=run_ctx)
                    status = handle_registration_result(result, cpa_upload=False, run_ctx=run_ctx)

                    if status == "success":
                        token_dict = json.loads(result[0])
                        if hasattr(client, "add_account"):
                            ok, msg = client.add_account(token_dict)
                            if ok: print(f"[{ts()}] [SUCCESS] Sub2API 补货入库成功")
                            else: print(f"[{ts()}] [ERROR] Sub2API 补货入库失败: {msg}")
                    return status

                def _sub2api_worker():
                    if async_stop_event.is_set(): return "stopped"
                    if cfg.is_raw_proxy_pool_enabled():
                        borrowed_generation, p = cfg.unpack_proxy_queue_item(cfg.PROXY_QUEUE.get())
                        try:
                            return _sub2api_run_wrapper(p, True)
                        finally:
                            if cfg.should_return_pooled_proxy(borrowed_generation):
                                cfg.PROXY_QUEUE.put(cfg.make_proxy_queue_item(p, borrowed_generation))
                                cfg.PROXY_QUEUE.task_done()
                    if cfg._clash_enable and cfg._clash_pool_mode:
                        p = cfg.PROXY_QUEUE.get()
                        proxy_url = p[-1] if isinstance(p, tuple) else p
                        try:
                            return _sub2api_run_wrapper(proxy_url, False)
                        finally:
                            cfg.PROXY_QUEUE.put(p)
                            cfg.PROXY_QUEUE.task_done()
                    return _sub2api_run_wrapper(args.proxy, True)

                while success_in_this_cycle < need_to_reg and not async_stop_event.is_set() and not cfg.POOL_EXHAUSTED:
                    remaining  = need_to_reg - success_in_this_cycle
                    batch_size = min(cfg.REG_THREADS, remaining)

                    if cfg._clash_enable and not cfg._clash_pool_mode:
                        print(f"[{ts()}] [INFO] [Sub2API补货] 切换全局节点...")
                        if not smart_switch_node(args.proxy):
                            print(f"[{ts()}] [WARNING] [Sub2API补货] 全局节点切换失败，使用当前 IP 继续...")

                    if cfg.ENABLE_MULTI_THREAD_REG:
                        print(f"[{ts()}] [INFO] 多线程补货: {success_in_this_cycle}/{need_to_reg} "
                              f"({batch_size} 线程)")
                        if executor is not None:
                            reg_futures = [
                                loop.run_in_executor(executor, _sub2api_worker)
                                for _ in range(batch_size)
                            ]
                            reg_results = await asyncio.gather(*reg_futures)
                        else:
                            with ThreadPoolExecutor(max_workers=batch_size) as ex:
                                reg_futures = [
                                    loop.run_in_executor(ex, _sub2api_worker)
                                    for _ in range(batch_size)
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


def main() -> None:
    reload_all_configs()
    parser = argparse.ArgumentParser(description="OpenAI 自动注册 & CPA 检测一体")
    parser.add_argument("--proxy", default=None, help="代理地址")
    # parser.add_argument("--once", action="store_true", help="只运行一次")
    args       = parser.parse_args()
    args.proxy = cfg.DEFAULT_PROXY if cfg.DEFAULT_PROXY.strip() else None

    if cfg.ENABLE_CPA_MODE:
        print("   当前状态: [ CPA 智能仓管模式 ] 已开启")
    else:
        print("   当前状态: [ 常规量产模式 ] 已开启")
    print("=" * 65)

    if cfg.ENABLE_CPA_MODE:
        try:
            asyncio.run(cpa_main_loop(args, asyncio.Event()))
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
            workers = max_workers or max(cfg.REG_THREADS, getattr(cfg, 'CPA_THREADS', 4), getattr(cfg, 'SUB2API_THREADS', 4))
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
        self._shutdown_executor()

    def start_normal(self, args):
        if self.is_running():
            return
        self._force_stopped = False
        cfg.GLOBAL_STOP = False
        cfg.POOL_EXHAUSTED = False
        self.thread_stop_event.clear()
        args.check_stop = lambda: self.thread_stop_event.is_set()
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
        self.thread_stop_event.clear()
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
        self.thread_stop_event.clear()
        self._ensure_executor()
        self.current_thread = threading.Thread(
            target=self._run_sub2api_in_thread, args=(args,), daemon=True
        )
        self.current_thread.start()

    def _run_cpa_in_thread(self, args):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._cpa_wrapper(args))
        finally:
            self._finalize_thread_run()

    def _run_normal_in_thread(self, args):
        try:
            normal_main_loop(args, self.thread_stop_event, executor=self._executor)
        except Exception as e:
            print(f"\n[{ts()}] [CRITICAL] 引擎主线程发生致命崩溃: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self._finalize_thread_run()

    def _run_sub2api_in_thread(self, args):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.async_stop_event = asyncio.Event()
            self.loop.run_until_complete(sub2api_main_loop(args, self.async_stop_event, executor=self._executor))
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
        self.thread_stop_event.clear()
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

if __name__ == "__main__":
    main()
