import imaplib
import json
import random
import re
import socket
import string
import time
import threading
from email import message_from_string
from email.header import decode_header, make_header
from email.message import Message
from email.policy import default as email_policy
from html import unescape
from typing import Any, Optional
from urllib.parse import urlparse
import socks
from curl_cffi import requests
from utils import config as cfg
from utils.integrations.ai_service import AIService
from utils.email_providers.gmail_service import get_gmail_otp_via_oauth
from utils.email_providers.duckmail_service import DuckMailService

class ProxyIMAP4_SSL(imaplib.IMAP4_SSL):
    """支持 Socks5 和 HTTP 代理的局部 IMAP 客户端"""

    def __init__(self, host, port, proxy_url=None, **kwargs):
        self.proxy_url = proxy_url
        super().__init__(host, port, **kwargs)

    def _create_socket(self, timeout):
        if not self.proxy_url:
            return socket.create_connection((self.host, self.port), timeout)

        parsed = urlparse(self.proxy_url)
        if 'socks5' in parsed.scheme.lower():
            p_type = socks.SOCKS5
        else:
            p_type = socks.HTTP

        proxy_port = parsed.port or (1080 if p_type == socks.SOCKS5 else 8080)
        sock = socks.socksocket(socket.AF_INET, socket.SOCK_STREAM)
        sock.set_proxy(p_type, parsed.hostname, proxy_port, rdns=True)
        sock.settimeout(timeout)
        sock.connect((self.host, self.port))
        return sock

luckmail_lock = threading.Lock()

_CM_TOKEN_CACHE: Optional[str] = None

_thread_data = threading.local()
_orig_sleep = time.sleep
LOCAL_USED_PIDS = set()
AI_NAME_POOL = []
AI_KW_POOL = []
FIRST_NAMES = [
    "james", "john", "robert", "michael", "william", "david", "richard", "joseph", "thomas", "charles",
    "christopher", "daniel", "matthew", "anthony", "mark", "donald", "steven", "paul", "andrew", "joshua"
]
LAST_NAMES = [
    "smith", "johnson", "williams", "brown", "jones", "garcia", "miller", "davis", "rodriguez", "martinez",
    "hernandez", "lopez", "gonzalez", "wilson", "anderson", "thomas", "taylor", "moore", "jackson", "martin"
]


def _safe_set_tag(lm_service, p_id, tag_id):
    """带重试机制的异步打标，防止网络波动导致打标失败变成死循环号"""
    for _ in range(3):
        try:
            if lm_service.set_email_tag(p_id, tag_id):
                return
        except Exception:
            pass
        time.sleep(2)


def clear_sticky_domain():
    """注册失败时调用"""
    if hasattr(_thread_data, 'sticky_domain'):
        _thread_data.sticky_domain = None


def set_last_email(email: str):
    _thread_data.last_attempt_email = email


def get_last_email() -> Optional[str]:
    return getattr(_thread_data, 'last_attempt_email', None)


def _smart_sleep(secs):
    for _ in range(int(secs * 10)):
        if getattr(cfg, 'GLOBAL_STOP', False):
            return
        _orig_sleep(0.1)


time.sleep = _smart_sleep


def _ssl_verify() -> bool:
    import os
    flag = os.getenv("OPENAI_SSL_VERIFY", "1").strip().lower()
    return flag not in {"0", "false", "no", "off"}


def mask_email(text: str, force_mask: bool = False) -> str:
    """日志脱敏：隐藏邮箱域名部分。"""
    if not force_mask and not getattr(cfg, 'ENABLE_EMAIL_MASKING', False):
        return text if text else ""
    if not text:
        return ""
    if "@" in text:
        try:
            user_part, _ = text.split("@", 1)

            if "+" in user_part:
                main_acc, alias_suffix = user_part.split("+", 1)

                m_keep = 2 if len(main_acc) > 2 else 1
                masked_main = main_acc[:m_keep] + "***"

                a_keep = 2 if len(alias_suffix) > 2 else 1
                masked_alias = alias_suffix[:a_keep] + "***"

                return f"{masked_main}+{masked_alias}@***.***"
            else:
                u_keep = 2 if len(user_part) > 2 else 1
                return f"{user_part[:u_keep]}***@***.***"
        except:
            return "******@***.***"

    domain_match = re.match(r"^([a-zA-Z0-9.-]+\.[a-zA-Z]{2,}|\d{1,3}(?:\.\d{1,3}){3})(:\d+)?$", text)
    if domain_match:
        domain_or_ip = domain_match.group(1)
        port = domain_match.group(2) or ""
        keep = min(4, max(2, len(domain_or_ip) // 3))
        prefix = domain_or_ip[:keep]
        return f"{prefix}***.***{port}"

    match = re.match(r"token_(.+)_(\d{10,})\.json", text)
    if match:
        ep, ts_ = match.group(1), match.group(2)
        return f"token_{ep[:len(ep) // 2]}***_{ts_}.json"
    if len(text) > 8 and ".json" in text:
        name_part = text.replace(".json", "")
        return f"{name_part[:len(name_part) // 2]}***.json"
    return text


def _reset_cm_token_cache() -> None:
    global _CM_TOKEN_CACHE
    _CM_TOKEN_CACHE = None


def get_cm_token(proxies=None) -> Optional[str]:
    global _CM_TOKEN_CACHE
    if _CM_TOKEN_CACHE:
        return _CM_TOKEN_CACHE
    try:
        url = f"{cfg.CM_API_URL}/api/public/genToken"
        payload = {"email": cfg.CM_ADMIN_EMAIL, "password": cfg.CM_ADMIN_PASS}
        res = requests.post(url, json=payload, proxies=proxies,
                            verify=_ssl_verify(), timeout=15)
        data = res.json()
        if data.get("code") == 200:
            _CM_TOKEN_CACHE = data["data"]["token"]
            return _CM_TOKEN_CACHE
        print(f"[{cfg.ts()}] [ERROR] CloudMail Token 生成失败: {data.get('message')}")
    except Exception as e:
        print(f"[{cfg.ts()}] [ERROR] CloudMail 接口请求异常: {e}")
    return None


def _get_ai_data_package():
    global AI_NAME_POOL, AI_KW_POOL
    ai_enabled = getattr(cfg, 'AI_ENABLE_PROFILE', False)

    if ai_enabled:
        ai = AIService()
        if len(AI_NAME_POOL) < 5: AI_NAME_POOL.extend(ai.fetch_names())
        if len(AI_KW_POOL) < 10: AI_KW_POOL.extend(ai.fetch_keywords())
        if AI_NAME_POOL:
            return AI_NAME_POOL.pop(0), True

    letters = "".join(random.choices(string.ascii_lowercase, k=5))
    digits = "".join(random.choices(string.digits, k=3))
    return f"{letters}{digits}", False


def get_email_and_token(proxies: Any = None) -> tuple:
    """兼容五种邮箱模式的地址创建，返回 (email, token_or_id)。"""
    if getattr(cfg, 'GLOBAL_STOP', False): return None, None
    _thread_data.last_attempt_email = None

    mode = cfg.EMAIL_API_MODE
    mail_proxies = proxies if cfg.USE_PROXY_FOR_EMAIL else None

    if mode == "mail_curl":
        try:
            url = f"{cfg.MC_API_BASE}/api/remail?key={cfg.MC_KEY}"
            res = requests.post(url, proxies=mail_proxies, verify=_ssl_verify(), timeout=15)
            data = res.json()
            if data.get("email") and data.get("id"):
                email = data["email"]
                mailbox_id = data["id"]
                set_last_email(email)
                print(f"[{cfg.ts()}] [INFO] mail-curl 分配邮箱: ({mask_email(email)}) (BoxID: {mailbox_id})")
                return email, mailbox_id
        except Exception as e:
            print(f"[{cfg.ts()}] [ERROR] mail-curl 获取邮箱异常: {e}")
        return None, None

    if mode == "fvia":
        try:
            from utils.email_providers.fvia_service import FviaMailService
            current_token = getattr(cfg, 'FVIA_TOKEN', '')

            if not current_token:
                print(f"[{cfg.ts()}] [ERROR] 未在配置中检测到 Fvia Token，请前往前端填写！")
                return None, None

            fs = FviaMailService(token=current_token, proxies=mail_proxies)
            email, token = fs.create_email()

            if email and token:
                set_last_email(email)
                print(f"[{cfg.ts()}] [INFO] FviaInboxes 成功分配邮箱: ({mask_email(email)})")
                return email, token
            else:
                print(f"[{cfg.ts()}] [ERROR] FviaInboxes 获取域名列表失败。")
        except Exception as e:
            print(f"[{cfg.ts()}] [ERROR] FviaInboxes 流程异常: {e}")
        return None, None

    if mode == "tmailor":
        try:
            from utils.email_providers.tmailor_service import TmailorService
            current_token = getattr(cfg, 'TMAILOR_CURRENT_TOKEN', '')
            ts_service = TmailorService(current_token=current_token, proxies=mail_proxies)
            email, token = ts_service.create_email()

            if email and token:
                set_last_email(email)
                print(f"[{cfg.ts()}] [INFO] Tmailor 成功创建邮箱: ({mask_email(email)})")
                return email, token
            else:
                print(f"[{cfg.ts()}] [ERROR] Tmailor 获取邮箱失败，请检查 Token 是否过期。")
        except Exception as e:
            print(f"[{cfg.ts()}] [ERROR] Tmailor 流程异常: {e}")
        return None, None

    if mode == "inboxes":
        try:
            from utils.email_providers.inboxes_service import InboxesService
            ibs = InboxesService(proxies=mail_proxies)
            email, token = ibs.create_email()

            if email and token:
                set_last_email(email)
                print(f"[{cfg.ts()}] [INFO] Inboxes.com 成功分配邮箱: ({mask_email(email)})")
                return email, token
            else:
                print(f"[{cfg.ts()}] [ERROR] Inboxes.com 申请失败。")
        except Exception as e:
            print(f"[{cfg.ts()}] [ERROR] Inboxes.com 流程异常: {e}")
        return None, None

    if mode == "temporarymail":
        try:
            from utils.email_providers.temporarymail_service import TemporaryMailService
            tm_service = TemporaryMailService(proxies=mail_proxies)
            email, token = tm_service.create_email()

            if email and token:
                set_last_email(email)
                print(f"[{cfg.ts()}] [INFO] TemporaryMail 成功分配邮箱: ({mask_email(email)})")
                return email, token
            else:
                print(f"[{cfg.ts()}] [ERROR] TemporaryMail 申请失败。")
        except Exception as e:
            print(f"[{cfg.ts()}] [ERROR] TemporaryMail 流程异常: {e}")
        return None, None

    if mode == "temporam":
        try:
            from utils.email_providers.temporam_service import TemporamService
            tp_service = TemporamService(proxies=mail_proxies)
            email, token = tp_service.create_email()

            if email and token:
                set_last_email(email)
                print(f"[{cfg.ts()}] [INFO] Temporam 成功生成邮箱: ({mask_email(email)})")
                return email, token
            else:
                print(f"[{cfg.ts()}] [ERROR] Temporam 获取邮箱失败")
        except Exception as e:
            print(f"[{cfg.ts()}] [ERROR] Temporam 流程异常: {e}")
        return None, None

    if mode == "luckmail":
        try:
            from utils.email_providers.luckmail_service import LuckMailService
            lm_service = LuckMailService(
                api_key=cfg.LUCKMAIL_API_KEY,
                preferred_domain=getattr(cfg, 'LUCKMAIL_PREFERRED_DOMAIN', ""),
                proxies=mail_proxies,
                email_type=getattr(cfg, 'LUCKMAIL_EMAIL_TYPE', "ms_graph"),
                variant_mode=getattr(cfg, 'LUCKMAIL_VARIANT_MODE', "")
            )

            tag_id = getattr(cfg, 'LUCKMAIL_TAG_ID', None)
            if not tag_id:
                with luckmail_lock:
                    tag_id = getattr(cfg, 'LUCKMAIL_TAG_ID', None)
                    if not tag_id:
                        tag_id = lm_service.get_or_create_tag_id("已使用")
                        if tag_id:
                            cfg.LUCKMAIL_TAG_ID = tag_id
                            try:
                                import yaml
                                with cfg.CONFIG_FILE_LOCK:
                                    with open(cfg.CONFIG_PATH, "r", encoding="utf-8") as f:
                                        y = yaml.safe_load(f) or {}
                                    y.setdefault("luckmail", {})["tag_id"] = tag_id
                                    with open(cfg.CONFIG_PATH, "w", encoding="utf-8") as f:
                                        yaml.dump(y, f, allow_unicode=True, sort_keys=False)
                                print(f"[{cfg.ts()}] [系统] 标签 ID {tag_id} 已同步至配置文件")
                            except Exception as e:
                                print(f"[{cfg.ts()}] [WARNING] 配置文件写入失败: {e}")

            if getattr(cfg, 'LUCKMAIL_REUSE_PURCHASED', False):
                with luckmail_lock:
                    email, token, p_id = lm_service.get_random_purchased_email(tag_id=tag_id,
                                                                               local_used_pids=LOCAL_USED_PIDS)
                    if p_id:
                        LOCAL_USED_PIDS.add(p_id)

                if email and token:
                    print(f"[{cfg.ts()}] [SUCCESS] LuckMail 成功复用历史邮箱: ({mask_email(email)})")
                    if p_id and tag_id:
                        threading.Thread(target=_safe_set_tag, args=(lm_service, p_id, tag_id), daemon=True).start()
                    return email, token
                print(f"[{cfg.ts()}] [WARNING] 未找到符合条件的历史邮箱，准备购买新号...")

            email, token, p_id = lm_service.get_email_and_token(auto_tag=False)

            if email and token:
                if p_id:
                    with luckmail_lock:
                        LOCAL_USED_PIDS.add(p_id)

                print(f"[{cfg.ts()}] [INFO] LuckMail 成功购买新邮箱: ({mask_email(email)})")

                if p_id and tag_id:
                    threading.Thread(target=_safe_set_tag, args=(lm_service, p_id, tag_id), daemon=True).start()
                return email, token

        except Exception as e:
            print(f"[{cfg.ts()}] [ERROR] LuckMail 流程异常: {e}")
            return None, None

    if mode == "duckmail":
        try:
            from utils.email_providers.duckmail_service import DuckMailService
            duck_use_proxy = getattr(cfg, 'DUCK_USE_PROXY', True)
            duck_proxies = proxies if duck_use_proxy else None
            ds = DuckMailService(proxies=duck_proxies)
            email, token = ds.create_email()
            if email:
                set_last_email(email)
                print(f"[{cfg.ts()}] [INFO] DuckMail ({ds.mode}) 成功创建邮箱: {mask_email(email)}")
                return email, token
            else:
                print(f"[{cfg.ts()}] [ERROR] DuckMail 获取邮箱失败")
        except Exception as e:
            print(f"[{cfg.ts()}] [ERROR] DuckMail 流程异常: {e}")
        return None, None

    if mode == "generator_email":
        try:
            from utils.email_providers.generator_email_service import GeneratorEmailService
            ge_service = GeneratorEmailService(proxies=mail_proxies)
            email, token = ge_service.create_email()

            if email and token:
                set_last_email(email)
                print(f"[{cfg.ts()}] [INFO] GeneratorEmail 成功创建邮箱: ({mask_email(email)})")
                return email, token
            else:
                print(f"[{cfg.ts()}] [ERROR] GeneratorEmail 获取邮箱失败")
        except Exception as e:
            print(f"[{cfg.ts()}] [ERROR] GeneratorEmail 流程异常: {e}")
        return None, None

    if mode == "tempmail":
        try:
            from utils.email_providers.tempmail_service import TempmailService
            tm_service = TempmailService(proxies=mail_proxies)
            email, token = tm_service.create_email()

            if email and token:
                set_last_email(email)
                print(f"[{cfg.ts()}] [INFO] Tempmail 成功创建邮箱: ({mask_email(email)})")
                return email, token
            else:
                print(f"[{cfg.ts()}] [ERROR] Tempmail 获取邮箱失败")
        except Exception as e:
            print(f"[{cfg.ts()}] [ERROR] Tempmail 流程异常: {e}")
        return None, None
    if mode == "tempmail_org":
        try:
            from utils.email_providers.tempmail_org import TempMailOrgService
            tm_org = TempMailOrgService(proxies=mail_proxies)
            email, token = tm_org.create_email()

            if email and token:
                set_last_email(email)
                print(f"[{cfg.ts()}] [INFO] TempMail.org 成功创建邮箱: ({mask_email(email)})")
                return email, token
            else:
                print(f"[{cfg.ts()}] [ERROR] TempMail.org 获取邮箱失败")
        except Exception as e:
            print(f"[{cfg.ts()}] [ERROR] TempMail.org 流程异常: {e}")
        return None, None

    if mode == "local_microsoft":
        from utils.email_providers.local_microsoft_service import LocalMicrosoftService
        ms_service = LocalMicrosoftService(proxies=mail_proxies)

        mailbox_info = ms_service.get_unused_mailbox()
        if not mailbox_info:
            cfg.POOL_EXHAUSTED = True
            print(f"[{cfg.ts()}] [WARNING] 微软邮箱库已耗尽，请前往前端导入更多账号。")
            return None, None

        email = mailbox_info["email"]
        set_last_email(email)
        print(f"[{cfg.ts()}] [INFO] 微软库分配并锁定账号: ({mask_email(email)})")
        return email, json.dumps(mailbox_info, ensure_ascii=False)

    prefix, ai_enabled = _get_ai_data_package()

    if cfg.ENABLE_SUB_DOMAINS:
        sticky = getattr(_thread_data, 'sticky_domain', None)
        if sticky:
            selected_domain = sticky
            print(f"[{cfg.ts()}] [INFO] 多级域名模式 - 沿用上一轮成功域名: {mask_email(selected_domain)}")
        else:
            main_list = [d.strip() for d in cfg.MAIL_DOMAINS.split(",") if d.strip()]
            if not main_list:
                print(f"[{cfg.ts()}] [ERROR] 未配置主域名池，无法捏造子域！")
                return None, None

            selected_main = random.choice(main_list)
            if getattr(cfg, 'RANDOM_SUB_DOMAIN_LEVEL', False):
                level = random.randint(1, 7)
            else:
                try:
                    level = int(getattr(cfg, 'SUB_DOMAIN_LEVEL', 1))
                except:
                    level = 1

            random_parts = []
            for _ in range(level):
                if ai_enabled and AI_KW_POOL:
                    kw = AI_KW_POOL.pop(0)
                    random_parts.append(f"{kw}-{''.join(random.choices(string.ascii_lowercase + string.digits, k=4))}")
                else:
                    random_parts.append(''.join(random.choices(string.ascii_lowercase + string.digits, k=8)))

            selected_domain = ".".join(random_parts) + f".{selected_main}"
            _thread_data.sticky_domain = selected_domain
    else:
        domain_list = [d.strip() for d in cfg.MAIL_DOMAINS.split(",") if d.strip()]
        if not domain_list:
            print(f"[{cfg.ts()}] [ERROR] 域名池配置为空，无法生成邮箱！")
            return None, None
        selected_domain = random.choice(domain_list)

    email_str = f"{prefix}@{selected_domain}"
    set_last_email(email_str)

    ai_switch_on = getattr(cfg, 'AI_ENABLE_PROFILE', False)
    if ai_switch_on:
        print(f"[{cfg.ts()}] [AI-状态] 已开启 （{mask_email(email_str)}） AI 智能邮箱域名信息增强...")

    if mode == "cloudmail":
        token = get_cm_token(mail_proxies)
        if not token:
            print(f"[{cfg.ts()}] [ERROR] 未能获取 CloudMail Token，跳过注册")
            return None, None
        try:
            res = requests.post(
                f"{cfg.CM_API_URL}/api/public/addUser",
                headers={"Authorization": token},
                json={"list": [{"email": email_str}]},
                proxies=mail_proxies, timeout=15,
            )
            if res.json().get("code") == 200:
                print(f"[{cfg.ts()}] [INFO] CloudMail 成功创建邮箱: {mask_email(email_str)}")
                return email_str, ""
            print(f"[{cfg.ts()}] [ERROR] CloudMail 邮箱创建失败: {res.text}")
        except Exception as e:
            print(f"[{cfg.ts()}] [ERROR] CloudMail 邮箱创建异常: {e}")
        return None, None

    if mode == "freemail":
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg.FREEMAIL_API_TOKEN}"
        }
        for attempt in range(5):
            if getattr(cfg, 'GLOBAL_STOP', False): return None, None
            try:
                res = requests.post(f"{cfg.FREEMAIL_API_URL}/api/create",
                                    json={"email": email_str}, headers=headers,
                                    proxies=mail_proxies, verify=_ssl_verify(), timeout=15)
                res.raise_for_status()
                print(f"[{cfg.ts()}] [INFO] 成功通过 Freemail 指定创建邮箱: {mask_email(email_str)}")
                return email_str, ""
            except Exception as e:
                print(f"[{cfg.ts()}] [ERROR] Freemail 邮箱创建异常: {e}")
                time.sleep(2)
        return None, None

    if mode == "imap":
        print(f"[{cfg.ts()}] [INFO] imap成功生成临时域名邮箱: {email_str}")
        return email_str, ""

    if mode == "cloudflare_temp_email":
        headers = {"x-admin-auth": cfg.ADMIN_AUTH, "Content-Type": "application/json"}
        body = {"enablePrefix": False, "name": prefix, "domain": selected_domain}
        for attempt in range(5):
            if getattr(cfg, 'GLOBAL_STOP', False): return None, None
            try:
                res = requests.post(
                    f"{cfg.GPTMAIL_BASE}/admin/new_address",
                    headers=headers, json=body,
                    proxies=mail_proxies, verify=_ssl_verify(), timeout=15,
                )
                res.raise_for_status()
                data = res.json()
                if data and data.get("address"):
                    email = data["address"].strip()
                    jwt = data.get("jwt", "").strip()
                    set_last_email(email)
                    print(f"[{cfg.ts()}] [INFO] cloudflare_temp_email成功获取临时邮箱: {mask_email(email)}")
                    return email, jwt
                print(f"[{cfg.ts()}] [WARNING] cloudflare_temp_email邮箱申请失败 (尝试 {attempt + 1}/5): {res.text}")
                time.sleep(1)
            except Exception as e:
                print(f"[{cfg.ts()}] [ERROR] cloudflare_temp_email邮箱注册网络异常，准备重试: {e}")
                time.sleep(2)
        return None, None


def _decode_mime_header(value: str) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _extract_body_from_message(message: Message) -> str:
    parts = []
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_maintype() == "multipart":
                continue
            ct = (part.get_content_type() or "").lower()
            if ct not in ("text/plain", "text/html"):
                continue
            try:
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or "utf-8"
                text = payload.decode(charset, errors="replace") if payload else ""
            except Exception:
                try:
                    text = part.get_content()
                except Exception:
                    text = ""
            if ct == "text/html":
                text = re.sub(r"<[^>]+>", " ", text)
            parts.append(text)
    else:
        try:
            payload = message.get_payload(decode=True)
            charset = message.get_content_charset() or "utf-8"
            body = payload.decode(charset, errors="replace") if payload else ""
        except Exception:
            try:
                body = message.get_content()
            except Exception:
                body = str(message.get_payload() or "")
        if "html" in (message.get_content_type() or "").lower():
            body = re.sub(r"<[^>]+>", " ", body)
        parts.append(body)
    return unescape("\n".join(p for p in parts if p).strip())


def _extract_mail_fields(mail: dict) -> dict:
    sender = str(
        mail.get("source") or mail.get("from") or
        mail.get("from_address") or mail.get("fromAddress") or ""
    ).strip()
    subject = str(mail.get("subject") or mail.get("title") or "").strip()
    body_text = str(
        mail.get("text") or mail.get("body") or
        mail.get("content") or mail.get("html") or ""
    ).strip()
    raw = str(mail.get("raw") or "").strip()
    if raw:
        try:
            msg = message_from_string(raw, policy=email_policy)
            sender = sender or _decode_mime_header(msg.get("From", ""))
            subject = subject or _decode_mime_header(msg.get("Subject", ""))
            parsed = _extract_body_from_message(msg)
            body_text = (f"{body_text}\n{parsed}".strip() if body_text else parsed) if parsed else body_text
        except Exception:
            body_text = f"{body_text}\n{raw}".strip() if body_text else raw
    body_text = unescape(re.sub(r"<[^>]+>", " ", body_text))
    return {"sender": sender, "subject": subject, "body": body_text, "raw": raw}


OTP_CODE_PATTERN = r"(?<!\d)(\d{6})(?!\d)"


def _extract_otp_code(content: str) -> str:
    if not content:
        return ""
    patterns = [
        r"(?i)Your ChatGPT code is\s*(\d{6})",
        r"(?i)ChatGPT code is\s*(\d{6})",
        r"(?i)verification code to continue:\s*(\d{6})",
        r"(?i)Subject:.*?(\d{6})",
    ]
    for p in patterns:
        m = re.search(p, content)
        if m:
            return m.group(1)
    fallback = re.search(r"(?<!\d)(\d{6})(?!\d)", content)
    return fallback.group(1) if fallback else ""


def _create_imap_conn(proxy_str=None):
    """使用原生方式建立 IMAP 连接 (支持局部代理)"""
    if proxy_str:
        return ProxyIMAP4_SSL(cfg.IMAP_SERVER, cfg.IMAP_PORT, proxy_url=proxy_str, timeout=15)
    return imaplib.IMAP4_SSL(cfg.IMAP_SERVER, cfg.IMAP_PORT, timeout=15)


def _poll_local_ms_for_oai_code_graph(ms_service, target_email: str, mailbox_dict: dict, max_attempts: int) -> str:
    from datetime import datetime
    import time

    assigned_at = float(mailbox_dict.get("assigned_at") or time.time())
    tgt = target_email.lower().strip()
    master_email = tgt.split('+')[0] + '@' + tgt.split('@')[1] if '+' in tgt else tgt

    processed_msg_ids = set()

    print(f"[{cfg.ts()}] [INFO] 进入 Graph 轮询器，靶向目标: {mask_email(tgt)}", flush=True)

    for attempt in range(max_attempts):
        if getattr(cfg, 'GLOBAL_STOP', False): return ""

        messages = ms_service.fetch_openai_messages(mailbox_dict)
        if mailbox_dict.get("_polling_stopped") == "abuse_mode":
            return ""
        if not messages:
            if attempt % 2 == 0:
                print(f"[{cfg.ts()}] [INFO] 第 {attempt + 1} 次轮询: 未发现任何邮件", flush=True)
        else:
            for msg in messages:
                msg_id = msg.get('id')
                if msg_id in processed_msg_ids:
                    continue
                raw_date = msg.get('receivedDateTime', '').replace('Z', '+00:00')
                try:
                    received_ts = datetime.fromisoformat(raw_date).timestamp()
                    if received_ts < assigned_at - 60:
                        continue
                except Exception:
                    continue
                sender = str(msg.get('from', {}).get('emailAddress', {}).get('address', '')).lower()
                if "openai.com" not in sender:
                    continue
                subject = msg.get('subject', '').lower()
                if not any(k in subject for k in ["code", "verify", "chatgpt", "openai"]):
                    continue

                recipients = [str(r.get('emailAddress', {}).get('address', '')).lower().strip()
                              for r in msg.get('toRecipients', [])]
                body_content = msg.get('body', {}).get('content', '')

                is_hit = (tgt in recipients) or (f"to: {tgt}" in body_content.lower()) or (tgt in body_content.lower())

                if not is_hit and master_email in recipients and (time.time() - received_ts < 30):
                    is_hit = True

                if is_hit:
                    code = _extract_otp_code(f"{subject}\n{body_content}")
                    if code:
                        print(f"\n[{cfg.ts()}] [SUCCESS] 🎯 成功捕获专属验证码: {code} -> {mask_email(tgt)}", flush=True)
                        return code

                processed_msg_ids.add(msg_id)

        time.sleep(5)
    return ""


def get_oai_code(
        email: str,
        jwt: str = "",
        proxies: Any = None,
        processed_mail_ids: set = None,
        pattern: str = OTP_CODE_PATTERN,
        max_attempts: int = 20,
) -> str:
    """轮询各邮箱服务商收取 OpenAI 验证码，返回 6 位字符串或空串。"""
    mailbox_id = jwt
    mail_proxies = proxies if cfg.USE_PROXY_FOR_EMAIL else None
    proxy_str = None
    if mail_proxies:
        if isinstance(mail_proxies, dict):
            proxy_str = mail_proxies.get("https") or mail_proxies.get("http")
        else:
            proxy_str = str(mail_proxies)
    base_url = cfg.GPTMAIL_BASE.rstrip("/")
    mode = cfg.EMAIL_API_MODE

    print(f"\n[{cfg.ts()}] [INFO] 等待接收验证码 ({mask_email(email)})...")

    if processed_mail_ids is None:
        processed_mail_ids = set()

    mail_conn = None
    if mode == "imap":
        try:
            mail_conn = _create_imap_conn(proxy_str)
            mail_conn.login(cfg.IMAP_USER, cfg.IMAP_PASS.replace(" ", ""))
        except Exception as e:
            print(f"\n[{cfg.ts()}] [ERROR] IMAP 初始登录失败: {e}")
            mail_conn = None

    local_ms_account = None
    if mode == "local_microsoft":
        try:
            parsed_jwt = json.loads(jwt or "{}")
            local_ms_account = parsed_jwt if isinstance(parsed_jwt, dict) else None
        except:
            pass

        if local_ms_account:
            local_ms_account["email"] = str(local_ms_account.get("email") or email).strip()
            local_ms_account["assigned_at"] = time.time() - 30
            from utils.email_providers.local_microsoft_service import LocalMicrosoftService
            ms_service = LocalMicrosoftService(proxies=mail_proxies)
            return _poll_local_ms_for_oai_code_graph(
                ms_service=ms_service,
                target_email=email,
                mailbox_dict=local_ms_account,
                max_attempts=max_attempts
            )
        else:
            print(f"\n[{cfg.ts()}] [ERROR] 缺少微软邮箱凭据，无法收信。")
            return ""

    for attempt in range(max_attempts):
        if getattr(cfg, 'GLOBAL_STOP', False): return ""
        try:
            if mode == "mail_curl":
                inbox_url = (f"{cfg.MC_API_BASE}/api/inbox"
                             f"?key={cfg.MC_KEY}&mailbox_id={mailbox_id}")
                res = requests.get(inbox_url, proxies=mail_proxies,
                                   verify=_ssl_verify(), timeout=10)
                if res.status_code == 200:
                    for mail_item in (res.json() or []):
                        m_id = mail_item.get("mail_id")
                        s_name = mail_item.get("sender_name", "").lower()
                        if m_id and m_id not in processed_mail_ids and "openai" in s_name:
                            detail_res = requests.get(
                                f"{cfg.MC_API_BASE}/api/mail"
                                f"?key={cfg.MC_KEY}&id={m_id}",
                                proxies=mail_proxies, verify=_ssl_verify(), timeout=10,
                            )
                            if detail_res.status_code == 200:
                                d = detail_res.json()
                                body = (f"{d.get('subject', '')}\n"
                                        f"{d.get('content', '')}\n"
                                        f"{d.get('html', '')}")
                                code = _extract_otp_code(body)
                                if code:
                                    processed_mail_ids.add(m_id)
                                    print(f"\n[{cfg.ts()}] [SUCCESS] mail_curl ({mask_email(email)})邮箱提取成功: {code}")
                                    return code
            elif mode == "fvia":
                from utils.email_providers.fvia_service import FviaMailService
                fs = FviaMailService(token=jwt, proxies=mail_proxies)
                msgs = fs.get_inbox(email)

                for m in msgs:
                    m_id = m.get("id")
                    if not m_id or m_id in processed_mail_ids:
                        continue

                    subject = str(m.get("subject", ""))
                    sender = str(m.get("from", "")).lower()

                    if "openai" in sender or "openai" in subject.lower() or "chatgpt" in subject:

                        code = _extract_otp_code(subject)

                        if code:
                            processed_mail_ids.add(m_id)
                            print(
                                f"\n[{cfg.ts()}] [SUCCESS] Fvia ({mask_email(email)}) 邮箱提取成功: {code}")
                            return code

            elif mode == "temporarymail":
                if not jwt:
                    return ""
                try:
                    from utils.email_providers.temporarymail_service import TemporaryMailService
                    tm_service = TemporaryMailService(proxies=mail_proxies)
                    inbox_dict = tm_service.get_inbox_list(jwt)

                    for m_id, m_info in inbox_dict.items():
                        if m_id in processed_mail_ids:
                            continue

                        sender = str(m_info.get("from", "")).lower()
                        detail = tm_service.get_email_detail(m_id)
                        subject = str(detail.get("subject", ""))

                        if "openai" in sender or "openai" in subject.lower() or "chatgpt" in subject:
                            code = _extract_otp_code(subject)
                            if code:
                                processed_mail_ids.add(m_id)
                                print(f"\n[{cfg.ts()}] [SUCCESS] TemporaryMail ({mask_email(email)}) 邮箱提取成功: {code}")
                                return code
                except Exception:
                    pass

            elif mode == "inboxes":
                if not jwt:
                    return ""
                try:
                    from utils.email_providers.inboxes_service import InboxesService
                    ibs = InboxesService(proxies=mail_proxies)
                    msgs = ibs.get_inbox(email, jwt)

                    for m in msgs:
                        m_id = str(m.get("uid", ""))
                        if not m_id or m_id in processed_mail_ids:
                            continue

                        subject = str(m.get("s", ""))
                        sender = str(m.get("f", "")).lower()

                        if "openai" in sender or "openai" in subject.lower() or "chatgpt" in subject:
                            code = _extract_otp_code(subject)
                            if code:
                                processed_mail_ids.add(m_id)
                                print(f"\n[{cfg.ts()}] [SUCCESS] Inboxes.com ({mask_email(email)}) 邮箱提取成功: {code}")
                                return code
                except Exception:
                    pass

            elif mode == "tmailor":
                if not jwt:
                    print(f"\n[{cfg.ts()}] [ERROR] Tmailor 缺少 token，无法提取验证码！")
                    return ""
                try:
                    from utils.email_providers.tmailor_service import TmailorService
                    current_token = getattr(cfg, 'TMAILOR_CURRENT_TOKEN', '')
                    if hasattr(cfg, 'tmailor') and isinstance(cfg.tmailor, dict):
                        current_token = cfg.tmailor.get('current_token', current_token)

                    ts_service = TmailorService(current_token=current_token, proxies=mail_proxies)
                    inbox_data = ts_service.get_inbox(jwt)

                    for mail_item in inbox_data.values():
                        msg_id = str(mail_item.get("uuid", ""))
                        if not msg_id or msg_id in processed_mail_ids:
                            continue

                        sender = str(mail_item.get("sender_name", "")).lower()
                        sender_email = str(mail_item.get("sender_email", "")).lower()
                        subject = str(mail_item.get("subject", ""))

                        if "openai" not in sender and "openai" not in sender_email and "openai" not in subject.lower():
                            continue

                        email_id = mail_item.get("email_id")
                        mail_body, real_subject = ts_service.read_email(jwt, msg_id, email_id)

                        if mail_body or real_subject:
                            content = f"{real_subject}\n{mail_body}"
                            code = _extract_otp_code(content)
                            if code:
                                processed_mail_ids.add(msg_id)
                                print(f"\n[{cfg.ts()}] [SUCCESS] Tmailor ({mask_email(email)}) 提取成功: {code}")
                                return code
                except Exception as e:
                    pass

            elif mode == "temporam":
                if not jwt:
                    print(f"\n[{cfg.ts()}] [ERROR] Temporam 缺少 token(即邮箱号)，无法提取验证码！")
                    return ""
                try:
                    from utils.email_providers.temporam_service import TemporamService
                    tp_service = TemporamService(proxies=mail_proxies)
                    raw_data = tp_service.get_messages(jwt)
                    email_list = raw_data.get("data", []) if isinstance(raw_data, dict) else []

                    for msg in email_list:
                        msg_id = str(msg.get("id", msg.get("uuid", "")))

                        if not msg_id or msg_id in processed_mail_ids:
                            continue
                        from_email = str(msg.get("fromEmail", "")).lower()
                        subject = str(msg.get("subject", ""))
                        summary = str(msg.get("summary", ""))
                        full_text = f"{from_email}\n{subject}\n{summary}"
                        if "openai" not in from_email and "openai" not in full_text.lower():
                            continue
                        code = _extract_otp_code(full_text)
                        if code:
                            processed_mail_ids.add(msg_id)
                            print(f"\n[{cfg.ts()}] [SUCCESS] Temporam ({mask_email(email)})邮箱提取成功: {code}")
                            return code

                except Exception as e:
                    pass

            elif mode == "cloudmail":
                token = get_cm_token(mail_proxies)
                if token:
                    res = requests.post(
                        f"{cfg.CM_API_URL}/api/public/emailList",
                        headers={"Authorization": token},
                        json={"toEmail": email, "timeSort": "desc", "size": 10},
                        proxies=mail_proxies, timeout=15,
                    )
                    if res.status_code == 200:
                        for m in res.json().get("data", []):
                            m_id = str(m.get("emailId"))
                            if m_id in processed_mail_ids:
                                continue
                            content = f"{m.get('subject', '')}\n{m.get('text', '')}"
                            if ("openai" in m.get("sendEmail", "").lower() or
                                    "openai" in content.lower()):
                                code = _extract_otp_code(content)
                                if code:
                                    processed_mail_ids.add(m_id)
                                    print(f"\n[{cfg.ts()}] [SUCCESS] CloudMail ({mask_email(email)})邮箱提取成功: {code}")
                                    return code
            elif mode == "duckmail":
                duck_use_proxy = getattr(cfg, 'DUCK_USE_PROXY', True)
                duck_proxies = proxies if duck_use_proxy else None
                ds = DuckMailService(proxies=duck_proxies)
                duck_run_mode = getattr(cfg, 'DUCKMAIL_MODE', 'duck_official')

                if duck_run_mode == "duck_official":
                    forward_mode = getattr(cfg, 'DUCKMAIL_FORWARD_MODE', 'Gmail_OAuth')
                    forward_email = getattr(cfg, 'DUCKMAIL_FORWARD_EMAIL', '')
                    if forward_mode == "Gmail_OAuth":
                        otp_code = get_gmail_otp_via_oauth(email, mail_proxies)
                        if otp_code:
                            print(
                                f"\n[{cfg.ts()}] [SUCCESS] Duck转发 (Gmail OAuth) ({mask_email(email)}) 提取成功: {otp_code}")
                            return otp_code

                    elif forward_mode == "cloudmail":
                        if not forward_email:
                            print(
                                f"\n[{cfg.ts()}] [ERROR] Duckmail 运行失败: 未配置转发邮箱地址({forward_email})！")
                            return ""
                        token = get_cm_token(mail_proxies)
                        if token:
                            res = requests.post(
                                f"{cfg.CM_API_URL}/api/public/emailList",
                                headers={"Authorization": token},
                                json={"toEmail": forward_email, "timeSort": "desc", "size": 10},
                                proxies=mail_proxies, timeout=15,
                            )
                            if res.status_code == 200:
                                for m in res.json().get("data", []):
                                    m_id = str(m.get("emailId"))
                                    if m_id in processed_mail_ids:
                                        continue
                                    content = f"{m.get('subject', '')}\n{m.get('text', '')}"
                                    if "openai" not in m.get("sendEmail",
                                                             "").lower() and "openai" not in content.lower():
                                        continue

                                    target_email = email.lower()
                                    if target_email not in str(m).lower() and target_email not in content.lower():
                                        continue

                                    code = _extract_otp_code(content)
                                    if code:
                                        processed_mail_ids.add(m_id)
                                        print(f"\n[{cfg.ts()}] [SUCCESS] Duck转发 (CloudMail) 提取成功: {code}")
                                        return code


                    elif forward_mode == "freemail":
                        if not forward_email:
                            print(f"\n[{cfg.ts()}] [ERROR] Duckmail 运行失败: 未配置转发邮箱地址(forward_email)！")
                            return ""
                        headers = {"Content-Type": "application/json",
                                   "Authorization": f"Bearer {cfg.FREEMAIL_API_TOKEN}"}
                        res = requests.get(f"{cfg.FREEMAIL_API_URL}/api/emails",
                                           params={"mailbox": forward_email, "limit": 20},
                                           headers=headers, proxies=mail_proxies,
                                           verify=_ssl_verify(), timeout=15)
                        if res.status_code == 200:
                            raw_data = res.json()
                            emails_list = (
                                raw_data.get("data") or raw_data.get("emails") or raw_data.get("messages") or raw_data.get(
                                    "results") or []
                                if isinstance(raw_data, dict) else raw_data
                            )
                            if not isinstance(emails_list, list): emails_list = []
                            for mail in emails_list:
                                mail_id = str(mail.get("id") or mail.get("timestamp") or mail.get("subject") or "")
                                if not mail_id or mail_id in processed_mail_ids: continue
                                subject_text = str(mail.get("subject") or mail.get("title") or "")
                                if "openai" not in subject_text.lower() and "openai" not in str(mail).lower():
                                    continue
                                try:
                                    dr = requests.get(f"{cfg.FREEMAIL_API_URL}/api/email/{mail_id}",
                                                      headers=headers, proxies=mail_proxies,
                                                      verify=_ssl_verify(), timeout=15)
                                    if dr.status_code == 200:
                                        d = dr.json()
                                        content = "\n".join(filter(None, [str(d.get("subject") or ""),
                                                                          str(d.get("content") or ""),
                                                                          str(d.get("html_content") or "")]))

                                        target_email = email.lower()
                                        if target_email not in str(d).lower() and target_email not in content.lower():
                                            continue
                                        code = _extract_otp_code(content)
                                        if not code: code = str(d.get("code") or d.get("verification_code") or "")
                                        if code:
                                            processed_mail_ids.add(mail_id)
                                            print(f"[{cfg.ts()}] [SUCCESS] Duck转发 (Freemail) 提取成功: {code}")
                                            return code
                                except Exception:
                                    pass

                    elif forward_mode == "mail_curl":
                        if not forward_email:
                            print(
                                f"\n[{cfg.ts()}] [ERROR] Duckmail 运行失败: 未配置转发邮箱地址(forward_email)！")
                            return ""
                        inbox_url = f"{cfg.MC_API_BASE}/api/inbox?key={cfg.MC_KEY}&mailbox_id={forward_email}"
                        res = requests.get(inbox_url, proxies=mail_proxies, verify=_ssl_verify(),
                                           timeout=10)
                        if res.status_code == 200:
                            for mail_item in (res.json() or []):
                                m_id = mail_item.get("mail_id")
                                s_name = mail_item.get("sender_name", "").lower()
                                if m_id and m_id not in processed_mail_ids and "openai" in s_name:
                                    detail_res = requests.get(
                                        f"{cfg.MC_API_BASE}/api/mail?key={cfg.MC_KEY}&id={m_id}",
                                        proxies=mail_proxies, verify=_ssl_verify(), timeout=10)
                                    if detail_res.status_code == 200:
                                        d = detail_res.json()
                                        body = f"{d.get('subject', '')}\n{d.get('content', '')}\n{d.get('html', '')}"
                                        target_email = email.lower()
                                        if target_email not in str(d).lower() and target_email not in body.lower():
                                            continue

                                        code = _extract_otp_code(body)
                                        if code:
                                            processed_mail_ids.add(m_id)
                                            print(f"\n[{cfg.ts()}] [SUCCESS] Duck转发 (mail_curl) 提取成功: {code}")
                                            return code
                    elif forward_mode == "cloudflare_temp_email":
                        if not forward_email:
                            print(f"[{cfg.ts()}] [ERROR] Duckmail 运行失败: 未配置转发邮箱地址(forward_email)！")
                            return ""
                        res = requests.get(
                            f"{cfg.GPTMAIL_BASE}/admin/mails",
                            params={"limit": 20, "offset": 0, "address": forward_email},
                            headers={"x-admin-auth": cfg.ADMIN_AUTH},
                            verify=_ssl_verify(), timeout=15, proxies=mail_proxies,
                        )

                        if res.status_code == 200:
                            results = res.json().get("results", [])
                            for mail in results:
                                m_id = mail.get("id")
                                if not m_id or m_id in processed_mail_ids:
                                    continue
                                parsed = _extract_mail_fields(mail)
                                sender_lower = str(parsed.get("sender", "")).lower()
                                content = f"{parsed['subject']}\n{parsed['body']}".strip()
                                if "openai" not in sender_lower and "openai" not in content.lower():
                                    continue
                                target_prefix = email.lower().split('@')[0]
                                if target_prefix not in sender_lower and target_prefix not in content.lower():

                                    continue
                                code = _extract_otp_code(content)
                                if code:
                                    processed_mail_ids.add(m_id)
                                    print(f"\n[{cfg.ts()}] [SUCCESS] Duck转发 (CF 临时邮箱) 提取成功: {code}")
                                    return code
                    else:
                        pass

                else:
                    msgs = ds.get_messages(jwt)
                    for m in msgs:
                        content = f"{m.get('subject', '')}\n{m.get('text', '')}\n{ds.strip_html(m.get('html', ''))}"
                        if "openai" in content.lower() or "chatgpt" in content.lower():
                            code = _extract_otp_code(content)
                            if code:
                                print(
                                    f"\n[{cfg.ts()}] [SUCCESS] Duck API ({mask_email(email)}) 提取成功: {code}")
                                return code
            elif mode == "generator_email":
                if not jwt:
                    print(f"\n[{cfg.ts()}] [ERROR] GeneratorEmail 缺少凭证 (surl)，无法提取验证码！")
                    return ""
                try:
                    from utils.email_providers.generator_email_service import GeneratorEmailService
                    ge_service = GeneratorEmailService(proxies=mail_proxies)

                    code = ge_service.get_verification_code(jwt)
                    if code:
                        processed_mail_ids.add(jwt)
                        print(f"\n[{cfg.ts()}] [SUCCESS] GeneratorEmail ({mask_email(email)})邮箱提取成功: {code}")
                        return code
                except Exception as e:
                    pass

            elif mode == "tempmail":
                if not jwt:
                    print(f"\n[{cfg.ts()}] [ERROR] Tempmail 缺少 token，无法提取验证码！")
                    return ""
                try:
                    from utils.email_providers.tempmail_service import TempmailService
                    tm_service = TempmailService(proxies=mail_proxies)
                    email_list = tm_service.get_inbox(jwt)

                    for msg in email_list:
                        msg_date = str(msg.get("date", 0))
                        if not msg_date or msg_date in processed_mail_ids:
                            continue

                        sender = str(msg.get("from", "")).lower()
                        subject = str(msg.get("subject", ""))
                        body = str(msg.get("body", ""))
                        html = str(msg.get("html") or "")

                        content = "\n".join([sender, subject, body, html])

                        safe_content = re.sub(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", " ", content)

                        if "openai" not in sender and "openai" not in content.lower():
                            continue

                        code = _extract_otp_code(safe_content)
                        if code:
                            processed_mail_ids.add(msg_date)
                            print(f"\n[{cfg.ts()}] [SUCCESS] Tempmail ({mask_email(email)})邮箱提取成功: {code}")
                            return code
                except Exception as e:
                    pass

            elif mode == "tempmail_org":
                if not jwt:
                    print(f"\n[{cfg.ts()}] [ERROR] TempMail.org 缺少 token，无法提取验证码！")
                    return ""
                try:
                    from utils.email_providers.tempmail_org import TempMailOrgService
                    tm_org = TempMailOrgService(proxies=mail_proxies)
                    email_list = tm_org.get_inbox(jwt)

                    for msg in email_list:
                        msg_id = str(msg.get("_id", msg.get("id", "")))
                        if not msg_id or msg_id in processed_mail_ids:
                            continue

                        subject = str(msg.get("subject", ""))
                        code = ""
                        m = re.search(r"(?<!\d)(\d{6})(?!\d)", subject)
                        if m:
                            code = m.group(1)

                        if code:
                            processed_mail_ids.add(msg_id)
                            print(f"\n[{cfg.ts()}] [SUCCESS] TempMail.org ({mask_email(email)})邮箱提取成功: {code}")
                            return code
                except Exception as e:
                    pass

            elif mode == "Gmail_OAuth":
                otp_code = get_gmail_otp_via_oauth(email, mail_proxies)
                if otp_code:
                    print(f"\n[{cfg.ts()}] [SUCCESS] Gmail OAuth ({mask_email(email)}) 提取成功: {otp_code}")
                    return otp_code

            elif mode == "imap":
                if not mail_conn:
                    try:
                        mail_conn = _create_imap_conn(proxy_str)
                        mail_conn.login(cfg.IMAP_USER, cfg.IMAP_PASS.replace(" ", ""))
                    except Exception:
                        time.sleep(5)
                        continue

                folders = ["INBOX", "Junk", '"Junk Email"', "Spam",
                           '"[Gmail]/Spam"', '"垃圾邮件"']
                found = False
                for folder in folders:
                    try:
                        mail_conn.noop()
                        status, _ = mail_conn.select(folder, readonly=True)
                        if status != "OK":
                            continue
                        status, messages = mail_conn.search(
                            None, f'(UNSEEN FROM "openai.com" TO "{email}")'
                        )
                        if status != "OK" or not messages[0]:
                            continue
                        for mail_id in reversed(messages[0].split()):
                            if mail_id in processed_mail_ids:
                                continue
                            res, data = mail_conn.fetch(mail_id, "(RFC822)")
                            for resp_part in data:
                                if not isinstance(resp_part, tuple):
                                    continue
                                import email as email_lib
                                msg = email_lib.message_from_bytes(resp_part[1])
                                subject = str(msg.get("Subject", ""))
                                if "=?UTF-8?" in subject:
                                    from email.header import decode_header as _dh
                                    dh = _dh(subject)
                                    subject = "".join(
                                        str(t[0].decode(t[1] or "utf-8")
                                            if isinstance(t[0], bytes) else t[0])
                                        for t in dh
                                    )
                                content = ""
                                if msg.is_multipart():
                                    for part in msg.walk():
                                        if part.get_content_type() == "text/plain":
                                            try:
                                                content += part.get_payload(decode=True).decode("utf-8", "ignore")
                                            except Exception:
                                                pass
                                else:
                                    content = msg.get_payload(decode=True).decode("utf-8", "ignore")
                                to_h = str(msg.get("To", "")).lower()
                                del_h = str(msg.get("Delivered-To", "")).lower()
                                tgt = email.lower()
                                if tgt not in to_h and tgt not in del_h and tgt not in content.lower():
                                    processed_mail_ids.add(mail_id)
                                    continue
                                code = _extract_otp_code(f"{subject}\n{content}")
                                if code:
                                    processed_mail_ids.add(mail_id)
                                    print(f"\n[{cfg.ts()}] [SUCCESS] IMAP ({mask_email(email)})邮箱提取成功: {code}")
                                    try:
                                        mail_conn.logout()
                                    except Exception:
                                        pass
                                    return code
                                processed_mail_ids.add(mail_id)
                        found = True
                        break
                    except imaplib.IMAP4.abort:
                        print(f"\n[{cfg.ts()}] [WARNING] IMAP 连接断开，将在下次循环重连...")
                        mail_conn = None
                        break
                    except Exception as e:
                        if "Spam" in folder:
                            print(f"\n[{cfg.ts()}] [DEBUG] 访问垃圾箱失败: {e}")
                if not found:
                    pass

            elif mode == "freemail":
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {cfg.FREEMAIL_API_TOKEN}"
                }

                res = requests.get(f"{cfg.FREEMAIL_API_URL}/api/emails",
                                   params={"mailbox": email, "limit": 20},
                                   headers=headers, proxies=mail_proxies, verify=_ssl_verify(), timeout=15)
                if res.status_code == 200:
                    raw_data = res.json()
                    emails_list = (
                        raw_data.get("data") or raw_data.get("emails") or
                        raw_data.get("messages") or raw_data.get("results") or []
                        if isinstance(raw_data, dict) else raw_data
                    )
                    if not isinstance(emails_list, list):
                        emails_list = []
                    for mail in emails_list:
                        mail_id = str(mail.get("id") or mail.get("timestamp") or
                                      mail.get("subject") or "")
                        if not mail_id or mail_id in processed_mail_ids:
                            continue
                        subject_text = str(mail.get("subject") or mail.get("title") or "")
                        code = ""
                        m = re.search(r"(?<!\d)(\d{6})(?!\d)", subject_text)
                        if m:
                            code = m.group(1)
                        if not code:
                            code = str(mail.get("code") or mail.get("verification_code") or "")
                        if not code:
                            try:
                                dr = requests.get(
                                    f"{cfg.FREEMAIL_API_URL}/api/email/{mail_id}",
                                    headers=headers, proxies=mail_proxies,
                                    verify=_ssl_verify(), timeout=15,
                                )
                                if dr.status_code == 200:
                                    d = dr.json()
                                    content = "\n".join(filter(None, [
                                        str(d.get("subject") or ""),
                                        str(d.get("content") or ""),
                                        str(d.get("html_content") or ""),
                                    ]))
                                    code = _extract_otp_code(content)
                            except Exception:
                                pass
                        if code:
                            processed_mail_ids.add(mail_id)
                            print(f"[{cfg.ts()}] [SUCCESS] freemail ({mask_email(email)})邮箱提取成功: {code}")
                            return code
            elif mode == "luckmail":
                if not jwt:
                    print(f"\n[{cfg.ts()}] [ERROR] LuckMail 缺少 token，无法提取验证码！")
                    return ""
                try:
                    from utils.email_providers.luckmail_service import LuckMailService
                    lm_service = LuckMailService(api_key=cfg.LUCKMAIL_API_KEY)

                    code = lm_service.get_code(jwt)
                    if code:
                        processed_mail_ids.add(jwt)
                        print(f"\n[{cfg.ts()}] [SUCCESS] LuckMail ({mask_email(email)})邮箱提取验证码成功: {code}")
                        return code
                except Exception as e:
                    pass
            else:
                if jwt:
                    res = requests.get(
                        f"{base_url}/api/mails",
                        params={"limit": 20, "offset": 0},
                        headers={
                            "Authorization": f"Bearer {jwt}",
                            "Content-Type": "application/json",
                            "Accept": "application/json",
                        },
                        proxies=mail_proxies, verify=_ssl_verify(), timeout=15,
                    )
                else:
                    res = requests.get(
                        f"{base_url}/admin/mails",
                        params={"limit": 20, "offset": 0, "address": email},
                        headers={"x-admin-auth": cfg.ADMIN_AUTH},
                        proxies=mail_proxies, verify=_ssl_verify(), timeout=15,
                    )
                if res.status_code != 200:
                    print(f"\n[{cfg.ts()}] [ERROR] ({mask_email(email)})邮箱接口请求失败 (HTTP {res.status_code}): {res.text}")
                    time.sleep(3)
                    continue
                results = res.json().get("results")
                if results:
                    for mail in results:
                        mail_id = mail.get("id")
                        if not mail_id or mail_id in processed_mail_ids:
                            continue
                        parsed = _extract_mail_fields(mail)
                        content = f"{parsed['subject']}\n{parsed['body']}".strip()
                        if ("openai" not in parsed["sender"].lower() and
                                "openai" not in content.lower()):
                            continue
                        m = re.search(pattern, content)
                        if m:
                            processed_mail_ids.add(mail_id)
                            print(f"[{cfg.ts()}] [SUCCESS] ({mask_email(email)})邮箱提取成功: {m.group(1)}")
                            return m.group(1)
                    pass
                else:
                    pass

        except Exception as e:
            if getattr(cfg, 'GLOBAL_STOP', False):
                return None
            if "timeout" in str(e).lower() or "time out" in str(e).lower():
                print(f"[{cfg.ts()}] [ERROR] 代理节点严重超时，终止本次邮箱查询。")
                return ""
            print(f"[{cfg.ts()}] [ERROR] 邮件循环发生异常: {str(e)}")
            import traceback
            traceback.print_exc()

        if attempt > 0 and attempt % 3 == 0:
            print(f"[{cfg.ts()}] [INFO] 仍在查询({mask_email(email)})邮箱，暂未收到验证码 (已尝试 {attempt + 1}/20)...")
        time.sleep(3)

    print(f"\n[{cfg.ts()}] [ERROR] ({mask_email(email)})邮箱接收验证码超时")
    return ""
