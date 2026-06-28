import sys
import types
import unittest
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

from utils.integrations.fivesim_sms import try_verify_phone_via_fivesim


class _DummySession:
    pass


class FiveSimFailClosedTests(unittest.TestCase):
    def test_timeout_cancels_instead_of_banning_order(self):
        response = Mock(status_code=200, text="OK")
        response.json.return_value = {}

        with patch("utils.integrations.fivesim_sms._fivesim_enabled", return_value=True), \
                patch("utils.integrations.fivesim_sms._fivesim_max_tries", return_value=1), \
                patch("utils.integrations.fivesim_sms._fivesim_reuse_enabled", return_value=False), \
                patch("utils.integrations.fivesim_sms._fivesim_auto_pick", return_value=False), \
                patch("utils.integrations.fivesim_sms._fivesim_pick_country", return_value="any"), \
                patch("utils.integrations.fivesim_sms._fivesim_get_number", return_value=("order-1", "+15550001111", "", "1.0")), \
                patch("utils.integrations.fivesim_sms._fivesim_poll_code", return_value=""), \
                patch("utils.integrations.fivesim_sms._fivesim_set_status") as set_status_mock, \
                patch("utils.integrations.fivesim_sms._post_with_retry", return_value=response), \
                patch("utils.integrations.fivesim_sms.generate_payload", return_value=""), \
                patch("utils.integrations.fivesim_sms._sleep_interruptible", return_value=False):
            ok, _ = try_verify_phone_via_fivesim(
                session=_DummySession(),
                proxies={"http": "http://proxy", "https": "http://proxy"},
            )

        self.assertFalse(ok)
        actions = [call.args[0] for call in set_status_mock.call_args_list]
        self.assertIn("cancel", actions)
        self.assertNotIn("ban", actions)


if __name__ == "__main__":
    unittest.main()
