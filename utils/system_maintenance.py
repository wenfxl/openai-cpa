import os
import platform
from typing import Any, Dict, Optional


def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def get_disk_usage_percent(target_path: str) -> Optional[int]:
    try:
        usage = os.statvfs(target_path)
    except Exception:
        return None
    total_blocks = int(getattr(usage, "f_blocks", 0) or 0)
    available_blocks = int(getattr(usage, "f_bavail", 0) or 0)
    if total_blocks <= 0:
        return None
    used_ratio = 1 - (available_blocks / total_blocks)
    return max(0, min(100, int(round(used_ratio * 100))))


def get_cleanup_status(base_dir: str) -> Dict[str, Any]:
    target_path = os.getenv("DISK_CLEANUP_TARGET_PATH", "/")
    threshold = _to_int(os.getenv("DISK_CLEANUP_THRESHOLD_PERCENT"), 80)
    app_dir = os.getenv("OPAIRE_APP_DIR", base_dir)
    script_path = os.path.join(base_dir, "scripts", "server_disk_cleanup.sh")
    disk_used_percent = get_disk_usage_percent(target_path) if platform.system().lower() == "linux" else None
    return {
        "platform": platform.system(),
        "is_linux": platform.system().lower() == "linux",
        "script_path": script_path,
        "script_exists": os.path.isfile(script_path),
        "target_path": target_path,
        "app_dir": app_dir,
        "threshold_percent": threshold,
        "disk_used_percent": disk_used_percent,
        "can_run": platform.system().lower() == "linux" and os.path.isfile(script_path),
    }
