import os
import time
import secrets
import re
import asyncio
import threading
import sys
import subprocess
import httpx
import requests
import zipfile
import io
import shutil

from typing import Optional, Any
from fastapi import APIRouter, Depends, Query, Request, WebSocket, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from global_state import VALID_TOKENS, CLUSTER_NODES, NODE_COMMANDS, cluster_lock, log_history, engine, verify_token, worker_status, append_log
from utils import core_engine, db_manager
from utils.email_providers import mail_service
from utils.config import reload_all_configs
from utils.integrations.tg_notifier import send_tg_msg_async
from utils.memory_predictor import build_memory_report
import utils.config as cfg

router = APIRouter()
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

class DummyArgs:
    def __init__(self, proxy=None, once=False):
        self.proxy = proxy
        self.once = once

class LoginData(BaseModel): password: str
class DomainRuntimeActionReq(BaseModel): domain: str
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
    core_engine.run_stats.update({"success": 0, "failed": 0, "retries": 0, "pwd_blocked": 0, "phone_verify": 0, "start_time": time.time(),"target": 0})
    mail_service.start_mail_domain_runtime_tracking()
    if getattr(core_engine.cfg, 'ENABLE_CPA_MODE', False):
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
    mail_service.stop_mail_domain_runtime_tracking()
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

    domain_summary = mail_service.get_mail_domain_runtime_summary()
    memory_report = build_memory_report(getattr(core_engine.cfg, '_c', {}))
    actual_memory = memory_report.get("actual", {})
    predicted_memory = memory_report.get("prediction", {}).get("predicted_mb", {})

    return {
        "success": stats["success"], "failed": stats["failed"], "retries": stats["retries"],
        "pwd_blocked": stats.get("pwd_blocked", 0), "phone_verify": stats.get("phone_verify", 0),
        "total": total_attempts, "target": stats["target"] if stats["target"] > 0 else "∞",
        "success_rate": f"{success_rate}%", "elapsed": f"{elapsed}s", "avg_time": f"{avg_time}s",
        "progress_pct": f"{progress_pct}%", "is_running": is_running, "mode": current_mode,
        "available_count": domain_summary.get("available_count", 0),
        "cooldown_count": domain_summary.get("cooldown_count", 0),
        "memory": {
            "rss_mb": actual_memory.get("rss_mb"),
            "predicted_mid_mb": predicted_memory.get("mid"),
            "predicted_high_mb": predicted_memory.get("high"),
            "safety_level": memory_report.get("safety", {}).get("level"),
            "safety_label": memory_report.get("safety", {}).get("label"),
        },
    }


@router.get("/api/system/memory_prediction")
async def get_memory_prediction(token: str = Depends(verify_token)):
    return build_memory_report(getattr(core_engine.cfg, '_c', {}))


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


@router.get("/api/config")
async def get_config(token: str = Depends(verify_token)):
    config_data = getattr(core_engine.cfg, '_c', {}).copy()

    if isinstance(config_data.get("sub2api_mode"), dict):
        config_data["sub2api_mode"].pop("min_remaining_weekly_percent", None)
    config_data["web_password"] = getattr(core_engine.cfg, "WEB_PASSWORD", config_data.get("web_password", "admin"))
    config_data["local_microsoft"] = _sanitize_local_microsoft_config(config_data.get("local_microsoft"))
    return config_data


@router.get("/api/config/mail_domain_runtime_stats")
async def get_mail_domain_runtime_stats(token: str = Depends(verify_token)):
    return {"status": "success", "items": mail_service.get_mail_domain_runtime_stats()}


@router.post("/api/config/mail_domain_runtime_stats/clear")
async def clear_mail_domain_runtime_stats(token: str = Depends(verify_token)):
    cleared_count = mail_service.clear_all_mail_domain_runtime_cooldowns()
    return {"status": "success", "message": f"已清除 {cleared_count} 个域名冷却"}


@router.post("/api/config/mail_domain_runtime_stats/clear_counters")
async def clear_mail_domain_runtime_domain_counters(req: DomainRuntimeActionReq, token: str = Depends(verify_token)):
    item = mail_service.clear_mail_domain_runtime_domain_counters(req.domain)
    if not item:
        return {"status": "error", "message": "未找到指定域名的运行时计数"}
    return {"status": "success", "message": f"已清空 {item['domain']} 的计数", "item": item}


@router.post("/api/config/mail_domain_runtime_stats/clear_cooldown")
async def clear_mail_domain_runtime_domain_cooldown(req: DomainRuntimeActionReq, token: str = Depends(verify_token)):
    item = mail_service.clear_mail_domain_runtime_domain_cooldown(req.domain)
    if not item:
        return {"status": "error", "message": "未找到指定域名的冷却状态"}
    return {"status": "success", "message": f"已清除 {item['domain']} 的冷却", "item": item}


@router.post("/api/config")
async def save_config(new_config: dict, token: str = Depends(verify_token)):
    try:
        if isinstance(new_config.get("sub2api_mode"), dict):
            new_config["sub2api_mode"].pop("min_remaining_weekly_percent", None)
        new_config["local_microsoft"] = _sanitize_local_microsoft_config(new_config.get("local_microsoft"))
        if not isinstance(new_config.get("disabled_mail_domains"), list):
            new_config["disabled_mail_domains"] = []
        if not isinstance(new_config.get("mail_domain_failure_types"), list):
            new_config["mail_domain_failure_types"] = ["discarded_email"]
        new_config["mail_domain_failure_types"] = list(dict.fromkeys(
            str(item or "").strip().lower()
            for item in new_config.get("mail_domain_failure_types", [])
            if str(item or "").strip()
        )) or ["discarded_email"]
        reload_all_configs(new_config_dict=new_config)
        mail_service.sync_mail_domain_runtime_state_with_config()

        return {"status": "success", "message": "✅ 配置已成功保存并同步至云端！"}
    except Exception as e:
        return {"status": "error", "message": f"❌ 保存失败: {str(e)}"}


@router.get("/api/system/check_update")
async def check_update(current_version: str, token: str = Depends(verify_token)):
    try:
        proxy_url = getattr(core_engine.cfg, 'DEFAULT_PROXY', None)

        web_url = "https://github.com/wenfxl/openai-cpa/releases/latest"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        async with httpx.AsyncClient(proxy=proxy_url, timeout=15.0) as client:
            resp = await client.head(web_url, headers=headers, follow_redirects=False)

            if resp.status_code == 302:
                redirect_url = resp.headers.get("Location")
                if not redirect_url:
                    return {"status": "error", "message": "无法从 GitHub 获取重定向地址"}
                remote_version = redirect_url.split("/")[-1]
                html_url = redirect_url
                download_url = f"https://github.com/wenfxl/openai-cpa/archive/refs/tags/{remote_version}.zip"
            else:
                return {"status": "error", "message": f"获取版本失败，状态码: {resp.status_code}"}
        def _parse(v):
            return [int(x) for x in re.findall(r'\d+', str(v))]

        has_update = _parse(remote_version) > _parse(current_version) if remote_version else False
        changelog = "暂不展示详细日志。请自行前往仓库查看。"

        return {
            "status": "success",
            "has_update": has_update,
            "remote_version": remote_version,
            "changelog": changelog,
            "download_url": download_url,
            "html_url": html_url
        }
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
                nodes_snapshot = CLUSTER_NODES.copy()
            await websocket.send_json({"status": "success", "nodes": nodes_snapshot})

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
        append_log(msg)
    except:
        pass
    return {"status": "success", "message": f"成功接收 {success_count} 个账号"}

#模式二注册
@router.get("/api/ext/generate_task")
def ext_generate_task(token: str = Depends(verify_token)):
    from utils.email_providers.mail_service import mask_email, get_email_and_token, clear_sticky_domain
    from utils.auth_pipeline.user_utils import generate_random_user_info, _generate_password
    from utils.auth_pipeline.oauth import generate_oauth_url

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
    from utils.auth_pipeline.register import submit_callback_url

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
    mail_service.start_mail_domain_runtime_tracking()
    return {"status": "success"}

@router.post("/api/ext/stop")
def ext_stop(token: str = Depends(verify_token)):
    from utils import core_engine
    core_engine.run_stats["ext_is_running"] = False
    mail_service.stop_mail_domain_runtime_tracking()
    return {"status": "success"}

@router.get("/api/system/version")
def get_system_version():
    return {"status": "success", "version": cfg.APP_VERSION}


def is_docker():
    path = '/proc/self/cgroup'
    return (
            os.path.exists('/.dockerenv') or
            os.path.exists('/run/.containerenv') or
            (os.path.isfile(path) and any('docker' in line for line in open(path)))
    )

@router.post("/api/system/auto_update")
def auto_update(token: str = Depends(verify_token)):
    if is_docker():
        return execute_docker_update()
    else:
        return execute_native_update()


def execute_docker_update():
    try:
        project_path = os.getenv("HOST_PROJECT_PATH")
        image_name = "wenfxl/wenfxl-codex-manager:latest"
        print(f"[{core_engine.ts()}] [系统] 🚀 正在通过官方 Compose 引擎执行重建...")
        subprocess.run(["docker", "pull", image_name], check=False)
        update_cmd = (
            f"nohup docker run --rm "
            f"-v /var/run/docker.sock:/var/run/docker.sock "
            f"-v {project_path}:{project_path} "
            f"-w {project_path} "
            f"docker/compose:latest up -d --no-deps codex-web > /dev/null 2>&1 &"
        )

        print(f"[{core_engine.ts()}] [系统] 🔄 指令已发出，由官方引擎接管重建任务...")
        subprocess.Popen(update_cmd, shell=True)

        return {
            "status": "success",
            "message": "更新指令已由官方引擎接管！系统正在自我重建，请 20 秒后刷新网页..."
        }

    except Exception as e:
        return {"status": "error", "message": f"更新异常: {str(e)}"}

def execute_native_update():
    try:
        proxy_url = getattr(core_engine.cfg, 'DEFAULT_PROXY', None)
        proxies = None
        if proxy_url:
            proxies = {
                "http": proxy_url,
                "https": proxy_url
            }
            print(f"[{core_engine.ts()}] [系统] 🚀 正在使用全局代理穿透下载更新: {proxy_url}")
        else:
            print(f"[{core_engine.ts()}] [系统] ⚠️ 未检测到全局代理，尝试直连下载...")

        web_url = "https://github.com/wenfxl/openai-cpa/releases/latest"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        release_response = requests.head(web_url, headers=headers, proxies=proxies, allow_redirects=False, timeout=15)

        if release_response.status_code == 302:
            redirect_url = release_response.headers.get('Location')
            if not redirect_url:
                raise Exception("无法从 GitHub 获取重定向地址")
            latest_tag = redirect_url.split('/')[-1]
            print(f"[{core_engine.ts()}] [系统] 🎉 成功获取最新版本标签: {latest_tag}")

            zip_url = f"https://github.com/wenfxl/openai-cpa/archive/refs/tags/{latest_tag}.zip"
        else:
            raise Exception(f"请求被拒绝或状态异常，状态码: {release_response.status_code}")

        print(f"[{core_engine.ts()}] [系统] 🚀 开始下载新版本源码包: {zip_url}")

        response = requests.get(zip_url, headers=headers, stream=True, proxies=proxies, timeout=60)
        response.raise_for_status()

        with zipfile.ZipFile(io.BytesIO(response.content)) as zip_ref:
            root_dir = zip_ref.namelist()[0]
            for member in zip_ref.namelist():
                if member == root_dir:
                    continue
                target_path = os.path.join(os.getcwd(), member.replace(root_dir, "", 1))
                if member.endswith('/'):
                    os.makedirs(target_path, exist_ok=True)
                else:
                    os.makedirs(os.path.dirname(target_path), exist_ok=True)
                    with zip_ref.open(member) as source, open(target_path, "wb") as target:
                        shutil.copyfileobj(source, target)

        def restart_server():
            time.sleep(2)
            print(f"[{core_engine.ts()}] [系统] 🔄 代码覆盖完毕，正在执行热重启...")
            try:
                sys.stdout.flush()
                sys.stderr.flush()
                subprocess.Popen([sys.executable] + sys.argv)
                os._exit(0)
            except Exception as e:
                print(f"[{core_engine.ts()}] [系统] ❌ 重启失败: {e}")
                os._exit(1)

        threading.Thread(target=restart_server).start()

        return {"status": "success", "message": "本地代码更新完成，系统正在热重启..."}

    except Exception as e:
        return {"status": "error", "message": f"本地更新异常: {str(e)}"}
