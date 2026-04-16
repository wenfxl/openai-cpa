import random
import re
import string
from curl_cffi import requests
from utils import config as cfg


class TempMailPlusService:
    """tempmail.plus 非官方 Web API 对接"""

    DOMAINS = [
        "mailto.plus",
        "fexpost.com",
        "fexbox.org",
        "mailbox.in.ua",
        "rover.info",
        "chitthi.in",
        "fextemp.com",
        "any.pink",
        "merepost.com",
    ]

    def __init__(self, proxies=None):
        self.session = requests.Session(impersonate="chrome120")
        if proxies:
            self.session.proxies = proxies if isinstance(proxies, dict) else {"http": proxies, "https": proxies}
        self.base_url = "https://tempmail.plus/api"
        self.headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://tempmail.plus/zh/",
        }

    def create_email(self):
        """生成随机邮箱地址（无需服务端注册，直接使用即可）"""
        username = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
        domain = random.choice(self.DOMAINS)
        email = f"{username}@{domain}"
        return email, email

    def get_inbox(self, email: str):
        """获取收件箱列表"""
        url = f"{self.base_url}/mails"
        params = {"email": email}
        try:
            res = self.session.get(url, params=params, headers=self.headers, timeout=15)
            if res.status_code == 200:
                data = res.json()
                if data.get("result"):
                    return data.get("mail_list", [])
        except Exception as e:
            print(f"[{cfg.ts()}] [ERROR] TempMailPlus 收件箱请求异常: {e}")
        return []

    def get_mail_content(self, mail_id: int, email: str):
        """获取单封邮件完整内容"""
        url = f"{self.base_url}/mails/{mail_id}"
        params = {"email": email}
        try:
            res = self.session.get(url, params=params, headers=self.headers, timeout=15)
            if res.status_code == 200:
                data = res.json()
                if data.get("result"):
                    return data
        except Exception as e:
            print(f"[{cfg.ts()}] [ERROR] TempMailPlus 读取邮件异常: {e}")
        return None

    def get_verification_code(self, email: str) -> str:
        """从收件箱中提取 OpenAI 6位验证码"""
        mail_list = self.get_inbox(email)
        for mail in mail_list:
            subject = str(mail.get("subject", ""))
            from_mail = str(mail.get("from_mail", "")).lower()

            if "openai" not in from_mail and "openai" not in subject.lower():
                continue

            # 先从标题提取
            code = self._extract_code(subject)
            if code:
                return code

            # 标题没有则读正文
            mail_id = mail.get("mail_id")
            if mail_id:
                detail = self.get_mail_content(mail_id, email)
                if detail:
                    html = detail.get("html", "")
                    text = detail.get("text", "")
                    code = self._extract_code(f"{subject}\n{text}\n{html}")
                    if code:
                        return code
        return ""

    @staticmethod
    def _extract_code(text: str) -> str:
        """提取 6 位数字验证码"""
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
