import os

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from global_state import verify_token
from utils.browser_request_recorder import browser_request_recorder


router = APIRouter()
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class EdgeLaunchReq(BaseModel):
    port: int = 9222
    start_url: str = "https://www.bing.com"
    user_data_dir: str = "data/edge-recorder-profile"
    reuse_existing: bool = True


class TargetConnectReq(BaseModel):
    port: int = 9222
    target_id: str = ""
    target_ws_url: str = ""


class CaptureStartReq(TargetConnectReq):
    resource_types: list[str] = ["document", "fetch", "xhr"]
    url_keyword: str = ""
    clear_existing: bool = False


class SaveRequestCodeReq(BaseModel):
    request_id: str
    output_path: str = ""
    client: str = "requests"
    include_sensitive: bool = False


@router.get("/edge-monitor")
async def get_edge_monitor_page():
    html_path = os.path.join(BASE_DIR, "static", "edge_monitor.html")
    if not os.path.exists(html_path):
        return HTMLResponse(content="<h1>找不到 edge_monitor.html</h1>", status_code=404)
    with open(html_path, "r", encoding="utf-8") as handle:
        return HTMLResponse(content=handle.read(), headers={"Cache-Control": "no-store"})


@router.get("/api/browser_monitor/state")
async def get_browser_monitor_state(token: str = Depends(verify_token)):
    return {"status": "success", "data": browser_request_recorder.get_state()}


@router.get("/api/browser_monitor/targets")
async def get_browser_monitor_targets(port: int = Query(9222), token: str = Depends(verify_token)):
    try:
        targets = browser_request_recorder.list_targets(port=port)
        return {"status": "success", "data": targets}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.post("/api/browser_monitor/launch_edge")
async def launch_edge_monitor(req: EdgeLaunchReq, token: str = Depends(verify_token)):
    try:
        return browser_request_recorder.launch_edge(
            port=req.port,
            start_url=req.start_url,
            user_data_dir=req.user_data_dir,
            reuse_existing=req.reuse_existing,
        )
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.post("/api/browser_monitor/connect")
async def connect_edge_monitor(req: TargetConnectReq, token: str = Depends(verify_token)):
    try:
        return browser_request_recorder.connect(port=req.port, target_id=req.target_id, target_ws_url=req.target_ws_url)
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.post("/api/browser_monitor/start")
async def start_edge_monitor(req: CaptureStartReq, token: str = Depends(verify_token)):
    try:
        return browser_request_recorder.start_capture(
            port=req.port,
            target_id=req.target_id,
            target_ws_url=req.target_ws_url,
            resource_types=req.resource_types,
            url_keyword=req.url_keyword,
            clear_existing=req.clear_existing,
        )
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.post("/api/browser_monitor/stop")
async def stop_edge_monitor(token: str = Depends(verify_token)):
    try:
        return browser_request_recorder.stop_capture()
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.post("/api/browser_monitor/clear")
async def clear_edge_monitor_requests(token: str = Depends(verify_token)):
    try:
        return browser_request_recorder.clear_requests()
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.get("/api/browser_monitor/requests")
async def get_edge_monitor_requests(
    limit: int = Query(50),
    resource_type: str = Query(""),
    url_keyword: str = Query(""),
    token: str = Depends(verify_token),
):
    return {
        "status": "success",
        "data": browser_request_recorder.list_requests(
            limit=limit,
            resource_type=resource_type,
            url_keyword=url_keyword,
        ),
    }


@router.get("/api/browser_monitor/request_code")
async def get_edge_monitor_request_code(
    request_id: str = Query(...),
    client: str = Query("requests"),
    include_sensitive: bool = Query(False),
    token: str = Depends(verify_token),
):
    try:
        code = browser_request_recorder.generate_code(
            request_id=request_id,
            client=client,
            include_sensitive=include_sensitive,
        )
        return {"status": "success", "data": {"request_id": request_id, "client": client, "code": code}}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.post("/api/browser_monitor/save_code")
async def save_edge_monitor_request_code(req: SaveRequestCodeReq, token: str = Depends(verify_token)):
    try:
        return browser_request_recorder.save_code(
            request_id=req.request_id,
            output_path=req.output_path,
            client=req.client,
            include_sensitive=req.include_sensitive,
        )
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
