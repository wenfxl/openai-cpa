import re
import random
import string
from curl_cffi import requests
from utils import config as cfg


class MoaktService:
    """moakt.com 临时邮箱对接"""

    DOMAINS = [
        "drmail.in",
        "teml.net",
        "tmpeml.com",
        "tmpbox.net",
        "moakt.cc",
        "disbox.net",
        "tmpmail.org",
        "tmpmail.net",
        "tmails.net",
        "disbox.org",
        "moakt.co",
        "moakt.ws",
        "tmail.ws",
        "bareed.ws",
    ]

    def __init__(self, proxies=None):
        self.session = requests.Session(impersonate="chrome131")
        if proxies:
            self.session.proxies = proxies if isinstance(proxies, dict) else {"http": proxies, "https": proxies}
        self.base_url = "https://www.moakt.com"
        self._email = None

    def create_email(self):
        """创建随机临时邮箱，返回 (email, email)"""
        try:
            r = self.session.post(
                f"{self.base_url}/zh/inbox",
                data={"random": "random"},
                headers={
                    "Referer": f"{self.base_url}/zh",
                    "Origin": self.base_url,
                },
                timeout=15,
            )
            if r.status_code != 200:
                raise Exception(f"创建邮箱失败: HTTP {r.status_code}")

            m = re.search(r'id="email-address">([^<]+)', r.text)
            if not m:
                raise Exception("未找到邮箱地址")

            email = m.group(1).strip()
            self._email = email

            if cfg:
                print(f"[{cfg.ts()}] [Moakt] 创建邮箱: {email}")
            return email, email

        except Exception as e:
            print(f"[{cfg.ts()}] [ERROR] Moakt 创建邮箱异常: {e}")
            return None, None

    def get_inbox(self, email=None):
        """获取收件箱邮件列表，返回 [{subject, sender, body, id}, ...]"""
        try:
            target = email or self._email
            if not target:
                return []

            # 如果不是当前 session 的邮箱，先切换
            if self._email != target:
                # 先创建随机邮箱获取 session
                self.session.post(
                    f"{self.base_url}/zh/inbox",
                    data={"random": "random"},
                    headers={"Referer": f"{self.base_url}/zh", "Origin": self.base_url},
                    timeout=15,
                )
                # 再切换到目标邮箱
                self.session.post(
                    f"{self.base_url}/zh/inbox/change",
                    data={"username": target},
                    headers={
                        "X-Requested-With": "XMLHttpRequest",
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Referer": f"{self.base_url}/zh/inbox",
                    },
                    timeout=15,
                )
                self._email = target

            # 刷新收件箱
            r = self.session.get(f"{self.base_url}/zh/inbox", timeout=15)
            if r.status_code != 200:
                return []

            return self._parse_mail_list(r.text)

        except Exception as e:
            print(f"[{cfg.ts()}] [ERROR] Moakt 获取收件箱异常: {e}")
            return []

    def _parse_mail_list(self, html):
        """从 HTML 解析邮件列表"""
        messages = []
        # 找到邮件表格区域
        table_start = html.find('<table class="tm-table">')
        if table_start < 0:
            return []

        table_end = html.find("</table>", table_start)
        if table_end < 0:
            return []

        table_html = html[table_start:table_end]

        # 解析每行邮件 (非表头行)
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, re.DOTALL)
        for row in rows:
            tds = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
            if len(tds) < 2:
                continue

            subject = re.sub(r"<[^>]+>", "", tds[0]).strip()
            sender = re.sub(r"<[^>]+>", "", tds[1]).strip()

            # 跳过空行提示
            if "没有新邮件" in subject or "no new" in subject.lower():
                continue

            # 提取邮件详情链接
            detail_link = re.search(r'href="([^"]*)"', tds[0])
            link = detail_link.group(1) if detail_link else ""

            # 提取删除链接 (作为邮件 ID 标识)
            delete_link = re.search(r"delete_mail\('([^']*)'", row)
            msg_id = delete_link.group(1) if delete_link else link

            if subject or sender:
                messages.append({
                    "subject": subject,
                    "sender": sender,
                    "link": link,
                    "id": msg_id,
                })

        return messages

    def get_mail_content(self, link):
        """获取邮件正文"""
        try:
            if not link:
                return ""
            r = self.session.get(f"{self.base_url}{link}", timeout=15)
            if r.status_code != 200:
                return ""

            # 邮件正文通常在某个特定 div 中
            body_match = re.search(r'<div[^>]*class="[^"]*mail-body[^"]*"[^>]*>(.*?)</div>', r.text, re.DOTALL)
            if body_match:
                return re.sub(r"<[^>]+>", " ", body_match.group(1))

            # 备选：找邮件内容区域
            body_match2 = re.search(r'<div[^>]*class="[^"]*message[^"]*"[^>]*>(.*?)</div>', r.text, re.DOTALL)
            if body_match2:
                return re.sub(r"<[^>]+>", " ", body_match2.group(1))

            return re.sub(r"<[^>]+>", " ", r.text)

        except Exception:
            return ""

    def get_verification_code(self, email=None):
        """从收件箱提取 OpenAI 验证码"""
        messages = self.get_inbox(email)
        for mail in messages:
            subject = mail.get("subject", "")
            sender = mail.get("sender", "").lower()

            if "openai" not in sender and "openai" not in subject.lower():
                continue

            # 先从主题提取
            code = self._extract_code(subject)
            if code:
                return code

            # 再从邮件正文提取
            link = mail.get("link", "")
            if link:
                body = self.get_mail_content(link)
                code = self._extract_code(f"{subject}\n{body}")
                if code:
                    return code

        return ""

    @staticmethod
    def _extract_code(text):
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
