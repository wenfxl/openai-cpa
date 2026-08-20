# -*- coding: utf-8 -*-
"""Grok 注册入口：邮箱 -> Camoufox 注册 -> device-flow。"""
from __future__ import annotations

import json
import os
import time
from curl_cffi import requests
import re
from typing import Optional, Tuple

from utils import config as cfg
from utils.email_providers.mail_service import (
    get_email_and_token,
    get_oai_code,
    mask_email,
    set_last_email,
)

from .browser_signup import signup_with_camoufox
from .embedded_turnstile import ensure_camoufox
from .xai_oauth import (
    CLIPROXYAPI_GROK_BASE_URL,
    build_cliproxyapi_auth_record,
    complete_build_oauth,
)


def _ts() -> str:
    try:
        return cfg.ts()
    except Exception:
        return time.strftime("%H:%M:%S")


def _log(msg: str, email: str = "") -> None:
    text = str(msg or "").strip()
    if not text:
        return
    label = ""
    if email:
        try:
            label = mask_email(email)
        except Exception:
            label = str(email)
    if label and label not in text:
        text = f"{text}: {label}"
    print(f"[{_ts()}] [Grok] {text}")


def _log_success(msg: str, email: str = "") -> None:
    text = str(msg or "").strip()
    if not text:
        return
    label = ""
    if email:
        try:
            label = mask_email(email)
        except Exception:
            label = str(email)
    if label and label not in text:
        text = f"{text}: {label}"
    print(f"[{_ts()}] [SUCCESS] {text}")


def _format_proxy(proxy: Optional[str]) -> Optional[str]:
    if not proxy:
        return None
    try:
        proxy = cfg.format_docker_url(proxy)
    except:
        pass
    proxy = str(proxy).strip()
    if proxy.startswith("socks5://"):
        proxy = proxy.replace("socks5://", "socks5h://", 1)
    return proxy or None


def _generate_password() -> str:
    return f"Pw{os.urandom(6).hex()}!a#A"


def _get_proxy_env(proxy: Optional[str]) -> str:
    if proxy:
        return proxy
    return (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("https_proxy")
        or os.environ.get("http_proxy")
        or ""
    )


def _short_err(text: str) -> str:
    short = str(text or "").split(" url=")[0].split(" at ")[0].strip()
    if len(short) > 120:
        short = short[:117] + "..."
    return short


def _import_grok_web_before_oauth(sso: str, email: str, run_ctx: dict) -> None:
    """Best-effort Grok Web import; it must never block Build OAuth."""
    if not getattr(cfg, "GROK2API_IMPORT_SSO_AS_GROK_WEB", False):
        return

    # Import lazily to avoid a module cycle while core_engine dispatches Grok registration.
    from utils import core_engine

    ok_login, grok_token, login_msg = core_engine.grok2api_admin_login()
    if not ok_login:
        run_ctx["grok_web_import_ok"] = False
        run_ctx["grok_web_import_message"] = login_msg
        _log(f"Grok Web SSO 导入跳过: {_short_err(login_msg)}；继续获取 Build OAuth", email)
        return

    web_ok, web_msg = core_engine._grok2api_import_web_sso(sso, grok_token)
    run_ctx["grok_web_import_ok"] = web_ok
    run_ctx["grok_web_import_message"] = web_msg
    if web_ok:
        _log_success("Grok Web SSO 已先行导入", email)
    else:
        _log(f"{_short_err(web_msg)}；继续获取 Build OAuth", email)


def run(
    proxy: Optional[str] = None,
    run_ctx: Optional[dict] = None,
    assigned_domain: Optional[str] = None,
    batch_id: Optional[int] = None,
    worker_index: Optional[int] = None,
    sso_only: Optional[bool] = None,
) -> Tuple[Optional[str], Optional[str]]:
    if run_ctx is None:
        run_ctx = {}

    ok_c, msg_c = ensure_camoufox(force=False)
    if not ok_c:
        _log(msg_c or "Camoufox 未就绪")
        return None, None

    proxy = _format_proxy(proxy)
    proxies = {"http": proxy, "https": proxy} if proxy else None

    email = ""
    password = ""
    # old_http = os.environ.get("HTTP_PROXY")
    # old_https = os.environ.get("HTTPS_PROXY")

    try:
        email, email_jwt = get_email_and_token(
            proxies,
            assigned_domain=assigned_domain,
            batch_id=batch_id,
            worker_index=worker_index,
        )
        if not email:
            _log("获取邮箱失败")
            return None, None
        set_last_email(email)
        password = _generate_password()
        _log("邮箱就绪", email)

        # if proxy:
        #     os.environ["HTTP_PROXY"] = proxy
        #     os.environ["HTTPS_PROXY"] = proxy

        def _fetch_code() -> str:
            return str(
                get_oai_code(
                    email,
                    jwt=email_jwt or "",
                    proxies=proxies,
                )
                or ""
            ).strip()

        def _blog(msg: str) -> None:
            _log(msg, email)

        headless_raw = str(os.environ.get("GROK_BROWSER_SIGNUP_HEADLESS", "1") or "1").strip().lower()
        browser_headless = headless_raw not in {"0", "false", "no", "off"}
        browser_res = signup_with_camoufox(
            email,
            password,
            fetch_code=_fetch_code,
            proxy=proxy or "",
            headless=browser_headless,
            given_name="Alex",
            family_name="Chen",
            timeout=float(getattr(cfg, "GROK_OAUTH_TIMEOUT", 180.0) or 180.0),
            log=_blog,
        )

        if not (isinstance(browser_res, dict) and browser_res.get("ok") and browser_res.get("sso")):
            err = ""
            if isinstance(browser_res, dict):
                err = str(browser_res.get("error") or "")
            err_text = err or "未知错误"
            low = err_text.lower()
            if ("cf人机" in low) or ("turnstile" in low) or ("captcha" in low):
                _log("CF人机校验失败（常见原因: 代理不通/过慢、目标打开失败）", email)
            elif ("sso" in low) and ("fail" in low or "提取失败" in err_text or "missing" in low):
                _log("SSO 提取失败", email)
            else:
                _log(f"注册失败: {_short_err(err_text)}", email)
            return None, None

        sso = str(browser_res.get("sso") or "")
        session_cookies = dict(browser_res.get("cookies") or {})
        session_cookies.setdefault("sso", sso)
        session_cookies.setdefault("sso-rw", sso)
        _log("SSO 提取成功", email)

        # 判断是否启用 SSO-only 模式（参数优先，其次读取配置）
        is_sso_only = sso_only if sso_only is not None else bool(getattr(cfg, "GROK2API_SSO_ONLY_MODE", False))

        # 风控检测（由 DISCARD_ON_DOWNGRADE 控制，两种模式都保留）
        discard_on_downgrade = getattr(cfg, "DISCARD_ON_DOWNGRADE", False)
        if discard_on_downgrade:
            bot_flag_dict = inspect_sso_account_state(session_cookies, proxy=proxy or "")
            if bot_flag_dict["found"]:
                bfs = bot_flag_dict.get('bot_flag_source')
                iq_status = "账号智商正常" if bfs == 0 else f"账号已降智({bfs}) 可能需更换IP"
                is_denied = bot_flag_dict.get('denied')
                deny_status = "被拒(死号)" if is_denied else "通过"
                risk_val = bot_flag_dict.get('risk')
                risk_display = risk_val if risk_val is not None else "无"
                _log(f"状态: {iq_status}，注册: {deny_status}，风险值: {risk_display}", email)
                if is_denied or bfs != 0:
                    _log("⚠️ 触发风控拒绝或[降智丢弃]规则，账号已作废不入库，中止流程", email)
                    run_ctx["discarded"] = True
                    return None, None
            else:
                _log(f"账号状态检测失败: {bot_flag_dict['error']}", email)

        _import_grok_web_before_oauth(sso, email, run_ctx)

        if is_sso_only:
            # SSO-only 模式：跳过 OAuth device flow，直接返回简化 JSON
            _log("SSO-only 模式已开启，跳过 Build OAuth", email)
            sso_record = {
                "email": email,
                "password": password,
                "sso": sso,
                "status": "grok_sso",
                "provider": "grok",
            }
            sso_json_str = json.dumps(sso_record, ensure_ascii=False)
            _log_success("SSO-only 模式完成", email)
            return sso_json_str, password

        try:
            oauth = complete_build_oauth(
                email,
                password,
                proxy=_get_proxy_env(proxy),
                session_cookies=session_cookies,
            )
        except Exception as oauth_exc:
            detail = str(oauth_exc).strip() or repr(oauth_exc)
            if "Access denied" in detail or "invalid_grant" in detail:
                _log("OAuth失败: 账号被拒绝发 token", email)
            else:
                _log(f"OAuth失败: {_short_err(detail)}", email)
            return None, None

        token = getattr(oauth, "token", None) or {}
        userinfo = getattr(oauth, "userinfo", None) or {}
        if not token.get("access_token"):
            _log("OAuth失败: 未拿到 access_token", email)
            return None, None

        record = build_cliproxyapi_auth_record(
            token,
            userinfo=userinfo,
            base_url=CLIPROXYAPI_GROK_BASE_URL,
        )
        if not record.get("email"):
            record["email"] = email
        record["password"] = password
        if sso:
            record["sso"] = sso
        record["status"] = "grok_oauth"
        record["provider"] = "grok"

        token_json_str = json.dumps(record, ensure_ascii=False)
        _log_success("注册完成", email)
        return token_json_str, password

    except Exception as exc:
        detail = str(exc).strip() or repr(exc)
        low = detail.lower()
        if "geoip" in low:
            _log("Camoufox geoip 依赖问题（当前已不强制 geoip）", email)
        elif ("turnstile" in low) or ("人机" in detail):
            _log("CF人机校验失败（常见原因: 代理不通/过慢、目标打开失败）", email)
        else:
            _log(f"异常: {_short_err(detail)}", email)
        if email:
            set_last_email(email)
        return None, None
    finally:
        pass
        # if proxy:
        #     if old_http is None:
        #         os.environ.pop("HTTP_PROXY", None)
        #     else:
        #         os.environ["HTTP_PROXY"] = old_http
        #     if old_https is None:
        #         os.environ.pop("HTTPS_PROXY", None)
        #     else:
        #         os.environ["HTTPS_PROXY"] = old_https


def _parse_grok_account_state(html_text: str) -> dict:
    raw = str(html_text or "")

    result = {
        "found": False,
        "bot_flag_source": None,
        "bot_flag_details": "",
        "policy": "",
        "risk": None,
        "event": "",
        "denied": False,
        "error": ""
    }

    if "Just a moment" in raw or "cf-browser-verification" in raw or "cf-turnstile" in raw:
        result["error"] = "被 CF 拦截"
        return result
    elif "Sign in to xAI" in raw or "sign in" in raw.lower():
        result["error"] = "SSO 无效"
        return result

    normalized = raw.replace('\\"', '"')
    source_match = re.search(r'botFlagSource"\s*:\s*(null|-?\d+|"[^"]*")', normalized)
    details_match = re.search(r'botFlagDetails"\s*:\s*(?:null|"([^"]*)")', normalized)

    if source_match and source_match.group(1) != "null":
        val = source_match.group(1).strip('"')
        try:
            result["bot_flag_source"] = int(val)
        except ValueError:
            result["bot_flag_source"] = val

    details_str = details_match.group(1) if details_match and details_match.group(1) else ""
    result["bot_flag_details"] = details_str

    detail_fields: dict[str, str] = {}
    for item in details_str.split(","):
        key, sep, value = item.partition("=")
        if sep and key.strip():
            detail_fields[key.strip().lower()] = value.strip()

    try:
        if detail_fields.get("risk"):
            result["risk"] = float(detail_fields["risk"])
    except (TypeError, ValueError):
        pass

    result["policy"] = detail_fields.get("policy", "").lower()
    result["event"] = detail_fields.get("event", "")
    result["denied"] = (result["policy"] == "deny" and result["event"] == "$registration")

    result["found"] = bool(source_match or details_match)
    if not result["found"]:
        result["error"] = "未找到 botFlag 字段"

    return result


def inspect_sso_account_state(session_cookies: dict, proxy: str = "") -> dict:
    url = "https://grok.com/"
    GROK_INSPECT_PROXY = str(getattr(cfg, 'GROK_INSPECT_PROXY', '')).strip()
    use_proxy = GROK_INSPECT_PROXY or proxy
    proxies = {"http": use_proxy, "https": use_proxy} if use_proxy else None

    final_result = {
        "status_code": 0,
        "found": False,
        "bot_flag_source": None,
        "bot_flag_details": "",
        "policy": "",
        "risk": None,
        "event": "",
        "denied": False,
        "error": ""
    }

    try:
        response = requests.get(
            url,
            cookies=session_cookies,
            proxies=proxies,
            impersonate="chrome",
            timeout=15
        )

        final_result["status_code"] = response.status_code
        if response.status_code >= 400:
            suffix = " 代理被CF物理级拉黑，请更换IP" if response.status_code in (403, 429, 503) else ""
            final_result["error"] = f"请求失败 (HTTP {response.status_code}){suffix}"
            return final_result
        parsed_data = _parse_grok_account_state(response.text)
        final_result.update(parsed_data)
        return final_result

    except Exception as e:
        final_result["error"] = f"请求异常 ({str(e)})"
        return final_result