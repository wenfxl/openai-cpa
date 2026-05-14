import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import routers.system_routes as system_routes
from global_state import log_history


class ClusterUploadAccountsRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(system_routes.router)
        self.client = TestClient(self.app)
        self._original_logs = list(log_history)
        log_history.clear()

    def tearDown(self):
        log_history.clear()
        log_history.extend(self._original_logs)

    def test_upload_accounts_logs_once_and_returns_ack_fields(self):
        payload = {
            "node_name": "NODE-2",
            "secret": "secret",
            "accounts": [
                {"email": "a@example.com", "password": "pw", "token_data": "{}"},
                {"email": "b@example.com", "password": "pw", "token_data": "{}"},
            ],
            "batch_index": 1,
            "total_batches": 3,
            "total_uploaded": 2,
        }

        with patch.object(system_routes.core_engine.cfg, "_c", {"cluster_secret": "secret"}), \
             patch.object(system_routes.db_manager, "save_account_to_db", return_value=True), \
             patch.object(system_routes.core_engine, "ts", return_value="14:08:22"):
            response = self.client.post("/api/cluster/upload_accounts", json=payload)

        self.assertEqual(200, response.status_code)
        self.assertEqual(
            {
                "status": "success",
                "message": "成功接收 2 个账号",
                "accepted_count": 2,
                "batch_index": 1,
                "total_batches": 3,
                "total_uploaded": 2,
                "done": False,
            },
            response.json(),
        )
        self.assertEqual(1, len(log_history))
        self.assertIn("第 1/3 批账号接收完成", log_history[0])

    def test_upload_accounts_logs_done_message_once_for_final_batch(self):
        payload = {
            "node_name": "NODE-2",
            "secret": "secret",
            "accounts": [
                {"email": "a@example.com", "password": "pw", "token_data": "{}"},
            ],
            "batch_index": 2,
            "total_batches": 2,
            "total_uploaded": 5,
        }

        with patch.object(system_routes.core_engine.cfg, "_c", {"cluster_secret": "secret"}), \
             patch.object(system_routes.db_manager, "save_account_to_db", return_value=True), \
             patch.object(system_routes.core_engine, "ts", return_value="14:08:22"):
            response = self.client.post("/api/cluster/upload_accounts", json=payload)

        self.assertEqual(200, response.status_code)
        self.assertTrue(response.json()["done"])
        self.assertEqual(2, len(log_history))
        self.assertIn("第 2/2 批账号接收完成", log_history[0])
        self.assertIn("账号批量接收完成", log_history[1])


if __name__ == "__main__":
    unittest.main()
