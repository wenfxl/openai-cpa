import json
import time
import datetime
import urllib.parse
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import List, Any, Optional, Union
from fastapi import APIRouter, Depends, Query, BackgroundTasks
from pydantic import BaseModel
from curl_cffi import requests as cffi_requests
from global_state import verify_token
from utils import core_engine, db_manager
import utils.config as cfg
from utils.integrations.sub2api_client import Sub2APIClient, build_sub2api_export_bundle, get_sub2api_push_settings
from utils.integrations.image2api_client import Image2APIClient
from utils.auth_core import email_jwt


router = APIRouter()

class ExportReq(BaseModel): emails: list[str]
class DeleteReq(BaseModel): emails: list[str]
class CloudAccountItem(BaseModel): id: str; type: str; platform: Optional[str] = None
class CloudActionReq(BaseModel): accounts: List[CloudAccountItem]; action: str
class ImportMailboxReq(BaseModel): raw_text: str
class DeleteMailboxReq(BaseModel): ids: list[Any]
class OutlookAuthUrlReq(BaseModel): client_id: str
class OutlookExchangeReq(BaseModel): email: str; client_id: str; code_or_url: str
class UpdateMailboxStatusReq(BaseModel): emails: list[str]; status: int
class PushReq(BaseModel):emails: list[str]; platform: str
class BulkRefreshReq(BaseModel): emails: list[str]
class Image2APIStatusReq(BaseModel): access_token: str; status: str; type: str = "Free"; quota: int = 25;
class Image2APIRefreshReq(BaseModel):tokens: list[str]
class ImportTeamReq(BaseModel): raw_text: str
class DeleteTeamReq(BaseModel): ids: list[int]
class ResetAuthReq(BaseModel):clear_license: bool = False; clear_hwid: bool = False; clear_lease: bool = False;
class LicenseUploadReq(BaseModel):content: str
class UpgradeOAuthReq(BaseModel):emails: Union[List[str], str]


_last_cloud_sync_time = 0


def _grok2api_base_url() -> str:
    return (getattr(cfg, "GROK2API_URL", None) or "http://host.docker.internal:8000").rstrip("/")


def _grok2api_login() -> tuple[bool, str, str]:
    """Login to Grok2API admin API. Returns (ok, token, message)."""
    grok_pass = getattr(cfg, "GROK2API_ADMIN_PASSWORD", None) or ""
    if not grok_pass:
        return False, "", "Grok2API 未配置 admin_password（config.yaml → grok2api.admin_password）"
    try:
        resp = cffi_requests.post(
            f"{_grok2api_base_url()}/api/admin/v1/auth/login",
            json={"username": "admin", "password": grok_pass},
            timeout=30,
            impersonate="chrome110",
        )
        if resp.status_code != 200:
            return False, "", f"Grok2API 登录失败 HTTP {resp.status_code}"
        token_value = resp.json().get("data", {}).get("tokens", {}).get("accessToken", "")
        if not token_value:
            return False, "", "Grok2API 登录后未获取到 accessToken"
        return True, token_value, "OK"
    except Exception as exc:
        return False, "", f"Grok2API 登录异常: {exc}"


def _grok2api_request(method: str, path: str, token_value: str, **kwargs):
    url = f"{_grok2api_base_url()}/api/admin/v1{path}"
    headers = kwargs.pop("headers", {}) or {}
    headers["Authorization"] = f"Bearer {token_value}"
    return cffi_requests.request(method, url, headers=headers, timeout=kwargs.pop("timeout", 60), impersonate="chrome110", **kwargs)


def _grok2api_list_accounts(token_value: str, provider: str | None = None, page_size: int = 500) -> tuple[bool, list[dict], str]:
    items: list[dict] = []
    page = 1
    total = None
    try:
        while True:
            params = {"page": page, "pageSize": page_size}
            if provider:
                params["provider"] = provider
            resp = _grok2api_request("GET", "/accounts", token_value, params=params, timeout=30)
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


def _grok2api_quota_details(item: dict) -> dict:
    details = {
        "platform": item.get("provider") or "grok2api",
        "type": item.get("provider") or "grok2api",
        "auth_status": item.get("authStatus", ""),
        "egress_node_id": item.get("egressNodeId", ""),
        "refreshable": item.get("refreshable", False),
    }
    quota = item.get("quota") or {}
    if quota:
        used = quota.get("used") or 0
        limit = quota.get("limit") or 0
        remaining = quota.get("remaining") if quota.get("remaining") is not None else max(0, limit - used)
        usage = quota.get("usagePercent")
        try:
            usage = round(float(usage), 1) if usage is not None else (round(float(used) * 100 / float(limit), 1) if limit else 0)
        except Exception:
            usage = 0
        details.update({
            "quota_type": quota.get("type") or "unknown",
            "quota_source": quota.get("source") or "",
            "quota_status": quota.get("status") or "",
            "quota_used": used,
            "quota_limit": limit,
            "quota_remaining": remaining,
            "quota_usage_percent": usage,
            "quota_window_hours": quota.get("windowHours"),
        })
    billing = item.get("billing") or {}
    if billing:
        details.update({
            "billing_used": billing.get("used"),
            "billing_remaining": billing.get("remaining"),
            "billing_synced_at": billing.get("syncedAt"),
        })
    windows = item.get("quotaWindows") or []
    if windows:
        details["quota_windows"] = windows
    return details


def _grok2api_to_cloud_item(item: dict) -> dict:
    status = "active" if item.get("enabled", True) else "disabled"
    if item.get("enabled", True) and str(item.get("authStatus", "active")).lower() not in {"active", "ok", "valid", ""}:
        status = "dead"
    last_check = item.get("quota", {}).get("syncedAt") or item.get("billing", {}).get("syncedAt") or item.get("updatedAt") or item.get("createdAt") or "-"
    return {
        "id": str(item.get("id", "")),
        "account_type": "grok2api",
        "credential": item.get("email") or item.get("name") or str(item.get("id", "")),
        "status": status,
        "last_check": str(last_check).split(".")[0].replace("T", " ") if last_check else "-",
        "details": _grok2api_quota_details(item),
    }

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

@router.get("/api/accounts")
async def get_accounts(page: int = Query(1), page_size: int = Query(50), hide_reg: str = Query("0"), search: Optional[str] = Query(None), status_filter: str = Query("all"), token: str = Depends(verify_token)):
    result = db_manager.get_accounts_page(page, page_size, hide_reg=hide_reg, search=search, status_filter=status_filter)
    return {"status": "success", "data": result["data"], "total": result["total"], "page": page, "page_size": page_size}


@router.get("/api/image_accounts")
async def get_image_accounts(
    page: int = Query(1),
    page_size: int = Query(50),
    search: Optional[str] = Query(None),
    token: str = Depends(verify_token)
):
    result = db_manager.get_image_accounts_page(page, page_size, search=search)
    return {"status": "success", "data": result["data"], "total": result["total"], "page": page, "page_size": page_size}

@router.post("/api/accounts/export_selected")
async def export_selected_accounts(req: ExportReq, token: str = Depends(verify_token)):
    if not req.emails: return {"status": "error", "message": "未收到任何要导出的账号"}
    tokens = db_manager.get_tokens_by_emails(req.emails)
    return {"status": "success", "data": tokens} if tokens else {"status": "error", "message": "未能提取到选中账号的有效 Token"}


@router.post("/api/accounts/delete")
async def delete_selected_accounts(req: DeleteReq, token: str = Depends(verify_token)):
    if not req.emails: return {"status": "error", "message": "未收到任何要删除的账号"}
    # chunk_size = 900
    # success = True
    # for i in range(0, len(req.emails), chunk_size):
    #     if not db_manager.delete_accounts_by_emails(req.emails[i:i + chunk_size]):
    #         success = False
    success = db_manager.delete_accounts_by_emails(req.emails)
    return {"status": "success", "message": f"成功删除所选账号"} if success else {"status": "error", "message": "部分删除操作失败"}


@router.post("/api/account/action")
def account_action(data: dict, token: str = Depends(verify_token)):
    from utils.email_providers.mail_service import mask_email
    try:
        action = data.get("action")
        target_emails = data.get("emails", [])
        if not target_emails and data.get("email"):
            target_emails = [data.get("email")]

        if not target_emails:
            return {"status": "error", "message": "未收到任何要操作的账号信息。"}

        config = getattr(core_engine.cfg, '_c', {})
        success_emails = []
        fail_count = 0
        last_error = ""

        if action == "push":
            if not config.get("cpa_mode", {}).get("enable", False):
                return {"status": "error", "message": "🚫 推送失败：未开启 CPA 模式！"}
            api_url = config.get("cpa_mode", {}).get("api_url", "")
            api_token = config.get("cpa_mode", {}).get("api_token", "")
            print(f"[{cfg.ts()}] [系统] 🚀 收到指令，准备将 {len(target_emails)} 个账号推送至 CPA...")

        elif action == "push_sub2api":
            if not getattr(core_engine.cfg, 'ENABLE_SUB2API_MODE', False):
                return {"status": "error", "message": "🚫 推送失败：未开启 Sub2API 模式！"}
            client = Sub2APIClient(
                api_url=getattr(core_engine.cfg, 'SUB2API_URL', ''),
                api_key=getattr(core_engine.cfg, 'SUB2API_KEY', '')
            )
            print(f"[{cfg.ts()}] [系统] 🛸 收到指令，准备将 {len(target_emails)} 个账号推送至 Sub2API...")
        elif action == "push_image2api":
            if not config.get("image2api_mode", {}).get("enable", False):
                return {"status": "error", "message": "🚫 推送失败：未开启 Image2API 模式！"}
            img_client = Image2APIClient()
            print(f"[{cfg.ts()}] [系统] 🖼️ 收到指令，准备将 {len(target_emails)} 个账号推送至 Image2API...")
        elif action == "grok2api-import":
            grok_url = getattr(cfg, "GROK2API_URL", None) or "http://host.docker.internal:8000"
            grok_pass = getattr(cfg, "GROK2API_ADMIN_PASSWORD", None) or ""
            if not grok_pass:
                return {"status": "error", "message": "请在设置页配置 Grok2API 管理员密码"}
            # Attempt to get or reuse grok token
            try:
                from curl_cffi import requests as cffi_req
                lr = cffi_req.post(f"{grok_url}/api/admin/v1/auth/login",
                    json={"username": "admin", "password": grok_pass},
                    timeout=15, impersonate="chrome110")
                if lr.status_code != 200:
                    return {"status": "error", "message": f"Grok2API 登录失败 HTTP {lr.status_code}"}
                grok_token = lr.json().get("data", {}).get("tokens", {}).get("accessToken", "")
                if not grok_token:
                    return {"status": "error", "message": "Grok2API 登录未获取到 accessToken"}
            except Exception as e:
                return {"status": "error", "message": f"Grok2API 登录异常: {e}"}
            print(f"[{cfg.ts()}] [系统] 🔮 收到指令，准备将 {len(target_emails)} 个账号导入至 Grok2API...")

        total_accounts = len(target_emails)
        for idx, email in enumerate(target_emails):
            token_data = db_manager.get_token_by_email(email)
            if not token_data:
                fail_count += 1
                last_error = f"账号 {email} 未找到 Token"
                print(f"[{cfg.ts()}] [警告] ❌ 账号 {mask_email(email)} 未找到有效 Token，跳过。")
                continue

            try:
                success = False
                if action == "push":
                    success, msg = core_engine.upload_to_cpa_integrated(token_data, api_url, api_token)
                    if not success:
                        last_error = msg
                        print(f"[{cfg.ts()}] [错误] ❌ 推送 CPA 失败 ({mask_email(email)}): {msg}")
                    else:
                        print(f"[{cfg.ts()}] [成功] ✅ 账号 {mask_email(email)} 成功推送至 CPA！")

                elif action == "push_sub2api":
                    success, resp = client.add_account(token_data)
                    if not success:
                        last_error = resp
                        print(f"[{cfg.ts()}] [错误] 推送 Sub2API 失败 ({mask_email(email)}): {resp}")
                    else:
                        print(f"[{cfg.ts()}] [成功] 账号 {mask_email(email)} 成功推送至 Sub2API！")
                elif action == "push_image2api":
                    access_token = token_data.get("access_token")
                    success, resp = img_client.add_accounts([access_token])
                    if not success:
                        last_error = resp
                    else:
                        print(f"[{cfg.ts()}] [成功] ✅ 账号 {mask_email(email)} 成功推送至 Image2API！")
                elif action == "grok2api-import":
                    if not core_engine._is_xai_like_token(token_data):
                        fail_count += 1
                        last_error = f"账号并非 Grok/xAI 类型，跳过"
                        print(f"[{cfg.ts()}] [跳过] ⚠️ 账号 {mask_email(email)} 非 Grok/xAI 类型，跳过导入")
                        continue
                    try:
                        from curl_cffi import requests as cffi_req2
                        import json as _json2
                        if token_data.get("refresh_token"):
                            # Force Grok2API to refresh immediately after import. This mirrors the
                            # previously successful manual expires_at backdate and lets Grok2API
                            # discover Build capabilities such as grok-imagine-video-1.5.
                            expires_str = datetime.datetime.fromtimestamp(int(time.time()) - 60, datetime.timezone.utc).isoformat().replace("+00:00", "Z")
                        else:
                            exp = token_data.get("expires_at")
                            expires_str = ""
                            if exp is not None:
                                try:
                                    if isinstance(exp, (int, float)):
                                        expires_str = datetime.datetime.fromtimestamp(int(exp), datetime.timezone.utc).isoformat().replace("+00:00", "Z")
                                    else:
                                        expires_str = str(exp)
                                except Exception:
                                    expires_str = str(exp) if exp else ""
                        grok_import_data = {
                            "provider": "grok_build",
                            "name": token_data.get("email", email),
                            "client_id": token_data.get("client_id", ""),
                            "access_token": token_data.get("access_token", ""),
                            "refresh_token": token_data.get("refresh_token", ""),
                            "id_token": token_data.get("id_token", ""),
                            "token_type": token_data.get("token_type", "Bearer"),
                            "email": token_data.get("email", email),
                            "user_id": token_data.get("user_id") or token_data.get("principal_id", ""),
                            "team_id": token_data.get("team_id", ""),
                            "expires_at": expires_str,
                        }
                        boundary = "----ocgrokimp"
                        body_parts = [
                            f"--{boundary}\r\n".encode(),
                            b'Content-Disposition: form-data; name="file"; filename="auth.json"\r\n',
                            b'Content-Type: application/json\r\n\r\n',
                            _json2.dumps(grok_import_data, ensure_ascii=False).encode(),
                            f"\r\n--{boundary}--\r\n".encode(),
                        ]
                        body = b"".join(body_parts)
                        import_resp = cffi_req2.post(
                            f"{grok_url}/api/admin/v1/accounts/import",
                            data=body,
                            headers={
                                "Authorization": f"Bearer {grok_token}",
                                "Content-Type": f"multipart/form-data; boundary={boundary}",
                            },
                            timeout=180, impersonate="chrome110"
                        )
                        if import_resp.status_code in (200, 201):
                            success = True
                            print(f"[{cfg.ts()}] [成功] ✅ 账号 {mask_email(email)} 已导入 Grok2API！")
                        else:
                            last_error = f"HTTP {import_resp.status_code}: {import_resp.text[:200]}"
                            print(f"[{cfg.ts()}] [错误] ❌ Grok2API 导入失败 {mask_email(email)}: HTTP {import_resp.status_code}")
                    except Exception as imp_err:
                        last_error = str(imp_err)
                        print(f"[{cfg.ts()}] [错误] ❌ Grok2API 导入异常 {mask_email(email)}: {imp_err}")
                if success:
                    success_emails.append(email)
                else:
                    fail_count += 1
            except Exception as e:
                fail_count += 1
                last_error = str(e)

        if total_accounts > 1 and idx < total_accounts - 1:
            time.sleep(0.3)

        if success_emails:
            platform_map = {
                "push": "CPA",
                "push_sub2api": "SUB2API",
                "push_image2api": "IMAGE2API",
                "grok2api-import": "GROK2API"
            }
            platform_marker = platform_map.get(action, "UNKNOWN")
            db_manager.update_account_push_info(success_emails, platform_marker)

        print(f"[{cfg.ts()}] [系统] 🏁 推送任务执行完毕。成功: {len(success_emails)} 个，失败: {fail_count} 个。")

        total = len(target_emails)
        if total == 1:
            if success_emails:
                return {"status": "success", "message": f"账号 {target_emails[0]} 已成功推送！"}
            else:
                return {"status": "error", "message": f"推送失败: {last_error}"}
        else:
            msg = f"批量操作完成！成功: {len(success_emails)} 个"
            if fail_count > 0:
                msg += f"，失败: {fail_count} 个"
            return {"status": "success" if success_emails else "error", "message": msg}

    except Exception as e:
        return {"status": "error", "message": f"后端处理异常: {str(e)}"}


@router.post("/api/accounts/export_sub2api")
async def export_sub2api_accounts(req: ExportReq, token: str = Depends(verify_token)):
    from datetime import datetime, timezone
    try:
        tokens = db_manager.get_tokens_by_emails(req.emails)
        if not tokens: return {"status": "error", "message": "未提取到Token"}

        bundle = build_sub2api_export_bundle(tokens, get_sub2api_push_settings(), rotate_missing_proxy=True)
        return {"status": "success", "data": bundle}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/api/accounts/export_all")
async def export_all_accounts(token: str = Depends(verify_token)):
    data = db_manager.get_all_accounts_raw()
    return {"status": "success", "data": data}

@router.post("/api/accounts/clear_all")
async def clear_all_accounts_api(token: str = Depends(verify_token)):
    if db_manager.clear_all_accounts():
        return {"status": "success", "message": "账号库已全部清空"}
    return {"status": "error", "message": "清空失败"}


def _background_sync_cloud_data(combined_data):
    # global _last_cloud_sync_time
    # if time.time() - _last_cloud_sync_time < 30:
    #     return
    # _last_cloud_sync_time = time.time()
    try:
        cpa_emails = [x["credential"] for x in combined_data if x["account_type"] == "cpa"]
        sub_emails = [x["credential"] for x in combined_data if x["account_type"] == "sub2api"]
        img2_emails = [x["credential"] for x in combined_data if x["account_type"] == "image2api"]
        grok_emails = [x["credential"] for x in combined_data if x["account_type"] == "grok2api"]

        if cpa_emails:
            db_manager.update_account_push_info(cpa_emails, "CPA", mode="sync")
        if sub_emails:
            db_manager.update_account_push_info(sub_emails, "SUB2API", mode="sync")
        if img2_emails:
            db_manager.update_account_push_info(img2_emails, "IMAGE2API", mode="sync")
        if grok_emails:
            db_manager.update_account_push_info(grok_emails, "GROK2API", mode="sync")

        active_emails = [x["credential"] for x in combined_data if x["status"] == "active"]
        inactive_emails = [x["credential"] for x in combined_data if x["status"] in ["disabled", "dead"]]
        def chunked_update(emails_list, status_code):
            chunk_size = 900
            for i in range(0, len(emails_list), chunk_size):
                db_manager.update_account_status(emails_list[i:i + chunk_size], status_code)

        if active_emails:
            chunked_update(active_emails, 1)
        if inactive_emails:
            chunked_update(inactive_emails, 0)

    except Exception as e:
        print(f"[{cfg.ts()}] [系统] 后台同步云端数据至本地库异常: {e}")


@router.get("/api/cloud/accounts")
def get_cloud_accounts(background_tasks: BackgroundTasks, types: str = "sub2api,cpa", status_filter: str = Query("all"), page: int = Query(1),
                       page_size: int = Query(50), search: Optional[str] = Query(None),
                       token: str = Depends(verify_token)):
    type_list = types.split(",")
    combined_data = []
    if "sub2api" in type_list and getattr(cfg, 'SUB2API_URL', None) and getattr(cfg, 'SUB2API_KEY', None):
        if not getattr(cfg, 'ENABLE_SUB2API_MODE', False):
            # Sub2API mode is disabled. Do not spend ~15-20s probing a stale endpoint
            # just because old URL/key values still exist in config.yaml.
            pass
        else:
            try:
                client = Sub2APIClient(api_url=cfg.SUB2API_URL, api_key=cfg.SUB2API_KEY)
                success, raw_sub2_data = client.get_all_accounts()
                if success:
                    def process_single_account(item):
                        raw_time = item.get("updated_at", "-")
                        if raw_time != "-":
                            try:
                                raw_time = raw_time.split(".")[0].replace("T", " ")
                            except:
                                pass

                        extra = item.get("extra", {})
                        account_id = str(item.get("id", ""))
                        window_stats = {}
                        # if account_id:
                        #     usage_ok, usage_data = client.get_account_usage(account_id)
                        #     if usage_ok and isinstance(usage_data, dict):
                        #         window_stats = usage_data.get("data", {}).get("five_hour", {}).get("window_stats", {})
                        return {
                            "id": account_id,
                            "account_type": "sub2api",
                            "credential": item.get("name", "未知账号"),
                            "status": "disabled" if item.get("status") == "inactive" else (
                                "active" if item.get("status") == "active" else "dead"),
                            "last_check": raw_time,
                            "details": {
                                "plan_type": item.get("credentials", {}).get("plan_type", "未知"),
                                "platform": item.get("platform") or item.get("type") or item.get("provider") or "",
                                "type": item.get("type") or item.get("platform") or "",
                                "codex_5h_used_percent": extra.get("codex_5h_used_percent", 0),
                                "codex_7d_used_percent": extra.get("codex_7d_used_percent", 0),
                                "window_stats": window_stats
                            }
                        }
                    with ThreadPoolExecutor(max_workers=10) as executor:
                        results = list(executor.map(process_single_account, raw_sub2_data))
                    combined_data.extend(results)

            except Exception as e:
                print(f"[{cfg.ts()}] [SUB2API] 拉取 Sub2API 数据异常，如果未填写相关数据可忽略该提示，将跳过: {e}")

    if "cpa" in type_list and getattr(cfg, 'CPA_API_URL', None) and getattr(cfg, 'CPA_API_TOKEN', None):
        try:
            from curl_cffi import requests
            res = requests.get(core_engine._normalize_cpa_auth_files_url(cfg.CPA_API_URL),
                               headers={"Authorization": f"Bearer {cfg.CPA_API_TOKEN}"}, timeout=20,
                               impersonate="chrome110")
            if res.status_code == 200:
                for item in res.json().get("files", []):
                    typ = str(item.get("type", "")).lower()
                    provider = str(item.get("provider", "")).lower()
                    is_codex = "codex" in typ or "codex" in provider
                    is_xai = ("xai" in typ or "xai" in provider or "grok" in typ or "grok" in provider)
                    if not (is_codex or is_xai):
                        continue
                    combined_data.append({
                        "id": item.get("name", ""),
                        "account_type": "cpa",
                        "credential": item.get("name", "").replace(".json", ""),
                        "status": "disabled" if item.get("disabled", False) else "active",
                        "details": {
                            "platform": "xai" if is_xai else "codex",
                            "type": item.get("type") or item.get("provider") or ("xai" if is_xai else "codex"),
                        },
                        "last_check": "-",
                    })
        except Exception as e:
            print(f"[{cfg.ts()}] [CPA] 拉取 CPA 数据异常，如果未填写相关数据可忽略该提示，将跳过: {e}")
    if "image2api" in type_list and getattr(cfg, 'ENABLE_IMAGE2API_MODE', False):
        try:
            from utils.integrations.image2api_client import Image2APIClient
            client = Image2APIClient()
            ok, raw_img2_data = client.get_accounts()
            if ok:
                for item in raw_img2_data.get("items", []):
                    token_str = item.get("access_token", "")
                    combined_data.append({
                        "id": token_str,
                        "account_type": "image2api",
                        "credential": item.get("email", "未知邮箱"),
                        "status": "active" if item.get("status") == "正常" else "disabled",
                        "last_check": str(item.get("restoreAt", "-")).split("T")[0],
                        "details": {
                            "plan_type": item.get("type", "Free"),
                            "quota": item.get("quota", 0),
                            "limits": item.get("limits_progress", [])
                        }
                    })
        except Exception as e:
            print(f"[{cfg.ts()}] [IMAGE2API] 拉取 Image2API 数据异常，如果未填写相关数据可忽略该提示，将跳过: {e}")

    if (
        "grok2api" in type_list
        and getattr(cfg, "ENABLE_GROK2API_MODE", False)
        and getattr(cfg, "GROK2API_URL", None)
        and getattr(cfg, "GROK2API_ADMIN_PASSWORD", None)
    ):
        ok_login, grok_token, msg = _grok2api_login()
        if ok_login:
            ok_list, raw_grok_accounts, list_msg = _grok2api_list_accounts(grok_token)
            if ok_list:
                combined_data.extend(_grok2api_to_cloud_item(item) for item in raw_grok_accounts)
            else:
                print(f"[{cfg.ts()}] [GROK2API] 拉取 Grok2API 数据异常: {list_msg}")
        else:
            print(f"[{cfg.ts()}] [GROK2API] 登录失败，将跳过云端库存: {msg}")

    try:
        cpa_list = [x for x in combined_data if x["account_type"] == "cpa"]
        sub2api_list = [x for x in combined_data if x["account_type"] == "sub2api"]
        image2api_list = [x for x in combined_data if x["account_type"] == "image2api"]
        grok2api_list = [x for x in combined_data if x["account_type"] == "grok2api"]

        cloud_stats = {
            "total": len(combined_data),
            "enabled": sum(1 for x in combined_data if x["status"] == "active"),
            "cpa": len(cpa_list),
            "cpa_active": sum(1 for x in cpa_list if x["status"] == "active"),
            "cpa_disabled": sum(1 for x in cpa_list if x["status"] != "active"),
            "sub2api": len(sub2api_list),
            "sub2api_active": sum(1 for x in sub2api_list if x["status"] == "active"),
            "sub2api_disabled": sum(1 for x in sub2api_list if x["status"] != "active"),
            "image2api": len(image2api_list),
            "image2api_active": sum(1 for x in image2api_list if x["status"] == "active"),
            "image2api_disabled": sum(1 for x in image2api_list if x["status"] != "active"),
            "grok2api": len(grok2api_list),
            "grok2api_active": sum(1 for x in grok2api_list if x["status"] == "active"),
            "grok2api_disabled": sum(1 for x in grok2api_list if x["status"] != "active")
        }
        if combined_data:
            background_tasks.add_task(_background_sync_cloud_data, combined_data)

        if status_filter != "all":
            combined_data = [item for item in combined_data if item.get("status") == status_filter]

        if search:
            search_lower = search.lower()
            combined_data = [
                item for item in combined_data
                if search_lower in str(item.get("credential", "")).lower() or
                   search_lower in str(item.get("id", "")).lower()
            ]
        total_count = len(combined_data)
        start_idx = (page - 1) * page_size
        end_idx = page * page_size
        paged_data = combined_data[start_idx:end_idx]
        return {
            "status": "success",
            "data": paged_data,
            "total": total_count,
            "cloud_stats": cloud_stats
        }
    except Exception as e:
        return {"status": "error", "message": f"拉取云端库存数据失败: {e}"}

class BulkUsageRequest(BaseModel):
    account_ids: List[str]

@router.post("/api/cloud/sub2api/usage/bulk")
def bulk_get_sub2api_usage(req: BulkUsageRequest, token: str = Depends(verify_token)):
    if not getattr(cfg, 'SUB2API_URL', None) or not getattr(cfg, 'SUB2API_KEY', None):
        return {"status": "error", "message": "Sub2API 配置未填写"}

    try:
        client = Sub2APIClient(api_url=cfg.SUB2API_URL, api_key=cfg.SUB2API_KEY)
        results = {}

        def fetch_usage(acc_id):
            ok, data = client.get_account_usage(acc_id)
            if ok and isinstance(data, dict):
                return acc_id, data.get("data", {}).get("five_hour", {}).get("window_stats", {})
            return acc_id, {}

        with ThreadPoolExecutor(max_workers=10) as executor:
            for acc_id, stats in executor.map(fetch_usage, req.account_ids):
                results[acc_id] = stats

        return {"status": "success", "data": results}
    except Exception as e:
        return {"status": "error", "message": f"批量获取异常: {str(e)}"}

@router.post("/api/cloud/action")
def process_cloud_action(req: CloudActionReq, token: str = Depends(verify_token)):
    from curl_cffi import requests
    from concurrent.futures import ThreadPoolExecutor

    success_count, fail_count, updated_details_map = 0, 0, {}
    sub2api_client = (
        Sub2APIClient(api_url=cfg.SUB2API_URL, api_key=cfg.SUB2API_KEY)
        if getattr(cfg, 'ENABLE_SUB2API_MODE', False) and getattr(cfg, 'SUB2API_URL', None) and getattr(cfg, 'SUB2API_KEY', None)
        else None
    )

    cpa_files_map = {}
    if any(a.type == "cpa" for a in req.accounts) and req.action == "check" and getattr(cfg, 'CPA_API_URL', None):
        try:
            res = requests.get(core_engine._normalize_cpa_auth_files_url(cfg.CPA_API_URL),
                               headers={"Authorization": f"Bearer {cfg.CPA_API_TOKEN}"}, timeout=15,
                               impersonate="chrome110")
            if res.status_code == 200: cpa_files_map = {f.get("name"): f for f in res.json().get("files", [])}
        except:
            pass

    sub2_item_map = {}
    if any(a.type == "sub2api" for a in req.accounts) and req.action == "check" and sub2api_client:
        try:
            ok_all, all_items = sub2api_client.get_all_accounts()
            if ok_all and isinstance(all_items, list):
                for it in all_items:
                    sub2_item_map[str(it.get("id", ""))] = it
        except Exception:
            pass

    grok2api_token = None
    if any(a.type == "grok2api" for a in req.accounts):
        if not getattr(cfg, "ENABLE_GROK2API_MODE", False):
            return {"status": "error", "message": "Grok2API 仓管中心未开启，已跳过远端操作"}
        ok_login, token_value, login_msg = _grok2api_login()
        if ok_login:
            grok2api_token = token_value
        else:
            return {"status": "error", "message": login_msg}

    def _resolve_sub2_test_model(acc: CloudAccountItem):
        platform = str(getattr(acc, "platform", None) or "").lower()
        if "grok" in platform or "xai" in platform:
            return "grok-4.5"
        item = sub2_item_map.get(str(acc.id), {})
        if core_engine._is_xai_like_token(item):
            return "grok-4.5"
        return None

    def _worker(acc: CloudAccountItem):
        is_success, details = False, None
        try:
            if acc.type == "sub2api" and sub2api_client:
                if req.action == "check":
                    model_id = _resolve_sub2_test_model(acc)
                    result, reason = sub2api_client.test_account(acc.id, model_id=model_id)
                    is_success = (result == "ok")
                    if not is_success:
                        sub2api_client.set_account_status(acc.id, disabled=True)
                        details = {"check_msg": str(reason or "测活失败"), "test_model": model_id or getattr(cfg, "SUB2API_TEST_MODEL", "")}
                    else:
                        details = {"check_msg": "测活成功", "test_model": model_id or getattr(cfg, "SUB2API_TEST_MODEL", "")}
                elif req.action in ["enable", "disable"]:
                    is_success = sub2api_client.set_account_status(acc.id, disabled=(req.action == "disable"))
                elif req.action == "delete":
                    is_success, _ = sub2api_client.delete_account(acc.id)

            elif acc.type == "cpa" and getattr(cfg, 'CPA_API_URL', None):
                if req.action == "check":
                    item = cpa_files_map.get(acc.id, {"name": acc.id, "disabled": False})
                    is_success, check_msg = core_engine.test_cliproxy_auth_file(
                        item, cfg.CPA_API_URL, cfg.CPA_API_TOKEN
                    )
                    if core_engine._is_xai_like_token(item):
                        details = {
                            "platform": "xai",
                            "type": item.get("type") or item.get("provider") or "xai",
                            "check_msg": check_msg,
                        }
                    elif "_raw_usage" in item:
                        details = parse_cpa_usage_to_details(item["_raw_usage"])
                    if not is_success:
                        core_engine.set_cpa_auth_file_status(
                            cfg.CPA_API_URL, cfg.CPA_API_TOKEN, acc.id, disabled=True
                        )
                elif req.action in ["enable", "disable"]:
                    is_success = core_engine.set_cpa_auth_file_status(cfg.CPA_API_URL, cfg.CPA_API_TOKEN, acc.id,
                                                                      disabled=(req.action == "disable"))
                elif req.action == "delete":
                    is_success = requests.delete(core_engine._normalize_cpa_auth_files_url(cfg.CPA_API_URL),
                                                 headers={"Authorization": f"Bearer {cfg.CPA_API_TOKEN}"},
                                                 params={"name": acc.id}, impersonate="chrome110").status_code in (
                                 200, 204)
            elif acc.type == "image2api":
                from utils.integrations.image2api_client import Image2APIClient
                client = Image2APIClient()

                if req.action in ["enable", "disable"]:
                    target_status = "正常" if req.action == "enable" else "禁用"
                    is_success, _ = client.update_account_status(access_token=acc.id, status=target_status)
                elif req.action == "refresh":
                    is_success, resp = client.refresh_tokens([acc.id])
                    if is_success:
                        details = {"refresh_msg": f"成功刷新 {resp.get('refreshed', 0)} 个"}

                elif req.action == "check":
                    is_success = True

                elif req.action == "delete":
                    pass

            elif acc.type == "grok2api" and grok2api_token:
                provider = str(getattr(acc, "platform", "") or "").strip().lower()
                if req.action in ["enable", "disable"]:
                    resp = _grok2api_request(
                        "PATCH", f"/accounts/{acc.id}", grok2api_token,
                        json={"enabled": req.action == "enable"}, timeout=30,
                    )
                    is_success = resp.status_code == 200
                    if is_success:
                        details = _grok2api_quota_details(resp.json().get("data", {}))
                    else:
                        details = {"check_msg": f"启停失败 HTTP {resp.status_code}"}
                elif req.action == "delete":
                    body = {"provider": provider} if provider else {}
                    resp = _grok2api_request("DELETE", f"/accounts/{acc.id}", grok2api_token, json=body, timeout=40)
                    is_success = resp.status_code in (200, 204)
                    if not is_success:
                        details = {"check_msg": f"删除失败 HTTP {resp.status_code}"}
                elif req.action == "refresh":
                    refresh_path = f"/accounts/{acc.id}/refresh-quota" if provider == "grok_web" else f"/accounts/{acc.id}/refresh-token"
                    resp = _grok2api_request("POST", refresh_path, grok2api_token, timeout=180)
                    is_success = resp.status_code == 200
                    if is_success:
                        details = _grok2api_quota_details(resp.json().get("data", {}))
                        details["refresh_msg"] = "刷新成功"
                    else:
                        details = {"refresh_msg": f"刷新失败 HTTP {resp.status_code}"}
                elif req.action == "check":
                    # Grok Web: refresh quota is the most meaningful health check.
                    # Grok Build/Console: token refresh checks credential validity without sending user traffic.
                    check_path = f"/accounts/{acc.id}/refresh-quota" if provider == "grok_web" else f"/accounts/{acc.id}/refresh-token"
                    resp = _grok2api_request("POST", check_path, grok2api_token, timeout=180)
                    is_success = resp.status_code == 200
                    if is_success:
                        details = _grok2api_quota_details(resp.json().get("data", {}))
                        details["check_msg"] = "测活成功"
                    else:
                        details = {"check_msg": f"测活失败 HTTP {resp.status_code}"}
        except Exception as exc:
            details = {"check_msg": f"操作异常: {exc}", "refresh_msg": f"操作异常: {exc}"}
        return (is_success, acc.id, acc.type, details)

    target_threads = 5
    if any(a.type == "cpa" for a in req.accounts): target_threads = max(target_threads, int(
        getattr(cfg, '_c', {}).get('cpa_mode', {}).get('threads', 10)))
    if any(a.type == "sub2api" for a in req.accounts): target_threads = max(target_threads, int(
        getattr(cfg, '_c', {}).get('sub2api_mode', {}).get('threads', 10)))
    if any(a.type == "image2api" for a in req.accounts):
        target_threads = max(target_threads, 10)
    if any(a.type == "grok2api" for a in req.accounts):
        target_threads = min(target_threads, 5)

    with ThreadPoolExecutor(max_workers=max(1, min(target_threads, 50))) as executor:
        futures = []
        for idx, acc in enumerate(req.accounts):
            futures.append(executor.submit(_worker, acc))
            if idx < len(req.accounts) - 1:
                time.sleep(0.5)

        for future in futures:
            is_success, acc_id, acc_type, details = future.result()
            if is_success:
                success_count += 1
            else:
                fail_count += 1
            if details:
                updated_details_map[acc_id] = details
                updated_details_map[f"{acc_type}|{acc_id}"] = details

    msg = f"测活完毕 | 存活: {success_count} 个 | 失效并已自动禁用: {fail_count} 个" if req.action == "check" else f"指令已下发 | 成功: {success_count} 个 | 失败: {fail_count} 个"

    return {"status": "success" if fail_count == 0 else "warning", "message": msg,
            "updated_details": updated_details_map}


class Grok2APIImportReq(BaseModel):
    accounts: list[dict]

@router.post("/api/cloud/grok2api-import")
def grok2api_import(req: Grok2APIImportReq, token: str = Depends(verify_token)):
    grok_url = getattr(cfg, "GROK2API_URL", None) or "http://host.docker.internal:8000"
    grok_pass = getattr(cfg, "GROK2API_ADMIN_PASSWORD", None) or ""
    if not grok_pass:
        return {"status": "error", "message": "Grok2API 未配置 admin_password（config.yaml → grok2api.admin_password）"}
    if not req.accounts:
        return {"status": "error", "message": "请选择至少一个账号"}
    if not getattr(cfg, "CPA_API_URL", None) or not getattr(cfg, "CPA_API_TOKEN", None):
        return {"status": "error", "message": "CPA 未配置"}

    # login to Grok2API
    try:
        from curl_cffi import requests as cffi_requests
        lr = cffi_requests.post(
            f"{grok_url}/api/admin/v1/auth/login",
            json={"username": "admin", "password": grok_pass},
            timeout=30, impersonate="chrome110"
        )
        if lr.status_code != 200:
            return {"status": "error", "message": f"Grok2API 登录失败 HTTP {lr.status_code}"}
        grok_token = lr.json().get("data", {}).get("tokens", {}).get("accessToken", "")
        if not grok_token:
            return {"status": "error", "message": "Grok2API 登录后未获取到 accessToken"}
    except Exception as e:
        return {"status": "error", "message": f"Grok2API 登录异常: {e}"}

    created, skipped, failed = 0, 0, 0
    errors = []
    for acc in req.accounts:
        name = acc.get("id", "")
        if not name:
            skipped += 1
            continue
        try:
            # download CPA file
            dl_url = f"{core_engine._normalize_cpa_auth_files_url(cfg.CPA_API_URL)}/download"
            content_resp = cffi_requests.get(
                dl_url, params={"name": name},
                headers={"Authorization": f"Bearer {cfg.CPA_API_TOKEN}"},
                timeout=25, impersonate="chrome110"
            )
            if content_resp.status_code != 200:
                failed += 1
                errors.append(f"{name}: CPA 下载失败 HTTP {content_resp.status_code}")
                continue
            item_data = content_resp.json()
            is_grok = core_engine._is_xai_like_token(item_data)
            if not is_grok:
                skipped += 1
                continue

            # transform to Grok2API format
            import_data = {
                "provider": "grok_build",
                "name": item_data.get("email", name.replace(".json", "")),
                "client_id": item_data.get("client_id", "b1a00492-073a-47ea-816f-4c329264a828"),
                "access_token": item_data.get("access_token", ""),
                "refresh_token": item_data.get("refresh_token", ""),
                "id_token": item_data.get("id_token", ""),
                "token_type": item_data.get("token_type", "Bearer"),
                "email": item_data.get("email", ""),
                "user_id": item_data.get("user_id") or item_data.get("principal_id", ""),
                "team_id": item_data.get("team_id", ""),
            }
            # Convert expires_at. If refresh_token exists, backdate it to force
            # Grok2API's OAuth refresh/discovery path immediately after import.
            if item_data.get("refresh_token"):
                import_data["expires_at"] = datetime.datetime.fromtimestamp(int(time.time()) - 60, datetime.timezone.utc).isoformat().replace("+00:00", "Z")
            else:
                exp = item_data.get("expires_at")
                if exp is not None:
                    try:
                        if isinstance(exp, (int, float)):
                            import_data["expires_at"] = datetime.datetime.fromtimestamp(int(exp), datetime.timezone.utc).isoformat().replace("+00:00", "Z")
                        elif str(exp).isdigit():
                            import_data["expires_at"] = datetime.datetime.fromtimestamp(int(str(exp)), datetime.timezone.utc).isoformat().replace("+00:00", "Z")
                        else:
                            import_data["expires_at"] = str(exp)
                    except Exception:
                        import_data["expires_at"] = str(exp) if exp else ""

            # upload to Grok2API
            import json as _json
            boundary = "----ocgrok2apiimport"
            body_parts = []
            body_parts.append(f"--{boundary}\r\n".encode())
            body_parts.append(b'Content-Disposition: form-data; name="file"; filename="auth.json"\r\n')
            body_parts.append(b'Content-Type: application/json\r\n\r\n')
            body_parts.append(_json.dumps(import_data, ensure_ascii=False).encode())
            body_parts.append(f"\r\n--{boundary}--\r\n".encode())
            body = b"".join(body_parts)

            import_resp = cffi_requests.post(
                f"{grok_url}/api/admin/v1/accounts/import",
                data=body,
                headers={
                    "Authorization": f"Bearer {grok_token}",
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                },
                timeout=180, impersonate="chrome110"
            )
            if import_resp.status_code in (200, 201):
                created += 1
            else:
                failed += 1
                errors.append(f"{name}: HTTP {import_resp.status_code}")
        except Exception as e:
            failed += 1
            errors.append(f"{name}: {e}")

    result_msg = f"导入完成 | 成功: {created} | 非Grok跳过: {skipped} | 失败: {failed}"
    return {
        "status": "success" if failed == 0 else "warning",
        "message": result_msg,
        "detail": {"created": created, "skipped": skipped, "failed": failed},
        "errors": errors[:10] if errors else None
    }


@router.get("/api/mailboxes")
async def get_mailboxes(page: int = Query(1), page_size: int = Query(50), search: Optional[str] = Query(None), token: str = Depends(verify_token)):
    result = db_manager.get_local_mailboxes_page(page, page_size, search=search)
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
                with get_db_conn(is_write=True) as conn:
                    c = get_cursor(conn)
                    execute_sql(c,
                                "UPDATE local_mailboxes SET client_id = ?, refresh_token = ?, status = 0 WHERE email = ?",
                                (req.client_id, refresh_token, req.email)
                                )
            except Exception as e:
                print(f"[{cfg.ts()}] [ERROR] 数据库更新 OAuth Token 失败: {e}")
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

@router.post("/api/mailboxes/export_all")
async def export_all_mailboxes(token: str = Depends(verify_token)):
    data = db_manager.get_all_mailboxes_raw()
    return {"status": "success", "data": data}

@router.post("/api/mailboxes/clear_all")
async def clear_all_mailboxes_api(token: str = Depends(verify_token)):
    if db_manager.clear_all_mailboxes():
        return {"status": "success", "message": "邮箱库已全部清空"}
    return {"status": "error", "message": "清空失败"}

@router.get("/api/accounts/stats")
async def api_get_inventory_stats(token: str = Depends(verify_token)):
    stats = db_manager.get_inventory_stats()
    return {"status": "success", "data": stats}

@router.post("/api/accounts/bulk_refresh")
def bulk_refresh_api(req: BulkRefreshReq, token: str = Depends(verify_token)):
    from utils.email_providers.mail_service import mask_email
    if not req.emails:
        return {"status": "error", "message": "未收到任何要刷新的账号"}

    sub2api_map = {}
    if getattr(cfg, 'ENABLE_SUB2API_MODE', False) and getattr(cfg, 'SUB2API_URL', None) and getattr(cfg, 'SUB2API_KEY', None):
        try:
            client = Sub2APIClient(api_url=cfg.SUB2API_URL, api_key=cfg.SUB2API_KEY)
            ok, raw_data = client.get_all_accounts()
            if ok:
                sub2api_map = {acc.get("name"): acc.get("id") for acc in raw_data if acc.get("name")}
        except Exception as e:
            print(f"[{cfg.ts()}] [系统] 批量刷新前获取 Sub2API 映射失败: {e}")

    def _refresh_single(email: str) -> bool:
        full_info = db_manager.get_account_full_info(email)
        if not full_info:
            print(f"[{cfg.ts()}] [警告] ❌ 账号 {mask_email(email)} 未找到本地数据，跳过。")
            return False

        token_data = full_info['token_data']
        rt = token_data.get("refresh_token")
        current_platforms = [p.strip().upper() for p in (full_info.get('push_platform') or "").split(',') if p.strip()]
        is_image2api = ("IMAGE2API" in current_platforms) or ("image2api" in str(token_data.get("status", "")).lower())
        if is_image2api:
            old_token = token_data.get("access_token")
            if not old_token:
                db_manager.update_account_status([email], 0)
                print(f"[{cfg.ts()}] [错误] ❌ Image2API 账号 {mask_email(email)} 缺少 access_token，标为死号。")
                return False

            print(f"[{cfg.ts()}] [INFO] 🔄 检测到 {mask_email(email)} 属于 Image2API，自动调用远端刷新...")
            try:
                from utils.integrations.image2api_client import Image2APIClient
                client = Image2APIClient()
                ok, res = client.refresh_tokens([old_token])

                if ok and isinstance(res, dict) and res.get("items"):
                    refreshed_item = next((item for item in res.get("items", []) if
                                           item.get("email") == email or item.get("access_token")), None)

                    if refreshed_item and refreshed_item.get("access_token"):
                        new_token = refreshed_item.get("access_token")
                        token_data["access_token"] = new_token

                        remote_status = refreshed_item.get("status", "正常")
                        token_data["status"] = "image2api" if remote_status == "正常" else "image2api_禁用"

                        db_manager.update_account_token_only(email, json.dumps(token_data))
                        db_manager.update_account_status([email], 1 if remote_status == "正常" else 0)

                        print(f"[{cfg.ts()}] [成功] ✅ 账号 {mask_email(email)} 经由 Image2API 远端刷新存活！")
                        return True
                db_manager.update_account_status([email], 0)
                db_manager.remove_account_push_platform(email, "IMAGE2API", exact_match=True)
                print(f"[{cfg.ts()}] [错误] ❌ 账号 {mask_email(email)} Image2API 远端刷新失败或已失效，标记死亡并解绑平台。")
                return False
            except Exception as e:
                print(f"[{cfg.ts()}] [系统] 账号 {mask_email(email)} Image2API 刷新异常: {e}")
                return False

        if not rt:
            db_manager.update_account_status([email], 0)
            print(f"[{cfg.ts()}] [错误] ❌ 账号 {mask_email(email)} 缺少 refresh_token，标为死号。")
            return False

        proxies = {"http": getattr(cfg, 'DEFAULT_PROXY', None),
                   "https": getattr(cfg, 'DEFAULT_PROXY', None)} if getattr(cfg, 'DEFAULT_PROXY', None) else None
        ok, new_tokens = core_engine.refresh_oauth_token(rt, proxies=proxies)

        if not ok:
            err = new_tokens.get("error", "未知错误") if isinstance(new_tokens, dict) else str(new_tokens)
            db_manager.update_account_status([email], 0)
            db_manager.remove_account_push_platform(email, "CPA", exact_match=True)
            db_manager.remove_account_push_platform(email, "SUB2API", exact_match=False)
            print(f"[{cfg.ts()}] [错误] ❌ 账号 {mask_email(email)} 刷新失败 ({err})，已标记死亡并解绑平台。")
            return False

        token_data.update(new_tokens)
        db_manager.update_account_token_only(email, json.dumps(token_data))
        db_manager.update_account_status([email], 1)

        current_platforms = [p.strip().upper() for p in (full_info.get('push_platform') or "").split(',') if p.strip()]
        sync_tags = []

        if "CPA" in current_platforms and getattr(cfg, 'CPA_API_URL', None):
            up_ok, _ = core_engine.upload_to_cpa_integrated(token_data, cfg.CPA_API_URL, cfg.CPA_API_TOKEN,
                                                            custom_filename=f"{email}.json")
            if up_ok: sync_tags.append("CPA")

        if "SUB2API" in current_platforms and sub2api_map:
            target_id = sub2api_map.get(email[:64])
            if target_id:
                client = Sub2APIClient(api_url=cfg.SUB2API_URL, api_key=cfg.SUB2API_KEY)
                client.update_account(target_id, {"credentials": token_data})
                sync_tags.append("Sub2API")

        sync_msg = f"已覆盖至: {' + '.join(sync_tags)}" if sync_tags else "无远端标签"
        print(f"[{cfg.ts()}] [成功] ✅ 账号 {mask_email(email)} 刷新存活！{sync_msg}")

        return True

    from concurrent.futures import ThreadPoolExecutor
    print(f"[{cfg.ts()}] [系统] 🔄 开始并发批量刷新 {len(req.emails)} 个账号...")

    target_threads = min(20, len(req.emails))
    results = []
    with ThreadPoolExecutor(max_workers=target_threads) as executor:
        futures = []
        for idx, email in enumerate(req.emails):

            futures.append(executor.submit(_refresh_single, email))
            if idx < len(req.emails) - 1:
                time.sleep(0.5)

        for future in futures:
            results.append(future.result())

    success_count = sum(1 for r in results if r)
    fail_count = len(results) - success_count

    print(f"[{cfg.ts()}] [系统] 🏁 批量刷新完毕！成功: {success_count} 个，失败/死亡: {fail_count} 个。")

    return {
        "status": "success" if success_count > 0 else "warning",
        "message": f"批量刷新完成！成功: {success_count} 个，失败/死亡: {fail_count} 个"
    }


@router.get("/api/team_accounts")
async def get_team_accounts(page: int = Query(1), page_size: int = Query(50), search: Optional[str] = Query(None),
                            token: str = Depends(verify_token)):
    result = db_manager.get_team_accounts_page(page, page_size, search=search)
    return {"status": "success", "data": result["data"], "total": result["total"], "page": page, "page_size": page_size}


@router.post("/api/team_accounts/import")
async def import_team_accounts(req: ImportTeamReq, token: str = Depends(verify_token)):
    if not req.raw_text: return {"status": "error", "message": "内容为空"}

    parsed_teams = []
    lines = req.raw_text.strip().split("\n")
    for line in lines:
        acc_token = line.strip()
        if not acc_token or len(acc_token) < 50: continue
        parts = line.split("----")
        acc_token = parts[0].strip()
        cookies = parts[1].strip() if len(parts) > 1 else ""
        jwt_data = email_jwt(acc_token)
        real_email = jwt_data.get("email", "") if isinstance(jwt_data, dict) else ""
        parsed_teams.append({
            "email": real_email if real_email else "未知邮箱(解析失败)",
            "access_token": acc_token,
            "cookies": cookies,
            "status": 1
        })
    if not parsed_teams: return {"status": "error", "message": "未能识别出有效 Token"}
    count = db_manager.import_team_accounts(parsed_teams)
    return {"status": "success", "count": count}


@router.post("/api/team_accounts/delete")
async def delete_team_accounts(req: DeleteTeamReq, token: str = Depends(verify_token)):
    if not req.ids: return {"status": "error", "message": "未收到任何要删除的ID"}
    if db_manager.delete_team_accounts(req.ids):
        return {"status": "success", "message": "删除成功"}
    return {"status": "error", "message": "删除操作失败"}


@router.post("/api/team_accounts/clear_all")
async def clear_all_team_accounts(token: str = Depends(verify_token)):
    if db_manager.clear_all_team_accounts():
        return {"status": "success", "message": "Team 库已全部清空"}
    return {"status": "error", "message": "清空失败"}


@router.post("/api/auth/upload_license")
async def upload_license(req: LicenseUploadReq, token: str = Depends(verify_token)):
    if not req.content or not req.content.strip():
        return {"status": "error", "message": "上传的授权内容为空"}
    try:
        db_manager.set_sys_kv('auth_license_file', req.content.strip())

        return {"status": "success", "message": "授权文件已成功上传至数据库！请重启程序生效。"}
    except Exception as e:
        return {"status": "error", "message": f"处理异常: {str(e)}"}

@router.post("/api/auth/reset")
async def reset_auth(req: ResetAuthReq, token: str = Depends(verify_token)):
    keys_to_delete = []
    if req.clear_license:
        keys_to_delete.append('auth_license_file')
    if req.clear_hwid:
        keys_to_delete.append('auth_hwid_data')
    if req.clear_lease:
        keys_to_delete.append('auth_lease_data')

    if not keys_to_delete:
        return {"status": "error", "message": "未选择任何需要清除的项目"}
    if db_manager.delete_sys_kvs(keys_to_delete):
        return {"status": "success", "message": "选中的授权凭据已成功重置，请重启程序。"}
    else:
        return {"status": "error", "message": "数据库删除操作失败"}

@router.post("/api/image_accounts/upgrade_oauth")
async def api_upgrade_image_oauth(req: UpgradeOAuthReq, token: str = Depends(verify_token)):
    from global_state import engine
    target_accounts = []

    if req.emails == "ALL":
        all_accs = db_manager.get_image_accounts_page(1, 999999)["data"]
        for a in all_accs:
            acc_token = ""
            raw_token = a.get("token_data", "{}")
            if raw_token:
                try:
                    td = json.loads(raw_token) if isinstance(raw_token, str) else raw_token
                    acc_token = td.get("access_token", "")
                    device_id = td.get("device_id", "")
                    user_agent = td.get("user_agent", "")
                except Exception:
                    pass

            target_accounts.append({
                "email": a["email"],
                "password": a["password"],
                "access_token": acc_token,
                "device_id": device_id,
                "user_agent": user_agent
            })
    else:
        for email in req.emails:
            info = db_manager.get_account_full_info(email)
            if info:
                acc_token = ""
                raw_token = info.get("token_data", "{}")
                if raw_token:
                    try:
                        td = json.loads(raw_token) if isinstance(raw_token, str) else raw_token
                        acc_token = td.get("access_token", "")
                        device_id = td.get("device_id", "")
                        user_agent = td.get("user_agent", "")
                    except Exception:
                        pass

                target_accounts.append({
                    "email": email,
                    "password": info.get("password"),
                    "access_token": acc_token,
                    "device_id": device_id,
                    "user_agent": user_agent
                })

    if not target_accounts:
        return {"status": "error", "message": "未找到可处理的账号"}

    class DummyArgs:
        pass

    args = DummyArgs()
    args.proxy = cfg.DEFAULT_PROXY if getattr(cfg, 'DEFAULT_PROXY', '').strip() else None
    ok, msg = engine.start_oauth_upgrade(args, target_accounts)

    if ok:
        return {"status": "success", "count": len(target_accounts), "message": msg}
    else:
        return {"status": "error", "message": msg}
