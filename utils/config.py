import os
import queue
import threading
import yaml
import random
import string
from datetime import datetime

from utils.proxy_manager import reload_proxy_config


def ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def format_docker_url(url: str) -> str:
    if not url or not isinstance(url, str):
        return url
    if os.path.exists("/.dockerenv"):
        url = url.replace("127.0.0.1", "host.docker.internal")
        url = url.replace("localhost", "host.docker.internal")
    return url


def init_config():
    config_path = "config.yaml"
    if not os.path.exists(config_path):
        print(f"[{ts()}] [ERROR] 配置文件 {config_path} 不存在，请检查！")
        exit(1)
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

_c: dict = {}
ENABLE_SUB_DOMAINS: bool = False
SUB_DOMAIN_COUNT: int = 10
EMAIL_API_MODE: str = ""
MAIL_DOMAINS: str = ""
GPTMAIL_BASE: str = ""
ADMIN_AUTH: str = ""

IMAP_SERVER: str = ""
IMAP_PORT: int = 993
IMAP_USER: str = ""
IMAP_PASS: str = ""

FREEMAIL_API_URL: str = ""
FREEMAIL_API_TOKEN: str = ""

CM_API_URL: str = ""
CM_ADMIN_EMAIL: str = ""
CM_ADMIN_PASS: str = ""

MC_API_BASE: str = ""
MC_KEY: str = ""

DEFAULT_PROXY: str = ""

ENABLE_MULTI_THREAD_REG: bool = False
REG_THREADS: int = 3
MAX_OTP_RETRIES: int = 5
USE_PROXY_FOR_EMAIL: bool = False
ENABLE_EMAIL_MASKING: bool = True

LOGIN_DELAY_MIN: int = 20
LOGIN_DELAY_MAX: int = 45

ENABLE_CPA_MODE: bool = False
SAVE_TO_LOCAL_IN_CPA_MODE: bool = True
CPA_API_URL: str = ""
CPA_API_TOKEN: str = ""
MIN_ACCOUNTS_THRESHOLD: int = 30
BATCH_REG_COUNT: int = 1
MIN_REMAINING_WEEKLY_PERCENT: int = 80
REMOVE_ON_LIMIT_REACHED: bool = False
REMOVE_DEAD_ACCOUNTS: bool = False
CPA_THREADS: int = 10
CHECK_INTERVAL_MINUTES: int = 60
ENABLE_TOKEN_REVIVE: bool = False
SUB_DOMAIN_LEVEL: int = 1
ENABLE_SUB2API_MODE: bool = False
SUB2API_URL: str = ""
SUB2API_KEY: str = ""
SUB2API_MIN_THRESHOLD: int = 70
SUB2API_BATCH_COUNT: int = 2
SUB2API_CHECK_INTERVAL: int = 60
SUB2API_THREADS: int = 10
SUB2API_SAVE_TO_LOCAL: bool = True
SUB2API_MIN_REMAINING_WEEKLY_PERCENT: int = 80
SUB2API_REMOVE_ON_LIMIT_REACHED: bool = True
SUB2API_REMOVE_DEAD_ACCOUNTS: bool = True
SUB2API_ENABLE_TOKEN_REVIVE: bool = False

NORMAL_SLEEP_MIN: int = 5
NORMAL_SLEEP_MAX: int = 30
NORMAL_TARGET_COUNT: int = 0

_clash_enable: bool = False
_clash_pool_mode: bool = False
WARP_PROXY_LIST: list = []
PROXY_QUEUE: queue.Queue = queue.Queue()

def reload_all_configs():
    global _c
    global EMAIL_API_MODE, MAIL_DOMAINS, GPTMAIL_BASE, ADMIN_AUTH
    global ENABLE_SUB_DOMAINS, SUB_DOMAIN_COUNT
    global IMAP_SERVER, IMAP_PORT, IMAP_USER, IMAP_PASS
    global FREEMAIL_API_URL, FREEMAIL_API_TOKEN
    global CM_API_URL, CM_ADMIN_EMAIL, CM_ADMIN_PASS
    global MC_API_BASE, MC_KEY
    global DEFAULT_PROXY
    global SUB_DOMAIN_LEVEL
    global ENABLE_MULTI_THREAD_REG, REG_THREADS, MAX_OTP_RETRIES
    global USE_PROXY_FOR_EMAIL, ENABLE_EMAIL_MASKING
    global LOGIN_DELAY_MIN, LOGIN_DELAY_MAX
    global ENABLE_CPA_MODE, SAVE_TO_LOCAL_IN_CPA_MODE
    global CPA_API_URL, CPA_API_TOKEN, MIN_ACCOUNTS_THRESHOLD, BATCH_REG_COUNT
    global MIN_REMAINING_WEEKLY_PERCENT, REMOVE_ON_LIMIT_REACHED, REMOVE_DEAD_ACCOUNTS
    global CPA_THREADS, CHECK_INTERVAL_MINUTES, ENABLE_TOKEN_REVIVE
    global NORMAL_SLEEP_MIN, NORMAL_SLEEP_MAX, NORMAL_TARGET_COUNT
    global _clash_enable, _clash_pool_mode, WARP_PROXY_LIST, PROXY_QUEUE
    global ENABLE_SUB2API_MODE, SUB2API_URL, SUB2API_KEY
    global SUB2API_MIN_THRESHOLD, SUB2API_BATCH_COUNT, SUB2API_CHECK_INTERVAL, SUB2API_THREADS
    global SUB2API_SAVE_TO_LOCAL, SUB2API_MIN_REMAINING_WEEKLY_PERCENT
    global SUB2API_REMOVE_ON_LIMIT_REACHED, SUB2API_REMOVE_DEAD_ACCOUNTS, SUB2API_ENABLE_TOKEN_REVIVE
    global LUCKMAIL_API_KEY,LUCKMAIL_PREFERRED_DOMAIN
    
    _c = init_config()

    EMAIL_API_MODE   = _c.get("email_api_mode", "cloudflare_temp_email")
    MAIL_DOMAINS     = _c.get("mail_domains", "")
    GPTMAIL_BASE     = _c.get("gptmail_base", "")
    ADMIN_AUTH       = _c.get("admin_auth", "")

    _imap            = _c.get("imap", {})
    IMAP_SERVER      = _imap.get("server", "imap.gmail.com")
    IMAP_PORT        = _imap.get("port", 993)
    IMAP_USER        = _imap.get("user", "")
    IMAP_PASS        = _imap.get("pass", "")

    _free            = _c.get("freemail", {})
    FREEMAIL_API_URL = _free.get("api_url", "")
    FREEMAIL_API_TOKEN = _free.get("api_token", "")
  
    _cm              = _c.get("cloudmail", {})
    CM_API_URL       = _cm.get("api_url", "").rstrip("/")
    CM_ADMIN_EMAIL   = _cm.get("admin_email", "")
    CM_ADMIN_PASS    = _cm.get("admin_password", "")

    _mc              = _c.get("mail_curl", {})
    MC_API_BASE      = _mc.get("api_base", "").rstrip("/")
    MC_KEY           = _mc.get("key", "")

    DEFAULT_PROXY    = format_docker_url(_c.get("default_proxy", ""))

    ENABLE_MULTI_THREAD_REG = _c.get("enable_multi_thread_reg", False)
    REG_THREADS      = _c.get("reg_threads", 3)
    MAX_OTP_RETRIES  = _c.get("max_otp_retries", 5)
    USE_PROXY_FOR_EMAIL     = _c.get("use_proxy_for_email", False)
    ENABLE_EMAIL_MASKING    = _c.get("enable_email_masking", True)

    LOGIN_DELAY_MIN  = _c.get("login_delay_min", 20)
    LOGIN_DELAY_MAX  = _c.get("login_delay_max", 45)

    _cpa             = _c.get("cpa_mode", {})
    ENABLE_CPA_MODE  = _cpa.get("enable", False)
    SAVE_TO_LOCAL_IN_CPA_MODE = _cpa.get("save_to_local", True)
    CPA_API_URL      = format_docker_url(_cpa.get("api_url", ""))
    CPA_API_TOKEN    = _cpa.get("api_token", "")
    MIN_ACCOUNTS_THRESHOLD  = _cpa.get("min_accounts_threshold", 30)
    BATCH_REG_COUNT  = _cpa.get("batch_reg_count", 1)
    MIN_REMAINING_WEEKLY_PERCENT = _cpa.get("min_remaining_weekly_percent", 80)
    REMOVE_ON_LIMIT_REACHED = _cpa.get("remove_on_limit_reached", False)
    REMOVE_DEAD_ACCOUNTS    = _cpa.get("remove_dead_accounts", False)
    CPA_THREADS      = _cpa.get("threads", 10)
    CHECK_INTERVAL_MINUTES  = _cpa.get("check_interval_minutes", 60)
    ENABLE_TOKEN_REVIVE     = _cpa.get("enable_token_revive", False)

    _sub2api = _c.get("sub2api_mode", {})
    ENABLE_SUB2API_MODE = _sub2api.get("enable", False)
    SUB2API_URL         = format_docker_url(_sub2api.get("api_url", ""))
    SUB2API_KEY         = _sub2api.get("api_key", "")
    SUB2API_MIN_THRESHOLD = _sub2api.get("min_accounts_threshold", 70)
    SUB2API_BATCH_COUNT = _sub2api.get("batch_reg_count", 2)
    SUB2API_CHECK_INTERVAL = _sub2api.get("check_interval_minutes", 60)
    SUB2API_THREADS     = _sub2api.get("threads", 10)
    SUB2API_SAVE_TO_LOCAL = _sub2api.get("save_to_local", True)
    SUB2API_MIN_REMAINING_WEEKLY_PERCENT = _sub2api.get("min_remaining_weekly_percent", 80)
    SUB2API_REMOVE_ON_LIMIT_REACHED = _sub2api.get("remove_on_limit_reached", True)
    SUB2API_REMOVE_DEAD_ACCOUNTS = _sub2api.get("remove_dead_accounts", True)
    SUB2API_ENABLE_TOKEN_REVIVE = _sub2api.get("enable_token_revive", False)

    _normal          = _c.get("normal_mode", {})
    NORMAL_SLEEP_MIN = _normal.get("sleep_min", 5)
    NORMAL_SLEEP_MAX = _normal.get("sleep_max", 30)
    NORMAL_TARGET_COUNT = _normal.get("target_count", 0)

    _clash_conf      = _c.get("clash_proxy_pool", {})
    _clash_enable    = _clash_conf.get("enable", False)
    _clash_pool_mode = _clash_conf.get("pool_mode", False)
    WARP_PROXY_LIST  = _c.get("warp_proxy_list", [])

    with PROXY_QUEUE.mutex:
        PROXY_QUEUE.queue.clear()
    if _clash_enable and _clash_pool_mode and WARP_PROXY_LIST:
        for p in WARP_PROXY_LIST:
            PROXY_QUEUE.put(p)
    else:
        PROXY_QUEUE.put(DEFAULT_PROXY if DEFAULT_PROXY else None)
    _luckmail        = _c.get("luckmail", {})
    LUCKMAIL_API_KEY = _luckmail.get("api_key", "")
    LUCKMAIL_PREFERRED_DOMAIN = _luckmail.get("preferred_domain", "")
    SUB_DOMAIN_LEVEL = _c.get("sub_domain_level", {})
    ENABLE_SUB_DOMAINS = _c.get("enable_sub_domains", False)
    reload_proxy_config()
    print(f"[{ts()}] [系统] 核心配置已完成同步。")

reload_all_configs()
