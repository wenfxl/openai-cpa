import random
import string
import time
from utils import config as cfg


class GmailAliasService:
    """Gmail 别名生成器：通过 + 号生成无限别名，使用主邮箱 OAuth 收信"""

    def __init__(self, proxies=None):
        self.proxies = proxies

    def create_email(self):
        """
        生成 Gmail 别名，返回 (alias_email, base_email)
        base_email 作为 token 返回，用于后续 OAuth 收信匹配
        """
        base_email = getattr(cfg, 'GMAIL_ALIAS_BASE_EMAIL', '')
        if not base_email:
            print(f"[{cfg.ts()}] [ERROR] Gmail 别名模式：未配置主邮箱地址 (gmail_alias.base_email)")
            return None, None

        base_email = base_email.strip().lower()
        if '@gmail.com' not in base_email:
            print(f"[{cfg.ts()}] [ERROR] Gmail 别名模式：主邮箱必须是 @gmail.com 地址")
            return None, None

        username, domain = base_email.split('@', 1)
        suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
        alias = f"{username}+{suffix}@{domain}"
        return alias, base_email
