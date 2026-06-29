from fastapi import APIRouter
from . import system_routes
from . import account_routes
from . import service_routes
from . import sms_routes
from . import browser_monitor_routes
from . import cdk_routes
from utils.auth_core import router as email_router
from utils.auth_core import code_pool, cache_lock, generate_payload

router = APIRouter()

router.include_router(system_routes.router)
router.include_router(account_routes.router)
router.include_router(service_routes.router)
router.include_router(sms_routes.router)
router.include_router(browser_monitor_routes.router)
router.include_router(cdk_routes.router)
router.include_router(email_router)
