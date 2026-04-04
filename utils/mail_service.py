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


_CM_TOKEN_CACHE: Optional[str] = None

_thread_data = threading.local()
_orig_sleep = time.sleep

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


def mask_email(text: str) -> str:
    """日志脱敏：隐藏邮箱域名部分。"""
    if not cfg.ENABLE_EMAIL_MASKING or not text:
        return text
    if "@" in text:
        prefix, _ = text.split("@", 1)
        return f"{prefix}@***.***"

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
        return f"token_{ep[:len(ep)//2]}***_{ts_}.json"
    if len(text) > 8 and ".json" in text:
        name_part = text.replace(".json", "")
        return f"{name_part[:len(name_part)//2]}***.json"
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

def get_email_and_token(proxies: Any = None) -> tuple:
    """兼容五种邮箱模式的地址创建，返回 (email, token_or_id)。"""
    if getattr(cfg, 'GLOBAL_STOP', False): return None, None
    _thread_data.last_attempt_email = None
    
    letters = "".join(random.choices(string.ascii_lowercase, k=5))
    digits  = "".join(random.choices(string.digits, k=random.randint(1, 3)))
    suffix  = "".join(random.choices(string.ascii_lowercase, k=random.randint(1, 3)))
    prefix  = letters + digits + suffix

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

    if mode == "luckmail":
        try:
            from utils.luckmail_service import LuckMailService
            lm_service = LuckMailService(
                api_key=cfg.LUCKMAIL_API_KEY,
                preferred_domain=getattr(cfg, 'LUCKMAIL_PREFERRED_DOMAIN', ""),
                proxies=mail_proxies,
                email_type=getattr(cfg, 'LUCKMAIL_EMAIL_TYPE', "ms_graph"),
                variant_mode=getattr(cfg, 'LUCKMAIL_VARIANT_MODE', "")
            )

            email, token, p_id = None, None, None
            tag_id = getattr(cfg, 'LUCKMAIL_TAG_ID', None)
            if getattr(cfg, 'LUCKMAIL_REUSE_PURCHASED', False):
                if not tag_id:
                    tag_id = lm_service.get_or_create_tag_id("已使用")
                    if tag_id:
                        cfg.LUCKMAIL_TAG_ID = tag_id
                        try:
                            import yaml
                            with open("config.yaml", "r", encoding="utf-8") as f:
                                y = yaml.safe_load(f) or {}
                            y.setdefault("luckmail", {})["tag_id"] = tag_id
                            with open("config.yaml", "w", encoding="utf-8") as f:
                                yaml.dump(y, f, allow_unicode=True, sort_keys=False)
                        except:
                            pass

                print(f"[{cfg.ts()}] [INFO] LuckMail 正在尝试从历史库复用邮箱...")
                email, token, p_id = lm_service.get_random_purchased_email(tag_id=tag_id)

                if email and token:
                    print(f"[{cfg.ts()}] [SUCCESS] LuckMail 成功复用历史邮箱: ({mask_email(email)})")
                    if p_id and tag_id: lm_service.set_email_tag(p_id, tag_id)
                    return email, token
                else:
                    print(f"[{cfg.ts()}] [WARNING] 未找到可复用的号，切换至购号流程...")
            email, token, p_id = lm_service.get_email_and_token(auto_tag=False)

            if email and token:
                print(f"[{cfg.ts()}] [INFO] LuckMail 成功购买新邮箱: ({mask_email(email)})")
                if p_id:
                    if not tag_id: tag_id = lm_service.get_or_create_tag_id("已使用")
                    if tag_id:
                        lm_service.set_email_tag(p_id, tag_id)

                return email, token

        except Exception as e:
            print(f"[{cfg.ts()}] [ERROR] LuckMail 流程异常: {e}")
            return None, None

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
            level = getattr(cfg, 'SUB_DOMAIN_LEVEL', 1) 
            
            random_parts = [''.join(random.choices(string.ascii_lowercase + string.digits, k=8)) for _ in range(level)]
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
                res = requests.post(f"{cfg.FREEMAIL_API_URL.rstrip('/')}/api/create", 
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
                print(f"[{cfg.ts()}] [WARNING] cloudflare_temp_email邮箱申请失败 (尝试 {attempt+1}/5): {res.text}")
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
    subject  = str(mail.get("subject") or mail.get("title") or "").strip()
    body_text = str(
        mail.get("text") or mail.get("body") or
        mail.get("content") or mail.get("html") or ""
    ).strip()
    raw = str(mail.get("raw") or "").strip()
    if raw:
        try:
            msg = message_from_string(raw, policy=email_policy)
            sender    = sender    or _decode_mime_header(msg.get("From", ""))
            subject   = subject   or _decode_mime_header(msg.get("Subject", ""))
            parsed    = _extract_body_from_message(msg)
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

class ProxiedIMAP4_SSL(imaplib.IMAP4_SSL):
    def __init__(self, host, port, proxy_host, proxy_port, proxy_type, **kwargs):
        self.proxy_host  = proxy_host
        self.proxy_port  = proxy_port
        self.proxy_type  = proxy_type
        self.timeout_val = kwargs.pop("timeout", 60)
        super().__init__(host, port, **kwargs)

    def _create_socket(self, timeout):
        sock = socks.socksocket()
        sock.set_proxy(self.proxy_type, self.proxy_host, self.proxy_port)
        sock.settimeout(self.timeout_val)
        sock.connect((self.host, self.port))
        return sock


def _create_imap_conn():
    """建立 IMAP 连接（可选代理穿透）。"""
    default_proxy = cfg.DEFAULT_PROXY
    if (cfg.USE_PROXY_FOR_EMAIL and default_proxy and
            cfg.IMAP_SERVER.lower() == "imap.gmail.com"):
        try:
            parsed     = urlparse(default_proxy)
            proxy_host = parsed.hostname
            proxy_port = parsed.port or 80
            proxy_type = (socks.HTTP if parsed.scheme.lower() in ("http", "https")
                          else socks.SOCKS5)
            original_socket = socket.socket
            try:
                socks.set_default_proxy(proxy_type, proxy_host, proxy_port)
                socket.socket = socks.socksocket
                conn = imaplib.IMAP4_SSL(cfg.IMAP_SERVER, cfg.IMAP_PORT, timeout=20)
                return conn
            finally:
                socket.socket = original_socket
        except Exception as e:
            print(f"\n[{cfg.ts()}] [ERROR] IMAP 代理注入失败: {e}，回退到直连。")
            return imaplib.IMAP4_SSL(cfg.IMAP_SERVER, cfg.IMAP_PORT, timeout=15)
    return imaplib.IMAP4_SSL(cfg.IMAP_SERVER, cfg.IMAP_PORT, timeout=15)

def get_oai_code(
    email: str,
    jwt: str = "",
    proxies: Any = None,
    processed_mail_ids: set = None,
    pattern: str = OTP_CODE_PATTERN,
) -> str:
    """轮询各邮箱服务商收取 OpenAI 验证码，返回 6 位字符串或空串。"""
    mailbox_id = jwt
    mail_proxies = proxies if cfg.USE_PROXY_FOR_EMAIL else None
    base_url = cfg.GPTMAIL_BASE.rstrip("/")
    mode = cfg.EMAIL_API_MODE

    print(f"\n[{cfg.ts()}] [INFO] 等待接收验证码 ({mask_email(email)}) ", end="", flush=True)

    if processed_mail_ids is None:
        processed_mail_ids = set()

    mail_conn = None
    if mode == "imap":
        try:
            mail_conn = _create_imap_conn()
            mail_conn.login(cfg.IMAP_USER, cfg.IMAP_PASS.replace(" ", ""))
        except Exception as e:
            print(f"\n[{cfg.ts()}] [ERROR] IMAP 初始登录失败: {e}")
            mail_conn = None

    for _ in range(20):
        if getattr(cfg, 'GLOBAL_STOP', False): return ""
        try:
            if mode == "mail_curl":
                inbox_url = (f"{cfg.MC_API_BASE}/api/inbox"
                             f"?key={cfg.MC_KEY}&mailbox_id={mailbox_id}")
                res = requests.get(inbox_url, proxies=mail_proxies,
                                   verify=_ssl_verify(), timeout=10)
                if res.status_code == 200:
                    for mail_item in (res.json() or []):
                        m_id   = mail_item.get("mail_id")
                        s_name = mail_item.get("sender_name", "").lower()
                        if m_id and m_id not in processed_mail_ids and "openai" in s_name:
                            detail_res = requests.get(
                                f"{cfg.MC_API_BASE}/api/mail"
                                f"?key={cfg.MC_KEY}&id={m_id}",
                                proxies=mail_proxies, verify=_ssl_verify(), timeout=10,
                            )
                            if detail_res.status_code == 200:
                                d = detail_res.json()
                                body = (f"{d.get('subject','')}\n"
                                        f"{d.get('content','')}\n"
                                        f"{d.get('html','')}")
                                code = _extract_otp_code(body)
                                if code:
                                    processed_mail_ids.add(m_id)
                                    print(f"\n[{cfg.ts()}] [SUCCESS] 发现验证码: {code}")
                                    return code

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
                            content = f"{m.get('subject','')}\n{m.get('text','')}"
                            if ("openai" in m.get("sendEmail", "").lower() or
                                    "openai" in content.lower()):
                                code = _extract_otp_code(content)
                                if code:
                                    processed_mail_ids.add(m_id)
                                    print(f"\n[{cfg.ts()}] [SUCCESS] CloudMail 提取验证码成功: {code}")
                                    return code

            elif mode == "imap":
                if not mail_conn:
                    try:
                        mail_conn = imaplib.IMAP4_SSL(cfg.IMAP_SERVER, cfg.IMAP_PORT, timeout=15)
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
                                to_h  = str(msg.get("To", "")).lower()
                                del_h = str(msg.get("Delivered-To", "")).lower()
                                tgt   = email.lower()
                                if tgt not in to_h and tgt not in del_h and tgt not in content.lower():
                                    processed_mail_ids.add(mail_id)
                                    continue
                                code = _extract_otp_code(f"{subject}\n{content}")
                                if code:
                                    processed_mail_ids.add(mail_id)
                                    print(f"\n[{cfg.ts()}] [SUCCESS] 验证码: {code}")
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
                    print(".", end="", flush=True)

            elif mode == "freemail":
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {cfg.FREEMAIL_API_TOKEN}"
                }

                res = requests.get(f"{cfg.FREEMAIL_API_URL.rstrip('/')}/api/emails", params={"mailbox": email, "limit": 20},
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
                                    f"{cfg.FREEMAIL_API_URL.rstrip('/')}/api/email/{mail_id}",
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
                            print(f" 提取成功: {code}")
                            return code
            if mode == "luckmail":
                if not jwt:
                    print(f"\n[{cfg.ts()}] [ERROR] LuckMail 缺少 token，无法提取验证码！")
                    return ""
                try:
                    from utils.luckmail_service import LuckMailService
                    lm_service = LuckMailService(api_key=cfg.LUCKMAIL_API_KEY)

                    code = lm_service.get_code(jwt)
                    if code:
                        processed_mail_ids.add(jwt)
                        print(f"\n[{cfg.ts()}] [SUCCESS] LuckMail 提取验证码成功: {code}")
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
                    print(f"\n[{cfg.ts()}] [ERROR] 邮箱接口请求失败 (HTTP {res.status_code}): {res.text}")
                    time.sleep(3)
                    continue
                results = res.json().get("results")
                if results:
                    for mail in results:
                        mail_id = mail.get("id")
                        if not mail_id or mail_id in processed_mail_ids:
                            continue
                        parsed  = _extract_mail_fields(mail)
                        content = f"{parsed['subject']}\n{parsed['body']}".strip()
                        if ("openai" not in parsed["sender"].lower() and
                                "openai" not in content.lower()):
                            continue
                        m = re.search(pattern, content)
                        if m:
                            processed_mail_ids.add(mail_id)
                            print(f" 提取成功: {m.group(1)}")
                            return m.group(1)
                    print(".", end="", flush=True)
                else:
                    print(".", end="", flush=True)

        except Exception:
            print(".", end="", flush=True)

        time.sleep(3)

    print(f"\n[{cfg.ts()}] [ERROR] 接收验证码超时")
    return ""
