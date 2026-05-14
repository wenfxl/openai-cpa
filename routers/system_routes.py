import hashlib
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
import json
from pathlib import Path

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
from utils.system_maintenance import get_cleanup_status
import utils.config as cfg

router = APIRouter()
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_cluster_sync_worker_started = False
_cluster_sync_worker_lock = threading.Lock()

class DummyArgs:
    def __init__(self, proxy=None, once=False):
        self.proxy = proxy
        self.once = once

class LoginData(BaseModel): password: str
class DomainRuntimeActionReq(BaseModel): domain: str
class ClusterSyncTaskCreateReq(BaseModel):
    node_name: str
    secret: str
    task_id: str
    file_path: str
    file_size: int = 0
    total_count: int = 0
    file_sha256: str = ""
class ClusterUploadAccountsReq(BaseModel):
    node_name: str
    secret: str
    accounts: list
    batch_index: Optional[int] = None
    total_batches: Optional[int] = None
    total_uploaded: Optional[int] = None
class ClusterReportReq(BaseModel): node_name: str; secret: str; stats: dict; logs: list
class ClusterControlReq(BaseModel): node_name: str; action: str
class GitSyncReq(BaseModel):
    action: str
    restart_after: bool = False

class CleanupRunReq(BaseModel):
    force: bool = False

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


def _run_local_command(command: list[str], timeout: int = 60, cwd: Optional[str] = None) -> dict:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd or BASE_DIR,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(5, int(timeout or 60)),
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        output = "\n".join(part for part in [(e.stdout or "").strip(), (e.stderr or "").strip()] if part).strip()
        return {"ok": False, "returncode": -1, "stdout": e.stdout or "", "stderr": e.stderr or "", "output": output, "error": "命令执行超时"}
    except Exception as e:
        return {"ok": False, "returncode": -1, "stdout": "", "stderr": "", "output": "", "error": str(e)}

    output = "\n".join(part for part in [(completed.stdout or "").strip(), (completed.stderr or "").strip()] if part).strip()
    return {
        "ok": completed.returncode == 0,
        "returncode": int(completed.returncode),
        "stdout": completed.stdout or "",
        "stderr": completed.stderr or "",
        "output": output,
        "error": "" if completed.returncode == 0 else output or f"命令执行失败 (exit {completed.returncode})",
    }


def _tail_lines(text: str, limit: int = 15) -> list[str]:
    lines = [str(line or "").rstrip() for line in str(text or "").splitlines() if str(line or "").strip()]
    return lines[-limit:] if limit > 0 else lines


def _detect_git_tracking(branch: str) -> str:
    tracking_result = _run_local_command(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        timeout=15,
    )
    if tracking_result.get("ok"):
        return str((tracking_result.get("stdout") or "").strip())
    branch_text = str(branch or "").strip()
    return f"origin/{branch_text}" if branch_text else "origin/main"


def _get_git_sync_status() -> dict:
    git_dir = os.path.join(BASE_DIR, ".git")
    if not os.path.isdir(git_dir):
        return {"ok": False, "is_git_repo": False, "message": "当前项目目录不是 Git 仓库。"}

    branch_result = _run_local_command(["git", "branch", "--show-current"], timeout=15)
    branch = str((branch_result.get("stdout") or "").strip())
    tracking = _detect_git_tracking(branch)
    remote_result = _run_local_command(["git", "remote", "get-url", "origin"], timeout=15)
    current_commit_result = _run_local_command(["git", "rev-parse", "--short", "HEAD"], timeout=15)
    tracking_commit_result = _run_local_command(["git", "rev-parse", "--short", tracking], timeout=15)
    status_result = _run_local_command(["git", "status", "--porcelain"], timeout=15)
    count_result = _run_local_command(["git", "rev-list", "--left-right", "--count", f"{tracking}...HEAD"], timeout=15)
    last_commit_result = _run_local_command(["git", "log", "-1", "--pretty=format:%H%n%s%n%ad", "--date=iso"], timeout=15)

    ahead = 0
    behind = 0
    if count_result.get("ok"):
        raw_counts = str((count_result.get("stdout") or "").strip()).split()
        if len(raw_counts) >= 2:
            try:
                behind = int(raw_counts[0])
                ahead = int(raw_counts[1])
            except Exception:
                ahead = 0
                behind = 0

    last_commit_lines = str(last_commit_result.get("stdout") or "").splitlines()
    last_commit = {
        "full": last_commit_lines[0].strip() if len(last_commit_lines) > 0 else "",
        "subject": last_commit_lines[1].strip() if len(last_commit_lines) > 1 else "",
        "date": last_commit_lines[2].strip() if len(last_commit_lines) > 2 else "",
    }

    dirty_lines = [line for line in str(status_result.get("stdout") or "").splitlines() if line.strip()]
    return {
        "ok": True,
        "is_git_repo": True,
        "branch": branch or "HEAD",
        "tracking": tracking,
        "remote_url": str((remote_result.get("stdout") or "").strip()),
        "current_commit": str((current_commit_result.get("stdout") or "").strip()),
        "tracking_commit": str((tracking_commit_result.get("stdout") or "").strip()),
        "ahead": ahead,
        "behind": behind,
        "is_clean": len(dirty_lines) == 0,
        "dirty_count": len(dirty_lines),
        "dirty_preview": dirty_lines[:12],
        "last_commit": last_commit,
        "message": "Git 状态已读取。",
    }


def _schedule_restart_after_delay(delay_sec: float = 1.5) -> None:
    def _do_restart():
        time.sleep(max(0.2, float(delay_sec or 1.5)))
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


def _run_cleanup_script(force: bool = False) -> dict:
    status = get_cleanup_status(BASE_DIR)
    if not status.get("can_run"):
        return {
            "ok": False,
            "status": status,
            "output_tail": [],
            "error": "当前环境不支持执行磁盘清理脚本，通常仅 Linux 服务器可用。",
        }

    command = ["bash", status["script_path"]]
    if force:
        command.append("--force")
    result = _run_local_command(command, timeout=240)
    return {
        "ok": result.get("ok", False),
        "status": get_cleanup_status(BASE_DIR),
        "output_tail": _tail_lines(result.get("output", ""), limit=20),
        "error": result.get("error", ""),
    }

def _normalize_mail_domain_items(raw_value: Any) -> list[str]:
    seen = set()
    domains = []
    for part in str(raw_value or "").split(','):
        text = str(part or "").strip().lower().strip('.')
        if text and text not in seen:
            seen.add(text)
            domains.append(text)
    return domains


def _normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(value, (int, float)):
        return value != 0
    return False


def _is_default_cluster_secret(secret: str) -> bool:
    return str(secret or "").strip() in {"", "wenfxl666"}


def _is_custom_cluster_secret_enforced() -> bool:
    return bool(getattr(cfg, "CLUSTER_SYNC_REQUIRE_CUSTOM_SECRET", True))


def _validate_cluster_secret(secret: str) -> tuple[bool, str]:
    current_secret = str(getattr(core_engine.cfg, '_c', {}).get("cluster_secret", "wenfxl666")).strip()
    if _is_custom_cluster_secret_enforced() and _is_default_cluster_secret(current_secret):
        return False, "请先配置自定义 cluster_secret"
    if str(secret or "").strip() != current_secret:
        return False, "密钥错误"
    return True, ""


def _resolve_cluster_sync_path(file_path: str) -> Path:
    candidate = Path(str(file_path or "")).expanduser()
    if not candidate.is_absolute():
        candidate = Path(BASE_DIR) / candidate
    return candidate.resolve()


def _get_cluster_sync_max_file_size_bytes() -> int:
    return max(1, int(getattr(cfg, "CLUSTER_SYNC_MAX_FILE_SIZE_MB", 20) or 20)) * 1024 * 1024


def _calculate_file_sha256(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            if chunk:
                digest.update(chunk)
    return digest.hexdigest()


def _delete_cluster_sync_file(file_path: str) -> tuple[bool, str]:
    try:
        if not _is_cluster_sync_path_allowed(file_path):
            return False, "同步文件路径不在共享目录内"
        target_path = _resolve_cluster_sync_path(file_path)
        if not target_path.exists():
            return True, ""
        target_path.unlink()
        return True, ""
    except Exception as e:
        return False, str(e)


def _cleanup_stale_cluster_sync_files() -> None:
    try:
        shared_dir = _resolve_cluster_sync_shared_dir()
        if not shared_dir.exists() or not shared_dir.is_dir():
            return
        max_age_hours = max(1, int(getattr(cfg, "CLUSTER_SYNC_STALE_FILE_MAX_AGE_HOURS", 12) or 12))
        cutoff_ts = time.time() - (max_age_hours * 3600)
        for target_path in shared_dir.rglob("*.jsonl"):
            try:
                resolved = target_path.resolve()
                resolved.relative_to(shared_dir)
                if not resolved.is_file():
                    continue
                if resolved.stat().st_mtime > cutoff_ts:
                    continue
                resolved.unlink()
                print(f"[{core_engine.ts()}] [系统] 🧹 已自动清理超时同步文件: {resolved}")
            except FileNotFoundError:
                continue
            except Exception as e:
                print(f"[{core_engine.ts()}] [WARNING] 清理超时同步文件失败: {e}")
    except Exception as e:
        print(f"[{core_engine.ts()}] [WARNING] 扫描超时同步文件失败: {e}")


def _verify_cluster_sync_file(file_path: str, expected_size: int = 0, expected_sha256: str = "", expected_total_count: int = 0) -> tuple[bool, str, Path | None]:
    if not _is_cluster_sync_path_allowed(file_path):
        return False, "同步文件路径不在共享目录内", None
    try:
        target_path = _resolve_cluster_sync_path(file_path)
    except Exception:
        return False, "同步文件路径非法", None
    if not target_path.exists() or not target_path.is_file():
        return False, "同步文件不存在", target_path
    actual_size = int(target_path.stat().st_size or 0)
    max_size = _get_cluster_sync_max_file_size_bytes()
    if actual_size > max_size:
        return False, "同步文件大小超限", target_path
    declared_size = max(0, int(expected_size or 0))
    if declared_size and declared_size != actual_size:
        return False, "同步文件大小校验失败", target_path
    max_records = max(1, int(getattr(cfg, "CLUSTER_SYNC_MAX_RECORDS", 100000) or 100000))
    declared_total_count = max(0, int(expected_total_count or 0))
    if declared_total_count > max_records:
        return False, "同步记录数量超限", target_path
    actual_sha256 = _calculate_file_sha256(target_path)
    normalized_expected_sha256 = str(expected_sha256 or "").strip().lower()
    if normalized_expected_sha256 and actual_sha256 != normalized_expected_sha256:
        return False, "同步文件摘要校验失败", target_path
    return True, "", target_path


def _finalize_cluster_sync_task_with_cleanup(task_id: str, status: str, success_count: int, fail_count: int, error_message: str = "", file_path: str = "") -> bool:
    final_error_message = str(error_message or "")
    if file_path:
        deleted, delete_error = _delete_cluster_sync_file(file_path)
        if not deleted:
            final_error_message = f"{final_error_message}；同步文件清理失败：{delete_error}" if final_error_message else f"同步文件清理失败：{delete_error}"
            print(f"[{core_engine.ts()}] [WARNING] 同步任务 {task_id} 文件清理失败：{delete_error}")
    return db_manager.finalize_cluster_sync_task(task_id, status, success_count, fail_count, final_error_message)


def _resolve_cluster_sync_shared_dir() -> Path:
    raw_path = str(getattr(cfg, "CLUSTER_SYNC_SHARED_DIR", "data/cluster_sync") or "data/cluster_sync").strip()
    shared_dir = Path(raw_path)
    if not shared_dir.is_absolute():
        shared_dir = Path(BASE_DIR) / shared_dir
    return shared_dir.resolve()


def _is_cluster_sync_path_allowed(file_path: str) -> bool:
    try:
        shared_dir = _resolve_cluster_sync_shared_dir()
        candidate = Path(str(file_path or "")).expanduser()
        if not candidate.is_absolute():
            candidate = Path(BASE_DIR) / candidate
        candidate = candidate.resolve()
        candidate.relative_to(shared_dir)
        return True
    except Exception:
        return False


def _serialize_cluster_sync_task(task: dict | None) -> dict | None:
    if not task:
        return None
    success_count = int(task.get("success_count") or 0)
    fail_count = int(task.get("fail_count") or 0)
    total_count = int(task.get("total_count") or 0)
    processed_count = success_count + fail_count
    progress_pct = round(processed_count / total_count * 100, 2) if total_count > 0 else 0.0
    payload = dict(task)
    payload["processed_count"] = processed_count
    payload["progress_pct"] = progress_pct
    return payload


def _run_cluster_sync_task(task: dict):
    task_id = str(task.get("task_id") or "").strip()
    if not task_id:
        return
    success_count = 0
    fail_count = 0
    flush_every = max(1, int(getattr(cfg, "CLUSTER_SYNC_PROGRESS_FLUSH_EVERY", 100) or 100))
    file_path = str(task.get("file_path") or "").strip()
    file_sha256 = str(task.get("file_sha256") or "").strip().lower()
    file_size = max(0, int(task.get("file_size") or 0))
    total_count = max(0, int(task.get("total_count") or 0))
    cancelled = False
    try:
        if db_manager.get_cluster_sync_task_status(task_id) in {"cancel_requested", "cancelled"}:
            _finalize_cluster_sync_task_with_cleanup(task_id, "cancelled", 0, 0, "用户取消任务", file_path)
            print(f"[{core_engine.ts()}] [系统] 🛑 同步任务 {task_id} 在开始前已取消。")
            return
        verified, verify_message, target_path = _verify_cluster_sync_file(
            file_path,
            expected_size=file_size,
            expected_sha256=file_sha256,
            expected_total_count=total_count,
        )
        if not verified or target_path is None:
            raise RuntimeError(verify_message or "同步文件校验失败")
        with target_path.open("r", encoding="utf-8") as handle:
            for index, raw_line in enumerate(handle, start=1):
                if index > max(1, int(getattr(cfg, "CLUSTER_SYNC_MAX_RECORDS", 100000) or 100000)):
                    raise RuntimeError("同步记录数量超限")
                if db_manager.get_cluster_sync_task_status(task_id) in {"cancel_requested", "cancelled"}:
                    db_manager.update_cluster_sync_task_progress(task_id, success_count, fail_count)
                    cancelled = True
                    break
                line = str(raw_line or "").strip()
                if not line:
                    continue
                try:
                    acc = json.loads(line)
                    if acc.get("email") and acc.get("token_data") and db_manager.save_account_to_db(
                        acc.get("email"), acc.get("password"), acc.get("token_data")
                    ):
                        success_count += 1
                    else:
                        fail_count += 1
                except Exception:
                    fail_count += 1
                if index % flush_every == 0:
                    db_manager.update_cluster_sync_task_progress(task_id, success_count, fail_count)
                    print(f"[{core_engine.ts()}] [系统] 📥 同步任务 {task_id} 导入进度：成功 {success_count}，失败 {fail_count}。")
        db_manager.update_cluster_sync_task_progress(task_id, success_count, fail_count)
        if cancelled or db_manager.get_cluster_sync_task_status(task_id) in {"cancel_requested", "cancelled"}:
            _finalize_cluster_sync_task_with_cleanup(task_id, "cancelled", success_count, fail_count, "用户取消任务", file_path)
            print(f"[{core_engine.ts()}] [系统] 🛑 同步任务 {task_id} 已取消，当前成功 {success_count}，失败 {fail_count}。")
            return
        final_status = "success" if fail_count == 0 else ("partial_success" if success_count > 0 else "failed")
        _finalize_cluster_sync_task_with_cleanup(task_id, final_status, success_count, fail_count, "", file_path)
        print(f"[{core_engine.ts()}] [系统] ✅ 同步任务 {task_id} 导入完成，成功 {success_count}，失败 {fail_count}。")
    except Exception as e:
        _finalize_cluster_sync_task_with_cleanup(task_id, "failed", success_count, fail_count, str(e), file_path)
        print(f"[{core_engine.ts()}] [ERROR] 同步任务 {task_id} 导入失败：{e}")


def _cluster_sync_worker_loop():
    while True:
        try:
            _cleanup_stale_cluster_sync_files()
            task = db_manager.claim_next_cluster_sync_task()
            if task:
                _run_cluster_sync_task(task)
                continue
        except Exception as e:
            print(f"[{core_engine.ts()}] [ERROR] 同步任务 worker 异常: {e}")
        time.sleep(max(1, int(getattr(cfg, "CLUSTER_SYNC_IMPORT_POLL_SEC", 2) or 2)))


def ensure_cluster_sync_worker_started():
    global _cluster_sync_worker_started
    if _cluster_sync_worker_started:
        return
    with _cluster_sync_worker_lock:
        if _cluster_sync_worker_started:
            return
        threading.Thread(target=_cluster_sync_worker_loop, daemon=True).start()
        _cluster_sync_worker_started = True


def _normalize_mail_domain_grouping_payload(config_data: dict) -> Optional[str]:
    master_domains = _normalize_mail_domain_items(config_data.get("mail_domains", ""))
    master_domain_set = set(master_domains)

    config_data["enable_mail_domain_grouping"] = _normalize_bool(config_data.get("enable_mail_domain_grouping", False))

    try:
        group_count = int(config_data.get("mail_domain_group_count", 2) or 2)
    except Exception:
        group_count = 2
    group_count = max(1, min(10, group_count))
    config_data["mail_domain_group_count"] = group_count

    group_mode = str(config_data.get("mail_domain_group_mode", "auto") or "auto").strip().lower()
    if group_mode not in {"auto", "manual"}:
        group_mode = "auto"
    config_data["mail_domain_group_mode"] = group_mode

    group_strategy = str(config_data.get("mail_domain_group_strategy", "round_robin") or "round_robin").strip().lower()
    if group_strategy not in {"round_robin", "exhaust_then_next"}:
        group_strategy = "round_robin"
    config_data["mail_domain_group_strategy"] = group_strategy

    raw_groups = config_data.get("mail_domain_groups", [])
    if not isinstance(raw_groups, list):
        raw_groups = []
    normalized_groups = [
        ",".join(_normalize_mail_domain_items(item))
        for item in raw_groups[:group_count]
    ]
    while len(normalized_groups) < group_count:
        normalized_groups.append("")
    config_data["mail_domain_groups"] = normalized_groups

    if config_data["enable_mail_domain_grouping"]:
        config_data["mail_domain_pinpoint_burst_mode"] = False
        if not master_domains:
            return "启用域名分组前请先填写 mail_domains"
        if group_count > len(master_domains):
            return "分组数量不能大于有效主域名数量"
        if group_mode == "manual":
            assigned = []
            assigned_set = set()
            for index, raw_group in enumerate(normalized_groups, start=1):
                domains = _normalize_mail_domain_items(raw_group)
                if not domains:
                    return f"第 {index} 组至少需要填写一个域名"
                for domain in domains:
                    if domain not in master_domain_set:
                        return f"第 {index} 组存在未配置在 mail_domains 中的域名: {domain}"
                    if domain in assigned_set:
                        return f"域名 {domain} 不能重复出现在多个分组中"
                    assigned.append(domain)
                    assigned_set.add(domain)
            if assigned_set != master_domain_set:
                missing = [domain for domain in master_domains if domain not in assigned_set]
                if missing:
                    return f"手动分组未覆盖所有主域名，缺少: {', '.join(missing)}"
    return None


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


@router.get("/api/system/cleanup_status")
async def get_system_cleanup_status(token: str = Depends(verify_token)):
    data = get_cleanup_status(BASE_DIR)
    return {
        "status": "success" if data.get("can_run") else "warning",
        "message": "清理状态已读取。" if data.get("can_run") else "当前环境仅提供状态展示，无法直接执行清理脚本。",
        "data": data,
    }


@router.post("/api/system/run_cleanup")
async def run_system_cleanup(req: CleanupRunReq, token: str = Depends(verify_token)):
    result = _run_cleanup_script(force=bool(req.force))
    if not result.get("ok"):
        return {
            "status": "error",
            "message": result.get("error") or "磁盘清理执行失败。",
            "data": {"status": result.get("status"), "output_tail": result.get("output_tail", [])},
        }
    return {
        "status": "success",
        "message": "磁盘 / 日志清理已执行完成。",
        "data": {"status": result.get("status"), "output_tail": result.get("output_tail", [])},
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


@router.get("/api/system/git_status")
async def get_git_status(token: str = Depends(verify_token)):
    data = _get_git_sync_status()
    return {
        "status": "success" if data.get("ok") else "warning",
        "message": data.get("message") or ("Git 状态已读取。" if data.get("ok") else "Git 状态读取失败。"),
        "data": data,
    }


@router.post("/api/system/git_update")
async def git_update(req: GitSyncReq, token: str = Depends(verify_token)):
    action = str(req.action or "").strip().lower()
    if action not in {"fetch", "reset_hard"}:
        return {"status": "error", "message": "不支持的 Git 操作。"}

    if action != "fetch" and engine.is_running():
        return {"status": "warning", "message": "请先停止当前运行任务，再执行 Git 更新。"}

    before = _get_git_sync_status()
    if not before.get("is_git_repo"):
        return {"status": "error", "message": before.get("message") or "当前项目目录不是 Git 仓库。", "data": before}

    commands: list[list[str]] = [["git", "fetch", "origin", "--prune"]]
    tracking = str(before.get("tracking") or "") or "origin/main"
    if action == "reset_hard":
        commands.append(["git", "reset", "--hard", tracking])
        commands.append(["git", "clean", "-fd"])

    output_chunks: list[str] = []
    for command in commands:
        result = _run_local_command(command, timeout=180)
        rendered = " ".join(command)
        chunk = f"$ {rendered}"
        if result.get("output"):
            chunk += "\n" + str(result.get("output") or "").strip()
        output_chunks.append(chunk)
        if not result.get("ok"):
            after_failed = _get_git_sync_status()
            return {
                "status": "error",
                "message": result.get("error") or f"命令失败: {rendered}",
                "data": {
                    "before": before,
                    "after": after_failed,
                    "action": action,
                    "restart_scheduled": False,
                    "output_tail": _tail_lines("\n\n".join(output_chunks)),
                },
            }

    after = _get_git_sync_status()
    restart_scheduled = False
    message = {
        "fetch": "远端状态已刷新。",
        "reset_hard": f"已强制同步到 {tracking}，本地冲突已直接覆盖。",
    }.get(action, "Git 操作已完成。")

    if req.restart_after:
        restart_scheduled = True
        message += " 系统将自动重启以加载最新代码。"
        _schedule_restart_after_delay()

    return {
        "status": "success",
        "message": message,
        "data": {
            "before": before,
            "after": after,
            "action": action,
            "restart_scheduled": restart_scheduled,
            "output_tail": _tail_lines("\n\n".join(output_chunks)),
        },
    }


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
        return {"status": "error", "message": "未找到指定域名的异常状态"}
    return {"status": "success", "message": f"已清除 {item['domain']} 的异常", "item": item}


@router.post("/api/config/mail_domain_runtime_stats/clear_cooldown")
async def clear_mail_domain_runtime_domain_cooldown(req: DomainRuntimeActionReq, token: str = Depends(verify_token)):
    item = mail_service.clear_mail_domain_runtime_domain_cooldown(req.domain)
    if not item:
        return {"status": "error", "message": "未找到指定域名的冷却状态"}
    return {"status": "success", "message": f"已清除 {item['domain']} 的冷却", "item": item}


@router.post("/api/config")
async def save_config(new_config: dict, token: str = Depends(verify_token)):
    try:
        current_config = getattr(core_engine.cfg, '_c', {}).copy()
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
        def normalize_bool(value):
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.strip().lower() in {"1", "true", "yes", "on"}
            if isinstance(value, (int, float)):
                return value != 0
            return False
        new_config["mail_domain_pinpoint_burst_mode"] = normalize_bool(new_config.get("mail_domain_pinpoint_burst_mode", False))
        new_config["mail_domain_prefer_low_failure_mode"] = normalize_bool(new_config.get("mail_domain_prefer_low_failure_mode", False))
        if new_config["mail_domain_pinpoint_burst_mode"] and new_config["mail_domain_prefer_low_failure_mode"]:
            new_config["mail_domain_prefer_low_failure_mode"] = False
        grouping_error = _normalize_mail_domain_grouping_payload(new_config)
        if grouping_error:
            return {"status": "error", "message": grouping_error}
        reload_all_configs(new_config_dict=new_config)
        mail_service.sync_mail_domain_runtime_state_with_config()
        extra_messages = []
        old_default_proxy = str(current_config.get("default_proxy") or "").strip()
        new_default_proxy = str(new_config.get("default_proxy") or "").strip()
        old_clash_conf = current_config.get("clash_proxy_pool", {}) if isinstance(current_config.get("clash_proxy_pool"), dict) else {}
        new_clash_conf = new_config.get("clash_proxy_pool", {}) if isinstance(new_config.get("clash_proxy_pool"), dict) else {}
        clash_runtime_related_changed = any([
            old_default_proxy != new_default_proxy,
            str(old_clash_conf.get("api_url") or "").strip() != str(new_clash_conf.get("api_url") or "").strip(),
            str(old_clash_conf.get("secret") or "").strip() != str(new_clash_conf.get("secret") or "").strip(),
        ])
        if clash_runtime_related_changed:
            ok, msg = clash_manager.sync_single_core_runtime_from_saved_config()
            if msg:
                extra_messages.append(msg if ok else f"Clash 运行配置同步失败: {msg}")

        final_message = "✅ 配置已成功保存并同步至云端！"
        if extra_messages:
            final_message += " " + " ".join(extra_messages)
        return {"status": "success", "message": final_message}
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


@router.post("/api/cluster/sync_tasks")
def create_cluster_sync_task(req: ClusterSyncTaskCreateReq):
    valid_secret, secret_message = _validate_cluster_secret(req.secret)
    if not valid_secret:
        return {"status": "error", "message": secret_message}
    verified, verify_message, target_path = _verify_cluster_sync_file(
        req.file_path,
        expected_size=req.file_size,
        expected_sha256=req.file_sha256,
        expected_total_count=req.total_count,
    )
    if not verified or target_path is None:
        return {"status": "error", "message": verify_message or "同步文件校验失败"}
    ensure_cluster_sync_worker_started()
    if not db_manager.create_cluster_sync_task(
        task_id=req.task_id,
        node_name=req.node_name,
        file_path=str(target_path),
        file_size=max(0, int(req.file_size or 0)),
        total_count=max(0, int(req.total_count or 0)),
        max_retries=0,
        file_sha256=str(req.file_sha256 or '').strip().lower(),
    ):
        return {"status": "error", "message": "同步任务已存在"}
    print(f"[{core_engine.ts()}] [系统] 📦 已接收来自子控 [{req.node_name}] 的同步任务 {req.task_id}，等待导入。")
    task = _serialize_cluster_sync_task(db_manager.get_cluster_sync_task(req.task_id))
    return {"status": "success", "task_id": req.task_id, "task": task}


@router.get("/api/cluster/sync_tasks")
async def list_cluster_sync_tasks(limit: int = Query(20), node_name: str = Query(""), status: str = Query(""), token: str = Depends(verify_token)):
    ensure_cluster_sync_worker_started()
    tasks = db_manager.list_cluster_sync_tasks(limit=max(1, min(limit, 100)), node_name=node_name.strip(), status=status.strip())
    return {"status": "success", "tasks": [_serialize_cluster_sync_task(task) for task in tasks]}


@router.get("/api/cluster/sync_tasks/{task_id}")
async def get_cluster_sync_task(task_id: str, token: str = Depends(verify_token)):
    ensure_cluster_sync_worker_started()
    task = _serialize_cluster_sync_task(db_manager.get_cluster_sync_task(task_id))
    if not task:
        return {"status": "error", "message": "同步任务不存在"}
    return {"status": "success", "task": task}


@router.post("/api/cluster/sync_tasks/clear_terminal")
async def clear_terminal_cluster_sync_tasks(token: str = Depends(verify_token)):
    ensure_cluster_sync_worker_started()
    cleared = db_manager.clear_cluster_sync_terminal_tasks()
    return {"status": "success", "message": f"已清理 {cleared} 条终态任务", "cleared": cleared}


@router.post("/api/cluster/sync_tasks/{task_id}/retry")
async def retry_cluster_sync_task(task_id: str, token: str = Depends(verify_token)):
    ensure_cluster_sync_worker_started()
    task = db_manager.get_cluster_sync_task(task_id)
    if not task:
        return {"status": "error", "message": "同步任务不存在"}
    return {"status": "error", "message": "旧任务文件已清理，请重新同步"}


@router.post("/api/cluster/sync_tasks/{task_id}/cancel")
async def cancel_cluster_sync_task(task_id: str, token: str = Depends(verify_token)):
    ensure_cluster_sync_worker_started()
    task = db_manager.get_cluster_sync_task(task_id)
    if not task:
        return {"status": "error", "message": "同步任务不存在"}
    if not db_manager.cancel_cluster_sync_task(task_id):
        return {"status": "error", "message": "仅排队中或导入中的任务支持取消"}
    print(f"[{core_engine.ts()}] [系统] 🛑 同步任务 {task_id} 已请求取消。")
    task = _serialize_cluster_sync_task(db_manager.get_cluster_sync_task(task_id))
    return {"status": "success", "message": f"同步任务 {task_id} 已取消", "task": task}


@router.post("/api/cluster/report")
async def cluster_report(req: ClusterReportReq):
    valid_secret, secret_message = _validate_cluster_secret(req.secret)
    if not valid_secret:
        return {"status": "error", "message": secret_message}

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
    valid_secret, _ = _validate_cluster_secret(secret)
    if not valid_secret:
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
    valid_secret, secret_message = _validate_cluster_secret(req.secret)
    if not valid_secret:
        return {"status": "error", "message": secret_message}
    success_count = 0
    for acc in req.accounts:
        if acc.get("email") and acc.get("token_data"):
            if db_manager.save_account_to_db(acc.get("email"), acc.get("password"),
                                             acc.get("token_data")):
                success_count += 1

    msg = f"[{core_engine.ts()}] [系统] 📦 第 {req.batch_index}/{req.total_batches} 批账号接收完成，来自子控 [{req.node_name}]，本批入库 {success_count} 个。" if req.batch_index and req.total_batches else f"[{core_engine.ts()}] [系统] 📦 成功从子控 [{req.node_name}] 提取并完美入库 {success_count} 个账号！"
    print(msg)
    done = bool(req.batch_index and req.total_batches and req.batch_index == req.total_batches)
    if done:
        done_msg = f"[{core_engine.ts()}] [系统] ✅ 账号批量接收完成，来自子控 [{req.node_name}]，共 {req.total_batches} 批，累计入库 {req.total_uploaded or success_count} 个。"
        print(done_msg)
    return {
        "status": "success",
        "message": f"成功接收 {success_count} 个账号",
        "accepted_count": success_count,
        "batch_index": req.batch_index,
        "total_batches": req.total_batches,
        "total_uploaded": req.total_uploaded or success_count,
        "done": done,
    }

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
