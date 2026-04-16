import re
from curl_cffi import requests
from utils import config as cfg


class GuerrillaMailService:
    """guerrillamail.com 临时邮箱对接"""

    DOMAINS = [
        "guerrillamail.com",
        "guerrillamailblock.com",
        "guerrillamail.info",
        "guerrillamail.biz",
        "guerrillamail.de",
        "guerrillamail.net",
        "guerrillamail.org",
        "sharklasers.com",
        "grr.la",
        "spam4.me",
    ]

    def __init__(self, proxies=None):
        self.session = requests.Session(impersonate="chrome120")
        if proxies:
            self.session.proxies = proxies if isinstance(proxies, dict) else {"http": proxies, "https": proxies}
        self.base_url = "https://api.guerrillamail.com/ajax.php"
        self.sid_token = ""
        self.headers = {
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }

    def create_email(self):
        """创建临时邮箱，返回 (email, sid_token)"""
        try:
            res = self.session.get(
                self.base_url,
                params={"f": "get_email_address", "lang": "en", "ip": "127.0.0.1", "agent": "Mozilla"},
                headers=self.headers,
                timeout=15,
            )
            if res.status_code == 200:
                data = res.json()
                email = data.get("email_addr", "")
                self.sid_token = data.get("sid_token", "")
                if email and self.sid_token:
                    return email, self.sid_token
            print(f"[{cfg.ts()}] [ERROR] GuerrillaMail 创建邮箱失败: HTTP {res.status_code}")
        except Exception as e:
            print(f"[{cfg.ts()}] [ERROR] GuerrillaMail 创建邮箱异常: {e}")
        return None, None

    def get_inbox(self, sid_token: str):
        """获取收件箱"""
        try:
            res = self.session.get(
                self.base_url,
                params={"f": "check_email", "seq": 0, "sid_token": sid_token},
                headers=self.headers,
                timeout=15,
            )
            if res.status_code == 200:
                data = res.json()
                return data.get("list", [])
        except Exception as e:
            print(f"[{cfg.ts()}] [ERROR] GuerrillaMail 获取收件箱异常: {e}")
        return []

    def get_email_detail(self, email_id: str, sid_token: str):
        """获取单封邮件完整内容"""
        try:
            res = self.session.get(
                self.base_url,
                params={"f": "fetch_email", "email_id": email_id, "sid_token": sid_token},
                headers=self.headers,
                timeout=15,
            )
            if res.status_code == 200:
                return res.json()
        except Exception as e:
            print(f"[{cfg.ts()}] [ERROR] GuerrillaMail 读取邮件异常: {e}")
        return None

    def get_verification_code(self, sid_token: str) -> str:
        """从收件箱提取 OpenAI 验证码"""
        mail_list = self.get_inbox(sid_token)
        for mail in mail_list:
            subject = str(mail.get("mail_subject", ""))
            from_addr = str(mail.get("mail_from", "")).lower()
            excerpt = str(mail.get("mail_excerpt", ""))

            if "openai" not in from_addr and "openai" not in subject.lower():
                continue

            code = self._extract_code(f"{subject}\n{excerpt}")
            if code:
                return code

            mail_id = mail.get("mail_id")
            if mail_id:
                detail = self.get_email_detail(str(mail_id), sid_token)
                if detail:
                    body = detail.get("mail_body", "")
                    code = self._extract_code(f"{subject}\n{body}")
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
