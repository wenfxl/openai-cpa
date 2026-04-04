import re
import sys
from pathlib import Path
from curl_cffi import requests
from typing import Optional, Any
from utils import config as cfg

def _load_luckmail_client_class():
    """动态加载 luckmail SDK"""
    try:
        from luckmail import LuckMailClient
        return LuckMailClient
    except ImportError:
        pass
        
    candidates = [
        Path(__file__).resolve().parent / "luckmail",
        Path(__file__).resolve().parents[1] / "tools" / "luckmail",
    ]
    for path in candidates:
        if not path.is_dir(): continue
        if str(path) not in sys.path: sys.path.insert(0, str(path))
        try:
            from luckmail import LuckMailClient
            return LuckMailClient
        except Exception:
            continue
    return None

class LuckMailService:
    """直连版 LuckMail 接码服务"""
    
    def __init__(self, api_key: str, preferred_domain: str = "", proxies: dict = None, email_type: str = "ms_graph", variant_mode: str = ""):
        if not api_key:
            raise ValueError("LuckMail API_KEY 不能为空！请检查配置。")

        self.api_key = api_key
        self.base_url = "https://mails.luckyous.com"
        self.project_code = "openai"
        self.preferred_domain = preferred_domain.strip()
        self.proxies = proxies

        # 保存邮箱类型和变种模式
        self.email_type = email_type.strip() or "ms_graph"
        self.variant_mode = variant_mode.strip()

        client_cls = _load_luckmail_client_class()
        if not client_cls:
            raise ValueError("未找到 LuckMail SDK！请确保本地存在 luckmail 文件夹。")

        self.client = client_cls(base_url=self.base_url + "/", api_key=self.api_key)

        if self.proxies and hasattr(self.client, "session"):
            self.client.session.proxies = self.proxies

    def _extract_field(self, obj: any, *keys: str) -> any:
        if not obj: return None
        if isinstance(obj, dict):
            for k in keys:
                if k in obj: return obj.get(k)
        for k in keys:
            if hasattr(obj, k): return getattr(obj, k)
        return None

    def get_email_and_token(self, auto_tag: bool = False, tag_id: int = None) -> tuple:
        api_url = f"{self.base_url}/api/v1/openapi/email/purchase"
        headers = {"X-API-Key": self.api_key, "Content-Type": "application/json"}
        payload = {"project_code": self.project_code, "email_type": self.email_type, "quantity": 1}
        if self.preferred_domain: payload["domain"] = self.preferred_domain
        if self.email_type == "google_variant" and self.variant_mode: payload["variant_mode"] = self.variant_mode

        try:
            resp = requests.post(api_url, headers=headers, json=payload, timeout=60)
            res_data = resp.json()
            if resp.status_code != 200 or res_data.get("code") != 0:
                raise Exception(f"购号失败: {res_data.get('message', resp.text)}")

            data_field = res_data.get("data", {})
            item = data_field.get("purchases", [{}])[0] if "purchases" in data_field else data_field
            email = str(self._extract_field(item, "email_address") or "").strip().lower()
            token = str(self._extract_field(item, "token") or "").strip()
            p_id = self._extract_field(item, "id")

            if p_id and auto_tag and tag_id:
                self.set_email_tag(p_id, tag_id)
            return email, token, p_id
        except Exception as e:
            raise Exception(f"LuckMail 购号异常: {e}")

    def bulk_purchase(self, quantity: int = 1, auto_tag: bool = False, tag_id: int = None) -> list:
        api_url = f"{self.base_url}/api/v1/openapi/email/purchase"
        headers = {"X-API-Key": self.api_key, "Content-Type": "application/json"}
        payload = {"project_code": self.project_code, "email_type": self.email_type, "quantity": quantity}

        if self.preferred_domain: payload["domain"] = self.preferred_domain
        if self.email_type == "google_variant" and self.variant_mode: payload["variant_mode"] = self.variant_mode

        try:
            resp = requests.post(api_url, headers=headers, json=payload, timeout=60)
            res_data = resp.json()
            if res_data.get("code") != 0: raise Exception(res_data.get("message"))

            items = res_data.get("data", {}).get("purchases", [])
            results = []
            for item in items:
                email = str(self._extract_field(item, "email_address") or "").strip().lower()
                token = str(self._extract_field(item, "token") or "").strip()
                purchase_id = self._extract_field(item, "id")

                if purchase_id and auto_tag and tag_id:
                    self.set_email_tag(purchase_id, tag_id)

                results.append({"email": email, "token": token})
            return results
        except Exception as e:
            raise Exception(f"LuckMail 批量购买失败: {e}")

    def get_code(self, token: str) -> str:
        result = self.client.user.get_token_code(token)
        code = str(self._extract_field(result, "verification_code") or "").strip()
        
        if code:
            match = re.search(r'\b\d{6}\b', code)
            if match:
                return match.group(0)
        return ""

    def get_purchased_emails(self, page=1, page_size=100, **kwargs) -> list:
        api_url = f"{self.base_url}/api/v1/openapi/email/purchases"
        headers = {"X-API-Key": self.api_key}
        params = {"page": page, "page_size": page_size, "user_disabled": 0}
        params.update({k: v for k, v in kwargs.items() if v is not None})
        try:
            resp = requests.get(api_url, headers=headers, params=params, proxies=self.proxies, timeout=60,
                                impersonate="chrome110")
            res_data = resp.json()
            return res_data.get("data", {}).get("list", []) if res_data.get("code") == 0 else []
        except Exception as e:
            print(f"[{cfg.ts()}] [WARNING] 获取 LuckMail 列表超时或失败: {e}")
            return []

    def get_random_purchased_email(self, tag_id: int) -> tuple:
        import random
        keyword = self.preferred_domain if self.preferred_domain else None
        emails = self.get_purchased_emails(keyword=keyword)

        available_emails = [e for e in emails if str(e.get("tag_id")) != str(tag_id)]
        if not available_emails: return None, None, None

        item = random.choice(available_emails)
        email = str(self._extract_field(item, "email_address") or "").strip().lower()
        token = str(self._extract_field(item, "token") or "").strip()
        p_id = self._extract_field(item, "id")
        return email, token, p_id

    def set_email_tag(self, purchase_id: int, tag_id: int) -> bool:
        if not tag_id: return False
        api_url = f"{self.base_url}/api/v1/openapi/email/purchases/{purchase_id}/tag"
        try:
            resp = requests.put(api_url, headers={"X-API-Key": self.api_key}, json={"tag_id": tag_id}, timeout=60)
            return resp.status_code == 200
        except Exception:
            return False

    def create_tag(self, name: str = "已使用") -> Optional[int]:
        api_url = f"{self.base_url}/api/v1/openapi/email/tags"
        headers = {"X-API-Key": self.api_key, "Content-Type": "application/json"}
        payload = {"name": name, "remark": "自动创建", "limit_type": 1}
        try:
            resp = requests.post(api_url, headers=headers, json=payload, timeout=10)
            res_data = resp.json()
            return res_data.get("data", {}).get("id") if res_data.get("code") == 0 else None
        except Exception:
            return None

    def get_tags(self) -> list:
        api_url = f"{self.base_url}/api/v1/openapi/email/tags"
        headers = {"X-API-Key": self.api_key}
        try:
            resp = requests.get(api_url, headers=headers, proxies=self.proxies, timeout=60, impersonate="chrome110")
            res_data = resp.json()
            return res_data.get("data", []) if res_data.get("code") == 0 else []
        except Exception:
            return []

    def ensure_tag_id(self, tag_name: str = "已使用") -> Optional[int]:
        tags = self.get_tags()
        for t in tags:
            if t.get("name") == tag_name:
                return t.get("id")

    def get_or_create_tag_id(self, tag_name: str = "已使用") -> Optional[int]:
        tags = self.get_tags()
        for t in tags:
            if t.get("name") == tag_name:
                return t.get("id")
        raise Exception(f"[LuckMail] 未找到 '{tag_name}'，正在自动创建...")
        return self.create_tag(tag_name)