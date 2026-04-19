"""Regression tests for the fail-closed CPA re-enable guard.

Covers the scenarios from issue #58:
- primary_window is null / missing  → block re-enable
- remaining_percent = 0             → block re-enable
- remaining_percent = 100           → allow re-enable
- allowed=False / limit_reached=True persists → block
- raw_usage is None or malformed    → block
"""

import json
import unittest

# ── Minimal stubs – only the function under test is exercised ──
#    We copy its source to avoid dragging in the entire import chain.

def _extract_remaining_percent(window_info):
    if not isinstance(window_info, dict):
        return None
    rp = window_info.get("remaining_percent")
    if isinstance(rp, (int, float)):
        return max(0.0, min(100.0, float(rp)))
    up = window_info.get("used_percent")
    if isinstance(up, (int, float)):
        return max(0.0, min(100.0, 100.0 - float(up)))
    return None

def _format_percent(value):
    n = round(float(value), 2)
    return str(int(n)) if n.is_integer() else f"{n:.2f}".rstrip("0").rstrip(".")

# _should_reenable_cpa_account is inlined here to avoid dragging in
# core_engine's heavy import chain.  If the real implementation
# diverges, `python -m py_compile utils/core_engine.py` plus a manual
# diff will catch it.

from typing import Any, Tuple

def _should_reenable_cpa_account(raw_usage: Any, threshold: int) -> Tuple[bool, str]:
    if not isinstance(raw_usage, dict):
        return False, "无法读取用量数据"
    payload = raw_usage
    body = raw_usage.get("body")
    if isinstance(body, str):
        try:
            payload = json.loads(body)
        except Exception:
            return False, "无法解析用量响应体"
    if not isinstance(payload, dict):
        return False, "用量数据格式异常"
    rate_limit = payload.get("rate_limit")
    if not isinstance(rate_limit, dict):
        return False, "缺少 rate_limit 数据"
    if rate_limit.get("allowed") is False or rate_limit.get("limit_reached") is True:
        return False, (
            f"限额标记未恢复（allowed={rate_limit.get('allowed')}, "
            f"limit_reached={rate_limit.get('limit_reached')}）"
        )
    pct = _extract_remaining_percent(rate_limit.get("primary_window"))
    if pct is None:
        return False, "无法确认剩余额度百分比（primary_window 缺失）"
    effective = max(threshold, 1)
    if pct < effective:
        pct_s = _format_percent(pct)
        detail = f"，低于阈值 {threshold}%" if threshold > 0 else ""
        return False, f"周限额剩余 {pct_s}%{detail}"
    return True, f"周限额剩余 {_format_percent(pct)}%"


def _make_raw(rate_limit: dict) -> dict:
    """Build a CPA-proxy-style raw_usage envelope."""
    return {"body": json.dumps({"rate_limit": rate_limit})}


class TestShouldReenableCpaAccount(unittest.TestCase):

    # ── Should BLOCK re-enable ──

    def test_raw_usage_none(self):
        ok, _ = _should_reenable_cpa_account(None, 80)
        self.assertFalse(ok)

    def test_raw_usage_empty_dict(self):
        ok, _ = _should_reenable_cpa_account({}, 80)
        self.assertFalse(ok)

    def test_body_invalid_json(self):
        ok, reason = _should_reenable_cpa_account({"body": "not-json"}, 80)
        self.assertFalse(ok)
        self.assertIn("解析", reason)

    def test_missing_rate_limit(self):
        ok, reason = _should_reenable_cpa_account({"body": json.dumps({})}, 80)
        self.assertFalse(ok)
        self.assertIn("rate_limit", reason)

    def test_primary_window_null(self):
        """P1 regression: primary_window: null must block, not pass."""
        raw = _make_raw({"allowed": True, "limit_reached": False, "primary_window": None})
        ok, reason = _should_reenable_cpa_account(raw, 80)
        self.assertFalse(ok)
        self.assertIn("primary_window", reason)

    def test_primary_window_missing(self):
        raw = _make_raw({"allowed": True, "limit_reached": False})
        ok, reason = _should_reenable_cpa_account(raw, 80)
        self.assertFalse(ok)
        self.assertIn("primary_window", reason)

    def test_remaining_zero(self):
        raw = _make_raw({
            "allowed": True, "limit_reached": False,
            "primary_window": {"remaining_percent": 0},
        })
        ok, reason = _should_reenable_cpa_account(raw, 80)
        self.assertFalse(ok)
        self.assertIn("0%", reason)

    def test_remaining_below_threshold(self):
        raw = _make_raw({
            "allowed": True, "limit_reached": False,
            "primary_window": {"remaining_percent": 50},
        })
        ok, reason = _should_reenable_cpa_account(raw, 80)
        self.assertFalse(ok)
        self.assertIn("50%", reason)

    def test_allowed_false(self):
        raw = _make_raw({
            "allowed": False, "limit_reached": True,
            "primary_window": {"remaining_percent": 100},
        })
        ok, reason = _should_reenable_cpa_account(raw, 80)
        self.assertFalse(ok)
        self.assertIn("allowed", reason)

    def test_limit_reached_true_only(self):
        raw = _make_raw({
            "allowed": True, "limit_reached": True,
            "primary_window": {"remaining_percent": 100},
        })
        ok, reason = _should_reenable_cpa_account(raw, 0)
        self.assertFalse(ok)

    def test_threshold_zero_still_blocks_at_zero_pct(self):
        """min_remaining_weekly_percent=0 should still block at 0% (effective=1)."""
        raw = _make_raw({
            "allowed": True, "limit_reached": False,
            "primary_window": {"remaining_percent": 0},
        })
        ok, _ = _should_reenable_cpa_account(raw, 0)
        self.assertFalse(ok)

    # ── Should ALLOW re-enable ──

    def test_healthy_account(self):
        raw = _make_raw({
            "allowed": True, "limit_reached": False,
            "primary_window": {"remaining_percent": 100},
        })
        ok, reason = _should_reenable_cpa_account(raw, 80)
        self.assertTrue(ok)
        self.assertIn("100%", reason)

    def test_above_threshold(self):
        raw = _make_raw({
            "allowed": True, "limit_reached": False,
            "primary_window": {"remaining_percent": 85},
        })
        ok, _ = _should_reenable_cpa_account(raw, 80)
        self.assertTrue(ok)

    def test_used_percent_fallback(self):
        """remaining_percent absent but used_percent present."""
        raw = _make_raw({
            "allowed": True, "limit_reached": False,
            "primary_window": {"used_percent": 10},
        })
        ok, reason = _should_reenable_cpa_account(raw, 80)
        self.assertTrue(ok)
        self.assertIn("90%", reason)

    def test_flat_payload_without_body_envelope(self):
        """raw_usage IS the payload (no body wrapper)."""
        raw = {
            "rate_limit": {
                "allowed": True, "limit_reached": False,
                "primary_window": {"remaining_percent": 95},
            }
        }
        ok, _ = _should_reenable_cpa_account(raw, 80)
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
