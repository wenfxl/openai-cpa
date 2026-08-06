# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import random
import re
import string
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

SIGNUP_URL = "https://accounts.x.ai/sign-up?redirect=grok-com"


def _env_float(name: str, default: float) -> float:
    try:
        val = float(str(os.environ.get(name, "") or "").strip())
        return val if val > 0 else default
    except Exception:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        val = int(float(str(os.environ.get(name, "") or "").strip()))
        return val if val > 0 else default
    except Exception:
        return default


# 邮箱验证码等待参数（可用环境变量覆盖）
# 默认: 30 次 x 5s = 最长 150s 等待验证码邮件
CODE_FETCH_ATTEMPTS = _env_int("GROK_CODE_FETCH_ATTEMPTS", 30)
CODE_FETCH_INTERVAL = _env_float("GROK_CODE_FETCH_INTERVAL", 5.0)
# 验证码阶段允许在总 deadline 之外额外延长的秒数
CODE_WAIT_GRACE = _env_float("GROK_CODE_WAIT_GRACE", 240.0)
# 验证码输入页出现的等待轮数（每轮 1s）
CODE_PAGE_ATTEMPTS = _env_int("GROK_CODE_PAGE_ATTEMPTS", 40)

EMAIL_ENTRY_SELECTORS = [
    'button:has-text("Sign up with email")',
    'button:has-text("Sign up with Email")',
    'button:has-text("Continue with email")',
    'button:has-text("Continue with Email")',
    'button:has-text("使用邮箱注册")',
    'button:has-text("用邮箱注册")',
    'a:has-text("Sign up with email")',
    'a:has-text("使用邮箱注册")',
    '[role="button"]:has-text("Sign up with email")',
    '[role="button"]:has-text("使用邮箱注册")',
]

EMAIL_INPUT_SELECTORS = [
    'input[type="email"]',
    'input[name="email"]',
    'input[name="username"]',
    'input[data-testid="email"]',
    'input[autocomplete="email"]',
]

CODE_INPUT_SELECTORS = [
    'input[name="code"]',
    'input[data-input-otp="true"]',
    'input[autocomplete="one-time-code"]',
    'input[placeholder*="code" i]',
    'input[placeholder*="Code"]',
    'input[placeholder*="验证"]',
    'input[inputmode="numeric"]',
]

SUBMIT_SELECTORS = [
    'button[type="submit"]',
    'button[data-testid="continue"]',
    'button:has-text("Continue")',
    'button:has-text("Next")',
    'button:has-text("继续")',
    'button:has-text("下一步")',
    'button:has-text("Sign up")',
]

COMPLETE_SELECTORS = [
    'button:has-text("Complete sign up")',
    'button:has-text("Create account")',
    'button:has-text("Sign up")',
    'button:has-text("完成注册")',
    'button:has-text("创建账户")',
    'button:has-text("创建账号")',
    'button[type="submit"]',
]


def _log_fn(log: Optional[Callable[[str], None]]):
    return log if callable(log) else (lambda *_a, **_k: None)



def _build_proxy_config(proxy: Optional[str]) -> Optional[dict]:
    proxy = (proxy or "").strip()
    if not proxy:
        return None
    if proxy.startswith("socks5h://"):
        proxy = "socks5://" + proxy[len("socks5h://"):]
    try:
        parsed = urlparse(proxy)
        if not parsed.scheme or not parsed.hostname or not parsed.port:
            return {"server": proxy}
        cfg = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
        if parsed.username:
            cfg["username"] = parsed.username
        if parsed.password:
            cfg["password"] = parsed.password
        return cfg
    except Exception:
        return {"server": proxy}


def _debug_dir() -> Path:
    base = Path(__file__).resolve().parents[2] / "data" / "grok_browser_debug"
    try:
        base.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return base


def _dump_debug(page, tag: str) -> None:
    try:
        ts = time.strftime("%Y%m%d_%H%M%S")
        d = _debug_dir()
        png = d / f"{ts}_{tag}.png"
        html = d / f"{ts}_{tag}.html"
        try:
            page.screenshot(path=str(png), full_page=True)
        except Exception:
            try:
                page.screenshot(path=str(png))
            except Exception:
                pass
        try:
            html.write_text(page.content() or "", encoding="utf-8", errors="replace")
        except Exception:
            pass
    except Exception:
        pass


def _get_cookies(page, names: List[str]) -> Dict[str, str]:
    try:
        cookies = page.context.cookies()
    except Exception:
        return {}
    wanted = {n.lower() for n in names}
    out: Dict[str, str] = {}
    for c in cookies or []:
        name = str(c.get("name") or "")
        if name.lower() in wanted:
            val = str(c.get("value") or "").strip()
            if val:
                out[name] = val
    return out


def _all_cookies(page) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        for c in page.context.cookies() or []:
            name = str(c.get("name") or "")
            val = str(c.get("value") or "")
            if name and val:
                out[name] = val
    except Exception:
        pass
    return out


def _wait_for_cookies(page, names: List[str], timeout: float = 90.0) -> Dict[str, str]:
    deadline = time.time() + max(5.0, float(timeout))
    while time.time() < deadline:
        found = _get_cookies(page, names)
        if all(any(k.lower() == n.lower() for k in found) for n in names):
            return found
        if any(n.lower() == "sso" for n in names):
            rw = _get_cookies(page, ["sso", "sso-rw"])
            if rw.get("sso") or rw.get("sso-rw"):
                return rw
        time.sleep(1.0)
    return _get_cookies(page, list(names) + ["sso-rw"])


def _query_any(page, selectors: List[str]):
    for sel in selectors:
        try:
            el = page.query_selector(sel)
            if el:
                return el, sel
        except Exception:
            continue
    return None, ""


def _fill_selector(page, selector: str, value: str) -> bool:
    try:
        page.fill(selector, value, force=True)
        return True
    except Exception:
        pass
    try:
        loc = page.locator(selector).first
        loc.click(force=True)
        loc.fill(value, force=True)
        return True
    except Exception:
        pass
    try:
        page.focus(selector)
        page.keyboard.type(value, delay=20)
        return True
    except Exception:
        return False


def _click_first(page, selectors: List[str], *, force: bool = True) -> str:
    for sel in selectors:
        try:
            if page.query_selector(sel):
                page.click(sel, force=force, timeout=2500)
                return sel
        except Exception:
            continue
    return ""

def _click_email_signup(page, timeout: float = 12.0) -> bool:
    el, _ = _query_any(page, EMAIL_INPUT_SELECTORS)
    if el:
        return True

    deadline = time.time() + timeout
    js = (
        "() => {"
        "const nodes = Array.from(document.querySelectorAll('button, a, [role=\"button\"]'));"
        "const target = nodes.find((node) => {"
        "  const text = (node.innerText || node.textContent || '').replace(/\\s+/g, '').toLowerCase();"
        "  return ("
        "    text.includes('使用邮箱注册') ||"
        "    text.includes('用邮箱注册') ||"
        "    text.includes('signupwithemail') ||"
        "    text.includes('continuewithemail') ||"
        "    (text.includes('email') && (text.includes('sign') || text.includes('注册') || text.includes('continue')))"
        "  );"
        "});"
        "if (!target) return false;"
        "target.click();"
        "return true;"
        "}"
    )
    while time.time() < deadline:
        if _click_first(page, EMAIL_ENTRY_SELECTORS):
            time.sleep(1.2)
            el, _ = _query_any(page, EMAIL_INPUT_SELECTORS)
            if el:
                return True
        try:
            clicked = page.evaluate(js)
            if clicked:
                time.sleep(1.2)
                el, _ = _query_any(page, EMAIL_INPUT_SELECTORS)
                if el:
                    return True
        except Exception:
            pass
        time.sleep(0.6)
    return bool(_query_any(page, EMAIL_INPUT_SELECTORS)[0])


def _read_turnstile_token(page) -> str:
    try:
        tok = page.evaluate(
            """() => {
  const names = ["cf-turnstile-response", "g-recaptcha-response"];
  for (const name of names) {
    const nodes = document.querySelectorAll(
      'input[name="' + name + '"], textarea[name="' + name + '"], [name="' + name + '"]'
    );
    for (const node of nodes) {
      const val = (node.value || node.getAttribute("value") || "").trim();
      if (val && val.length > 20) return val;
    }
  }
  return "";
}"""
        )
        return str(tok or "").strip()
    except Exception:
        return ""


def _click_turnstile_if_any(page, rounds: int = 3, log=None) -> bool:
    left_targets = [
        (20, 0.50),
        (24, 0.50),
        (28, 0.52),
        (18, 0.48),
        (32, 0.50),
        (26, 0.45),
        (22, 0.55),
        (14, 0.50),
    ]

    def _try_click_box(box) -> bool:
        if not box:
            return False
        w = float(box.get("width") or 0)
        h = float(box.get("height") or 0)
        if w < 12 or h < 12:
            return False
        for dx, y_ratio in left_targets:
            try:
                x = float(box["x"]) + min(dx, max(8.0, w * 0.12))
                y = float(box["y"]) + h * float(y_ratio)
                try:
                    page.mouse.move(x, y)
                except Exception:
                    pass
                page.mouse.click(x, y)
                time.sleep(0.35)
                if _read_turnstile_token(page):
                    return True
            except Exception:
                continue
        return False

    for _round_i in range(max(1, int(rounds))):
        try:
            iframes = page.query_selector_all('iframe[src*="challenges.cloudflare.com"]') or []
        except Exception:
            iframes = []
        if not iframes:
            try:
                one = page.query_selector(
                    'iframe[src*="challenges.cloudflare.com"], .cf-turnstile iframe, .cf-turnstile'
                )
                iframes = [one] if one else []
            except Exception:
                iframes = []

        for handle in iframes:
            if not handle:
                continue
            try:
                box = handle.bounding_box()
            except Exception:
                box = None
            if _try_click_box(box):
                return True

        try:
            for sel in (".cf-turnstile", "[data-sitekey]", 'div[id^="cf-turnstile"]'):
                for node in page.query_selector_all(sel) or []:
                    try:
                        box = node.bounding_box()
                    except Exception:
                        box = None
                    if _try_click_box(box):
                        return True
        except Exception:
            pass

        try:
            frames = list(page.frames)
        except Exception:
            frames = []
        for frame in frames:
            try:
                url = (frame.url or "").lower()
            except Exception:
                url = ""
            if "challenges.cloudflare.com" not in url and "turnstile" not in url:
                continue
            for dx, dy in ((24, 28), (20, 30), (28, 26), (16, 32), (32, 30)):
                try:
                    frame.click("body", position={"x": dx, "y": dy}, force=True, timeout=1500)
                    time.sleep(0.35)
                    if _read_turnstile_token(page):
                        return True
                except Exception:
                    continue
            try:
                el = frame.query_selector("input[type='checkbox']")
                if el:
                    box = el.bounding_box()
                    if box:
                        x = float(box["x"]) + min(10.0, float(box["width"]) * 0.4)
                        y = float(box["y"]) + float(box["height"]) * 0.5
                        page.mouse.click(x, y)
                        time.sleep(0.35)
                        if _read_turnstile_token(page):
                            return True
            except Exception:
                pass

        time.sleep(0.4)

    return bool(_read_turnstile_token(page))


def _wait_turnstile_token(
    page,
    timeout: float = 60.0,
    log=None,
    *,
    rounds: int = 3,
    headless: bool = True,
    proxy: str = "",
) -> bool:
    lg = _log_fn(log)
    total = max(1, int(rounds or 1))
    per_round = max(8.0, float(timeout) / total)
    lg("CF人机校验进行中")

    for i in range(1, total + 1):
        deadline = time.time() + per_round
        while time.time() < deadline:
            if _read_turnstile_token(page):
                lg("CF人机校验已通过")
                return True
            _click_turnstile_if_any(page, rounds=2, log=lg)
            time.sleep(1.0)

        if _read_turnstile_token(page):
            lg("CF人机校验已通过")
            return True
        if i < total:
            lg(f"CF人机校验失败，重试中 {i}/{total}")

    lg("CF人机校验失败")
    return False


def _signup_on_page(
    page,
    *,
    email: str,
    password: str,
    fetch_code: Callable[[], str],
    headless: bool = True,
    given_name: str = "Alex",
    family_name: str = "Chen",
    timeout: float = 180.0,
    log: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    email = (email or "").strip()
    password = password or ""
    lg = _log_fn(log)
    if not email or not password:
        return {"ok": False, "error": "email/password empty"}

    if not given_name or given_name == "Alex":
        given_name = "".join(random.choices(string.ascii_lowercase, k=5)).capitalize()
    if not family_name or family_name == "Chen":
        family_name = "".join(random.choices(string.ascii_lowercase, k=5)).capitalize()

    deadline = time.time() + max(60.0, float(timeout or 180.0))

    try:
        page.set_default_timeout(20000)

        try:
            page.goto(SIGNUP_URL, wait_until="networkidle", timeout=60000)
        except Exception:
            page.goto(SIGNUP_URL, wait_until="domcontentloaded", timeout=60000)
        time.sleep(2.0)

        if not _click_email_signup(page, timeout=12.0):
            _dump_debug(page, "no_email_entry")
            return {"ok": False, "error": "未提交邮箱", "url": page.url}

        email_sel = ", ".join(EMAIL_INPUT_SELECTORS)
        try:
            page.wait_for_selector(email_sel, timeout=15000)
        except Exception:
            _click_email_signup(page, timeout=6.0)
            try:
                page.wait_for_selector(email_sel, timeout=10000)
            except Exception:
                _dump_debug(page, "email_input_missing")
                return {"ok": False, "error": "未提交邮箱", "url": page.url}

        if not _fill_selector(page, email_sel, email):
            _dump_debug(page, "email_fill_fail")
            return {"ok": False, "error": "邮箱提交失败", "url": page.url}

        if not _click_first(page, SUBMIT_SELECTORS):
            for txt in ("Continue", "Next", "继续", "下一步", "Sign up"):
                if _click_first(page, [f'button:has-text("{txt}")']):
                    break
        time.sleep(2.0)

        code_sel = ", ".join(CODE_INPUT_SELECTORS)
        code_ready = False
        for _ in range(CODE_PAGE_ATTEMPTS):
            if time.time() > deadline:
                break
            if page.query_selector(code_sel):
                code_ready = True
                break
            _click_first(page, SUBMIT_SELECTORS)
            time.sleep(1.0)

        if not code_ready:
            _dump_debug(page, "code_page_missing")
            return {"ok": False, "error": "验证码页未出现", "url": page.url}

        # 验证码邮件可能延迟，这里允许超出整体 deadline 一段宽限时间，
        # 否则 GROK_OAUTH_TIMEOUT 会在邮件到达前就把流程掐断。
        code = ""
        code_deadline = max(deadline, time.time() + CODE_WAIT_GRACE)
        total_attempts = max(1, CODE_FETCH_ATTEMPTS)
        for _attempt in range(1, total_attempts + 1):
            if time.time() > code_deadline:
                lg(f"验证码等待已达上限 ({_attempt - 1}/{total_attempts})")
                break
            try:
                code = str(fetch_code() or "").strip()
            except Exception as exc:
                lg(f"取验证码异常: {exc}")
                code = ""
            if code:
                break
            if _attempt % 5 == 0:
                lg(f"仍未收到验证码，继续等待 ({_attempt}/{total_attempts})")
            time.sleep(CODE_FETCH_INTERVAL)
        if not code:
            return {"ok": False, "error": "邮箱验证码超时", "url": page.url}

        # 验证码可能来得比较晚，把后续步骤的 deadline 往后顺延，
        # 避免拿到码却因为总超时而无法完成提交。
        deadline = max(deadline, time.time() + 120.0)

        clean_code = re.sub(r"[\s\-]+", "", code)
        try:
            page.fill(code_sel, clean_code, force=True)
        except Exception:
            try:
                page.locator(code_sel).first.press_sequentially(clean_code, delay=30)
            except Exception:
                try:
                    page.keyboard.type(clean_code, delay=30)
                except Exception:
                    return {"ok": False, "error": "验证码填写失败", "url": page.url}

        for sel in [
            'button:has-text("Confirm email")',
            'button:has-text("确认邮箱")',
            'button:has-text("Verify")',
            'button:has-text("Continue")',
            'button:has-text("继续")',
        ] + SUBMIT_SELECTORS:
            if _click_first(page, [sel]):
                break
        time.sleep(2.0)

        profile_sel = (
            'input[name="given_name"], input[name="givenName"], input[placeholder*="First"], '
            'input[name="password"], input[type="password"], input[data-testid="password"]'
        )
        for _ in range(20):
            if page.query_selector(profile_sel):
                break
            time.sleep(1.0)

        fname_sel = (
            'input[name="given_name"], input[name="givenName"], '
            'input[data-testid="givenName"], input[autocomplete="given-name"], '
            'input[placeholder*="First"]'
        )
        lname_sel = (
            'input[name="family_name"], input[name="familyName"], '
            'input[data-testid="familyName"], input[autocomplete="family-name"], '
            'input[placeholder*="Last"]'
        )
        if page.query_selector(fname_sel):
            _fill_selector(page, fname_sel, given_name)
        if page.query_selector(lname_sel):
            _fill_selector(page, lname_sel, family_name)

        pass_sel = 'input[name="password"], input[type="password"], input[data-testid="password"]'
        if page.query_selector(fname_sel) and not page.query_selector(pass_sel):
            _click_first(page, SUBMIT_SELECTORS)
            time.sleep(1.5)

        for _ in range(12):
            if page.query_selector(pass_sel):
                break
            time.sleep(1.0)

        if page.query_selector(pass_sel):
            _fill_selector(page, pass_sel, password)
            lg("邮箱与密码校验通过")
            try:
                for cb in page.query_selector_all('input[type="checkbox"]') or []:
                    try:
                        cb.click(force=True)
                    except Exception:
                        pass
            except Exception:
                pass

            ts_timeout = 90.0 if not headless else 60.0
            ts_rounds = 3
            ts_ok = _wait_turnstile_token(
                page,
                timeout=ts_timeout,
                log=lg,
                rounds=ts_rounds,
                headless=bool(headless),
            )
            if not ts_ok:
                return {
                    "ok": False,
                    "error": "CF人机校验失败（常见原因: 代理不通/过慢、目标打开失败）",
                    "url": page.url,
                }

            for attempt in range(1, 8):
                if _read_turnstile_token(page) or attempt >= 2:
                    _click_first(page, COMPLETE_SELECTORS, force=True)
                time.sleep(2.0)
                sso_now = _get_cookies(page, ["sso", "sso-rw"])
                if sso_now.get("sso") or sso_now.get("sso-rw"):
                    break
                url_l = (page.url or "").lower()
                if "sign-up" not in url_l and any(k in url_l for k in ("account", "grok.com", "consent")):
                    break
                if not _read_turnstile_token(page):
                    _click_turnstile_if_any(page, rounds=3, log=lg)

        remain = max(15.0, deadline - time.time())
        cookies_partial = _wait_for_cookies(page, ["sso"], timeout=min(90.0, remain))
        sso = str(cookies_partial.get("sso") or cookies_partial.get("sso-rw") or "").strip()
        all_ck = _all_cookies(page)
        if not sso:
            sso = str(all_ck.get("sso") or all_ck.get("sso-rw") or "").strip()
        if not sso:
            _dump_debug(page, "sso_missing")
            return {
                "ok": False,
                "error": "SSO 提取失败",
                "cookies": all_ck,
                "url": page.url,
            }

        all_ck.setdefault("sso", sso)
        all_ck.setdefault("sso-rw", sso)
        return {
            "ok": True,
            "sso": sso,
            "cookies": all_ck,
            "url": page.url,
            "email": email,
            "password": password,
        }
    except Exception as exc:
        detail = str(exc).strip() or repr(exc)
        low = detail.lower()
        if "geoip" in low:
            return {"ok": False, "error": "Camoufox geoip 依赖问题（当前已不强制 geoip）"}
        if "turnstile" in low or "timeout" in low:
            return {"ok": False, "error": "CF人机校验失败（常见原因: 代理不通/过慢、目标打开失败）"}
        short = detail.split(" at ")[0].split(" url=")[0]
        if len(short) > 120:
            short = short[:117] + "..."
        return {"ok": False, "error": short}


def _signup_with_browser(
    browser,
    *,
    email: str,
    password: str,
    fetch_code: Callable[[], str],
    proxy: str = "",
    headless: bool = True,
    given_name: str = "Alex",
    family_name: str = "Chen",
    timeout: float = 180.0,
    log: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    context = None
    try:
        ctx_opts: Dict[str, Any] = {}
        proxy_cfg = _build_proxy_config(proxy)
        if proxy_cfg:
            ctx_opts["proxy"] = proxy_cfg
        try:
            context = browser.new_context(**ctx_opts)
        except TypeError:
            context = browser.new_context()
        page = context.new_page()
        return _signup_on_page(
            page,
            email=email,
            password=password,
            fetch_code=fetch_code,
            headless=headless,
            given_name=given_name,
            family_name=family_name,
            timeout=timeout,
            log=log,
        )
    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass


def _signup_oneshot(
    *,
    email: str,
    password: str,
    fetch_code: Callable[[], str],
    proxy: str = "",
    headless: bool = True,
    given_name: str = "Alex",
    family_name: str = "Chen",
    timeout: float = 180.0,
    log: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    try:
        from camoufox.sync_api import Camoufox
    except Exception as exc:
        return {"ok": False, "error": f"camoufox import failed: {exc}"}

    try:
        from .embedded_turnstile import ensure_camoufox
        ok_c, msg_c = ensure_camoufox(force=False)
        if not ok_c:
            return {"ok": False, "error": msg_c}
    except Exception:
        pass

    proxy_cfg = _build_proxy_config(proxy)
    launch_opts: Dict[str, Any] = {"headless": bool(headless)}
    try:
        with Camoufox(**launch_opts) as browser:
            return _signup_with_browser(
                browser,
                email=email,
                password=password,
                fetch_code=fetch_code,
                proxy=proxy,
                headless=headless,
                given_name=given_name,
                family_name=family_name,
                timeout=timeout,
                log=log,
            )
    except Exception as exc:
        if proxy_cfg:
            launch_opts["proxy"] = proxy_cfg
        try:
            with Camoufox(**launch_opts) as browser:
                page = browser.new_page()
                return _signup_on_page(
                    page,
                    email=email,
                    password=password,
                    fetch_code=fetch_code,
                    headless=headless,
                    given_name=given_name,
                    family_name=family_name,
                    timeout=timeout,
                    log=log,
                )
        except Exception as exc2:
            detail = str(exc2).strip() or repr(exc2)
            low = detail.lower()
            if "geoip" in low:
                return {"ok": False, "error": "Camoufox geoip 依赖问题（当前已不强制 geoip）"}
            if "turnstile" in low or "timeout" in low:
                return {"ok": False, "error": "CF人机校验失败（常见原因: 代理不通/过慢、目标打开失败）"}
            short = detail.split(" at ")[0].split(" url=")[0]
            if len(short) > 120:
                short = short[:117] + "..."
            return {"ok": False, "error": short or (str(exc).strip() or repr(exc))}


def _pool_job(
    browser,
    *,
    email: str,
    password: str,
    fetch_code: Callable[[], str],
    proxy: str = "",
    signup_headless: bool = True,
    given_name: str = "Alex",
    family_name: str = "Chen",
    signup_timeout: float = 180.0,
    log: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    return _signup_with_browser(
        browser,
        email=email,
        password=password,
        fetch_code=fetch_code,
        proxy=proxy,
        headless=bool(signup_headless),
        given_name=given_name,
        family_name=family_name,
        timeout=float(signup_timeout or 180.0),
        log=log,
    )


def _signup_sync(
    *,
    email: str,
    password: str,
    fetch_code: Callable[[], str],
    proxy: str = "",
    headless: bool = True,
    given_name: str = "Alex",
    family_name: str = "Chen",
    timeout: float = 180.0,
    log: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    email = (email or "").strip()
    password = password or ""
    if not email or not password:
        return {"ok": False, "error": "email/password empty"}

    try:
        from .embedded_turnstile import ensure_camoufox
        ok_c, msg_c = ensure_camoufox(force=False)
        if not ok_c:
            return {"ok": False, "error": msg_c}
    except Exception:
        pass

    try:
        from .browser_pool import ensure_browser_pool, run_with_browser
        ensure_browser_pool(headless=bool(headless))
    except Exception:
        return _signup_oneshot(
            email=email,
            password=password,
            fetch_code=fetch_code,
            proxy=proxy or "",
            headless=bool(headless),
            given_name=given_name,
            family_name=family_name,
            timeout=timeout,
            log=log,
        )

    try:
        wait_timeout = max(90.0, float(timeout or 180.0) + 60.0)
        return run_with_browser(
            _pool_job,
            headless=bool(headless),
            timeout=wait_timeout,
            email=email,
            password=password,
            fetch_code=fetch_code,
            proxy=proxy or "",
            signup_headless=bool(headless),
            given_name=given_name,
            family_name=family_name,
            signup_timeout=float(timeout or 180.0),
            log=log,
        )
    except Exception as exc:
        detail = str(exc).strip() or repr(exc)
        low = detail.lower()
        if "geoip" in low:
            return {"ok": False, "error": "Camoufox geoip 依赖问题（当前已不强制 geoip）"}
        if "turnstile" in low or "timeout" in low:
            return {"ok": False, "error": "CF人机校验失败（常见原因: 代理不通/过慢、目标打开失败）"}
        short = detail.split(" at ")[0].split(" url=")[0]
        if len(short) > 120:
            short = short[:117] + "..."
        return {"ok": False, "error": short}


def signup_with_camoufox(
    email: str,
    password: str,
    *,
    fetch_code: Callable[[], str],
    proxy: str = "",
    headless: bool = True,
    given_name: str = "Alex",
    family_name: str = "Chen",
    timeout: float = 180.0,
    log: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    env_h = str(os.environ.get("GROK_BROWSER_SIGNUP_HEADLESS", "") or "").strip().lower()
    if env_h in {"0", "false", "no", "off"}:
        headless = False
    elif env_h in {"1", "true", "yes", "on"}:
        headless = True

    return _signup_sync(
        email=email,
        password=password,
        fetch_code=fetch_code,
        proxy=proxy,
        headless=headless,
        given_name=given_name,
        family_name=family_name,
        timeout=timeout,
        log=log,
    )

