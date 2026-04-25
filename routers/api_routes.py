import re

from fastapi import APIRouter, Header, HTTPException, Request
from . import system_routes
from . import account_routes
from . import service_routes
from . import sms_routes
from utils.auth_core import router as email_router
from utils.auth_core import code_pool, cache_lock, generate_payload
import utils.config as cfg

router = APIRouter()

router.include_router(system_routes.router)
router.include_router(account_routes.router)
router.include_router(service_routes.router)
router.include_router(sms_routes.router)

# Keep legacy local-webhook readers and the OpenAI-CPA memory-pool reader on the
# same backing pool. Older mail_service branches import this from
# routers.system_routes, while OpenAI-CPA imports it from utils.auth_core.
system_routes.code_pool = code_pool


def _normalize_webhook_email(value: str) -> str:
    value = str(value or "").strip().lower()
    match = re.search(r"[\w.!#$%&'*+/=?^`{|}~-]+@[\w.-]+\.[a-z]{2,}", value)
    return match.group(0) if match else value


def _extract_email_from_raw(raw_content: str) -> str:
    text = str(raw_content or "")
    for header in ("delivered-to", "x-original-to", "to"):
        match = re.search(rf"(?im)^{re.escape(header)}:\s*(.+)$", text)
        if match:
            email = _normalize_webhook_email(match.group(1))
            if email and "@" in email:
                return email
    return ""


def _valid_webhook_secrets() -> set[str]:
    secrets = set()

    openai_cpa_secret = str(getattr(cfg, "OPENAI_CPA_WEBHOOK_SECRET", "") or "").strip()
    if openai_cpa_secret:
        secrets.add(openai_cpa_secret)

    if getattr(cfg, "FREEMAIL_LOCAL_WEBHOOK", False):
        freemail_secret = str(getattr(cfg, "FREEMAIL_WEBHOOK_SECRET", "") or "").strip()
        if freemail_secret:
            secrets.add(freemail_secret)

    if getattr(cfg, "CM_LOCAL_WEBHOOK", False):
        cloudmail_secret = str(getattr(cfg, "CM_WEBHOOK_SECRET", "") or "").strip()
        if cloudmail_secret:
            secrets.add(cloudmail_secret)

    return secrets


@router.post("/api/webhook/email")
async def receive_email_webhook(
    request: Request,
    x_webhook_secret: str = Header(default="", alias="X-Webhook-Secret"),
):
    expected_secrets = _valid_webhook_secrets()
    if not expected_secrets:
        raise HTTPException(status_code=403, detail="Local webhook feature is disabled")

    if str(x_webhook_secret or "").strip() not in expected_secrets:
        raise HTTPException(status_code=401, detail="Invalid webhook secret")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    raw_content = str(payload.get("raw_content") or "")
    target_email = _normalize_webhook_email(
        payload.get("to_addr")
        or payload.get("email")
        or payload.get("to")
        or payload.get("recipient")
        or ""
    )
    if not target_email or "@" not in target_email:
        target_email = _extract_email_from_raw(raw_content)

    if not target_email or not raw_content:
        raise HTTPException(status_code=400, detail="Missing core email data")

    async with cache_lock:
        code_pool[target_email] = raw_content

    return {"status": "ok", "email": target_email}

router.include_router(email_router)
