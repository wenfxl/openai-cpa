import json
import re
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any, Dict, Tuple

from curl_cffi import requests

from .constants import AUTH_URL, TOKEN_URL, CLIENT_ID, DEFAULT_REDIRECT_URI, DEFAULT_SCOPE
from .common import _random_state, _pkce_verifier, _sha256_b64url_no_pad, _parse_callback_url, _jwt_claims_no_verify
from .http_utils import _post_form, _to_int, _ssl_verify


@dataclass(frozen=True)
class OAuthStart:
    auth_url: str
    state: str
    code_verifier: str
    redirect_uri: str


AUTHORIZE_CONTINUE_SUCCESS_STATUSES = frozenset((200, 204))


def generate_oauth_url(
        *,
        redirect_uri: str = DEFAULT_REDIRECT_URI,
        scope: str = DEFAULT_SCOPE,
) -> OAuthStart:
    state = _random_state()
    code_verifier = _pkce_verifier()
    code_challenge = _sha256_b64url_no_pad(code_verifier)
    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "prompt": "login",
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
    }
    return OAuthStart(
        auth_url=f"{AUTH_URL}?{urllib.parse.urlencode(params)}",
        state=state,
        code_verifier=code_verifier,
        redirect_uri=redirect_uri,
    )


def ensure_oai_context_url(
        auth_url: str,
        *,
        device_id: str,
        auth_session_logging_id: str,
        screen_hint: str = "login_or_signup",
) -> str:
    """Preserve OAuth query parameters while adding the Auth session context."""
    try:
        parsed = urllib.parse.urlsplit(auth_url)
        params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        additions = {
            "prompt": "login",
            "ext-oai-did": device_id,
            "auth_session_logging_id": auth_session_logging_id,
            "screen_hint": screen_hint,
        }
        changed = False
        for key, value in additions.items():
            if value and not params.get(key):
                params[key] = [value]
                changed = True
        if not changed:
            return auth_url
        query = urllib.parse.urlencode(params, doseq=True)
        return urllib.parse.urlunsplit((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            query,
            parsed.fragment,
        ))
    except Exception:
        return auth_url


def response_json_object(response: Any) -> Dict[str, Any]:
    try:
        payload = response.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def extract_oauth_next_url(payload: Any) -> str:
    """Extract the next Auth URL from flat or page-nested response fields."""
    if not isinstance(payload, dict):
        return ""
    page = payload.get("page")
    page = page if isinstance(page, dict) else {}
    containers = (payload, page)
    keys = ("continue_url", "external_url", "redirect_url", "url")
    for container in containers:
        for key in keys:
            value = str(container.get(key) or "").strip()
            if value:
                return value
    return ""


def _redact_oauth_url(url: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(url)
        blocked = {
            "code",
            "state",
            "token",
            "access_token",
            "refresh_token",
            "id_token",
            "auth_session_logging_id",
            "session_id",
        }
        pairs = []
        for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True):
            pairs.append((key, "***" if key.lower() in blocked else value))
        return urllib.parse.urlunsplit((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urllib.parse.urlencode(pairs),
            parsed.fragment,
        ))
    except Exception:
        return "<url>"


def summarize_oauth_failure(response: Any, max_text: int = 240) -> str:
    """Build a compact, sanitized diagnostic for a rejected Auth request."""
    status = int(getattr(response, "status_code", 0) or 0)
    payload = response_json_object(response)
    page = payload.get("page")
    page = page if isinstance(page, dict) else {}
    error = payload.get("error")
    error = error if isinstance(error, dict) else {}

    page_type = str(page.get("type") or payload.get("page_type") or "").strip()
    error_code = str(
        error.get("code")
        or payload.get("error_code")
        or payload.get("code")
        or ""
    ).strip()
    error_message = str(
        error.get("message")
        or payload.get("message")
        or payload.get("detail")
        or ""
    ).strip()
    next_url = extract_oauth_next_url(payload)

    parts = [f"status={status}"]
    if page_type:
        parts.append(f"page={page_type}")
    if error_code:
        parts.append(f"error={error_code}")
    if error_message:
        error_message = re.sub(
            r"(?i)(code|state|token|session_id)(\s*[=:]\s*)([^&\s,;]+)",
            r"\1\2***",
            error_message[:max_text],
        )
        parts.append(f"message={error_message}")
    if next_url:
        parts.append(f"next={_redact_oauth_url(next_url)}")

    if len(parts) == 1:
        try:
            text = str(getattr(response, "text", "") or "")[:max_text]
        except Exception:
            text = ""
        if text:
            text = re.sub(
                r"(?i)(code|state|token|session_id)(\s*[=:]\s*)([^&\s,;]+)",
                r"\1\2***",
                text,
            )
            parts.append(f"body={text}")
    return " | ".join(parts)


def submit_callback_url(
        *,
        callback_url: str,
        expected_state: str,
        code_verifier: str,
        redirect_uri: str = DEFAULT_REDIRECT_URI,
        proxies: Any = None,
) -> str:
    cb = _parse_callback_url(callback_url)
    if cb["error"]:
        raise RuntimeError(f"oauth error: {cb['error']}: {cb['error_description']}".strip())
    if not cb["code"]:
        raise ValueError("callback url missing ?code=")
    if not cb["state"]:
        raise ValueError("callback url missing ?state=")
    if cb["state"] != expected_state:
        raise ValueError("state mismatch")

    token_resp = _post_form(
        TOKEN_URL,
        {
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "code": cb["code"],
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        },
        proxies=proxies,
    )

    access_token = (token_resp.get("access_token") or "").strip()
    refresh_token = (token_resp.get("refresh_token") or "").strip()
    id_token = (token_resp.get("id_token") or "").strip()
    expires_in = _to_int(token_resp.get("expires_in"))

    claims = _jwt_claims_no_verify(id_token)
    email = str(claims.get("email") or "").strip()
    auth_claims = claims.get("https://api.openai.com/auth") or {}
    account_id = str(auth_claims.get("chatgpt_account_id") or "").strip()

    now = int(time.time())
    now_rfc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    expired_rfc = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                time.gmtime(now + max(expires_in, 0)))

    config_obj = {
        "id_token": id_token,
        "client_id": CLIENT_ID,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "account_id": account_id,
        "last_refresh": now_rfc,
        "email": email,
        "type": "codex",
        "expired": expired_rfc,
    }
    return json.dumps(config_obj, ensure_ascii=False, separators=(",", ":"))


def refresh_oauth_token(refresh_token: str, proxies: Any = None) -> Tuple[bool, dict]:
    if not refresh_token:
        return False, {"error": "无 refresh_token"}
    try:
        resp = requests.post(
            TOKEN_URL,
            data={
                "client_id": CLIENT_ID,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "redirect_uri": DEFAULT_REDIRECT_URI,
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            proxies=proxies,
            verify=_ssl_verify(),
            timeout=30,
            impersonate="chrome110",
        )
        if resp.status_code == 200:
            data = resp.json()
            now = int(time.time())
            expires_in = _to_int(data.get("expires_in", 3600))
            return True, {
                "access_token": data.get("access_token"),
                "refresh_token": data.get("refresh_token", refresh_token),
                "id_token": data.get("id_token"),
                "last_refresh": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
                "expired": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                         time.gmtime(now + max(expires_in, 0))),
            }
        return False, {"error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return False, {"error": str(e)}
