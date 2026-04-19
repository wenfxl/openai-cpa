import importlib
import sys
import types
import unittest
from unittest.mock import patch


_yaml_module = types.SimpleNamespace(
    safe_load=lambda *args, **kwargs: {},
    dump=lambda *args, **kwargs: None,
)


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = ""

    def json(self):
        return self._payload


class Sub2APIProxyPoolTests(unittest.TestCase):
    repo_module_names = [
        "utils.config",
        "utils.proxy_manager",
        "utils.integrations.sub2api_client",
        "utils.integrations.sub2api_proxy",
    ]

    def setUp(self):
        fake_requests_module = types.SimpleNamespace(
            post=None,
            get=None,
            put=None,
            patch=None,
            delete=None,
            Response=object,
            exceptions=types.SimpleNamespace(ConnectionError=Exception, Timeout=TimeoutError),
        )

        self.original_modules = {}
        for module_name in ["curl_cffi", "requests", "yaml", *self.repo_module_names]:
            if module_name in sys.modules:
                self.original_modules[module_name] = sys.modules[module_name]
            sys.modules.pop(module_name, None)

        sys.modules["curl_cffi"] = types.SimpleNamespace(requests=fake_requests_module)
        sys.modules["requests"] = types.SimpleNamespace(get=None, put=None)
        sys.modules["yaml"] = _yaml_module

        self.cfg = importlib.import_module("utils.config")
        self.proxy_module = importlib.import_module("utils.integrations.sub2api_proxy")
        self.client_module = importlib.import_module("utils.integrations.sub2api_client")

        self.Sub2APIClient = self.client_module.Sub2APIClient
        self.build_sub2api_export_bundle = self.client_module.build_sub2api_export_bundle
        self.get_sub2api_push_settings = self.client_module.get_sub2api_push_settings
        self.normalize_sub2api_proxy_urls = self.proxy_module.normalize_sub2api_proxy_urls

        self.cfg_patches = [
            patch.object(self.cfg, "SUB2API_DEFAULT_PROXY", "socks5://user1:pass1@1.1.1.1:1080\nhttp://2.2.2.2:8080"),
            patch.object(self.cfg, "SUB2API_ACCOUNT_CONCURRENCY", 10),
            patch.object(self.cfg, "SUB2API_ACCOUNT_LOAD_FACTOR", 10),
            patch.object(self.cfg, "SUB2API_ACCOUNT_PRIORITY", 1),
            patch.object(self.cfg, "SUB2API_ACCOUNT_RATE_MULTIPLIER", 1.0),
            patch.object(self.cfg, "SUB2API_ACCOUNT_GROUP_IDS", []),
            patch.object(self.cfg, "SUB2API_ENABLE_WS_MODE", True),
        ]
        for item in self.cfg_patches:
            item.start()
        setattr(
            self.cfg,
            "SUB2API_DEFAULT_PROXY_POOL",
            [
                "socks5://user1:pass1@1.1.1.1:1080",
                "http://2.2.2.2:8080",
            ],
        )
        self.cfg.reset_sub2api_proxy_rotation()

    def tearDown(self):
        for item in reversed(self.cfg_patches):
            item.stop()

        for module_name in ["curl_cffi", "requests", "yaml", *self.repo_module_names]:
            sys.modules.pop(module_name, None)
        sys.modules.update(self.original_modules)

    def test_add_account_rotates_proxy_pool_and_uses_import_endpoint(self):
        captured_posts = []

        def fake_post(url, json=None, headers=None, **kwargs):
            captured_posts.append({"url": url, "json": json, "headers": headers})
            if url.endswith("/api/v1/admin/accounts/data"):
                return _FakeResponse(201, {"status": "ok"})
            if url.endswith("/api/v1/admin/accounts"):
                return _FakeResponse(201, {"data": {}})
            raise AssertionError(f"unexpected url: {url}")

        token_a = {"email": "alpha@example.com", "refresh_token": "rt-alpha"}
        token_b = {"email": "beta@example.com", "refresh_token": "rt-beta"}

        with patch.object(self.client_module.cffi_requests, "post", side_effect=fake_post):
            client = self.Sub2APIClient(api_url="https://sub2api.example", api_key="demo-key")
            ok_a, _ = client.add_account(dict(token_a))
            ok_b, _ = client.add_account(dict(token_b))

        self.assertTrue(ok_a)
        self.assertTrue(ok_b)
        self.assertEqual(
            [
                "https://sub2api.example/api/v1/admin/accounts/data",
                "https://sub2api.example/api/v1/admin/accounts/data",
            ],
            [item["url"] for item in captured_posts],
        )
        first_payload = captured_posts[0]["json"]["data"]
        second_payload = captured_posts[1]["json"]["data"]
        self.assertEqual(
            "socks5|1.1.1.1|1080|user1|pass1",
            first_payload["accounts"][0]["proxy_key"],
        )
        self.assertEqual(
            "http|2.2.2.2|8080||",
            second_payload["accounts"][0]["proxy_key"],
        )
        self.assertEqual(1, len(first_payload["proxies"]))
        self.assertEqual(1, len(second_payload["proxies"]))

    def test_add_account_preserves_zero_and_single_proxy_semantics(self):
        captured_posts = []

        def fake_post(url, json=None, headers=None, **kwargs):
            captured_posts.append({"url": url, "json": json, "headers": headers})
            if url.endswith("/api/v1/admin/accounts/data"):
                return _FakeResponse(201, {"status": "ok"})
            if url.endswith("/api/v1/admin/accounts"):
                return _FakeResponse(201, {"data": {}})
            raise AssertionError(f"unexpected url: {url}")

        with patch.object(self.client_module.cffi_requests, "post", side_effect=fake_post):
            client = self.Sub2APIClient(api_url="https://sub2api.example", api_key="demo-key")

            self.cfg.SUB2API_DEFAULT_PROXY = ""
            self.cfg.SUB2API_DEFAULT_PROXY_POOL = []
            self.cfg.reset_sub2api_proxy_rotation()
            ok_zero, _ = client.add_account({"email": "zero@example.com", "refresh_token": "rt-zero"})

            self.cfg.SUB2API_DEFAULT_PROXY = "http://2.2.2.2:8080"
            self.cfg.SUB2API_DEFAULT_PROXY_POOL = ["http://2.2.2.2:8080"]
            self.cfg.reset_sub2api_proxy_rotation()
            ok_one_a, _ = client.add_account({"email": "one-a@example.com", "refresh_token": "rt-one-a"})
            ok_one_b, _ = client.add_account({"email": "one-b@example.com", "refresh_token": "rt-one-b"})

        self.assertTrue(ok_zero)
        self.assertTrue(ok_one_a)
        self.assertTrue(ok_one_b)
        self.assertEqual(
            [
                "https://sub2api.example/api/v1/admin/accounts",
                "https://sub2api.example/api/v1/admin/accounts/data",
                "https://sub2api.example/api/v1/admin/accounts/data",
            ],
            [item["url"] for item in captured_posts],
        )
        self.assertNotIn("proxy_key", captured_posts[0]["json"])
        self.assertEqual(
            "http|2.2.2.2|8080||",
            captured_posts[1]["json"]["data"]["accounts"][0]["proxy_key"],
        )
        self.assertEqual(
            "http|2.2.2.2|8080||",
            captured_posts[2]["json"]["data"]["accounts"][0]["proxy_key"],
        )

    def test_reload_all_configs_accepts_list_and_filters_invalid_proxies(self):
        with patch("utils.config.init_config", return_value={}), \
                patch("utils.config.reload_proxy_config"):
            self.cfg.reload_all_configs({
                "sub2api_mode": {
                    "default_proxy": [
                        "bad-proxy",
                        "http://2.2.2.2:8080",
                    ]
                }
            })

        self.cfg.reset_sub2api_proxy_rotation()
        self.assertEqual(["http://2.2.2.2:8080"], self.cfg.SUB2API_DEFAULT_PROXY_POOL)
        self.assertEqual("http://2.2.2.2:8080", self.cfg.get_next_sub2api_proxy_url())

    def test_export_bundle_truncates_long_account_name(self):
        email = ("a" * 80) + "@example.com"
        bundle = self.build_sub2api_export_bundle(
            [{"email": email, "refresh_token": "rt-long"}],
            self.get_sub2api_push_settings(),
        )
        self.assertEqual(email[:64], bundle["accounts"][0]["name"])

    def test_normalize_proxy_urls_preserves_commas_inside_userinfo(self):
        self.assertEqual(
            ["http://user:pa,ss@host.example:8080"],
            self.normalize_sub2api_proxy_urls("http://user:pa,ss@host.example:8080"),
        )


if __name__ == "__main__":
    unittest.main()
