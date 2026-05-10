import json
import logging
from typing import Dict, Any, List, Tuple
from utils import config as cfg
from curl_cffi import requests as cffi_requests

logger = logging.getLogger(__name__)


class Image2APIClient:
    def __init__(self, api_url: str = None, api_key: str = None):
        self.api_url = (api_url or getattr(cfg, "IMAGE2API_URL", "")).rstrip("/")
        self.api_key = api_key or getattr(cfg, "IMAGE2API_KEY", "")

        self.headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }

        self.request_kwargs = {
            "timeout": 30,
            "impersonate": "chrome110",
            "verify": False
        }

    def _handle_response(
            self,
            response: cffi_requests.Response,
            success_codes: Tuple[int, ...] = (200, 201, 204),
    ) -> Tuple[bool, Any]:
        if response.status_code in success_codes:
            try:
                return True, response.json() if response.text else {}
            except ValueError:
                return True, response.text

        error_msg = f"HTTP {response.status_code}"
        try:
            detail = response.json()
            if isinstance(detail, dict):
                error_msg = detail.get("message", error_msg)
        except Exception:
            error_msg = f"{error_msg} - {response.text[:200]}"

        return False, error_msg

    def add_accounts(self, tokens: List[str]) -> Tuple[bool, str]:
        if not self.api_url or not self.api_key:
            return False, "Image2API 配置缺失，请检查 URL 和 Auth Key"

        if not tokens:
            return False, "没有需要上传的 Token"

        url = f"{self.api_url}/api/accounts"
        payload = {
            "tokens": tokens
        }

        try:
            response = cffi_requests.post(
                url,
                stream=True,
                json=payload,
                headers=self.headers,
                **self.request_kwargs
            )
            if response.status_code in (200, 201):
                response.close()
                logger.info(f"Image2API 推送成功: {len(tokens)} 个账号")
                return True, f"成功推送 {len(tokens)} 个账号"
            else:
                response.read()
                ok, result = self._handle_response(response)
                logger.warning(f"Image2API 推送失败: {result}")
                return False, str(result)
        except Exception as exc:
            logger.error("向 Image2API 推送网络请求失败: %s", exc)
            return False, f"网络请求失败: {exc}"

    def get_accounts(self) -> Tuple[bool, Any]:
        if not self.api_url or not self.api_key:
            return False, "配置未填写"
        url = f"{self.api_url}/api/accounts"
        try:
            kwargs = self.request_kwargs.copy()
            kwargs["timeout"] = 60
            response = cffi_requests.get(url, headers=self.headers, **kwargs)
            return self._handle_response(response)
        except Exception as exc:
            return False, f"获取远端账号失败: {exc}"

    def update_account_status(self, access_token: str, status: str, acc_type: str = "Free", quota: int = 25) -> \
    Tuple[bool, Any]:
        url = f"{self.api_url}/api/accounts/update"
        payload = {
            "access_token": access_token,
            "type": acc_type,
            "status": status,
            "quota": quota
        }
        try:
            response = cffi_requests.post(url, json=payload, headers=self.headers, **self.request_kwargs)
            return self._handle_response(response)
        except Exception as exc:
            return False, f"更新远端状态失败: {exc}"

    def refresh_tokens(self, access_tokens: List[str]) -> Tuple[bool, Any]:
        url = f"{self.api_url}/api/accounts/refresh"
        payload = {"access_tokens": access_tokens}
        try:
            response = cffi_requests.post(url, json=payload, headers=self.headers, **self.request_kwargs)
            return self._handle_response(response)
        except Exception as exc:
            return False, f"刷新远端凭证失败: {exc}"