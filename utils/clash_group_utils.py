import re
from typing import Optional


def strip_group_decorations(text: str) -> str:
    raw = str(text or "").strip().lower()
    raw = re.sub(r'[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\ufe0f]', '', raw)
    raw = re.sub(r'[\s\-_]+', '', raw)
    return raw


def resolve_group_name(proxy_map: dict, desired_group_name: str) -> Optional[str]:
    desired = strip_group_decorations(desired_group_name)
    for key, value in (proxy_map or {}).items():
        if not (isinstance(value, dict) and 'all' in value):
            continue
        current = strip_group_decorations(key)
        if (
            key == desired_group_name
            or (desired and desired in current)
            or (current and current in desired)
        ):
            return key
    return None
