# -*- coding: utf-8 -*-
"""可选插件加载器：扫描 plugin/*/manifest.json。"""
from __future__ import annotations

import importlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[1]
_PLUGIN_ROOT = _ROOT / "plugin"


def discover_plugins() -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    if not _PLUGIN_ROOT.is_dir():
        return items
    for child in sorted(_PLUGIN_ROOT.iterdir()):
        if not child.is_dir():
            continue
        manifest = child / "manifest.json"
        if not manifest.is_file():
            continue
        meta: Dict[str, Any] = {
            "id": child.name,
            "title": child.name,
            "version": "",
            "enabled": True,
            "path": str(child),
        }
        try:
            data = json.loads(manifest.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict):
                meta["id"] = str(data.get("name") or child.name)
                meta["title"] = str(data.get("title") or meta["id"])
                meta["version"] = str(data.get("version") or "")
                meta["description"] = str(data.get("description") or "")
                meta["ui"] = bool(data.get("ui", True))
        except Exception as exc:
            meta["enabled"] = False
            meta["error"] = f"manifest parse failed: {exc}"
        try:
            mod = importlib.import_module(f"plugin.{child.name}")
            if hasattr(mod, "is_available"):
                meta["enabled"] = bool(mod.is_available())
            if hasattr(mod, "get_meta"):
                extra = mod.get_meta() or {}
                if isinstance(extra, dict):
                    meta.update(extra)
                    meta["enabled"] = bool(extra.get("enabled", meta.get("enabled", True)))
        except Exception as exc:
            meta["enabled"] = False
            meta["error"] = str(exc)
        items.append(meta)
    return items


def register_all(app) -> List[str]:
    loaded: List[str] = []
    if not _PLUGIN_ROOT.is_dir():
        return loaded
    for child in sorted(_PLUGIN_ROOT.iterdir()):
        if not child.is_dir() or not (child / "manifest.json").is_file():
            continue
        try:
            mod = importlib.import_module(f"plugin.{child.name}")
            ok = False
            if hasattr(mod, "register"):
                ok = bool(mod.register(app))
            elif hasattr(mod, "get_router"):
                router = mod.get_router()
                if router is not None:
                    app.include_router(router)
                    ok = True
            if ok:
                loaded.append(child.name)
                logger.info("plugin loaded: %s", child.name)
        except Exception as exc:
            logger.warning("plugin load failed %s: %s", child.name, exc)
    return loaded

