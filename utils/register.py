import base64
import gc
import hashlib
import json
import os
import random
import re
import secrets
import string
import time
import uuid
import urllib.parse
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from curl_cffi import requests
from utils import config as cfg
from utils.email_providers.mail_service import get_email_and_token, get_oai_code, mask_email
from utils.integrations.hero_sms import _try_verify_phone_via_hero_sms
from utils.auth_core import generate_payload

AUTH_URL = "https://auth.openai.com/oauth/authorize"
TOKEN_URL = "https://auth.openai.com/oauth/token"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
DEFAULT_REDIRECT_URI = "http://localhost:1455/auth/callback"
DEFAULT_SCOPE = "openid email profile offline_access"

FIRST_NAMES = [
    "James", "John", "Robert", "Michael", "William", "David", "Richard",
    "Joseph", "Thomas", "Charles", "Emma", "Olivia", "Ava", "Isabella",
    "Sophia", "Mia", "Charlotte", "Amelia", "Harper", "E Evelyn",
]
LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
    "Davis", "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez",
    "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin",
]


def _ssl_verify() -> bool:
    flag = os.getenv("OPENAI_SSL_VERIFY", "1").strip().lower()
    return flag not in {"0", "false", "no", "off"}


def _skip_net_check() -> bool:
    flag = os.getenv("SKIP_NET_CHECK", "0").strip().lower()
    return flag in {"1", "true", "yes", "on"}


def _b64url_no_pad(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _sha256_b64url_no_pad(s: str) -> str:
    return _b64url_no_pad(hashlib.sha256(s.encode("ascii")).digest())


def _random_state(nbytes: int = 16) -> str:
    return secrets.token_urlsafe(nbytes)


def _pkce_verifier() -> str:
    return secrets.token_urlsafe(64)


def _parse_callback_url(callback_url: str) -> Dict[str, Any]:
    candidate = callback_url.strip()
    if not candidate:
        return {"code": "", "state": "", "error": "", "error_description": ""}
    if "://" not in candidate:
        if candidate.startswith("?"):
            candidate = f"http://localhost{candidate}"
        elif any(ch in candidate for ch in "/?#") or ":" in candidate:
            candidate = f"http://{candidate}"
        elif "=" in candidate:
            candidate = f"http://localhost/?{candidate}"
    parsed = urllib.parse.urlparse(candidate)
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    fragment = urllib.parse.parse_qs(parsed.fragment, keep_blank_values=True)
    for key, values in fragment.items():
        if key not in query or not query[key] or not (query[key][0] or "").strip():
            query[key] = values

    def get1(k: str) -> str:
        v = query.get(k, [""])
        return (v[0] or "").strip()

    code = get1("code")
    state = get1("state")
    error = get1("error")
    error_description = get1("error_description")
    if code and not state and "#" in code:
        code, state = code.split("#", 1)
    if not error and error_description:
        error, error_description = error_description, ""
    return {"code": code, "state": state, "error": error,
            "error_description": error_description}


def _jwt_claims_no_verify(id_token: str) -> Dict[str, Any]:
    if not id_token or id_token.count(".") < 2:
        return {}
    payload_b64 = id_token.split(".")[1]
    pad = "=" * ((4 - (len(payload_b64) % 4)) % 4)
    try:
        return json.loads(
            base64.urlsafe_b64decode((payload_b64 + pad).encode("ascii")).decode("utf-8")
        )
    except Exception:
        return {}


def _decode_jwt_segment(seg: str) -> Dict[str, Any]:
    raw = (seg or "").strip()
    if not raw:
        return {}
    pad = "=" * ((4 - (len(raw) % 4)) % 4)
    try:
        return json.loads(
            base64.urlsafe_b64decode((raw + pad).encode("ascii")).decode("utf-8")
        )
    except Exception:
        return {}


def _to_int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _post_form(
        url: str,
        data: Dict[str, str],
        proxies: Any = None,
        timeout: int = 30,
        retries: int = 3,
) -> Dict[str, Any]:
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    last_error: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            resp = requests.post(
                url, data=data, headers=headers,
                proxies=proxies, verify=_ssl_verify(),
                timeout=timeout, impersonate="chrome110",
            )
            if resp.status_code != 200:
                raise RuntimeError(
                    f"token exchange failed: {resp.status_code}: {resp.text}"
                )
            return resp.json()
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                print(f"\n[{cfg.ts()}] [WARNING] 换取 Token 时遇到网络异常: {exc}。"
                      f"准备第 {attempt + 1}/{retries} 次重试...")
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(
        f"token exchange failed after {retries} retries: {last_error}"
    ) from last_error


def _post_with_retry(
        session: requests.Session,
        url: str,
        *,
        headers: Dict[str, Any],
        data: Any = None,
        json_body: Any = None,
        proxies: Any = None,
        timeout: int = 30,
        retries: int = 2,
        allow_redirects: bool = True,
) -> Any:
    last_error: Optional[Exception] = None
    for attempt in range(retries + 1):
        if getattr(cfg, 'GLOBAL_STOP', False): raise RuntimeError("系统已停止，强制中断网络请求")
        try:
            if json_body is not None:
                return session.post(
                    url, headers=headers, json=json_body,
                    proxies=proxies, verify=_ssl_verify(),
                    timeout=timeout, allow_redirects=allow_redirects,
                )
            return session.post(
                url, headers=headers, data=data,
                proxies=proxies, verify=_ssl_verify(),
                timeout=timeout, allow_redirects=allow_redirects,
            )
        except Exception as e:
            last_error = e
            if attempt >= retries:
                break
            time.sleep(2 * (attempt + 1))
    if last_error:
        raise last_error
    raise RuntimeError("Request failed without exception")


def _oai_headers(did: str, extra: dict = None) -> dict:
    h = {
        "accept": "application/json",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/110.0.0.0 Safari/537.36"
        ),
        "sec-ch-ua": '"Google Chrome";v="110", "Chromium";v="110", "Not_A Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "oai-device-id": did,
    }
    if extra:
        h.update(extra)
    return h


_cached_browser_token = None


def _follow_redirect_chain_local(
        session: requests.Session,
        start_url: str,
        proxies: Any = None,
        max_redirects: int = 12,
) -> Tuple[Any, str]:
    current_url = start_url
    response = None
    for _ in range(max_redirects):
        try:
            response = session.get(
                current_url,
                allow_redirects=False,
                proxies=proxies,
                verify=_ssl_verify(),
                timeout=15,
            )
            if response.status_code not in (301, 302, 303, 307, 308):
                return response, current_url
            loc = response.headers.get("Location", "")
            if not loc:
                return response, current_url
            current_url = urllib.parse.urljoin(current_url, loc)
            if "code=" in current_url and "state=" in current_url:
                return None, current_url
        except Exception:
            return None, current_url
    return response, current_url


def _extract_next_url(data: Dict[str, Any]) -> str:
    continue_url = str(data.get("continue_url") or "").strip()
    if continue_url:
        return continue_url
    page_type = str((data.get("page") or {}).get("type") or "").strip()
    mapping = {
        "email_otp_verification": "https://auth.openai.com/email-verification",
        "sign_in_with_chatgpt_codex_consent": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
        "workspace": "https://auth.openai.com/workspace",
        "add_phone": "https://auth.openai.com/add-phone",
        "phone_verification": "https://auth.openai.com/add-phone",
        "phone_otp_verification": "https://auth.openai.com/add-phone",
        "phone_number_verification": "https://auth.openai.com/add-phone",
    }
    return mapping.get(page_type, "")


@dataclass(frozen=True)
class OAuthStart:
    auth_url: str
    state: str
    code_verifier: str
    redirect_uri: str


def generate_oauth_url(
        *,
        redirect_uri: str = DEFAULT_REDIRECT_URI,
        scope: str = DEFAULT_SCOPE,
) -> OAuthStart:
    state = _random_state()
    code_verifier = _pkce_verifier()
    code_challenge = _sha256_b64url_no_pad(code_verifier)
    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "prompt": "login",
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
    }

    return OAuthStart(
        auth_url=f"{AUTH_URL}?{urllib.parse.urlencode(params)}",
        state=state,
        code_verifier=code_verifier,
        redirect_uri=redirect_uri,
    )


def submit_callback_url(
        *,
        callback_url: str,
        expected_state: str,
        code_verifier: str,
        redirect_uri: str = DEFAULT_REDIRECT_URI,
        proxies: Any = None,
) -> str:
    cb = _parse_callback_url(callback_url)
    if cb["error"]:
        raise RuntimeError(f"oauth error: {cb['error']}: {cb['error_description']}".strip())
    if not cb["code"]:
        raise ValueError("callback url missing ?code=")
    if not cb["state"]:
        raise ValueError("callback url missing ?state=")
    if cb["state"] != expected_state:
        raise ValueError("state mismatch")

    token_resp = _post_form(
        TOKEN_URL,
        {
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "code": cb["code"],
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        },
        proxies=proxies,
    )

    access_token = (token_resp.get("access_token") or "").strip()
    refresh_token = (token_resp.get("refresh_token") or "").strip()
    id_token = (token_resp.get("id_token") or "").strip()
    expires_in = _to_int(token_resp.get("expires_in"))

    claims = _jwt_claims_no_verify(id_token)
    email = str(claims.get("email") or "").strip()
    auth_claims = claims.get("https://api.openai.com/auth") or {}
    account_id = str(auth_claims.get("chatgpt_account_id") or "").strip()

    now = int(time.time())
    now_rfc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    expired_rfc = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                time.gmtime(now + max(expires_in, 0)))

    config_obj = {
        "id_token": id_token,
        "client_id": CLIENT_ID,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "account_id": account_id,
        "last_refresh": now_rfc,
        "email": email,
        "type": "codex",
        "expired": expired_rfc,
    }
    return json.dumps(config_obj, ensure_ascii=False, separators=(",", ":"))


def _generate_password(length: int = 20) -> str:
    upper = random.choices(string.ascii_uppercase, k=2)
    lower = random.choices(string.ascii_lowercase, k=2)
    digits = random.choices(string.digits, k=2)
    specials = random.choices("!@#$%&*", k=2)
    pool = string.ascii_letters + string.digits + "!@#$%&*"
    rest = random.choices(pool, k=length - 8)
    chars = upper + lower + digits + specials + rest
    random.shuffle(chars)
    return "".join(chars)


def generate_random_user_info() -> dict:
    name = f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"
    year = random.randint(datetime.now().year - 45, datetime.now().year - 18)
    month = random.randint(1, 12)
    day = random.randint(1, 28)
    return {"name": name, "birthdate": f"{year}-{month:02d}-{day:02d}"}


def _parse_workspace_from_auth_cookie(auth_cookie: str) -> list:
    if not auth_cookie or "." not in auth_cookie:
        return []
    parts = auth_cookie.split(".")
    if len(parts) >= 2:
        claims = _decode_jwt_segment(parts[1])
        workspaces = claims.get("workspaces") or []
        if workspaces:
            return workspaces
    claims = _decode_jwt_segment(parts[0])
    return claims.get("workspaces") or []


def run(proxy: Optional[str], run_ctx: dict = None) -> tuple:
    processed_mails: set = set()
    proxy = cfg.format_docker_url(proxy)
    if proxy and proxy.startswith("socks5://"):
        proxy = proxy.replace("socks5://", "socks5h://")
    proxies = {"http": proxy, "https": proxy} if proxy else None
    active_sessions = []
    try:
        s_reg = requests.Session(proxies=proxies, impersonate="chrome110")
        s_reg.headers.update({"Connection": "close"})
        s_reg.timeout = 30
        active_sessions.append(s_reg)
        is_takeover = False
        is_onephone = False
        target_continue_url = ""

        if not _skip_net_check():
            try:
                start = time.time()
                res = s_reg.get(
                    "https://cloudflare.com/cdn-cgi/trace",
                    proxies=proxies, verify=_ssl_verify(), timeout=10,
                )
                elapsed = time.time() - start
                loc = (re.search(r"^loc=(.+)$", res.text, re.MULTILINE) or [None, None])[1]
                if loc in ("CN", "HK"):
                    raise RuntimeError(f"当前{proxies}代理所在地不支持 OpenAI ({loc})")
                print(f"[{cfg.ts()}] [INFO] 节点测活成功！地区: {loc} | 延迟: {elapsed:.2f}s")
            except Exception as e:
                print(f"[{cfg.ts()}] [ERROR] 代理网络检查失败: {e}")
                return None, None

        email, email_jwt = get_email_and_token(proxies)
        if not email:
            return None, None

        password = _generate_password()
        print(f"[{cfg.ts()}] [INFO] 提交注册信息 (密码: {password[:4]}****)")
        MAX_REG_RETRIES = 2

        for attempt in range(MAX_REG_RETRIES):
            if active_sessions:
                try:
                    active_sessions[-1].close()
                except Exception:
                    pass
            s_reg = requests.Session(proxies=proxies, impersonate="chrome110")
            s_reg.headers.update({"Connection": "close"})
            s_reg.timeout = 30
            active_sessions.append(s_reg)
            oauth_reg = generate_oauth_url()
            is_takeover = False
            target_continue_url = ""
            try:
                s_reg.get(oauth_reg.auth_url, proxies=proxies, verify=_ssl_verify(), timeout=15)
                did = s_reg.cookies.get("oai-did") or ""
                if not did:
                    print(f"[{cfg.ts()}] [WARNING] （{mask_email(email)}）未获取到 oai-did，节点环境可能被关注，请更换IP或节点。")

                current_ua = s_reg.headers.get("User-Agent")

                reg_ctx = {}

                print(f"[{cfg.ts()}] [INFO] 正在计算（{mask_email(email)}）风控算力挑战...")
                sentinel_signup = generate_payload(did=did, flow="authorize_continue", proxy=proxy, user_agent=current_ua,
                                                   impersonate="chrome110", ctx=reg_ctx)
                if sentinel_signup:
                    print(f"[{cfg.ts()}] [SUCCESS] （{mask_email(email)}）算力挑战成功。")
                signup_headers = _oai_headers(did, {
                    "Referer": "https://auth.openai.com/create-account",
                    "content-type": "application/json",
                })
                if sentinel_signup:
                    signup_headers["openai-sentinel-token"] = sentinel_signup

                signup_resp = _post_with_retry(
                    s_reg,
                    "https://auth.openai.com/api/accounts/authorize/continue",
                    headers=signup_headers,
                    json_body={"username": {"value": email, "kind": "email"}, "screen_hint": "signup"},
                    proxies=proxies,
                )

                if signup_resp.status_code == 403:
                    print(f"[{cfg.ts()}] [WARNING] （{mask_email(email)}）注册请求触发 403 拦截，稍作等待后重试...")
                    return "retry_403", None
                if signup_resp.status_code != 200:
                    print(f"[{cfg.ts()}] [ERROR] （{mask_email(email)}）提交邮箱环节异常, 返回: {signup_resp.status_code}")
                    return None, None

                try:
                    signup_json = signup_resp.json()
                    continue_url = signup_json.get("continue_url", "")
                    if "log-in" in continue_url:
                        is_takeover = True
                        print(f"[{cfg.ts()}] [WARNING] （{mask_email(email)}）该邮箱已注册！准备走【无密码邮箱验证码】接管登录...")
                        login_ctx = reg_ctx.copy() if reg_ctx else {}
                        sentinel_login = generate_payload(did=did, flow="authorize_continue", proxy=proxy, user_agent=current_ua,
                                                          impersonate="chrome110", ctx=login_ctx)
                        login_send_headers = _oai_headers(did, {
                            "Referer": "https://auth.openai.com/log-in/password",
                            "content-type": "application/json",
                        })
                        if sentinel_login: login_send_headers["openai-sentinel-token"] = sentinel_login

                        if cfg.EMAIL_API_MODE == "luckmail":
                            try:
                                from utils.email_providers.luckmail_service import LuckMailService
                                print(f"[{cfg.ts()}] [INFO] 正在检测 LuckMail 邮箱（{mask_email(email)}）是否存活...")
                                lm_service = LuckMailService(
                                    api_key=cfg.LUCKMAIL_API_KEY,
                                    proxies=proxies if getattr(cfg, 'USE_PROXY_FOR_EMAIL', True) else None
                                )
                                if not lm_service.check_token_alive(email_jwt):
                                    print(f"[{cfg.ts()}] [ERROR] （{mask_email(email)}）邮箱 已失效，放弃当前注册并重试！")
                                    return None, None
                            except Exception as e:
                                print(f"[{cfg.ts()}] [WARNING] LuckMail 可用性检测异常(忽略并继续): {e}")

                        print(f"[{cfg.ts()}] [INFO] （{mask_email(email)}）老帐号触发初次发信...")
                        sentinel_login_resp =_post_with_retry(
                            s_reg,
                            "https://auth.openai.com/api/accounts/passwordless/send-otp",
                            headers=login_send_headers, proxies=proxies, timeout=30,
                        )

                        if sentinel_login_resp.status_code != 200:
                            print(f"[{cfg.ts()}] [ERROR] （{mask_email(email)}）邮件发送异常, 返回: {sentinel_login_resp.status_code}")
                            return None, None

                        login_code = ""
                        for resend_attempt in range(max(1, cfg.MAX_OTP_RETRIES)):
                            if getattr(cfg, 'GLOBAL_STOP', False): return None, None
                            if resend_attempt > 0:
                                print(f"\n[{cfg.ts()}] [INFO] 未收到登录验证码正在重试 {resend_attempt}/{cfg.MAX_OTP_RETRIES}...")
                                try:
                                    sentinel_resend = generate_payload(did=did, flow="authorize_continue", proxy=proxy,
                                                                       user_agent=current_ua, impersonate="chrome110", ctx=login_ctx)
                                    resend_headers = _oai_headers(did, {
                                        "Referer": "https://auth.openai.com/email-verification",
                                        "content-type": "application/json"
                                    })
                                    if sentinel_resend:
                                        resend_headers["openai-sentinel-token"] = sentinel_resend

                                    _post_with_retry(
                                        s_reg,
                                        "https://auth.openai.com/api/accounts/email-otp/resend",
                                        headers=resend_headers,
                                        json_body={}, proxies=proxies, timeout=15,
                                    )
                                    time.sleep(2)
                                except Exception as e:
                                    print(f"[{cfg.ts()}] [WARNING] （{mask_email(email)}）重新发送请求异常: {e}")

                            login_code = get_oai_code(email, jwt=email_jwt, proxies=proxies,
                                                processed_mail_ids=processed_mails)
                            if login_code:
                                break

                        if not login_code:
                            print(f"[{cfg.ts()}] [ERROR] 重试次数上限，丢弃当前 {mask_email(email)} 邮箱，放弃接管。")
                            return None, None

                        login_sentinel_otp = generate_payload(did=did, flow="authorize_continue", proxy=proxy, user_agent=current_ua,
                                                        impersonate="chrome110", ctx=login_ctx)
                        val_headers = _oai_headers(did, {
                            "Referer": "https://auth.openai.com/email-verification",
                            "content-type": "application/json",
                        })
                        if login_sentinel_otp:
                            val_headers["openai-sentinel-token"] = login_sentinel_otp

                        code_resp = _post_with_retry(
                            s_reg,
                            "https://auth.openai.com/api/accounts/email-otp/validate",
                            headers=val_headers,
                            json_body={"code": login_code}, proxies=proxies,
                        )

                        if code_resp.status_code == 200:
                            print(f"[{cfg.ts()}] [SUCCESS] （{mask_email(email)}）接管验证通过！")
                            password = "Takeover_NoPassword"
                        else:
                            print(f"[{cfg.ts()}] [ERROR] （{mask_email(email)}）接管验证码未通过: {code_resp.status_code}{code_resp.json()}")
                            return None, None

                        code_url = str(code_resp.json().get("continue_url") or "").strip()
                        if code_url.endswith("/about-you"):
                            user_info = generate_random_user_info()
                            print(f"[{cfg.ts()}] [INFO] （{mask_email(email)}）初始化账户信息 "
                                  f"(昵称: {user_info['name']}, 生日: {user_info['birthdate']})...")

                            sentinel_create = generate_payload(did=did, flow="create_account", proxy=proxy,
                                                               user_agent=current_ua,
                                                               impersonate="chrome110", ctx=login_ctx)
                            create_headers = _oai_headers(did, {
                                "Referer": "https://auth.openai.com/about-you",
                                "content-type": "application/json",
                            })

                            if sentinel_create:
                                create_headers["openai-sentinel-token"] = sentinel_create

                            create_account_resp = _post_with_retry(
                                s_reg,
                                "https://auth.openai.com/api/accounts/create_account",
                                headers=create_headers,
                                json_body=user_info, proxies=proxies,
                            )
                            try:
                                code_json = create_account_resp.json() or {}
                                target_continue_url = str(code_json.get("continue_url") or "").strip()
                            except Exception:
                                target_continue_url = ""
                        else:
                            try:
                                code_json = code_resp.json() or {}
                                target_continue_url = str(code_json.get("continue_url") or "").strip()
                            except Exception:
                                target_continue_url = ""


                except Exception as e:
                    pass

                if not is_takeover:
                    sentinel_pwd = generate_payload(did=did, flow="username_password_create", proxy=proxy, user_agent=current_ua,
                                                    impersonate="chrome110", ctx=reg_ctx)
                    pwd_headers = _oai_headers(did, {
                        "Referer": "https://auth.openai.com/create-account/password",
                        "content-type": "application/json",
                    })
                    if sentinel_pwd:
                        pwd_headers["openai-sentinel-token"] = sentinel_pwd

                    pwd_resp = _post_with_retry(
                        s_reg,
                        "https://auth.openai.com/api/accounts/user/register",
                        headers=pwd_headers,
                        json_body={"password": password, "username": email},
                        proxies=proxies,
                    )

                    if pwd_resp.status_code != 200:
                        err_json = pwd_resp.json()
                        err_code = err_json.get("error", {}).get("code")
                        err_msg = err_json.get("error", {}).get("message", "")
                        if err_code is None and "Failed to create account" in err_msg:
                            print(f"[{cfg.ts()}] [ERROR] （{mask_email(email)}）遭遇底层影子风控 (无明确代码拦截)！当前 IP 域名 可能已黑。")
                            if run_ctx is not None: run_ctx['pwd_blocked'] = True
                            return None, None
                        print(f"[{cfg.ts()}] [ERROR] （{mask_email(email)}）设密码环节被拦截，返回: {pwd_resp.status_code}，该提示可忽略，不影响后面执行流程")
                        if run_ctx is not None: run_ctx['pwd_blocked'] = True
                        return None, None

                    try:
                        reg_json = pwd_resp.json()
                        need_otp = (
                                "verify" in reg_json.get("continue_url", "")
                                or "otp" in (reg_json.get("page") or {}).get("type", "")
                        )
                    except Exception:
                        need_otp = False

                    if need_otp:
                        if cfg.EMAIL_API_MODE == "luckmail":
                            try:
                                from utils.email_providers.luckmail_service import LuckMailService
                                print(f"[{cfg.ts()}] [INFO] 正在检测 LuckMail 邮箱（{mask_email(email)}）是否存活...")
                                lm_service = LuckMailService(
                                    api_key=cfg.LUCKMAIL_API_KEY,
                                    proxies=proxies if getattr(cfg, 'USE_PROXY_FOR_EMAIL', True) else None
                                )
                                if not lm_service.check_token_alive(email_jwt):
                                    print(f"[{cfg.ts()}] [ERROR] （{mask_email(email)}）邮箱 已失效，放弃当前注册并重试！")
                                    return None, None
                            except Exception as e:
                                print(f"[{cfg.ts()}] [WARNING] LuckMail 可用性检测异常(忽略并继续): {e}")

                        print(f"\n[{cfg.ts()}] [INFO] 正在向 {mask_email(email)} 主动请求发送验证码...")
                        send_otp_url = "https://auth.openai.com/api/accounts/email-otp/send"

                        try:
                            sentinel_send = generate_payload(did=did, flow="authorize_continue", proxy=proxy, user_agent=current_ua,
                                                             impersonate="chrome110", ctx=reg_ctx)
                            send_headers = _oai_headers(did, {
                                "Referer": "https://auth.openai.com/create-account/password",
                                "content-type": "application/json",
                            })
                            if sentinel_send:
                                send_headers["openai-sentinel-token"] = sentinel_send

                            _post_with_retry(
                                s_reg,
                                send_otp_url,
                                headers=send_headers,
                                json_body={}, proxies=proxies, timeout=30,
                            )
                        except Exception as e:
                            print(f"[{cfg.ts()}] [WARNING] （{mask_email(email)}）OTP 初始发送请求异常: {e}")

                        code = ""
                        for resend_attempt in range(max(1, cfg.MAX_OTP_RETRIES)):
                            if getattr(cfg, 'GLOBAL_STOP', False): return None, None
                            if resend_attempt > 0:
                                print(f"\n[{cfg.ts()}] [INFO] 正在重试 {resend_attempt}/{cfg.MAX_OTP_RETRIES}...")
                                try:
                                    sentinel_resend = generate_payload(did=did, flow="authorize_continue", proxy=proxy,
                                                                       user_agent=current_ua, impersonate="chrome110", ctx=reg_ctx)
                                    resend_headers = _oai_headers(did, {
                                        "Referer": "https://auth.openai.com/email-verification",
                                        "content-type": "application/json"
                                    })
                                    if sentinel_resend:
                                        resend_headers["openai-sentinel-token"] = sentinel_resend

                                    _post_with_retry(
                                        s_reg,
                                        "https://auth.openai.com/api/accounts/email-otp/resend",
                                        headers=resend_headers,
                                        json_body={}, proxies=proxies, timeout=15,
                                    )
                                    time.sleep(2)
                                except Exception as e:
                                    print(f"[{cfg.ts()}] [WARNING] （{mask_email(email)}）重新发送请求异常: {e}")

                            code = get_oai_code(email, jwt=email_jwt, proxies=proxies,
                                                processed_mail_ids=processed_mails)
                            if code:
                                break

                        if not code:
                            print(f"[{cfg.ts()}] [ERROR] 重试次数上限，丢弃当前 {mask_email(email)} 邮箱。")
                            return None, None

                        sentinel_otp = generate_payload(did=did, flow="authorize_continue", proxy=proxy, user_agent=current_ua,
                                                        impersonate="chrome110", ctx=reg_ctx)
                        val_headers = _oai_headers(did, {
                            "Referer": "https://auth.openai.com/email-verification",
                            "content-type": "application/json",
                        })
                        if sentinel_otp:
                            val_headers["openai-sentinel-token"] = sentinel_otp

                        code_resp = _post_with_retry(
                            s_reg,
                            "https://auth.openai.com/api/accounts/email-otp/validate",
                            headers=val_headers,
                            json_body={"code": code}, proxies=proxies,
                        )

                        if code_resp.status_code != 200:
                            print(f"[{cfg.ts()}] [ERROR] （{mask_email(email)}）验证码校验未通过: {code_resp.status_code}")
                            return None, None

                        code_account_json = code_resp.json()
                        code_account_url = code_account_json.get("continue_url", "")

                        if "/add-phone" in code_account_url:
                            print(f"[{cfg.ts()}] [WARNING] （{mask_email(email)}） 账号创建过程触发手机风控...")
                            if attempt < MAX_REG_RETRIES - 1:
                                print(
                                    f"[{cfg.ts()}] [INFO] （{mask_email(email)}） 准备重置环境，重新进行第 {attempt + 2} 次 注册流程尝试...")
                                continue
                            print(f"[{cfg.ts()}] [WARNING] （{mask_email(email)}） 账号创建过程多次尝试仍触发手机风控，进入 HeroSMS 手机号验证流程...")
                            print(f"[{cfg.ts()}] [WARNING] （{mask_email(email)}） 重点提示：有些邮箱接码后也无法创建成功账号，可能Oauth阶段还需要接码，请自行斟酌...")
                            if bool(cfg.HERO_SMS_ENABLED) and bool(cfg.HERO_SMS_VERIFY_ON_REGISTER):
                                ok, next_url_or_reason = _try_verify_phone_via_hero_sms(
                                    session=s_reg,
                                    proxies=proxies,
                                    hint_url=code_account_url
                                )

                                if ok and next_url_or_reason:
                                    print(f"[{cfg.ts()}] [INFO] （{mask_email(email)}） 手机验证成功，继续创建账号: {next_url_or_reason}")
                                else:
                                    print(f"[{cfg.ts()}] [ERROR] （{mask_email(email)}） {next_url_or_reason}")
                                    if run_ctx is not None: run_ctx['phone_verify'] = True
                                    return None, None
                            else:
                                print(f"[{cfg.ts()}] [WARNING] （{mask_email(email)}） HeroSMS主开关 或 创建时接码开关未开启，如果不想花钱接码请忽略该条提示")
                                if run_ctx is not None: run_ctx['phone_verify'] = True
                                return None, None

                    user_info = generate_random_user_info()
                    print(f"[{cfg.ts()}] [INFO] （{mask_email(email)}）初始化账户信息 "
                          f"(昵称: {user_info['name']}, 生日: {user_info['birthdate']})...")

                    sentinel_create = generate_payload(did=did, flow="create_account", proxy=proxy, user_agent=current_ua,
                                                       impersonate="chrome110", ctx=reg_ctx)
                    create_headers = _oai_headers(did, {
                        "Referer": "https://auth.openai.com/about-you",
                        "content-type": "application/json",
                    })

                    if sentinel_create:
                        create_headers["openai-sentinel-token"] = sentinel_create

                    create_account_resp = _post_with_retry(
                        s_reg,
                        "https://auth.openai.com/api/accounts/create_account",
                        headers=create_headers,
                        json_body=user_info, proxies=proxies,
                    )

                    if create_account_resp.status_code != 200:
                        err_json = create_account_resp.json()
                        err_code = str(err_json.get("error", {}).get("code", "")).strip()
                        err_msg = str(err_json.get("error", {}).get("message", "")).strip()
                        if err_code == "identity_provider_mismatch":
                            try:
                                is_takeover = True
                                print(f"[{cfg.ts()}] [INFO] （{mask_email(email)}）检测到第三方登录账号！强行变道走无密码 OTP...")
                                print(f"[{cfg.ts()}] [INFO] （{mask_email(email)}）已打上接管标记，交由 OAuth 提取流程进行无密码登录...")
                            except Exception as e:
                                 pass

                        if not is_takeover:
                            if "been deleted or deactivated" in err_msg:
                                print(f"[{cfg.ts()}] [ERROR] （{mask_email(email)}）您没有帐户，因为它已被删除或停用。如果您认为这是一个错误，请通过我们的帮助中心help.openai.com.与我们联系")
                                run_ctx['signup_blocked'] = True
                                return None, None
                            run_ctx['signup_blocked'] = True
                            print(f"[{cfg.ts()}] [ERROR] （{mask_email(email)}）账户创建受阻，疑似被标记为账号已存在，返回: {create_account_resp.status_code}，该提示可忽略，不影响后面执行流程")
                            return None, None

                    try:
                        create_json = create_account_resp.json() or {}
                        target_continue_url = str(create_json.get("continue_url") or "").strip()
                    except Exception:
                        target_continue_url = ""

                wait_time = random.randint(cfg.LOGIN_DELAY_MIN, cfg.LOGIN_DELAY_MAX)
                print(f"[{cfg.ts()}] [INFO] （{mask_email(email)}）账号已通过，等待 {wait_time} 秒后同步最终状态...")
                if cfg.SAVE_TO_LOCAL_IN_CPA_MODE:
                    # if not is_takeover and password != "Takeover_NoPassword":
                    try:
                        from utils import db_manager
                        db_manager.save_account_to_db(email, password, '{"status": "仅注册成功"}')
                        print(f"[{cfg.ts()}] [INFO] （{mask_email(email)}）账号已注册成功，提前作为半成品写入本地库。")
                    except Exception as e:
                        pass
                time.sleep(wait_time)

                workspace_hint_url = ""
                if target_continue_url:
                    workspace_hint_url = target_continue_url if target_continue_url.startswith(
                        "http") else f"https://auth.openai.com{target_continue_url}"
                    try:
                        _, current_url = _follow_redirect_chain_local(s_reg, workspace_hint_url, proxies)
                        if "code=" in current_url and "state=" in current_url:
                            return submit_callback_url(
                                callback_url=current_url,
                                expected_state=oauth_reg.state,
                                code_verifier=oauth_reg.code_verifier,
                                proxies=proxies,
                            ), password
                    except Exception as e:
                        current_url = workspace_hint_url
                else:
                    current_url = "https://auth.openai.com/sign-in-with-chatgpt/codex/consent"

                auth_cookie = s_reg.cookies.get("oai-client-auth-session") or ""
                workspaces = _parse_workspace_from_auth_cookie(auth_cookie)

                if workspaces:
                    print(f"[{cfg.ts()}] [SUCCESS] （{mask_email(email)}）检测到工作区，正在确认并提取最终凭据...")
                    workspace_id = str((workspaces[0] or {}).get("id") or "").strip()

                    if workspace_id:
                        select_resp = _post_with_retry(
                            s_reg,
                            "https://auth.openai.com/api/accounts/workspace/select",
                            headers=_oai_headers(did, {"Referer": current_url, "content-type": "application/json"}),
                            json_body={"workspace_id": workspace_id},
                            proxies=proxies,
                        )

                        if select_resp.status_code == 200:
                            try:
                                select_data = select_resp.json() or {}
                                next_url = str(select_data.get("continue_url") or "").strip()
                            except Exception:
                                next_url = ""

                            if next_url:
                                _, final_url = _follow_redirect_chain_local(s_reg, next_url, proxies)
                                if "code=" in final_url and "state=" in final_url:
                                    print(f"[{cfg.ts()}] [SUCCESS] （{mask_email(email)}）凭据提取成功！一气呵成！")
                                    return submit_callback_url(
                                        callback_url=final_url,
                                        expected_state=oauth_reg.state,
                                        code_verifier=oauth_reg.code_verifier,
                                        proxies=proxies,
                                    ), password
                print(f"[{cfg.ts()}] [INFO] （{mask_email(email)}）账号登录完毕，执行静默获取 Token...")
                for oauth_attempt in range(2):
                    if oauth_attempt == 1:
                        print(f"[{cfg.ts()}] [ERROR] （{mask_email(email)}）首次遇到 add-phone 风控，正在重试...")
                    if active_sessions:
                        try:
                            active_sessions[-1].close()
                        except Exception:
                            pass
                    s_log = requests.Session(proxies=proxies, impersonate="chrome110")
                    s_log.headers.update({"Connection": "close"})
                    s_log.timeout = 30
                    active_sessions.append(s_log)
                    oauth_log = generate_oauth_url()

                    resp, current_url = _follow_redirect_chain_local(s_log, oauth_log.auth_url, proxies)
                    if "code=" in current_url and "state=" in current_url:
                        return submit_callback_url(
                            callback_url=current_url,
                            code_verifier=oauth_log.code_verifier,
                            redirect_uri=oauth_log.redirect_uri,
                            expected_state=oauth_log.state,
                            proxies=proxies,
                        ), password
                    log_did = s_log.cookies.get("oai-did") or did

                    log_ctx = reg_ctx.copy() if reg_ctx else {}
                    log_ctx["session_id"] = str(uuid.uuid4())
                    now_ms = int(time.time() * 1000)
                    log_ctx["time_origin"] = float(now_ms - random.randint(20000, 300000))

                    sentinel_log = generate_payload(did=log_did, flow="authorize_continue", proxy=proxy, user_agent=current_ua,
                                                    impersonate="chrome110", ctx=log_ctx)

                    log_start_headers = _oai_headers(log_did, {
                        "Referer": current_url,
                        "content-type": "application/json",
                    })
                    if sentinel_log:
                        log_start_headers["openai-sentinel-token"] = sentinel_log

                    login_start_resp = _post_with_retry(
                        s_log,
                        "https://auth.openai.com/api/accounts/authorize/continue",
                        headers=log_start_headers,
                        json_body={"username": {"value": email, "kind": "email"}},
                        proxies=proxies, allow_redirects=False,
                    )

                    if login_start_resp.status_code != 200:
                        print(f"[{cfg.ts()}] [ERROR] （{mask_email(email)}）登录环节第一步请求被拒: HTTP {login_start_resp.status_code}")
                        return None, None

                    if is_takeover:
                        log_send_headers = generate_payload(did=log_did, flow="authorize_continue", proxy=proxy, user_agent=current_ua,
                                                          impersonate="chrome110", ctx=log_ctx)
                        login_send_headers = _oai_headers(log_did, {
                            "Referer": "https://auth.openai.com/email-verification",
                            "content-type": "application/json",
                        })
                        if log_send_headers: login_send_headers["openai-sentinel-token"] = log_send_headers
                        if cfg.EMAIL_API_MODE == "luckmail":
                            try:
                                from utils.email_providers.luckmail_service import LuckMailService
                                print(f"[{cfg.ts()}] [INFO] 正在检测 LuckMail 邮箱（{mask_email(email)}）是否存活...")
                                lm_service = LuckMailService(
                                    api_key=cfg.LUCKMAIL_API_KEY,
                                    proxies=proxies if getattr(cfg, 'USE_PROXY_FOR_EMAIL', True) else None
                                )
                                if not lm_service.check_token_alive(email_jwt):
                                    print(f"[{cfg.ts()}] [ERROR] （{mask_email(email)}）邮箱 已失效，放弃当前注册并重试！")
                                    return None, None
                            except Exception as e:
                                print(f"[{cfg.ts()}] [WARNING] LuckMail 可用性检测异常(忽略并继续): {e}")
                        print(f"[{cfg.ts()}] [INFO] （{mask_email(email)}）老帐号OAuth无密码登录发信...")
                        sentinel_login_resp =_post_with_retry(
                            s_log,
                            "https://auth.openai.com/api/accounts/passwordless/send-otp",
                            headers=login_send_headers, proxies=proxies, timeout=30,
                        )

                        if sentinel_login_resp.status_code != 200:
                            print(f"[{cfg.ts()}] [ERROR] （{mask_email(email)}）邮件发送异常, 返回: {sentinel_login_resp.status_code}")
                            return None, None

                        login_code_oauth = ""
                        for login_code_attempt in range(max(1, cfg.MAX_OTP_RETRIES)):
                            if getattr(cfg, 'GLOBAL_STOP', False): return None, None
                            if login_code_attempt > 0:
                                print(f"\n[{cfg.ts()}] [INFO] 老帐号OAuth 阶段未收到验证码正在重试 {login_code_attempt}/{cfg.MAX_OTP_RETRIES}...")
                                try:
                                    login_code_resend = generate_payload(did=log_did, flow="authorize_continue", proxy=proxy,
                                                                       user_agent=current_ua, impersonate="chrome110", ctx=log_ctx)
                                    resend_headers = _oai_headers(log_did, {
                                        "Referer": "https://auth.openai.com/email-verification",
                                        "content-type": "application/json"
                                    })
                                    if login_code_resend:
                                        resend_headers["openai-sentinel-token"] = login_code_resend

                                    _post_with_retry(
                                        s_log,
                                        "https://auth.openai.com/api/accounts/email-otp/resend",
                                        headers=resend_headers,
                                        json_body={}, proxies=proxies, timeout=15,
                                    )
                                    time.sleep(2)
                                except Exception as e:
                                    print(f"[{cfg.ts()}] [WARNING] （{mask_email(email)}）重新发送请求异常: {e}")

                            login_code_oauth = get_oai_code(email, jwt=email_jwt, proxies=proxies,
                                                processed_mail_ids=processed_mails)
                            if login_code_oauth:
                                break

                        if not login_code_oauth:
                            print(f"[{cfg.ts()}] [ERROR] 重试次数上限，丢弃当前 {mask_email(email)} 邮箱，放弃接管。")
                            return None, None

                        login_sentinel_otp = generate_payload(did=log_did, flow="authorize_continue", proxy=proxy, user_agent=current_ua,
                                                        impersonate="chrome110", ctx=log_ctx)
                        val_headers = _oai_headers(log_did, {
                            "Referer": "https://auth.openai.com/email-verification",
                            "content-type": "application/json",
                        })
                        if login_sentinel_otp:
                            val_headers["openai-sentinel-token"] = login_sentinel_otp

                        login_code_resp = _post_with_retry(
                            s_log,
                            "https://auth.openai.com/api/accounts/email-otp/validate",
                            headers=val_headers,
                            json_body={"code": login_code_oauth}, proxies=proxies,
                        )
                        if login_code_resp.status_code == 200:
                            print(f"[{cfg.ts()}] [SUCCESS] （{mask_email(email)}）老帐号OAuth 阶段验证码通过！")
                            password = "Takeover_NoPassword"
                        else:
                            print(f"[{cfg.ts()}] [ERROR] （{mask_email(email)}）老帐号OAuth 阶段验证码未通过，账号异常: {login_code_resp.status_code}")
                            return None, None

                        login_code_url = str(login_code_resp.json().get("continue_url") or "").strip()
                        if login_code_url.endswith("/about-you"):
                            user_info = generate_random_user_info()
                            print(f"[{cfg.ts()}] [INFO] （{mask_email(email)}）初始化账户信息 "
                                  f"(昵称: {user_info['name']}, 生日: {user_info['birthdate']})...")

                            sentinel_create = generate_payload(did=did, flow="create_account", proxy=proxy,
                                                               user_agent=current_ua,
                                                               impersonate="chrome110", ctx=log_ctx)
                            create_headers = _oai_headers(did, {
                                "Referer": "https://auth.openai.com/about-you",
                                "content-type": "application/json",
                            })

                            if sentinel_create:
                                create_headers["openai-sentinel-token"] = sentinel_create

                            create_account_resp = _post_with_retry(
                                s_log,
                                "https://auth.openai.com/api/accounts/create_account",
                                headers=create_headers,
                                json_body=user_info, proxies=proxies,
                            )
                            next_url = str(create_account_resp.json().get("continue_url") or "").strip()
                        else:
                            next_url = str(login_code_resp.json().get("continue_url") or "").strip()

                        resp, current_url = _follow_redirect_chain_local(s_log, next_url, proxies)


                    else:
                        pwd_page_url = str(
                            (login_start_resp.json() if login_start_resp.status_code == 200 else {})
                            .get("continue_url") or ""
                        ).strip()
                        resp, current_url = _follow_redirect_chain_local(s_log, pwd_page_url, proxies)

                        sentinel_pwd_log = generate_payload(did=log_did, flow="password_verify", proxy=proxy, user_agent=current_ua,
                                                            impersonate="chrome110", ctx=log_ctx)

                        login_pwd_headers = _oai_headers(log_did, {
                            "Referer": current_url,
                            "content-type": "application/json",
                        })

                        if sentinel_pwd_log:
                            login_pwd_headers["openai-sentinel-token"] = sentinel_pwd_log

                        pwd_login_resp = _post_with_retry(
                            s_log,
                            "https://auth.openai.com/api/accounts/password/verify",
                            headers=login_pwd_headers,
                            json_body={"password": password}, proxies=proxies,
                        )

                        if pwd_login_resp.status_code != 200:
                            print(f"[{cfg.ts()}] [ERROR] （{mask_email(email)}）最终静默登录验证失败: HTTP {pwd_login_resp.status_code}")
                            return None, None

                        pwd_json = pwd_login_resp.json()
                        next_url = _extract_next_url(pwd_json)
                        resp, current_url = _follow_redirect_chain_local(s_log, next_url, proxies)

                        if current_url.endswith("/email-verification"):
                            if cfg.EMAIL_API_MODE == "luckmail":
                                try:
                                    from utils.email_providers.luckmail_service import LuckMailService
                                    print(f"[{cfg.ts()}] [INFO] 正在检测 LuckMail 邮箱（{mask_email(email)}）是否存活...")
                                    lm_service = LuckMailService(
                                        api_key=cfg.LUCKMAIL_API_KEY,
                                        proxies=proxies if getattr(cfg, 'USE_PROXY_FOR_EMAIL', True) else None
                                    )
                                    if not lm_service.check_token_alive(email_jwt):
                                        print(f"[{cfg.ts()}] [ERROR] （{mask_email(email)}）邮箱 已失效，放弃当前注册并重试！")
                                        return None, None
                                except Exception as e:
                                    print(f"[{cfg.ts()}] [WARNING] LuckMail 可用性检测异常(忽略并继续): {e}")

                            print(f"\n[{cfg.ts()}] [INFO] （{mask_email(email)}）静默登录需要验证码，主动触发发送...")
                            try:
                                sentinel_log_send = generate_payload(did=log_did, flow="authorize_continue", proxy=proxy,
                                                                     user_agent=current_ua, impersonate="chrome110", ctx=log_ctx)
                                log_send_headers = _oai_headers(log_did, {
                                    "Referer": current_url,
                                    "content-type": "application/json",
                                })
                                if sentinel_log_send:
                                    log_send_headers["openai-sentinel-token"] = sentinel_log_send

                                _post_with_retry(
                                    s_log,
                                    "https://auth.openai.com/api/accounts/email-otp/send",
                                    headers=log_send_headers,
                                    json_body={}, proxies=proxies, timeout=30,
                                )
                            except Exception as e:
                                print(f"[{cfg.ts()}] [WARNING] （{mask_email(email)}）登录 OTP 发送请求异常: {e}")

                            code2 = ""
                            for resend_attempt in range(max(1, cfg.MAX_OTP_RETRIES)):
                                if getattr(cfg, 'GLOBAL_STOP', False): return None, None
                                if resend_attempt > 0:
                                    print(f"\n[{cfg.ts()}] [INFO] （{mask_email(email)}）正在重试 {resend_attempt}/{cfg.MAX_OTP_RETRIES}...")
                                    try:
                                        sentinel_log_resend = generate_payload(did=log_did, flow="authorize_continue", proxy=proxy,
                                                                               user_agent=current_ua, impersonate="chrome110",
                                                                               ctx=log_ctx)
                                        log_resend_headers = _oai_headers(log_did, {
                                            "Referer": "https://auth.openai.com/email-verification", "content-type": "application/json"
                                        })
                                        if sentinel_log_resend:
                                            log_resend_headers["openai-sentinel-token"] = sentinel_log_resend

                                        _post_with_retry(
                                            s_log,
                                            "https://auth.openai.com/api/accounts/email-otp/resend",
                                            headers=log_resend_headers,
                                            json_body={}, proxies=proxies, timeout=15,
                                        )
                                        time.sleep(2)
                                    except Exception as e:
                                        print(f"[{cfg.ts()}] [WARNING] （{mask_email(email)}）重新发送请求异常: {e}")

                                code2 = get_oai_code(email, jwt=email_jwt, proxies=proxies,
                                                     processed_mail_ids=processed_mails)
                                if code2:
                                    break

                            if not code2:
                                print(f"[{cfg.ts()}] [ERROR] （{mask_email(email)}）重新发送后依然未收到验证码，彻底放弃。")
                                return None, None

                            sentinel_otp2 = generate_payload(did=log_did, flow="authorize_continue", proxy=proxy, user_agent=current_ua,
                                                             impersonate="chrome110", ctx=log_ctx)
                            val2_headers = _oai_headers(log_did, {
                                "Referer": "https://auth.openai.com/email-verification",
                                "content-type": "application/json",
                            })
                            if sentinel_otp2:
                                val2_headers["openai-sentinel-token"] = sentinel_otp2

                            code2_resp = _post_with_retry(
                                s_log,
                                "https://auth.openai.com/api/accounts/email-otp/validate",
                                headers=val2_headers,
                                json_body={"code": code2}, proxies=proxies,
                            )
                            if code2_resp.status_code != 200:
                                print(f"[{cfg.ts()}] [ERROR] （{mask_email(email)}）二次安全验证 OTP 校验失败: {code2_resp.status_code}")
                                return None, None

                            next_url = str(code2_resp.json().get("continue_url") or "").strip()
                            resp, current_url = _follow_redirect_chain_local(s_log, next_url, proxies)

                    if "code=" in current_url and "state=" in current_url:
                        return submit_callback_url(
                            callback_url=current_url,
                            code_verifier=oauth_log.code_verifier,
                            redirect_uri=oauth_log.redirect_uri,
                            expected_state=oauth_log.state,
                            proxies=proxies,
                        ), password

                    if current_url.endswith("/consent") or current_url.endswith("/workspace"):
                        auth_cookie2 = s_log.cookies.get("oai-client-auth-session") or ""
                        workspaces2 = _parse_workspace_from_auth_cookie(auth_cookie2)
                        if workspaces2:
                            select_resp = _post_with_retry(
                                s_log,
                                "https://auth.openai.com/api/accounts/workspace/select",
                                headers=_oai_headers(s_log.cookies.get("oai-did") or "", {
                                    "Referer": current_url, "content-type": "application/json"
                                }),
                                json_body={"workspace_id": str(workspaces2[0].get("id"))},
                                proxies=proxies,
                            )
                            final_url = (
                                _extract_next_url(select_resp.json())
                                if select_resp.status_code == 200 else ""
                            )
                            _, final_loc = _follow_redirect_chain_local(s_log, final_url, proxies)
                            if "code=" in final_loc:
                                return submit_callback_url(
                                    callback_url=final_loc,
                                    expected_state=oauth_log.state,
                                    code_verifier=oauth_log.code_verifier,
                                    proxies=proxies,
                                ), password
                    if "/add-phone" in current_url:
                        if oauth_attempt == 0:
                            continue
                        else:
                            print(f"[{cfg.ts()}] [INFO] （{mask_email(email)}） OAuth链路触发风控，进入 HeroSMS 手机号验证流程...")
                            ok, next_url_or_reason = _try_verify_phone_via_hero_sms(
                                session=s_log,
                                proxies=proxies,
                                hint_url=current_url
                            )

                            if ok and next_url_or_reason:
                                print(f"[{cfg.ts()}] [INFO] （{mask_email(email)}） 手机验证成功，继续 OAuth 链路: {next_url_or_reason}")

                                if next_url_or_reason.endswith("/about-you"):
                                    user_info = generate_random_user_info()
                                    print(f"[{cfg.ts()}] [INFO] （{mask_email(email)}）初始化账户信息 "
                                          f"(昵称: {user_info['name']}, 生日: {user_info['birthdate']})...")

                                    sentinel_create = generate_payload(did=did, flow="create_account", proxy=proxy,
                                                                       user_agent=current_ua,
                                                                       impersonate="chrome110", ctx=log_ctx)
                                    create_headers = _oai_headers(did, {
                                        "Referer": "https://auth.openai.com/about-you",
                                        "content-type": "application/json",
                                    })

                                    if sentinel_create:
                                        create_headers["openai-sentinel-token"] = sentinel_create

                                    create_account_resp = _post_with_retry(
                                        s_log,
                                        "https://auth.openai.com/api/accounts/create_account",
                                        headers=create_headers,
                                        json_body=user_info, proxies=proxies,
                                    )
                                    next_url_or_reason = str(create_account_resp.json().get("continue_url") or "").strip()

                                if "code=" in next_url_or_reason:
                                    return submit_callback_url(
                                        callback_url=next_url_or_reason,
                                        expected_state=oauth_log.state,
                                        code_verifier=oauth_log.code_verifier,
                                        proxies=proxies,
                                    ), password

                                if next_url_or_reason.endswith("/consent") or next_url_or_reason.endswith("/workspace"):
                                    auth_cookie2 = s_log.cookies.get("oai-client-auth-session") or ""
                                    workspaces2 = _parse_workspace_from_auth_cookie(auth_cookie2)
                                    if workspaces2:
                                        select_resp = _post_with_retry(
                                            s_log,
                                            "https://auth.openai.com/api/accounts/workspace/select",
                                            headers=_oai_headers(s_log.cookies.get("oai-did") or "", {
                                                "Referer": next_url_or_reason, "content-type": "application/json"
                                            }),
                                            json_body={"workspace_id": str(workspaces2[0].get("id"))},
                                            proxies=proxies,
                                        )
                                        final_url = (
                                            _extract_next_url(select_resp.json())
                                            if select_resp.status_code == 200 else ""
                                        )
                                        _, final_loc = _follow_redirect_chain_local(s_log, final_url, proxies)
                                        if "code=" in final_loc:
                                            return submit_callback_url(
                                                callback_url=final_loc,
                                                expected_state=oauth_log.state,
                                                code_verifier=oauth_log.code_verifier,
                                                proxies=proxies,
                                            ), password
                                current_url = next_url_or_reason
                            else:
                                print(f"[{cfg.ts()}] [ERROR] （{mask_email(email)}） {next_url_or_reason}")
                            break
                    else:
                        break

                if run_ctx is not None: run_ctx['phone_verify'] = True
                print(f"[{cfg.ts()}] [ERROR] （{mask_email(email)}） OAuth 授权链路追踪失败！当前死在网页: {current_url}")
                return None, None

            except Exception as e:
                print(f"[{cfg.ts()}] [ERROR] （{mask_email(email)}） 注册主流程发生严重异常: {e}")
                if attempt < MAX_REG_RETRIES - 1:
                    print(f"[{cfg.ts()}] [INFO] 正在准备重试...")
                    time.sleep(2)
                    continue
                return None, None
        return None, None
    finally:
        for s in active_sessions:
            try:
                s.close()
            except:
                pass
        del active_sessions[:]
        gc.collect()

def refresh_oauth_token(refresh_token: str, proxies: Any = None) -> Tuple[bool, dict]:
    if not refresh_token:
        return False, {"error": "无 refresh_token"}
    try:
        resp = requests.post(
            TOKEN_URL,
            data={
                "client_id": CLIENT_ID,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "redirect_uri": DEFAULT_REDIRECT_URI,
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            proxies=proxies,
            verify=_ssl_verify(),
            timeout=30,
            impersonate="chrome110",
        )
        if resp.status_code == 200:
            data = resp.json()
            now = int(time.time())
            expires_in = _to_int(data.get("expires_in", 3600))
            return True, {
                "access_token": data.get("access_token"),
                "refresh_token": data.get("refresh_token", refresh_token),
                "id_token": data.get("id_token"),
                "last_refresh": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
                "expired": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                         time.gmtime(now + max(expires_in, 0))),
            }
        return False, {"error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return False, {"error": str(e)}