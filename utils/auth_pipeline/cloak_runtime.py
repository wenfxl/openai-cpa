"""Shared CloakBrowser runtime and page helpers.

Platform-specific registration flows live in separate modules and import these
helpers. Keep browser launch/thread handling here so new platforms do not need
to duplicate lifecycle code.
"""
from __future__ import annotations

import asyncio
import os
import platform
import random
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

from utils import config as cfg


GEOIP_DB_FILENAME = "GeoLite2-City.mmdb"
GEOIP_METADATA_MARKER = b"\xab\xcd\xefMaxMind.com"
GEOIP_MIN_BYTES = 1_000_000
PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCAL_RUNTIME_DIRNAME = "cloakbrowser-runtime"


def log(message: str) -> None:
    print(f"[{cfg.ts()}] [Cloak] {message}")


def check_stop() -> None:
    if getattr(cfg, "GLOBAL_STOP", False):
        raise RuntimeError("任务已停止")


def to_ms(seconds: Any, default: int = 30) -> int:
    try:
        value = int(seconds)
    except Exception:
        value = default
    return max(1, value) * 1000


def action_timeout_ms() -> int:
    return to_ms(getattr(cfg, "CLOAK_ACTION_TIMEOUT", 30), 30)


def navigation_timeout_ms() -> int:
    return to_ms(getattr(cfg, "CLOAK_NAVIGATION_TIMEOUT", 90), 90)


def human_pause(min_seconds: float = 0.08, max_seconds: float = 0.25) -> None:
    if not bool(getattr(cfg, "CLOAK_HUMANIZE", True)):
        return
    time.sleep(random.uniform(min_seconds, max_seconds))


def page_url(page: Any) -> str:
    try:
        return str(page.url or "")
    except Exception:
        return ""


def safe_url_for_log(url: str) -> str:
    try:
        parts = urlsplit(str(url or ""))
    except (TypeError, ValueError):
        return ""
    if parts.scheme and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}{parts.path or '/'}"
    return parts.path or ""


def safe_proxy_for_log(proxy: Optional[str]) -> str:
    try:
        parts = urlsplit(str(proxy or ""))
    except (TypeError, ValueError):
        return "configured"
    if not parts.hostname:
        return "configured"
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme or 'http'}://{parts.hostname}{port}"


def normalize_browser_proxy(proxy: Optional[str]) -> Optional[str]:
    value = str(proxy or "").strip()
    if not value:
        return None
    return value.replace("socks5h://", "socks5://")


def is_target_closed(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "target closed",
            "target page",
            "context or browser has been closed",
            "browser has been closed",
        )
    )


def safe_evaluate(page: Any, script: str, arg: Any = None) -> Any:
    try:
        return page.evaluate(script, arg) if arg is not None else page.evaluate(script)
    except Exception as exc:
        if is_target_closed(exc):
            raise
        return None


def visible_locator(page: Any, selectors: list[str], timeout_ms: int = 800) -> Any:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.is_visible(timeout=timeout_ms):
                return locator
        except Exception:
            continue
    return None


def _is_humanize_enabled() -> bool:
    return bool(getattr(cfg, "CLOAK_HUMANIZE", True))


def _human_key_delay_ms() -> int:
    return random.randint(55, 145)


def human_press(page: Any, key: str) -> None:
    try:
        page.keyboard.press(key)
    except Exception:
        if key == "ControlOrMeta+A":
            page.keyboard.press("Control+A")
        else:
            raise
    human_pause(0.04, 0.12)


def human_select(locator: Any, value: str) -> None:
    human_pause(0.08, 0.2)
    locator.select_option(str(value), timeout=4000)
    human_pause(0.1, 0.28)


def human_type(page: Any, text: str, delay_ms: Optional[int] = None) -> None:
    try:
        page.keyboard.type(
            str(text),
            delay=delay_ms if delay_ms is not None else _human_key_delay_ms(),
        )
    finally:
        human_pause(0.04, 0.12)


def new_action_marker(label: str = "action") -> str:
    safe_label = "".join(ch for ch in str(label or "action") if ch.isalnum() or ch in "-_")
    return f"{safe_label or 'action'}-{time.time_ns()}-{random.randint(1000, 9999)}"


def action_locator(page: Any, marker: str) -> Any:
    return page.locator(f"[data-cloak-action='{marker}']").first


def clear_action_marker(page: Any, marker: str) -> None:
    safe_evaluate(
        page,
        """marker => {
          for (const el of document.querySelectorAll(`[data-cloak-action="${marker}"]`)) {
            el.removeAttribute('data-cloak-action');
          }
        }""",
        marker,
    )


def human_input_value(locator: Any) -> str:
    try:
        return str(locator.input_value(timeout=2000) or "")
    except Exception:
        return ""


def human_fill_verified(page: Any, locator: Any, value: str) -> None:
    text = str(value)
    human_fill(page, locator, text)
    actual = human_input_value(locator)
    if actual == text:
        return
    try:
        locator.evaluate(
            """(el, value) => {
              const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
              if (setter) setter.call(el, value); else el.value = value;
              el.dispatchEvent(new InputEvent('input', {
                bubbles: true, composed: true, inputType: 'insertText', data: value
              }));
              el.dispatchEvent(new Event('change', {bubbles: true, composed: true}));
            }""",
            text,
        )
    except Exception:
        pass
    actual = human_input_value(locator)
    if actual != text:
        raise RuntimeError(
            f"输入框写入未生效，期望长度={len(text)}，实际长度={len(actual)}"
        )


def human_click_marked(page: Any, marker: str, timeout: int = 4000) -> bool:
    locator = action_locator(page, marker)
    try:
        clicked = human_click(locator, timeout=timeout)
        return clicked
    finally:
        clear_action_marker(page, marker)


def human_fill(page: Any, locator: Any, value: str) -> None:
    text = str(value)
    try:
        locator.scroll_into_view_if_needed(timeout=2500)
    except Exception:
        pass
    clicked = False
    if _is_humanize_enabled():
        try:
            locator.click(timeout=3000, delay=random.randint(35, 110))
            clicked = True
        except Exception:
            try:
                locator.focus(timeout=1500)
            except Exception:
                pass
        human_pause(0.12, 0.32)
        try:
            if clicked and hasattr(page, "keyboard"):
                human_press(page, "ControlOrMeta+A")
                human_press(page, "Backspace")
            locator.press_sequentially(text, delay=_human_key_delay_ms(), timeout=5000)
            human_pause(0.15, 0.4)
            return
        except Exception:
            pass
        try:
            if not clicked:
                locator.focus(timeout=1500)
            human_press(page, "ControlOrMeta+A")
            human_type(page, text, delay_ms=_human_key_delay_ms())
            human_pause(0.15, 0.4)
            return
        except Exception:
            pass
    else:
        try:
            locator.click(timeout=3000)
        except Exception:
            try:
                locator.focus(timeout=1500)
            except Exception:
                pass
    try:
        locator.fill(text, timeout=5000)
    except Exception:
        try:
            locator.evaluate(
                """(el, value) => {
                  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
                  if (setter) setter.call(el, value); else el.value = value;
                  el.dispatchEvent(new Event('input', {bubbles:true}));
                  el.dispatchEvent(new Event('change', {bubbles:true}));
                }""",
                text,
            )
        except Exception:
            human_press(page, "ControlOrMeta+A")
            human_type(page, text, delay_ms=random.randint(45, 100))
    human_pause(0.12, 0.35)


def human_click(locator: Any, timeout: int = 4000) -> bool:
    try:
        locator.scroll_into_view_if_needed(timeout=1800)
    except Exception:
        pass
    human_pause(0.1, 0.28)
    try:
        locator.click(timeout=timeout, delay=random.randint(45, 140))
        return True
    except Exception:
        try:
            locator.click(timeout=timeout, force=True)
            return True
        except Exception:
            return False


def maybe_accept_cookies(page: Any) -> None:
    selectors = [
        "button:has-text('Accept all')",
        "button:has-text('Accept')",
        "button:has-text('同意')",
        "button:has-text('接受')",
        "button:has-text('I agree')",
    ]
    locator = visible_locator(page, selectors, timeout_ms=500)
    if locator is not None:
        human_click(locator, timeout=2500)


def cloudflare_timeout_seconds() -> float:
    try:
        value = float(str(os.environ.get("CLOAK_CLOUDFLARE_TIMEOUT", "120") or "120"))
    except Exception:
        value = 120.0
    return max(30.0, value)


def try_cloudflare_challenge(page: Any) -> bool:
    """Click a visible Turnstile checkbox using the project's existing helper."""
    try:
        from utils.grok_auth.browser_signup import _click_turnstile_if_any

        _click_turnstile_if_any(page, rounds=1, log=log)
        return True
    except Exception as exc:
        if is_target_closed(exc):
            raise
        log(f"CF 自动处理异常: {exc}")
        return False


def is_valid_geoip_db(path: Path) -> bool:
    try:
        size = path.stat().st_size
        if size < GEOIP_MIN_BYTES:
            return False
        with path.open("rb") as handle:
            handle.seek(max(0, size - 128 * 1024))
            return GEOIP_METADATA_MARKER in handle.read()
    except OSError:
        return False


def runtime_candidate_dirs(cache_dir: str = "") -> list[Path]:
    candidates = [
        Path(cache_dir) if cache_dir else None,
        Path.home() / ".cloakbrowser",
        PROJECT_ROOT,
        PROJECT_ROOT / LOCAL_RUNTIME_DIRNAME,
        PROJECT_ROOT / ".cloakbrowser",
    ]
    result = []
    seen = set()
    for candidate in candidates:
        if candidate is None:
            continue
        key = str(candidate.resolve())
        if key not in seen:
            seen.add(key)
            result.append(candidate)
    return result


def find_local_geoip_db(candidates: list[Path]) -> Optional[Path]:
    for base in candidates:
        for path in (
            base / "geoip" / GEOIP_DB_FILENAME,
            base / GEOIP_DB_FILENAME,
        ):
            if is_valid_geoip_db(path):
                return path
    return None


def find_local_browser(candidates: list[Path]) -> Optional[Path]:
    override = str(os.environ.get("CLOAKBROWSER_BINARY_PATH") or "").strip()
    if override:
        path = Path(override).expanduser()
        if path.is_file():
            return path

    if platform.system() == "Darwin":
        executables = (
            Path("Chromium.app") / "Contents" / "MacOS" / "Chromium",
            Path("chrome"),
        )
        versioned_pattern = "chromium-*/Chromium.app/Contents/MacOS/Chromium"
    elif platform.system() == "Windows":
        executables = (Path("chrome.exe"),)
        versioned_pattern = "chromium-*/chrome.exe"
    else:
        executables = (Path("chrome"),)
        versioned_pattern = "chromium-*/chrome"

    for base in candidates:
        for relative in executables:
            path = base / relative
            if path.is_file():
                return path
        for path in sorted(base.glob(versioned_pattern), reverse=True):
            if path.is_file():
                return path
    return None


def configure_runtime() -> None:
    cache_dir = str(getattr(cfg, "CLOAK_CACHE_DIR", "") or "").strip()
    if cache_dir:
        cache_dir = os.path.abspath(
            os.path.expandvars(os.path.expanduser(cache_dir))
        )
    configured_env_cache = str(os.environ.get("CLOAKBROWSER_CACHE_DIR") or "").strip()
    candidates = runtime_candidate_dirs(cache_dir or configured_env_cache)

    geoip_path = find_local_geoip_db(candidates)
    if geoip_path is None:
        raise RuntimeError(
            "Cloak 的 GeoIP 数据库缺失或损坏。\n"
            f"请确认项目存在: {PROJECT_ROOT / LOCAL_RUNTIME_DIRNAME / 'geoip' / GEOIP_DB_FILENAME}"
        )

    browser_path = find_local_browser(candidates)
    if browser_path is None:
        raise RuntimeError(
            "Cloak 未找到浏览器可执行文件。\n"
            f"请将 {PROJECT_ROOT / LOCAL_RUNTIME_DIRNAME / 'cloakbrowser-windows-x64.zip'} "
            "解压到项目根目录。"
        )

    os.environ["CLOAKBROWSER_CACHE_DIR"] = str(geoip_path.parent.parent.resolve())
    os.environ["CLOAKBROWSER_BINARY_PATH"] = str(browser_path.resolve())
    os.environ["CLOAKBROWSER_AUTO_UPDATE"] = "false"


def launch_browser(
    proxy: Optional[str],
    *,
    locale: str = "",
    timezone_name: str = "",
    geoip: bool = True,
) -> tuple[Any, Any, Any, Any]:
    configure_runtime()
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError as exc:
        raise RuntimeError("缺少 playwright，请执行: pip install playwright") from exc
    try:
        from cloakbrowser import ensure_binary, launch, launch_persistent_context
    except ImportError as exc:
        raise RuntimeError(
            "缺少 cloak，请先执行: pip install \"cloakbrowser[geoip]>=0.6.5,<0.7\""
        ) from exc

    # CloakBrowser starts and owns its Playwright instance inside launch().
    playwright = None
    raw_proxy = str(proxy or "").strip()
    if not raw_proxy:
        raise RuntimeError(
            "Cloak 注册未配置代理。请先配置 default_proxy，"
            "避免使用本机公网 IP 注册。"
        )
    if not bool(getattr(cfg, "CLOAK_USE_PROXY", True)):
        raise RuntimeError(
            "Cloak 已配置代理，但 use_proxy 已关闭；"
            "为避免暴露本机出口 IP，已拒绝直连。"
        )
    proxy_url = normalize_browser_proxy(raw_proxy)
    if not proxy_url:
        raise RuntimeError("Cloak 代理地址无效，请检查 default_proxy。")
    options = {
        "headless": bool(getattr(cfg, "CLOAK_HEADLESS", True)),
        "humanize": bool(getattr(cfg, "CLOAK_HUMANIZE", True)),
        "geoip": bool(geoip),
    }
    locale = str(locale or getattr(cfg, "CLOAK_LOCALE", "") or "").strip()
    timezone_name = str(timezone_name or getattr(cfg, "CLOAK_TIMEZONE", "") or "").strip()
    if locale:
        options["locale"] = locale
    if timezone_name:
        options["timezone"] = timezone_name
    if proxy_url:
        options["proxy"] = proxy_url
    extra_args = list(getattr(cfg, "CLOAK_EXTRA_ARGS", []) or [])
    fingerprint_seed = str(getattr(cfg, "CLOAK_FINGERPRINT_SEED", "") or "").strip()
    if fingerprint_seed:
        extra_args.append(f"--fingerprint={fingerprint_seed}")
    if extra_args:
        options["args"] = extra_args
    license_key = str(getattr(cfg, "CLOAK_LICENSE_KEY", "") or "").strip()
    if license_key:
        options["license_key"] = license_key

    try:
        ensure_binary(license_key=license_key or None)
    except Exception as exc:
        raise RuntimeError(
            "Cloak 浏览器安装或检查失败。\n"
            "请确认 Cloak runtime 已下载并解压。\n"
            f"原始错误: {exc}"
        ) from exc

    context_kwargs = {}
    if locale:
        context_kwargs["locale"] = locale
    if timezone_name:
        context_kwargs["timezone_id"] = timezone_name

    def _start():
        user_data_dir = str(getattr(cfg, "CLOAK_USER_DATA_DIR", "") or "").strip()
        if user_data_dir:
            context = launch_persistent_context(user_data_dir, **options)
            browser = getattr(context, "browser", None) or context
            page = context.pages[0] if getattr(context, "pages", None) else context.new_page()
        else:
            browser = launch(**options)
            context = browser.new_context(**context_kwargs)
            page = context.pages[0] if getattr(context, "pages", None) else context.new_page()
        page.set_default_timeout(action_timeout_ms())
        page.set_default_navigation_timeout(navigation_timeout_ms())
        return playwright, browser, context, page

    log("正在启动引擎")
    return _start()


def close_browser(playwright: Any, browser: Any, context: Any) -> None:
    if bool(getattr(cfg, "CLOAK_KEEP_BROWSER_OPEN", False)):
        return
    try:
        if context is not None:
            context.close()
    except Exception:
        pass
    try:
        if browser is not None and browser is not context:
            browser.close()
    except Exception:
        pass
    try:
        if playwright is not None:
            playwright.stop()
    except Exception:
        pass


def run_with_thread_affinity(
    sync_runner: Callable[..., dict],
    *,
    thread_name: str,
    proxy: Optional[str],
    run_ctx: Optional[dict] = None,
    assigned_domain: Optional[str] = None,
    batch_id: Optional[int] = None,
    worker_index: Optional[int] = None,
) -> dict:
    """Run sync Playwright outside an active asyncio loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return sync_runner(
            proxy,
            run_ctx=run_ctx,
            assigned_domain=assigned_domain,
            batch_id=batch_id,
            worker_index=worker_index,
        )

    outcome: dict[str, Any] = {}

    def _worker() -> None:
        try:
            outcome["result"] = sync_runner(
                proxy,
                run_ctx=run_ctx,
                assigned_domain=assigned_domain,
                batch_id=batch_id,
                worker_index=worker_index,
            )
        except BaseException as exc:
            outcome["error"] = exc

    worker = threading.Thread(target=_worker, name=thread_name, daemon=True)
    worker.start()
    worker.join()

    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("result") or {}
