import re
import json
from curl_cffi import requests
from utils import config as cfg


class M2UService:
    """m2u.io (MailToYou) 临时邮箱对接"""

    def __init__(self, proxies=None):
        self.session = requests.Session(impersonate="chrome120")
        if proxies:
            self.session.proxies = proxies if isinstance(proxies, dict) else {"http": proxies, "https": proxies}
        self.base_url = "https://api.m2u.io/v1"
        self.headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": "https://m2u.io",
            "Referer": "https://m2u.io/zh/",
        }

    def create_email(self):
        """自动创建随机邮箱，返回 (email, json_token_string)"""
        url = f"{self.base_url}/mailboxes/auto"
        try:
            res = self.session.post(url, json={}, headers=self.headers, timeout=15)
            if res.status_code == 200:
                data = res.json()
                mailbox = data.get("mailbox")
                if mailbox:
                    email = f"{mailbox['local_part']}@{mailbox['domain']}"
                    token_data = {
                        "token": mailbox["token"],
                        "view_token": mailbox["view_token"],
                    }
                    return email, json.dumps(token_data)
            print(f"[{cfg.ts()}] [ERROR] M2U 创建邮箱失败: HTTP {res.status_code} - {res.text[:200]}")
        except Exception as e:
            print(f"[{cfg.ts()}] [ERROR] M2U 创建邮箱异常: {e}")
        return None, None

    def get_inbox(self, token_data_str: str):
        """获取收件箱列表"""
        try:
            td = json.loads(token_data_str) if isinstance(token_data_str, str) else token_data_str
            token = td["token"]
            view_token = td["view_token"]
        except (json.JSONDecodeError, KeyError):
            return []

        url = f"{self.base_url}/mailboxes/{token}/messages"
        params = {"view": view_token}
        try:
            res = self.session.get(url, params=params, headers=self.headers, timeout=15)
            if res.status_code == 200:
                data = res.json()
                return data.get("messages", [])
        except Exception as e:
            print(f"[{cfg.ts()}] [ERROR] M2U 获取收件箱异常: {e}")
        return []

    def get_message(self, token_data_str: str, message_id: str):
        """获取单封邮件完整内容"""
        try:
            td = json.loads(token_data_str) if isinstance(token_data_str, str) else token_data_str
            token = td["token"]
            view_token = td["view_token"]
        except (json.JSONDecodeError, KeyError):
            return None

        url = f"{self.base_url}/mailboxes/{token}/messages/{message_id}"
        params = {"view": view_token}
        try:
            res = self.session.get(url, params=params, headers=self.headers, timeout=15)
            if res.status_code == 200:
                data = res.json()
                return data.get("message")
        except Exception as e:
            print(f"[{cfg.ts()}] [ERROR] M2U 读取邮件异常: {e}")
        return None

    def get_verification_code(self, token_data_str: str) -> str:
        """从收件箱提取 OpenAI 验证码"""
        messages = self.get_inbox(token_data_str)
        for msg in messages:
            from_addr = str(msg.get("from_addr", "")).lower()
            subject = str(msg.get("subject", ""))

            if "openai" not in from_addr and "openai" not in subject.lower():
                continue

            code = self._extract_code(subject)
            if code:
                return code

            msg_id = msg.get("id")
            if msg_id:
                detail = self.get_message(token_data_str, msg_id)
                if detail:
                    text = detail.get("text_body", "")
                    html = detail.get("html_body", "")
                    code = self._extract_code(f"{subject}\n{text}\n{html}")
                    if code:
                        return code
        return ""

    @staticmethod
    def _extract_code(text: str) -> str:
        m = re.search(r"Your ChatGPT code is (\d{6})", text, re.I)
        if m:
            return m.group(1)
        m = re.search(r"(?:openai|chatgpt)[\s\S]{0,200}?(\d{6})", text, re.I)
        if m:
            return m.group(1)
        if "openai" in text.lower() or "chatgpt" in text.lower():
            codes = re.findall(r"\b(\d{6})\b", text)
            if codes:
                return codes[-1]
        return ""
