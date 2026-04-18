import json
import random
import string
import time
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


class MailboxAbuseModeError(RuntimeError):
    def __init__(self, email: str):
        super().__init__(f"[{cfg.ts()}] [WARNING] Microsoft 邮箱已进入 service abuse mode，已自动停用: {email}")
        self.email = email


class LocalMicrosoftService:
    _MYSTIC_NAMES = [
        "leo", "nova", "kai", "luna", "milo", "iris", "axel", "zara"
    ]
    _MYSTIC_NOUNS = [
        "fox", "river", "comet", "lotus", "cloud", "ember", "aurora", "tiger"
    ]

    def __init__(self, proxies: Optional[Dict[str, str]] = None):
        self.proxies = proxies
        self.token_url = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
        self.graph_base_url = "https://graph.microsoft.com/v1.0/me"

    def _resolve_suffix_mode(self) -> str:
        mode = str(getattr(cfg, "LOCAL_MS_SUFFIX_MODE", "fixed") or "fixed").strip().lower()
        if mode not in {"fixed", "range", "mystic"}:
            return "fixed"
        return mode

    def _resolve_suffix_bounds(self, user_part: str) -> tuple[int, int]:
        try:
            min_len = int(getattr(cfg, "LOCAL_MS_SUFFIX_LEN_MIN", 8) or 8)
        except Exception:
            min_len = 8
        try:
            max_len = int(getattr(cfg, "LOCAL_MS_SUFFIX_LEN_MAX", min_len) or min_len)
        except Exception:
            max_len = min_len

        min_len = max(8, min(32, min_len))
        max_len = max(8, min(32, max_len))
        if max_len < min_len:
            max_len = min_len

        # RFC local-part max length is 64, include plus separator itself.
        user_len = len(str(user_part or ""))
        available = 64 - user_len - 1
        if available <= 0:
            return 0, 0

        min_len = max(1, min(min_len, available))
        max_len = max(min_len, min(max_len, available))
        return min_len, max_len

    def _random_hex(self, length: int) -> str:
        return "".join(random.choices("0123456789abcdef", k=max(1, int(length))))

    def _build_mystic_seed(self) -> str:
        name = random.choice(self._MYSTIC_NAMES)
        noun = random.choice(self._MYSTIC_NOUNS)
        mmdd = f"{random.randint(1, 12):02d}{random.randint(1, 28):02d}"
        yyyy = str(random.randint(1990, 2012))
        return random.choice([
            f"{name}{noun}{mmdd}",
            f"{noun}{name}{mmdd}",
            f"{name}{mmdd}{noun}",
            f"{name}{noun}{yyyy}",
        ]).lower()

    def generate_suffix_v2(self, user_part: str = ""):
        mode = self._resolve_suffix_mode()
        min_len, max_len = self._resolve_suffix_bounds(user_part)
        if max_len <= 0:
            return ""
        target_len = min_len if mode == "fixed" else random.randint(min_len, max_len)

        if mode == "mystic":
            suffix = "".join(ch for ch in self._build_mystic_seed() if ch.isalnum())
            if len(suffix) < target_len:
                suffix += "".join(random.choices(string.ascii_lowercase + string.digits, k=target_len - len(suffix)))
            return suffix[:target_len]

        return self._random_hex(target_len)

    def get_unused_mailbox(self) -> Optional[dict]:
        """核心逻辑"""
        if getattr(cfg, "LOCAL_MS_ENABLE_FISSION", False):
            master_email = getattr(cfg, "LOCAL_MS_MASTER_EMAIL", "").strip()
            if master_email and "@" in master_email:
                user_part, domain_part = master_email.split("@", 1)
                random_suffix = self.generate_suffix_v2(user_part=user_part)
                target_email = f"{user_part}+{random_suffix}@{domain_part}" if random_suffix else master_email
                return {
                    "id": "manual_config",
                    "email": target_email,
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
                        user_part, domain_part = master_email.split("@", 1)
                        random_suffix = self.generate_suffix_v2(user_part=user_part)
                        target_email = f"{user_part}+{random_suffix}@{domain_part}" if random_suffix else master_email

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
            err_text = str(err_msg)
            if "AADSTS70000" in err_text and "service abuse mode" in err_text.lower():
                target_email = mailbox.get("master_email") or mailbox.get("email")
                if target_email:
                    try:
                        db_manager.update_local_mailbox_status(target_email, 3)
                    except:
                        pass
                    raise MailboxAbuseModeError(target_email)
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
        except MailboxAbuseModeError as e:
            mailbox["_polling_stopped"] = "abuse_mode"
            print(str(e), flush=True)
        except Exception as e:
            print(f"[{cfg.ts()}] [DEBUG-GRAPH] 扫信模块严重错误: {e}", flush=True)
        return all_msgs

    def _fetch_via_imap(self, mailbox: dict, headers_only: bool = False) -> List[Dict[str, Any]]:
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

                status, search_data = imap.search(None, 'FROM', '"openai.com"')
                if status != 'OK' or not search_data[0]: continue

                uids = search_data[0].split()
                if not uids: continue

                for uid in reversed(uids[-10:]):
                    fetch_query = "(RFC822.HEADER)" if headers_only else "(RFC822)"
                    _, raw = imap.fetch(uid, fetch_query)
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

                    if not headers_only:
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

    def get_snapshot_ids(self, mailbox: dict, target_email: str) -> set:
        snapshot = set()
        tgt = target_email.lower().strip()
        from utils.email_providers.mail_service import mask_email
        print(f"[{cfg.ts()}] [INFO] 🛰️ 正在为目标 {mask_email(tgt)} 执行历史邮件封存...")

        try:
            access_token = self._exchange_refresh_token(mailbox)
            current_type = mailbox.get('token_type', 'unknown')

            if current_type == 'graph_full':
                url = f"{self.graph_base_url}/messages"
                params = {
                    "$select": "id,toRecipients,subject",
                    "$filter": "contains(from/emailAddress/address, 'openai.com')",
                    "$top": 15
                }
                headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

                resp = cffi_requests.get(url, params=params, headers=headers, proxies=self.proxies, timeout=10,
                                         impersonate="chrome110")

                if resp.status_code == 200:
                    raw_value = resp.json().get("value", [])
                    for m in raw_value:
                        recs = [r.get('emailAddress', {}).get('address', '').lower() for r in m.get('toRecipients', [])]
                        if tgt in recs:
                            snapshot.add(m.get('id'))
                else:
                    current_type = 'outlook_legacy'

            if current_type in ['outlook_legacy', 'legacy_imap']:
                messages = self._fetch_via_imap(mailbox, headers_only=True)

                for m in messages:
                    recs = [r.get('emailAddress', {}).get('address', '').lower() for r in m.get('toRecipients', [])]
                    if tgt in recs:
                        snapshot.add(m.get('id'))

        except Exception as e:
            print(f"[{cfg.ts()}] [DEBUG] 💥 发生严重错误: {str(e)}")

        return snapshot
