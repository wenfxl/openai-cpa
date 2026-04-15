import io
import sys
import types
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

fake_requests_module = types.SimpleNamespace(post=None, get=None)
sys.modules.setdefault("curl_cffi", types.SimpleNamespace(requests=fake_requests_module))

from utils.email_providers.local_microsoft_service import LocalMicrosoftService


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
                    with self.assertRaises(RuntimeError) as ctx:
                        service._exchange_refresh_token(mailbox)

        self.assertIn("AADSTS70000", str(ctx.exception))
        update_status.assert_called_once_with("user@example.com", 3)


if __name__ == "__main__":
    unittest.main()
