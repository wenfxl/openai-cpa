import re
import json
import html
import random
import string
from curl_cffi import requests
from utils import config as cfg


class BeeInboxService:
    """beeinbox.com 临时邮箱对接 (基于 Livewire 框架)"""

    DOMAINS = [
        "beeinbox.com",
        "superbee.my",
        "ussteel.xyz",
        "beeinbox.edu.pl",
        "chinasteel.xyz",
        "obee.info",
    ]

    def __init__(self, proxies=None):
        self.session = requests.Session(impersonate="chrome120")
        if proxies:
            self.session.proxies = proxies if isinstance(proxies, dict) else {"http": proxies, "https": proxies}
        self.base_url = "https://beeinbox.com"
        self.headers = {
            "Accept": "text/html, application/xhtml+xml",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        }
        self._token = None
        self._actions_fp = None
        self._actions_memo = None
        self._app_fp = None
        self._app_memo = None

    def _init_session(self):
        """首次请求获取 CSRF token 和 Livewire 组件数据"""
        r = self.session.get(self.base_url + "/", headers=self.headers, timeout=15)
        if r.status_code != 200:
            raise Exception(f"beeinbox 首页请求失败: HTTP {r.status_code}")

        m = re.search(r"window\.livewire_token = '([^']+)'", r.text)
        if not m:
            raise Exception("beeinbox 未找到 livewire token")
        self._token = m.group(1)

        init_datas = re.findall(r'wire:initial-data="([^"]+)"', r.text)
        for d in init_datas:
            data = json.loads(html.unescape(d))
            name = data["fingerprint"]["name"]
            if name == "frontend.actions":
                self._actions_fp = data["fingerprint"]
                self._actions_memo = data["serverMemo"]
            elif name == "frontend.app":
                self._app_fp = data["fingerprint"]
                self._app_memo = data["serverMemo"]

        if not self._actions_fp or not self._app_fp:
            raise Exception("beeinbox 未找到 Livewire 组件")

    @staticmethod
    def _merge_memo(original_memo, resp_memo):
        """合并 Livewire 响应中的差异 serverMemo 到原始 memo"""
        if not resp_memo:
            return original_memo
        merged = json.loads(json.dumps(original_memo))  # deep copy
        # 合并 data 字段
        if "data" in resp_memo:
            if "data" not in merged:
                merged["data"] = {}
            merged["data"].update(resp_memo["data"])
        # 更新 checksum
        if "checksum" in resp_memo:
            merged["checksum"] = resp_memo["checksum"]
        # 合并其他字段
        for key in ("htmlHash", "children", "errors", "dataMeta"):
            if key in resp_memo:
                merged[key] = resp_memo[key]
        return merged

    def _livewire_post(self, component_name, fingerprint, server_memo, event, params):
        """发送 Livewire 事件请求"""
        api_headers = {
            "X-CSRF-TOKEN": self._token,
            "X-Livewire": "true",
            "Content-Type": "application/json",
            "Accept": "text/html, application/xhtml+xml",
            "Referer": self.base_url + "/",
        }
        payload = {
            "fingerprint": fingerprint,
            "serverMemo": server_memo,
            "updates": [{"type": "fireEvent", "payload": {"id": None, "event": event, "params": params}}],
        }
        r = self.session.post(
            f"{self.base_url}/livewire/message/{component_name}",
            json=payload,
            headers=api_headers,
            timeout=15,
        )
        if r.status_code != 200:
            raise Exception(f"Livewire {event} 失败: HTTP {r.status_code}")
        return r.json()

    def create_email(self):
        """创建临时邮箱，返回 (email, email) — 只需保存邮箱地址即可"""
        try:
            if not self._token:
                self._init_session()

            username = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
            domain = random.choice(self.DOMAINS)
            email = f"{username}@{domain}"

            # syncEmail 到 actions 组件
            resp = self._livewire_post(
                "frontend.actions", self._actions_fp, self._actions_memo,
                "syncEmail", [email]
            )
            self._actions_memo = self._merge_memo(self._actions_memo, resp.get("serverMemo"))

            if cfg:
                print(f"[{cfg.ts()}] [BeeInbox] 创建邮箱: {email}")
            return email, email

        except Exception as e:
            print(f"[{cfg.ts()}] [ERROR] BeeInbox 创建邮箱异常: {e}")
            return None, None

    def _ensure_session(self):
        """确保有有效的 CSRF token 和 Livewire 状态，过期则重新获取"""
        if self._token:
            return
        self._init_session()

    def get_inbox(self, email):
        """获取收件箱邮件列表 — 复用 session，失败时重新初始化"""
        for attempt in range(2):
            try:
                if not self._token:
                    self._init_session()

                # syncEmail 到 app 组件
                resp = self._livewire_post(
                    "frontend.app", self._app_fp, self._app_memo,
                    "syncEmail", [email]
                )
                self._app_memo = self._merge_memo(self._app_memo, resp.get("serverMemo"))

                # fetchMessages
                resp2 = self._livewire_post(
                    "frontend.app", self._app_fp, self._app_memo,
                    "fetchMessages", []
                )
                self._app_memo = self._merge_memo(self._app_memo, resp2.get("serverMemo"))

                messages = resp2.get("serverMemo", {}).get("data", {}).get("messages", [])
                return messages

            except Exception as e:
                # 第一次失败则重置 session 重试
                if attempt == 0:
                    self._token = None
                    continue
                print(f"[{cfg.ts()}] [ERROR] BeeInbox 获取收件箱异常: {e}")
        return []

    def get_verification_code(self, email):
        """从收件箱提取 OpenAI 验证码"""
        messages = self.get_inbox(email)
        for mail in messages:
            subject = str(mail.get("subject", ""))
            sender = str(mail.get("sender_email", "")).lower()
            content = str(mail.get("content", ""))

            # 清理 HTML 标签
            content_text = re.sub(r"<[^>]+>", " ", content)

            if "openai" not in sender and "openai" not in subject.lower():
                continue

            code = self._extract_code(f"{subject}\n{content_text}")
            if code:
                return code

            # 如果主题匹配但没找到验证码，也检查通用 6 位数字
            if "openai" in subject.lower() or "chatgpt" in subject.lower():
                codes = re.findall(r"\b(\d{6})\b", content_text)
                if codes:
                    return codes[-1]

        return ""

    @staticmethod
    def _extract_code(text):
        m = re.search(r"Your ChatGPT code is (\d{6})", text, re.I)
        if m:
            return m.group(1)
        m = re.search(r"(?:openai|chatgpt)[\s\S]{0,200}?(\d{6})", text, re.I)
        if m:
            return m.group(1)
        return ""
