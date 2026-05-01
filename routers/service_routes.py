import os
import asyncio
import httpx
import json
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from cloudflare import Cloudflare
from curl_cffi import requests as cffi_requests

from global_state import verify_token
from utils import core_engine
import utils.config as cfg
import utils.integrations.clash_manager as clash_manager
from utils.email_providers.gmail_oauth_handler import GmailOAuthHandler

router = APIRouter()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GMAIL_VERIFIER_PATH = os.path.join(BASE_DIR, "data", "temp_verifier.txt")

class CFSyncExistingReq(BaseModel): sub_domains: str; api_email: str; api_key: str
class LuckMailBulkBuyReq(BaseModel): quantity: int; auto_tag: bool; config: dict
class GmailExchangeReq(BaseModel): code: str
class ClashDeployReq(BaseModel): count: int
class ClashUpdateReq(BaseModel): sub_url: str; target: str = "all"
class TestTgReq(BaseModel):token: str; chat_id: str
class GmailCredentialsReq(BaseModel):content: str

class CFDeployWorkerReq(BaseModel):api_email: str; api_key: str; worker_name: str; webhook_url: str; webhook_secret: str
class CFZoneBaseReq(BaseModel):domains: str; api_email: str; api_key: str
class CFSetupRoutingReq(CFZoneBaseReq):worker_name: str


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



@router.post("/api/gmail/upload_credentials")
async def upload_gmail_credentials(req: GmailCredentialsReq, token: str = Depends(verify_token)):
    if not req.content:
        return {"status": "error", "message": "内容为空"}
    try:
        json.loads(req.content)
        from utils import db_manager
        db_manager.set_sys_kv('gmail_credentials_json', req.content)
        return {"status": "success", "message": "Gmail 凭据已成功存入数据库！"}
    except Exception as e:
        return {"status": "error", "message": f"非法的 JSON 格式: {str(e)}"}

@router.post("/api/gmail/clear_credentials")
async def clear_gmail_credentials(token: str = Depends(verify_token)):
    from utils import db_manager
    if db_manager.delete_sys_kvs(['gmail_credentials_json']):
        return {"status": "success", "message": "Gmail 凭据已从数据库清除"}
    return {"status": "error", "message": "清除失败"}

@router.get("/api/gmail/auth_url")
async def get_gmail_auth_url(token: str = Depends(verify_token)):
    from utils import db_manager
    creds_str = db_manager.get_sys_kv('gmail_credentials_json')
    if not creds_str:
        return {"status": "error", "message": "❌ 未在云端数据库找到凭证！请先上传 credentials.json"}
    try:
        creds_dict = json.loads(creds_str)
        url, verifier = GmailOAuthHandler.get_authorization_url(creds_dict)
        with open(GMAIL_VERIFIER_PATH, "w") as f:
            f.write(verifier)
        return {"status": "success", "url": url}
    except Exception as e:
        return {"status": "error", "message": f"生成链接失败: {str(e)}"}


@router.post("/api/gmail/exchange_code")
async def exchange_gmail_code(req: GmailExchangeReq, token: str = Depends(verify_token)):
    if not req.code: return {"status": "error", "message": "授权码不能为空"}

    from utils import db_manager
    creds_str = db_manager.get_sys_kv('gmail_credentials_json')
    if not creds_str:
        return {"status": "error", "message": "❌ 请先上传 credentials.json"}

    try:
        if not os.path.exists(GMAIL_VERIFIER_PATH): return {"status": "error", "message": "会话已过期"}
        with open(GMAIL_VERIFIER_PATH, "r") as f:
            stored_verifier = f.read().strip()
        success, result_data = GmailOAuthHandler.save_token_from_code(
            json.loads(creds_str), req.code, None,
            code_verifier=stored_verifier,
            proxy=getattr(core_engine.cfg, 'DEFAULT_PROXY', None)
        )

        if success:
            db_manager.set_sys_kv('gmail_token_json', result_data)
            if os.path.exists(GMAIL_VERIFIER_PATH): os.remove(GMAIL_VERIFIER_PATH)
            return {"status": "success", "message": "🎉 Gmail 永久授权成功并已存入云端数据库！"}

        return {"status": "error", "message": result_data}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/api/gmail/clear_token")
async def clear_gmail_token(token: str = Depends(verify_token)):
    from utils import db_manager
    if db_manager.delete_sys_kvs(['gmail_token_json']):
        return {"status": "success", "message": "Gmail 授权 Token 已从数据库清除"}
    return {"status": "error", "message": "清除失败"}

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


@router.post("/api/notify/test_tg")
async def test_tg_notification(req: TestTgReq, token: str = Depends(verify_token)):
    if not req.token or not req.chat_id:
        return {"status": "error", "message": "请先填写 Bot Token 和 Chat ID"}
    proxy_url = getattr(cfg, 'DEFAULT_PROXY', None)
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None

    url = f"https://api.telegram.org/bot{req.token}/sendMessage"
    payload = {
        "chat_id": req.chat_id,
        "text": "🎉 *Wenfxl Manager*\n\n✅ 恭喜！Telegram 机器人通信完全正常。\n您的代理和参数配置正确！",
        "parse_mode": "Markdown"
    }

    try:
        res = cffi_requests.post(url, json=payload, proxies=proxies, timeout=15, impersonate="chrome110")
        data = res.json()
        if data.get("ok"):
            return {"status": "success", "message": "测试消息已成功发出！请检查 Telegram。"}
        else:
            return {"status": "error", "message": f"TG 接口报错: {data.get('description', '未知错误')}"}
    except Exception as e:
        return {"status": "error", "message": f"网络通信异常，请检查代理: {str(e)}"}


@router.post("/api/cloudflare/add_zones")
async def cloudflare_add_zones(req: CFZoneBaseReq, token: str = Depends(verify_token)):
    try:
        domain_list = [d.strip() for d in req.domains.split(",") if d.strip()]
        if not domain_list:
            return {"status": "error", "message": "没有找到有效的域名"}

        headers = {
            "X-Auth-Email": req.api_email,
            "X-Auth-Key": req.api_key,
            "Content-Type": "application/json"
        }
        proxy_url = getattr(core_engine.cfg, 'DEFAULT_PROXY', None)
        client_kwargs = {"timeout": 30.0, "proxy": proxy_url} if proxy_url else {"timeout": 30.0}

        results = []
        async with httpx.AsyncClient(**client_kwargs) as client:
            acc_resp = await client.get("https://api.cloudflare.com/client/v4/accounts", headers=headers)
            acc_data = acc_resp.json()
            if not acc_data.get("success") or not acc_data.get("result"):
                return {"status": "error", "message": f"无法获取 CF Account ID，请检查 API 凭证: {acc_data.get('errors')}"}

            account_id = acc_data["result"][0]["id"]
            for domain in domain_list:
                print(f"[{core_engine.ts()}] [CF 托管] 正在检查域名状态: {domain}")
                check_resp = await client.get(f"https://api.cloudflare.com/client/v4/zones?name={domain}",
                                              headers=headers)
                check_data = check_resp.json()
                if check_data.get("success") and len(check_data.get("result", [])) > 0:
                    zone_info = check_data["result"][0]
                    results.append({
                        "domain": domain,
                        "status": zone_info.get("status"),
                        "name_servers": zone_info.get("name_servers", []),
                        "msg": "✅ 域名已托管，无需重复操作"
                    })
                    print(f"[{core_engine.ts()}] [CF 托管] ✅ [{domain}] 已经在 CF 账号中，无需重复添加。")
                    continue

                print(f"[{core_engine.ts()}] [CF 托管] 正在为 [{domain}] 申请托管及 NS 分配...")
                payload = {
                    "name": domain,
                    "account": {"id": account_id},
                    "type": "full",
                    "jump_start": True
                }
                add_resp = await client.post("https://api.cloudflare.com/client/v4/zones", headers=headers,
                                             json=payload)
                add_data = add_resp.json()

                if add_data.get("success"):
                    zone_info = add_data["result"]
                    results.append({
                        "domain": domain,
                        "status": zone_info.get("status"),
                        "name_servers": zone_info.get("name_servers", []),
                        "msg": "成功添加到 CF"
                    })
                    print(f"[{core_engine.ts()}] [CF 托管] 🎉 [{domain}] 托管成功！等待用户修改 NS。")
                else:
                    err_msg = str(add_data.get("errors", "未知错误"))
                    results.append({"domain": domain, "status": "error", "name_servers": [], "msg": err_msg})
                    print(f"[{core_engine.ts()}] [CF 托管] ❌ [{domain}] 托管失败: {err_msg}")

                await asyncio.sleep(0.5)

        return {"status": "success", "data": results}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/api/cloudflare/enable_email")
async def cloudflare_enable_email(req: CFZoneBaseReq, token: str = Depends(verify_token)):
    try:
        domain_list = [d.strip() for d in req.domains.split(",") if d.strip()]
        headers = {"X-Auth-Email": req.api_email, "X-Auth-Key": req.api_key, "Content-Type": "application/json"}
        proxy_url = getattr(core_engine.cfg, 'DEFAULT_PROXY', None)
        client_kwargs = {"timeout": 30.0, "proxy": proxy_url} if proxy_url else {"timeout": 30.0}

        results = []
        async with httpx.AsyncClient(**client_kwargs) as client:
            for domain in domain_list:
                print(f"[{core_engine.ts()}] [CF 邮件] 正在校验 NS 状态: {domain}")
                zone_resp = await client.get(f"https://api.cloudflare.com/client/v4/zones?name={domain}",
                                             headers=headers)
                zone_data = zone_resp.json()

                if not zone_data.get("success") or not zone_data.get("result"):
                    results.append({"domain": domain, "status": "error", "name_servers": [], "msg": "未在CF找到该域名"})
                    print(f"[{core_engine.ts()}] [CF 邮件] ❌ [{domain}] 未在当前 CF 账号中找到。")
                    continue

                zone_info = zone_data["result"][0]
                zone_id, ns_status = zone_info["id"], zone_info.get("status")

                if ns_status != "active":
                    results.append(
                        {"domain": domain, "status": ns_status, "name_servers": [], "msg": "NS 暂未生效，CF 尚未接管该域名"})
                    print(f"[{core_engine.ts()}] [CF 邮件] ⚠️ [{domain}] NS 尚未生效 (Pending)，跳过邮件激活。")
                    continue

                print(f"[{core_engine.ts()}] [CF 邮件] NS 已生效，正在尝试激活企业邮局...")
                email_resp = await client.post(
                    f"https://api.cloudflare.com/client/v4/zones/{zone_id}/email/routing/enable", headers=headers)
                email_data = email_resp.json()
                if email_resp.status_code == 200 and email_data.get("success"):
                    results.append({"domain": domain, "status": "active", "name_servers": [], "msg": "✅ CF 邮件服务已成功激活"})
                    print(f"[{core_engine.ts()}] [CF 邮件] 🎉 [{domain}] 邮件服务激活成功。")
                else:
                    err_msg = str(email_data.get("errors", []))
                    results.append(
                        {"domain": domain, "status": "error", "name_servers": [], "msg": f"❌ 激活失败: {err_msg}"})
                    print(f"[{core_engine.ts()}] [CF 邮件] ❌ [{domain}] 激活失败: {err_msg}")

                await asyncio.sleep(0.5)

        return {"status": "success", "data": results}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/api/cloudflare/deploy_worker")
async def cloudflare_deploy_worker(req: CFDeployWorkerReq, token: str = Depends(verify_token)):
    WORKER_RAW_URL = "https://raw.githubusercontent.com/wenfxl/openai-cpa-email/refs/heads/master/worker.js"
    try:
        proxy_url = getattr(core_engine.cfg, 'DEFAULT_PROXY', None)
        client_kwargs = {"timeout": 30.0, "proxy": proxy_url} if proxy_url else {"timeout": 30.0}

        async with httpx.AsyncClient(**client_kwargs) as client:
            headers = {"X-Auth-Email": req.api_email, "X-Auth-Key": req.api_key}

            print(f"[{core_engine.ts()}] [CF Worker] 正在连接 Cloudflare 获取 Account ID...")
            acc_resp = await client.get("https://api.cloudflare.com/client/v4/accounts", headers=headers)
            if not acc_resp.json().get("success"):
                return {"status": "error", "message": "无法获取 CF Account ID"}
            account_id = acc_resp.json()["result"][0]["id"]

            print(f"[{core_engine.ts()}] [CF Worker] 正在检查 Worker [{req.worker_name}] 是否已存在...")
            check_worker = await client.get(
                f"https://api.cloudflare.com/client/v4/accounts/{account_id}/workers/scripts/{req.worker_name}",
                headers=headers)
            if check_worker.status_code == 200:
                print(f"[{core_engine.ts()}] [CF Worker] ✅ Worker [{req.worker_name}] 已经存在，安全跳过覆盖部署。")
                return {"status": "success", "message": f"✅ Worker [{req.worker_name}] 已存在，无需重复部署"}

            print(f"[{core_engine.ts()}] [CF Worker] 正在从 Github 拉取最新源码...")
            code_resp = await client.get(WORKER_RAW_URL)
            if code_resp.status_code != 200:
                print(f"[{core_engine.ts()}] [CF Worker] ❌ 获取 Github 源码失败")
                return {"status": "error", "message": "获取 Github 源码失败"}

            metadata = {
                "main_module": "worker.js",
                "compatibility_date": "2024-03-01",
                "bindings": [
                    {"name": "EMAIL_WEBHOOK_URL", "type": "plain_text", "text": req.webhook_url},
                    {"name": "EMAIL_WEBHOOK_TIMEOUT_MS", "type": "plain_text", "text": "10000"},
                    {"name": "EMAIL_WEBHOOK_SECRET", "type": "secret_text", "text": req.webhook_secret}
                ]
            }

            files = {
                "metadata": (None, json.dumps(metadata), "application/json"),
                "worker.js": ("worker.js", code_resp.text, "application/javascript+module")
            }
            print(f"[{core_engine.ts()}] [CF Worker] 正在注入环境变量并推送至边缘节点...")
            deploy_resp = await client.put(
                f"https://api.cloudflare.com/client/v4/accounts/{account_id}/workers/scripts/{req.worker_name}",
                headers=headers, files=files)

            if deploy_resp.status_code == 200 and deploy_resp.json().get("success"):
                print(f"[{core_engine.ts()}] [CF Worker] 🎉 部署成功！环境变量已就位。")
                return {"status": "success", "message": "✅ 部署并绑定环境变量成功"}
            else:
                err_msg = str(deploy_resp.json().get("errors", "未知错误"))
                print(f"[{core_engine.ts()}] [CF Worker] ❌ 部署失败: {err_msg}")
                return {"status": "error", "message": err_msg}
    except Exception as e:
        print(f"[{core_engine.ts()}] [CF Worker] ❌ 发生异常: {str(e)}")
        return {"status": "error", "message": str(e)}


@router.post("/api/cloudflare/setup_catch_all")
async def cloudflare_setup_catch_all(req: CFSetupRoutingReq, token: str = Depends(verify_token)):
    try:
        domain_list = [d.strip() for d in req.domains.split(",") if d.strip()]
        headers = {"X-Auth-Email": req.api_email, "X-Auth-Key": req.api_key, "Content-Type": "application/json"}
        proxy_url = getattr(core_engine.cfg, 'DEFAULT_PROXY', None)
        client_kwargs = {"timeout": 30.0, "proxy": proxy_url} if proxy_url else {"timeout": 30.0}

        results = []
        async with httpx.AsyncClient(**client_kwargs) as client:
            for domain in domain_list:
                print(f"[{core_engine.ts()}] [CF 路由] 正在校验域名状态: {domain}")
                zone_resp = await client.get(f"https://api.cloudflare.com/client/v4/zones?name={domain}",
                                             headers=headers)
                zone_data = zone_resp.json()

                if not zone_data.get("success") or not zone_data.get("result"):
                    results.append({"domain": domain, "status": "error", "name_servers": [], "msg": "未找到域名"})
                    print(f"[{core_engine.ts()}] [CF 路由] ❌ [{domain}] 未在账号中找到。")
                    continue

                zone_info = zone_data["result"][0]
                zone_id, ns_status = zone_info["id"], zone_info.get("status")

                if ns_status != "active":
                    results.append({"domain": domain, "status": ns_status, "name_servers": [], "msg": "NS 暂未生效，无法配置路由"})
                    print(f"[{core_engine.ts()}] [CF 路由] ⚠️ [{domain}] NS 未生效，跳过配置。")
                    continue

                print(f"[{core_engine.ts()}] [CF 路由] 正在获取当前 Catch-All 规则...")
                catch_all_resp = await client.get(
                    f"https://api.cloudflare.com/client/v4/zones/{zone_id}/email/routing/rules/catch_all",
                    headers=headers)

                if catch_all_resp.status_code == 200:
                    ca_data = catch_all_resp.json()
                    if ca_data.get("success") and ca_data.get("result"):
                        rule = ca_data["result"]
                        if rule.get("enabled"):
                            actions = rule.get("actions", [])
                            if actions and actions[0].get("type") == "worker" and req.worker_name in actions[0].get(
                                    "value", []):
                                results.append({"domain": domain, "status": "active", "name_servers": [],
                                                "msg": f"✅ Catch-All 规则已指向: {req.worker_name}，无需重复操作"})
                                print(
                                    f"[{core_engine.ts()}] [CF 路由] ✅ [{domain}] 规则已经完美指向 Worker [{req.worker_name}]，跳过修改。")
                                continue

                print(f"[{core_engine.ts()}] [CF 路由] 正在将 Catch-All 指向 Worker: {req.worker_name}")
                catch_all_payload = {
                    "actions": [{"type": "worker", "value": [req.worker_name]}],
                    "matchers": [{"type": "catch_all"}],
                    "enabled": True,
                    "name": f"Catch-All to {req.worker_name}"
                }

                route_resp = await client.put(
                    f"https://api.cloudflare.com/client/v4/zones/{zone_id}/email/routing/rules/catch_all",
                    headers=headers, json=catch_all_payload)

                if route_resp.json().get("success"):
                    results.append({"domain": domain, "status": "active", "name_servers": [],
                                    "msg": f"✅ 成功! Catch-All 已指向: {req.worker_name}"})
                    print(f"[{core_engine.ts()}] [CF 路由] 🎉 [{domain}] 成功绑定邮件转发至 Worker!")
                else:
                    err_msg = route_resp.text
                    results.append({"domain": domain, "status": "active", "name_servers": [],
                                    "msg": f"❌ 路由配置失败: {err_msg}"})
                    print(f"[{core_engine.ts()}] [CF 路由] ❌ [{domain}] 配置失败: {err_msg}")

                await asyncio.sleep(0.5)

        return {"status": "success", "data": results}
    except Exception as e:
        print(f"[{core_engine.ts()}] [CF 路由] ❌ 发生异常: {str(e)}")
        return {"status": "error", "message": str(e)}