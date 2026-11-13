"""X.com registration through CloakBrowser.

This flow creates an X account and returns only credentials used for local
inventory. It intentionally does not run any OAuth flow.
"""
from __future__ import annotations

import random
import re
import time
from typing import Any, Optional

from utils import config as cfg
from utils.email_providers.mail_service import (
    get_email_and_token,
    get_oai_code,
    mask_email,
    set_last_email,
)

from . import cloak_runtime as runtime
from .user_utils import _generate_password


X_COM_START_URL = "https://x.com/i/flow/signup"
_X_HANDLE_PREFIXES = (
    "Alex", "Avery", "Blake", "Cameron", "Casey", "Charlie", "Drew",
    "Elliot", "Emerson", "Finley", "Harper", "Hayden", "Jamie", "Jordan",
    "Kai", "Kendall", "Logan", "Mason", "Morgan", "Parker", "Quinn",
    "Reese", "Riley", "Robin", "Rowan", "Sage", "Skyler", "Taylor",
)
_X_HANDLE_SURNAMES = (
    "Anderson", "Bailey", "Bennett", "Brooks", "Carter", "Coleman",
    "Cooper", "Davis", "Foster", "Gray", "Hayes", "Hughes", "Jenkins",
    "Kelly", "Lewis", "Mitchell", "Nelson", "Perry", "Powell", "Reed",
    "Ross", "Russell", "Sullivan", "Turner", "Walker", "Ward", "Watson",
    "Wells", "Wood", "Young",
)
_X_USERNAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,14}$")
_X_USERNAME_REJECTION_PATTERN = re.compile(
    r"(?:username|用户名).{0,80}"
    r"(?:unavailable|not available|taken|already|invalid|not valid|too (?:long|short)|"
    r"only contain|must|can.t be|isn.t available|不可用|已被占用|无效|不合法|只能|太长|太短)"
    r"|(?:unavailable|not available|already (?:taken|in use)|can.t be used).{0,80}"
    r"(?:username|用户名)"
    r"|please match the requested format",
    re.IGNORECASE,
)

_log = runtime.log
_check_stop = runtime.check_stop
_action_timeout_ms = runtime.action_timeout_ms
_navigation_timeout_ms = runtime.navigation_timeout_ms
_human_pause = runtime.human_pause
_page_url = runtime.page_url
_safe_url_for_log = runtime.safe_url_for_log
_safe_evaluate = runtime.safe_evaluate
_visible_locator = runtime.visible_locator
_human_fill = runtime.human_fill
_human_fill_verified = runtime.human_fill_verified
_human_input_value = runtime.human_input_value
_human_click = runtime.human_click
_human_click_marked = runtime.human_click_marked
_human_select = runtime.human_select
_human_type = runtime.human_type
_maybe_accept_cookies = runtime.maybe_accept_cookies
_new_action_marker = runtime.new_action_marker


def _is_x_com_url(url: str) -> bool:
    try:
        host = str(url.split("://", 1)[1].split("/", 1)[0].split(":", 1)[0]).lower()
    except (IndexError, AttributeError):
        return False
    return host == "x.com" or host.endswith(".x.com")


def _x_com_page_state(page: Any) -> dict:
    result = _safe_evaluate(
        page,
        r"""marker => {
          const url = String(location.href || '');
          const host = String(location.hostname || '').toLowerCase();
          const path = String(location.pathname || '/').toLowerCase();
          const visible = el => {
            if (!el) return false;
            const st = getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            return st.visibility !== 'hidden' && st.display !== 'none'
              && rect.width > 0 && rect.height > 0;
          };
          const hasVisible = selector =>
            Array.from(document.querySelectorAll(selector)).some(visible);
          const body = String(document.body && document.body.innerText || '').toLowerCase();
          const title = String(document.title || '').toLowerCase();
          const text = `${title} ${body}`;
          const hash = String(location.hash || '').toLowerCase();
          const inViewport = el => {
            if (!el) return false;
            const rect = el.getBoundingClientRect();
            return rect.bottom > 0 && rect.right > 0
              && rect.top < window.innerHeight && rect.left < window.innerWidth;
          };
          const emailInput = hasVisible(
            "input[name='email'],input[name='username_or_email'],input[type='email'],"
            + "input[autocomplete='email'],input[autocomplete='username'],"
            + "input[placeholder*='Email or username' i],input[placeholder*='email' i]"
          );
          const passwordInput = [...document.querySelectorAll(
            "input[name='password'],input[type='password'],input[autocomplete='new-password']"
          )].some(el => visible(el) && !el.inert
            && String(el.getAttribute('inert') || '').toLowerCase() !== 'true'
            && inViewport(el)
            && getComputedStyle(el).pointerEvents !== 'none'
            && Number(getComputedStyle(el).opacity || 1) > 0.05);
          const codeInput = hasVisible(
            "input[autocomplete='one-time-code'],input[name='verification_code'],input[name='code'],input[inputmode='numeric']"
          );
          const singleDigitInputs = [
            ...document.querySelectorAll("input[maxlength='1'][inputmode='numeric'],input[maxlength='1'][autocomplete='one-time-code']")
          ].filter(visible);
          const hasOtp = codeInput || singleDigitInputs.length >= 4;
          const unifiedSignup = hash.includes('/unified_signup/')
            || (
              hasVisible("input[name='name']")
              && hasVisible("input[name='username']")
              && passwordInput
            );
          const hasFaceLiveness = hash.includes('/face_liveness/')
            || /confirm (with face|you.re a human)|真人|人脸|面部识别|活体/.test(text);
          const hasChallenge = /verify you are human|verification|confirm your identity|check your email|确认你是人类|验证/.test(text)
            && !passwordInput && !hasOtp;
          const postSignup = /\/i\/jf\/onboarding\/web\/post_signup(?:\/|$)/.test(path);
          const registrationComplete = postSignup;
          const onboarding = path.includes('/i/jf/onboarding') && !registrationComplete;
          const visibleControls = [...document.querySelectorAll('input,select,textarea,[role="combobox"]')]
            .filter(visible)
            .map(el => ({
              tag: String(el.tagName || '').toLowerCase(),
              type: String(el.type || ''),
              name: String(el.name || ''),
              id: String(el.id || ''),
              placeholder: String(el.placeholder || ''),
              ariaLabel: String(el.getAttribute('aria-label') || ''),
              valueLength: String(el.value || '').length,
              valid: typeof el.checkValidity === 'function' ? el.checkValidity() : true,
              minLength: Number(el.minLength),
              maxLength: Number(el.maxLength),
              pattern: String(el.getAttribute('pattern') || ''),
              inputMode: String(el.getAttribute('inputmode') || ''),
              ariaInvalid: String(el.getAttribute('aria-invalid') || ''),
              validationMessage: String(el.validationMessage || '').slice(0, 240)
            }));
          const visibleButtons = [...document.querySelectorAll('button,[role="button"]')]
            .filter(visible)
            .map(el => {
              const rect = el.getBoundingClientRect();
              return {
                text: String(el.innerText || el.textContent || '').trim().slice(0, 120),
                testId: String(el.getAttribute('data-testid') || ''),
                type: String(el.getAttribute('type') || ''),
                disabled: !!el.disabled || String(el.getAttribute('aria-disabled') || '').toLowerCase() === 'true',
                top: Math.round(rect.top),
                left: Math.round(rect.left)
              };
            });
          const visibleSubmitNodes = [...document.querySelectorAll(
            '[name="submit"],[type="submit"],[class*="submit" i],[data-testid*="submit" i]'
          )].filter(visible).map(el => {
            const rect = el.getBoundingClientRect();
            const style = getComputedStyle(el);
            return {
              tag: String(el.tagName || '').toLowerCase(),
              text: String(el.innerText || el.textContent || '').trim().slice(0, 120),
              disabled: !!el.disabled,
              ariaDisabled: el.getAttribute('aria-disabled'),
              pointerEvents: style.pointerEvents,
              opacity: style.opacity,
              top: Math.round(rect.top),
              left: Math.round(rect.left)
            };
          });
          const validationErrors = visibleControls
            .filter(control => control.validationMessage || control.ariaInvalid === 'true' || !control.valid)
            .map(control => {
              const label = control.name || control.id || control.ariaLabel || control.placeholder || control.type || control.tag;
              const message = control.validationMessage || `invalid:${label}`;
              return `${label}: ${message}`;
            });
          const feedbackErrors = [...document.querySelectorAll(
            "[role='alert'],[aria-live='polite'],[aria-live='assertive'],"
            + "[data-testid*='error' i],[class*='error' i],[aria-invalid='true']"
          )].filter(visible).map(el => String(el.innerText || el.textContent || '').trim())
            .filter(Boolean);
          const errors = [...new Set([...feedbackErrors, ...validationErrors])].slice(0, 12);
          const callbackOrHome = (
            (host === 'x.com' || host.endsWith('.x.com'))
            && !path.includes('/i/flow/')
            && (
              path === '/home'
              || path.startsWith('/home/')
              || hasVisible("[data-testid='SideNav_AccountSwitcher_Button']")
              || hasVisible("a[href='/home']")
            )
          );
          return {
            url,
            host,
            path,
            bodyPreview: body.slice(0, 300),
            emailInput,
            passwordInput,
            hasOtp,
            unifiedSignup,
            hasFaceLiveness,
            hasChallenge,
            callbackOrHome,
            postSignup,
            registrationComplete,
            onboarding,
            controls: visibleControls.slice(0, 30),
            buttons: visibleButtons.slice(0, 30),
            submitNodes: visibleSubmitNodes.slice(0, 10),
            errors,
            validationErrors
          };
        }""",
    )
    return result if isinstance(result, dict) else {"url": _page_url(page)}


def _x_com_state_summary(state: dict) -> str:
    """Return a compact, log-safe snapshot of an unrecognised X.com page."""
    controls = []
    for control in (state or {}).get("controls") or []:
        if not isinstance(control, dict):
            continue
        label = (
            control.get("name")
            or control.get("id")
            or control.get("ariaLabel")
            or control.get("placeholder")
            or control.get("type")
            or control.get("tag")
            or "?"
        )
        details = (
            f"{label}({'valid' if control.get('valid', True) else 'invalid'},"
            f"len={control.get('valueLength', 0)}"
        )
        try:
            max_length = int(control.get("maxLength") or -1)
        except (TypeError, ValueError):
            max_length = -1
        if max_length >= 0:
            details += f",max={max_length}"
        if control.get("pattern"):
            details += f",pattern={str(control.get('pattern'))[:80]}"
        if control.get("validationMessage"):
            details += f",message={str(control.get('validationMessage'))[:160]}"
        controls.append(details + ")")
    buttons = []
    for button in (state or {}).get("buttons") or []:
        if not isinstance(button, dict):
            continue
        text = str(button.get("text") or button.get("testId") or button.get("type") or "?").strip()
        suffix = ",disabled" if button.get("disabled") else ""
        buttons.append(f"{text}{suffix}")
    errors = [str(item).strip()[:240] for item in (state or {}).get("errors") or [] if str(item).strip()]
    return (
        f"path={str((state or {}).get('path') or '/')[:160]} "
        f"controls={controls[:10]} buttons={buttons[:10]} errors={errors[:5]} "
        f"hash={str((state or {}).get('url') or '').split('#', 1)[-1][:160] if '#' in str((state or {}).get('url') or '') else '-'}"
    )


def _x_com_read_cookies(context: Any) -> dict:
    try:
        cookies = context.cookies(["https://x.com", "https://twitter.com"])
    except Exception:
        return {}
    return {
        str(cookie.get("name") or ""): str(cookie.get("value") or "")
        for cookie in cookies or []
        if cookie.get("name")
    }


def _x_com_wait_for_auth_token(context: Any, timeout: float = 10.0) -> str:
    deadline = time.time() + max(0.5, timeout)
    while time.time() < deadline:
        _check_stop()
        token = str(_x_com_read_cookies(context).get("auth_token") or "").strip()
        if token:
            return token
        time.sleep(0.4)
    return ""


def _x_com_find_email_input(page: Any, timeout_ms: Optional[int] = None) -> Any:
    deadline = time.time() + (timeout_ms or _action_timeout_ms()) / 1000.0
    selectors = [
        "input[name='email']",
        "input[name='username']",
        "input[name='username_or_email']",
        "input[type='email']",
        "input[autocomplete='email']",
        "input[autocomplete='username']",
        "input[placeholder*='Email or username' i]",
        "input[placeholder*='email' i]",
        "input[placeholder*='邮箱']",
    ]
    while time.time() < deadline:
        _check_stop()
        locator = _visible_locator(page, selectors, timeout_ms=500)
        if locator is not None:
            return locator
        _x_com_click_email_entry(page)
        time.sleep(0.35)
    raise RuntimeError(f"X.com 找不到邮箱输入框，当前页面: {_safe_url_for_log(_page_url(page)) or '-'}")


def _x_com_click_email_entry(page: Any) -> bool:
    selectors = [
        "[data-testid='signupWithEmailButton']",
        "[data-testid='emailSignupButton']",
        "button:has-text('Sign up with email')",
        "button:has-text('Use email')",
        "button:has-text('Continue with email')",
        "button:has-text('使用邮箱')",
        "button:has-text('用邮箱注册')",
        "a:has-text('Sign up with email')",
        "a:has-text('Use email')",
    ]
    locator = _visible_locator(page, selectors, timeout_ms=500)
    if locator is not None:
        return _human_click(locator, timeout=3500)
    marker = _new_action_marker("x-email-entry")
    result = _safe_evaluate(
        page,
        r"""marker => {
          const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
            && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none'
            && !el.disabled && String(el.getAttribute('aria-disabled') || '').toLowerCase() !== 'true';
          const nodes = [...document.querySelectorAll('button,a,[role="button"]')].filter(visible);
          const match = nodes.find(el => {
            const blob = [
              el.innerText, el.textContent, el.getAttribute('aria-label'),
              el.getAttribute('data-testid'), el.getAttribute('href')
            ].filter(Boolean).join(' ').toLowerCase();
            return /(sign\s*up|create).{0,20}(email|邮箱)/.test(blob)
              || /(email|邮箱).{0,20}(sign\s*up|register|注册)/.test(blob)
              || /use\s+(email|phone).{0,20}email/.test(blob);
          });
          if (!match) return false;
          match.setAttribute('data-cloak-action', marker);
          return true;
        }""",
        marker,
    )
    return bool(result) and _human_click_marked(page, marker, timeout=3500)


def _x_com_click_submit(page: Any) -> bool:
    state = _x_com_page_state(page)
    email_controls = [
        control
        for control in state.get("controls") or []
        if any(
            token in str(control.get("name") or control.get("id") or "").lower()
            for token in ("email", "username")
        )
    ]
    if email_controls and all(int(control.get("valueLength") or 0) <= 0 for control in email_controls):
        _human_pause(0.5, 1.0)
    marker = _new_action_marker("x-submit")
    submit_result = _safe_evaluate(
        page,
        r"""marker => {
          const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
            && getComputedStyle(el).display !== 'none' && getComputedStyle(el).visibility !== 'hidden';
          const email = [...document.querySelectorAll(
            'input[name="username_or_email"],input[name="email"],input[name="username"],input[type="email"]'
          )].find(visible);
          if (email && !String(email.value || '').trim()) return {clicked: false, reason: 'empty-email'};
          const scope = email && email.closest('form') ? email.closest('form') : document;
          const nodes = [...scope.querySelectorAll('[name="submit"],[type="submit"],[class*="submit" i],[data-testid*="submit" i]')]
            .filter(visible);
          const node = nodes.find(el => {
            const st = getComputedStyle(el);
            return !el.disabled
              && String(el.getAttribute('aria-disabled') || '').toLowerCase() !== 'true'
              && st.pointerEvents !== 'none'
              && Number(st.opacity || 1) > 0.05
              && el.getBoundingClientRect().height > 8;
          });
          if (!node) return {clicked: false};
          node.setAttribute('data-cloak-action', marker);
          return {
            clicked: true,
            tag: node.tagName,
            text: String(node.innerText || node.textContent || '').trim().slice(0, 80)
          };
        }""",
        marker,
    )
    if isinstance(submit_result, dict) and submit_result.get("clicked"):
        clicked = _human_click_marked(page, marker, timeout=4500)
        if clicked:
            return True
    if _x_com_click_text_control(page, ["Continue", "Next", "继续", "下一步"]):
        return True
    fallback_marker = _new_action_marker("x-submit-fallback")
    result = _safe_evaluate(
        page,
        r"""() => {
          const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
            && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none';
          const enabled = el => visible(el) && !el.disabled
            && String(el.getAttribute('aria-disabled') || '').toLowerCase() !== 'true';
          const controls = [
            ...document.querySelectorAll(
              "input:not([type='hidden']):not([type='radio']):not([type='checkbox']),select,textarea,[role='combobox']"
            )
          ].filter(enabled);
          if (controls.some(el => typeof el.checkValidity === 'function' && !el.checkValidity())) {
            return {clicked: false, reason: 'invalid-field'};
          }
          const lastControl = controls.length
            ? controls.reduce((lowest, el) =>
                el.getBoundingClientRect().bottom > lowest.getBoundingClientRect().bottom ? el : lowest
              )
            : null;
          const controlRect = lastControl ? lastControl.getBoundingClientRect() : null;
          const candidates = [...document.querySelectorAll(
            'button,[role="button"],input[type="submit"],[name="submit"],'
            + '[data-testid*="submit" i],[class*="submit" i]'
          )]
            .filter(enabled)
            .map(el => ({
              el,
              text: String(
                el.innerText || el.textContent || el.value || el.getAttribute('aria-label') || ''
              ).trim().toLowerCase(),
              testId: String(el.getAttribute('data-testid') || '').toLowerCase(),
              type: String(el.getAttribute('type') || '').toLowerCase(),
              rect: el.getBoundingClientRect()
            }))
            .filter(item => {
              if (item.type === 'submit' || /continue|next|sign up|create account|finish|done|save|继续|下一步|注册|完成/.test(item.text)) {
                return true;
              }
              return /continue|next|submit|signup|login|flow/.test(item.testId);
            })
            .filter(item => !controlRect || item.rect.top >= controlRect.bottom - 16)
            .sort((a, b) => a.rect.top - b.rect.top);
          if (!candidates.length) {
            const disabled = [...document.querySelectorAll('button,[role="button"],input[type="submit"]')]
              .filter(visible)
              .some(el => /continue|next|sign up|create account|finish|done|save|继续|下一步|注册|完成/.test(
                String(el.innerText || el.textContent || el.value || '').trim().toLowerCase()
              ));
            return {clicked: false, reason: disabled ? 'submit-disabled' : 'submit-not-found'};
          }
          const button = candidates[0].el;
          button.setAttribute('data-cloak-action', marker);
          return {clicked: true, reason: 'marked'};
        }""",
        fallback_marker,
    )
    if isinstance(result, dict) and result.get("clicked"):
        return _human_click_marked(page, fallback_marker, timeout=4500)
    selectors = [
        "[data-testid='LoginForm_Login_Button']",
        "[data-testid='signupButton']",
        "[data-testid='ocfEnterTextNextButton']",
        "button[type='submit']",
        "button:has-text('Next')",
        "button:has-text('Continue')",
        "button:has-text('Sign up')",
        "button:has-text('下一步')",
        "button:has-text('继续')",
        "button:has-text('注册')",
    ]
    locator = _visible_locator(page, selectors, timeout_ms=900)
    if locator is not None:
        return _human_click(locator, timeout=4500)
    return False


def _x_com_click_text_control(page: Any, labels: list[str]) -> bool:
    """Click the interactive ancestor of X's text-only JF controls."""
    marker = _new_action_marker("x-text-control")
    result = _safe_evaluate(
        page,
        r"""({labels, marker}) => {
          const wanted = new Set(labels.map(value => String(value).trim().toLowerCase()));
          const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
            && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none';
          const enabled = el => {
            if (!visible(el)) return false;
            if (el.disabled || String(el.getAttribute('aria-disabled') || '').toLowerCase() === 'true') return false;
            const style = getComputedStyle(el);
            return style.pointerEvents !== 'none' && Number(style.opacity || 1) > 0.05;
          };
          const textNodes = [...document.querySelectorAll('button,[role="button"],a,a *,button *,div,span,p')]
            .filter(visible)
            .filter(el => wanted.has(String(el.innerText || el.textContent || '').trim().toLowerCase()));
          for (const node of textNodes) {
            if (!enabled(node)) continue;
            node.setAttribute('data-cloak-action', marker);
            return {
              clicked: true,
              tag: String(node.tagName || '').toLowerCase(),
              role: String(node.getAttribute('role') || '').toLowerCase(),
              text: String(node.innerText || node.textContent || '').trim().slice(0, 120)
            };
          }
          return {clicked: false};
        }""",
        {"labels": labels, "marker": marker},
    )
    return (
        isinstance(result, dict)
        and bool(result.get("clicked"))
        and _human_click_marked(page, marker, timeout=4000)
    )


def _x_com_submit_diagnostics(page: Any) -> dict:
    result = _safe_evaluate(
        page,
        r"""() => {
          const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
            && getComputedStyle(el).display !== 'none' && getComputedStyle(el).visibility !== 'hidden';
          return {
            url: String(location.href || ''),
            active: document.activeElement ? {
              tag: document.activeElement.tagName,
              name: document.activeElement.getAttribute('name'),
              text: String(document.activeElement.innerText || document.activeElement.textContent || '').trim().slice(0, 100)
            } : null,
            matches: [...document.querySelectorAll('button,[role="button"],[name="submit"],[type="submit"],div,span,p')]
              .filter(visible)
              .filter(el => ['continue', 'next', '继续', '下一步'].includes(
                String(el.innerText || el.textContent || '').trim().toLowerCase()
              ))
              .map(el => {
                const st = getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return {
                  tag: el.tagName,
                  role: el.getAttribute('role'),
                  name: el.getAttribute('name'),
                  text: String(el.innerText || el.textContent || '').trim().slice(0, 100),
                  pointerEvents: st.pointerEvents,
                  cursor: st.cursor,
                  opacity: st.opacity,
                  disabled: !!el.disabled,
                  ariaDisabled: el.getAttribute('aria-disabled'),
                  rect: {top: rect.top, left: rect.left, width: rect.width, height: rect.height}
                };
              }),
            controls: [...document.querySelectorAll('input,textarea,select,[role="combobox"]')]
              .filter(visible)
              .map(el => ({
                tag: String(el.tagName || '').toLowerCase(),
                type: String(el.getAttribute('type') || ''),
                name: String(el.getAttribute('name') || ''),
                id: String(el.getAttribute('id') || ''),
                placeholder: String(el.getAttribute('placeholder') || ''),
                ariaLabel: String(el.getAttribute('aria-label') || ''),
                valueLength: String(el.value || '').length,
                valid: typeof el.checkValidity === 'function' ? el.checkValidity() : true,
                minLength: Number(el.minLength),
                maxLength: Number(el.maxLength),
                pattern: String(el.getAttribute('pattern') || ''),
                inputMode: String(el.getAttribute('inputmode') || ''),
                ariaInvalid: String(el.getAttribute('aria-invalid') || ''),
                validationMessage: String(el.validationMessage || '').slice(0, 240),
                rect: (() => {
                  const rect = el.getBoundingClientRect();
                  return {
                    top: Math.round(rect.top),
                    left: Math.round(rect.left),
                    width: Math.round(rect.width),
                    height: Math.round(rect.height)
                  };
                })()
              })),
            feedback: [...document.querySelectorAll(
              "[role='alert'],[aria-live='polite'],[aria-live='assertive'],"
              + "[data-testid*='error' i],[class*='error' i],[aria-invalid='true']"
            )].filter(visible).map(el => String(el.innerText || el.textContent || '').trim())
              .filter(Boolean).slice(0, 12),
            marked: [...document.querySelectorAll('[data-cloak-action]')]
              .map(el => ({
                tag: String(el.tagName || '').toLowerCase(),
                text: String(el.innerText || el.textContent || '').trim().slice(0, 100),
                marker: String(el.getAttribute('data-cloak-action') || '').slice(0, 100)
              }))
          };
        }""",
    )
    return result if isinstance(result, dict) else {}


def _x_com_fill_otp(page: Any, code: str) -> bool:
    locator = _visible_locator(
        page,
        [
            "input[autocomplete='one-time-code']",
            "input[name='verification_code']",
            "input[name='code']",
            "input[inputmode='numeric']",
        ],
        timeout_ms=900,
    )
    if locator is not None:
        _human_fill(page, locator, code)
        return True
    boxes = page.locator("input[maxlength='1'][inputmode='numeric'],input[maxlength='1'][autocomplete='one-time-code']")
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
        return True
    return False


def _x_com_fill_password(page: Any, password: str) -> bool:
    locator = _visible_locator(
        page,
        [
            "input[name='password']",
            "input[type='password']",
            "input[autocomplete='new-password']",
        ],
        timeout_ms=1200,
    )
    if locator is None:
        return False
    _human_fill(page, locator, password)
    return True


def _x_com_fill_email(page: Any, email: str) -> bool:
    locator = _x_com_find_email_input(page)
    _human_fill_verified(page, locator, email)
    return True


def _x_com_username_candidates(
    *,
    email_prefix: str = "",
    first_name: str = "",
    last_name: str = "",
    count: int = 12,
) -> list[str]:
    """Build X-compatible handle candidates from the account identity."""
    def clean(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_]", "", str(value or ""))

    first = clean(first_name).lower()
    last = clean(last_name).lower()
    email_name = clean(email_prefix).lower()
    stems = [
        f"{first}{last}",
        f"{first}_{last}",
        first,
        f"{last}{first}",
        email_name,
        f"{email_name}_{first}",
        last,
    ]
    stems = [
        stem.strip("_")[:12]
        for stem in stems
        if len(stem.strip("_")) >= 3
    ] or ["account"]

    candidates: list[str] = []
    seen: set[str] = set()
    target_count = max(1, int(count))
    while len(candidates) < target_count:
        stem = random.choice(stems)
        digits = str(random.randint(1000, 9999))
        stem_limit = 15 - len(digits)
        candidate = f"{stem[:stem_limit].rstrip('_')}{digits}"
        if len(candidate) < 4 or not _X_USERNAME_PATTERN.fullmatch(candidate):
            candidate = f"user{random.randint(10000, 9999999)}"
        if candidate in seen:
            continue
        seen.add(candidate)
        candidates.append(candidate)
    return candidates


def _x_com_username_control(state: dict) -> dict:
    for control in (state or {}).get("controls") or []:
        if not isinstance(control, dict):
            continue
        identity = " ".join(
            str(control.get(key) or "")
            for key in ("name", "id", "autocomplete", "placeholder", "ariaLabel")
        ).lower()
        if "username" in identity or "screen_name" in identity:
            return control
    return {}


def _x_com_state_error_text(state: dict) -> str:
    values = []
    values.extend(str(item) for item in (state or {}).get("errors") or [])
    values.extend(str(item) for item in (state or {}).get("validationErrors") or [])
    username = _x_com_username_control(state)
    if username.get("validationMessage"):
        values.append(str(username["validationMessage"]))
    return " | ".join(item.strip() for item in values if item and item.strip())


def _x_com_username_rejection(state: dict) -> str:
    """Return X's visible username rejection, if the current page has one."""
    username = _x_com_username_control(state)
    error_text = _x_com_state_error_text(state)
    username_invalid = bool(
        username
        and (
            not bool(username.get("valid", True))
            or str(username.get("ariaInvalid") or "").lower() == "true"
            or bool(username.get("validationMessage"))
        )
    )
    if username_invalid or _X_USERNAME_REJECTION_PATTERN.search(error_text):
        return error_text or "username field is invalid"
    return ""


def _x_com_handle_dob(page: Any) -> bool:
    selects = _safe_evaluate(
        page,
        r"""() => {
          const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
            && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none';
          const selects = [...document.querySelectorAll('select')].filter(visible);
          const birthSelects = selects.filter(el => {
            const text = [el.name, el.id, el.getAttribute('aria-label')].filter(Boolean).join(' ').toLowerCase();
            return /month|day|year|birth|dob|月|日|年/.test(text);
          });
          return birthSelects.map(el => {
            const text = [el.name, el.id, el.getAttribute('aria-label')].filter(Boolean).join(' ').toLowerCase();
            return {
              kind: /year|年/.test(text) ? 'year' : (/day|日/.test(text) ? 'day' : 'month'),
              label: String(el.getAttribute('aria-label') || el.name || el.id || '').slice(0, 120)
            };
          });
        }""",
    )
    if isinstance(selects, list) and len(selects) >= 3:
        values = {
            "month": random.randint(1, 12),
            "day": random.randint(1, 27),
            "year": random.randint(1988, 2002),
        }
        select_locators = page.locator("select")
        try:
            count = select_locators.count()
        except Exception:
            return False
        by_kind = {str(item.get("kind")): item for item in selects if isinstance(item, dict)}
        used = 0
        for index in range(min(count, 8)):
            locator = select_locators.nth(index)
            try:
                if not locator.is_visible(timeout=300):
                    continue
                label = str(locator.get_attribute("aria-label") or "")
                name = str(locator.get_attribute("name") or "")
                identity = f"{label} {name}".lower()
                kind = (
                    "year" if "year" in identity or "年" in identity
                    else "day" if "day" in identity or "日" in identity
                    else "month" if "month" in identity or "月" in identity
                    else ""
                )
                if kind not in values:
                    item = next(
                        (value for value in selects if isinstance(value, dict)
                         and str(value.get("kind")) == kind),
                        None,
                    )
                    if item is None:
                        continue
                _human_click(locator, timeout=1500)
                _human_select(locator, str(values[kind]))
                used += 1
            except Exception:
                continue
        if used >= 3:
            _human_pause(0.25, 0.5)
            return True

    inputs = _safe_evaluate(
        page,
        r"""() => {
          const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
            && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none';
          return [...document.querySelectorAll('input')].filter(visible).map(el => {
            const text = [el.name, el.id, el.placeholder, el.getAttribute('aria-label')]
              .filter(Boolean).join(' ').toLowerCase();
            return /month|月/.test(text) ? 'month'
              : (/day|日/.test(text) ? 'day' : (/year|年/.test(text) ? 'year' : ''));
          });
        }""",
    )
    kinds = [str(item) for item in (inputs or []) if item]
    if all(kind in kinds for kind in ("month", "day", "year")):
        values = {
            "month": str(random.randint(1, 12)),
            "day": str(random.randint(1, 27)),
            "year": str(random.randint(1988, 2002)),
        }
        locators = page.locator("input")
        try:
            count = locators.count()
        except Exception:
            return False
        used = 0
        for index in range(min(count, 20)):
            locator = locators.nth(index)
            try:
                if not locator.is_visible(timeout=300):
                    continue
                identity = " ".join(
                    str(locator.get_attribute(name) or "")
                    for name in ("name", "id", "placeholder", "aria-label")
                ).lower()
                kind = (
                    "month" if "month" in identity or "月" in identity
                    else "day" if "day" in identity or "日" in identity
                    else "year" if "year" in identity or "年" in identity
                    else ""
                )
                if not kind:
                    continue
                _human_fill(page, locator, values[kind])
                used += 1
            except Exception:
                continue
        if used >= 3:
            _human_pause(0.25, 0.5)
            return True
    return False


def _x_com_handle_onboarding_step(
    page: Any,
    password: str = "",
    username_candidates: Optional[list[str]] = None,
    display_name: str = "",
) -> bool:
    """Complete one non-credential X onboarding step."""
    if _x_com_handle_dob(page):
        _log("X.com 资料页已填写")
        return _x_com_click_submit(page)

    state = _x_com_page_state(page)
    controls = state.get("controls") or []
    buttons = state.get("buttons") or []
    control_names = {
        str(control.get("name") or control.get("id") or "").strip().lower()
        for control in controls
        if isinstance(control, dict)
    }

    if state.get("unifiedSignup") or {"name", "username", "password"} <= control_names:
        display_name = str(display_name or "").strip() or str(
            getattr(cfg, "CLOAK_REGISTRATION_NAME", "") or ""
        ).strip()
        if not display_name:
            display_name = (
                f"{random.choice(_X_HANDLE_PREFIXES)} "
                f"{random.choice(_X_HANDLE_SURNAMES)}"
            )
        username = (
            username_candidates.pop(0)
            if username_candidates
            else _x_com_username_candidates(
                first_name=display_name.split(" ", 1)[0],
                last_name=display_name.rsplit(" ", 1)[-1],
            )[0]
        )
        registration_password = str(password or _generate_password())
        fields = [
            (["input[name='name']", "input[autocomplete='name']"], display_name, "姓名"),
            (["input[name='username']", "input[name='screen_name']"], username, "用户名"),
            (
                [
                    "input[name='password'][type='password']",
                    "input[type='password']",
                    "input[autocomplete='new-password']",
                ],
                registration_password,
                "密码",
            ),
        ]
        for selectors, value, label in fields:
            locator = _visible_locator(page, selectors, timeout_ms=1500)
            if locator is None:
                _log(f"X.com unified signup 找不到{label}输入框")
                return False
            _human_fill_verified(page, locator, value)
        _log(
            f"X.com unified signup 已填写: username={username}, "
            f"password_length={len(registration_password)}"
        )
        return _x_com_click_submit(page)

    username_control = next(
        (
            control
            for control in controls
            if str(control.get("name") or control.get("id") or "").lower()
            in {"username", "screen_name", "user_name"}
        ),
        None,
    )
    if username_control is not None:
        locator = _visible_locator(
            page,
            [
                "input[name='username']",
                "input[name='screen_name']",
                "input[name='user_name']",
                "input[id*='username' i]",
            ],
            timeout_ms=1200,
        )
        if locator is not None:
            username = (
                username_candidates.pop(0)
                if username_candidates
                else _x_com_username_candidates(count=1)[0]
            )
            _human_fill_verified(page, locator, username)
            _log(f"X.com 用户名步骤已填写: {username}")
            return _x_com_click_submit(page)

    labels = [
        "Continue",
        "Next",
        "Finish",
        "Let's go",
        "Start",
        "Done",
        "Skip for now",
        "Not now",
        "I agree",
        "Allow",
        "继续",
        "下一步",
        "完成",
        "暂时跳过",
        "以后再说",
        "同意",
    ]
    visible_texts = {
        str(button.get("text") or "").strip().lower()
        for button in buttons
        if isinstance(button, dict)
    }
    matching_labels = [label for label in labels if label.lower() in visible_texts]
    for label in matching_labels:
        if _x_com_click_text_control(page, [label]):
            _log(f"X.com onboarding 已点击: {label}")
            return True

    return _x_com_click_submit(page)


def _x_com_step_from_state(info: dict) -> str:
    if info.get("postSignup") or info.get("registrationComplete"):
        return "post_signup"
    if info.get("hasFaceLiveness"):
        return "face_liveness"
    if info.get("hasOtp"):
        return "verification"
    if info.get("unifiedSignup"):
        return "unified_signup"
    if info.get("onboarding"):
        if info.get("emailInput"):
            return "email"
        return "onboarding"
    if info.get("emailInput") and info.get("passwordInput"):
        return "credentials"
    if info.get("emailInput"):
        return "email"
    if info.get("passwordInput"):
        return "password"
    if info.get("callbackOrHome"):
        return "home"
    if info.get("errors"):
        return "error"
    return "other"


def _x_com_wait_for_step(page: Any, timeout: Optional[float] = None) -> str:
    if timeout is None:
        timeout = max(30.0, _action_timeout_ms() / 1000.0)
    deadline = time.time() + max(0.2, timeout)
    while time.time() < deadline:
        _check_stop()
        step = _x_com_step_from_state(_x_com_page_state(page))
        if step != "other":
            return step
        time.sleep(0.4)
    return "other"


def _x_com_wait_after_submit(
    page: Any,
    previous_step: str,
    timeout: Optional[float] = None,
) -> str:
    """Wait for the submitted page to settle before classifying the result."""
    if timeout is None:
        timeout = max(30.0, _action_timeout_ms() / 1000.0)
    deadline = time.time() + max(5.0, timeout)
    unchanged_since = time.time()
    last_signature = None
    last_step = previous_step
    stable_seconds = min(20.0, max(10.0, float(timeout) / 3.0))

    while time.time() < deadline:
        _check_stop()
        state = _x_com_page_state(page)
        if state.get("hasFaceLiveness"):
            return "face_liveness"
        if state.get("hasOtp"):
            return "verification"

        current_step = _x_com_step_from_state(state)
        last_step = current_step
        signature = (
            current_step,
            str(state.get("url") or ""),
            tuple(str(item) for item in state.get("errors") or []),
        )
        if current_step != previous_step and current_step not in {"other", "error"}:
            return current_step

        now = time.time()
        if signature != last_signature:
            last_signature = signature
            unchanged_since = now
        elif (
            current_step == previous_step
            and now - unchanged_since >= stable_seconds
        ):
            return current_step
        time.sleep(0.4)
    return last_step if last_step in {
        previous_step,
        "credentials",
        "email",
        "verification",
        "password",
        "onboarding",
        "unified_signup",
        "post_signup",
        "home",
    } else "other"


def _x_com_require_no_face_liveness(page: Any) -> None:
    state = _x_com_page_state(page)
    if state.get("hasFaceLiveness"):
        raise _FaceLivenessRequired(
            "X.com 要求人脸活体验证; "
            f"{_x_com_state_summary(state)}; "
            f"当前页面: {_safe_url_for_log(_page_url(page)) or '-'}"
        )


def _x_com_prepare_signup_page(page: Any) -> None:
    try:
        page.goto(
            X_COM_START_URL,
            wait_until="domcontentloaded",
            timeout=_navigation_timeout_ms(),
        )
    except Exception as exc:
        raise RuntimeError(
            f"打开 X.com 失败: {exc}；当前页面: {_safe_url_for_log(_page_url(page)) or '-'}"
        ) from exc
    deadline = time.time() + max(20.0, _action_timeout_ms() / 1000.0)
    last_state: dict = {}
    while time.time() < deadline:
        _check_stop()
        state = _x_com_page_state(page)
        last_state = state
        if state.get("emailInput") or state.get("passwordInput") or state.get("hasOtp"):
            _maybe_accept_cookies(page)
            return
        _x_com_click_email_entry(page)
        time.sleep(0.5)
    _maybe_accept_cookies(page)
    raise RuntimeError(
        "打开 X.com 后未找到注册入口; "
        f"{_x_com_state_summary(last_state)}; "
        f"diagnostics={_x_com_submit_diagnostics(page)}; "
        f"当前页面: {_safe_url_for_log(_page_url(page)) or '-'}"
    )


def _run_sync(
    proxy: Optional[str],
    run_ctx: Optional[dict] = None,
    assigned_domain: Optional[str] = None,
    batch_id: Optional[int] = None,
    worker_index: Optional[int] = None,
) -> dict:
    """Create one X.com account, restarting when X requests face liveness."""
    max_attempts = max(1, int(getattr(cfg, "MAX_OTP_RETRIES", 3) or 3))
    last_error: Optional[BaseException] = None
    for attempt in range(1, max_attempts + 1):
        try:
            return _run_sync_once(
                proxy,
                run_ctx=run_ctx,
                assigned_domain=assigned_domain,
                batch_id=batch_id,
                worker_index=worker_index,
            )
        except _FaceLivenessRequired as exc:
            last_error = exc
            _log(
                f"X.com 触发人脸识别风控（第 {attempt}/{max_attempts} 次）: "
                "当前账号不保存，将重新分配邮箱并重走注册流程"
            )
            if run_ctx is not None:
                run_ctx["x_com_face_liveness_restarts"] = attempt
            if attempt < max_attempts:
                _human_pause(1.0, 2.0)
                continue
        except _EmailRejected as exc:
            last_error = exc
            _log(
                f"X.com 拒绝当前邮箱（第 {attempt}/{max_attempts} 次）: "
                "平台已收到提交，但该邮箱无法继续注册；将重新分配邮箱并重走注册流程"
            )
            if run_ctx is not None:
                run_ctx["x_com_email_rejections"] = attempt
            if attempt < max_attempts:
                _human_pause(1.0, 2.0)
                continue
        except Exception as exc:
            if not runtime.is_target_closed(exc):
                raise
            last_error = exc
            _log(
                f"X.com 页面/浏览器在注册过程中关闭（第 {attempt}/{max_attempts} 次）: "
                "将重新分配邮箱并重走注册流程"
            )
            if run_ctx is not None:
                run_ctx["x_com_browser_restarts"] = attempt
            if attempt < max_attempts:
                _human_pause(1.0, 2.0)
                continue
    raise RuntimeError(
        f"X.com 连续 {max_attempts} 次未完成注册（邮箱被拒、人脸识别或浏览器关闭），"
        f"已停止本次注册: {last_error}"
    ) from last_error


class _FaceLivenessRequired(RuntimeError):
    """X requires camera-based face verification for this registration."""


class _EmailRejected(RuntimeError):
    """X received the email submission but refused the email address."""


def _run_sync_once(
    proxy: Optional[str],
    run_ctx: Optional[dict] = None,
    assigned_domain: Optional[str] = None,
    batch_id: Optional[int] = None,
    worker_index: Optional[int] = None,
) -> dict:
    """Run one X.com browser registration attempt without retrying."""
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
    configured_display_name = str(
        getattr(cfg, "CLOAK_REGISTRATION_NAME", "") or ""
    ).strip()
    if configured_display_name:
        name_parts = configured_display_name.split()
        registration_first = name_parts[0]
        registration_last = (
            name_parts[-1]
            if len(name_parts) > 1
            else random.choice(_X_HANDLE_SURNAMES)
        )
        registration_display_name = configured_display_name
    else:
        registration_first = random.choice(_X_HANDLE_PREFIXES)
        registration_last = random.choice(_X_HANDLE_SURNAMES)
        registration_display_name = f"{registration_first} {registration_last}"
    username_candidates: list[str] = []
    processed_mails: set[str] = set()
    keep_browser_for_inspection = False
    try:
        _check_stop()
        _log("正在启动")
        configured_locale = str(getattr(cfg, "CLOAK_LOCALE", "") or "").strip()
        x_com_locale = str(getattr(cfg, "CLOAK_X_COM_LOCALE", "") or "").strip()
        geoip_enabled = bool(getattr(cfg, "CLOAK_GEOIP", True))
        playwright, browser, context, page = runtime.launch_browser(
            proxy,
            locale=configured_locale or x_com_locale or "en-US",
            timezone_name=str(getattr(cfg, "CLOAK_TIMEZONE", "") or "").strip(),
            geoip=geoip_enabled,
        )
        _log("Cloak已启动")
        _log("正在创建 X.com 账号")
        _x_com_prepare_signup_page(page)

        _x_com_find_email_input(page)
        email, email_jwt = get_email_and_token(
            proxies,
            assigned_domain=assigned_domain,
            batch_id=batch_id,
            worker_index=worker_index,
        )
        if not email:
            raise RuntimeError("邮箱服务未分配邮箱")
        username_candidates = _x_com_username_candidates(
            email_prefix=email.split("@", 1)[0],
            first_name=registration_first,
            last_name=registration_last,
            count=16,
        )
        set_last_email(email)
        _log(f"分配邮箱: {mask_email(email)}，密码长度: {len(password)}")

        step = _x_com_wait_for_step(page)
        _x_com_require_no_face_liveness(page)
        if step == "credentials":
            _x_com_fill_email(page, email)
            if not _x_com_fill_password(page, password):
                raise RuntimeError("X.com 密码输入框不可用")
            if not _x_com_click_submit(page):
                raise RuntimeError("X.com 注册提交按钮不可用")
            step = _x_com_wait_after_submit(page, previous_step="credentials")
            _x_com_require_no_face_liveness(page)
            _log(f"账号密码提交后的页面状态: {step}")
        else:
            _x_com_fill_email(page, email)
            url_before_submit = str(_x_com_page_state(page).get("url") or "")
            submit_ok = _x_com_click_submit(page)
            if not submit_ok:
                raise RuntimeError(
                    "X.com 邮箱提交按钮不可用; "
                    f"diagnostics={_x_com_submit_diagnostics(page)}"
                )
            step = _x_com_wait_after_submit(page, previous_step="email")
            _x_com_require_no_face_liveness(page)
            _log(f"邮箱提交后的页面状态: {step}")
            if step == "email":
                raise _EmailRejected(
                    "X.com 已收到邮箱提交，但平台拒绝了该邮箱; "
                    f"diagnostics={_x_com_submit_diagnostics(page)}; "
                    f"当前页面: {_safe_url_for_log(_page_url(page)) or '-'}"
                )

        if step not in {"verification", "password", "post_signup", "home"}:
            raise RuntimeError(
                f"X.com 注册未进入下一步，页面状态: {step}; "
                f"{_x_com_state_summary(_x_com_page_state(page))}; "
                f"diagnostics={_x_com_submit_diagnostics(page)}; "
                f"当前页面: {_safe_url_for_log(_page_url(page)) or '-'}"
            )

        max_attempts = max(1, int(getattr(cfg, "MAX_OTP_RETRIES", 3) or 3))
        for _attempt in range(1, max_attempts + 1):
            if step == "verification":
                code = get_oai_code(
                    email,
                    jwt=email_jwt,
                    proxies=proxies,
                    processed_mail_ids=processed_mails,
                    platform="x_com",
                )
                if not code:
                    raise RuntimeError("未收到 X.com 邮箱验证码")
                _log(f"收到 X.com 验证码: {mask_email(email)}")
                if not _x_com_fill_otp(page, code):
                    raise RuntimeError("X.com 验证码输入框不可用")
                otp_state = _x_com_page_state(page)
                if otp_state.get("hasOtp"):
                    if not _x_com_click_submit(page):
                        raise RuntimeError("X.com 验证码提交按钮不可用")
                    _log("X.com 验证码已提交，等待页面跳转")
                else:
                    _log("X.com 验证码已自动提交，跳过重复点击")
                step = _x_com_wait_after_submit(page, previous_step="verification")
                _x_com_require_no_face_liveness(page)
                _log(f"验证码提交后的页面状态: {step}")
            if step == "password":
                if not _x_com_fill_password(page, password):
                    raise RuntimeError("X.com 密码输入框不可用")
                if not _x_com_click_submit(page):
                    raise RuntimeError("X.com 密码提交按钮不可用")
                _log("X.com 密码已提交，等待页面跳转")
                step = _x_com_wait_after_submit(page, previous_step="password")
                _x_com_require_no_face_liveness(page)
                _log(f"密码提交后的页面状态: {step}")
            if step not in {"verification", "password"}:
                break

        onboarding_deadline = time.time() + max(
            30.0,
            float(getattr(cfg, "CLOAK_PROFILE_TIMEOUT", 60) or 60),
        )
        step = "onboarding"
        onboarding_steps = {"onboarding", "unified_signup"}
        unchanged_onboarding = 0
        last_onboarding_signature = None
        while time.time() < onboarding_deadline:
            step = _x_com_wait_for_step(page)
            _x_com_require_no_face_liveness(page)
            if step in {"post_signup", "home", "other"}:
                break
            if step not in onboarding_steps:
                break
            state = _x_com_page_state(page)
            signature = (
                str(state.get("url") or ""),
                tuple(
                    (
                        str(control.get("name") or control.get("id") or ""),
                        int(control.get("valueLength") or 0),
                        bool(control.get("valid", True)),
                    )
                    for control in state.get("controls") or []
                    if isinstance(control, dict)
                ),
                tuple(str(error) for error in state.get("errors") or []),
            )
            if signature == last_onboarding_signature:
                unchanged_onboarding += 1
            else:
                last_onboarding_signature = signature
                unchanged_onboarding = 1
            if unchanged_onboarding > 3:
                raise RuntimeError(
                    "X.com onboarding 连续多次未发生变化; "
                    f"{_x_com_state_summary(state)}; "
                    f"当前页面: {_safe_url_for_log(_page_url(page)) or '-'}"
                )

            previous_onboarding_step = step
            if not _x_com_handle_onboarding_step(
                page,
                password=password,
                username_candidates=username_candidates,
                display_name=registration_display_name,
            ):
                raise RuntimeError(
                    "X.com onboarding 步骤无法继续; "
                    f"{_x_com_state_summary(_x_com_page_state(page))}; "
                    f"当前页面: {_safe_url_for_log(_page_url(page)) or '-'}"
                )
            if previous_onboarding_step == "unified_signup":
                _log("X.com unified signup 已提交，等待账号创建结果")
            else:
                _log("X.com onboarding 步骤已提交，等待页面跳转")
            step = _x_com_wait_after_submit(page, previous_step=previous_onboarding_step)
            _x_com_require_no_face_liveness(page)
            _log(f"onboarding 提交后的页面状态: {step}")
            post_state = _x_com_page_state(page)
            username_rejection = _x_com_username_rejection(post_state)
            if username_rejection and step in onboarding_steps:
                _log(
                    "X.com username 被拒绝，将仅更换用户名重试: "
                )
                if not username_candidates:
                    raise RuntimeError(
                        "X.com 连续多个 username 候选均被拒绝; "
                        f"{_x_com_state_summary(post_state)}; "
                        f"当前页面: {_safe_url_for_log(_page_url(page)) or '-'}"
                    )
                unchanged_onboarding = 0
                last_onboarding_signature = None
                continue
            if step not in onboarding_steps:
                break

        _x_com_require_no_face_liveness(page)
        if step not in {"post_signup", "home"}:
            raise RuntimeError(
                "X.com 注册流程未到达完成页; "
                f"{_x_com_state_summary(_x_com_page_state(page))}; "
                f"当前页面: {_safe_url_for_log(_page_url(page)) or '-'}"
            )
        auth_token = str(_x_com_read_cookies(context).get("auth_token") or "").strip()
        run_ctx["x_com_registration_acknowledged"] = True
        _log(f"X.com 注册完成: {mask_email(email)}")
        return {
            "email": email,
            "password": password,
            "status": "x_com_registered",
            "confirmation": "post_signup" if step == "post_signup" else "home",
            "has_auth_token": bool(auth_token),
        }
    finally:
        if keep_browser_for_inspection:
            _log("X.com 需要人工检查，浏览器已保留")
        else:
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
        thread_name="cloakbrowser-x-com-register",
        proxy=proxy,
        run_ctx=run_ctx,
        assigned_domain=assigned_domain,
        batch_id=batch_id,
        worker_index=worker_index,
    )
