import sys
import types
import unittest
from unittest.mock import patch


fake_requests_module = types.SimpleNamespace(
    post=None,
    get=None,
    put=None,
    patch=None,
    delete=None,
    Response=object,
    exceptions=types.SimpleNamespace(ConnectionError=Exception, Timeout=TimeoutError),
)
sys.modules.setdefault("curl_cffi", types.SimpleNamespace(requests=fake_requests_module))
sys.modules.setdefault("requests", types.SimpleNamespace(get=None, put=None))
sys.modules["yaml"] = types.SimpleNamespace(
    safe_load=lambda *args, **kwargs: {},
    dump=lambda *args, **kwargs: None,
)

from utils import config as cfg
from utils.integrations.sub2api_client import Sub2APIClient, build_sub2api_export_bundle, get_sub2api_push_settings
from utils.integrations.sub2api_proxy import normalize_sub2api_proxy_urls


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = ""

    def json(self):
        return self._payload


class Sub2APIProxyPoolTests(unittest.TestCase):
    def setUp(self):
        self.cfg_patches = [
            patch.object(cfg, "SUB2API_DEFAULT_PROXY", "socks5://user1:pass1@1.1.1.1:1080\nhttp://2.2.2.2:8080"),
            patch.object(cfg, "SUB2API_ACCOUNT_CONCURRENCY", 10),
            patch.object(cfg, "SUB2API_ACCOUNT_LOAD_FACTOR", 10),
            patch.object(cfg, "SUB2API_ACCOUNT_PRIORITY", 1),
            patch.object(cfg, "SUB2API_ACCOUNT_RATE_MULTIPLIER", 1.0),
            patch.object(cfg, "SUB2API_ACCOUNT_GROUP_IDS", []),
            patch.object(cfg, "SUB2API_ENABLE_WS_MODE", True),
        ]
        for item in self.cfg_patches:
            item.start()
        setattr(
            cfg,
            "SUB2API_DEFAULT_PROXY_POOL",
            [
                "socks5://user1:pass1@1.1.1.1:1080",
                "http://2.2.2.2:8080",
            ],
        )
        reset_rotation = getattr(cfg, "reset_sub2api_proxy_rotation", None)
        if callable(reset_rotation):
            reset_rotation()

    def tearDown(self):
        for item in reversed(self.cfg_patches):
            item.stop()

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

        with patch("utils.integrations.sub2api_client.cffi_requests.post", side_effect=fake_post):
            client = Sub2APIClient(api_url="https://sub2api.example", api_key="demo-key")
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

    def test_reload_all_configs_accepts_list_and_filters_invalid_proxies(self):
        with patch("utils.config.init_config", return_value={}), \
                patch("utils.config.reload_proxy_config"):
            cfg.reload_all_configs({
                "sub2api_mode": {
                    "default_proxy": [
                        "bad-proxy",
                        "http://2.2.2.2:8080",
                    ]
                }
            })

        cfg.reset_sub2api_proxy_rotation()
        self.assertEqual(["http://2.2.2.2:8080"], cfg.SUB2API_DEFAULT_PROXY_POOL)
        self.assertEqual("http://2.2.2.2:8080", cfg.get_next_sub2api_proxy_url())

    def test_export_bundle_truncates_long_account_name(self):
        email = ("a" * 80) + "@example.com"
        bundle = build_sub2api_export_bundle(
            [{"email": email, "refresh_token": "rt-long"}],
            get_sub2api_push_settings(),
        )
        self.assertEqual(email[:64], bundle["accounts"][0]["name"])

    def test_normalize_proxy_urls_preserves_commas_inside_userinfo(self):
        self.assertEqual(
            ["http://user:pa,ss@host.example:8080"],
            normalize_sub2api_proxy_urls("http://user:pa,ss@host.example:8080"),
        )


if __name__ == "__main__":
    unittest.main()
