import io
import sys
import types
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

fake_requests_module = types.SimpleNamespace(post=None, get=None)
sys.modules.setdefault("curl_cffi", types.SimpleNamespace(requests=fake_requests_module))
sys.modules.setdefault(
    "utils.integrations.ai_service",
    types.SimpleNamespace(AIService=object),
)
sys.modules.setdefault(
    "utils.email_providers.gmail_service",
    types.SimpleNamespace(get_gmail_otp_via_oauth=lambda *args, **kwargs: ""),
)
sys.modules.setdefault(
    "utils.email_providers.duckmail_service",
    types.SimpleNamespace(DuckMailService=object),
)

from utils.email_providers.mail_service import _poll_local_ms_for_oai_code_graph


class _AbuseStopService:
    def __init__(self):
        self.calls = 0

    def fetch_openai_messages(self, mailbox):
        self.calls += 1
        mailbox["_polling_stopped"] = "abuse_mode"
        return []


class MailServiceAbuseModeTests(unittest.TestCase):
    def test_graph_poll_stops_immediately_after_mailbox_enters_abuse_mode(self):
        service = _AbuseStopService()
        mailbox = {
            "email": "user+alias@example.com",
            "master_email": "user@example.com",
            "assigned_at": 0,
        }

        with patch("utils.email_providers.mail_service.time.sleep") as sleep_mock:
            with redirect_stdout(io.StringIO()):
                code = _poll_local_ms_for_oai_code_graph(
                    ms_service=service,
                    target_email="user+alias@example.com",
                    mailbox_dict=mailbox,
                    max_attempts=5,
                )

        self.assertEqual("", code)
        self.assertEqual("abuse_mode", mailbox.get("_polling_stopped"))
        self.assertEqual(1, service.calls)
        sleep_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
