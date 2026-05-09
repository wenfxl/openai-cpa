"""Memory usage prediction and runtime measurement helpers.

This module intentionally keeps psutil optional at import time so the web
console can still boot in older deployments. When psutil is installed, the
API returns actual RSS/VMS/percent information in addition to the static
prediction model.
"""

import os
import platform
import sys
import time
from typing import Any, Dict, Iterable


MB = 1024 * 1024


def _to_int(value: Any, default: int = 0, minimum: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, parsed)


def _is_enabled(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _list_len(value: Any) -> int:
    if isinstance(value, (list, tuple, set)):
        return len(value)
    if isinstance(value, str):
        return len([item for item in value.splitlines() if item.strip()])
    return 0


def _get_nested(config: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    cur: Any = config
    for key in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key, default)
    return cur


def _round_mb(value: float) -> float:
    return round(float(value), 2)


def _proxy_pool_size(config: Dict[str, Any]) -> int:
    raw_proxy_pool = config.get("raw_proxy_pool") or {}
    raw_enabled = _is_enabled(raw_proxy_pool.get("enable"))
    raw_count = _list_len(raw_proxy_pool.get("proxy_list"))
    if raw_enabled and raw_count:
        return raw_count

    clash_conf = config.get("clash_proxy_pool") or {}
    clash_enabled = _is_enabled(clash_conf.get("enable"))
    pool_mode = _is_enabled(clash_conf.get("pool_mode"))
    warp_count = _list_len(config.get("warp_proxy_list"))
    if clash_enabled and pool_mode and warp_count:
        return warp_count
    return 1 if config.get("default_proxy") else 0


def _mail_domain_count(config: Dict[str, Any]) -> int:
    domains = config.get("mail_domains")
    if isinstance(domains, str):
        return len([item for item in domains.split(",") if item.strip()])
    return _list_len(domains)


def predict_memory_usage(config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Estimate low/mid/high memory usage in MB from current configuration."""
    config = config or {}
    os_name = platform.system() or sys.platform
    is_windows = os_name.lower().startswith("windows")

    reg_threads = _to_int(config.get("reg_threads"), 3, 1)
    cpa_threads = _to_int(_get_nested(config, "cpa_mode", "threads", default=10), 10, 1)
    sub2api_threads = _to_int(_get_nested(config, "sub2api_mode", "threads", default=10), 10, 1)
    max_executor_workers = max(reg_threads, cpa_threads, sub2api_threads)
    max_log_lines = _to_int(config.get("max_log_lines"), 500, 1)
    proxy_pool_size = _proxy_pool_size(config)
    mail_domain_count = _mail_domain_count(config)

    enable_multi_thread = _is_enabled(config.get("enable_multi_thread_reg"))
    enable_cpa = _is_enabled(_get_nested(config, "cpa_mode", "enable", default=False))
    enable_sub2api = _is_enabled(_get_nested(config, "sub2api_mode", "enable", default=False))
    cluster_enabled = bool(str(config.get("cluster_master_url", "") or "").strip())
    db_type = str(_get_nested(config, "database", "type", default=config.get("db_type", "sqlite")) or "sqlite").lower()

    # Conservative estimates based on this FastAPI + threaded worker workload.
    python_runtime_mb = 35.0
    web_stack_mb = 18.0
    db_mb = 12.0 if db_type == "mysql" else 5.0
    config_mb = 2.0
    log_history_mb = max(0.1, max_log_lines * 0.002)
    cluster_mb = 6.0 if cluster_enabled else 1.0
    proxy_mb = proxy_pool_size * 0.15
    mail_domain_mb = mail_domain_count * 0.03

    thread_stack_mb = 1.5 if is_windows else 8.0
    worker_state_mb = 5.0
    health_worker_mb = 2.0
    engine_thread_mb = thread_stack_mb + 1.0

    low_threads = 1
    mid_reg_workers = reg_threads if enable_multi_thread else 1
    mid_health_workers = cpa_threads if enable_cpa else (sub2api_threads if enable_sub2api else 0)
    high_reg_workers = max_executor_workers
    high_health_workers = max(cpa_threads, sub2api_threads) if (enable_cpa or enable_sub2api) else max(cpa_threads, sub2api_threads) * 0.35

    base_breakdown = {
        "python_runtime_mb": python_runtime_mb,
        "fastapi_uvicorn_mb": web_stack_mb,
        "database_mb": db_mb,
        "config_mb": config_mb,
        "log_history_mb": _round_mb(log_history_mb),
        "cluster_mb": cluster_mb,
        "proxy_pool_mb": _round_mb(proxy_mb),
        "mail_domain_tracking_mb": _round_mb(mail_domain_mb),
    }

    base_total = sum(base_breakdown.values())

    low = base_total + engine_thread_mb + low_threads * 1.0
    mid = base_total + engine_thread_mb + mid_reg_workers * (thread_stack_mb + worker_state_mb) + mid_health_workers * health_worker_mb
    high = base_total + engine_thread_mb + high_reg_workers * (thread_stack_mb + worker_state_mb) + high_health_workers * health_worker_mb + proxy_pool_size * 0.05

    return {
        "predicted_mb": {
            "low": _round_mb(low),
            "mid": _round_mb(mid),
            "high": _round_mb(high),
        },
        "breakdown": {
            **base_breakdown,
            "engine_thread_mb": _round_mb(engine_thread_mb),
            "registration_worker_mb_each": _round_mb(thread_stack_mb + worker_state_mb),
            "health_worker_mb_each": health_worker_mb,
        },
        "config_snapshot": {
            "os": os_name,
            "enable_multi_thread_reg": enable_multi_thread,
            "reg_threads": reg_threads,
            "cpa_threads": cpa_threads,
            "sub2api_threads": sub2api_threads,
            "max_executor_workers": max_executor_workers,
            "max_log_lines": max_log_lines,
            "proxy_pool_size": proxy_pool_size,
            "mail_domain_count": mail_domain_count,
            "db_type": db_type,
            "cluster_enabled": cluster_enabled,
        },
        "model_note": "估算值用於容量預警；實際 RSS 仍會因作業系統、curl/HTTP 連線、第三方套件快取而浮動。",
    }


def get_actual_memory_usage() -> Dict[str, Any]:
    """Return actual process memory via psutil when available."""
    try:
        import psutil  # type: ignore
    except Exception as exc:
        return {
            "available": False,
            "rss_mb": None,
            "vms_mb": None,
            "percent": None,
            "system_total_mb": None,
            "system_available_mb": None,
            "note": f"未安裝 psutil，僅提供靜態預測。請安裝 psutil 以取得實測 RSS。({exc})",
        }

    process = psutil.Process(os.getpid())
    info = process.memory_info()
    virtual = psutil.virtual_memory()
    return {
        "available": True,
        "rss_mb": _round_mb(info.rss / MB),
        "vms_mb": _round_mb(info.vms / MB),
        "percent": round(process.memory_percent(), 2),
        "system_total_mb": _round_mb(virtual.total / MB),
        "system_available_mb": _round_mb(virtual.available / MB),
        "system_used_percent": round(virtual.percent, 2),
        "pid": process.pid,
        "timestamp": time.time(),
    }


def estimate_safety_status(prediction: Dict[str, Any], actual: Dict[str, Any]) -> Dict[str, Any]:
    predicted = prediction.get("predicted_mb", {}) if isinstance(prediction, dict) else {}
    high = float(predicted.get("high") or 0)
    mid = float(predicted.get("mid") or 0)
    rss = actual.get("rss_mb") if isinstance(actual, dict) else None
    system_total = actual.get("system_total_mb") if isinstance(actual, dict) else None

    if rss is None:
        return {
            "level": "unknown",
            "label": "無實測資料",
            "message": "psutil 不可用，目前只能查看靜態預測。",
        }

    level = "ok"
    label = "正常"
    message = "目前 RSS 位於預測區間內。"

    if system_total and rss > float(system_total) * 0.8:
        level = "critical"
        label = "危險"
        message = "目前進程 RSS 已超過系統記憶體 80%，建議立即降低併發或重啟釋放資源。"
    elif high and rss > high * 1.2:
        level = "warning"
        label = "偏高"
        message = "目前 RSS 明顯高於高標預測，可能存在連線快取、第三方套件快取或長時間運行累積。"
    elif mid and rss > mid:
        level = "watch"
        label = "觀察"
        message = "目前 RSS 高於中標預測但仍未超出高標太多，建議持續觀察。"

    return {
        "level": level,
        "label": label,
        "message": message,
        "rss_vs_high_ratio": round(rss / high, 2) if high else None,
    }


def build_memory_report(config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    prediction = predict_memory_usage(config or {})
    actual = get_actual_memory_usage()
    safety = estimate_safety_status(prediction, actual)
    return {
        "status": "success",
        "prediction": prediction,
        "actual": actual,
        "safety": safety,
        "timestamp": time.time(),
    }
