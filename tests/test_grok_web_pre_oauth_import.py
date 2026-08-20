import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# register imports browser modules that are unavailable in the minimal test image.
sys.modules.setdefault("camoufox", types.SimpleNamespace(Camoufox=object))
# mock core_engine and its dependencies to avoid DLL import failures
_ce = types.ModuleType("utils.core_engine")
_ce.grok2api_admin_login = lambda: (True, "admin-token", "ok")
_ce._grok2api_import_web_sso = lambda sso, token: (True, "web result")
_ce._grok2api_import_expires_at = lambda d: ""
_ce._grok2api_import_payload = lambda d: {}
_ce.cfg = None
_ce.ts = lambda: ""
sys.modules["utils.core_engine"] = _ce
# mock hero_sms, auth_core to avoid DLL import failures
_hero = types.ModuleType("utils.integrations.hero_sms")
_hero._try_verify_phone_via_hero_sms = lambda *a, **k: (False, "")
_hero.get_phone_for_signup = lambda *a, **k: (False, "")
_hero.wait_code_for_signup = lambda *a, **k: (False, "")
_hero.report_signup_result = lambda *a, **k: None
sys.modules["utils.integrations.hero_sms"] = _hero
_core = types.ModuleType("utils.auth_core")
_core.generate_payload = lambda *a, **k: {}
sys.modules["utils.auth_core"] = _core

from utils.grok_auth import register


class _OAuth:
    token = {"access_token": "build-access", "refresh_token": "build-refresh"}
    userinfo = {"email": "test@example.com"}


def _run(monkeypatch, events, *, enabled=True, web_ok=True):
    monkeypatch.setattr(register.cfg, "GROK2API_IMPORT_SSO_AS_GROK_WEB", enabled, raising=False)
    monkeypatch.setattr(register, "ensure_camoufox", lambda force=False: (True, ""))
    monkeypatch.setattr(register, "get_email_and_token", lambda *a, **k: ("test@example.com", "mail-token"))
    monkeypatch.setattr(register, "set_last_email", lambda email: None)
    monkeypatch.setattr(register, "signup_with_camoufox", lambda *a, **k: {
        "ok": True,
        "sso": "sso-secret",
        "cookies": {"sso": "sso-secret"},
    })

    def complete(*args, **kwargs):
        events.append("build_oauth")
        return _OAuth()

    monkeypatch.setattr(register, "complete_build_oauth", complete)
    monkeypatch.setattr(register, "build_cliproxyapi_auth_record", lambda *a, **k: {"email": "test@example.com"})

    import utils.core_engine as core_engine
    monkeypatch.setattr(core_engine, "grok2api_admin_login", lambda: (True, "admin-token", "ok"))

    def import_web(sso, token):
        events.append("grok_web")
        assert sso == "sso-secret"
        assert token == "admin-token"
        return web_ok, "web result"

    monkeypatch.setattr(core_engine, "_grok2api_import_web_sso", import_web)
    ctx = {}
    result = register.run(run_ctx=ctx)
    return result, ctx


def test_enabled_imports_grok_web_before_build_oauth(monkeypatch):
    events = []
    result, ctx = _run(monkeypatch, events, enabled=True, web_ok=True)
    assert result[0]
    assert events == ["grok_web", "build_oauth"]
    assert ctx["grok_web_import_ok"] is True


def test_grok_web_failure_does_not_block_build_oauth(monkeypatch):
    events = []
    result, ctx = _run(monkeypatch, events, enabled=True, web_ok=False)
    assert result[0]
    assert events == ["grok_web", "build_oauth"]
    assert ctx["grok_web_import_ok"] is False


def test_disabled_skips_grok_web_and_keeps_build_oauth(monkeypatch):
    events = []
    result, ctx = _run(monkeypatch, events, enabled=False)
    assert result[0]
    assert events == ["build_oauth"]
    assert "grok_web_import_ok" not in ctx


def _run_sso_only(monkeypatch, events, *, enabled=True, web_ok=True):
    monkeypatch.setattr(register.cfg, "GROK2API_IMPORT_SSO_AS_GROK_WEB", enabled, raising=False)
    monkeypatch.setattr(register, "ensure_camoufox", lambda force=False: (True, ""))
    monkeypatch.setattr(register, "get_email_and_token", lambda *a, **k: ("test@example.com", "mail-token"))
    monkeypatch.setattr(register, "set_last_email", lambda email: None)
    monkeypatch.setattr(register, "signup_with_camoufox", lambda *a, **k: {
        "ok": True,
        "sso": "sso-secret",
        "cookies": {"sso": "sso-secret"},
    })

    # SSO-only mode should NOT call complete_build_oauth
    def complete(*args, **kwargs):
        events.append("build_oauth")
        return _OAuth()

    monkeypatch.setattr(register, "complete_build_oauth", complete)

    import utils.core_engine as core_engine
    monkeypatch.setattr(core_engine, "grok2api_admin_login", lambda: (True, "admin-token", "ok"))

    def import_web(sso, token):
        events.append("grok_web")
        assert sso == "sso-secret"
        assert token == "admin-token"
        return web_ok, "web result"

    monkeypatch.setattr(core_engine, "_grok2api_import_web_sso", import_web)
    ctx = {}
    result = register.run(run_ctx=ctx, sso_only=True)
    return result, ctx


def test_sso_only_skips_build_oauth_and_imports_grok_web(monkeypatch):
    events = []
    result, ctx = _run_sso_only(monkeypatch, events, enabled=True, web_ok=True)
    token_json_str, password = result
    assert token_json_str is not None
    assert password
    # Should only have grok_web, NOT build_oauth
    assert events == ["grok_web"]
    # Verify the returned JSON has SSO fields
    import json
    record = json.loads(token_json_str)
    assert record["email"] == "test@example.com"
    assert record["sso"] == "sso-secret"
    assert record["password"] == password
    assert record["status"] == "grok_sso"
    assert record["provider"] == "grok"


def test_sso_only_with_grok_web_failure(monkeypatch):
    events = []
    result, ctx = _run_sso_only(monkeypatch, events, enabled=True, web_ok=False)
    token_json_str, password = result
    assert token_json_str is not None
    assert events == ["grok_web"]
    assert ctx["grok_web_import_ok"] is False


def test_sso_only_disabled_grok_web_import(monkeypatch):
    events = []
    result, ctx = _run_sso_only(monkeypatch, events, enabled=False)
    token_json_str, password = result
    assert token_json_str is not None
    # No grok_web import, no build_oauth
    assert events == []
    assert "grok_web_import_ok" not in ctx


def test_sso_only_with_risk_check(monkeypatch):
    """SSO-only 模式下，风控检测（DISCARD_ON_DOWNGRADE）仍应执行"""
    events = []
    monkeypatch.setattr(register.cfg, "GROK2API_IMPORT_SSO_AS_GROK_WEB", True, raising=False)
    monkeypatch.setattr(register, "ensure_camoufox", lambda force=False: (True, ""))
    monkeypatch.setattr(register, "get_email_and_token", lambda *a, **k: ("test@example.com", "mail-token"))
    monkeypatch.setattr(register, "set_last_email", lambda email: None)
    monkeypatch.setattr(register, "signup_with_camoufox", lambda *a, **k: {
        "ok": True,
        "sso": "sso-secret",
        "cookies": {"sso": "sso-secret"},
    })

    risk_checked = []
    def fake_inspect(cookies, proxy=""):
        risk_checked.append(True)
        return {"found": True, "bot_flag_source": 0, "denied": False, "risk": 0.0, "error": ""}
    monkeypatch.setattr(register, "inspect_sso_account_state", fake_inspect)
    monkeypatch.setattr(register.cfg, "DISCARD_ON_DOWNGRADE", True, raising=False)

    import utils.core_engine as core_engine
    def import_web(sso, token):
        events.append("grok_web")
        return True, "web result"
    monkeypatch.setattr(core_engine, "_grok2api_import_web_sso", import_web)

    ctx = {}
    result = register.run(run_ctx=ctx, sso_only=True)
    token_json_str, password = result
    assert token_json_str is not None
    # 风险检测应被调用
    assert risk_checked == [True]
    # 只有 grok_web，没有 build_oauth
    assert events == ["grok_web"]
