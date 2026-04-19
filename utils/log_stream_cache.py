import re
from typing import Iterable, List, Sequence, Tuple


_LOG_PATTERN = re.compile(r"^\[(.*?)\]\s*\[(.*?)\]\s+(.*)$")


def get_recent_logs(log_source: Iterable[str], limit: int) -> List[str]:
    if limit <= 0:
        return []
    recent = list(log_source)
    if len(recent) > limit:
        return recent[-limit:]
    return recent


def parse_log_entry(raw: str) -> dict:
    match = _LOG_PATTERN.match(raw.strip())
    if match:
        return {
            "parsed": True,
            "time": match.group(1),
            "level": match.group(2).upper(),
            "text": match.group(3),
            "raw": raw,
        }
    return {"parsed": False, "raw": raw}


class RecentParsedLogCache:
    def __init__(self, limit: int = 50):
        self.limit = max(1, int(limit))
        self._recent_raw: List[str] = []
        self._parsed_logs: List[dict] = []

    def refresh(self, log_source: Iterable[str]) -> Tuple[List[str], List[dict], bool]:
        recent = get_recent_logs(log_source, self.limit)
        if recent == self._recent_raw:
            return self._recent_raw, self._parsed_logs, False

        overlap = self._find_overlap(recent)
        if overlap > 0:
            reused_start = len(self._recent_raw) - overlap
            parsed = self._parsed_logs[reused_start:] + [
                parse_log_entry(raw) for raw in recent[overlap:]
            ]
        else:
            parsed = [parse_log_entry(raw) for raw in recent]

        self._recent_raw = recent
        self._parsed_logs = parsed
        return self._recent_raw, self._parsed_logs, True

    def _find_overlap(self, recent: Sequence[str]) -> int:
        max_overlap = min(len(self._recent_raw), len(recent))
        for overlap in range(max_overlap, 0, -1):
            if self._recent_raw[-overlap:] == list(recent[:overlap]):
                return overlap
        return 0
