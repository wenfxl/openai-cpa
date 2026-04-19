import unittest
import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

# Stub heavy third-party and project dependencies so we can import
# RegEngine without installing the full runtime stack.
_stubs = {
    "yaml": types.SimpleNamespace(
        safe_load=lambda *a, **kw: {},
        safe_dump=lambda *a, **kw: "",
    ),
    "curl_cffi": types.SimpleNamespace(
        requests=types.SimpleNamespace(),
        CurlMime=object,
    ),
    "utils.email_providers.mail_service": types.SimpleNamespace(
        mask_email=lambda value: value,
    ),
    "utils.register": types.SimpleNamespace(
        run=lambda *args, **kwargs: None,
        refresh_oauth_token=lambda *args, **kwargs: (False, {}),
    ),
    "utils.proxy_manager": types.SimpleNamespace(
        smart_switch_node=lambda *args, **kwargs: True,
        reload_proxy_config=lambda *args, **kwargs: None,
    ),
    "utils.integrations.sub2api_client": types.SimpleNamespace(
        Sub2APIClient=object,
    ),
    "utils.integrations.tg_notifier": types.SimpleNamespace(
        send_tg_msg_sync=lambda *args, **kwargs: None,
    ),
}
for mod_name, stub in _stubs.items():
    if mod_name not in sys.modules:
        sys.modules[mod_name] = stub

from utils.core_engine import RegEngine


class RegEngineExecutorCleanupTests(unittest.TestCase):
    def test_start_normal_reclaims_executor_after_natural_completion(self):
        engine = RegEngine()
        executor = Mock()
        engine._executor = executor
        args = SimpleNamespace()

        with patch("utils.core_engine.normal_main_loop", side_effect=lambda *a, **kw: None):
            engine.start_normal(args)
            engine.current_thread.join(timeout=2)

        self.assertFalse(engine.current_thread.is_alive())
        executor.shutdown.assert_called_once_with(wait=False)
        self.assertIsNone(engine._executor)

    def test_run_threads_reclaim_executor_after_natural_completion(self):
        cases = [
            ("_run_cpa_in_thread", "_cpa_wrapper"),
            ("_run_sub2api_in_thread", "sub2api_main_loop"),
            ("_run_check_in_thread", "manual_check_main_loop"),
        ]

        for runner_name, target_name in cases:
            with self.subTest(runner=runner_name):
                engine = RegEngine()
                executor = Mock()
                engine._executor = executor
                args = SimpleNamespace()

                if target_name.startswith("_"):
                    setattr(engine, target_name, AsyncMock(return_value=None))
                    getattr(engine, runner_name)(args)
                else:
                    with patch(f"utils.core_engine.{target_name}", new=AsyncMock(return_value=None)):
                        getattr(engine, runner_name)(args)

                executor.shutdown.assert_called_once_with(wait=False)
                self.assertIsNone(engine._executor)


if __name__ == "__main__":
    unittest.main()
