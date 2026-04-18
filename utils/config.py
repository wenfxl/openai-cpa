import os
import queue
import threading
import yaml
import random
import string
import shutil
from datetime import datetime
from typing import Optional
from utils.proxy_manager import reload_proxy_config

CONFIG_FILE_LOCK = threading.Lock()
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(CURRENT_DIR)
CONFIG_PATH = os.path.join(BASE_DIR, "data", "config.yaml")


def ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def format_docker_url(url: str) -> str:
    if not url or not isinstance(url, str):
        return url
    if os.path.exists("/.dockerenv"):
        url = url.replace("127.0.0.1", "host.docker.internal")
        url = url.replace("localhost", "host.docker.internal")
    return url


def deep_update_config(default_dict, user_dict):
    """递归检查配置文件"""
    updated = False
    for key, value in default_dict.items():
        if key not in user_dict:
            user_dict[key] = value
            updated = True
        elif isinstance(value, dict) and isinstance(user_dict[key], dict):
            if deep_update_config(value, user_dict[key]):
                updated = True
    return updated


def init_config():
    config_dir = os.path.join(BASE_DIR, "data")
    config_path = os.path.join(config_dir, "config.yaml")
    template_path = os.path.join(BASE_DIR, "config.example.yaml")

    os.makedirs(config_dir, exist_ok=True)
    if not os.path.exists(config_path):
        if os.path.exists(template_path):
            print(f"[{ts()}] [系统] 未检测到 {config_path}，正在从模板自动生成...")
            try:
                shutil.copyfile(template_path, config_path)
                print(f"[{ts()}] [SUCCESS] 配置文件初始化成功！程序已加载默认配置。")
            except PermissionError:
                print(f"[{ts()}] [ERROR] 权限不足，无法在 {config_dir} 目录创建配置。请检查 Docker 目录权限。")
                exit(1)
            except Exception as e:
                print(f"[{ts()}] [ERROR] 自动生成配置文件失败: {e}")
                exit(1)
        else:
            print(f"[{ts()}] [ERROR] 缺少核心模板文件 {template_path}，无法启动！")
            exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        user_config = yaml.safe_load(f) or {}
    if os.path.exists(template_path):
        with open(template_path, "r", encoding="utf-8") as f:
            default_config = yaml.safe_load(f) or {}

        if deep_update_config(default_config, user_config):
            print(f"[{ts()}] [系统] 🛠️ 检测到旧版配置缺失新参数，已自动补齐并生效！")
            try:
                with CONFIG_FILE_LOCK:
                    with open(config_path, "w", encoding="utf-8") as f:
                        yaml.dump(user_config, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
            except Exception as e:
                print(f"[{ts()}] [WARNING] 自动补全配置文件写入失败: {e}")

    return user_config

_c: dict = {}
WEB_PASSWORD: str = "admin"
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
LOCAL_MS_ENABLE_FISSION: bool = False
LOCAL_MS_POOL_FISSION: bool = False
LOCAL_MS_MASTER_EMAIL: str = ""
LOCAL_MS_PASSWORD: str = ""
LOCAL_MS_CLIENT_ID: str = ""
LOCAL_MS_REFRESH_TOKEN: str = ""
LOCAL_MS_SUFFIX_MODE: str = "fixed"
LOCAL_MS_SUFFIX_LEN_MIN: int = 8
LOCAL_MS_SUFFIX_LEN_MAX: int = 8
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
CPA_AUTO_CHECK: bool = True
CHECK_INTERVAL_MINUTES: int = 60
ENABLE_TOKEN_REVIVE: bool = False
SUB_DOMAIN_LEVEL: int = 1
RANDOM_SUB_DOMAIN_LEVEL: bool = False
ENABLE_SUB2API_MODE: bool = False
SUB2API_URL: str = ""
SUB2API_KEY: str = ""
SUB2API_TEST_MODEL: str = "gpt-5.2"
SUB2API_MIN_THRESHOLD: int = 70
SUB2API_BATCH_COUNT: int = 2
SUB2API_CHECK_INTERVAL: int = 60
SUB2API_THREADS: int = 10
SUB2API_SAVE_TO_LOCAL: bool = True
SUB2API_REMOVE_ON_LIMIT_REACHED: bool = True
SUB2API_REMOVE_DEAD_ACCOUNTS: bool = True
SUB2API_ENABLE_TOKEN_REVIVE: bool = False
SUB2API_AUTO_CHECK: bool = True
SUB2API_ACCOUNT_CONCURRENCY: int = 10
SUB2API_ACCOUNT_LOAD_FACTOR: int = 10
SUB2API_ACCOUNT_PRIORITY: int = 1
SUB2API_ACCOUNT_RATE_MULTIPLIER: float = 1.0
SUB2API_ACCOUNT_GROUP_IDS: list = []
SUB2API_ENABLE_WS_MODE: bool = True
SUB2API_DEFAULT_PROXY: str = ""

LUCKMAIL_PREFERRED_DOMAIN: str = ""
LUCKMAIL_EMAIL_TYPE: str = ""
LUCKMAIL_VARIANT_MODE: str = ""
LUCKMAIL_REUSE_PURCHASED: bool = False
LUCKMAIL_TAG_ID: Optional[int] = None
DUCKMAIL_API_URL: str = "https://api.duckmail.com"
DUCKMAIL_DOMAIN: str = ""
DUCKMAIL_MODE: str = "custom_api"
DUCK_API_TOKEN: str = ""
DUCK_COOKIE: str = ""
DUCK_OFFICIAL_API_BASE: str = "https://quack.duckduckgo.com"
DUCKMAIL_FORWARD_MODE: str = "Gmail_OAuth"
DUCKMAIL_FORWARD_EMAIL: str = ""
DUCK_USE_PROXY: bool = True
HERO_SMS_ENABLED: bool = False
HERO_SMS_API_KEY: str = ""
HERO_SMS_BASE_URL: str = "https://hero-sms.com/stubs/handler_api.php"
HERO_SMS_COUNTRY: str = "US"
HERO_SMS_SERVICE: str = "openai"
HERO_SMS_AUTO_PICK_COUNTRY: bool = False
HERO_SMS_REUSE_PHONE: bool = True
HERO_SMS_VERIFY_ON_REGISTER: bool = False
HERO_SMS_MAX_PRICE: float = 2.0
HERO_SMS_MIN_BALANCE: float = 2.0
HERO_SMS_MAX_TRIES: int = 3
HERO_SMS_POLL_TIMEOUT_SEC: int = 120
NORMAL_SLEEP_MIN: int = 5
NORMAL_SLEEP_MAX: int = 30
NORMAL_TARGET_COUNT: int = 0
MAX_LOG_LINES: int = 500
_clash_enable: bool = False
_clash_pool_mode: bool = False
CLASH_CLUSTER_COUNT: int = 5
CLASH_SUB_URL: str = ""
WARP_PROXY_LIST: list = []
PROXY_QUEUE: queue.Queue = queue.Queue()
AI_API_BASE: str = ""
AI_API_KEY: str = ""
AI_MODEL: str = "gpt-3.5-turbo"
AI_ENABLE_PROFILE: bool = False
TG_BOT: dict = {"enable": False, "token": "", "chat_id": ""}
CLUSTER_NODE_NAME: str = ""
CLUSTER_MASTER_URL: str = ""
CLUSTER_SECRET: str = "wenfxl666"
TEMPORAM_COOKIE: str = ""
FVIA_TOKEN: str = ""
TMAILOR_CURRENT_TOKEN: str = ""
REG_MODE: str = "protocol"
DB_TYPE: str = "sqlite"
MYSQL_CFG: dict = {}

def reload_all_configs(new_config_dict=None):
    global _c
    global WEB_PASSWORD
    global EMAIL_API_MODE, MAIL_DOMAINS, GPTMAIL_BASE, ADMIN_AUTH
    global ENABLE_SUB_DOMAINS, SUB_DOMAIN_COUNT
    global IMAP_SERVER, IMAP_PORT, IMAP_USER, IMAP_PASS
    global FREEMAIL_API_URL, FREEMAIL_API_TOKEN
    global CM_API_URL, CM_ADMIN_EMAIL, CM_ADMIN_PASS
    global MC_API_BASE, MC_KEY
    global DEFAULT_PROXY
    global SUB_DOMAIN_LEVEL, RANDOM_SUB_DOMAIN_LEVEL
    global ENABLE_MULTI_THREAD_REG, REG_THREADS, MAX_OTP_RETRIES
    global USE_PROXY_FOR_EMAIL, ENABLE_EMAIL_MASKING
    global LOGIN_DELAY_MIN, LOGIN_DELAY_MAX
    global ENABLE_CPA_MODE, SAVE_TO_LOCAL_IN_CPA_MODE
    global CPA_API_URL, CPA_API_TOKEN, MIN_ACCOUNTS_THRESHOLD, BATCH_REG_COUNT
    global MIN_REMAINING_WEEKLY_PERCENT, REMOVE_ON_LIMIT_REACHED, REMOVE_DEAD_ACCOUNTS
    global CPA_THREADS, CHECK_INTERVAL_MINUTES, ENABLE_TOKEN_REVIVE
    global NORMAL_SLEEP_MIN, NORMAL_SLEEP_MAX, NORMAL_TARGET_COUNT
    global _clash_enable, _clash_pool_mode, WARP_PROXY_LIST, PROXY_QUEUE
    global CLASH_CLUSTER_COUNT, CLASH_SUB_URL
    global ENABLE_SUB2API_MODE, SUB2API_URL, SUB2API_KEY
    global SUB2API_MIN_THRESHOLD, SUB2API_BATCH_COUNT, SUB2API_CHECK_INTERVAL, SUB2API_THREADS, SUB2API_TEST_MODEL
    global SUB2API_SAVE_TO_LOCAL
    global SUB2API_REMOVE_ON_LIMIT_REACHED, SUB2API_REMOVE_DEAD_ACCOUNTS, SUB2API_ENABLE_TOKEN_REVIVE
    global SUB2API_ACCOUNT_CONCURRENCY, SUB2API_ACCOUNT_LOAD_FACTOR, SUB2API_ACCOUNT_PRIORITY, SUB2API_DEFAULT_PROXY
    global SUB2API_ACCOUNT_RATE_MULTIPLIER, SUB2API_ACCOUNT_GROUP_IDS, SUB2API_ENABLE_WS_MODE
    global LUCKMAIL_API_KEY, LUCKMAIL_PREFERRED_DOMAIN, LUCKMAIL_EMAIL_TYPE, LUCKMAIL_VARIANT_MODE, LUCKMAIL_REUSE_PURCHASED, LUCKMAIL_TAG_ID
    global HERO_SMS_ENABLED, HERO_SMS_API_KEY, HERO_SMS_BASE_URL, HERO_SMS_COUNTRY, HERO_SMS_SERVICE
    global HERO_SMS_AUTO_PICK_COUNTRY, HERO_SMS_REUSE_PHONE, HERO_SMS_MAX_PRICE, HERO_SMS_VERIFY_ON_REGISTER
    global HERO_SMS_MIN_BALANCE, HERO_SMS_MAX_TRIES, HERO_SMS_POLL_TIMEOUT_SEC
    global AI_API_BASE, AI_API_KEY, AI_MODEL, AI_ENABLE_PROFILE
    global CPA_AUTO_CHECK, SUB2API_AUTO_CHECK
    global TG_BOT
    global TEMPORAM_COOKIE
    global TMAILOR_CURRENT_TOKEN
    global FVIA_TOKEN
    global DUCKMAIL_API_URL, DUCKMAIL_DOMAIN, DUCKMAIL_MODE, DUCK_API_TOKEN, DUCK_COOKIE, DUCK_OFFICIAL_API_BASE
    global DUCKMAIL_FORWARD_MODE, DUCKMAIL_FORWARD_EMAIL
    global DUCK_USE_PROXY
    global CLUSTER_NODE_NAME, CLUSTER_MASTER_URL, CLUSTER_SECRET
    global REG_MODE
    global LOCAL_MS_ENABLE_FISSION, LOCAL_MS_MASTER_EMAIL, LOCAL_MS_PASSWORD, LOCAL_MS_CLIENT_ID, LOCAL_MS_REFRESH_TOKEN, LOCAL_MS_POOL_FISSION
    global LOCAL_MS_SUFFIX_MODE, LOCAL_MS_SUFFIX_LEN_MIN, LOCAL_MS_SUFFIX_LEN_MAX
    global DB_TYPE, MYSQL_CFG
    global MAX_LOG_LINES

    base_yaml_config = init_config()

    _db_conf = base_yaml_config.get("database", {})
    _mysql_conf = _db_conf.get("mysql", {})

    DB_TYPE = os.getenv("DB_TYPE", str(_db_conf.get("type", "sqlite"))).strip().lower()

    MYSQL_CFG = {
        "host": os.getenv("DB_HOST", _mysql_conf.get("host", "127.0.0.1")),
        "port": int(os.getenv("DB_PORT", _mysql_conf.get("port", 3306))),
        "user": os.getenv("DB_USER", _mysql_conf.get("user", "root")),
        "password": os.getenv("DB_PASS", _mysql_conf.get("password", "")),
        "db_name": os.getenv("DB_NAME", _mysql_conf.get("db_name", "wenfxl_manager"))
    }

    base_yaml_config["database"] = {"type": DB_TYPE, "mysql": MYSQL_CFG}
    is_cloud_db = (DB_TYPE == "mysql")
    db_ready = False
    if is_cloud_db:
        try:
            from utils.db_manager import get_sys_kv, set_sys_kv
            db_ready = True
        except Exception as e:
            print(f"[{ts()}] [WARNING] 无法连接到云端数据库，退回本地 YAML 模式: {e}")

    if new_config_dict is not None:
        _c = new_config_dict
        try:
            with CONFIG_FILE_LOCK:
                with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                    yaml.dump(new_config_dict, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
        except Exception:
            pass

        if is_cloud_db and db_ready:
            try:
                set_sys_kv("global_app_config", new_config_dict)
            except Exception:
                pass
    else:
        if is_cloud_db and db_ready:
            db_config = get_sys_kv("global_app_config")
            if db_config:
                deep_update_config(base_yaml_config, db_config)
                _c = db_config
            else:
                _c = base_yaml_config
                set_sys_kv("global_app_config", _c)
        else:
            _c = base_yaml_config

    def safe_int(value, default, minimum=None):
        try:
            parsed = int(str(value).strip())
        except (TypeError, ValueError):
            parsed = default
        if minimum is not None:
            return max(minimum, parsed)
        return parsed

    def safe_float(value, default, minimum=None):
        try:
            parsed = float(str(value).strip())
        except (TypeError, ValueError):
            parsed = default
        if minimum is not None:
            return max(minimum, parsed)
        return parsed

    def safe_bool(value, default=False):
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
        return default

    def parse_group_ids(raw_value):
        if isinstance(raw_value, list):
            raw_items = raw_value
        else:
            raw_items = str(raw_value or "").split(",")

        group_ids = []
        for item in raw_items:
            text = str(item).strip()
            if text.isdigit():
                group_ids.append(int(text))
        return group_ids

    WEB_PASSWORD = str(_c.get("web_password", "admin")).strip()

    EMAIL_API_MODE = _c.get("email_api_mode", "cloudflare_temp_email")
    MAIL_DOMAINS = _c.get("mail_domains", "")
    GPTMAIL_BASE = str(_c.get("gptmail_base", "")).strip().rstrip("/")
    ADMIN_AUTH = _c.get("admin_auth", "")

    _imap = _c.get("imap", {})
    IMAP_SERVER = _imap.get("server", "imap.gmail.com")
    IMAP_PORT = _imap.get("port", 993)
    IMAP_USER = _imap.get("user", "")
    IMAP_PASS = _imap.get("pass", "")

    _local_microsoft = _c.get("local_microsoft", {})
    LOCAL_MS_ENABLE_FISSION = bool(_local_microsoft.get("enable_fission", False))
    LOCAL_MS_POOL_FISSION = bool(_local_microsoft.get("pool_fission", False))
    LOCAL_MS_MASTER_EMAIL = str(_local_microsoft.get("master_email", "")).strip()
    LOCAL_MS_CLIENT_ID = str(_local_microsoft.get("client_id", "")).strip()
    LOCAL_MS_REFRESH_TOKEN = str(_local_microsoft.get("refresh_token", "")).strip()
    LOCAL_MS_SUFFIX_MODE = str(_local_microsoft.get("suffix_mode", "fixed")).strip().lower()
    if LOCAL_MS_SUFFIX_MODE not in {"fixed", "range", "mystic"}:
        LOCAL_MS_SUFFIX_MODE = "fixed"
    try:
        LOCAL_MS_SUFFIX_LEN_MIN = int(_local_microsoft.get("suffix_len_min", 8) or 8)
    except Exception:
        LOCAL_MS_SUFFIX_LEN_MIN = 8
    try:
        LOCAL_MS_SUFFIX_LEN_MAX = int(_local_microsoft.get("suffix_len_max", LOCAL_MS_SUFFIX_LEN_MIN) or LOCAL_MS_SUFFIX_LEN_MIN)
    except Exception:
        LOCAL_MS_SUFFIX_LEN_MAX = LOCAL_MS_SUFFIX_LEN_MIN
    LOCAL_MS_SUFFIX_LEN_MIN = max(8, min(32, LOCAL_MS_SUFFIX_LEN_MIN))
    LOCAL_MS_SUFFIX_LEN_MAX = max(8, min(32, LOCAL_MS_SUFFIX_LEN_MAX))
    if LOCAL_MS_SUFFIX_LEN_MAX < LOCAL_MS_SUFFIX_LEN_MIN:
        LOCAL_MS_SUFFIX_LEN_MAX = LOCAL_MS_SUFFIX_LEN_MIN

    _free = _c.get("freemail", {})
    FREEMAIL_API_URL = str(_free.get("api_url", "")).strip().rstrip("/")
    FREEMAIL_API_TOKEN = _free.get("api_token", "")

    _cm = _c.get("cloudmail", {})
    CM_API_URL = str(_cm.get("api_url", "")).strip().rstrip("/")
    CM_ADMIN_EMAIL = _cm.get("admin_email", "")
    CM_ADMIN_PASS = _cm.get("admin_password", "")

    _mc = _c.get("mail_curl", {})
    MC_API_BASE = str(_mc.get("api_base", "")).strip().rstrip("/")
    MC_KEY = _mc.get("key", "")

    DEFAULT_PROXY = format_docker_url(_c.get("default_proxy", ""))

    ENABLE_MULTI_THREAD_REG = _c.get("enable_multi_thread_reg", False)
    REG_THREADS = _c.get("reg_threads", 3)
    MAX_OTP_RETRIES = _c.get("max_otp_retries", 5)
    USE_PROXY_FOR_EMAIL = _c.get("use_proxy_for_email", False)
    ENABLE_EMAIL_MASKING = _c.get("enable_email_masking", True)

    LOGIN_DELAY_MIN = _c.get("login_delay_min", 20)
    LOGIN_DELAY_MAX = _c.get("login_delay_max", 45)

    _cpa = _c.get("cpa_mode", {})
    ENABLE_CPA_MODE = _cpa.get("enable", False)
    SAVE_TO_LOCAL_IN_CPA_MODE = _cpa.get("save_to_local", True)
    CPA_API_URL = format_docker_url(str(_cpa.get("api_url", "")).strip()).rstrip("/")
    CPA_API_TOKEN = _cpa.get("api_token", "")
    MIN_ACCOUNTS_THRESHOLD = _cpa.get("min_accounts_threshold", 30)
    BATCH_REG_COUNT = _cpa.get("batch_reg_count", 1)
    MIN_REMAINING_WEEKLY_PERCENT = _cpa.get("min_remaining_weekly_percent", 80)
    REMOVE_ON_LIMIT_REACHED = _cpa.get("remove_on_limit_reached", False)
    REMOVE_DEAD_ACCOUNTS = _cpa.get("remove_dead_accounts", False)
    CPA_THREADS = _cpa.get("threads", 10)
    CHECK_INTERVAL_MINUTES = _cpa.get("check_interval_minutes", 60)
    ENABLE_TOKEN_REVIVE = _cpa.get("enable_token_revive", False)
    CPA_AUTO_CHECK = _cpa.get("auto_check", True)

    _sub2api = _c.get("sub2api_mode", {})
    ENABLE_SUB2API_MODE = _sub2api.get("enable", False)
    SUB2API_URL = format_docker_url(str(_sub2api.get("api_url", "")).strip()).rstrip("/")
    SUB2API_KEY = _sub2api.get("api_key", "")
    SUB2API_MIN_THRESHOLD = _sub2api.get("min_accounts_threshold", 70)
    SUB2API_TEST_MODEL = _sub2api.get("test_model", "")
    SUB2API_BATCH_COUNT = _sub2api.get("batch_reg_count", 2)
    SUB2API_CHECK_INTERVAL = _sub2api.get("check_interval_minutes", 60)
    SUB2API_THREADS = _sub2api.get("threads", 10)
    SUB2API_SAVE_TO_LOCAL = _sub2api.get("save_to_local", True)
    SUB2API_REMOVE_ON_LIMIT_REACHED = _sub2api.get("remove_on_limit_reached", True)
    SUB2API_REMOVE_DEAD_ACCOUNTS = _sub2api.get("remove_dead_accounts", True)
    SUB2API_ENABLE_TOKEN_REVIVE = _sub2api.get("enable_token_revive", False)
    SUB2API_AUTO_CHECK = _sub2api.get("auto_check", True)
    SUB2API_ACCOUNT_CONCURRENCY = safe_int(_sub2api.get("account_concurrency", 10), 10, minimum=1)
    SUB2API_ACCOUNT_LOAD_FACTOR = safe_int(_sub2api.get("account_load_factor", 10), 10, minimum=1)
    SUB2API_ACCOUNT_PRIORITY = safe_int(_sub2api.get("account_priority", 1), 1, minimum=1)
    SUB2API_ACCOUNT_RATE_MULTIPLIER = safe_float(_sub2api.get("account_rate_multiplier", 1.0), 1.0, minimum=0.0)
    SUB2API_ACCOUNT_GROUP_IDS = parse_group_ids(_sub2api.get("account_group_ids", ""))
    SUB2API_ENABLE_WS_MODE = safe_bool(_sub2api.get("enable_ws_mode", True), default=True)
    SUB2API_DEFAULT_PROXY = _sub2api.get("default_proxy", "")
    _normal = _c.get("normal_mode", {})
    NORMAL_SLEEP_MIN = _normal.get("sleep_min", 5)
    NORMAL_SLEEP_MAX = _normal.get("sleep_max", 30)
    NORMAL_TARGET_COUNT = _normal.get("target_count", 0)

    _clash_conf = _c.get("clash_proxy_pool", {})
    _clash_enable = _clash_conf.get("enable", False)
    _clash_pool_mode = _clash_conf.get("pool_mode", False)
    CLASH_CLUSTER_COUNT = int(_clash_conf.get("cluster_count") or 5)
    CLASH_SUB_URL = str(_clash_conf.get("sub_url") or "").strip()
    WARP_PROXY_LIST = _c.get("warp_proxy_list", [])

    with PROXY_QUEUE.mutex:
        PROXY_QUEUE.queue.clear()
    if _clash_enable and _clash_pool_mode and WARP_PROXY_LIST:
        for p in WARP_PROXY_LIST:
            PROXY_QUEUE.put(p)
    else:
        PROXY_QUEUE.put(DEFAULT_PROXY if DEFAULT_PROXY else None)

    _luckmail = _c.get("luckmail", {})
    LUCKMAIL_API_KEY = _luckmail.get("api_key", "")
    LUCKMAIL_PREFERRED_DOMAIN = _luckmail.get("preferred_domain", "")
    LUCKMAIL_EMAIL_TYPE = str(_luckmail.get("email_type") or "ms_graph").strip()
    LUCKMAIL_VARIANT_MODE = str(_luckmail.get("variant_mode") or "").strip()
    LUCKMAIL_REUSE_PURCHASED = bool(_luckmail.get("reuse_purchased", False))

    _raw_tag_id = _luckmail.get("tag_id")
    try:
        LUCKMAIL_TAG_ID = int(_raw_tag_id) if _raw_tag_id else None
    except (ValueError, TypeError):
        LUCKMAIL_TAG_ID = None

    SUB_DOMAIN_LEVEL = _c.get("sub_domain_level", 1)
    RANDOM_SUB_DOMAIN_LEVEL = _c.get("random_sub_domain_level", False)
    ENABLE_SUB_DOMAINS = _c.get("enable_sub_domains", False)

    _hero_sms_conf = _c.get("hero_sms", {})
    HERO_SMS_ENABLED = _hero_sms_conf.get("enabled", False)
    HERO_SMS_API_KEY = _hero_sms_conf.get("api_key", "")
    HERO_SMS_BASE_URL = str(
        _hero_sms_conf.get("base_url", "https://hero-sms.com/stubs/handler_api.php")).strip().rstrip("/")
    HERO_SMS_COUNTRY = _hero_sms_conf.get("country", "US")
    HERO_SMS_SERVICE = _hero_sms_conf.get("service", "dr")
    HERO_SMS_AUTO_PICK_COUNTRY = _hero_sms_conf.get("auto_pick_country", False)
    HERO_SMS_REUSE_PHONE = _hero_sms_conf.get("reuse_phone", True)
    HERO_SMS_VERIFY_ON_REGISTER = _hero_sms_conf.get("verify_on_register", False)

    try:
        HERO_SMS_MAX_PRICE = float(_hero_sms_conf.get("max_price", 2.0))
    except:
        HERO_SMS_MAX_PRICE = 2.0

    try:
        HERO_SMS_MIN_BALANCE = float(_hero_sms_conf.get("min_balance", 2.0))
    except:
        HERO_SMS_MIN_BALANCE = 2.0

    try:
        HERO_SMS_MAX_TRIES = int(_hero_sms_conf.get("max_tries", 3))
    except:
        HERO_SMS_MAX_TRIES = 3

    try:
        HERO_SMS_POLL_TIMEOUT_SEC = int(_hero_sms_conf.get("poll_timeout_sec", 120))
    except:
        HERO_SMS_POLL_TIMEOUT_SEC = 120

    _ai = _c.get("ai_service", {})
    AI_API_BASE = str(_ai.get("api_base", "https://api.openai.com/v1")).strip().rstrip("/")
    AI_API_KEY = _ai.get("api_key", "")
    AI_MODEL = _ai.get("model", "gpt-3.5-turbo")
    AI_ENABLE_PROFILE = _ai.get("enable_profile", False)

    _tg = _c.get("tg_bot", {})
    TG_BOT = {
        "enable": _tg.get("enable", False),
        "token": str(_tg.get("token", "")),
        "chat_id": str(_tg.get("chat_id", "")),
        "mask_email": _tg.get("mask_email", False),
        "mask_password": _tg.get("mask_password", False),
        "template_success": _tg.get("template_success",
                                    "🎉 <b>注册成功</b>\n⏰ 时间: <code>{time}</code>\n📧 账号: <code>{email}</code>\n🔑 密码: <code>{password}</code>"),
        "template_stop": _tg.get("template_stop",
                                 "🛑 <b>系统已收到停止指令</b>\n\n📊 <b>最终运行统计</b>：\n成功率: {success_rate}% · 成功: {success}/{target} · 失败: {failed} 次 · 风控拦截: {retries} 次 · 总耗时: {elapsed_time}s · 平均单号: {avg_time}s")
    }

    _duck = _c.get("duckmail", {})
    DUCKMAIL_API_URL = str(_duck.get("api_url") or "https://api.duckmail.com").rstrip("/")
    DUCKMAIL_DOMAIN = str(_duck.get("domain") or "").strip()
    DUCKMAIL_MODE = str(_duck.get("mode") or "custom_api").strip().lower()
    DUCK_API_TOKEN = str(_duck.get("duck_api_token") or "").strip()
    DUCK_COOKIE = str(_duck.get("duck_cookie") or "").strip()
    DUCK_OFFICIAL_API_BASE = str(_duck.get("duck_api_base_url") or "https://quack.duckduckgo.com").rstrip("/")
    DUCKMAIL_FORWARD_MODE = str(_duck.get("forward_mode") or "Gmail_OAuth").strip()
    DUCKMAIL_FORWARD_EMAIL = str(_duck.get("forward_email") or "").strip()
    DUCK_USE_PROXY = safe_bool(_duck.get("use_proxy", True), default=True)

    CLUSTER_NODE_NAME = str(_c.get("cluster_node_name", "")).strip()
    CLUSTER_MASTER_URL = str(_c.get("cluster_master_url", "")).strip().rstrip("/")
    CLUSTER_SECRET = str(_c.get("cluster_secret", "wenfxl666")).strip()

    REG_MODE = str(_c.get("reg_mode", "protocol")).strip().lower()

    _temporam = _c.get("temporam", {})
    TEMPORAM_COOKIE = str(_temporam.get("cookie") or "").strip()

    _tmailor = _c.get("tmailor", {})
    TMAILOR_CURRENT_TOKEN = str(_tmailor.get("current_token") or "").strip()

    _fvia = _c.get("fvia", {})
    FVIA_TOKEN = str(_fvia.get("token") or "").strip()

    MAX_LOG_LINES = safe_int(_c.get("max_log_lines", 500), 500, minimum=50)


    reload_proxy_config()
    print(f"[{ts()}] [系统] 核心配置已完成同步。")

reload_all_configs()
