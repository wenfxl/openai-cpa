import io
import sys
import types
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

fake_requests_module = types.SimpleNamespace(get=None, post=None, Session=object)
sys.modules.setdefault("curl_cffi", types.SimpleNamespace(requests=fake_requests_module))
sys.modules.setdefault(
    "utils.db_manager",
    types.SimpleNamespace(
        get_sys_kv=lambda *args, **kwargs: None,
        set_sys_kv=lambda *args, **kwargs: None,
    ),
)

from utils.integrations.hero_sms import _try_verify_phone_via_hero_sms


class _DummySession:
    pass


class HeroSmsCountryRetryTests(unittest.TestCase):
    def test_no_numbers_retries_same_country_when_auto_pick_disabled(self):
        attempted_countries = []

        def fake_get_number(proxies, *, service_code="", country_id=None):
            attempted_countries.append(int(country_id))
            return "", "", "NO_NUMBERS"

        with patch("utils.integrations.hero_sms._hero_sms_enabled", return_value=True), \
                patch("utils.integrations.hero_sms._hero_sms_max_tries", return_value=2), \
                patch("utils.integrations.hero_sms.hero_sms_get_balance", return_value=(9.92, "")), \
                patch("utils.integrations.hero_sms._hero_sms_update_runtime"), \
                patch("utils.integrations.hero_sms._hero_sms_resolve_service_code", return_value="dr"), \
                patch("utils.integrations.hero_sms._hero_sms_resolve_country_id", return_value=16), \
                patch("utils.integrations.hero_sms._hero_sms_auto_pick_country", return_value=False), \
                patch("utils.integrations.hero_sms._hero_sms_pick_country_id", return_value=16) as pick_country_mock, \
                patch("utils.integrations.hero_sms._hero_sms_reuse_enabled", return_value=False), \
                patch("utils.integrations.hero_sms._hero_sms_get_number", side_effect=fake_get_number), \
                patch("utils.integrations.hero_sms._sleep_interruptible", return_value=False):
            with redirect_stdout(io.StringIO()):
                ok, reason = _try_verify_phone_via_hero_sms(
                    session=_DummySession(),
                    proxies={"http": "http://proxy", "https": "http://proxy"},
                )

        self.assertFalse(ok)
        self.assertEqual("取号失败: NO_NUMBERS", reason)
        self.assertEqual([16, 16], attempted_countries)
        pick_country_mock.assert_called_once()

    def test_no_numbers_repicks_country_when_auto_pick_enabled(self):
        attempted_countries = []

        def fake_get_number(proxies, *, service_code="", country_id=None):
            attempted_countries.append(int(country_id))
            return "", "", "NO_NUMBERS"

        with patch("utils.integrations.hero_sms._hero_sms_enabled", return_value=True), \
                patch("utils.integrations.hero_sms._hero_sms_max_tries", return_value=2), \
                patch("utils.integrations.hero_sms.hero_sms_get_balance", return_value=(9.92, "")), \
                patch("utils.integrations.hero_sms._hero_sms_update_runtime"), \
                patch("utils.integrations.hero_sms._hero_sms_resolve_service_code", return_value="dr"), \
                patch("utils.integrations.hero_sms._hero_sms_resolve_country_id", return_value=16), \
                patch("utils.integrations.hero_sms._hero_sms_auto_pick_country", return_value=True), \
                patch("utils.integrations.hero_sms._hero_sms_pick_country_id", side_effect=[86, 91]) as pick_country_mock, \
                patch("utils.integrations.hero_sms._hero_sms_reuse_enabled", return_value=False), \
                patch("utils.integrations.hero_sms._hero_sms_get_number", side_effect=fake_get_number), \
                patch("utils.integrations.hero_sms._sleep_interruptible", return_value=False):
            with redirect_stdout(io.StringIO()):
                ok, reason = _try_verify_phone_via_hero_sms(
                    session=_DummySession(),
                    proxies={"http": "http://proxy", "https": "http://proxy"},
                )

        self.assertFalse(ok)
        self.assertEqual("取号失败: NO_NUMBERS", reason)
        self.assertEqual([86, 91], attempted_countries)
        self.assertEqual(2, pick_country_mock.call_count)

    def test_reuse_timeout_cancels_instead_of_preserving_number(self):
        response = Mock(status_code=200, text="OK")
        response.json.return_value = {}

        with patch("utils.integrations.hero_sms._hero_sms_enabled", return_value=True), \
                patch("utils.integrations.hero_sms._hero_sms_max_tries", return_value=1), \
                patch("utils.integrations.hero_sms.hero_sms_get_balance", return_value=(9.92, "")), \
                patch("utils.integrations.hero_sms._hero_sms_update_runtime"), \
                patch("utils.integrations.hero_sms._hero_sms_resolve_service_code", return_value="dr"), \
                patch("utils.integrations.hero_sms._hero_sms_resolve_country_id", return_value=16), \
                patch("utils.integrations.hero_sms._hero_sms_auto_pick_country", return_value=False), \
                patch("utils.integrations.hero_sms._hero_sms_pick_country_id", return_value=16), \
                patch("utils.integrations.hero_sms._hero_sms_reuse_enabled", return_value=True), \
                patch("utils.integrations.hero_sms._hero_sms_reuse_get", return_value=("reuse-1", "+15550001111", 0)), \
                patch("utils.integrations.hero_sms._hero_sms_country_mark_timeout", return_value=False), \
                patch("utils.integrations.hero_sms._hero_sms_reuse_clear") as reuse_clear_mock, \
                patch("utils.integrations.hero_sms._hero_sms_mark_ready_enabled", return_value=False), \
                patch("utils.integrations.hero_sms._hero_sms_poll_code", return_value=""), \
                patch("utils.integrations.hero_sms._hero_sms_get_number", return_value=("", "", "NO_NUMBERS")), \
                patch("utils.integrations.hero_sms._post_with_retry", return_value=response), \
                patch("utils.integrations.hero_sms.generate_payload", return_value=""), \
                patch("utils.integrations.hero_sms._hero_sms_set_status") as set_status_mock, \
                patch("utils.integrations.hero_sms._sleep_interruptible", return_value=False):
            with redirect_stdout(io.StringIO()):
                ok, _ = _try_verify_phone_via_hero_sms(
                    session=_DummySession(),
                    proxies={"http": "http://proxy", "https": "http://proxy"},
                )

        self.assertFalse(ok)
        status_values = [call.args[1] for call in set_status_mock.call_args_list]
        self.assertIn(8, status_values)
        self.assertNotIn(3, status_values)
        reuse_clear_mock.assert_called()


if __name__ == "__main__":
    unittest.main()
