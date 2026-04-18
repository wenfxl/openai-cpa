import unittest
from unittest.mock import patch

import utils.config as cfg


class RawProxyPoolTests(unittest.TestCase):
    def _queue_values(self):
        return [cfg.unpack_proxy_queue_item(item)[1] for item in list(cfg.PROXY_QUEUE.queue)]

    def setUp(self):
        self._original_queue = list(cfg.PROXY_QUEUE.queue)
        self._original_unfinished_tasks = cfg.PROXY_QUEUE.unfinished_tasks
        self._original_queue_generation = cfg.PROXY_QUEUE_GENERATION
        self._original_raw_enable = getattr(cfg, "_raw_proxy_enable", False)
        self._original_raw_list = list(getattr(cfg, "RAW_PROXY_LIST", []))
        self._original_clash_enable = cfg._clash_enable
        self._original_clash_pool_mode = cfg._clash_pool_mode
        self._original_warp_list = list(cfg.WARP_PROXY_LIST)
        with cfg.PROXY_QUEUE.mutex:
            cfg.PROXY_QUEUE.queue.clear()
            cfg.PROXY_QUEUE.unfinished_tasks = 0
            cfg.PROXY_QUEUE.all_tasks_done.notify_all()

    def tearDown(self):
        cfg._raw_proxy_enable = self._original_raw_enable
        cfg.RAW_PROXY_LIST = self._original_raw_list
        cfg._clash_enable = self._original_clash_enable
        cfg._clash_pool_mode = self._original_clash_pool_mode
        cfg.WARP_PROXY_LIST = self._original_warp_list
        cfg.PROXY_QUEUE_GENERATION = self._original_queue_generation
        with cfg.PROXY_QUEUE.mutex:
            cfg.PROXY_QUEUE.queue.clear()
            cfg.PROXY_QUEUE.unfinished_tasks = self._original_unfinished_tasks
            for item in self._original_queue:
                cfg.PROXY_QUEUE.queue.append(item)
            if cfg.PROXY_QUEUE.unfinished_tasks == 0:
                cfg.PROXY_QUEUE.all_tasks_done.notify_all()

    def test_normalize_raw_proxy_entry_accepts_standard_socks_url(self):
        self.assertEqual(
            "socks5h://user:pass@127.0.0.1:1080",
            cfg.normalize_raw_proxy_entry("socks5://user:pass@127.0.0.1:1080"),
        )

    def test_normalize_raw_proxy_entry_accepts_http_url(self):
        self.assertEqual(
            "http://user:pass@127.0.0.1:8080",
            cfg.normalize_raw_proxy_entry("http://user:pass@127.0.0.1:8080"),
        )

    def test_normalize_raw_proxy_entry_preserves_pre_encoded_credentials(self):
        self.assertEqual(
            "socks5h://user:p%40ss@127.0.0.1:1080",
            cfg.normalize_raw_proxy_entry("socks5://user:p%40ss@127.0.0.1:1080"),
        )

    def test_normalize_raw_proxy_entry_accepts_colon_separated_auth_format(self):
        self.assertEqual(
            "socks5h://user:pass@127.0.0.1:1080",
            cfg.normalize_raw_proxy_entry("127.0.0.1:1080:user:pass"),
        )

    def test_normalize_raw_proxy_entry_defaults_host_port_to_socks5h(self):
        self.assertEqual(
            "socks5h://127.0.0.1:1080",
            cfg.normalize_raw_proxy_entry("127.0.0.1:1080"),
        )

    def test_reload_all_configs_prefers_raw_pool_over_clash_pool(self):
        fake_config = {
            "default_proxy": "http://default:8000",
            "clash_proxy_pool": {
                "enable": True,
                "pool_mode": True,
            },
            "warp_proxy_list": [
                "http://127.0.0.1:41001",
                "http://127.0.0.1:41002",
            ],
            "raw_proxy_pool": {
                "enable": True,
                "proxy_list": [
                    "http://user:pass@127.0.0.1:8080",
                    "socks5://other:pwd@127.0.0.2:1081",
                ],
            },
        }

        with patch("utils.config.init_config", return_value=fake_config), patch(
            "utils.config.reload_proxy_config"
        ):
            cfg.reload_all_configs()

        self.assertTrue(cfg.is_raw_proxy_pool_enabled())
        self.assertFalse(cfg.is_clash_proxy_pool_enabled())
        self.assertTrue(cfg.is_queue_proxy_pool_enabled())
        self.assertFalse(cfg.pooled_proxy_requires_clash_switch())
        self.assertFalse(cfg.is_shared_clash_switch_enabled())
        self.assertFalse(cfg._clash_enable)
        self.assertEqual(
            [
                "http://user:pass@127.0.0.1:8080",
                "socks5h://other:pwd@127.0.0.2:1081",
            ],
            self._queue_values(),
        )

    def test_reload_all_configs_resets_queue_generation_and_unfinished_tasks(self):
        fake_config = {
            "default_proxy": "http://default:8000",
            "raw_proxy_pool": {
                "enable": True,
                "proxy_list": [
                    "127.0.0.1:1080:user:pass",
                    "127.0.0.2:1081:user:pass",
                ],
            },
        }

        with patch("utils.config.init_config", return_value=fake_config), patch(
            "utils.config.reload_proxy_config"
        ):
            cfg.reload_all_configs()
            first_generation = cfg.PROXY_QUEUE_GENERATION
            self.assertEqual(2, cfg.PROXY_QUEUE.unfinished_tasks)
            cfg.reload_all_configs()

        self.assertEqual(first_generation + 1, cfg.PROXY_QUEUE_GENERATION)
        self.assertEqual(2, cfg.PROXY_QUEUE.unfinished_tasks)
        self.assertEqual(
            [
                "socks5h://user:pass@127.0.0.1:1080",
                "socks5h://user:pass@127.0.0.2:1081",
            ],
            self._queue_values(),
        )

    def test_pooled_proxy_slot_drops_stale_proxy_after_reload(self):
        old_config = {
            "default_proxy": "http://default:8000",
            "raw_proxy_pool": {
                "enable": True,
                "proxy_list": [
                    "127.0.0.1:1080:user:pass",
                ],
            },
        }
        new_config = {
            "default_proxy": "http://default:8000",
            "raw_proxy_pool": {
                "enable": True,
                "proxy_list": [
                    "127.0.0.9:1090:user:pass",
                ],
            },
        }

        with patch("utils.config.init_config", return_value=old_config), patch(
            "utils.config.reload_proxy_config"
        ):
            cfg.reload_all_configs()

        borrowed_generation, proxy = cfg.unpack_proxy_queue_item(cfg.PROXY_QUEUE.get())
        self.assertEqual("socks5h://user:pass@127.0.0.1:1080", proxy)

        with patch("utils.config.init_config", return_value=new_config), patch(
            "utils.config.reload_proxy_config"
        ):
            cfg.reload_all_configs()

        if cfg.should_return_pooled_proxy(borrowed_generation):
            cfg.PROXY_QUEUE.put(cfg.make_proxy_queue_item(proxy, borrowed_generation))
            cfg.PROXY_QUEUE.task_done()

        self.assertEqual(
            ["socks5h://user:pass@127.0.0.9:1090"],
            self._queue_values(),
        )
        self.assertEqual(1, cfg.PROXY_QUEUE.unfinished_tasks)

    def test_dequeued_proxy_carries_current_generation_after_reload(self):
        fake_config = {
            "default_proxy": "http://default:8000",
            "raw_proxy_pool": {
                "enable": True,
                "proxy_list": [
                    "127.0.0.9:1090:user:pass",
                ],
            },
        }

        with patch("utils.config.init_config", return_value=fake_config), patch(
            "utils.config.reload_proxy_config"
        ):
            cfg.reload_all_configs()

        borrowed_generation, proxy = cfg.unpack_proxy_queue_item(cfg.PROXY_QUEUE.get())
        self.assertEqual(cfg.PROXY_QUEUE_GENERATION, borrowed_generation)
        self.assertTrue(cfg.should_return_pooled_proxy(borrowed_generation))
        cfg.PROXY_QUEUE.put(cfg.make_proxy_queue_item(proxy, borrowed_generation))
        cfg.PROXY_QUEUE.task_done()

        self.assertEqual(
            ["socks5h://user:pass@127.0.0.9:1090"],
            self._queue_values(),
        )
        self.assertEqual(1, cfg.PROXY_QUEUE.unfinished_tasks)


if __name__ == "__main__":
    unittest.main()
