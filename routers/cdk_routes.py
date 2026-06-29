import asyncio
import time
from typing import Optional, List
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from global_state import VALID_TOKENS, verify_token, cdk_log_history, cdk_engine
from utils import db_manager
from utils.config import reload_all_configs
import utils.config as cfg

router = APIRouter()


# ─────────── Request Models ───────────

class CdkStartReq(BaseModel):
    target_count: int = 0
    concurrency: int = 1

class CdkPoolImportReq(BaseModel):
    raw_text: str

class CdkPoolDeleteReq(BaseModel):
    ids: List[int]


# ─────────── Engine Control ───────────

@router.post("/api/cdk/start")
async def cdk_start(req: CdkStartReq, token: str = Depends(verify_token)):
    if cdk_engine.is_running():
        return {"status": "error", "message": "CDK 激活引擎已在运行中"}
    try:
        reload_all_configs()
    except Exception:
        pass
    pool_stats = db_manager.get_cdk_pool_stats()
    if pool_stats["unused"] == 0:
        return {"status": "error", "message": "CDK 池中没有可用的 CDK，请先导入"}
    default_proxy = getattr(cfg, 'DEFAULT_PROXY', None) or ""
    cdk_engine.start(
        target_count=req.target_count,
        concurrency=max(req.concurrency, 1),
        proxy=default_proxy,
    )
    return {"status": "success", "message": f"CDK 激活引擎已启动 (目标: {req.target_count or '无限'}, 并发: {req.concurrency})"}


@router.post("/api/cdk/stop")
async def cdk_stop(token: str = Depends(verify_token)):
    if not cdk_engine.is_running():
        return {"status": "warning", "message": "当前没有运行中的 CDK 激活任务"}
    cdk_engine.stop()
    return {"status": "success", "message": "CDK 激活引擎已停止"}


@router.get("/api/cdk/status")
async def cdk_status(token: str = Depends(verify_token)):
    stats = cdk_engine.get_stats()
    return {"status": "success", **stats}


# ─────────── CDK Pool CRUD ───────────

@router.post("/api/cdk/pool/import")
async def cdk_pool_import(req: CdkPoolImportReq, token: str = Depends(verify_token)):
    lines = [line.strip() for line in req.raw_text.strip().splitlines() if line.strip()]
    if not lines:
        return {"status": "error", "message": "没有检测到有效的 CDK"}
    count = db_manager.import_cdk_codes(lines)
    return {"status": "success", "message": f"成功导入 {count} 个 CDK (共 {len(lines)} 行)", "imported": count}


@router.get("/api/cdk/pool")
async def cdk_pool_list(token: str = Depends(verify_token)):
    items = db_manager.get_cdk_pool_list()
    stats = db_manager.get_cdk_pool_stats()
    return {"status": "success", "items": items, "stats": stats}


@router.post("/api/cdk/pool/delete")
async def cdk_pool_delete(req: CdkPoolDeleteReq, token: str = Depends(verify_token)):
    ok = db_manager.delete_cdk_codes(req.ids)
    return {"status": "success" if ok else "error", "message": f"已删除 {len(req.ids)} 个 CDK" if ok else "删除失败"}


@router.post("/api/cdk/pool/clear")
async def cdk_pool_clear(token: str = Depends(verify_token)):
    ok = db_manager.clear_cdk_pool()
    return {"status": "success" if ok else "error", "message": "CDK 池已清空" if ok else "清空失败"}


# ─────────── Log Stream ───────────

@router.get("/api/cdk/logs/stream")
async def cdk_log_stream(request: Request, token: str = Query(None)):
    if token not in VALID_TOKENS:
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="Unauthorized")

    async def log_generator():
        current_snapshot = list(cdk_log_history)
        for old_msg in current_snapshot:
            yield f"data: {old_msg}\n\n"
        last_sent_msg = current_snapshot[-1] if current_snapshot else None
        idle_loops = 0

        try:
            while True:
                if await request.is_disconnected():
                    break
                snap = list(cdk_log_history)
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


@router.post("/api/cdk/logs/clear")
async def cdk_log_clear(token: str = Depends(verify_token)):
    cdk_log_history.clear()
    return {"status": "success"}
