"""
CDK Team 激活引擎
调用邮箱注册流程获取 AT → CDK 验证 → 加入团队 → 刷新 RT → 保存 Team 账号。
"""

import json
import random
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from curl_cffi import requests
from utils import config as cfg
from utils import db_manager
from utils.config import ts, format_docker_url
from utils.email_providers.mail_service import mask_email
from utils.auth_pipeline.register import run as register_run
from utils.auth_pipeline.oauth import refresh_oauth_token
from utils.proxy_manager import smart_switch_node

CDK_API_BASE = "https://team.sanguine.qzz.io/api"

_stats_lock = threading.Lock()


def cdk_print(msg: str):
    """写入 CDK 专用日志队列。"""
    formatted = f"[{ts()}] {msg}"
    try:
        from global_state import append_cdk_log
        append_cdk_log(formatted)
    except Exception:
        pass


def _verify_cdk(cdk_code: str, access_token: str, proxies: dict = None) -> Optional[str]:
    """POST /api/verify，返回 proxyToken 或 None。"""
    try:
        resp = requests.post(
            f"{CDK_API_BASE}/verify",
            json={"cdk": cdk_code, "accessToken": access_token},
            proxies=proxies,
            timeout=30,
            impersonate="chrome",
        )
        data = resp.json()
        if resp.status_code == 200 and data.get("proxyToken"):
            return data["proxyToken"]
        cdk_print(f"[ERROR] CDK 验证失败: HTTP {resp.status_code}, {data}")
        return None
    except Exception as e:
        cdk_print(f"[ERROR] CDK 验证异常: {e}")
        return None


def _join_team(proxy_token: str, access_token: str, proxies: dict = None) -> Optional[dict]:
    """POST /api/proxy/join，返回结果 dict 或 None。"""
    try:
        resp = requests.post(
            f"{CDK_API_BASE}/proxy/join",
            json={"proxyToken": proxy_token, "accessToken": access_token},
            proxies=proxies,
            timeout=30,
            impersonate="chrome",
        )
        data = resp.json()
        if resp.status_code == 200 and data.get("success"):
            return data
        cdk_print(f"[ERROR] 加入团队失败: HTTP {resp.status_code}, {data}")
        return None
    except Exception as e:
        cdk_print(f"[ERROR] 加入团队异常: {e}")
        return None


def cdk_activation_worker(proxy: str, stop_event: threading.Event) -> str:
    """单次 CDK 激活工作流。返回 'success' / 'failed' / 'no_cdk' / 'stopped'。"""
    if stop_event.is_set():
        return "stopped"

    # 1. 取 CDK
    cdk_item = db_manager.claim_next_cdk()
    if not cdk_item:
        cdk_print("[WARNING] CDK 池已耗尽，没有可用的 CDK")
        return "no_cdk"

    cdk_id = cdk_item["id"]
    cdk_code = cdk_item["code"]
    cdk_print(f"[INFO] 获取 CDK: {cdk_code[:7]}...{cdk_code[-4:]}")

    # 2. 保存并临时禁用 TEAM_MODE
    orig_team_mode = getattr(cfg, 'TEAM_MODE_ENABLE', False)
    orig_team_overspeed = getattr(cfg, 'TEAM_MODE_OVERSPEED', False)
    cfg.TEAM_MODE_ENABLE = False
    cfg.TEAM_MODE_OVERSPEED = False

    proxy = format_docker_url(proxy)
    if proxy and proxy.startswith("socks5://"):
        proxy = proxy.replace("socks5://", "socks5h://")
    proxies = {"http": proxy, "https": proxy} if proxy else None

    token_json_str = None
    password = None
    try:
        # 3. 走注册流程拿 AT
        cdk_print("[INFO] 开始邮箱注册流程...")
        result = register_run(proxy, run_ctx={})
        if result and isinstance(result, (tuple, list)) and len(result) >= 2:
            token_json_str, password = result
    except Exception as e:
        cdk_print(f"[ERROR] 注册流程异常: {e}")
        traceback.print_exc()
    finally:
        # 恢复原始 TEAM_MODE
        cfg.TEAM_MODE_ENABLE = orig_team_mode
        cfg.TEAM_MODE_OVERSPEED = orig_team_overspeed

    if stop_event.is_set():
        db_manager.release_cdk(cdk_id)
        return "stopped"

    if not token_json_str:
        cdk_print("[ERROR] 注册失败，未获得 Token，释放 CDK")
        db_manager.release_cdk(cdk_id)
        return "failed"

    # 4. 解析 AT
    try:
        token_data = json.loads(token_json_str)
    except Exception:
        cdk_print("[ERROR] Token JSON 解析失败，释放 CDK")
        db_manager.release_cdk(cdk_id)
        return "failed"

    access_token = token_data.get("access_token", "")
    refresh_token_val = token_data.get("refresh_token", "")
    account_email = token_data.get("email", "unknown")
    masked = mask_email(account_email)

    if not access_token:
        cdk_print(f"[ERROR] （{masked}）未获取到 access_token，释放 CDK")
        db_manager.release_cdk(cdk_id)
        return "failed"

    cdk_print(f"[SUCCESS] （{masked}）AT 已获取，开始 CDK 验证...")

    # 5. CDK 验证
    proxy_token = _verify_cdk(cdk_code, access_token, proxies)
    if not proxy_token:
        cdk_print(f"[ERROR] （{masked}）CDK 验证失败，标记 CDK 为失败")
        db_manager.mark_cdk_failed(cdk_id)
        # 保存为普通账号
        db_manager.save_account_to_db(account_email, password or "", token_json_str)
        cdk_print(f"[INFO] （{masked}）已保存为普通账号")
        return "failed"

    cdk_print(f"[SUCCESS] （{masked}）CDK 验证成功，获得 proxyToken，正在加入团队...")

    # 6. 加入团队
    join_result = _join_team(proxy_token, access_token, proxies)
    if not join_result:
        cdk_print(f"[ERROR] （{masked}）加入团队失败，标记 CDK 为失败")
        db_manager.mark_cdk_failed(cdk_id)
        db_manager.save_account_to_db(account_email, password or "", token_json_str)
        cdk_print(f"[INFO] （{masked}）已保存为普通账号")
        return "failed"

    workspace_name = join_result.get("workspaceName", "")
    workspace_info = join_result.get("workspaceInfo", {})
    ws_plan = workspace_info.get("plan", "unknown")
    cdk_print(f"[SUCCESS] （{masked}）已加入团队: {workspace_name} (plan: {ws_plan})")

    # 7. 刷新 RT 以获取团队上下文 token
    if refresh_token_val:
        cdk_print(f"[INFO] （{masked}）正在刷新 RT 获取团队上下文凭据...")
        time.sleep(random.uniform(1.0, 3.0))
        ok, new_token_data = refresh_oauth_token(refresh_token_val, proxies)
        if ok:
            token_data["access_token"] = new_token_data.get("access_token", access_token)
            token_data["refresh_token"] = new_token_data.get("refresh_token", refresh_token_val)
            token_data["last_refresh"] = new_token_data.get("last_refresh", "")
            token_data["expired"] = new_token_data.get("expired", "")
            token_json_str = json.dumps(token_data, ensure_ascii=False)
            cdk_print(f"[SUCCESS] （{masked}）RT 刷新成功，凭据已更新为团队上下文")
        else:
            cdk_print(f"[WARNING] （{masked}）RT 刷新失败: {new_token_data.get('error', '')}, 使用原始凭据保存")

    # 8. 保存 team 账号
    cookies_str = json.dumps({"workspace": workspace_name, "plan": ws_plan})
    db_manager.save_team_account_with_cdk(account_email, token_json_str, cookies_str, cdk_code)
    db_manager.mark_cdk_used(cdk_id, account_email)
    cdk_print(f"[SUCCESS] （{masked}）CDK 激活完成！已保存到 Team 账号库 (CDK: {cdk_code[:7]}...)")

    return "success"


class CdkEngine:
    """CDK Team 激活引擎控制类，参照 RegEngine 设计。"""

    def __init__(self):
        self.thread_stop_event = threading.Event()
        self.current_thread = None
        self._executor = None
        self._force_stopped = False
        self._stats = {
            "success": 0,
            "failed": 0,
            "total_attempts": 0,
            "start_time": 0,
            "target": 0,
        }

    def start(self, target_count: int = 0, concurrency: int = 1, proxy: str = ""):
        if self.is_running():
            return
        self._force_stopped = False
        self.thread_stop_event = threading.Event()
        self._stats = {
            "success": 0,
            "failed": 0,
            "total_attempts": 0,
            "start_time": time.time(),
            "target": target_count,
        }
        workers = max(concurrency, 1)
        self._executor = ThreadPoolExecutor(max_workers=workers)
        self.current_thread = threading.Thread(
            target=self._run_loop,
            args=(target_count, workers, proxy),
            daemon=True,
        )
        self.current_thread.start()

    def stop(self):
        self._force_stopped = True
        self.thread_stop_event.set()
        time.sleep(0.5)
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

    def is_running(self) -> bool:
        if self._force_stopped:
            return False
        return self.current_thread is not None and self.current_thread.is_alive()

    def get_stats(self) -> dict:
        elapsed = time.time() - self._stats["start_time"] if self._stats["start_time"] else 0
        pool_stats = db_manager.get_cdk_pool_stats()
        return {
            "success": self._stats["success"],
            "failed": self._stats["failed"],
            "total_attempts": self._stats["total_attempts"],
            "target": self._stats["target"],
            "elapsed": round(elapsed, 1),
            "is_running": self.is_running(),
            "pool": pool_stats,
        }

    def _run_loop(self, target_count: int, concurrency: int, proxy: str):
        cdk_print("[系统] >>> 启动 CDK Team 激活引擎 <<<")
        if target_count > 0:
            cdk_print(f"[系统] 任务目标: 激活 {target_count} 个 Team 账号")
        else:
            cdk_print("[系统] 任务目标: 无限激活 (按停止终止)")
        cdk_print(f"[系统] 并发数: {concurrency}")

        try:
            while not self.thread_stop_event.is_set():
                if target_count > 0 and self._stats["success"] >= target_count:
                    cdk_print(f"[SUCCESS] 已达到目标数量 ({target_count})，任务完成！")
                    break

                # 检查 CDK 池
                pool_stats = db_manager.get_cdk_pool_stats()
                if pool_stats["unused"] == 0:
                    cdk_print("[WARNING] CDK 池已耗尽，引擎停止")
                    break

                # 计算本批数量
                remaining_cdks = pool_stats["unused"]
                if target_count > 0:
                    remaining_target = target_count - self._stats["success"]
                    batch = min(concurrency, remaining_target, remaining_cdks)
                else:
                    batch = min(concurrency, remaining_cdks)

                batch = max(batch, 1)
                self._stats["total_attempts"] += batch

                cdk_print(f"[INFO] 开始第 {self._stats['total_attempts']} 批次 ({batch} 条通道)")

                # Clash 单端口模式下先切节点
                if cfg._clash_enable and not cfg._clash_pool_mode:
                    if not smart_switch_node(proxy):
                        cdk_print("[WARNING] 全局节点切换失败，使用当前 IP 继续")

                # 提交 worker
                def _worker():
                    if self.thread_stop_event.is_set():
                        return "stopped"
                    p = proxy
                    if cfg.is_raw_proxy_pool_enabled():
                        _, p = cfg.unpack_proxy_queue_item(cfg.PROXY_QUEUE.get())
                    elif cfg._clash_enable and cfg._clash_pool_mode:
                        pool_item = cfg.PROXY_QUEUE.get()
                        p = pool_item[-1] if isinstance(pool_item, tuple) else pool_item
                    return cdk_activation_worker(p, self.thread_stop_event)

                futures = [self._executor.submit(_worker) for _ in range(batch)]
                for f in futures:
                    try:
                        result = f.result(timeout=600)
                        if result == "success":
                            with _stats_lock:
                                self._stats["success"] += 1
                        elif result == "no_cdk":
                            break
                        elif result == "failed":
                            with _stats_lock:
                                self._stats["failed"] += 1
                    except Exception as e:
                        cdk_print(f"[ERROR] Worker 异常: {e}")
                        with _stats_lock:
                            self._stats["failed"] += 1

                # 间隔
                sleep_sec = random.uniform(2, 5)
                if self.thread_stop_event.wait(sleep_sec):
                    break

        except Exception as e:
            cdk_print(f"[CRITICAL] 引擎主线程崩溃: {e}")
            traceback.print_exc()
        finally:
            if self._executor is not None:
                self._executor.shutdown(wait=False, cancel_futures=True)
                self._executor = None
            elapsed = time.time() - self._stats["start_time"] if self._stats["start_time"] else 0
            cdk_print(f"[系统] CDK 激活引擎已停止 | 成功: {self._stats['success']} | 失败: {self._stats['failed']} | 耗时: {elapsed:.1f}s")
