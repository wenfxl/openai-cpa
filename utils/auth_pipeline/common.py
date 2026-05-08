import base64
import hashlib
import json
import secrets
import time
import urllib.parse
from typing import Any, Dict, Optional, Tuple

from curl_cffi import requests
from utils import config as cfg
from utils.email_providers.mail_service import get_oai_code, mask_email
from utils.local_auth_core import generate_payload

from .http_utils import _post_with_retry, _oai_headers
from .user_utils import generate_random_user_info

def _b64url_no_pad(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _sha256_b64url_no_pad(s: str) -> str:
    return _b64url_no_pad(hashlib.sha256(s.encode("ascii")).digest())


def _random_state(nbytes: int = 16) -> str:
    return secrets.token_urlsafe(nbytes)


def _pkce_verifier() -> str:
    return secrets.token_urlsafe(64)

def _parse_callback_url(callback_url: str) -> Dict[str, Any]:
    candidate = callback_url.strip()
    if not candidate:
        return {"code": "", "state": "", "error": "", "error_description": ""}
    if "://" not in candidate:
        if candidate.startswith("?"):
            candidate = f"http://localhost{candidate}"
        elif any(ch in candidate for ch in "/?#") or ":" in candidate:
            candidate = f"http://{candidate}"
        elif "=" in candidate:
            candidate = f"http://localhost/?{candidate}"
    parsed = urllib.parse.urlparse(candidate)
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    fragment = urllib.parse.parse_qs(parsed.fragment, keep_blank_values=True)
    for key, values in fragment.items():
        if key not in query or not query[key] or not (query[key][0] or "").strip():
            query[key] = values

    def get1(k: str) -> str:
        v = query.get(k, [""])
        return (v[0] or "").strip()

    code = get1("code")
    state = get1("state")
    error = get1("error")
    error_description = get1("error_description")
    if code and not state and "#" in code:
        code, state = code.split("#", 1)
    if not error and error_description:
        error, error_description = error_description, ""
    return {"code": code, "state": state, "error": error,
            "error_description": error_description}


def _jwt_claims_no_verify(id_token: str) -> Dict[str, Any]:
    if not id_token or id_token.count(".") < 2:
        return {}
    payload_b64 = id_token.split(".")[1]
    pad = "=" * ((4 - (len(payload_b64) % 4)) % 4)
    try:
        return json.loads(
            base64.urlsafe_b64decode((payload_b64 + pad).encode("ascii")).decode("utf-8")
        )
    except Exception:
        return {}


def _decode_jwt_segment(seg: str) -> Dict[str, Any]:
    raw = (seg or "").strip()
    if not raw:
        return {}
    pad = "=" * ((4 - (len(raw) % 4)) % 4)
    try:
        return json.loads(
            base64.urlsafe_b64decode((raw + pad).encode("ascii")).decode("utf-8")
        )
    except Exception:
        return {}


def _extract_next_url(data: Dict[str, Any]) -> str:
    continue_url = str(data.get("continue_url") or "").strip()
    if continue_url:
        return continue_url
    page_type = str((data.get("page") or {}).get("type") or "").strip()
    mapping = {
        "email_otp_verification": "https://auth.openai.com/email-verification",
        "sign_in_with_chatgpt_codex_consent": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
        "workspace": "https://auth.openai.com/workspace",
        "add_phone": "https://auth.openai.com/add-phone",
        "phone_verification": "https://auth.openai.com/add-phone",
        "phone_otp_verification": "https://auth.openai.com/add-phone",
        "phone_number_verification": "https://auth.openai.com/add-phone",
    }
    return mapping.get(page_type, "")


def _parse_workspace_from_auth_cookie(auth_cookie: str) -> list:
    if not auth_cookie or "." not in auth_cookie:
        return []
    parts = auth_cookie.split(".")
    if len(parts) >= 2:
        claims = _decode_jwt_segment(parts[1])
        workspaces = claims.get("workspaces") or []
        if workspaces:
            return workspaces
    claims = _decode_jwt_segment(parts[0])
    return claims.get("workspaces") or []

def _otp_verify_loop(
        *,
        session: requests.Session,
        email: str,
        email_jwt: str,
        did: str,
        current_ua: str,
        proxy: str,
        proxies,
        ctx: dict,
        processed_mails: set,
        referer: str,
        resend_url: str,
        validate_url: str,
        flow: str = "authorize_continue",
        first_send_url: str = None,
        first_send_referer: str = None,
        first_send_json_body=None,
) -> Tuple[str, Any]:
    code_resp = None

    if first_send_url:
        try:
            sentinel_send = generate_payload(did=did, flow=flow, proxy=proxy, user_agent=current_ua,
                                             impersonate="chrome110", ctx=ctx)
            send_headers = _oai_headers(did, {
                "Referer": first_send_referer or referer,
                "content-type": "application/json",
            })
            if sentinel_send:
                send_headers["openai-sentinel-token"] = sentinel_send
            _post_with_retry(
                session,
                first_send_url,
                headers=send_headers,
                json_body=first_send_json_body if first_send_json_body is not None else {},
                proxies=proxies, timeout=30,
            )
        except Exception as e:
            print(f"[{cfg.ts()}] [WARNING] （{mask_email(email)}）OTP 初始发送请求异常: {e}")

    code = ""
    for resend_attempt in range(max(1, cfg.MAX_OTP_RETRIES)):
        if getattr(cfg, 'GLOBAL_STOP', False):
            return "", None
        if resend_attempt > 0:
            try:
                sentinel_resend = generate_payload(did=did, flow=flow, proxy=proxy,
                                                   user_agent=current_ua, impersonate="chrome110", ctx=ctx)
                resend_headers = _oai_headers(did, {
                    "Referer": referer,
                    "content-type": "application/json"
                })
                if sentinel_resend:
                    resend_headers["openai-sentinel-token"] = sentinel_resend
                _post_with_retry(
                    session,
                    resend_url,
                    headers=resend_headers,
                    json_body={}, proxies=proxies, timeout=15,
                )
                time.sleep(2)
            except Exception as e:
                print(f"[{cfg.ts()}] [WARNING] （{mask_email(email)}）重新发送请求异常: {e}")

        code = get_oai_code(email, jwt=email_jwt, proxies=proxies,
                            processed_mail_ids=processed_mails)
        if not code:
            continue

        sentinel_otp = generate_payload(did=did, flow=flow, proxy=proxy, user_agent=current_ua,
                                        impersonate="chrome110", ctx=ctx)
        val_headers = _oai_headers(did, {
            "Referer": referer,
            "content-type": "application/json",
        })
        if sentinel_otp:
            val_headers["openai-sentinel-token"] = sentinel_otp

        code_resp = _post_with_retry(
            session,
            validate_url,
            headers=val_headers,
            json_body={"code": code}, proxies=proxies,
        )

        if code_resp.status_code == 200:
            return code, code_resp
        else:
            code = ""
            continue

    return "", code_resp

def _create_account_about_you(
        *,
        session: requests.Session,
        email: str,
        did: str,
        current_ua: str,
        proxy: str,
        proxies,
        ctx: dict,
) -> Tuple[dict, Any]:
    user_info = generate_random_user_info()
    print(f"[{cfg.ts()}] [INFO] （{mask_email(email)}）初始化账户信息 "
          f"(昵称: {user_info['name']}, 生日: {user_info['birthdate']})...")

    sentinel_create = generate_payload(did=did, flow="create_account", proxy=proxy,
                                       user_agent=current_ua, impersonate="chrome110", ctx=ctx)
    create_headers = _oai_headers(did, {
        "Referer": "https://auth.openai.com/about-you",
        "content-type": "application/json",
    })
    if sentinel_create:
        create_headers["openai-sentinel-token"] = sentinel_create

    create_account_resp = _post_with_retry(
        session,
        "https://auth.openai.com/api/accounts/create_account",
        headers=create_headers,
        json_body=user_info, proxies=proxies,
    )
    return user_info, create_account_resp
