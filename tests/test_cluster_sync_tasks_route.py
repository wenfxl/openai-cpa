import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import routers.system_routes as system_routes


class ClusterSyncTasksRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(system_routes.router)
        self.client = TestClient(self.app)
        self._token = 'test-token'
        system_routes.VALID_TOKENS.add(self._token)

    def tearDown(self):
        system_routes.VALID_TOKENS.discard(self._token)

    def _auth_headers(self):
        return {'Authorization': f'Bearer {self._token}'}

    def test_create_sync_task_rejects_path_outside_shared_dir(self):
        payload = {
            'node_name': 'NODE-2',
            'secret': 'secret',
            'task_id': 'task-outside',
            'file_path': 'C:/temp/outside.jsonl',
            'file_size': 12,
            'total_count': 1,
        }

        with patch.object(system_routes.core_engine.cfg, '_c', {'cluster_secret': 'secret'}), \
             patch.object(system_routes, '_is_cluster_sync_path_allowed', return_value=False):
            response = self.client.post('/api/cluster/sync_tasks', json=payload)

        self.assertEqual(200, response.status_code)
        self.assertEqual({'status': 'error', 'message': '同步文件路径不在共享目录内'}, response.json())

    def test_create_sync_task_rejects_default_cluster_secret_when_enforced(self):
        payload = {
            'node_name': 'NODE-2',
            'secret': 'wenfxl666',
            'task_id': 'task-default-secret',
            'file_path': 'data/cluster_sync/NODE-2/task-default-secret.jsonl',
            'file_size': 12,
            'total_count': 1,
            'file_sha256': 'abc',
        }

        with patch.object(system_routes.core_engine.cfg, '_c', {'cluster_secret': 'wenfxl666'}), \
             patch.object(system_routes.cfg, 'CLUSTER_SYNC_REQUIRE_CUSTOM_SECRET', True):
            response = self.client.post('/api/cluster/sync_tasks', json=payload)

        self.assertEqual(200, response.status_code)
        self.assertEqual({'status': 'error', 'message': '请先配置自定义 cluster_secret'}, response.json())

    def test_create_sync_task_rejects_sha256_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sync_file = Path(tmp_dir) / 'NODE-2' / 'task-bad-sha.jsonl'
            sync_file.parent.mkdir(parents=True, exist_ok=True)
            sync_file.write_text('{"email":"a@example.com","password":"pw","token_data":"{}"}\n', encoding='utf-8')
            payload = {
                'node_name': 'NODE-2',
                'secret': 'secret',
                'task_id': 'task-bad-sha',
                'file_path': str(sync_file),
                'file_size': sync_file.stat().st_size,
                'total_count': 1,
                'file_sha256': 'deadbeef',
            }

            with patch.object(system_routes.core_engine.cfg, '_c', {'cluster_secret': 'secret'}), \
                 patch.object(system_routes.cfg, 'CLUSTER_SYNC_REQUIRE_CUSTOM_SECRET', True), \
                 patch.object(system_routes.cfg, 'CLUSTER_SYNC_SHARED_DIR', tmp_dir):
                response = self.client.post('/api/cluster/sync_tasks', json=payload)

        self.assertEqual(200, response.status_code)
        self.assertEqual({'status': 'error', 'message': '同步文件摘要校验失败'}, response.json())

    def test_create_sync_task_rejects_duplicate_task_id(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sync_file = Path(tmp_dir) / 'NODE-2' / 'task-dup.jsonl'
            sync_file.parent.mkdir(parents=True, exist_ok=True)
            sync_file.write_text('{"email":"a@example.com","password":"pw","token_data":"{}"}\n', encoding='utf-8')
            payload = {
                'node_name': 'NODE-2',
                'secret': 'secret',
                'task_id': 'task-dup',
                'file_path': str(sync_file),
                'file_size': sync_file.stat().st_size,
                'total_count': 1,
                'file_sha256': hashlib.sha256(sync_file.read_bytes()).hexdigest(),
            }

            with patch.object(system_routes.core_engine.cfg, '_c', {'cluster_secret': 'secret'}), \
                 patch.object(system_routes, 'ensure_cluster_sync_worker_started'), \
                 patch.object(system_routes.cfg, 'CLUSTER_SYNC_SHARED_DIR', tmp_dir), \
                 patch.object(system_routes.db_manager, 'create_cluster_sync_task', return_value=False):
                response = self.client.post('/api/cluster/sync_tasks', json=payload)

        self.assertEqual(200, response.status_code)
        self.assertEqual({'status': 'error', 'message': '同步任务已存在'}, response.json())


    def test_create_sync_task_returns_serialized_task(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sync_file = Path(tmp_dir) / 'NODE-2' / 'task-1.jsonl'
            sync_file.parent.mkdir(parents=True, exist_ok=True)
            sync_file.write_text('{"email":"a@example.com","password":"pw","token_data":"{}"}\n', encoding='utf-8')
            payload = {
                'node_name': 'NODE-2',
                'secret': 'secret',
                'task_id': 'task-1',
                'file_path': str(sync_file),
                'file_size': sync_file.stat().st_size,
                'total_count': 1,
                'file_sha256': hashlib.sha256(sync_file.read_bytes()).hexdigest(),
            }
            stored_task = {
                'task_id': 'task-1',
                'node_name': 'NODE-2',
                'file_path': str(sync_file),
                'file_size': sync_file.stat().st_size,
                'total_count': 1,
                'success_count': 0,
                'fail_count': 0,
                'status': 'pending',
                'error_message': '',
                'retry_count': 0,
                'max_retries': 0,
                'created_at': '2026-05-14 12:00:00',
                'started_at': None,
                'finished_at': None,
                'last_heartbeat': None,
                'file_sha256': payload['file_sha256'],
            }

            with patch.object(system_routes.core_engine.cfg, '_c', {'cluster_secret': 'secret'}), \
                 patch.object(system_routes.cfg, 'CLUSTER_SYNC_REQUIRE_CUSTOM_SECRET', True), \
                 patch.object(system_routes.cfg, 'CLUSTER_SYNC_SHARED_DIR', tmp_dir), \
                 patch.object(system_routes, 'ensure_cluster_sync_worker_started'), \
                 patch.object(system_routes.db_manager, 'create_cluster_sync_task', return_value=True), \
                 patch.object(system_routes.db_manager, 'get_cluster_sync_task', return_value=stored_task), \
                 patch.object(system_routes.core_engine, 'ts', return_value='14:08:22'):
                response = self.client.post('/api/cluster/sync_tasks', json=payload)

        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual('success', body['status'])
        self.assertEqual('task-1', body['task_id'])
        self.assertEqual(0, body['task']['processed_count'])
        self.assertEqual(0.0, body['task']['progress_pct'])

        tasks = [{
            'task_id': 'task-2',
            'node_name': 'NODE-3',
            'file_path': 'data/cluster_sync/NODE-3/task-2.jsonl',
            'file_size': 10,
            'total_count': 5,
            'success_count': 3,
            'fail_count': 1,
            'status': 'running',
            'error_message': '',
            'retry_count': 0,
            'max_retries': 3,
            'created_at': '2026-05-14 12:00:00',
            'started_at': '2026-05-14 12:01:00',
            'finished_at': None,
            'last_heartbeat': '2026-05-14 12:02:00',
        }]

        with patch.object(system_routes, 'ensure_cluster_sync_worker_started'), \
             patch.object(system_routes.db_manager, 'list_cluster_sync_tasks', return_value=tasks):
            response = self.client.get('/api/cluster/sync_tasks', headers=self._auth_headers())

        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual('success', body['status'])
        self.assertEqual(4, body['tasks'][0]['processed_count'])
        self.assertEqual(80.0, body['tasks'][0]['progress_pct'])

    def test_clear_terminal_sync_tasks_removes_only_terminal_statuses(self):
        with patch.object(system_routes, 'ensure_cluster_sync_worker_started'), \
             patch.object(system_routes.db_manager, 'clear_cluster_sync_terminal_tasks', return_value=4):
            response = self.client.post('/api/cluster/sync_tasks/clear_terminal', headers=self._auth_headers())

        self.assertEqual(200, response.status_code)
        self.assertEqual({'status': 'success', 'message': '已清理 4 条终态任务', 'cleared': 4}, response.json())

    def test_clear_terminal_sync_tasks_is_idempotent_when_nothing_to_clear(self):
        with patch.object(system_routes, 'ensure_cluster_sync_worker_started'), \
             patch.object(system_routes.db_manager, 'clear_cluster_sync_terminal_tasks', return_value=0):
            response = self.client.post('/api/cluster/sync_tasks/clear_terminal', headers=self._auth_headers())

        self.assertEqual(200, response.status_code)
        self.assertEqual({'status': 'success', 'message': '已清理 0 条终态任务', 'cleared': 0}, response.json())

    def test_retry_sync_task_always_requires_resync(self):
        with patch.object(system_routes, 'ensure_cluster_sync_worker_started'), \
             patch.object(system_routes.db_manager, 'get_cluster_sync_task', return_value={
                 'task_id': 'task-3',
                 'status': 'failed',
             }):
            response = self.client.post('/api/cluster/sync_tasks/task-3/retry', headers=self._auth_headers())

        self.assertEqual(200, response.status_code)
        self.assertEqual({'status': 'error', 'message': '旧任务文件已清理，请重新同步'}, response.json())

    def test_cancel_sync_task_returns_error_for_invalid_status(self):
        with patch.object(system_routes, 'ensure_cluster_sync_worker_started'), \
             patch.object(system_routes.db_manager, 'get_cluster_sync_task', return_value={
                 'task_id': 'task-5',
                 'status': 'success',
             }), \
             patch.object(system_routes.db_manager, 'cancel_cluster_sync_task', return_value=False):
            response = self.client.post('/api/cluster/sync_tasks/task-5/cancel', headers=self._auth_headers())

        self.assertEqual(200, response.status_code)
        self.assertEqual({'status': 'error', 'message': '仅排队中或导入中的任务支持取消'}, response.json())

    def test_cancel_sync_task_succeeds_for_pending_task(self):
        stored_task = {
            'task_id': 'task-6',
            'node_name': 'NODE-2',
            'file_path': 'data/cluster_sync/NODE-2/task-6.jsonl',
            'file_size': 10,
            'total_count': 1,
            'success_count': 0,
            'fail_count': 0,
            'status': 'cancelled',
            'error_message': '用户取消任务',
            'retry_count': 0,
            'max_retries': 3,
            'created_at': '2026-05-14 12:00:00',
            'started_at': None,
            'finished_at': '2026-05-14 12:05:00',
            'last_heartbeat': '2026-05-14 12:05:00',
        }

        with patch.object(system_routes, 'ensure_cluster_sync_worker_started'), \
             patch.object(system_routes.db_manager, 'get_cluster_sync_task', side_effect=[{'task_id': 'task-6', 'status': 'pending'}, stored_task]), \
             patch.object(system_routes.db_manager, 'cancel_cluster_sync_task', return_value=True), \
             patch.object(system_routes.core_engine, 'ts', return_value='14:08:22'):
            response = self.client.post('/api/cluster/sync_tasks/task-6/cancel', headers=self._auth_headers())

        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual('success', body['status'])
        self.assertEqual('task-6', body['task']['task_id'])
        self.assertEqual('cancelled', body['task']['status'])

    def test_run_cluster_sync_task_flushes_final_progress_and_finalizes_partial_success(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sync_file = Path(tmp_dir) / 'task-4.jsonl'
            sync_file.write_text(
                '{"email":"a@example.com","password":"pw","token_data":"{}"}\n'
                '{"email":"broken@example.com","password":"pw"}\n',
                encoding='utf-8'
            )
            task = {
                'task_id': 'task-4',
                'file_path': str(sync_file),
                'file_size': sync_file.stat().st_size,
                'total_count': 2,
                'file_sha256': hashlib.sha256(sync_file.read_bytes()).hexdigest(),
            }

            with patch.object(system_routes.cfg, 'CLUSTER_SYNC_PROGRESS_FLUSH_EVERY', 100), \
                 patch.object(system_routes.cfg, 'CLUSTER_SYNC_MAX_RECORDS', 100000), \
                 patch.object(system_routes.cfg, 'CLUSTER_SYNC_SHARED_DIR', tmp_dir), \
                 patch.object(system_routes.db_manager, 'get_cluster_sync_task_status', return_value='running'), \
                 patch.object(system_routes.db_manager, 'save_account_to_db', side_effect=[True]), \
                 patch.object(system_routes.db_manager, 'update_cluster_sync_task_progress') as update_progress, \
                 patch.object(system_routes, '_finalize_cluster_sync_task_with_cleanup') as finalize_task, \
                 patch.object(system_routes.core_engine, 'ts', return_value='14:08:22'):
                system_routes._run_cluster_sync_task(task)

        update_progress.assert_called_with('task-4', 1, 1)
        finalize_task.assert_called_with('task-4', 'partial_success', 1, 1, '', str(sync_file))

    def test_run_cluster_sync_task_stops_when_cancel_requested(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sync_file = Path(tmp_dir) / 'task-7.jsonl'
            sync_file.write_text(
                '{"email":"a@example.com","password":"pw","token_data":"{}"}\n'
                '{"email":"b@example.com","password":"pw","token_data":"{}"}\n',
                encoding='utf-8'
            )
            task = {
                'task_id': 'task-7',
                'file_path': str(sync_file),
                'file_size': sync_file.stat().st_size,
                'total_count': 2,
                'file_sha256': hashlib.sha256(sync_file.read_bytes()).hexdigest(),
            }

            status_values = iter(['running', 'running', 'cancel_requested'])

            with patch.object(system_routes.cfg, 'CLUSTER_SYNC_PROGRESS_FLUSH_EVERY', 100), \
                 patch.object(system_routes.cfg, 'CLUSTER_SYNC_MAX_RECORDS', 100000), \
                 patch.object(system_routes.cfg, 'CLUSTER_SYNC_SHARED_DIR', tmp_dir), \
                 patch.object(system_routes.db_manager, 'get_cluster_sync_task_status', side_effect=lambda _: next(status_values)), \
                 patch.object(system_routes.db_manager, 'save_account_to_db', return_value=True) as save_account, \
                 patch.object(system_routes.db_manager, 'update_cluster_sync_task_progress') as update_progress, \
                 patch.object(system_routes, '_finalize_cluster_sync_task_with_cleanup') as finalize_task, \
                 patch.object(system_routes.core_engine, 'ts', return_value='14:08:22'):
                system_routes._run_cluster_sync_task(task)

        self.assertEqual(1, save_account.call_count)
        update_progress.assert_called_with('task-7', 1, 0)
        finalize_task.assert_called_with('task-7', 'cancelled', 1, 0, '用户取消任务', str(sync_file))

    def test_finalize_cluster_sync_task_with_cleanup_deletes_file(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sync_file = Path(tmp_dir) / 'task-cleanup.jsonl'
            sync_file.write_text('demo\n', encoding='utf-8')

            with patch.object(system_routes, '_is_cluster_sync_path_allowed', return_value=True), \
                 patch.object(system_routes.db_manager, 'finalize_cluster_sync_task', return_value=True) as finalize_task:
                result = system_routes._finalize_cluster_sync_task_with_cleanup('task-cleanup', 'success', 1, 0, '', str(sync_file))

        self.assertTrue(result)
        self.assertFalse(sync_file.exists())
        finalize_task.assert_called_with('task-cleanup', 'success', 1, 0, '')

    def test_cleanup_stale_cluster_sync_files_removes_old_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sync_root = Path(tmp_dir)
            stale_file = sync_root / 'NODE-1' / 'stale.jsonl'
            fresh_file = sync_root / 'NODE-1' / 'fresh.jsonl'
            stale_file.parent.mkdir(parents=True, exist_ok=True)
            stale_file.write_text('old\n', encoding='utf-8')
            fresh_file.write_text('new\n', encoding='utf-8')

            now = 1_700_000_000
            old_ts = now - (13 * 3600)
            fresh_ts = now - 60
            __import__('os').utime(stale_file, (old_ts, old_ts))
            __import__('os').utime(fresh_file, (fresh_ts, fresh_ts))

            with patch.object(system_routes.cfg, 'CLUSTER_SYNC_SHARED_DIR', tmp_dir), \
                 patch.object(system_routes.cfg, 'CLUSTER_SYNC_STALE_FILE_MAX_AGE_HOURS', 12), \
                 patch.object(system_routes.time, 'time', return_value=now), \
                 patch.object(system_routes.core_engine, 'ts', return_value='14:08:22'):
                system_routes._cleanup_stale_cluster_sync_files()

            self.assertFalse(stale_file.exists())
            self.assertTrue(fresh_file.exists())

if __name__ == '__main__':
    unittest.main()
