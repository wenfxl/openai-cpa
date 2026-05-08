import base64
import json
import os
import re
import threading
import urllib.parse
import uuid
from typing import Any

from fastapi import APIRouter, Request
from utils import config as cfg


router = APIRouter()
code_pool: dict[str, str] = {}
cache_lock = threading.Lock()


def _decode_b64url(value: str) -> bytes:
    value = value.strip()
    value += "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value.encode("ascii"))


def email_jwt(token: str) -> dict[str, Any]:
    """Parse a JWT payload without verifying the signature."""
    try:
        parts = str(token or "").split(".")
        if len(parts) < 2:
            return {}
        payload = json.loads(_decode_b64url(parts[1]).decode("utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def generate_payload(*, ctx: dict | None = None, **_: Any) -> str:
    """Local compatibility hook for request payload generation.

    The old native module generated a remote-service token here. This local
    replacement does not contact any authorization server and returns only an
    explicitly configured static token.
    """
    token = os.getenv("LOCAL_AUTH_SENTINEL_TOKEN", "").strip()
    if ctx is not None and token:
        ctx["local_auth_payload"] = True
    return token


def init_auth(*, session: Any, email: str = "", masked_email: str = "", proxies: Any = None,
              verify: bool = True, **_: Any) -> tuple[str, str]:
    did = ""
    try:
        did = session.cookies.get("oai-did") or session.cookies.get("oai-device-id") or ""
    except Exception:
        did = ""
    if not did:
        did = str(uuid.uuid4())
        try:
            session.cookies.set("oai-did", did, domain=".openai.com")
        except Exception:
            pass

    user_agent = ""
    try:
        user_agent = session.headers.get("user-agent") or session.headers.get("User-Agent") or ""
    except Exception:
        user_agent = ""
    if not user_agent:
        user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    return did, user_agent


def image2api_data(session: Any, target_continue_url: str = "", proxies: Any = None, **_: Any) -> str:
    return os.getenv("LOCAL_AUTH_IMAGE2API_DATA", "").strip()


def sys_node_allocate(session: Any, did: str = "", data: str = "", proxies: Any = None,
                      **_: Any) -> tuple[bool, str, str]:
    return True, "", ""


def sys_node_release(data: str = "", handle_a: str = "", handle_b: str = "", proxies: Any = None,
                     **_: Any) -> bool:
    return True


def sys_node_bulk_silent(proxies: Any = None, force_all: bool = False, **_: Any) -> bool:
    return True


def _extract_email(payload: dict[str, Any]) -> str:
    candidates = [
        payload.get("email"),
        payload.get("to"),
        payload.get("recipient"),
        payload.get("target"),
        payload.get("address"),
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and "@" in candidate:
            return candidate.lower().strip()
        if isinstance(candidate, list):
            for item in candidate:
                if isinstance(item, str) and "@" in item:
                    return item.lower().strip()
                if isinstance(item, dict):
                    nested = _extract_email(item)
                    if nested:
                        return nested
    for value in payload.values():
        if isinstance(value, dict):
            nested = _extract_email(value)
            if nested:
                return nested
    return ""


def _extract_text(payload: dict[str, Any]) -> str:
    parts = []
    for key in ("code", "text", "body", "html", "content", "message", "subject"):
        value = payload.get(key)
        if value is not None:
            parts.append(str(value))
    return "\n".join(parts)


def _configured_webhook_secrets() -> set[str]:
    secrets = {
        str(getattr(cfg, "OPENAI_CPA_WEBHOOK_SECRET", "") or "").strip(),
        str(getattr(cfg, "FREEMAIL_WEBHOOK_SECRET", "") or "").strip(),
        str(getattr(cfg, "CM_WEBHOOK_SECRET", "") or "").strip(),
    }
    return {secret for secret in secrets if secret}


def _provided_webhook_secret(request: Request, payload: dict[str, Any]) -> str:
    auth_header = request.headers.get("authorization", "").strip()
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    for key in ("x-webhook-secret", "x-email-webhook-secret", "x-auth-token"):
        value = request.headers.get(key, "").strip()
        if value:
            return value
    for key in ("secret", "webhook_secret", "token"):
        value = request.query_params.get(key) or payload.get(key)
        if value:
            return str(value).strip()
    return ""


@router.post("/api/email/webhook")
@router.post("/api/mail/webhook")
@router.post("/api/openai_cpa/webhook")
@router.post("/webhook")
async def receive_mail_webhook(request: Request):
    try:
        payload = await request.json()
    except Exception:
        raw_body = (await request.body()).decode("utf-8", errors="ignore")
        payload = dict(urllib.parse.parse_qsl(raw_body))

    if not isinstance(payload, dict):
        return {"status": "error", "message": "invalid payload"}

    configured_secrets = _configured_webhook_secrets()
    if configured_secrets:
        provided_secret = _provided_webhook_secret(request, payload)
        if provided_secret not in configured_secrets:
            return {"status": "error", "message": "invalid secret"}

    email = _extract_email(payload)
    text = _extract_text(payload)
    code_match = re.search(r"(?<!\d)(\d{6})(?!\d)", text)
    if code_match and not text.strip():
        text = code_match.group(1)

    if not email:
        return {"status": "error", "message": "missing email"}
    if not text:
        return {"status": "error", "message": "missing content"}

    with cache_lock:
        code_pool[email] = text
    return {"status": "success"}
