import os
import sys
import json
import time
import asyncio
import threading
import uvicorn
import warnings
import subprocess
import socket
import socks
warnings.filterwarnings("ignore", category=RuntimeWarning, module="trio")

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from utils import core_engine, db_manager
from utils.config import reload_all_configs
from utils.log_stream_cache import RecentParsedLogCache

from global_state import engine, log_history, append_log
from routers import api_routes

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    print("\n" + "="*65, flush=True)
    print("🛑 接收到系统终止信号，正在强制结束引擎...", flush=True)
    try:
        if engine.is_running():
            engine.stop()
    except Exception: pass
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

db_manager.init_db()

app.include_router(api_routes.router)

class DummyArgs:
    def __init__(self, proxy=None, once=False):
        self.proxy = proxy
        self.once = once

def _worker_push_thread():
    last_role = None
    log_cache = RecentParsedLogCache(limit=50)
    push_interval = 1.0

    def _internal_start():
        try: reload_all_configs()
        except: pass
        args = DummyArgs(proxy=getattr(core_engine.cfg, 'DEFAULT_PROXY', None))
        core_engine.run_stats.update({"success": 0, "failed": 0, "retries": 0, "pwd_blocked": 0, "phone_verify": 0, "start_time": time.time()})
        if getattr(core_engine.cfg, 'ENABLE_CPA_MODE', False): engine.start_cpa(args)
        elif getattr(core_engine.cfg, 'ENABLE_SUB2API_MODE', False): engine.start_sub2api(args)
        else: engine.start_normal(args)

    async def _ws_loop():
        nonlocal last_role
        try: import websockets
        except ImportError:
            print(f"[{core_engine.ts()}] [系统] ❌ 缺少 WebSocket 库！请在终端执行: pip install websockets")
            return

        while True:
            try:
                while not core_engine.log_queue.empty():
                    msg = core_engine.log_queue.get_nowait()
                    append_log(msg)
            except: pass

            cf_dict = getattr(core_engine.cfg, '_c', {})
            master_url = str(cf_dict.get("cluster_master_url", "")).strip()
            node_name = str(cf_dict.get("cluster_node_name", "")).strip() or "未命名节点"
            secret = str(cf_dict.get("cluster_secret", "wenfxl666")).strip()

            if not master_url:
                if last_role != "master":
                    print(f"[{core_engine.ts()}] [集群] 主控模式激活。")
                    last_role = "master"
                await asyncio.sleep(0.5)
                continue

            if master_url.startswith("http"):
                import urllib.parse
                ws_url = master_url.replace("http://", "ws://").replace("https://", "wss://")
                ws_endpoint = f"{ws_url.rstrip('/')}/api/cluster/report_ws?node_name={urllib.parse.quote(node_name)}&secret={urllib.parse.quote(secret)}"

                try:
                    async with websockets.connect(ws_endpoint, ping_interval=None) as ws:
                        if last_role != "node":
                            print(f"[{core_engine.ts()}] [集群] 🚀 已通过 WebSocket 建立超高速光纤连接: {master_url}")
                            last_role = "node"

                        while True:
                            try:
                                while not core_engine.log_queue.empty():
                                    msg = core_engine.log_queue.get_nowait()
                                    append_log(msg)
                            except: pass

                            s = core_engine.run_stats
                            is_running = engine.is_running()
                            total = s["success"] + s["failed"]
                            if is_running:
                                elapsed = round(time.time() - s["start_time"], 1) if s.get("start_time", 0) > 0 else 0
                                s["_frozen_elapsed"] = elapsed
                            else:
                                elapsed = s.get("_frozen_elapsed", 0)

                            stats_payload = {
                                "success": s["success"], "failed": s["failed"], "retries": s["retries"],
                                "pwd_blocked": s.get("pwd_blocked", 0), "phone_verify": s.get("phone_verify", 0),
                                "total": total, "target": s["target"] if s["target"] > 0 else "∞",
                                "success_rate": f"{round(s['success'] / total * 100, 2) if total > 0 else 0}%",
                                "elapsed": f"{elapsed}s", "avg_time": f"{round(elapsed / s['success'], 1) if s['success'] > 0 else 0}s",
                                "progress_pct": f"{min(100, round(s['success'] / s['target'] * 100, 1)) if s['target'] > 0 else 0}%",
                                "is_running": is_running,
                                "mode": "CPA仓管" if getattr(core_engine.cfg, 'ENABLE_CPA_MODE', False) else ("Sub2Api" if getattr(core_engine.cfg, 'ENABLE_SUB2API_MODE', False) else "常规量产")
                            }

                            _, parsed_logs, changed = log_cache.refresh(log_history)

                            if changed or is_running:
                                await ws.send(json.dumps({"stats": stats_payload, "logs": parsed_logs}))
                            else:
                                await ws.send(json.dumps({"stats": stats_payload}))

                            resp_str = await ws.recv()
                            cmd = json.loads(resp_str).get("command", "none")

                            if cmd == "restart":
                                print(f"[{core_engine.ts()}] [集群] 🔄 收到总控重启指令，正在重启...")
                                def _do_restart():
                                    time.sleep(1)
                                    sys.stdout.flush()
                                    subprocess.Popen([sys.executable] + sys.argv)
                                    os._exit(0)
                                threading.Thread(target=_do_restart, daemon=True).start()
                            elif cmd == "start" and not is_running:
                                threading.Thread(target=_internal_start, daemon=True).start()
                            elif cmd == "stop" and is_running:
                                engine.stop()
                            elif cmd == "export_accounts":
                                print(f"[{core_engine.ts()}] [系统] 收到总控提取指令，准备发货！")
                                def _upload_task():
                                    try:
                                        import urllib.request
                                        local_accounts = db_manager.get_all_accounts_with_token(10000)
                                        if not local_accounts:
                                            print(f"[{core_engine.ts()}] [系统] ⚠️ 本地库存为空，无账号可提取。")
                                            return
                                        req_data = {"node_name": node_name, "secret": secret, "accounts": local_accounts}
                                        req_body = json.dumps(req_data).encode('utf-8')
                                        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                                        upload_req = urllib.request.Request(
                                            f"{master_url.rstrip('/')}/api/cluster/upload_accounts", data=req_body,
                                            headers={'Content-Type': 'application/json'})
                                        with opener.open(upload_req, timeout=15) as _:
                                            print(f"[{core_engine.ts()}] [系统] 📤 已成功将 {len(local_accounts)} 个账号打包发往总控！")
                                    except Exception as e:
                                        print(f"[{core_engine.ts()}] [ERROR] ❌ 账号上传总控失败: {e}")
                                threading.Thread(target=_upload_task, daemon=True).start()

                            await asyncio.sleep(push_interval if is_running else 3.0)
                except Exception: pass
            await asyncio.sleep(3)
    asyncio.run(_ws_loop())

threading.Thread(target=_worker_push_thread, daemon=True).start()

if __name__ == "__main__":
    try: reload_all_configs()
    except: pass
    print("=" * 65)
    print(f"[{core_engine.ts()}] [系统] OpenAI 全链路自动化生产与多维资源中转调度平台")
    print(f"[{core_engine.ts()}] [系统] Author: (wenfxl)轩灵")
    print(f"[{core_engine.ts()}] [系统] 如果遇到问题请更换域名解决，目前eu.cc，xyz，cn，edu.cc等常见域名均不可用，请更换为冷门域名")
    print("-" * 65)
    print(f"[{core_engine.ts()}] [系统] Web 控制台已准备就绪，等待下发指令...")
    sys.__stdout__.write(f"[{core_engine.ts()}] [系统] 控制台地址：http://127.0.0.1:8000 \n")
    sys.__stdout__.write(f"[{core_engine.ts()}] [系统] 控制台初始密码：admin \n")
    sys.__stdout__.write(f"[{core_engine.ts()}] [系统] 结束请猛猛重复按CTRL+C \n")
    sys.__stdout__.flush()
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning", access_log=False, timeout_graceful_shutdown=1)
