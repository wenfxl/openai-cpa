import io
import sys
import types
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

fake_requests_module = types.SimpleNamespace(post=None, get=None)
sys.modules.setdefault("curl_cffi", types.SimpleNamespace(requests=fake_requests_module))

from utils.email_providers.local_microsoft_service import LocalMicrosoftService, MailboxAbuseModeError


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class LocalMicrosoftServiceAbuseModeTests(unittest.TestCase):
    def test_service_abuse_mode_marks_master_mailbox_dead(self):
        service = LocalMicrosoftService()
        mailbox = {
            "email": "user+alias@example.com",
            "master_email": "user@example.com",
            "refresh_token": "refresh-token",
            "client_id": "client-id",
        }
        responses = [
            _FakeResponse(
                400,
                {
                    "error": "invalid_scope",
                    "error_description": "AADSTS70000 invalid_scope",
                },
            ),
            _FakeResponse(
                400,
                {
                    "error": "invalid_grant",
                    "error_description": "AADSTS70000: User account is found to be in service abuse mode.",
                },
            ),
        ]

        def fake_post(*args, **kwargs):
            return responses.pop(0)

        with patch(
            "utils.email_providers.local_microsoft_service.cffi_requests.post",
            side_effect=fake_post,
        ):
            with patch(
                "utils.email_providers.local_microsoft_service.db_manager.update_local_mailbox_status"
            ) as update_status:
                with redirect_stdout(io.StringIO()):
                    with self.assertRaises(MailboxAbuseModeError) as ctx:
                        service._exchange_refresh_token(mailbox)

        self.assertIn("service abuse mode", str(ctx.exception))
        update_status.assert_called_once_with("user@example.com", 3)

    def test_fetch_openai_messages_logs_warning_instead_of_debug_for_service_abuse(self):
        service = LocalMicrosoftService()
        mailbox = {
            "email": "user+alias@example.com",
            "master_email": "user@example.com",
        }

        with patch.object(
            service,
            "_exchange_refresh_token",
            side_effect=MailboxAbuseModeError("user@example.com"),
        ):
            captured = io.StringIO()
            with redirect_stdout(captured):
                messages = service.fetch_openai_messages(mailbox)

        self.assertEqual([], messages)
        self.assertEqual("abuse_mode", mailbox.get("_polling_stopped"))
        output = captured.getvalue()
        self.assertIn("service abuse mode", output)
        self.assertNotIn("[DEBUG-GRAPH]", output)


if __name__ == "__main__":
    unittest.main()
