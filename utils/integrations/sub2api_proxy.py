from urllib.parse import urlparse
from typing import Any, Dict, List, Optional


def normalize_sub2api_proxy_urls(raw_value: Any) -> List[str]:
    if isinstance(raw_value, list):
        raw_items = raw_value
    else:
        text = str(raw_value or "").replace("\r", "\n")
        raw_items = text.split("\n")

    proxy_urls: List[str] = []
    seen = set()
    for item in raw_items:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        proxy_urls.append(value)
    return proxy_urls


def parse_sub2api_proxy(proxy_url: str) -> Optional[Dict[str, Any]]:
    if not proxy_url:
        return None

    try:
        parsed = urlparse(str(proxy_url).strip())
        protocol = parsed.scheme
        host = parsed.hostname
        port = parsed.port
        username = parsed.username or ""
        password = parsed.password or ""

        if not protocol or not host or not port:
            return None

        proxy_key = f"{protocol}|{host}|{port}|{username}|{password}"
        proxy_dict: Dict[str, Any] = {
            "proxy_key": proxy_key,
            "name": "openai-cpa",
            "protocol": protocol,
            "host": host,
            "port": port,
            "status": "active",
        }
        if username and password:
            proxy_dict["username"] = username
            proxy_dict["password"] = password
        return proxy_dict
    except Exception:
        return None


def get_valid_sub2api_proxy_urls(raw_value: Any) -> List[str]:
    proxy_urls: List[str] = []
    for item in normalize_sub2api_proxy_urls(raw_value):
        if parse_sub2api_proxy(item) is None:
            continue
        proxy_urls.append(item)
    return proxy_urls
