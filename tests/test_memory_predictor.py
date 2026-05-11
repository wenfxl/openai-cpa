import unittest
from unittest.mock import patch

from utils.memory_predictor import build_memory_report


class MemoryPredictorTests(unittest.TestCase):
    def _fake_actual(self, rss_mb, total_mb=512, available_mb=200):
        return {
            "available": True,
            "rss_mb": rss_mb,
            "vms_mb": 700,
            "percent": 12.5,
            "system_total_mb": total_mb,
            "system_available_mb": available_mb,
            "system_used_percent": round((1 - available_mb / total_mb) * 100, 2),
            "pid": 1234,
            "timestamp": 0,
        }

    def test_build_memory_report_returns_recommendation_when_pressure_is_high(self):
        config = {
            "reg_threads": 20,
            "enable_multi_thread_reg": True,
            "cpa_mode": {"enable": True, "threads": 20},
            "sub2api_mode": {"enable": True, "threads": 20},
            "max_log_lines": 5000,
        }
        with patch("utils.memory_predictor.get_actual_memory_usage", return_value=self._fake_actual(300)):
            report = build_memory_report(config)

        recommendation = report["recommendation"]
        self.assertEqual("warning", recommendation["level"])
        self.assertLess(recommendation["suggested_config"]["reg_threads"], 20)
        self.assertLess(recommendation["suggested_config"]["cpa_threads"], 20)
        self.assertLess(recommendation["suggested_config"]["sub2api_threads"], 20)
        self.assertIn("建议", recommendation["summary"])

    def test_build_memory_report_keeps_config_when_pressure_is_low(self):
        config = {
            "reg_threads": 6,
            "enable_multi_thread_reg": True,
            "cpa_mode": {"enable": True, "threads": 8},
            "sub2api_mode": {"enable": False, "threads": 10},
            "max_log_lines": 800,
        }
        with patch("utils.memory_predictor.get_actual_memory_usage", return_value=self._fake_actual(80)):
            report = build_memory_report(config)

        recommendation = report["recommendation"]
        self.assertEqual("ok", recommendation["level"])
        self.assertEqual(6, recommendation["suggested_config"]["reg_threads"])
        self.assertEqual(8, recommendation["suggested_config"]["cpa_threads"])
        self.assertEqual(10, recommendation["suggested_config"]["sub2api_threads"])


if __name__ == "__main__":
    unittest.main()
