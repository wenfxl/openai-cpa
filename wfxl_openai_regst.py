import asyncio
import os
import yaml
import uvicorn
import secrets
import glob
import json
import time
import sys
import random
import string
import urllib.request
import urllib.parse
import subprocess
from collections import deque
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends, Header, Query, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse,StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from cloudflare import Cloudflare, APIError
from contextlib import asynccontextmanager
from utils import core_engine
from utils.config import reload_all_configs
from utils import db_manager
from utils.sub2api_client import Sub2APIClient
from playwright.sync_api import sync_playwright

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    print("\n" + "="*65, flush=True)
    print("🛑 接收到系统终止信号，正在强制结束引擎...", flush=True)
    
    try:
        if engine.is_running():
            engine.stop()
    except Exception:
        pass
        
    print("💥 已强制斩断所有底层连接，进程秒退！", flush=True)
    print("="*65 + "\n", flush=True)
    os._exit(0)

app = FastAPI(title="Wenfxl Codex Manager", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = core_engine.RegEngine()
db_manager.init_db()
log_history = deque(maxlen=500)
VALID_TOKENS = set()

class DummyArgs:
    def __init__(self, proxy=None, once=False):
        self.proxy = proxy
        self.once = once
        
class ExportReq(BaseModel):
    emails: list[str]

class DeleteReq(BaseModel):
    emails: list[str]

class LoginData(BaseModel):
    password: str

class GenerateSubReq(BaseModel):
    main_domains: str
    count: int
    api_email: str
    api_key: str
    sync: bool
    level: int = 1
    
class CFSyncExistingReq(BaseModel):
    sub_domains: str
    api_email: str
    api_key: str

class CFDeleteExistingReq(BaseModel):
    sub_domains: str
    api_email: str
    api_key: str
    
class CFQueryReq(BaseModel):
    main_domains: str
    api_email: str
    api_key: str

class LuckMailBulkBuyReq(BaseModel):
    quantity: int
    auto_tag: bool
    config: dict

class SMSPriceReq(BaseModel):
    service: str = "openai"

def get_web_password():
    try:
        if os.path.exists("config.yaml"):
            with open("config.yaml", "r", encoding="utf-8") as f:
                c = yaml.safe_load(f) or {}
                return str(c.get("web_password", "admin")).strip()
    except Exception:
        pass
    return "admin"

async def verify_token(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未提供有效凭证")
    token = authorization.split(" ")[1]
    if token not in VALID_TOKENS:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    return token

def dispatch_email_backend_add(domain_name: str, cf_cfg: dict):
    email_api_mode = cf_cfg.get("email_api_mode", "")
    enable_sub_domains = cf_cfg.get("enable_sub_domains", False)

    if not enable_sub_domains:
        return

def dispatch_email_backend_delete(domain_name: str, cf_cfg: dict):
    email_api_mode = cf_cfg.get("email_api_mode", "")
    enable_sub_domains = cf_cfg.get("enable_sub_domains", False)

    if not enable_sub_domains:
        return

@app.post("/api/login")
async def login(data: LoginData):
    correct_pwd = get_web_password()
    if data.password == correct_pwd:
        token = secrets.token_hex(16)
        VALID_TOKENS.add(token)
        return {"status": "success", "token": token}
    return {"status": "error", "message": "密码错误"}

@app.get("/api/status")
async def get_status(token: str = Depends(verify_token)):
    return {"is_running": engine.is_running()}

@app.post("/api/start")
async def start_task(token: str = Depends(verify_token)):
    if engine.is_running():
        return {"status": "error", "message": "任务已经在运行中！"}
    
    try: reload_all_configs()
    except Exception as e: print(f"[{core_engine.ts()}] [警告] 启动重载提示: {e}")
        
    default_proxy = getattr(core_engine.cfg, 'DEFAULT_PROXY', None)
    args = DummyArgs(proxy=default_proxy if default_proxy else None)

    core_engine.run_stats["success"] = 0
    core_engine.run_stats["failed"] = 0
    core_engine.run_stats["retries"] = 0
    core_engine.run_stats["start_time"] = time.time()

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

@app.post("/api/accounts/export_selected")
async def export_selected_accounts(req: ExportReq, token: str = Depends(verify_token)):
    try:
        if not req.emails:
            return {"status": "error", "message": "未收到任何要导出的账号"}
            
        tokens = db_manager.get_tokens_by_emails(req.emails)
        
        if not tokens:
            return {"status": "error", "message": "未能提取到选中账号的有效 Token"}
            
        return {"status": "success", "data": tokens}
    except Exception as e:
        return {"status": "error", "message": f"导出失败: {str(e)}"}

@app.post("/api/accounts/delete")
async def delete_selected_accounts(req: DeleteReq, token: str = Depends(verify_token)):
    try:
        if not req.emails:
            return {"status": "error", "message": "未收到任何要删除的账号"}
            
        success = db_manager.delete_accounts_by_emails(req.emails)
        
        if success:
            return {"status": "success", "message": f"成功删除 {len(req.emails)} 个账号"}
        else:
            return {"status": "error", "message": "删除操作失败"}
    except Exception as e:
        return {"status": "error", "message": f"删除异常: {str(e)}"}

@app.get("/api/stats")
async def get_stats(token: str = Depends(verify_token)):
    stats = core_engine.run_stats
    is_running = engine.is_running()
    
    elapsed = round(time.time() - stats["start_time"], 1) if (is_running and stats["start_time"] > 0) else 0
    total_attempts = stats["success"] + stats["failed"]
    success_rate = round((stats["success"] / total_attempts * 100), 2) if total_attempts > 0 else 0.0
    avg_time = round(elapsed / stats["success"], 1) if stats["success"] > 0 else 0.0
    
    progress_pct = 0
    if stats["target"] > 0:
        progress_pct = min(100, round((stats["success"] / stats["target"]) * 100, 1))
    elif stats["success"] > 0:
        progress_pct = 100

    if getattr(core_engine.cfg, 'ENABLE_CPA_MODE', False):
        current_mode = "CPA 仓管"
    elif getattr(core_engine.cfg, 'ENABLE_SUB2API_MODE', False):
        current_mode = "Sub2Api 仓管"
    else:
        current_mode = "常规量产"
    return {
        "success": stats["success"],
        "failed": stats["failed"],
        "retries": stats["retries"],
        "total": total_attempts,
        "target": stats["target"] if stats["target"] > 0 else "∞",
        "success_rate": f"{success_rate}%",
        "elapsed": f"{elapsed}s",
        "avg_time": f"{avg_time}s",
        "progress_pct": f"{progress_pct}%",
        "is_running": is_running,
        "mode": current_mode
    }

@app.post("/api/stop")
async def stop_task(token: str = Depends(verify_token)):
    if not engine.is_running():
        return {"status": "warning", "message": "当前没有运行的任务"}
    engine.stop()
    return {"status": "success", "message": "已发送停止指令，正在安全退出..."}

@app.get("/api/config")
async def get_config(token: str = Depends(verify_token)):
    config_path = "config.yaml"
    config_data = {}
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            config_data = yaml.safe_load(f) or {}
    config_data["web_password"] = config_data.get("web_password", "admin")
    return config_data

@app.post("/api/config")
async def save_config(new_config: dict, token: str = Depends(verify_token)):
    config_path = "config.yaml"
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(new_config, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
        try: reload_all_configs()
        except Exception: pass
        return {"status": "success", "message": "✅ 配置已成功保存！"}
    except Exception as e:
        return {"status": "error", "message": f"❌ 保存失败: {str(e)}"}


@app.post("/api/config/add_wildcard_dns")
async def add_wildcard_dns(req: CFSyncExistingReq, token: str = Depends(verify_token)):
    try:
        main_list = [d.strip() for d in req.sub_domains.split(",") if d.strip()]
        
        if not main_list:
            return {"status": "error", "message": "❌ 没有找到有效的主域名"}

        cf = Cloudflare(api_email=req.api_email, api_key=req.api_key)
        def process_single_domain(domain):
            try:
                zones = cf.zones.list(name=domain)
                if not zones.result:
                    return False
                
                zone_id = zones.result[0].id
                records = [
                    {"type": "MX", "name": "*", "content": "route3.mx.cloudflare.net", "priority": 36},
                    {"type": "MX", "name": "*", "content": "route2.mx.cloudflare.net", "priority": 25},
                    {"type": "MX", "name": "*", "content": "route1.mx.cloudflare.net", "priority": 51},
                    {"type": "TXT", "name": "*", "content": '"v=spf1 include:_spf.mx.cloudflare.net ~all"'}
                ]

                for rec in records:
                    try:
                        cf.dns.records.create(zone_id=zone_id, **rec, ttl=300)
                    except APIError as e:
                        if e.code == 81057: continue 
                        print(f"[{domain}] 报错: {e.message}")
                return True
            except:
                return False
        tasks = [asyncio.to_thread(process_single_domain, dom) for dom in main_list]
        results = await asyncio.gather(*tasks)

        success_count = sum(1 for r in results if r)
        return {
            "status": "success",
            "message": f"🚀 批量配置完成！成功处理 {success_count}/{len(main_list)} 个域名的泛解析记录。"
        }

    except Exception as e:
        return {"status": "error", "message": f"执行异常: {str(e)}"}

# @router.post("/api/config/add_wildcard_dns")
# async def add_wildcard_dns_api(req: GenerateSubReq, token: str = Depends(verify_token)):
    # try:
        # cf = Cloudflare(api_email=api_email, api_key=api_key)
        # main_list = [d.strip() for d in req.main_domains.split(",") if d.strip()]
        # if not main_list:
            # return {"status": "error", "message": "请填写主域名！"}

        # # 2. 获取 API 配置 (从请求或配置中读取)
        # # 建议统一使用 API Token，如果用 API Key 需要修改 headers
        # api_token = req.api_key  
        
        # all_tasks = []

        # # 3. 遍历主域名获取 Zone ID 并创建任务
        # # 注意：为了效率，获取 Zone ID 也可以放到线程中
        # for main_dom in main_list:
            # # 这里的获取 Zone ID 逻辑可以沿用你之前的
            # # 假设你使用的是 Cloudflare 官方库或 Requests
            # # zones = cf.zones.list(name=main_dom)
            
            # zone_id = "从API获取到的ID" 
            
            # if not zone_id: continue
            # all_tasks.append(asyncio.to_thread(
                # cloudflare_dns_worker, api_token, zone_id, "MX", "*", "mail.example.com", 10
            # ))
            # all_tasks.append(asyncio.to_thread(
                # cloudflare_dns_worker, api_token, zone_id, "TXT", "*", "v=spf1 include:_spf.google.com ~all"
            # ))

        # if all_tasks:
            # results = await asyncio.gather(*all_tasks)
            # success_count = sum(1 for r in results if r)
        # else:
            # success_count = 0

        # return {
            # "status": "success",
            # "message": f"🚀 泛域名解析处理完成！成功下发 {success_count} 条记录。"
        # }

    # except Exception as e:
        # return {"status": "error", "message": f"执行异常: {str(e)}"}

# @app.post("/api/config/generate_subdomains")
# async def generate_subdomains_api(req: GenerateSubReq, token: str = Depends(verify_token)):
    # try:
        # main_list = [d.strip() for d in req.main_domains.split(",") if d.strip()]
        # if not main_list:
            # return {"status": "error", "message": "请先在界面上方填写主域名池！"}

        # level = getattr(req, 'level', 1)

        # generated = []
        # for main_dom in main_list:
            # for _ in range(req.count):
                # random_parts = []
                # for _ in range(level):
                    # random_parts.append(''.join(random.choices(string.ascii_lowercase + string.digits, k=8)))
                
                # full_sub = ".".join(random_parts) + f".{main_dom}"
                # generated.append(full_sub)
                
        # generated_str = ",".join(generated)
        # config_path = "config.yaml"
        # try:
            # with open(config_path, "r", encoding="utf-8") as f:
                # c = yaml.safe_load(f) or {}
        # except Exception:
            # c = {}
            
        # c["sub_domains_list"] = generated_str
        # c["sub_domain_count"] = req.count
        # c["sub_domain_level"] = level
        # c["cf_api_email"] = req.api_email
        # c["cf_api_key"] = req.api_key
        # c["enable_sub_domains"] = True
        
        # with open(config_path, "w", encoding="utf-8") as f:
            # yaml.dump(c, f, allow_unicode=True, sort_keys=False, default_flow_style=False)

        # try: reload_all_configs()
        # except Exception: pass

        # return {
            # "status": "success", 
            # "domains": generated_str, 
            # "message": f"成功生成 {len(generated)} 个 {level + 1}级 域名，并已自动保存至系统！"
        # }
    # except Exception as e:
        # return {"status": "error", "message": f"执行异常: {str(e)}"}

# @app.post("/api/config/sync_cf_domains")
# async def sync_cf_domains_api(req: CFSyncExistingReq, token: str = Depends(verify_token)):
    # try:
        # sub_list = [d.strip() for d in req.sub_domains.split(",") if d.strip()]
        # if not sub_list:
            # return {"status": "error", "message": "没有需要同步的域名"}

        # cf_cfg = getattr(core_engine.cfg, '_c', {})
        # configured_main_domains = [d.strip() for d in cf_cfg.get("mail_domains", "").split(",") if d.strip()]
        # cf = Cloudflare(api_email=req.api_email, api_key=req.api_key)

        # main_domains_map = {}
        # for sub in sub_list:
            # for main in configured_main_domains:
                # if sub.endswith(main):
                    # main_domains_map.setdefault(main, []).append(sub)
                    # break

        # def create_dns_record(zone_id, name):
            # try:
                # cf.email_routing.dns.create(zone_id=zone_id, name=name)
            # except Exception:
                # pass 

            # dispatch_email_backend_add(name, cf_cfg)
            # return True

        # all_tasks = []
        # for main_dom, subs in main_domains_map.items():
            # zones = await asyncio.to_thread(cf.zones.list, name=main_dom)
            # if not zones.result: continue
            # zone_id = zones.result[0].id
            
            # for full_sub in subs:
                # all_tasks.append(asyncio.to_thread(create_dns_record, zone_id, full_sub))

        # success_count = 0
        # if all_tasks:
            # results = await asyncio.gather(*all_tasks)
            # success_count = sum(1 for r in results if r)
        
        # return {
            # "status": "success", 
            # "message": f"🚀 同步完成！成功处理了 {success_count} 个域名的路由与后端下发。"
        # }
    # except Exception as e:
        # return {"status": "error", "message": f"执行异常: {str(e)}"}
        
@app.get("/api/config/cf_global_status")
async def get_cf_global_status(main_domain: str, token: str = Depends(verify_token)):
    try:
        cf_cfg = getattr(core_engine.cfg, '_c', {})
        api_email = cf_cfg.get("cf_api_email")
        api_key = cf_cfg.get("cf_api_key")

        if not api_email or not api_key:
            return {"status": "error", "message": "未配置 CF 账号信息"}

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
                if val is None and hasattr(obj, 'result'):
                    val = getattr(obj.result, attr, None)
                return val if val is not None else default
            raw_status = safe_get(routing_info, 'status', 'unknown')
            raw_synced = safe_get(routing_info, 'synced', False)

            is_enabled = (raw_status == 'ready' and raw_synced is True)
            
            dns_ui_status = "active" if raw_synced else "pending"

            results.append({
                "domain": dom,
                "is_enabled": is_enabled,
                "dns_status": dns_ui_status
            })

        return {"status": "success", "data": results}
    except Exception as e:
        return {"status": "error", "message": f"状态同步失败: {str(e)}"}

@app.post("/api/config/delete_cf_domains")
async def delete_cf_domains_api(req: CFDeleteExistingReq, token: str = Depends(verify_token)):
    try:
        sub_list = [d.strip() for d in req.sub_domains.split(",") if d.strip()]
        if not sub_list:
            return {"status": "error", "message": "没有需要删除的域名"}

        cf_cfg = getattr(core_engine.cfg, '_c', {})
        configured_main_domains = [d.strip() for d in cf_cfg.get("mail_domains", "").split(",") if d.strip()]
        cf = Cloudflare(api_email=req.api_email, api_key=req.api_key)

        main_domains_map = {}
        for sub in sub_list:
            for main in configured_main_domains:
                if sub.endswith(main):
                    main_domains_map.setdefault(main, []).append(sub)
                    break

        def do_delete(zone_id, full_sub):
            try:
                url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/email/routing/dns"
                headers = {
                    "X-Auth-Email": req.api_email,
                    "X-Auth-Key": req.api_key,
                    "Content-Type": "application/json"
                }
                payload = json.dumps({"name": full_sub}).encode('utf-8')
                request = urllib.request.Request(url, data=payload, headers=headers, method="DELETE")
                urllib.request.urlopen(request, timeout=10)
                dispatch_email_backend_delete(full_sub, cf_cfg)
                return full_sub 
            except:
                return None

        all_tasks = []
        for main_dom, subs in main_domains_map.items():
            zones = await asyncio.to_thread(cf.zones.list, name=main_dom)
            if not zones.result: continue
            zone_id = zones.result[0].id
            
            for full_sub in subs:
                all_tasks.append(asyncio.to_thread(do_delete, zone_id, full_sub))

        success_list = []
        if all_tasks:
            results = await asyncio.gather(*all_tasks)
            success_list = [r for r in results if r]
        
        if success_list:
            config_path = "config.yaml"
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    c = yaml.safe_load(f) or {}
                
                existing_str = c.get("sub_domains_list", "")
                if existing_str:
                    existing_list = [d.strip() for d in existing_str.split(",") if d.strip()]
                    new_list = [d for d in existing_list if d not in success_list]
                    c["sub_domains_list"] = ",".join(new_list)
                    
                    with open(config_path, "w", encoding="utf-8") as f:
                        yaml.dump(c, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
                    try: reload_all_configs()
                    except: pass
            except: pass

        return {
            "status": "success", 
            "message": f"🚀 清理完成！成功从全端卸载了 {len(success_list)} 个路由记录。"
        }
    except Exception as e:
        return {"status": "error", "message": f"执行异常: {str(e)}"}
        
@app.get("/api/accounts")
async def get_accounts(page: int = Query(1), page_size: int = Query(50), token: str = Depends(verify_token)):
    result = db_manager.get_accounts_page(page, page_size)
    return {
        "status": "success", 
        "data": result["data"],
        "total": result["total"],
        "page": page,
        "page_size": page_size
    }

@app.post("/api/account/action")
async def account_action(data: dict, token: str = Depends(verify_token)):
    email = data.get("email")
    action = data.get("action")
    config = getattr(core_engine.cfg, '_c', {})
    
    token_data = db_manager.get_token_by_email(email)
    if not token_data:
        return {"status": "error", "message": f"未找到 {email} 的 Token。"}

    if action == "push":
        if not config.get("cpa_mode", {}).get("enable", False):
            return {"status": "error", "message": "🚫 推送失败：未开启 CPA 模式！"}
        
        success, msg = core_engine.upload_to_cpa_integrated(
            token_data, 
            config.get("cpa_mode", {}).get("api_url", ""), 
            config.get("cpa_mode", {}).get("api_token", "")
        )
        if success:
            return {"status": "success", "message": f"账号 {email} 已成功推送到 CPA！"}
        return {"status": "error", "message": f"CPA 推送失败: {msg}"}

    elif action == "push_sub2api":
        if not getattr(core_engine.cfg, 'ENABLE_SUB2API_MODE', False):
            return {"status": "error", "message": "🚫 推送失败：未开启 Sub2API 模式！"}
            
        client = Sub2APIClient(api_url=core_engine.cfg.SUB2API_URL, 
                               api_key=core_engine.cfg.SUB2API_KEY)
        
        success, resp = client.add_account(token_data)
        if success:
            return {"status": "success", "message": f"账号 {email} 已同步至 Sub2API！"}
        return {"status": "error", "message": f"Sub2API 推送失败: {resp}"}
            
@app.post("/api/config/query_cf_domains")
async def query_cf_domains_api(req: CFQueryReq, token: str = Depends(verify_token)):
    try:
        main_list = [d.strip() for d in req.main_domains.split(",") if d.strip()]
        if not main_list:
            return {"status": "error", "message": "请先填写主域名池！"}

        cf = Cloudflare(api_email=req.api_email, api_key=req.api_key)
        found_subdomains = set()
        errors = []

        for main_dom in main_list:
            try:
                zones = cf.zones.list(name=main_dom)
                if not zones.result:
                    errors.append(f"找不到 {main_dom} 的 Zone ID")
                    continue
                zone_id = zones.result[0].id

                dns_records = cf.dns.records.list(zone_id=zone_id, type="MX")

                for record in dns_records:
                    if "cloudflare.net" in str(record.content).lower():
                        if record.name != main_dom:
                            found_subdomains.add(record.name)
                            
            except Exception as e:
                errors.append(f"查询 {main_dom} 时异常: {str(e)}")

        if not found_subdomains and errors:
            return {"status": "error", "message": f"查询失败: {errors[0]}"}

        result_list = list(found_subdomains)
        
        return {
            "status": "success", 
            "domains": ",".join(result_list), 
            "message": f"成功从 CF 线上拉取到 {len(result_list)} 个已配置邮件路由的子域名！"
        }
    except Exception as e:
        return {"status": "error", "message": f"执行异常: {str(e)}"}

@app.post("/api/logs/clear")
async def clear_backend_logs(token: str = Depends(verify_token)):
    """清空后端的历史日志缓存"""
    log_history.clear()
    return {"status": "success"}

@app.get("/api/logs/stream")
async def stream_logs(request: Request, token: str = Query(None)):
    if token not in VALID_TOKENS:
        raise HTTPException(status_code=401, detail="Unauthorized")

    async def log_generator():
        for old_msg in log_history:
            yield f"data: {old_msg}\n\n"
        
        try:
            while True:
                if await request.is_disconnected():
                    break
                    
                if not core_engine.log_queue.empty():
                    msg = core_engine.log_queue.get_nowait()
                    log_history.append(msg)
                    yield f"data: {msg}\n\n"
                else:
                    await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            pass

    return StreamingResponse(log_generator(), media_type="text/event-stream")

@app.get("/")
async def get_dashboard():
    html_path = os.path.join(os.path.dirname(__file__), "index.html")
    if not os.path.exists(html_path):
        return HTMLResponse(content="<h1>找不到 index.html</h1>", status_code=404)
    with open(html_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


def check_chromium_installed():
    """
    检测逻辑：判断 Playwright 所需的 Chromium 是否已安装。
    """
    try:
        with sync_playwright() as p:
            executable_path = p.chromium.executable_path

            if os.path.exists(executable_path):
                return True
            else:
                print(f"[{core_engine.ts()}] [ERROR] 运行环境异常！")
                print("=========================================")
                print(f"[{core_engine.ts()}] [ERROR] 请在终端中手动运行以下命令完成安装：")
                print(f"[{core_engine.ts()}] [ERROR] playwright install --with-deps chromium")
                print("=========================================")
                return False

    except Exception as e:
        # 捕获 Playwright 异常
        print(f"[{core_engine.ts()}] [ERROR] 运行环境异常！")
        print(f"[{core_engine.ts()}] [ERROR] 请在终端中手动运行以下命令完成安装：")
        print(f"[{core_engine.ts()}] [ERROR] playwright install --with-deps chromium")
        return False

# 余额查询接口示例
@app.get('/api/sms/balance')
async def api_get_sms_balance(token: str = Depends(verify_token)):
    from utils.hero_sms import hero_sms_get_balance
    proxy_url = core_engine.cfg.DEFAULT_PROXY

    # 2. 构造代理字典 (curl_cffi 需要这种映射格式)
    proxies = {
        "http": proxy_url,
        "https": proxy_url
    } if proxy_url else None
    # 获取当前配置的代理 (如果需要走系统代理，这里可以传入 core_engine.cfg.DEFAULT_PROXY)
    balance, err = hero_sms_get_balance(proxies=proxies)
    if balance >= 0:
        return {"status": "success", "balance": f"{balance:.2f}"}
    return {"status": "error", "message": err}

# 库存价格查询接口
@app.post('/api/sms/prices')
async def api_get_sms_prices(req: SMSPriceReq, token: str = Depends(verify_token)):
    from utils.hero_sms import _hero_sms_prices_by_service
    proxy_url = core_engine.cfg.DEFAULT_PROXY

    # 2. 构造代理字典 (curl_cffi 需要这种映射格式)
    proxies = {
        "http": proxy_url,
        "https": proxy_url
    } if proxy_url else None
    rows = _hero_sms_prices_by_service(req.service, proxies=proxies)
    if rows:
        return {"status": "success", "prices": rows}
    return {"status": "error", "message": "无法获取价格或当前服务无库存"}


@app.post("/api/luckmail/bulk_buy")
async def api_luckmail_bulk_buy(req: LuckMailBulkBuyReq, token: str = Depends(verify_token)):
    try:
        from utils.luckmail_service import LuckMailService
        lm_service = LuckMailService(
            api_key=req.config.get("api_key"),
            preferred_domain=req.config.get("preferred_domain", ""),
            email_type=req.config.get("email_type", "ms_graph"),
            variant_mode=req.config.get("variant_mode", "")
        )

        tag_id = req.config.get("tag_id") or lm_service.get_or_create_tag_id("已使用")

        results = lm_service.bulk_purchase(
            quantity=req.quantity,
            auto_tag=req.auto_tag,
            tag_id=tag_id
        )
        return {"status": "success", "message": f"成功购买 {len(results)} 个邮箱！", "data": results}
    except Exception as e:
        return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    try: reload_all_configs()
    except: pass

    print("=" * 65)
    print(f"[{core_engine.ts()}] [系统] OpenAI 无限注册 & CPA 智能仓管")
    print(f"[{core_engine.ts()}] [系统] Author: (wenfxl)轩灵")
    print(f"[{core_engine.ts()}] [系统] 如果遇到问题请更换域名解决，目前eu.cc，xyz，cn，edu.cc等常见域名均不可用，请更换为冷门域名")
    print("-" * 65)
    check_chromium_installed()
    print(f"[{core_engine.ts()}] [系统] Web 控制台已准备就绪，等待下发指令...")
    sys.__stdout__.write(f"[{core_engine.ts()}] [系统] 控制台地址：http://127.0.0.1:8000 \n")
    sys.__stdout__.write(f"[{core_engine.ts()}] [系统] 控制台初始密码：admin \n")
    sys.__stdout__.write(f"[{core_engine.ts()}] [系统] 结束请猛猛重复按CTRL+C \n")
    sys.__stdout__.flush()
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning", access_log=False, timeout_graceful_shutdown=1)