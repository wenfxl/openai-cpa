import json
import random
import string
import time
import uuid
import threading
import imaplib
import base64
import email as email_lib
from email.header import decode_header
from typing import List, Optional, Dict, Any
from curl_cffi import requests as cffi_requests
from utils import config as cfg
from utils import db_manager
_fission_lock = threading.Lock()

class LocalMicrosoftService:
    def __init__(self, proxies: Optional[Dict[str, str]] = None):
        self.proxies = proxies
        self.token_url = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
        self.graph_base_url = "https://graph.microsoft.com/v1.0/me"

    def generate_suffix_v2(self):
        return uuid.uuid4().hex[:8]

    def get_unused_mailbox(self) -> Optional[dict]:
        """核心逻辑"""
        if getattr(cfg, "LOCAL_MS_ENABLE_FISSION", False):
            master_email = getattr(cfg, "LOCAL_MS_MASTER_EMAIL", "").strip()
            if master_email and "@" in master_email:
                random_suffix = self.generate_suffix_v2()
                user_part, domain_part = master_email.split("@", 1)
                return {
                    "id": "manual_config",
                    "email": f"{user_part}+{random_suffix}@{domain_part}",
                    "master_email": master_email,
                    "is_raw_trial": False,
                    "client_id": getattr(cfg, "LOCAL_MS_CLIENT_ID", ""),
                    "refresh_token": getattr(cfg, "LOCAL_MS_REFRESH_TOKEN", ""),
                    "assigned_at": time.time()
                }

        if getattr(cfg, "LOCAL_MS_POOL_FISSION", False):
            with _fission_lock:
                mailbox_data = db_manager.get_mailbox_for_pool_fission()
                if mailbox_data:
                    master_email = mailbox_data["email"]
                    is_raw = (mailbox_data.get("retry_master") == 1)

                    if is_raw:
                        target_email = master_email
                        db_manager.clear_retry_master_status(master_email)
                    else:
                        random_suffix = self.generate_suffix_v2()
                        user_part, domain_part = master_email.split("@", 1)
                        target_email = f"{user_part}+{random_suffix}@{domain_part}"

                    return {
                        "id": mailbox_data["id"],
                        "email": target_email,
                        "master_email": master_email,
                        "is_raw_trial": is_raw,
                        "client_id": mailbox_data.get("client_id", ""),
                        "refresh_token": mailbox_data.get("refresh_token", ""),
                        "assigned_at": time.time()
                    }
        mailbox = db_manager.get_and_lock_unused_local_mailbox()
        if mailbox:
            res = dict(mailbox)
            res["master_email"] = res["email"]
            res["is_raw_trial"] = True
            res["assigned_at"] = time.time()
            return res

        return None

    def _exchange_refresh_token(self, mailbox: dict) -> str:
        refresh_token = mailbox.get("refresh_token")
        BUILTIN_CLIENT_ID = "7feada80-d946-4d06-b134-73afa3524fb7"
        db_client_id = mailbox.get("client_id")
        cfg_client_id = getattr(cfg, "LOCAL_MS_CLIENT_ID", "")
        client_id = str(db_client_id or cfg_client_id or BUILTIN_CLIENT_ID).strip()

        if not refresh_token or not client_id:
            raise ValueError(f"[{cfg.ts()}] [ERROR] 缺失凭据，无法执行令牌交换")

        scope_graph = "https://graph.microsoft.com/.default offline_access"
        scope_fallback = "offline_access"

        def _do_token_request(current_scope):
            payload = {
                "client_id": client_id,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "scope": current_scope
            }
            return cffi_requests.post(
                self.token_url,
                data=payload,
                proxies=self.proxies,
                timeout=15,
                impersonate="chrome110"
            )

        resp = _do_token_request(scope_graph)
        data = resp.json()
        if resp.status_code != 200 and ("AADSTS70000" in str(data) or "invalid_scope" in str(data)):
            print(f"[{cfg.ts()}] [INFO] {mailbox['email']}] ⚠️ Graph 未授权，回退到基础/IMAP 兼容模式...")
            resp = _do_token_request(scope_fallback)
            data = resp.json()
            mailbox['token_type'] = 'legacy_imap'
        else:
            returned_scope = str(data.get("scope", "")).lower()
            if "mail.read" in returned_scope:
                mailbox['token_type'] = 'graph_full'
            elif "imap.accessasuser.all" in returned_scope:
                mailbox['token_type'] = 'outlook_legacy'
            else:
                mailbox['token_type'] = 'graph_full'
        if resp.status_code == 200 and "access_token" in data:
            new_rt = data.get("refresh_token")
            if new_rt and new_rt != refresh_token and mailbox.get("id") != "fission":
                try:
                    db_manager.update_local_mailbox_refresh_token(mailbox["email"], new_rt)
                except:
                    pass
            return data["access_token"]
        else:
            err_msg = data.get('error_description', data)
            raise RuntimeError(f"[{cfg.ts()}] [ERROR] 双令牌模式尝试均失败: {err_msg}")

    def fetch_openai_messages(self, mailbox: dict) -> List[Dict[str, Any]]:
        all_msgs = []
        try:
            access_token = self._exchange_refresh_token(mailbox)
            if mailbox.get('token_type') == 'outlook_legacy':
                return self._fetch_via_imap(mailbox)
            url = f"{self.graph_base_url}/messages"
            params = {
                "$select": "subject,from,toRecipients,receivedDateTime,body",
                "$orderby": "receivedDateTime desc",
                "$top": 20
            }
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json"
            }
            resp = cffi_requests.get(url, params=params, headers=headers, proxies=self.proxies, timeout=15,
                                     impersonate="chrome110")
            if resp.status_code == 200:
                raw_msgs = resp.json().get("value", [])
                if not raw_msgs:
                    pass
                for i, m in enumerate(raw_msgs):
                    subject = m.get('subject', '无主题')
                    sender = m.get('from', {}).get('emailAddress', {}).get('address', '未知发件人')
                    if "openai" in sender.lower() or "openai" in subject.lower():
                        all_msgs.append(m)
                return all_msgs
            else:
                print(f"[{cfg.ts()}] [ERROR] 扫信接口请求失败: {resp.status_code} | {resp.text}")
        except Exception as e:
            print(f"[{cfg.ts()}] [DEBUG-GRAPH] 扫信模块严重错误: {e}", flush=True)
        return all_msgs

    def _fetch_via_imap(self, mailbox: dict) -> List[Dict[str, Any]]:
        all_msgs = []
        login_email = mailbox.get("master_email") or mailbox.get("email")
        target_email = mailbox.get("email").lower()
        refresh_token = mailbox.get("refresh_token")

        BUILTIN_CLIENT_ID = "7feada80-d946-4d06-b134-73afa3524fb7"
        client_id = str(mailbox.get("client_id") or getattr(cfg, "LOCAL_MS_CLIENT_ID", "") or BUILTIN_CLIENT_ID).strip()

        try:
            payload = {
                "client_id": client_id,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "scope": "https://outlook.office.com/IMAP.AccessAsUser.All offline_access"
            }
            resp = cffi_requests.post(self.token_url, data=payload, proxies=self.proxies, timeout=15,
                                      impersonate="chrome110")
            data = resp.json()
            if resp.status_code != 200: return all_msgs

            access_token = data["access_token"]
            auth_string = f"user={login_email}\x01auth=Bearer {access_token}\x01\x01"

            imap = imaplib.IMAP4_SSL("outlook.office365.com", 993)
            imap.authenticate("XOAUTH2", lambda _: auth_string.encode("ascii"))

            from email.utils import parsedate_to_datetime

            for folder in ["INBOX", "Junk"]:
                status, _ = imap.select(folder, readonly=True)
                if status != 'OK': continue

                _, search_data = imap.search(None, "ALL")
                uids = search_data[0].split()
                if not uids: continue

                for uid in reversed(uids[-10:]):
                    _, raw = imap.fetch(uid, "(RFC822)")
                    if not raw or not raw[0]: continue

                    msg = email_lib.message_from_bytes(raw[0][1])
                    try:
                        date_str = msg.get("Date")
                        dt = parsedate_to_datetime(date_str)
                        iso_date = dt.isoformat()
                    except:
                        iso_date = ""

                    to_header = str(msg.get("To", "")).lower()
                    to_recipients = [{"emailAddress": {"address": to_header}}]

                    subject_raw, enc = decode_header(msg.get("Subject", ""))[0]
                    subject = subject_raw.decode(enc or "utf-8") if isinstance(subject_raw, bytes) else (
                                subject_raw or "无主题")

                    body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            if part.get_content_type() == "text/html":
                                body = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8",
                                                                            errors="replace")
                                break
                    else:
                        body = msg.get_payload(decode=True).decode(msg.get_content_charset() or "utf-8",
                                                                   errors="replace")

                    all_msgs.append({
                        "id": f"imap_{uid.decode()}",
                        "subject": subject,
                        "from": {"emailAddress": {"address": str(msg.get("From", "")).lower()}},
                        "toRecipients": to_recipients,
                        "receivedDateTime": iso_date,
                        "body": {"content": body}
                    })

            imap.logout()
        except:
            pass

        return all_msgs