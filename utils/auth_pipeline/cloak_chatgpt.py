"""ChatGPT registration through CloakBrowser.

This module owns the OpenAI-specific signup DOM flow. Protocol OAuth remains in
the existing register/oauth pipeline after this step returns a web session.
"""
from __future__ import annotations

import json
import random
import re
import time
from datetime import date
from typing import Any, Optional
from urllib.parse import urlsplit

from utils import config as cfg
from utils.email_providers.mail_service import (
    get_email_and_token,
    get_oai_code,
    mask_email,
    set_last_email,
)

from . import cloak_runtime as runtime
from .user_utils import _generate_password


START_URL = "https://chatgpt.com/auth/login"
PASSWORD_URL = "https://auth.openai.com/create-account/password"

_log = runtime.log
_check_stop = runtime.check_stop
_to_ms = runtime.to_ms
_action_timeout_ms = runtime.action_timeout_ms
_navigation_timeout_ms = runtime.navigation_timeout_ms
_human_pause = runtime.human_pause
_page_url = runtime.page_url
_safe_url_for_log = runtime.safe_url_for_log
_is_target_closed = runtime.is_target_closed
_safe_evaluate = runtime.safe_evaluate
_visible_locator = runtime.visible_locator
_human_fill = runtime.human_fill
_human_click = runtime.human_click
_human_click_marked = runtime.human_click_marked
_human_press = runtime.human_press
_human_select = runtime.human_select
_human_type = runtime.human_type
_new_action_marker = runtime.new_action_marker
_maybe_accept_cookies = runtime.maybe_accept_cookies
_cloudflare_timeout_seconds = runtime.cloudflare_timeout_seconds
_try_cloudflare_challenge = runtime.try_cloudflare_challenge


def _url_path(url: str) -> str:
    try:
        path = urlsplit(str(url or "")).path or "/"
    except (TypeError, ValueError):
        return ""
    path = re.sub(r"/+", "/", path).rstrip("/")
    return (path or "/").lower()


def _is_profile_path(path: str) -> bool:
    return any(
        path == suffix or path.endswith(suffix)
        for suffix in (
            "/about-you",
            "/create-account/about",
            "/create-account/about-you",
            "/create-account/profile",
        )
    )


def _url_hostname(url: str) -> str:
    try:
        return str(urlsplit(str(url or "")).hostname or "").lower()
    except ValueError:
        return ""


def _is_chatgpt_url(url: str) -> bool:
    host = _url_hostname(url)
    return host == "chatgpt.com" or host.endswith(".chatgpt.com")


def _is_auth_callback_url(url: str) -> bool:
    return _is_chatgpt_url(url) and _url_path(url).startswith("/api/auth/callback/")


def _is_auth_flow_url(url: str) -> bool:
    host = _url_hostname(url)
    path = _url_path(url)
    if host == "auth.openai.com":
        return True
    return _is_chatgpt_url(url) and (
        path == "/auth"
        or path.startswith("/auth/")
        or path == "/api/auth"
        or path.startswith("/api/auth/")
    )


def _can_open_chatgpt_home(url: str, state: str, has_cloudflare: bool) -> bool:
    return (
        state == "chatgpt"
        and not has_cloudflare
        and _is_chatgpt_url(url)
        and not _is_auth_flow_url(url)
    )


def _classify_page_state(snapshot: dict) -> str:
    url = str(snapshot.get("url") or "")
    path = _url_path(url)
    if path.endswith("/log-in/password"):
        return "login_password"
    if bool(snapshot.get("hasOtp")) or any(
        path.endswith(suffix)
        for suffix in ("/email-verification", "/email-otp", "/verify-email")
    ):
        return "email_verification"
    if bool(snapshot.get("hasPassword")):
        return "password"
    if _is_profile_path(path) and bool(snapshot.get("profileFormVisible")):
        return "profile"
    if bool(snapshot.get("hasCloudflare")):
        return "cloudflare"
    if _is_chatgpt_url(url) and not _is_auth_flow_url(url):
        return "chatgpt"
    return "other"


def _page_error_summary(info: dict, limit: int = 4) -> str:
    """Return visible page errors without changing the registration state."""
    errors = []
    for item in (info or {}).get("errors") or []:
        text = re.sub(r"\s+", " ", str(item or "")).strip()
        if text and text not in errors:
            errors.append(text[:300])
    return " | ".join(errors[:limit])


_TERMINAL_ACCOUNT_CREATE_ERROR = re.compile(
    r"failed to create account|failed to sign up|unable to create account"
    r"|too many attempts|创建账户失败|注册失败",
    re.IGNORECASE,
)


class AccountEmailBlocked(RuntimeError):
    """The signup page rejected account creation for the assigned email."""


def _is_terminal_account_create_error(info: dict) -> bool:
    return bool(_TERMINAL_ACCOUNT_CREATE_ERROR.search(_page_error_summary(info)))


def _account_create_error_detail(info: dict, page: Any = None) -> str:
    details = []
    detail = _page_error_summary(info)
    if detail:
        details.append(f"页面提示: {detail}")
    if page is not None:
        url = _safe_url_for_log(_page_url(page))
        if url:
            details.append(f"当前页面: {url}")
    return "; ".join(details) or "账户创建被拒绝"


def _mark_account_email_blocked(run_ctx: Optional[dict]) -> None:
    if run_ctx is None:
        return
    run_ctx["account_email_blocked"] = True
    run_ctx["discarded_email_failure"] = True
    run_ctx["signup_blocked"] = True


def _page_state(page: Any) -> dict:
    result = _safe_evaluate(
        page,
        r"""() => {
          const url = String(location.href || '');
          const path = (String(location.pathname || '/').toLowerCase().replace(/\/+$/, '') || '/');
          const visible = el => {
            if (!el) return false;
            const st = getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            return st.visibility !== 'hidden' && st.display !== 'none' && rect.width > 0 && rect.height > 0;
          };
          const hasVisible = selector => Array.from(document.querySelectorAll(selector)).some(visible);
          const bodyText = String(document.body && document.body.innerText || '');
          const body = bodyText.toLowerCase();
          const cleanText = value => String(value || '').replace(/\s+/g, ' ').trim();
          const singleDigitInputs = [...document.querySelectorAll("input[maxlength='1']")].filter(visible);
          const hasOtp = path.includes('email-verification') || path.includes('email-otp')
            || path.includes('verify-email')
            || hasVisible("input[name='code']")
            || hasVisible("input[name='otp']")
            || hasVisible("input[name*='code' i]")
            || hasVisible("input[name*='otp' i]")
            || hasVisible("input[autocomplete='one-time-code']")
            || singleDigitInputs.length >= 4;
          const hasPassword = path.endsWith('/create-account/password')
            || path.endsWith('/u/signup/password')
            || path.endsWith('/signup/password')
            || hasVisible("input[type='password']")
            || hasVisible("input[name='password']")
            || hasVisible("input[autocomplete='new-password']");
          const controlText = el => {
            const labelledBy = String(el.getAttribute('aria-labelledby') || '').split(/\s+/)
              .map(id => document.getElementById(id))
              .filter(Boolean)
              .map(node => node.innerText || node.textContent || '')
              .join(' ');
            const labels = [...(el.labels || [])].map(node => node.innerText || node.textContent || '').join(' ');
            return [
              el.name, el.id, el.placeholder, el.getAttribute('aria-label'), labelledBy, labels,
              el.getAttribute('autocomplete'), el.getAttribute('data-testid'),
              el.getAttribute('data-type'), el.getAttribute('role')
            ].filter(Boolean).join(' ').toLowerCase();
          };
          const controls = [...document.querySelectorAll(
            "input,textarea,select,[role='spinbutton'],[role='combobox']"
          )].filter(visible);
          const profileControls = controls.map(el => ({el, text: controlText(el)}));
          const hasNameField = profileControls.some(item =>
            /(full.?name|first.?name|last.?name|given.?name|family.?name|surname|姓名|名字|(^|[\s_-])name([\s_-]|$))/.test(item.text)
            && !/(email|phone|password|code|otp|username)/.test(item.text)
          );
          const hasBirthField = profileControls.some(item =>
            /(birth|birthday|date.?of.?birth|\bdob\b|\bage\b|year|month|day|生日|年龄|出生日期)/.test(item.text)
          );
          const profileFormVisible = hasNameField && hasBirthField;
          const cfFrameVisible = [
            ...document.querySelectorAll(
              "iframe[src*='challenges.cloudflare.com'],iframe[src*='turnstile']"
            )
          ].some(visible);
          const cfWidgetVisible = hasVisible(
            ".cf-turnstile,[data-sitekey],#challenge-form,#challenge-stage,#cf-challenge-running,.cf-browser-verification"
          );
          const cfText = `${document.title || ''} ${body}`.toLowerCase();
          const cfMarkerText = /just a moment|checking your browser|verify you are human|needs to review the security|enable javascript and cookies|确认您是真人|验证您是否是真人|正在检查|请稍候|稍等/.test(cfText);
          const authFormVisible = hasOtp || hasPassword || profileFormVisible
            || hasVisible("input[type='email'],input[name='email'],input[name='username']");
          const hasCloudflare = cfFrameVisible || cfWidgetVisible || (cfMarkerText && !authFormVisible);
          const errorNodes = [...document.querySelectorAll(
            "[role='alert'],[role='status'],[aria-live='assertive'],[aria-live='polite'],"
            + "[data-testid*='error' i],[data-testid*='alert' i],[data-testid*='message' i],"
            + "[class*='error' i],[class*='alert' i]"
          )].filter(visible).map(el => cleanText(el.innerText || el.textContent));
          const errorSignal = /failed to create account|failed to sign up|unable to create account|something went wrong|too many attempts|try again|invalid email|email (?:is )?(?:invalid|already|not supported)|创建账户失败|注册失败|请稍后再试|邮箱无效|邮箱已存在/i;
          const bodyErrors = bodyText.split(/\n+/)
            .map(cleanText)
            .filter(line => line && errorSignal.test(line));
          const errors = [...new Set(
            [...errorNodes, ...bodyErrors].filter(Boolean)
          )].slice(0, 12);
          return {
            url,
            path,
            hasOtp,
            hasPassword,
            profileFormVisible,
            hasCloudflare,
            profileFields: {hasNameField, hasBirthField},
            bodyPreview: body.slice(0, 200),
            errors
          };
        }""",
    )
    if not isinstance(result, dict):
        return {"state": "other", "url": _page_url(page)}
    result["state"] = _classify_page_state(result)
    return result


def _find_email_input(page: Any, timeout_ms: Optional[int] = None) -> Any:
    deadline = time.time() + (timeout_ms or _action_timeout_ms()) / 1000.0
    cloudflare_deadline = None
    last_cloudflare_attempt = 0.0
    strict = [
        "input[type='email']",
        "input[name='email']",
        "input[name='username']",
        "input#email-input",
        "input[autocomplete='email']",
        "input[autocomplete*='email' i]",
        "input[autocomplete='username']",
        "input[id*='email' i]",
        "input[id*='username' i]",
        "input[aria-label*='email' i]",
        "input[placeholder*='email' i]",
    ]
    clicked_email_option = False
    while time.time() < deadline:
        _check_stop()
        locator = _visible_locator(page, strict, timeout_ms=400)
        if locator is not None:
            return locator
        info = _page_state(page)
        if info.get("hasCloudflare"):
            if cloudflare_deadline is None:
                cloudflare_deadline = time.time() + _cloudflare_timeout_seconds()
                _log("检测到 Cloudflare/Turnstile 验证，正在自动处理")
            deadline = max(deadline, cloudflare_deadline)
            if time.time() - last_cloudflare_attempt >= 2.0:
                last_cloudflare_attempt = time.time()
                _try_cloudflare_challenge(page)
        elif cloudflare_deadline is not None:
            _log("Cloudflare/Turnstile 验证已通过")
            cloudflare_deadline = None
            deadline = max(deadline, time.time() + max(6.0, (timeout_ms or _action_timeout_ms()) / 1000.0))
        if not clicked_email_option:
            clicked_email_option = _click_email_option(page)
            if clicked_email_option:
                _human_pause(0.5, 1.0)
                continue
        time.sleep(0.35)
    raise RuntimeError(f"找不到邮箱输入框，当前页面: {_safe_url_for_log(_page_url(page)) or '-'}")


def _click_email_option(page: Any) -> bool:
    selectors = [
        "button[data-testid*='email' i]",
        "button[data-provider='email']",
        "button[data-auth-provider='email']",
        "button[data-testid*='mail' i]",
        "button:has-text('Continue with email')",
        "button:has-text('Sign up with email')",
        "button:has-text('Log in with email')",
        "button:has-text('使用邮箱')",
        "button:has-text('邮箱')",
    ]
    locator = _visible_locator(page, selectors, timeout_ms=500)
    if locator is not None:
        return _human_click(locator, timeout=3000)
    marker = _new_action_marker("chatgpt-email-option")
    result = _safe_evaluate(
        page,
        r"""marker => {
          const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
            && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none'
            && !el.disabled && el.getAttribute('aria-disabled') !== 'true';
          const attrText = el => {
            const own = [
              el.id, el.getAttribute('name'), el.getAttribute('type'), el.getAttribute('autocomplete'),
              el.getAttribute('data-testid'), el.getAttribute('data-test-id'), el.getAttribute('data-provider'),
              el.getAttribute('data-auth-provider'), el.getAttribute('data-idp'), el.getAttribute('href'),
              el.getAttribute('formaction'), el.getAttribute('value'), el.getAttribute('aria-label'), el.className
            ].filter(Boolean).join(' ');
            const desc = [...el.querySelectorAll('img,svg,use,[aria-label],[data-provider],[data-testid]')]
              .map(node => [
                node.getAttribute('alt'), node.getAttribute('src'), node.getAttribute('href'),
                node.getAttribute('aria-label'), node.getAttribute('data-provider'),
                node.getAttribute('data-testid'), node.className
              ].filter(Boolean).join(' ')).join(' ');
            return (own + ' ' + desc).toLowerCase();
          };
          const bad = /google|apple|microsoft|github|facebook|saml|sso|oauth|social|oidc|idp|authorize|consent/;
          const good = /(^|[^a-z])(email|mail|username|passwordless|otp|magic)([^a-z]|$)/;
          const candidates = [...document.querySelectorAll('button,a,[role="button"],input[type="submit"]')]
            .map((el, idx) => ({el, idx, attrs: attrText(el), logo: !!el.querySelector('img,svg,use')}))
            .filter(item => visible(item.el) && good.test(item.attrs) && !bad.test(item.attrs) && !item.logo);
          if (candidates.length !== 1) return {ok:false, count:candidates.length};
          candidates[0].el.setAttribute('data-cloak-action', marker);
          return {ok:true, attrs:candidates[0].attrs.slice(0, 180)};
        }""",
        marker,
    )
    return (
        isinstance(result, dict)
        and bool(result.get("ok"))
        and _human_click_marked(page, marker, timeout=3000)
    )


def _submit_email(page: Any, email: str) -> None:
    marker = _new_action_marker("chatgpt-email-submit")
    result = _safe_evaluate(
        page,
        r"""({email, marker}) => {
          email = String(email || '').trim().toLowerCase();
          const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
            && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none'
            && !el.disabled && !el.readOnly;
          const inputs = [...document.querySelectorAll(
            'input[type="email"],input[name="email"],input[name="username"],input[autocomplete*="email"],input[autocomplete="username"],input#email-input'
          )].filter(visible);
          const input = inputs.find(el => String(el.value || '').trim().toLowerCase() === email)
            || inputs.find(el => String(el.value || '').includes('@')) || inputs[0];
          if (!input) return {ok:false, reason:'missing_email_input'};
          const form = input.closest('form');
          if (!form) return {ok:false, reason:'missing_form'};
          const bad = /google|apple|microsoft|github|facebook|saml|sso|oauth|social|oidc|idp|authorize|consent/;
          const attrText = el => [
            el.id, el.name, el.type, el.getAttribute('data-testid'), el.getAttribute('data-provider'),
            el.getAttribute('aria-label'), el.getAttribute('formaction'), el.value, el.className
          ].filter(Boolean).join(' ').toLowerCase();
          const inputRect = input.getBoundingClientRect();
          const buttons = [...form.querySelectorAll('button,input[type="submit"]')]
            .map((el, idx) => {
              const rect = el.getBoundingClientRect();
              const attrs = attrText(el);
              const distance = Math.max(0, rect.top - inputRect.bottom)
                + Math.abs((rect.left + rect.right - inputRect.left - inputRect.right) / 2) / 10;
              const type = String(el.getAttribute('type') || '').toLowerCase();
              const score = (type === 'submit' ? 100 : 0) - distance;
              return {el, idx, attrs, distance, score, type};
            })
            .filter(item => visible(item.el) && !bad.test(item.attrs)
              && !item.el.querySelector('img,svg,use') && item.el.getBoundingClientRect().top >= inputRect.bottom - 12)
            .sort((a, b) => b.score - a.score || a.distance - b.distance);
          const submit = buttons[0] && buttons[0].el;
          if (submit) {
            submit.setAttribute('data-cloak-action', marker);
            return {ok:true, method:'button'};
          }
          return {ok:false, reason:'missing_submit'};
        }""",
        {"email": email, "marker": marker},
    )
    if isinstance(result, dict) and result.get("ok"):
        if _human_click_marked(page, marker, timeout=4000):
            _human_pause(0.35, 0.9)
            return
    try:
        _human_press(page, "Enter")
        _human_pause(0.35, 0.9)
    except Exception as exc:
        raise RuntimeError(f"邮箱提交失败: {exc}") from exc


def _wait_transition(page: Any, timeout: int = 18) -> str:
    deadline = time.time() + timeout
    last_state = "other"
    last_info: dict = {}
    cloudflare_deadline = None
    last_cloudflare_attempt = 0.0
    while time.time() < deadline:
        _check_stop()
        info = _page_state(page)
        last_info = info
        last_state = str(info.get("state") or "other")
        if info.get("hasCloudflare"):
            if cloudflare_deadline is None:
                cloudflare_deadline = time.time() + _cloudflare_timeout_seconds()
                _log("检测到 Cloudflare/Turnstile 验证，正在自动处理")
            deadline = max(deadline, cloudflare_deadline)
            if time.time() - last_cloudflare_attempt >= 2.0:
                last_cloudflare_attempt = time.time()
                _try_cloudflare_challenge(page)
        elif cloudflare_deadline is not None:
            _log("Cloudflare/Turnstile 验证已通过")
            cloudflare_deadline = None
            deadline = max(deadline, time.time() + max(6, timeout))
        if last_state in {"login_password", "password", "email_verification", "profile", "chatgpt"}:
            return last_state
        if _page_error_summary(info):
            return "email_error"
        time.sleep(0.35)
    url = _page_url(page).lower()
    if _page_error_summary(last_info):
        return "email_error"
    if "chatgpt.com/auth/login" in url:
        return "email_page"
    return last_state


def _click_use_password(page: Any) -> bool:
    selectors = [
        "a[href*='/create-account/password']",
        "[role='link'][href*='/create-account/password']",
        "button:has-text('Continue with password')",
        "button:has-text('Use password')",
        "a:has-text('Continue with password')",
    ]
    locator = _visible_locator(page, selectors, timeout_ms=700)
    if locator is not None:
        return _human_click(locator, timeout=3000)
    marker = _new_action_marker("chatgpt-use-password")
    result = _safe_evaluate(
        page,
        r"""marker => {
          const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
            && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none'
            && !el.disabled;
          const control = [...document.querySelectorAll('a,button,[role="link"],[role="button"]')]
            .filter(visible)
            .find(el => String(el.getAttribute('href') || '').includes('/create-account/password'));
          if (!control) return false;
          control.setAttribute('data-cloak-action', marker);
          return true;
        }""",
        marker,
    )
    return bool(result) and _human_click_marked(page, marker, timeout=3000)


def _switch_to_password_registration(page: Any, timeout: int = 12) -> str:
    """Wait for the delayed "use password" control and follow it to signup."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        _check_stop()
        state = str(_page_state(page).get("state") or "other")
        if state != "email_verification":
            return state
        if _click_use_password(page):
            state = _wait_transition(page, timeout=10)
            if state != "email_verification":
                return state
        time.sleep(0.5)
    return str(_page_state(page).get("state") or "other")


def _password_page_diagnostics(page: Any) -> dict:
    result = _safe_evaluate(
        page,
        r"""() => {
          const host = String(location.hostname || '').toLowerCase();
          const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
            && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none';
          const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
          const errors = [...document.querySelectorAll(
            "[role='alert'],[aria-live],[data-testid*='error' i],[class*='error' i]"
          )]
            .filter(visible)
            .map(el => clean(el.innerText || el.textContent))
            .filter(Boolean);
          const authErrors = host === 'auth.openai.com'
            ? [...document.querySelectorAll(
              "[role='alert'],[aria-live],[data-testid*='error' i],[class*='error' i]"
            )]
              .filter(visible)
              .map(el => clean(el.innerText || el.textContent))
              .filter(Boolean)
            : [];
          const inputs = [...document.querySelectorAll(
            "input[type='password'],input[name*='password' i],input[autocomplete='new-password']"
          )]
            .filter(visible)
            .map(el => ({
              name: el.name || '',
              type: el.type || '',
              autocomplete: el.autocomplete || '',
              valueLength: String(el.value || '').length,
              invalid: el.getAttribute('aria-invalid') === 'true'
                || !!(el.validity && el.validity.valid === false)
            }));
          const buttons = [...document.querySelectorAll('button,input[type="submit"],[role="button"]')]
            .filter(visible)
            .slice(0, 12)
            .map(el => ({
              text: clean(el.innerText || el.textContent || el.value).slice(0, 80),
              type: el.getAttribute('type') || '',
              disabled: !!el.disabled || el.getAttribute('aria-disabled') === 'true'
            }));
          return {
            url: location.href,
            title: String(document.title || '').slice(0, 120),
            inputs,
            errors: [...new Set([...errors, ...authErrors])]
              .slice(0, 6)
              .map(item => item.slice(0, 300)),
            buttons
          };
        }""",
    )
    return result if isinstance(result, dict) else {}


def _submit_password_form(page: Any) -> str:
    selectors = [
        "form:has(input[type='password']) button[type='submit']",
        "form:has(input[autocomplete='new-password']) button[type='submit']",
        "form:has(input[type='password']) button:has-text('Continue')",
        "form:has(input[type='password']) button:has-text('Next')",
        "form:has(input[type='password']) button:has-text('Create account')",
        "button[type='submit']:has-text('Continue')",
        "button[type='submit']:has-text('Next')",
        "button[type='submit']:has-text('Create account')",
    ]
    locator = _visible_locator(page, selectors, timeout_ms=700)
    if locator is not None and _human_click(locator, timeout=4000):
        return "button"
    marker = _new_action_marker("chatgpt-password-submit")
    result = _safe_evaluate(
        page,
        r"""marker => {
          const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
            && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none'
            && !el.disabled && String(el.getAttribute('aria-disabled') || '').toLowerCase() !== 'true';
          const input = [...document.querySelectorAll(
            "input[type='password'],input[name*='password' i],input[autocomplete='new-password']"
          )].find(visible);
          const form = input && input.closest('form');
          if (!form) return {ok:false, reason:'missing_form'};
          const candidates = [...document.querySelectorAll('button,input[type="submit"],[role="button"]')]
            .filter(visible)
            .map((el, idx) => {
              const text = [el.innerText, el.textContent, el.value, el.getAttribute('aria-label'), el.type]
                .join(' ').toLowerCase();
              if (/show|hide|reveal|visibility|password-eye|显示密码|隐藏密码/.test(text)) return null;
              let score = 0;
              if (String(el.getAttribute('type') || '').toLowerCase() === 'submit') score += 100;
              if (/continue|next|create|submit|sign up|继续|下一步|创建/.test(text)) score += 70;
              if (el.form === form || form.contains(el)) score += 20;
              return {el, idx, score};
            })
            .filter(item => item && item.score > 0)
            .sort((a, b) => b.score - a.score || a.idx - b.idx);
          if (candidates.length) {
            candidates[0].el.setAttribute('data-cloak-action', marker);
            return {ok:true, method:'scored_button'};
          }
          return {ok:false, reason:'missing_submit'};
        }""",
        marker,
    )
    if isinstance(result, dict) and result.get("ok"):
        if _human_click_marked(page, marker, timeout=4000):
            return str(result.get("method") or "marked_button")
    raise RuntimeError(f"密码页找不到提交按钮: {result}")


def _wait_for_password_transition(page: Any, timeout: float = 18.0) -> tuple:
    deadline = time.time() + timeout
    cloudflare_deadline = None
    last_cloudflare_attempt = 0.0
    while time.time() < deadline:
        _check_stop()
        info = _page_state(page)
        state = str(info.get("state") or "other")
        if info.get("hasCloudflare"):
            if cloudflare_deadline is None:
                cloudflare_deadline = time.time() + _cloudflare_timeout_seconds()
                _log("密码提交后检测到 Cloudflare/Turnstile 验证")
            deadline = max(deadline, cloudflare_deadline)
            if time.time() - last_cloudflare_attempt >= 2.0:
                last_cloudflare_attempt = time.time()
                _try_cloudflare_challenge(page)
        elif cloudflare_deadline is not None:
            _log("Cloudflare/Turnstile 验证已通过")
            cloudflare_deadline = None
            deadline = max(deadline, time.time() + max(6.0, timeout))
        if _is_terminal_account_create_error(info):
            return False, info
        if state != "password":
            return True, info
        time.sleep(0.4)
    return False, _page_state(page)


def _fill_password(page: Any, password: str) -> bool:
    if _page_state(page).get("state") != "password":
        return False
    inputs = page.locator(
        "input[type='password'],input[name*='password' i],input[autocomplete='new-password']"
    )
    filled = 0
    last_filled = None
    for index in range(min(inputs.count(), 4)):
        locator = inputs.nth(index)
        try:
            if not locator.is_visible(timeout=500):
                continue
            _human_fill(page, locator, password)
            filled += 1
            last_filled = locator
        except Exception:
            continue
    if filled <= 0:
        raise RuntimeError(f"密码页找不到可填写密码框: {_password_page_diagnostics(page)}")

    _submit_password_form(page)
    _log("密码已提交")
    wait_seconds = max(18.0, _action_timeout_ms() / 1000.0)
    transitioned, transition_info = _wait_for_password_transition(
        page,
        timeout=wait_seconds,
    )
    if transitioned:
        return True

    diagnostics = _password_page_diagnostics(page)
    if _is_terminal_account_create_error(transition_info):
        raise AccountEmailBlocked(
            f"{_account_create_error_detail(transition_info, page)}; "
            f"页面信息={json.dumps(diagnostics, ensure_ascii=False)[:1200]}"
        )
    _log(f"密码页提交未跳转，准备重试: {json.dumps(diagnostics, ensure_ascii=False)[:800]}")
    try:
        last_filled.focus(timeout=1500)
        _human_press(page, "Enter")
    except Exception:
        pass
    transitioned, transition_info = _wait_for_password_transition(
        page,
        timeout=12.0,
    )
    if transitioned:
        return True

    diagnostics = _password_page_diagnostics(page)
    error_text = _page_error_summary(transition_info)
    if error_text:
        if _is_terminal_account_create_error(transition_info):
            raise AccountEmailBlocked(
                f"{_account_create_error_detail(transition_info, page)}; "
                f"页面信息={json.dumps(diagnostics, ensure_ascii=False)[:1200]}"
            )
        raise RuntimeError(
            f"密码提交后页面返回错误: {error_text}; "
            f"当前页面: {_safe_url_for_log(_page_url(page))}；"
            f"页面信息={json.dumps(diagnostics, ensure_ascii=False)[:1200]}"
        )
    raise RuntimeError(
        f"密码设置后仍未离开密码页: {_safe_url_for_log(_page_url(page))}，页面信息="
        f"{json.dumps(diagnostics, ensure_ascii=False)[:1200]}"
    )


def _wait_for_otp_page(page: Any, timeout: int = 30) -> bool:
    deadline = time.time() + timeout
    cloudflare_deadline = None
    last_cloudflare_attempt = 0.0
    while time.time() < deadline:
        _check_stop()
        info = _page_state(page)
        state = str(info.get("state") or "other")
        if info.get("hasCloudflare"):
            if cloudflare_deadline is None:
                cloudflare_deadline = time.time() + _cloudflare_timeout_seconds()
                _log("等待邮箱验证码时检测到 Cloudflare/Turnstile 验证")
            deadline = max(deadline, cloudflare_deadline)
            if time.time() - last_cloudflare_attempt >= 2.0:
                last_cloudflare_attempt = time.time()
                _try_cloudflare_challenge(page)
        elif cloudflare_deadline is not None:
            _log("Cloudflare/Turnstile 验证已通过")
            cloudflare_deadline = None
            deadline = max(deadline, time.time() + max(6, timeout))
        if state == "email_verification":
            return True
        if state in {"login_password", "password", "profile", "chatgpt"}:
            return False
        time.sleep(0.35)
    return False


def _clear_otp(page: Any) -> None:
    _safe_evaluate(
        page,
        r"""() => {
          for (const el of document.querySelectorAll('input')) {
            const type = String(el.type || '').toLowerCase();
            const name = String(el.name || '').toLowerCase();
            const auto = String(el.autocomplete || '').toLowerCase();
            if (type === 'tel' || type === 'number' || type === 'text'
              || name.includes('code') || name.includes('otp') || auto.includes('one-time')) {
              const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
              if (setter) setter.call(el, ''); else el.value = '';
              el.dispatchEvent(new Event('input', {bubbles:true}));
              el.dispatchEvent(new Event('change', {bubbles:true}));
            }
          }
        }""",
    )


def _fill_otp(page: Any, code: str) -> None:
    _clear_otp(page)
    single_selectors = [
        "input[name='code']",
        "input[name='otp']",
        "input[autocomplete='one-time-code']",
        "input[inputmode='numeric']",
        "input[aria-label*='code' i]",
        "input[placeholder*='code' i]",
    ]
    locator = _visible_locator(page, single_selectors, timeout_ms=700)
    if locator is not None:
        _human_fill(page, locator, code)
        return
    boxes = page.locator("input[maxlength='1'], input[data-index], input[aria-label*='digit' i]")
    try:
        count = boxes.count()
    except Exception:
        count = 0
    if count >= len(code):
        for index, char in enumerate(code):
            box = boxes.nth(index)
            _human_click(box, timeout=1500)
            _human_type(page, char, delay_ms=random.randint(40, 100))
            _human_pause(0.04, 0.12)
        return
    raise RuntimeError(f"找不到 OTP 输入框: {_safe_url_for_log(_page_url(page))}")


def _click_continue(page: Any) -> None:
    selectors = [
        "button[type='submit']",
        "button[data-dd-action-name='Continue']",
        "button[data-login-web-auth-control='true'][type='submit']",
        "button:has-text('Continue')",
        "button:has-text('Verify')",
        "button:has-text('继续')",
        "button:has-text('验证')",
    ]
    locator = _visible_locator(page, selectors, timeout_ms=1200)
    if locator is not None and _human_click(locator, timeout=4000):
        return
    try:
        _human_press(page, "Enter")
    except Exception:
        pass


def _resend_otp(page: Any) -> bool:
    selectors = [
        "button[name='intent'][value='resend']",
        "button:has-text('Resend')",
        "button:has-text('Send again')",
        "button:has-text('重新发送')",
        "button:has-text('再发送')",
        "a:has-text('Resend')",
        "a:has-text('重新发送')",
    ]
    locator = _visible_locator(page, selectors, timeout_ms=900)
    if locator is not None and _human_click(locator, timeout=3500):
        return True
    marker = _new_action_marker("chatgpt-resend-otp")
    result = _safe_evaluate(
        page,
        r"""marker => {
          const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
            && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none'
            && !el.disabled && String(el.getAttribute('aria-disabled') || '').toLowerCase() !== 'true';
          const candidates = [...document.querySelectorAll('button,a,[role="button"]')]
            .filter(visible)
            .map((el, idx) => {
              const blob = [
                el.innerText, el.textContent, el.value, el.id, el.name,
                el.getAttribute('aria-label'), el.getAttribute('data-testid'),
                el.getAttribute('href'), el.className
              ].filter(Boolean).join(' ').toLowerCase();
              let score = 0;
              if (/resend|send.?again|重新发送|再发送/.test(blob)) score += 100;
              if (/continue|submit|verify/.test(blob)) score -= 30;
              return {el, idx, score};
            })
            .filter(item => item.score > 0)
            .sort((a, b) => b.score - a.score || a.idx - b.idx);
          if (!candidates.length) return false;
          candidates[0].el.setAttribute('data-cloak-action', marker);
          return true;
        }""",
        marker,
    )
    return bool(result) and _human_click_marked(page, marker, timeout=3500)


def _wait_after_otp(page: Any, timeout: int = 18) -> str:
    deadline = time.time() + timeout
    cloudflare_deadline = None
    last_cloudflare_attempt = 0.0
    while time.time() < deadline:
        info = _page_state(page)
        state = str(info.get("state") or "other")
        if info.get("hasCloudflare"):
            if cloudflare_deadline is None:
                cloudflare_deadline = time.time() + _cloudflare_timeout_seconds()
                _log("验证码提交后检测到 Cloudflare/Turnstile 验证")
            deadline = max(deadline, cloudflare_deadline)
            if time.time() - last_cloudflare_attempt >= 2.0:
                last_cloudflare_attempt = time.time()
                _try_cloudflare_challenge(page)
        elif cloudflare_deadline is not None:
            _log("Cloudflare/Turnstile 验证已通过")
            cloudflare_deadline = None
            deadline = max(deadline, time.time() + max(6, timeout))
        if state in {"profile", "chatgpt"}:
            return "accepted"
        if state == "email_verification":
            body = str(info.get("bodyPreview") or "").lower()
            if any(word in body for word in ("incorrect", "invalid", "expired", "错误", "过期", "无效")):
                return "invalid"
        time.sleep(0.35)
    return "unknown"


def _fill_profile_fields(page: Any, name: str, birthday: str) -> dict:
    # Retained for compatibility with older callers. Interactive registration
    # uses _fill_profile_fields_humanized so browser actions follow humanize.
    year, month, day = [int(part) for part in birthday.split("-")]
    today = date.today()
    age = max(18, min(60, today.year - year - ((today.month, today.day) < (month, day))))
    result = _safe_evaluate(
        page,
        r"""({name, birthday, year, month, day, age}) => {
          const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
            && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none'
            && !el.disabled && !el.readOnly;
          const hay = el => [el.name, el.id, el.placeholder, el.getAttribute('aria-label'), el.autocomplete, el.getAttribute('data-testid')]
            .join(' ').toLowerCase();
          const setValue = (el, value) => {
            if (!el) return false;
            try { el.scrollIntoView({block:'center'}); el.focus(); } catch (_) {}
            const tag = String(el.tagName || '').toLowerCase();
            const proto = tag === 'textarea' ? HTMLTextAreaElement.prototype
              : tag === 'select' ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
            const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
            if (setter) setter.call(el, String(value)); else el.value = String(value);
            if (tag === 'select') {
              [...el.options].forEach(opt => {
                opt.selected = String(opt.value) === String(value) || String(opt.textContent || '').trim() === String(value);
              });
            }
            el.dispatchEvent(new Event('input', {bubbles:true}));
            el.dispatchEvent(new Event('change', {bubbles:true}));
            el.blur?.();
            return true;
          };
          const inputs = [...document.querySelectorAll('input,textarea')].filter(visible);
          const filled = {};
          const nameInput = inputs.find(el => /(name|full.?name|user.?name|姓名|名字)/i.test(hay(el))
              && !/(month|day|year|age|birth|code|email|phone|password)/i.test(hay(el)))
            || inputs.find(el => ['text', ''].includes(String(el.type || '').toLowerCase())
              && !/(code|email|phone|tel|number|password|month|day|year|age|birth)/i.test(hay(el)));
          if (nameInput) filled.name = setValue(nameInput, name);

          const names = name.split(/\s+/, 2);
          const firstInput = inputs.find(el => /(first.?name|given)/i.test(hay(el)));
          const lastInput = inputs.find(el => /(last.?name|family|surname)/i.test(hay(el)));
          if (!filled.name && firstInput) filled.firstName = setValue(firstInput, names[0] || name);
          if (lastInput) filled.lastName = setValue(lastInput, names[1] || 'User');

          const ageInput = inputs.find(el => /(age|年龄)/i.test(hay(el))
            || (String(el.type || '').toLowerCase() === 'number' && !/(day|month|year)/i.test(hay(el))));
          if (ageInput) filled.age = setValue(ageInput, age);

          const dateInput = [...document.querySelectorAll('input[name="birthdate"],input[name="birthday"],input[type="date"]')]
            .find(el => visible(el) || String(el.getAttribute('type') || '').toLowerCase() === 'date');
          if (dateInput) filled.birthdate = setValue(dateInput, birthday);

          const setFirst = (selectors, values, key) => {
            for (const selector of selectors) {
              for (const el of [...document.querySelectorAll(selector)]) {
                if (!visible(el)) continue;
                for (const value of values) {
                  if (String(el.tagName || '').toLowerCase() === 'select') {
                    const has = [...el.options].some(opt => String(opt.value) === String(value)
                      || String(opt.textContent || '').trim() === String(value));
                    if (!has) continue;
                  }
                  if (setValue(el, value)) {
                    filled[key] = value;
                    return true;
                  }
                }
              }
            }
            return false;
          };
          const yearOk = setFirst(['select[name="year"]','input[name="year"]','input[id*="year"]','input[aria-label*="year" i]'], [year], 'year');
          const monthOk = setFirst(['select[name="month"]','input[name="month"]','input[id*="month"]','input[aria-label*="month" i]'], [month, String(month).padStart(2, '0')], 'month');
          const dayOk = setFirst(['select[name="day"]','input[name="day"]','input[id*="day"]','input[aria-label*="day" i]'], [day, String(day).padStart(2, '0')], 'day');
          if (yearOk && monthOk && dayOk) filled.ymd = true;

          const selects = [...document.querySelectorAll('[data-testid="hidden-select-container"] select,.react-aria-Select select,select')]
            .filter(el => !el.disabled);
          const numbers = sel => [...sel.options].map(opt => Number(opt.value)).filter(Number.isFinite);
          const hasOption = (sel, value) => [...sel.options].some(opt => String(opt.value) === String(value));
          const yearSelects = selects.filter(sel => hasOption(sel, year) && Math.max(...numbers(sel), -Infinity) > 1900);
          const smallSelects = selects.filter(sel => !yearSelects.includes(sel));
          const monthSelects = smallSelects.filter(sel => (hasOption(sel, month) || hasOption(sel, String(month).padStart(2, '0')))
            && Math.min(...numbers(sel), Infinity) <= 1 && Math.max(...numbers(sel), -Infinity) <= 12);
          const daySelects = smallSelects.filter(sel => (hasOption(sel, day) || hasOption(sel, String(day).padStart(2, '0')))
            && Math.max(...numbers(sel), -Infinity) >= 28);
          let birthMode = filled.age ? 'age' : (filled.birthdate ? 'birthdate' : (filled.ymd ? 'ymd' : null));
          if (!birthMode && yearSelects.length && monthSelects.length && daySelects.length) {
            const yearSelect = yearSelects[0];
            const monthSelect = monthSelects[0];
            const daySelect = daySelects.find(sel => sel !== monthSelect) || daySelects[0];
            setValue(yearSelect, year);
            setValue(monthSelect, hasOption(monthSelect, month) ? month : String(month).padStart(2, '0'));
            setValue(daySelect, hasOption(daySelect, day) ? day : String(day).padStart(2, '0'));
            filled.reactSelect = true;
            birthMode = 'react_select';
          }
          if (!birthMode && document.querySelector('[role="spinbutton"][data-type="year"]')) {
            birthMode = 'spinbutton_needed';
          }

          let checkboxCount = 0;
          for (const box of [...document.querySelectorAll('input[type="checkbox"],[role="checkbox"]')].filter(visible)) {
            const checked = box.checked === true
              || String(box.getAttribute('aria-checked') || '').toLowerCase() === 'true';
            if (!checked) {
              const label = box.closest('label');
              try { (label || box).click(); } catch (_) {}
              if (box.checked !== true) {
                const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'checked')?.set;
                if (setter) setter.call(box, true); else box.checked = true;
                box.dispatchEvent(new Event('input', {bubbles:true}));
                box.dispatchEvent(new Event('change', {bubbles:true}));
              }
              checkboxCount += 1;
            }
          }
          return {
            ok: Boolean((filled.name || filled.firstName) && birthMode),
            birthMode,
            checkboxCount,
            filled,
            url: location.href
          };
        }""",
        {
            "name": name,
            "birthday": birthday,
            "year": year,
            "month": month,
            "day": day,
            "age": age,
        },
    )
    return result if isinstance(result, dict) else {"ok": False}


def _profile_controls(page: Any) -> dict:
    result = _safe_evaluate(
        page,
        r"""() => {
          const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
            && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none'
            && !el.disabled && !el.readOnly;
          const hay = el => [
            el.name, el.id, el.placeholder, el.getAttribute('aria-label'),
            el.autocomplete, el.getAttribute('data-testid'), el.getAttribute('type')
          ].filter(Boolean).join(' ').toLowerCase();
          const controls = [...document.querySelectorAll('input,textarea,select,[role="spinbutton"]')]
            .filter(visible)
            .map((el, index) => {
              const labels = [...(el.labels || [])].map(node => node.innerText || node.textContent || '').join(' ');
              const labelled = String(el.getAttribute('aria-labelledby') || '').split(/\s+/)
                .map(id => document.getElementById(id))
                .filter(Boolean)
                .map(node => node.innerText || node.textContent || '')
                .join(' ');
              return {
                index,
                tag: String(el.tagName || '').toLowerCase(),
                type: String(el.getAttribute('type') || '').toLowerCase(),
                role: String(el.getAttribute('role') || '').toLowerCase(),
                hay: `${hay(el)} ${labels} ${labelled}`.toLowerCase(),
                options: [...(el.options || [])].map(opt => ({
                  value: String(opt.value || ''),
                  text: String(opt.textContent || '').trim()
                })).filter(opt => opt.value !== '').slice(0, 200)
              };
            });
          return {controls};
        }""",
    )
    return result if isinstance(result, dict) else {"controls": []}


def _fill_profile_fields_humanized(page: Any, name: str, birthday: str) -> dict:
    year, month, day = [int(part) for part in birthday.split("-")]
    controls = list(_profile_controls(page).get("controls") or [])
    if not controls:
        return {"ok": False}
    locators = page.locator("input,textarea,select,[role='spinbutton']")

    def locator_for(control: dict) -> Any:
        return locators.nth(int(control.get("index", 0)))

    def choose_value(control: dict, values: list[str]) -> Optional[str]:
        options = list(control.get("options") or [])
        for value in values:
            for option in options:
                if value in {str(option.get("value") or ""), str(option.get("text") or "")}:
                    return str(option.get("value") or "")
        return None

    def fill_control(control: dict, value: str) -> bool:
        locator = locator_for(control)
        if str(control.get("tag") or "").lower() == "select":
            selected = choose_value(control, [value, str(value).zfill(2)])
            if selected is None:
                return False
            _human_select(locator, selected)
            return True
        _human_fill(page, locator, value)
        return True

    filled: dict = {}
    name_controls = [
        item for item in controls
        if re.search(r"(name|full.?name|user.?name|姓名|名字)", str(item.get("hay") or ""))
        and not re.search(r"(month|day|year|age|birth|code|email|phone|password)", str(item.get("hay") or ""))
    ]
    if name_controls:
        fill_control(name_controls[0], name)
        filled["name"] = True
    else:
        first = next(
            (item for item in controls if re.search(r"(first.?name|given)", str(item.get("hay") or ""))),
            None,
        )
        last = next(
            (item for item in controls if re.search(r"(last.?name|family|surname)", str(item.get("hay") or ""))),
            None,
        )
        names = name.split(None, 1)
        if first is not None:
            fill_control(first, names[0])
            filled["firstName"] = True
        if last is not None:
            fill_control(last, names[1] if len(names) > 1 else "User")
            filled["lastName"] = True

    birth_mode = None
    age_control = next(
        (
            item for item in controls
            if re.search(r"(age|年龄)", str(item.get("hay") or ""))
            or (
                str(item.get("type") or "") == "number"
                and not re.search(r"(day|month|year)", str(item.get("hay") or ""))
            )
        ),
        None,
    )
    if age_control is not None:
        today = date.today()
        age = max(18, min(60, today.year - year - ((today.month, today.day) < (month, day))))
        if fill_control(age_control, str(age)):
            filled["age"] = True
            birth_mode = "age"

    date_control = next(
        (
            item for item in controls
            if str(item.get("type") or "") == "date"
            or re.search(r"(birthdate|birthday)", str(item.get("hay") or ""))
        ),
        None,
    )
    if date_control is not None:
        if fill_control(date_control, birthday):
            filled["birthdate"] = True
            birth_mode = "birthdate"

    if birth_mode is None:
        ymd: dict[str, dict] = {}
        for key, pattern in (
            ("year", r"year|年"),
            ("month", r"month|月"),
            ("day", r"(^|[\s_-])day([\s_-]|$)|日"),
        ):
            match = next(
                (
                    item for item in controls
                    if re.search(pattern, str(item.get("hay") or ""), re.IGNORECASE)
                    and not re.search(r"(birthdate|birthday)", str(item.get("hay") or ""))
                ),
                None,
            )
            if match is not None:
                ymd[key] = match
        if all(key in ymd for key in ("year", "month", "day")):
            values = {"year": str(year), "month": str(month), "day": str(day)}
            if all(fill_control(ymd[key], values[key]) for key in ("year", "month", "day")):
                filled["ymd"] = True
                birth_mode = "ymd"

    if birth_mode is None:
        spinbuttons = [
            item for item in controls if str(item.get("role") or "") == "spinbutton"
        ]
        if spinbuttons:
            birth_mode = "spinbutton_needed"

    filled["birthMode"] = birth_mode
    filled["ok"] = bool(filled.get("name") or filled.get("firstName")) and bool(birth_mode)
    return filled


def _fill_birthday_spinbuttons(page: Any, birthday: str) -> bool:
    try:
        year, month, day = birthday.split("-")
    except Exception:
        return False
    filled = False
    for selector, value in (
        ("[role='spinbutton'][data-type='year']", year),
        ("[role='spinbutton'][data-type='month']", str(int(month)).zfill(2)),
        ("[role='spinbutton'][data-type='day']", str(int(day)).zfill(2)),
    ):
        try:
            locator = page.locator(selector).first
            if locator.count() <= 0 or not locator.is_visible(timeout=500):
                continue
            _human_click(locator, timeout=2000)
            _human_press(page, "ControlOrMeta+A")
            _human_type(page, str(value), delay_ms=random.randint(90, 180))
            _human_press(page, "Tab")
            filled = True
        except Exception:
            try:
                _human_press(page, "ControlOrMeta+A")
                _human_type(page, str(value), delay_ms=random.randint(90, 180))
                filled = True
            except Exception:
                pass
    return filled


def _submit_profile(page: Any) -> bool:
    marker = _new_action_marker("chatgpt-profile-submit")
    result = _safe_evaluate(
        page,
        r"""marker => {
          const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
            && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none'
            && !el.disabled && String(el.getAttribute('aria-disabled') || '').toLowerCase() !== 'true';
          const forms = [...document.querySelectorAll('form')].filter(visible);
          for (const form of forms) {
            const submit = form.querySelector('button[type="submit"],input[type="submit"]');
            if (submit && visible(submit)) {
              submit.setAttribute('data-cloak-action', marker);
              return true;
            }
          }
          const buttons = [...document.querySelectorAll('button,input[type="submit"],[role="button"]')]
            .filter(visible)
            .map((el, idx) => {
              const text = [el.innerText, el.textContent, el.value, el.getAttribute('aria-label'), el.type]
                .join(' ').toLowerCase();
              let score = 0;
              if (String(el.getAttribute('type') || '').toLowerCase() === 'submit') score += 100;
              if (/continue|next|done|submit|create|start|继续|下一步|完成|提交/.test(text)) score += 80;
              return {el, idx, score};
            })
            .filter(item => item.score > 0)
            .sort((a, b) => b.score - a.score || a.idx - b.idx);
          if (buttons.length) {
            buttons[0].el.setAttribute('data-cloak-action', marker);
            return true;
          }
          return false;
        }""",
        marker,
    )
    if result:
        result = _human_click_marked(page, marker, timeout=4500)
    if not result:
        try:
            _human_press(page, "Enter")
            result = True
        except Exception:
            result = False
    return bool(result)


def _complete_profile(page: Any, name: str, birthday: str) -> bool:
    if str(_page_state(page).get("state") or "") != "profile":
        return False
    info = _fill_profile_fields_humanized(page, name, birthday)
    if info.get("birthMode") == "spinbutton_needed":
        if _fill_birthday_spinbuttons(page, birthday):
            info["ok"] = True
            info["birthMode"] = "spinbutton"
    if info.get("ok"):
        checked = _safe_evaluate(
            page,
            r"""() => {
              const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
                && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none';
              return [...document.querySelectorAll('input[type="checkbox"],[role="checkbox"]')]
                .filter(visible)
                .some(el => el.checked === true
                  || String(el.getAttribute('aria-checked') || '').toLowerCase() === 'true');
            }""",
        )
        if checked is False:
            marker = _new_action_marker("chatgpt-profile-checkbox")
            marked = _safe_evaluate(
                page,
                r"""marker => {
                  const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
                    && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none';
                  const box = [...document.querySelectorAll('input[type="checkbox"],[role="checkbox"]')]
                    .find(visible);
                  if (!box) return false;
                  const target = box.closest('label') || box;
                  target.setAttribute('data-cloak-action', marker);
                  return true;
                }""",
                marker,
            )
            if marked:
                _human_click_marked(page, marker, timeout=2500)
    if not info.get("ok"):
        raise RuntimeError(f"资料页字段填写不完整: {json.dumps(info, ensure_ascii=False)[:800]}")
    if not _submit_profile(page):
        raise RuntimeError(f"资料页找不到提交按钮: {_safe_url_for_log(_page_url(page))}")
    _log(f"资料页已提交: {name}, {birthday}")
    deadline = time.time() + _to_ms(getattr(cfg, "CLOAK_PROFILE_TIMEOUT", 60), 60) / 1000.0
    cloudflare_deadline = None
    last_cloudflare_attempt = 0.0
    while time.time() < deadline:
        _check_stop()
        info = _page_state(page)
        state = str(info.get("state") or "other")
        if info.get("hasCloudflare"):
            if cloudflare_deadline is None:
                cloudflare_deadline = time.time() + _cloudflare_timeout_seconds()
                _log("资料提交后检测到 Cloudflare/Turnstile 验证")
            deadline = max(deadline, cloudflare_deadline)
            if time.time() - last_cloudflare_attempt >= 2.0:
                last_cloudflare_attempt = time.time()
                _try_cloudflare_challenge(page)
        elif cloudflare_deadline is not None:
            _log("Cloudflare/Turnstile 验证已通过")
            cloudflare_deadline = None
            deadline = max(
                deadline,
                time.time() + _to_ms(getattr(cfg, "CLOAK_PROFILE_TIMEOUT", 60), 60) / 1000.0,
            )
        if (
            state in {"chatgpt", "other"}
            and not _is_profile_path(_url_path(_page_url(page)))
        ):
            return True
        time.sleep(0.5)
    raise RuntimeError(
        f"资料提交后未进入 ChatGPT: {_safe_url_for_log(_page_url(page))}"
    )


def _read_session_request(context: Any) -> Optional[dict]:
    try:
        response = context.request.get(
            "https://chatgpt.com/api/auth/session",
            timeout=_action_timeout_ms(),
            headers={
                "accept": "application/json",
                "referer": "https://chatgpt.com/",
                "cache-control": "no-cache",
            },
        )
        data = response.json()
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _read_session_page(page: Any) -> Optional[dict]:
    data = _safe_evaluate(
        page,
        r"""async ({timeoutMs}) => {
          const controller = new AbortController();
          const timer = setTimeout(() => controller.abort('timeout'), timeoutMs);
          try {
            const response = await fetch('/api/auth/session', {
              credentials: 'include',
              cache: 'no-store',
              headers: {'accept': 'application/json'},
              signal: controller.signal
            });
            return await response.json();
          } finally {
            clearTimeout(timer);
          }
        }""",
        {"timeoutMs": min(5000, _action_timeout_ms())},
    )
    return data if isinstance(data, dict) else None


def _fetch_session(page: Any, context: Any) -> dict:
    timeout = _to_ms(getattr(cfg, "CLOAK_SESSION_TIMEOUT", 90), 90) / 1000.0
    deadline = time.time() + timeout
    started = time.time()
    last_payload: Any = None
    redirected = False
    callback_seen = False
    cloudflare_deadline = None
    last_cloudflare_attempt = 0.0
    while time.time() < deadline:
        _check_stop()
        data = _read_session_request(context)
        if data:
            last_payload = data
            if data.get("accessToken"):
                return data
        url = _page_url(page)
        if _is_chatgpt_url(url) and not _is_auth_flow_url(url):
            data = _read_session_page(page)
            if data:
                last_payload = data
                if data.get("accessToken"):
                    return data
        if _is_auth_callback_url(url) and not callback_seen:
            callback_seen = True
            _log("检测到 OAuth 回调，等待回调完成，避免跳转页面")
        info = _page_state(page)
        state = str(info.get("state") or "other")
        if info.get("hasCloudflare"):
            if cloudflare_deadline is None:
                cloudflare_deadline = time.time() + _cloudflare_timeout_seconds()
                _log("读取会话时检测到 Cloudflare/Turnstile 验证")
            deadline = max(deadline, cloudflare_deadline)
            if time.time() - last_cloudflare_attempt >= 2.0:
                last_cloudflare_attempt = time.time()
                _try_cloudflare_challenge(page)
        elif cloudflare_deadline is not None:
            _log("Cloudflare/Turnstile 验证已通过")
            cloudflare_deadline = None
            deadline = max(deadline, time.time() + max(6.0, timeout))
        if (
            not redirected
            and time.time() - started >= 12
            and _can_open_chatgpt_home(url, state, bool(info.get("hasCloudflare")))
        ):
            redirected = True
            try:
                _log(f"从 ChatGPT 主页读取会话，当前路径: {_url_path(url) or '-'}")
                page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=_navigation_timeout_ms())
                _human_pause(0.5, 1.0)
            except Exception as exc:
                if _is_target_closed(exc):
                    raise
        time.sleep(0.7)
    raise RuntimeError(
        "等待 /api/auth/session accessToken 超时，最后响应: "
        + json.dumps(last_payload, ensure_ascii=False)[:800]
    )


def _read_browser_identity(page: Any, context: Any) -> tuple[str, str]:
    device_id = ""
    try:
        for cookie in context.cookies(["https://chatgpt.com", "https://auth.openai.com"]):
            if cookie.get("name") == "oai-did":
                device_id = str(cookie.get("value") or "")
                break
    except Exception:
        pass
    user_agent = ""
    try:
        user_agent = str(page.evaluate("() => navigator.userAgent") or "")
    except Exception:
        pass
    return device_id, user_agent


def _registration_identity() -> tuple[str, str]:
    first_names = ("Alex", "Chris", "Daniel", "Ethan", "Jason", "Kevin", "Leo", "Ryan")
    last_names = ("Brown", "Clark", "Davis", "Evans", "Green", "Miller", "Taylor", "Walker")
    name = str(getattr(cfg, "CLOAK_REGISTRATION_NAME", "") or "").strip()
    if not name:
        name = f"{random.choice(first_names)} {random.choice(last_names)}"
    birthday = str(getattr(cfg, "CLOAK_REGISTRATION_BIRTHDAY", "") or "").strip()
    birthday_valid = False
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", birthday):
        try:
            parsed = date.fromisoformat(birthday)
            today = date.today()
            age = today.year - parsed.year - ((today.month, today.day) < (parsed.month, parsed.day))
            birthday_valid = 18 <= age <= 100
        except ValueError:
            birthday_valid = False
    if not birthday_valid:
        start = date(1988, 1, 1).toordinal()
        end = date(2003, 12, 31).toordinal()
        birthday = date.fromordinal(random.randint(start, end)).isoformat()
    return name, birthday


def _run_sync(
    proxy: Optional[str],
    run_ctx: Optional[dict] = None,
    assigned_domain: Optional[str] = None,
    batch_id: Optional[int] = None,
    worker_index: Optional[int] = None,
) -> dict:
    """Complete browser registration and return data for protocol OAuth."""
    run_ctx = run_ctx if run_ctx is not None else {}
    proxy = cfg.format_docker_url(proxy)
    mail_proxy = proxy
    if mail_proxy and mail_proxy.startswith("socks5://"):
        mail_proxy = mail_proxy.replace("socks5://", "socks5h://")
    proxies = {"http": mail_proxy, "https": mail_proxy} if mail_proxy else None

    playwright = None
    browser = None
    context = None
    page = None
    email = ""
    email_jwt = ""
    password = _generate_password()
    name, birthday = _registration_identity()
    try:
        _check_stop()
        playwright, browser, context, page = runtime.launch_browser(
            proxy,
            locale=str(getattr(cfg, "CLOAK_LOCALE", "") or "").strip() or "en-US",
            timezone_name=str(getattr(cfg, "CLOAK_TIMEZONE", "") or "").strip(),
            geoip=bool(getattr(cfg, "CLOAK_GEOIP", True)),
        )
        _log("正在创建账号")
        page.goto(START_URL, wait_until="domcontentloaded", timeout=_navigation_timeout_ms())
        _human_pause(0.6, 1.2)
        _maybe_accept_cookies(page)

        _find_email_input(page)
        email, email_jwt = get_email_and_token(
            proxies,
            assigned_domain=assigned_domain,
            batch_id=batch_id,
            worker_index=worker_index,
        )
        if not email:
            raise RuntimeError("邮箱服务未分配邮箱")
        set_last_email(email)
        _log(f"分配邮箱: {mask_email(email)}，密码长度: {len(password)}")
        email_input = _find_email_input(page)
        _human_fill(page, email_input, email)
        _submit_email(page, email)
        state = _wait_transition(page)
        if state == "email_error":
            error_info = _page_state(page)
            _mark_account_email_blocked(run_ctx)
            raise AccountEmailBlocked(_account_create_error_detail(error_info, page))

        if state == "login_password":
            run_ctx["discarded_email_failure"] = True
            run_ctx["signup_blocked"] = True
            raise RuntimeError("邮箱已存在并进入登录密码页，标记为不可用邮箱")

        if state == "email_verification":
            state = _switch_to_password_registration(page)

        if state == "password":
            try:
                _fill_password(page, password)
            except AccountEmailBlocked:
                _mark_account_email_blocked(run_ctx)
                raise
            run_ctx["cloak_registration_acknowledged"] = True
            state = _page_state(page).get("state") or "other"

        if state not in {"profile", "chatgpt"}:
            if not _wait_for_otp_page(page, timeout=35):
                current_info = _page_state(page)
                state = str(current_info.get("state") or "other")
                if state == "password":
                    try:
                        _fill_password(page, password)
                    except AccountEmailBlocked:
                        _mark_account_email_blocked(run_ctx)
                        raise
                    run_ctx["cloak_registration_acknowledged"] = True
                    current_info = _page_state(page)
                    state = str(current_info.get("state") or "other")
                    _log(f"延迟进入密码页并设置完成，当前状态: {state}")
                if state == "login_password":
                    run_ctx["discarded_email_failure"] = True
                    run_ctx["signup_blocked"] = True
                    raise RuntimeError("邮箱已存在并进入登录密码页，标记为不可用邮箱")
                error_text = _page_error_summary(current_info)
                if state == "email_error" or error_text:
                    _mark_account_email_blocked(run_ctx)
                    raise AccountEmailBlocked(
                        _account_create_error_detail(current_info, page)
                    )
                if state not in {"email_verification", "profile", "chatgpt"}:
                    raise RuntimeError(f"未进入邮箱验证码页，当前状态: {state}")

        processed_mails = set()
        if str(_page_state(page).get("state") or "") == "email_verification":
            max_attempts = max(1, int(getattr(cfg, "MAX_OTP_RETRIES", 3) or 3))
            for attempt in range(1, max_attempts + 1):
                _check_stop()
                _log(f"等待邮箱验证码 ({attempt}/{max_attempts}): {mask_email(email)}")
                code = get_oai_code(
                    email,
                    jwt=email_jwt,
                    proxies=proxies,
                    processed_mail_ids=processed_mails,
                )
                if not code:
                    if attempt < max_attempts and _resend_otp(page):
                        _log("未收到验证码，已触发重新发送")
                        _human_pause(1.0, 2.0)
                        continue
                    raise RuntimeError("等待邮箱验证码超时")
                _fill_otp(page, code)
                _click_continue(page)
                outcome = _wait_after_otp(page)
                _log(
                    f"验证码提交结果: {outcome}，"
                    f"{_safe_url_for_log(_page_url(page)) or '-'}"
                )
                if outcome in {"accepted", "unknown"}:
                    break
                if attempt >= max_attempts:
                    raise RuntimeError("邮箱验证码连续无效或已过期")
                if _resend_otp(page):
                    _human_pause(1.0, 2.0)

        state = str(_page_state(page).get("state") or "other")
        if state == "profile":
            _complete_profile(page, name, birthday)
            run_ctx["cloak_registration_acknowledged"] = True

        session_info = _fetch_session(page, context)
        access_token = str(session_info.get("accessToken") or "")
        if not access_token:
            raise RuntimeError("注册流程完成但未取得 accessToken")
        device_id, user_agent = _read_browser_identity(page, context)
        if device_id:
            run_ctx["device_id"] = device_id
        if user_agent:
            run_ctx["user_agent"] = user_agent
        run_ctx["access_token"] = access_token
        run_ctx["cloak_registration_acknowledged"] = True
        _log(f"Cloak 注册完成: {mask_email(email)}")
        return {
            "email": email,
            "password": password,
            "access_token": access_token,
            "device_id": device_id,
            "user_agent": user_agent,
            "session": session_info,
        }
    finally:
        runtime.close_browser(playwright, browser, context)


def run(
    proxy: Optional[str],
    run_ctx: Optional[dict] = None,
    assigned_domain: Optional[str] = None,
    batch_id: Optional[int] = None,
    worker_index: Optional[int] = None,
) -> dict:
    return runtime.run_with_thread_affinity(
        _run_sync,
        thread_name="cloakbrowser-chatgpt-register",
        proxy=proxy,
        run_ctx=run_ctx,
        assigned_domain=assigned_domain,
        batch_id=batch_id,
        worker_index=worker_index,
    )
