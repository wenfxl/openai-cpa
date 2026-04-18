import os
import time
import json
import secrets
import re
import asyncio
import traceback
import threading
import sys
import subprocess
import yaml
import urllib.parse
import httpx
from fastapi import APIRouter, Depends, Header, Query, Request, WebSocket, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
from typing import List, Optional, Any
from cloudflare import Cloudflare
from utils import core_engine, db_manager
from utils.config import reload_all_configs
from utils.integrations.sub2api_client import Sub2APIClient
from utils.integrations.tg_notifier import send_tg_msg_async
from utils.email_providers.gmail_oauth_handler import GmailOAuthHandler
from curl_cffi import requests as cffi_requests
from global_state import VALID_TOKENS, CLUSTER_NODES, NODE_COMMANDS, cluster_lock, log_history, engine, verify_token, worker_status
import utils.config as cfg
import utils.integrations.clash_manager as clash_manager
router = APIRouter()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "data", "config.yaml")
GMAIL_CLIENT_SECRETS = os.path.join(BASE_DIR, "data", "credentials.json")
GMAIL_TOKEN_PATH = os.path.join(BASE_DIR, "data", "token.json")
GMAIL_VERIFIER_PATH = os.path.join(BASE_DIR, "data", "temp_verifier.txt")

class DummyArgs:
    def __init__(self, proxy=None, once=False):
        self.proxy = proxy
        self.once = once


class ExportReq(BaseModel): emails: list[str]


class DeleteReq(BaseModel): emails: list[str]


class LoginData(BaseModel): password: str


class CFSyncExistingReq(BaseModel): sub_domains: str; api_email: str; api_key: str


class LuckMailBulkBuyReq(BaseModel): quantity: int; auto_tag: bool; config: dict


class SMSPriceReq(BaseModel): service: str = "openai"


class GmailExchangeReq(BaseModel): code: str


class CloudAccountItem(BaseModel): id: str; type: str


class CloudActionReq(BaseModel): accounts: List[CloudAccountItem]; action: str


class ClusterUploadAccountsReq(BaseModel): node_name: str; secret: str; accounts: list


class ClusterReportReq(BaseModel): node_name: str; secret: str; stats: dict; logs: list


class ClusterControlReq(BaseModel): node_name: str; action: str

class ExtResultReq(BaseModel):
    status: str
    task_id: Optional[str] = ""
    email: Optional[str] = ""
    password: Optional[str] = ""
    error_msg: Optional[str] = ""
    token_data: Optional[str] = ""
    callback_url: Optional[str] = ""
    code_verifier: Optional[str] = ""
    expected_state: Optional[str] = ""
    error_type: Optional[str] = "failed"

class ImportMailboxReq(BaseModel):
    raw_text: str

class DeleteMailboxReq(BaseModel):
    ids: list[Any]

class OutlookAuthUrlReq(BaseModel):
    client_id: str

class OutlookExchangeReq(BaseModel):
    email: str
    client_id: str
    code_or_url: str

class UpdateMailboxStatusReq(BaseModel):
    emails: list[str]
    status: int
# ==========================================
# 辅助函数
# ==========================================

def parse_cpa_usage_to_details(raw_usage: dict) -> dict:
    details = {"is_cpa": True}
    try:
        payload = raw_usage
        if "body" in raw_usage and isinstance(raw_usage["body"], str):
            try:
                payload = json.loads(raw_usage["body"])
            except:
                pass
        details["cpa_plan_type"] = str(payload.get("plan_type", "未知")).upper()
        total = payload.get("total_granted") or payload.get("hard_limit_usd") or payload.get("total")
        used = payload.get("total_used") or payload.get("total_usage") or payload.get("used")
        if total is not None and used is not None:
            total_val = float(total)
            used_val = float(used)
            details["cpa_total"] = f"${total_val:.2f}"
            details["cpa_remaining"] = f"${max(0.0, total_val - used_val):.2f}"
        else:
            details["cpa_total"] = "100%"
            details["cpa_remaining"] = "未知"

        rate_limit = payload.get("rate_limit", {})
        if isinstance(rate_limit, dict):
            primary = rate_limit.get("primary_window", {})
            if primary:
                p_remain = primary.get("remaining_percent")
                if p_remain is None and primary.get("used_percent") is not None:
                    p_remain = 100.0 - float(primary.get("used_percent"))
                details["cpa_primary_remain_pct"] = round(float(p_remain if p_remain is not None else 100.0), 1)

        code_review = payload.get("code_review_rate_limit", {})
        if isinstance(code_review, dict):
            c_primary = code_review.get("primary_window", {})
            if c_primary:
                c_remain = c_primary.get("remaining_percent")
                if c_remain is None and c_primary.get("used_percent") is not None:
                    c_remain = 100.0 - float(c_primary.get("used_percent"))
                details["cpa_codex_remain_pct"] = round(float(c_remain if c_remain is not None else 100.0), 1)

        details["cpa_used_percent"] = round(100.0 - details.get("cpa_primary_remain_pct", 100.0), 1)
        return details
    except Exception as e:
        print(f"[DEBUG] 解析CPA用量异常: {e}")
    details["cpa_total"] = "0.00";
    details["cpa_remaining"] = "0.00";
    details["cpa_used_percent"] = 0.0;
    details["cpa_plan_type"] = "未知"
    return details

@router.get("/")
async def get_dashboard():
    version = "1.0.0"
    js_path = os.path.join(BASE_DIR, "static", "js", "app.js")
    try:
        if os.path.exists(js_path):
            with open(js_path, "r", encoding="utf-8") as f:
                match = re.search(r"appVersion:\s*['\"]([^'\"]+)['\"]", f.read())
                if match: version = match.group(1)
    except Exception:
        pass

    html_path = os.path.join(BASE_DIR, "index.html")
    if not os.path.exists(html_path): return HTMLResponse(content="<h1>找不到 index.html</h1>", status_code=404)

    with open(html_path, "r", encoding="utf-8") as f:
        html_content = f.read()
    return HTMLResponse(content=html_content.replace("__VER__", version),
                        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"})


@router.post("/api/login")
async def login(data: LoginData):
    current_password = getattr(core_engine.cfg, "WEB_PASSWORD", "admin")
    if data.password == current_password:
        token = secrets.token_hex(16)
        VALID_TOKENS.add(token)
        return {"status": "success", "token": token}
    return {"status": "error", "message": "密码错误"}


@router.get("/api/status")
async def get_status(token: str = Depends(verify_token)):
    return {"is_running": engine.is_running()}

@router.post("/api/start")
async def start_task(token: str = Depends(verify_token)):
    if engine.is_running(): return {"status": "error", "message": "任务已经在运行中！"}
    try:
        reload_all_configs()
    except Exception as e:
        print(f"[{core_engine.ts()}] [警告] 启动重载提示: {e}")

    default_proxy = getattr(core_engine.cfg, 'DEFAULT_PROXY', None)
    args = DummyArgs(proxy=default_proxy if default_proxy else None)
    core_engine.run_stats.update({"success": 0, "failed": 0, "retries": 0, "pwd_blocked": 0, "phone_verify": 0, "start_time": time.time()})
    if getattr(core_engine.cfg, 'ENABLE_CPA_MODE', False):
        core_engine.run_stats["target"] = 0
        engine.start_cpa(args)
        return {"status": "success", "message": "启动成功：已自动识别并开启 [CPA 智能仓管模式]"}
    elif getattr(core_engine.cfg, 'ENABLE_SUB2API_MODE', False):
        engine.start_sub2api(args)
        return {"status": "success", "message": "启动成功：已自动识别并开启 [Sub2API 仓管模式]"}
    else:
        core_engine.run_stats["target"] = core_engine.cfg.NORMAL_TARGET_COUNT
        engine.start_normal(args)
        return {"status": "success", "message": "启动成功：已自动识别并开启 [常规量产模式]"}


@router.post("/api/stop")
async def stop_task(token: str = Depends(verify_token)):
    if not engine.is_running(): return {"status": "warning", "message": "当前没有运行的任务"}
    stats = core_engine.run_stats
    elapsed_time = round(time.time() - stats["start_time"], 1) if stats["start_time"] > 0 else 0
    total_attempts = stats["success"] + stats["failed"]
    success_rate = round((stats["success"] / total_attempts * 100), 2) if total_attempts > 0 else 0.0
    avg_time = round(elapsed_time / stats["success"], 1) if stats["success"] > 0 else 0.0
    target_str = stats["target"] if stats["target"] > 0 else "∞"
    template_str = getattr(core_engine.cfg, 'TG_BOT', {}).get("template_stop", "🛑 停止：成功 {success}/{target}")
    pwd_blocked = stats["pwd_blocked"] if stats["pwd_blocked"] > 0 else 0
    phone_blocked = stats["phone_verify"] if stats["phone_verify"] > 0 else 0

    try:
        msg = template_str.format(success_rate=success_rate, success=stats['success'], target=target_str,
                                  failed=stats['failed'], retries=stats['retries'], elapsed_time=elapsed_time,
                                  pwd_blocked=pwd_blocked,phone_verify=phone_blocked,avg_time=avg_time)
    except Exception:
        msg = f"⚠️ TG 模板渲染出错：未知的变量格式。\n请检查配置面板中的模板变量是否正确填写。"

    asyncio.create_task(send_tg_msg_async(msg))
    engine.stop()
    return {"status": "success", "message": "已发送停止指令，正在安全退出..."}


@router.get("/api/stats")
async def get_stats(token: str = Depends(verify_token)):
    stats = core_engine.run_stats
    is_running = engine.is_running()
    current_reg_mode = getattr(core_engine.cfg, 'REG_MODE', 'protocol')

    if current_reg_mode == 'extension':
        is_running = stats.get("ext_is_running", False)
    else:
        is_running = engine.is_running()

    if is_running or (current_reg_mode == 'extension' and stats["start_time"] > 0):
        elapsed = round(time.time() - stats["start_time"], 1) if stats.get("start_time", 0) > 0 else 0
        stats["_frozen_elapsed"] = elapsed
    else:
        elapsed = stats.get("_frozen_elapsed", 0)

    total_attempts = stats["success"] + stats["failed"]
    success_rate = round((stats["success"] / total_attempts * 100), 2) if total_attempts > 0 else 0.0
    avg_time = round(elapsed / stats["success"], 1) if stats["success"] > 0 else 0.0

    progress_pct = 0
    if stats["target"] > 0:
        progress_pct = min(100, round((stats["success"] / stats["target"]) * 100, 1))
    elif stats["success"] > 0:
        progress_pct = 100
    if current_reg_mode == 'extension':
        current_mode = "插件托管 (古法)"
    else:
        current_mode = "CPA 仓管" if getattr(core_engine.cfg, 'ENABLE_CPA_MODE', False) else (
            "Sub2Api 仓管" if getattr(core_engine.cfg, 'ENABLE_SUB2API_MODE', False) else "常规量产")

    return {
        "success": stats["success"], "failed": stats["failed"], "retries": stats["retries"],
        "pwd_blocked": stats.get("pwd_blocked", 0), "phone_verify": stats.get("phone_verify", 0),
        "total": total_attempts, "target": stats["target"] if stats["target"] > 0 else "∞",
        "success_rate": f"{success_rate}%", "elapsed": f"{elapsed}s", "avg_time": f"{avg_time}s",
        "progress_pct": f"{progress_pct}%", "is_running": is_running, "mode": current_mode
    }


@router.post("/api/start_check")
async def start_check_api(token: str = Depends(verify_token)):
    if engine.is_running(): return {"code": 400, "message": "系统正在运行中，请先停止主任务！"}
    default_proxy = getattr(core_engine.cfg, 'DEFAULT_PROXY', None)
    engine.start_check(DummyArgs(proxy=default_proxy if default_proxy else None))
    return {"code": 200, "message": "独立测活指令已下发！"}


@router.post("/api/system/restart")
async def restart_system(token: str = Depends(verify_token)):
    try:
        if engine.is_running(): engine.stop()

        def _do_restart():
            time.sleep(1.5)
            print(f"[{core_engine.ts()}] [系统] 🔄 正在执行重启命令...")
            try:
                sys.stdout.flush()
                sys.stderr.flush()
                subprocess.Popen([sys.executable] + sys.argv)
                os._exit(0)
            except Exception as e:
                print(f"[{core_engine.ts()}] [系统] ❌ 重启失败: {e}")
                os._exit(1)

        threading.Thread(target=_do_restart, daemon=True).start()
        return {"status": "success", "message": "指令已下发，系统即将重启..."}
    except Exception as e:
        return {"status": "error", "message": f"重启异常: {str(e)}"}

def _sanitize_local_microsoft_config(local_ms: Any) -> dict:
    data = dict(local_ms) if isinstance(local_ms, dict) else {}
    data.setdefault("enable_fission", False)
    data.setdefault("pool_fission", False)
    data.setdefault("master_email", "")
    data.setdefault("client_id", "")
    data.setdefault("refresh_token", "")

    mode = str(data.get("suffix_mode", "fixed") or "fixed").strip().lower()
    if mode not in {"fixed", "range", "mystic"}:
        mode = "fixed"

    try:
        min_len = int(data.get("suffix_len_min", 8) or 8)
    except Exception:
        min_len = 8
    try:
        max_len = int(data.get("suffix_len_max", min_len) or min_len)
    except Exception:
        max_len = min_len

    min_len = max(8, min(32, min_len))
    max_len = max(8, min(32, max_len))
    if max_len < min_len:
        max_len = min_len

    data["suffix_mode"] = mode
    data["suffix_len_min"] = min_len
    data["suffix_len_max"] = max_len
    return data


@router.get("/api/config")
async def get_config(token: str = Depends(verify_token)):
    config_data = getattr(core_engine.cfg, '_c', {}).copy()

    if isinstance(config_data.get("sub2api_mode"), dict):
        config_data["sub2api_mode"].pop("min_remaining_weekly_percent", None)
    config_data["web_password"] = getattr(core_engine.cfg, "WEB_PASSWORD", config_data.get("web_password", "admin"))
    config_data["local_microsoft"] = _sanitize_local_microsoft_config(config_data.get("local_microsoft"))
    return config_data


@router.post("/api/config")
async def save_config(new_config: dict, token: str = Depends(verify_token)):
    try:
        if isinstance(new_config.get("sub2api_mode"), dict):
            new_config["sub2api_mode"].pop("min_remaining_weekly_percent", None)
        new_config["local_microsoft"] = _sanitize_local_microsoft_config(new_config.get("local_microsoft"))
        reload_all_configs(new_config_dict=new_config)

        return {"status": "success", "message": "✅ 配置已成功保存并同步至云端！"}
    except Exception as e:
        return {"status": "error", "message": f"❌ 保存失败: {str(e)}"}


@router.post("/api/config/add_wildcard_dns")
async def add_wildcard_dns(req: CFSyncExistingReq, token: str = Depends(verify_token)):
    try:
        main_list = [d.strip() for d in req.sub_domains.split(",") if d.strip()]
        if not main_list: return {"status": "error", "message": "❌ 没有找到有效的主域名"}

        proxy_url = getattr(core_engine.cfg, 'DEFAULT_PROXY', None)
        headers = {"X-Auth-Email": req.api_email, "X-Auth-Key": req.api_key, "Content-Type": "application/json"}
        client_kwargs = {"timeout": 30.0}
        if proxy_url: client_kwargs["proxy"] = proxy_url

        semaphore = asyncio.Semaphore(2)

        async def process_single_domain(client, domain):
            async with semaphore:
                try:
                    zone_resp = await client.get(f"https://api.cloudflare.com/client/v4/zones?name={domain}",
                                                 headers=headers)
                    zone_data = zone_resp.json()
                    if not zone_data.get("success") or not zone_data.get("result"): return False
                    zone_id = zone_data["result"][0]["id"]

                    records = [
                        {"type": "MX", "name": "*", "content": "route3.mx.cloudflare.net", "priority": 36},
                        {"type": "MX", "name": "*", "content": "route2.mx.cloudflare.net", "priority": 25},
                        {"type": "MX", "name": "*", "content": "route1.mx.cloudflare.net", "priority": 51},
                        {"type": "TXT", "name": "*", "content": '"v=spf1 include:_spf.mx.cloudflare.net ~all"'}
                    ]
                    for rec in records:
                        rec_resp = await client.post(
                            f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records", headers=headers,
                            json=rec)
                        rec_data = rec_resp.json()
                        if not rec_data.get("success"):
                            errors = rec_data.get("errors", [])
                            is_quota_exceeded = any(err.get("code") == 81045 for err in errors)
                            is_exist = any(err.get("code") in {81057, 81058} for err in errors)

                            if is_quota_exceeded:
                                print(f"[{core_engine.ts()}] [CF] [{domain}] 记录配额已超出，无法继续创建，请手动去cf官网清除记录后在推送。")
                                continue
                            elif is_exist:
                                print(f"[{core_engine.ts()}] [CF] [{domain}] 记录已存在无需重复创建。")
                                continue

                            print(f"[{core_engine.ts()}] [ERROR] [{domain}] 记录创建报错: {errors}")
                        print(f"[{core_engine.ts()}] [SUCCESS] [{domain}] 创建成功")
                        await asyncio.sleep(0.5)
                    print(f"[{core_engine.ts()}] [CF] ✅ [{domain}] 解析处理成功，防止遗漏，请等待日志输出完毕后，重新点击推送！")
                    return True
                except:
                    return False
                finally:
                    await asyncio.sleep(0.5)

        async with httpx.AsyncClient(**client_kwargs) as client:
            tasks = [process_single_domain(client, dom) for dom in main_list]
            results = await asyncio.gather(*tasks)

        success_count = sum(1 for r in results if r)
        return {"status": "success", "message": f"成功处理 {success_count}/{len(main_list)} 个域名。"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/api/config/cf_global_status")
def get_cf_global_status(main_domain: str, token: str = Depends(verify_token)):
    try:
        cf_cfg = getattr(core_engine.cfg, '_c', {})
        api_email, api_key = cf_cfg.get("cf_api_email"), cf_cfg.get("cf_api_key")
        if not api_email or not api_key: return {"status": "error", "message": "未配置 CF 账号信息"}

        cf = Cloudflare(api_email=api_email, api_key=api_key)
        domains = [d.strip() for d in main_domain.split(",") if d.strip()]
        results = []

        for dom in domains:
            zones = cf.zones.list(name=dom)
            if not zones.result:
                results.append({"domain": dom, "is_enabled": False, "dns_status": "not_found"})
                continue
            zone_id = zones.result[0].id
            routing_info = cf.email_routing.get(zone_id=zone_id)

            def safe_get(obj, attr, default=None):
                val = getattr(obj, attr, None)
                if val is None and hasattr(obj, 'result'): val = getattr(obj.result, attr, None)
                return val if val is not None else default

            raw_status, raw_synced = safe_get(routing_info, 'status', 'unknown'), safe_get(routing_info, 'synced',
                                                                                           False)
            results.append({"domain": dom, "is_enabled": (raw_status == 'ready' and raw_synced is True),
                            "dns_status": "active" if raw_synced else "pending"})

        return {"status": "success", "data": results}
    except Exception as e:
        return {"status": "error", "message": f"状态同步失败: {str(e)}"}

@router.get("/api/accounts")
async def get_accounts(page: int = Query(1), page_size: int = Query(50), token: str = Depends(verify_token)):
    result = db_manager.get_accounts_page(page, page_size)
    return {"status": "success", "data": result["data"], "total": result["total"], "page": page, "page_size": page_size}


@router.post("/api/accounts/export_selected")
async def export_selected_accounts(req: ExportReq, token: str = Depends(verify_token)):
    if not req.emails: return {"status": "error", "message": "未收到任何要导出的账号"}
    tokens = db_manager.get_tokens_by_emails(req.emails)
    return {"status": "success", "data": tokens} if tokens else {"status": "error", "message": "未能提取到选中账号的有效 Token"}


@router.post("/api/accounts/delete")
async def delete_selected_accounts(req: DeleteReq, token: str = Depends(verify_token)):
    if not req.emails: return {"status": "error", "message": "未收到任何要删除的账号"}
    return {"status": "success", "message": f"成功删除 {len(req.emails)} 个账号"} if db_manager.delete_accounts_by_emails(
        req.emails) else {"status": "error", "message": "删除操作失败"}


@router.post("/api/account/action")
def account_action(data: dict, token: str = Depends(verify_token)):
    try:
        email, action = data.get("email"), data.get("action")
        config = getattr(core_engine.cfg, '_c', {})
        token_data = db_manager.get_token_by_email(email)
        if not token_data: return {"status": "error", "message": f"未找到 {email} 的 Token。"}

        if action == "push":
            if not config.get("cpa_mode", {}).get("enable", False): return {"status": "error",
                                                                            "message": "🚫 推送失败：未开启 CPA 模式！"}
            success, msg = core_engine.upload_to_cpa_integrated(token_data,
                                                                config.get("cpa_mode", {}).get("api_url", ""),
                                                                config.get("cpa_mode", {}).get("api_token", ""))
            return {"status": "success", "message": f"账号 {email} 已成功推送到 CPA！"} if success else {"status": "error",
                                                                                                "message": f"CPA 推送失败: {msg}"}

        elif action == "push_sub2api":
            if not getattr(core_engine.cfg, 'ENABLE_SUB2API_MODE', False): return {"status": "error",
                                                                                   "message": "🚫 推送失败：未开启 Sub2API 模式！"}
            proxy_obj = parse_sub2api_proxy(cfg.SUB2API_DEFAULT_PROXY)
            if proxy_obj:
                token_data["sub2api_proxy"] = proxy_obj
            client = Sub2APIClient(api_url=getattr(core_engine.cfg, 'SUB2API_URL', ''),
                                   api_key=getattr(core_engine.cfg, 'SUB2API_KEY', ''))
            success, resp = client.add_account(token_data)
            return {"status": "success", "message": f"账号 {email} 已同步至 Sub2API！"} if success else {"status": "error",
                                                                                                  "message": f"Sub2API 推送失败: {resp}"}
    except Exception as e:
        return {"status": "error", "message": f"后端推送异常: {str(e)}"}


@router.post("/api/accounts/export_sub2api")
async def export_sub2api_accounts(req: ExportReq, token: str = Depends(verify_token)):
    from datetime import datetime, timezone
    try:
        tokens = db_manager.get_tokens_by_emails(req.emails)
        if not tokens: return {"status": "error", "message": "未提取到Token"}

        sub2api_settings = getattr(core_engine.cfg, '_c', {}).get("sub2api_mode", {})
        proxy_obj = parse_sub2api_proxy(cfg.SUB2API_DEFAULT_PROXY)
        accounts_list = []
        for td in tokens:
            acc = {
                "name": str(td.get("email", "unknown"))[:64],
                "platform": "openai", "type": "oauth",
                "credentials": {"refresh_token": td.get("refresh_token", "")},
                "concurrency": int(sub2api_settings.get("account_concurrency", 10)),
                "priority": int(sub2api_settings.get("account_priority", 1)),
                "rate_multiplier": float(sub2api_settings.get("account_rate_multiplier", 1.0)),
                "extra": {"load_factor": int(sub2api_settings.get("account_load_factor", 10))}
            }
            if proxy_obj:
                acc["proxy_key"] = proxy_obj["proxy_key"]
            accounts_list.append(acc)
        return {"status": "success",
                "data": {"exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "proxies": [proxy_obj] if proxy_obj else [],
                         "accounts": accounts_list}}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/api/cloud/accounts")
def get_cloud_accounts(types: str = "sub2api,cpa", page: int = Query(1), page_size: int = Query(50),
                       token: str = Depends(verify_token)):
    type_list = types.split(",")
    combined_data = []
    try:
        if "sub2api" in type_list and getattr(cfg, 'SUB2API_URL', None) and getattr(cfg, 'SUB2API_KEY', None):
            client = Sub2APIClient(api_url=cfg.SUB2API_URL, api_key=cfg.SUB2API_KEY)
            success, raw_sub2_data = client.get_all_accounts()
            if success:
                for item in raw_sub2_data:
                    raw_time = item.get("updated_at", "-")
                    if raw_time != "-":
                        try:
                            raw_time = raw_time.split(".")[0].replace("T", " ")
                        except:
                            pass
                    extra = item.get("extra", {})
                    combined_data.append({
                        "id": str(item.get("id", "")), "account_type": "sub2api",
                        "credential": item.get("name", "未知账号"),
                        "status": "disabled" if item.get("status") == "inactive" else (
                            "active" if item.get("status") == "active" else "dead"),
                        "last_check": raw_time,
                        "details": {"plan_type": item.get("credentials", {}).get("plan_type", "未知"),
                                    "codex_5h_used_percent": extra.get("codex_5h_used_percent", 0),
                                    "codex_7d_used_percent": extra.get("codex_7d_used_percent", 0)}
                    })

        if "cpa" in type_list and getattr(cfg, 'CPA_API_URL', None) and getattr(cfg, 'CPA_API_TOKEN', None):
            from curl_cffi import requests
            res = requests.get(core_engine._normalize_cpa_auth_files_url(cfg.CPA_API_URL),
                               headers={"Authorization": f"Bearer {cfg.CPA_API_TOKEN}"}, timeout=20,
                               impersonate="chrome110")
            if res.status_code == 200:
                for item in [f for f in res.json().get("files", []) if
                             "codex" in str(f.get("type", "")).lower() or "codex" in str(
                                     f.get("provider", "")).lower()]:
                    combined_data.append({"id": item.get("name", ""), "account_type": "cpa",
                                          "credential": item.get("name", "").replace(".json", ""),
                                          "status": "disabled" if item.get("disabled", False) else "active",
                                          "details": {}, "last_check": "-"})

        return {"status": "success", "data": combined_data[(page - 1) * page_size: page * page_size],
                "total": len(combined_data)}
    except Exception as e:
        return {"status": "error", "message": f"拉取远端数据失败: {str(e)}"}


@router.post("/api/cloud/action")
def process_cloud_action(req: CloudActionReq, token: str = Depends(verify_token)):
    from curl_cffi import requests
    from concurrent.futures import ThreadPoolExecutor

    success_count, fail_count, updated_details_map = 0, 0, {}
    sub2api_client = Sub2APIClient(api_url=cfg.SUB2API_URL, api_key=cfg.SUB2API_KEY) if getattr(cfg, 'SUB2API_URL',
                                                                                                None) and getattr(cfg,
                                                                                                                  'SUB2API_KEY',
                                                                                                                  None) else None

    cpa_files_map = {}
    if any(a.type == "cpa" for a in req.accounts) and req.action == "check" and getattr(cfg, 'CPA_API_URL', None):
        try:
            res = requests.get(core_engine._normalize_cpa_auth_files_url(cfg.CPA_API_URL),
                               headers={"Authorization": f"Bearer {cfg.CPA_API_TOKEN}"}, timeout=15,
                               impersonate="chrome110")
            if res.status_code == 200: cpa_files_map = {f.get("name"): f for f in res.json().get("files", [])}
        except:
            pass

    def _worker(acc: CloudAccountItem):
        is_success, details = False, None
        try:
            if acc.type == "sub2api" and sub2api_client:
                if req.action == "check":
                    result, _ = sub2api_client.test_account(acc.id)
                    is_success = (result == "ok")
                    if not is_success: sub2api_client.set_account_status(acc.id, disabled=True)
                elif req.action in ["enable", "disable"]:
                    is_success = sub2api_client.set_account_status(acc.id, disabled=(req.action == "disable"))
                elif req.action == "delete":
                    is_success, _ = sub2api_client.delete_account(acc.id)

            elif acc.type == "cpa" and getattr(cfg, 'CPA_API_URL', None):
                if req.action == "check":
                    item = cpa_files_map.get(acc.id, {"name": acc.id, "disabled": False})
                    is_success, _ = core_engine.test_cliproxy_auth_file(item, cfg.CPA_API_URL, cfg.CPA_API_TOKEN)
                    if '_raw_usage' in item: details = parse_cpa_usage_to_details(item['_raw_usage'])
                    if not is_success: core_engine.set_cpa_auth_file_status(cfg.CPA_API_URL, cfg.CPA_API_TOKEN, acc.id,
                                                                            disabled=True)
                elif req.action in ["enable", "disable"]:
                    is_success = core_engine.set_cpa_auth_file_status(cfg.CPA_API_URL, cfg.CPA_API_TOKEN, acc.id,
                                                                      disabled=(req.action == "disable"))
                elif req.action == "delete":
                    is_success = requests.delete(core_engine._normalize_cpa_auth_files_url(cfg.CPA_API_URL),
                                                 headers={"Authorization": f"Bearer {cfg.CPA_API_TOKEN}"},
                                                 params={"name": acc.id}, impersonate="chrome110").status_code in (
                                 200, 204)
        except:
            pass
        return (is_success, acc.id, details)

    target_threads = 5
    if any(a.type == "cpa" for a in req.accounts): target_threads = max(target_threads, int(
        getattr(cfg, '_c', {}).get('cpa_mode', {}).get('threads', 10)))
    if any(a.type == "sub2api" for a in req.accounts): target_threads = max(target_threads, int(
        getattr(cfg, '_c', {}).get('sub2api_mode', {}).get('threads', 10)))

    with ThreadPoolExecutor(max_workers=max(1, min(target_threads, 50))) as executor:
        for is_success, acc_id, details in executor.map(_worker, req.accounts):
            if is_success:
                success_count += 1
            else:
                fail_count += 1
            if details: updated_details_map[acc_id] = details

    msg = f"测活完毕 | 存活: {success_count} 个 | 失效并已自动禁用: {fail_count} 个" if req.action == "check" else f"指令已下发 | 成功: {success_count} 个 | 失败: {fail_count} 个"
    return {"status": "success" if fail_count == 0 else "warning", "message": msg,
            "updated_details": updated_details_map}

@router.get('/api/sms/balance')
def api_get_sms_balance(token: str = Depends(verify_token)):
    from utils.integrations.hero_sms import hero_sms_get_balance
    proxy_url = core_engine.cfg.DEFAULT_PROXY
    balance, err = hero_sms_get_balance(proxies={"http": proxy_url, "https": proxy_url} if proxy_url else None)
    return {"status": "success", "balance": f"{balance:.2f}"} if balance >= 0 else {"status": "error", "message": err}


@router.post('/api/sms/prices')
def api_get_sms_prices(req: SMSPriceReq, token: str = Depends(verify_token)):
    from utils.integrations.hero_sms import _hero_sms_prices_by_service
    proxy_url = core_engine.cfg.DEFAULT_PROXY
    rows = _hero_sms_prices_by_service(req.service,
                                       proxies={"http": proxy_url, "https": proxy_url} if proxy_url else None)
    return {"status": "success", "prices": rows} if rows else {"status": "error", "message": "无法获取价格或当前服务无库存"}


@router.post("/api/luckmail/bulk_buy")
def api_luckmail_bulk_buy(req: LuckMailBulkBuyReq, token: str = Depends(verify_token)):
    try:
        from utils.email_providers.luckmail_service import LuckMailService
        lm_service = LuckMailService(api_key=req.config.get("api_key"),
                                     preferred_domain=req.config.get("preferred_domain", ""),
                                     email_type=req.config.get("email_type", "ms_graph"),
                                     variant_mode=req.config.get("variant_mode", ""))
        tag_id = req.config.get("tag_id") or lm_service.get_or_create_tag_id("已使用")
        results = lm_service.bulk_purchase(quantity=req.quantity, auto_tag=req.auto_tag, tag_id=tag_id)
        return {"status": "success", "message": f"成功购买 {len(results)} 个邮箱！", "data": results}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/api/gmail/auth_url")
async def get_gmail_auth_url(token: str = Depends(verify_token)):
    if not os.path.exists(GMAIL_CLIENT_SECRETS): return {"status": "error",
                                                         "message": f"❌ 未找到凭证文件！请上传至: {GMAIL_CLIENT_SECRETS}"}
    try:
        url, verifier = GmailOAuthHandler.get_authorization_url(GMAIL_CLIENT_SECRETS)
        with open(GMAIL_VERIFIER_PATH, "w") as f:
            f.write(verifier)
        return {"status": "success", "url": url}
    except Exception as e:
        return {"status": "error", "message": f"生成链接失败: {str(e)}"}


@router.post("/api/gmail/exchange_code")
async def exchange_gmail_code(req: GmailExchangeReq, token: str = Depends(verify_token)):
    if not req.code: return {"status": "error", "message": "授权码不能为空"}
    try:
        if not os.path.exists(GMAIL_VERIFIER_PATH): return {"status": "error", "message": "会话已过期，请重新生成链接"}
        with open(GMAIL_VERIFIER_PATH, "r") as f:
            stored_verifier = f.read().strip()
        success, msg = GmailOAuthHandler.save_token_from_code(GMAIL_CLIENT_SECRETS, req.code, GMAIL_TOKEN_PATH,
                                                              code_verifier=stored_verifier,
                                                              proxy=getattr(core_engine.cfg, 'DEFAULT_PROXY', None))
        if success and os.path.exists(GMAIL_VERIFIER_PATH):
            os.remove(GMAIL_VERIFIER_PATH)
            return {"status": "success", "message": "✨ 授权成功！token.json 已保存在 data 目录。"}
        return {"status": "error", "message": msg}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/api/sub2api/groups")
def get_sub2api_groups(token: str = Depends(verify_token)):
    from curl_cffi import requests as cffi_requests
    sub2api_url = getattr(core_engine.cfg, "SUB2API_URL", "").strip()
    sub2api_key = getattr(core_engine.cfg, "SUB2API_KEY", "").strip()
    if not sub2api_url or not sub2api_key: return {"status": "error",
                                                   "message": "Please save the Sub2API URL and API key first."}
    try:
        response = cffi_requests.get(f"{sub2api_url.rstrip('/')}/api/v1/admin/groups/all",
                                     headers={"x-api-key": sub2api_key, "Content-Type": "application/json"}, timeout=10,
                                     impersonate="chrome110")
        if response.status_code != 200: return {"status": "error",
                                                "message": f"HTTP {response.status_code}: {response.text[:200]}"}
        return {"status": "success", "data": response.json().get("data", [])}
    except Exception as exc:
        return {"status": "error", "message": f"Failed to fetch Sub2API groups: {exc}"}


@router.get("/api/system/check_update")
async def check_update(current_version: str, token: str = Depends(verify_token)):
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get("https://api.github.com/repos/wenfxl/openai-cpa/releases/latest",
                                    headers={"Accept": "application/vnd.github.v3+json"})
            if resp.status_code != 200: return {"status": "error",
                                                "message": f"无法获取更新数据 (GitHub API 返回 HTTP {resp.status_code})"}
        data = resp.json()
        remote_version = data.get("tag_name", "")

        def _parse(v):
            return [int(x) for x in re.findall(r'\d+', str(v))]

        has_update = _parse(remote_version) > _parse(current_version) if remote_version else False
        assets = data.get("assets")
        download_url = assets[0].get("browser_download_url", "") if assets else data.get("zipball_url", "")
        return {"status": "success", "has_update": has_update, "remote_version": remote_version,
                "changelog": data.get("body", "无更新日志"), "download_url": download_url,
                "html_url": data.get("html_url", "")}
    except Exception as e:
        return {"status": "error", "message": f"检查更新发生未知异常: {str(e)}"}

@router.post("/api/logs/clear")
async def clear_backend_logs(token: str = Depends(verify_token)):
    log_history.clear()
    return {"status": "success"}


@router.get("/api/logs/stream")
async def stream_logs(request: Request, token: str = Query(None)):
    if token not in VALID_TOKENS: raise HTTPException(status_code=401, detail="Unauthorized")

    async def log_generator():
        current_snapshot = list(log_history)
        for old_msg in current_snapshot:
            yield f"data: {old_msg}\n\n"
        last_sent_msg = current_snapshot[-1] if current_snapshot else None
        idle_loops = 0

        try:
            while True:
                if await request.is_disconnected():
                    break
                snap = list(log_history)
                if snap and snap[-1] != last_sent_msg:
                    start_idx = 0
                    for i in range(len(snap) - 1, -1, -1):
                        if snap[i] == last_sent_msg:
                            start_idx = i + 1
                            break
                    for i in range(start_idx, len(snap)):
                        yield f"data: {snap[i]}\n\n"
                    last_sent_msg = snap[-1]
                    idle_loops = 0
                else:
                    idle_loops += 1
                    if idle_loops >= 50:
                        yield ": keepalive\n\n"
                        idle_loops = 0

                await asyncio.sleep(0.3)
        except Exception:
            pass

    return StreamingResponse(log_generator(), media_type="text/event-stream")

@router.post("/api/cluster/control")
async def cluster_control(req: ClusterControlReq, token: str = Depends(verify_token)):
    if req.action not in ["start", "stop", "restart", "export_accounts"]: return {"status": "error",
                                                                                  "message": "不支持的指令"}
    with cluster_lock: NODE_COMMANDS[req.node_name] = req.action
    return {"status": "success", "message": f"指令 [{req.action}] 已排队"}


@router.get("/api/cluster/view")
async def cluster_view(token: str = Depends(verify_token)):
    global CLUSTER_NODES
    now = time.time()
    with cluster_lock:
        CLUSTER_NODES = {k: v for k, v in CLUSTER_NODES.items() if now - v["last_seen"] < 20}
        return {"status": "success", "nodes": CLUSTER_NODES}


@router.post("/api/cluster/report")
async def cluster_report(req: ClusterReportReq):
    cf_dict = getattr(core_engine.cfg, '_c', {})
    if req.secret != str(cf_dict.get("cluster_secret", "wenfxl666")).strip(): return {"status": "error",
                                                                                      "message": "密钥错误"}

    target_cmd = NODE_COMMANDS.get(req.node_name, "none")
    node_is_running = req.stats.get("is_running", False)

    if target_cmd in ["restart", "export_accounts"]:
        NODE_COMMANDS[req.node_name] = "none"
    elif (target_cmd == "start" and node_is_running) or (target_cmd == "stop" and not node_is_running):
        NODE_COMMANDS[req.node_name] = "none"
        target_cmd = "none"

    with cluster_lock:
        CLUSTER_NODES[req.node_name] = {
            "stats": req.stats, "logs": req.logs, "last_seen": time.time(),
            "join_time": CLUSTER_NODES.get(req.node_name, {}).get("join_time", time.time())
        }
    return {"status": "success", "command": target_cmd}


@router.websocket("/api/cluster/report_ws")
async def ws_cluster_report(websocket: WebSocket, node_name: str, secret: str):
    await websocket.accept()
    if secret != str(getattr(core_engine.cfg, '_c', {}).get("cluster_secret", "wenfxl666")).strip():
        await websocket.close(code=1008, reason="Secret Mismatch")
        return
    try:
        while True:
            data = await websocket.receive_json()
            target_cmd = NODE_COMMANDS.get(node_name, "none")
            node_is_running = data.get("stats", {}).get("is_running", False)
            if target_cmd in ["restart", "export_accounts"]:
                NODE_COMMANDS[node_name] = "none"
            elif (target_cmd == "start" and node_is_running) or (target_cmd == "stop" and not node_is_running):
                NODE_COMMANDS[node_name] = "none"
                target_cmd = "none"
            with cluster_lock:
                CLUSTER_NODES[node_name] = {
                    "stats": data.get("stats", {}), "logs": data.get("logs", []), "last_seen": time.time(),
                    "join_time": CLUSTER_NODES.get(node_name, {}).get("join_time", time.time())
                }
            await websocket.send_json({"command": target_cmd})
    except Exception:
        pass


@router.websocket("/api/cluster/view_ws")
async def cluster_view_ws(websocket: WebSocket, token: str = Query(None)):
    if token not in VALID_TOKENS:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    try:
        while True:
            global CLUSTER_NODES
            now = time.time()
            with cluster_lock:
                CLUSTER_NODES = {k: v for k, v in CLUSTER_NODES.items() if now - v["last_seen"] < 20}
                await websocket.send_json({"status": "success", "nodes": CLUSTER_NODES})
            await asyncio.sleep(0.5)
    except Exception:
        pass

@router.post("/api/cluster/upload_accounts")
def cluster_upload_accounts(req: ClusterUploadAccountsReq):
    if req.secret != str(getattr(core_engine.cfg, '_c', {}).get("cluster_secret", "wenfxl666")).strip(): return {
        "status": "error", "message": "密钥错误"}
    success_count = 0
    for acc in req.accounts:
        if acc.get("email") and acc.get("token_data"):
            if db_manager.save_account_to_db(acc.get("email"), acc.get("password"),
                                             acc.get("token_data")): success_count += 1

    msg = f"[{core_engine.ts()}] [系统] 📦 成功从子控 [{req.node_name}] 提取并完美入库 {success_count} 个账号！"
    print(msg)
    try:
        log_history.append(msg)
    except:
        pass
    return {"status": "success", "message": f"成功接收 {success_count} 个账号"}

#模式二注册
@router.get("/api/ext/generate_task")
def ext_generate_task(token: str = Depends(verify_token)):
    from utils.email_providers.mail_service import mask_email, get_email_and_token, clear_sticky_domain
    from utils.register import _generate_password, generate_random_user_info, generate_oauth_url
    import utils.config as cfg
    import time
    print(f"[{cfg.ts()}] [INFO] 正在进行插件古法注册模式，请稍后...")
    try:
        cfg.GLOBAL_STOP = False
        clear_sticky_domain()

        email = None
        email_jwt = None
        for attempt in range(3):
            print(f"[{cfg.ts()}] [INFO] 正在进行邮箱创建...")
            email, email_jwt = get_email_and_token(proxies=None)
            if email:
                break
            time.sleep(1.5)

        if not email:
            return {"status": "error", "message": "邮箱获取超时或暂无库存，请稍候"}

        user_info = generate_random_user_info()
        password = _generate_password()

        oauth_reg = generate_oauth_url()

        print(f"[{cfg.ts()}] [INFO] （{mask_email(email)}）下发任务数据 (昵称: {user_info['name']}) (密码: {password}) (生日: {user_info['birthdate']})...")

        name_parts = user_info['name'].split(' ')
        return {
            "status": "success",
            "task_data": {
                "email": email,
                "email_jwt": email_jwt,
                "password": password,
                "firstName": name_parts[0] if len(name_parts) > 0 else "John",
                "lastName": name_parts[1] if len(name_parts) > 1 else "Doe",
                "birthday": user_info['birthdate'],
                "registerUrl": oauth_reg.auth_url,
                "code_verifier": oauth_reg.code_verifier,
                "expected_state": oauth_reg.state
            }
        }
    except Exception as e:
        return {"status": "error", "message": f"任务生成失败: {str(e)}"}

@router.get("/api/ext/get_mail_code")
def ext_get_mail_code(email: str, email_jwt: str = "", type: str = "signup", max_attempts: int = 20, token: str = Depends(verify_token)):
    from utils.email_providers.mail_service import get_oai_code
    try:
        code = get_oai_code(email, jwt=email_jwt, proxies=None, max_attempts=max_attempts)
        if code:
            return {"status": "success", "code": code}
        return {"status": "pending"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/api/ext/submit_result")
def ext_submit_result(req: ExtResultReq, token: str = Depends(verify_token)):
    from utils import core_engine
    from utils.register import submit_callback_url

    if req.status == "success":
        token_json = req.token_data
        if not token_json and req.callback_url:
            try:
                token_json = submit_callback_url(
                    callback_url=req.callback_url,
                    expected_state=req.expected_state,
                    code_verifier=req.code_verifier
                )
            except Exception as e:
                print(f"换取 Token 失败: {e}")
                return {"status": "error", "message": "Token 换取失败"}
        db_manager.save_account_to_db(req.email, req.password, token_json)
        core_engine.run_stats['success'] = core_engine.run_stats.get('success', 0) + 1

        return {"status": "success", "message": "战利品已入库"}
    else:
        core_engine.run_stats['failed'] = core_engine.run_stats.get('failed', 0) + 1
        is_dead_account = False
        if req.error_type == 'phone_verify':
            core_engine.run_stats['phone_verify'] = core_engine.run_stats.get('phone_verify', 0) + 1
            is_dead_account = True
        elif req.error_type == 'pwd_blocked':
            core_engine.run_stats['pwd_blocked'] = core_engine.run_stats.get('pwd_blocked', 0) + 1
        if is_dead_account and getattr(cfg, "EMAIL_API_MODE", "") == "local_microsoft" and req.email:
            db_manager.update_local_mailbox_status(req.email, 3)
            print(f"[{cfg.ts()}] [WARNING] 插件上报邮箱不可用，已将邮箱标记为死号: {req.email}")
        return {"status": "success", "message": "异常统计已录入看板"}


@router.post("/api/ext/heartbeat")
def ext_heartbeat(worker_id: str, token: str = Depends(verify_token)):
    worker_status[worker_id] = time.time()
    return {"status": "success", "message": "ok"}


@router.get("/api/ext/check_node")
def check_node_status(worker_id: str, token: str = Depends(verify_token)):
    last_seen = worker_status.get(worker_id)
    if not last_seen:
        return {"status": "success", "online": False, "reason": "never_connected"}
    is_online = (time.time() - last_seen) < 15
    return {
        "status": "success",
        "online": is_online,
        "last_seen": last_seen
    }

@router.post("/api/ext/reset_stats")
def ext_reset_stats(token: str = Depends(verify_token)):
    from utils import core_engine
    import time
    core_engine.run_stats.update({
        "success": 0, "failed": 0, "retries": 0,
        "pwd_blocked": 0, "phone_verify": 0,
        "start_time": time.time(),
        "target": getattr(core_engine.cfg, 'NORMAL_TARGET_COUNT', 0),
        "ext_is_running": True
    })
    return {"status": "success"}

@router.post("/api/ext/stop")
def ext_stop(token: str = Depends(verify_token)):
    from utils import core_engine
    core_engine.run_stats["ext_is_running"] = False
    return {"status": "success"}

@router.get("/api/mailboxes")
async def get_mailboxes(page: int = Query(1), page_size: int = Query(50), token: str = Depends(verify_token)):
    result = db_manager.get_local_mailboxes_page(page, page_size)
    return {"status": "success", "data": result["data"], "total": result["total"], "page": page, "page_size": page_size}


@router.post("/api/mailboxes/import")
async def import_mailboxes(req: ImportMailboxReq, token: str = Depends(verify_token)):
    if not req.raw_text: return {"status": "error", "message": "内容为空"}

    parsed_mailboxes = []
    lines = req.raw_text.strip().split("\n")
    for line in lines:
        text = line.strip()
        if not text or text.startswith("#"): continue
        parts = [p.strip() for p in text.split("----")]
        if len(parts) >= 2 and "@" in parts[0]:
            parsed_mailboxes.append({
                "email": parts[0],
                "password": parts[1],
                "client_id": parts[2] if len(parts) >= 3 else "",
                "refresh_token": parts[3] if len(parts) >= 4 else ""
            })

    if not parsed_mailboxes: return {"status": "error", "message": "未能识别出有效数据"}
    count = db_manager.import_local_mailboxes(parsed_mailboxes)
    return {"status": "success", "count": count}


@router.post("/api/mailboxes/delete")
async def delete_mailboxes(req: DeleteMailboxReq, token: str = Depends(verify_token)):
    if not req.ids: return {"status": "error", "message": "未收到任何要删除的ID"}
    if db_manager.delete_local_mailboxes(req.ids):
        return {"status": "success", "message": "删除成功"}
    return {"status": "error", "message": "删除操作失败"}


@router.post("/api/mailboxes/oauth_url")
async def get_outlook_oauth_url(req: OutlookAuthUrlReq, token: str = Depends(verify_token)):
    if not req.client_id:
        return {"status": "error", "message": "缺少 Client ID"}

    redirect_uri = "http://localhost"
    scope_str = urllib.parse.quote("offline_access https://graph.microsoft.com/Mail.Read")
    auth_url = (
        f"https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
        f"?client_id={req.client_id}"
        f"&response_type=code"
        f"&redirect_uri={urllib.parse.quote(redirect_uri)}"
        f"&response_mode=query"
        f"&scope={scope_str}"
    )
    return {"status": "success", "url": auth_url}


@router.post("/api/mailboxes/oauth_exchange")
async def exchange_outlook_oauth_code(req: OutlookExchangeReq, token: str = Depends(verify_token)):
    try:
        auth_code = req.code_or_url.strip()
        if "http" in auth_code or "code=" in auth_code:
            parsed_url = urllib.parse.urlparse(auth_code)
            query_params = urllib.parse.parse_qs(parsed_url.query)
            extracted = query_params.get("code", [None])[0]
            if extracted:
                auth_code = extracted
            else:
                return {"status": "error", "message": "无法从网址中提取 code 参数，请确保复制了完整的网址。"}

        token_url = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
        payload = {
            "client_id": req.client_id,
            "grant_type": "authorization_code",
            "code": auth_code,
            "redirect_uri": "http://localhost"
        }

        proxy_url = getattr(cfg, 'DEFAULT_PROXY', None)
        proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None

        response = cffi_requests.post(token_url, data=payload, proxies=proxies, timeout=15, impersonate="chrome110")
        data = response.json()

        if response.status_code == 200:
            refresh_token = data.get("refresh_token")
            import sqlite3
            from utils.db_manager import get_db_conn, get_cursor, execute_sql
            try:
                with get_db_conn() as conn:
                    c = get_cursor(conn)
                    execute_sql(c,
                                "UPDATE local_mailboxes SET client_id = ?, refresh_token = ?, status = 0 WHERE email = ?",
                                (req.client_id, refresh_token, req.email)
                                )
            except Exception as e:
                print(f"[ERROR] 数据库更新 OAuth Token 失败: {e}")

            return {"status": "success", "message": f"授权成功！已为 {req.email} 绑定永久 Token。", "refresh_token": refresh_token}
        else:
            return {"status": "error", "message": f"获取失败: {data.get('error_description', data)}"}

    except Exception as e:
        return {"status": "error", "message": f"处理异常: {str(e)}"}


@router.post("/api/mailboxes/update_status")
async def update_mailboxes_status(req: UpdateMailboxStatusReq, token: str = Depends(verify_token)):
    if not req.emails:
        return {"status": "error", "message": "未收到任何邮箱"}

    success_count = 0
    for email in req.emails:
        try:
            db_manager.update_local_mailbox_status(email, req.status)
            db_manager.clear_retry_master_status(email)
            success_count += 1
        except Exception as e:
            pass

    return {"status": "success", "message": f"成功将 {success_count} 个邮箱状态重置！"}

class ClashDeployReq(BaseModel):
    count: int

class ClashUpdateReq(BaseModel):
    sub_url: str
    target: str = "all"

@router.get("/api/clash/status")
async def get_clash_status(token: str = Depends(verify_token)):
    res = clash_manager.get_pool_status()
    if "error" in res:
        return {"status": "error", "message": res["error"]}
    return {"status": "success", "data": res}

@router.post("/api/clash/deploy")
async def post_clash_deploy(req: ClashDeployReq, token: str = Depends(verify_token)):
    success, msg = clash_manager.deploy_clash_pool(req.count)
    return {"status": "success" if success else "error", "message": msg}

@router.post("/api/clash/update")
async def post_clash_update(req: ClashUpdateReq, token: str = Depends(verify_token)):
    success, msg = clash_manager.patch_and_update(req.sub_url, req.target)
    return {"status": "success" if success else "error", "message": msg}


def parse_sub2api_proxy(proxy_url: str):
    """提取代理URL为Sub2API所需格式"""
    if not proxy_url:
        return None
    try:
        from urllib.parse import urlparse
        parsed = urlparse(proxy_url)
        protocol = parsed.scheme
        host = parsed.hostname
        port = parsed.port
        username = parsed.username or ""
        password = parsed.password or ""

        if not protocol or not host or not port:
            return None

        proxy_key = f"{protocol}|{host}|{port}|{username}|{password}"
        proxy_dict = {
            "proxy_key": proxy_key,
            "name": "openai-cpa",
            "protocol": protocol,
            "host": host,
            "port": port,
            "status": "active"
        }
        if username and password:
            proxy_dict["username"] = username
            proxy_dict["password"] = password

        return proxy_dict
    except:
        return None
@router.post("/api/accounts/export_all")
async def export_all_accounts(token: str = Depends(verify_token)):
    data = db_manager.get_all_accounts_raw()
    return {"status": "success", "data": data}

@router.post("/api/accounts/clear_all")
async def clear_all_accounts_api(token: str = Depends(verify_token)):
    if db_manager.clear_all_accounts():
        return {"status": "success", "message": "账号库已全部清空"}
    return {"status": "error", "message": "清空失败"}

@router.post("/api/mailboxes/export_all")
async def export_all_mailboxes(token: str = Depends(verify_token)):
    data = db_manager.get_all_mailboxes_raw()
    return {"status": "success", "data": data}

@router.post("/api/mailboxes/clear_all")
async def clear_all_mailboxes_api(token: str = Depends(verify_token)):
    if db_manager.clear_all_mailboxes():
        return {"status": "success", "message": "邮箱库已全部清空"}
    return {"status": "error", "message": "清空失败"}
