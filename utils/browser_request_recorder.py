import asyncio
import json
import os
import pprint
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qsl

import requests
import websockets


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_PROFILE_DIR = BASE_DIR / "data" / "edge-recorder-profile"
DEFAULT_RESOURCE_TYPES = ["document", "fetch", "xhr"]
SENSITIVE_HEADERS = {
    "authorization",
    "cookie",
    "proxy-authorization",
    "x-api-key",
    "x-auth-token",
    "x-csrf-token",
    "set-cookie",
}
SKIP_HEADERS = {"host", "content-length", "connection"}
EDGE_BINARY_CANDIDATES = [
    shutil.which("msedge.exe"),
    shutil.which("msedge"),
    shutil.which("microsoft-edge"),
    os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
    os.path.join(os.environ.get("PROGRAMFILES", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
]


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _normalize_headers(headers: dict[str, Any]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in (headers or {}).items():
        text_key = _stringify(key).strip()
        if not text_key:
            continue
        normalized[text_key] = _stringify(value)
    return normalized


def _guess_payload_kind(body: str, headers: dict[str, str]) -> tuple[str, Any]:
    payload = _stringify(body)
    if not payload:
        return "none", None

    content_type = _stringify(headers.get("Content-Type") or headers.get("content-type")).split(";", 1)[0].strip().lower()
    if content_type.endswith("+json") or content_type == "application/json":
        try:
            return "json", json.loads(payload)
        except Exception:
            return "raw", payload

    if content_type == "application/x-www-form-urlencoded":
        pairs = parse_qsl(payload, keep_blank_values=True)
        keys = [key for key, _ in pairs]
        if len(set(keys)) == len(keys):
            return "form", {key: value for key, value in pairs}
        return "form", pairs

    stripped = payload.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            return "json", json.loads(stripped)
        except Exception:
            pass

    return "raw", payload


class BrowserRequestRecorder:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._requests: list[dict[str, Any]] = []
        self._requests_by_id: dict[str, dict[str, Any]] = {}
        self._extra_headers: dict[str, dict[str, str]] = {}
        self._devtools_capture_index: dict[str, list[str]] = {}
        self._request_seq = 0
        self._message_seq = 0
        self._max_requests = 300
        self._port = 9222
        self._selected_target: dict[str, Any] = {}
        self._capture_filters = {"resource_types": list(DEFAULT_RESOURCE_TYPES), "url_keyword": ""}
        self._capture_stop = threading.Event()
        self._capture_thread: Optional[threading.Thread] = None
        self._capture_running = False
        self._capture_error = ""
        self._capture_started_at = 0.0
        self._last_request_at = 0.0
        self._launched_process: Optional[subprocess.Popen] = None
        self._edge_binary = ""

    def _next_message_id(self) -> int:
        with self._lock:
            self._message_seq += 1
            return self._message_seq

    def _next_capture_id(self) -> str:
        with self._lock:
            self._request_seq += 1
            return f"edge_req_{int(time.time() * 1000)}_{self._request_seq}"

    def _set_capture_state(self, running: bool, error: str = "") -> None:
        with self._lock:
            self._capture_running = running
            self._capture_error = error.strip()
            if running and not self._capture_started_at:
                self._capture_started_at = time.time()
            if not running:
                self._capture_started_at = 0.0

    def _get_process_alive(self) -> bool:
        with self._lock:
            process = self._launched_process
        return bool(process and process.poll() is None)

    def _wait_for_debug_port(self, port: int, timeout_sec: float = 12.0) -> bool:
        deadline = time.time() + max(1.0, float(timeout_sec))
        while time.time() < deadline:
            if self.is_debug_endpoint_available(port):
                return True
            time.sleep(0.35)
        return False

    def find_edge_executable(self) -> str:
        for candidate in EDGE_BINARY_CANDIDATES:
            if candidate and os.path.isfile(candidate):
                return candidate
        raise FileNotFoundError("找不到 Edge 可执行文件，请确认已安装 Microsoft Edge。")

    def is_debug_endpoint_available(self, port: int) -> bool:
        try:
            response = requests.get(f"http://127.0.0.1:{int(port)}/json/version", timeout=2.5)
            return response.status_code == 200
        except Exception:
            return False

    def launch_edge(
        self,
        port: int = 9222,
        start_url: str = "about:blank",
        user_data_dir: str = "",
        reuse_existing: bool = True,
    ) -> dict[str, Any]:
        port = int(port or 9222)
        if reuse_existing and self.is_debug_endpoint_available(port):
            with self._lock:
                self._port = port
            return {
                "status": "success",
                "message": f"已复用现有调试端口 {port}",
                "port": port,
                "reused_existing": True,
            }

        edge_binary = self.find_edge_executable()
        profile_dir = Path(user_data_dir).expanduser() if user_data_dir else DEFAULT_PROFILE_DIR
        if not profile_dir.is_absolute():
            profile_dir = BASE_DIR / profile_dir
        profile_dir.mkdir(parents=True, exist_ok=True)

        command = [
            edge_binary,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={str(profile_dir)}",
            "--new-window",
            _stringify(start_url).strip() or "about:blank",
        ]
        process = subprocess.Popen(command)
        if not self._wait_for_debug_port(port):
            raise RuntimeError(f"Edge 已启动，但在端口 {port} 上没有发现 DevTools 调试接口。")

        with self._lock:
            self._launched_process = process
            self._edge_binary = edge_binary
            self._port = port

        return {
            "status": "success",
            "message": "Edge 调试窗口已启动",
            "port": port,
            "profile_dir": str(profile_dir),
            "edge_binary": edge_binary,
            "reused_existing": False,
        }

    def list_targets(self, port: Optional[int] = None) -> list[dict[str, Any]]:
        active_port = int(port or self._port or 9222)
        response = requests.get(f"http://127.0.0.1:{active_port}/json", timeout=3.0)
        response.raise_for_status()
        targets = []
        for item in response.json():
            if _stringify(item.get("type")).lower() != "page":
                continue
            websocket_url = _stringify(item.get("webSocketDebuggerUrl")).strip()
            if not websocket_url:
                continue
            url = _stringify(item.get("url")).strip()
            if url.startswith(("devtools://", "edge://", "chrome-extension://")):
                continue
            targets.append(
                {
                    "id": _stringify(item.get("id")),
                    "title": _stringify(item.get("title")) or "(untitled)",
                    "url": url,
                    "type": _stringify(item.get("type")) or "page",
                    "websocket_url": websocket_url,
                    "attached": bool(item.get("attached")),
                }
            )
        return targets

    def connect(self, port: int = 9222, target_id: str = "", target_ws_url: str = "") -> dict[str, Any]:
        targets = self.list_targets(port)
        selected = None
        for item in targets:
            if target_ws_url and item["websocket_url"] == target_ws_url:
                selected = item
                break
            if target_id and item["id"] == target_id:
                selected = item
                break

        if selected is None:
            if target_id or target_ws_url:
                raise ValueError("没有找到指定的 Edge 页面，请刷新页面目标列表后重试。")
            selected = next((item for item in targets if item["url"] and item["url"] != "about:blank"), None)
            if selected is None and targets:
                selected = targets[0]

        if selected is None:
            raise ValueError("当前没有可连接的 Edge 页面。")

        with self._lock:
            self._port = int(port or self._port or 9222)
            self._selected_target = dict(selected)

        return {"status": "success", "target": dict(selected)}

    def clear_requests(self) -> dict[str, Any]:
        with self._lock:
            self._requests.clear()
            self._requests_by_id.clear()
            self._extra_headers.clear()
            self._devtools_capture_index.clear()
            self._last_request_at = 0.0
        return {"status": "success", "message": "已清空捕获记录"}

    def start_capture(
        self,
        port: int = 9222,
        target_id: str = "",
        target_ws_url: str = "",
        resource_types: Optional[list[str]] = None,
        url_keyword: str = "",
        clear_existing: bool = False,
    ) -> dict[str, Any]:
        with self._lock:
            if self._capture_running:
                return {"status": "success", "message": "监听已在运行中", "state": self.get_state()}

        if clear_existing:
            self.clear_requests()
        connect_result = self.connect(port=port, target_id=target_id, target_ws_url=target_ws_url)

        normalized_types = [str(item or "").strip().lower() for item in (resource_types or DEFAULT_RESOURCE_TYPES)]
        normalized_types = [item for item in normalized_types if item]
        if not normalized_types:
            normalized_types = list(DEFAULT_RESOURCE_TYPES)

        with self._lock:
            self._capture_filters = {
                "resource_types": normalized_types,
                "url_keyword": _stringify(url_keyword).strip().lower(),
            }
            self._capture_error = ""

        self._capture_stop.clear()
        capture_thread = threading.Thread(target=self._capture_worker, daemon=True, name="edge-request-capture")
        with self._lock:
            self._capture_thread = capture_thread
            self._capture_started_at = time.time()
        capture_thread.start()
        return {
            "status": "success",
            "message": "已开始监听 Edge 网络请求",
            "target": connect_result["target"],
            "state": self.get_state(),
        }

    def stop_capture(self) -> dict[str, Any]:
        self._capture_stop.set()
        thread = None
        with self._lock:
            thread = self._capture_thread
        if thread and thread.is_alive():
            thread.join(timeout=4.0)
        self._set_capture_state(False, self._capture_error)
        return {"status": "success", "message": "已停止监听", "state": self.get_state()}

    def _capture_worker(self) -> None:
        self._set_capture_state(True, "")
        try:
            asyncio.run(self._capture_loop())
        except Exception as exc:
            self._set_capture_state(False, str(exc))
        else:
            self._set_capture_state(False, "")

    async def _send_devtools_command(self, websocket, method: str, params: Optional[dict[str, Any]] = None) -> None:
        message = {"id": self._next_message_id(), "method": method}
        if params:
            message["params"] = params
        await websocket.send(json.dumps(message, ensure_ascii=False))

    async def _capture_loop(self) -> None:
        with self._lock:
            target = dict(self._selected_target)
            filters = dict(self._capture_filters)

        if not target:
            raise RuntimeError("尚未选择 Edge 页面目标。")

        websocket_url = _stringify(target.get("websocket_url")).strip()
        if not websocket_url:
            refreshed = self.connect(port=self._port, target_id=target.get("id", ""))
            websocket_url = refreshed["target"]["websocket_url"]

        async with websockets.connect(websocket_url, ping_interval=20, ping_timeout=20, max_size=16 * 1024 * 1024) as ws:
            await self._send_devtools_command(ws, "Network.enable")
            await self._send_devtools_command(ws, "Page.enable")
            while not self._capture_stop.is_set():
                try:
                    raw_message = await asyncio.wait_for(ws.recv(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                try:
                    payload = json.loads(raw_message)
                except Exception:
                    continue

                method = _stringify(payload.get("method"))
                params = payload.get("params") or {}
                if method == "Network.requestWillBeSent":
                    self._handle_request_event(params, filters)
                elif method == "Network.requestWillBeSentExtraInfo":
                    self._merge_extra_headers(params)

    def _merge_extra_headers(self, params: dict[str, Any]) -> None:
        request_id = _stringify(params.get("requestId"))
        if not request_id:
            return
        headers = _normalize_headers(params.get("headers") or {})
        if not headers:
            return
        with self._lock:
            current = dict(self._extra_headers.get(request_id) or {})
            current.update(headers)
            self._extra_headers[request_id] = current
            for capture_id in self._devtools_capture_index.get(request_id) or []:
                captured = self._requests_by_id.get(capture_id)
                if captured:
                    merged = dict(captured.get("headers") or {})
                    merged.update(headers)
                    captured["headers"] = merged

    def _handle_request_event(self, params: dict[str, Any], filters: dict[str, Any]) -> None:
        request = params.get("request") or {}
        url = _stringify(request.get("url")).strip()
        if not url.startswith(("http://", "https://")):
            return

        resource_type = _stringify(params.get("type")).strip().lower()
        filter_types = set(filters.get("resource_types") or DEFAULT_RESOURCE_TYPES)
        if resource_type and filter_types and resource_type not in filter_types:
            return

        keyword = _stringify(filters.get("url_keyword")).strip().lower()
        if keyword and keyword not in url.lower():
            return

        devtools_request_id = _stringify(params.get("requestId"))
        headers = _normalize_headers(request.get("headers") or {})
        with self._lock:
            extra_headers = dict(self._extra_headers.get(devtools_request_id) or {})
        if extra_headers:
            headers.update(extra_headers)

        captured_id = self._next_capture_id()
        record = {
            "id": captured_id,
            "devtools_request_id": devtools_request_id,
            "method": _stringify(request.get("method")).upper() or "GET",
            "url": url,
            "headers": headers,
            "body": _stringify(request.get("postData")),
            "resource_type": resource_type or "other",
            "document_url": _stringify(params.get("documentURL")),
            "initiator_type": _stringify((params.get("initiator") or {}).get("type")),
            "captured_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        }
        with self._lock:
            self._requests.append(record)
            self._requests_by_id[captured_id] = record
            if devtools_request_id:
                bucket = self._devtools_capture_index.setdefault(devtools_request_id, [])
                bucket.append(captured_id)
            self._last_request_at = time.time()
            while len(self._requests) > self._max_requests:
                removed = self._requests.pop(0)
                self._requests_by_id.pop(removed["id"], None)
                removed_devtools_id = _stringify(removed.get("devtools_request_id"))
                if removed_devtools_id and removed_devtools_id in self._devtools_capture_index:
                    self._devtools_capture_index[removed_devtools_id] = [
                        item for item in self._devtools_capture_index[removed_devtools_id]
                        if item != removed["id"]
                    ]
                    if not self._devtools_capture_index[removed_devtools_id]:
                        self._devtools_capture_index.pop(removed_devtools_id, None)

    def list_requests(self, limit: int = 50, resource_type: str = "", url_keyword: str = "") -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._requests)
        result = []
        filter_resource_type = _stringify(resource_type).strip().lower()
        filter_keyword = _stringify(url_keyword).strip().lower()
        for item in reversed(items):
            if filter_resource_type and _stringify(item.get("resource_type")).lower() != filter_resource_type:
                continue
            if filter_keyword and filter_keyword not in _stringify(item.get("url")).lower():
                continue
            result.append(
                {
                    "id": item["id"],
                    "method": item["method"],
                    "url": item["url"],
                    "resource_type": item["resource_type"],
                    "document_url": item["document_url"],
                    "initiator_type": item["initiator_type"],
                    "captured_at": item["captured_at"],
                    "has_body": bool(item.get("body")),
                    "body_preview": _stringify(item.get("body"))[:240],
                }
            )
            if len(result) >= max(1, int(limit or 50)):
                break
        return result

    def get_request(self, request_id: str) -> dict[str, Any]:
        with self._lock:
            item = self._requests_by_id.get(_stringify(request_id))
            if not item:
                raise KeyError("未找到指定请求记录。")
            return dict(item)

    def _prepare_headers_for_codegen(self, headers: dict[str, str], include_sensitive: bool) -> dict[str, str]:
        prepared: dict[str, str] = {}
        for key, value in (headers or {}).items():
            lower_key = _stringify(key).strip().lower()
            if not lower_key or lower_key in SKIP_HEADERS:
                continue
            if include_sensitive or lower_key not in SENSITIVE_HEADERS:
                prepared[key] = _stringify(value)
            else:
                prepared[key] = f"<redacted:{key}>"
        return prepared

    def generate_code(self, request_id: str, client: str = "requests", include_sensitive: bool = False) -> str:
        client_name = _stringify(client).strip().lower() or "requests"
        if client_name != "requests":
            raise ValueError("当前仅支持生成 requests 版本代码。")

        item = self.get_request(request_id)
        headers = self._prepare_headers_for_codegen(item.get("headers") or {}, include_sensitive)
        payload_kind, payload_value = _guess_payload_kind(item.get("body", ""), item.get("headers") or {})

        lines = [
            "import requests",
            "",
            f"url = {item['url']!r}",
            f"headers = {pprint.pformat(headers, sort_dicts=False, width=100)}",
        ]

        request_args = [
            f"    method={item['method']!r},",
            "    url=url,",
            "    headers=headers,",
        ]

        if payload_kind == "json":
            lines.append(f"json_data = {pprint.pformat(payload_value, sort_dicts=False, width=100)}")
            request_args.append("    json=json_data,")
        elif payload_kind == "form":
            lines.append(f"form_data = {pprint.pformat(payload_value, sort_dicts=False, width=100)}")
            request_args.append("    data=form_data,")
        elif payload_kind == "raw":
            lines.append(f"payload = {pprint.pformat(payload_value, sort_dicts=False, width=100)}")
            request_args.append("    data=payload,")

        request_args.append("    timeout=30,")
        lines.extend(
            [
                "",
                "response = requests.request(",
                *request_args,
                ")",
                "",
                "print(response.status_code)",
                "print(response.text[:1000])",
            ]
        )
        return "\n".join(lines)

    def _resolve_output_path(self, output_path: str, request_id: str) -> Path:
        relative_path = _stringify(output_path).strip() or f"data/browser_monitor/{request_id}.py"
        candidate = Path(relative_path).expanduser()
        if not candidate.is_absolute():
            candidate = BASE_DIR / candidate
        resolved = candidate.resolve()
        base_resolved = BASE_DIR.resolve()
        if resolved != base_resolved and base_resolved not in resolved.parents:
            raise ValueError("输出路径必须位于当前项目目录内。")
        return resolved

    def save_code(
        self,
        request_id: str,
        output_path: str = "",
        client: str = "requests",
        include_sensitive: bool = False,
    ) -> dict[str, Any]:
        code = self.generate_code(request_id=request_id, client=client, include_sensitive=include_sensitive)
        target_path = self._resolve_output_path(output_path, request_id)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(code, encoding="utf-8")
        return {
            "status": "success",
            "message": "代码已写入项目目录",
            "path": str(target_path),
        }

    def get_state(self) -> dict[str, Any]:
        with self._lock:
            target = dict(self._selected_target)
            request_count = len(self._requests)
            filters = dict(self._capture_filters)
            capture_running = self._capture_running
            capture_error = self._capture_error
            started_at = self._capture_started_at
            last_request_at = self._last_request_at
            port = self._port
            edge_binary = self._edge_binary

        return {
            "port": port,
            "capture_running": capture_running,
            "capture_error": capture_error,
            "selected_target": target,
            "request_count": request_count,
            "filters": filters,
            "capture_started_at": started_at,
            "last_request_at": last_request_at,
            "debug_endpoint_ready": self.is_debug_endpoint_available(port),
            "launched_edge_alive": self._get_process_alive(),
            "edge_binary": edge_binary,
        }


browser_request_recorder = BrowserRequestRecorder()
