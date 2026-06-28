import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.browser_request_recorder import BrowserRequestRecorder


def _make_recorder_with_request(record: dict) -> BrowserRequestRecorder:
    recorder = BrowserRequestRecorder()
    recorder._requests.append(record)
    recorder._requests_by_id[record["id"]] = record
    return recorder


class BrowserRequestRecorderTests(unittest.TestCase):
    def test_generate_code_redacts_sensitive_headers_and_uses_json_payload(self):
        recorder = _make_recorder_with_request(
            {
                "id": "req-json",
                "devtools_request_id": "devtools-1",
                "method": "POST",
                "url": "https://example.com/api/demo",
                "headers": {
                    "Content-Type": "application/json",
                    "Authorization": "Bearer secret-token",
                    "Cookie": "session=abc",
                },
                "body": '{"alpha": 1, "beta": true}',
                "resource_type": "xhr",
                "document_url": "https://example.com",
                "initiator_type": "script",
                "captured_at": "2026-05-15 22:00:00",
            },
        )

        code = recorder.generate_code("req-json")

        self.assertIn("json=json_data", code)
        self.assertIn("<redacted:Authorization>", code)
        self.assertIn("<redacted:Cookie>", code)
        self.assertIn("'alpha': 1", code)

    def test_generate_code_keeps_sensitive_headers_when_explicitly_enabled(self):
        recorder = _make_recorder_with_request(
            {
                "id": "req-sensitive",
                "devtools_request_id": "devtools-2",
                "method": "GET",
                "url": "https://example.com/private",
                "headers": {
                    "Authorization": "Bearer secret-token",
                },
                "body": "",
                "resource_type": "fetch",
                "document_url": "https://example.com",
                "initiator_type": "script",
                "captured_at": "2026-05-15 22:01:00",
            }
        )

        code = recorder.generate_code("req-sensitive", include_sensitive=True)

        self.assertIn("Bearer secret-token", code)

    def test_generate_code_supports_form_payload(self):
        recorder = _make_recorder_with_request(
            {
                "id": "req-form",
                "devtools_request_id": "devtools-3",
                "method": "POST",
                "url": "https://example.com/form",
                "headers": {
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                "body": "name=alice&role=admin",
                "resource_type": "document",
                "document_url": "https://example.com",
                "initiator_type": "other",
                "captured_at": "2026-05-15 22:02:00",
            }
        )

        code = recorder.generate_code("req-form")

        self.assertIn("data=form_data", code)
        self.assertIn("'name': 'alice'", code)
        self.assertIn("'role': 'admin'", code)

    def test_save_code_rejects_paths_outside_project(self):
        recorder = _make_recorder_with_request(
            {
                "id": "req-save",
                "devtools_request_id": "devtools-4",
                "method": "GET",
                "url": "https://example.com/ping",
                "headers": {},
                "body": "",
                "resource_type": "fetch",
                "document_url": "https://example.com",
                "initiator_type": "script",
                "captured_at": "2026-05-15 22:03:00",
            }
        )

        with self.assertRaises(ValueError):
            recorder.save_code("req-save", output_path="../outside.py")


if __name__ == "__main__":
    unittest.main()
