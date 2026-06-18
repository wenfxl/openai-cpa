import random
import re
import time
import uuid
from typing import Optional
import json
from curl_cffi import requests
from utils import config as cfg
from utils.email_providers.mail_service import get_email_and_token, get_oai_code, mask_email,_extract_otp_code
from utils.integrations.hero_sms import _try_verify_phone_via_hero_sms
from utils.integrations.hero_sms import get_phone_for_signup as hs_get_phone, wait_code_for_signup as hs_wait_code, report_signup_result as hs_report
from utils.integrations.smsbower_sms import handle_smsbower_verification
from utils.integrations.smsbower_sms import get_phone_for_signup as sb_get_phone, wait_code_for_signup as sb_wait_code, report_signup_result as sb_report
from utils.integrations.fivesim_sms import try_verify_phone_via_fivesim
from utils.integrations.fivesim_sms import get_phone_for_signup as fs_get_phone, wait_code_for_signup as fs_wait_code, report_signup_result as fs_report
from utils.auth_core import generate_payload, init_auth, image2api_data, sys_node_allocate, sys_node_release, code_pool,sys_team_domain_verify
from utils.integrations.image2api_client import Image2APIClient
from .http_utils import _ssl_verify, _skip_net_check, _post_with_retry, _oai_headers, _follow_redirect_chain_local
from .common import _extract_next_url, _parse_workspace_from_auth_cookie, _otp_verify_loop, _create_account_about_you
from .oauth import generate_oauth_url, submit_callback_url
from .user_utils import _generate_password

def run(
    proxy: Optional[str],
    run_ctx: dict = None,
    assigned_domain: Optional[str] = None,
    batch_id: Optional[int] = None,
    worker_index: Optional[int] = None,
) -> tuple:
    processed_mails: set = set()
    proxy = cfg.format_docker_url(proxy)
    if proxy and proxy.startswith("socks5://"):
        proxy = proxy.replace("socks5://", "socks5h://")
    proxies = {"http": proxy, "https": proxy} if proxy else None
    s_reg = None
    s_log = None
    saved_temp_at = ""
    sys_handle_a = ""
    sys_handle_b = ""
    sys_handle_c = ""
    try:
        if getattr(cfg, 'TEAM_MODE_OVERSPEED', False):
            if not getattr(cfg, 'CF_API_EMAIL', ""):
                print(f"[{cfg.ts()}] [ERROR] 请确认填写好CF邮箱和KEY在启动")
                return None, None
        s_reg = requests.Session(proxies=proxies, impersonate="chrome")
        s_reg.headers.update({"Connection": "close"})
        s_reg.timeout = 30
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
        try:
            s_reg.close()
        except:
            pass
        del s_reg
        s_reg = None

        email, email_jwt = get_email_and_token(
            proxies,
            assigned_domain=assigned_domain,
            batch_id=batch_id,
            worker_index=worker_index,
        )
        if not email:
            return None, None

        password = _generate_password()

        is_phone_mode = str(getattr(cfg, 'REG_MODE', '')).strip()

        if is_phone_mode == "phone":
            print(f"[{cfg.ts()}] [INFO] 【手机首发模式】已生成备用邮箱({mask_email(email)})及密码用于后续绑定和存库")
        else:
            print(f"[{cfg.ts()}] [INFO] 提交注册信息 (密码: {password[:4]}****)")


        is_overspeed = getattr(cfg, 'TEAM_MODE_OVERSPEED', False)
        if is_overspeed:
            print(f"[{cfg.ts()}] [INFO] 超速妙已启动！正在验证邮箱: {mask_email(email)}...")
            is_verified, sys_handle_a, sys_handle_b, sys_handle_c = sys_team_domain_verify(email, proxies)
            if not is_verified:
                print(f"[{cfg.ts()}] [ERROR] 超速妙验证失败，丢弃邮箱。")
                return None, None
            print(f"[{cfg.ts()}] [SUCCESS] 邮箱 {mask_email(email)} 验证完成...")

        MAX_REG_RETRIES = 2

        for attempt in range(MAX_REG_RETRIES):
            if s_reg is not None:
                try:
                    s_reg.close()
                except:
                    pass
                del s_reg
            s_reg = requests.Session(proxies=proxies, impersonate="chrome")
            s_reg.headers.update({"Connection": "close"})
            s_reg.cookies.clear()
            s_reg.timeout = 30
            oauth_reg = generate_oauth_url()
            is_takeover = False
            target_continue_url = ""
            saved_temp_at = ""
            sms_get_func = None
            sms_wait_func = None
            sms_report_func = None

            if getattr(cfg, 'SMSBOWER_ENABLED', False):
                sms_get_func, sms_wait_func, sms_report_func = sb_get_phone, sb_wait_code, sb_report
            elif getattr(cfg, 'FIVESIM_ENABLED', False):
                sms_get_func, sms_wait_func, sms_report_func = fs_get_phone, fs_wait_code, fs_report
            elif getattr(cfg, 'HERO_SMS_ENABLED', False):
                sms_get_func, sms_wait_func, sms_report_func = hs_get_phone, hs_wait_code, hs_report
            try:
                if is_phone_mode == "phone":
                    if not sms_get_func:
                        print(f"[{cfg.ts()}] [ERROR] 手机首发模式下，未开启任何接码平台开关！")
                        return None, None
                    aid, phone, cid, err = sms_get_func(proxies)
                    if not aid:
                        print(f"[{cfg.ts()}] [ERROR] 手机取号失败: {err}")
                        if attempt < MAX_REG_RETRIES - 1:
                            time.sleep(2)
                            continue
                        return None, None
                    print(f"[{cfg.ts()}] [INFO] 成功获取注册手机号: {phone}")
                    login_username = phone
                    login_kind = "phone_number"
                    masked_login = phone
                else:
                    login_username = email
                    login_kind = "email"
                    masked_login = mask_email(email)

                did, current_ua = init_auth(
                    session=s_reg,
                    email=login_username,
                    masked_email=masked_login,
                    proxies=proxies,
                    verify=_ssl_verify()
                )

                if not did or not current_ua:
                    print(f"[{cfg.ts()}] [WARNING] 未获取到 oai-did，节点环境可能被关注，正在为你生成did。")
                    did = str(uuid.uuid4())

                if run_ctx is not None:
                    run_ctx['device_id'] = did
                    run_ctx['user_agent'] = current_ua


                reg_ctx = {}

                print(f"[{cfg.ts()}] [INFO] 正在计算（{masked_login}）风控算力挑战...")

                start_url = "https://auth.openai.com/create-account"
                screen_hint_val = "login_or_signup"

                if getattr(cfg, 'TEAM_MODE_OVERSPEED', False):
                    screen_hint_val = "signup"
                    target_auth_url = oauth_reg.auth_url.replace("prompt=login", "")
                    if "screen_hint=" not in target_auth_url:
                        target_auth_url += "&screen_hint=signup"
                    try:
                        _, start_url = _follow_redirect_chain_local(s_reg, target_auth_url, proxies)
                    except Exception:
                        start_url = target_auth_url


                sentinel_signup = generate_payload(did=did, flow="authorize_continue", proxy=proxy, user_agent=current_ua,
                                                   impersonate="chrome", ctx=reg_ctx)
                if sentinel_signup:
                    print(f"[{cfg.ts()}] [SUCCESS] （{masked_login}）算力挑战成功。")
                signup_headers = _oai_headers(did, {
                    "Referer": "https://auth.openai.com/create-account",
                    "content-type": "application/json",
                })
                signup_headers = _oai_headers(did, {
                    "Referer": start_url,
                    "content-type": "application/json",
                })
                if sentinel_signup:
                    signup_headers["openai-sentinel-token"] = sentinel_signup

                signup_resp = _post_with_retry(
                    s_reg,
                    "https://auth.openai.com/api/accounts/authorize/continue",
                    headers=signup_headers,
                    json_body={"username": {"value": login_username, "kind": login_kind},
                               "screen_hint": screen_hint_val},
                    proxies=proxies,
                )

                if signup_resp.status_code == 403:
                    print(f"[{cfg.ts()}] [WARNING] （{masked_login}）注册请求触发 403 拦截，稍作等待后重试...")
                    return "retry_403", None
                if signup_resp.status_code != 200:
                    print(f"[{cfg.ts()}] [ERROR] （{masked_login}）提交账号环节异常, 返回: {signup_resp.status_code}")
                    return None, None

                try:
                    is_overspeed = getattr(cfg, 'TEAM_MODE_OVERSPEED', False)
                    if is_phone_mode == "phone":
                        sentinel_pwd = generate_payload(did=did, flow="username_password_create", proxy=proxy,
                                                        user_agent=current_ua, impersonate="chrome", ctx=reg_ctx)
                        pwd_headers = _oai_headers(did, {"Referer": "https://auth.openai.com/create-account/password",
                                                         "content-type": "application/json"})
                        if sentinel_pwd: pwd_headers["openai-sentinel-token"] = sentinel_pwd

                        pwd_resp = _post_with_retry(
                            s_reg, "https://auth.openai.com/api/accounts/user/register",
                            headers=pwd_headers, json_body={"password": password, "username": login_username},
                            proxies=proxies,
                        )
                        if pwd_resp.status_code != 200:
                            try:
                                err_json = pwd_resp.json()
                                err_code = str(err_json.get("error", {}).get("code", ""))
                            except:
                                err_code = "unknown"
                            print(f"[{cfg.ts()}] [ERROR] 设密码环节失败 HTTP {pwd_resp.status_code} | {err_code}")
                            if err_code == "account_creation_failed":
                                print(f"[{cfg.ts()}] [WARNING] 手机号 {login_username} 被判定为高风险/虚拟号，已自动退款并准备换号...")
                                sms_report_func(aid, cid, False, "虚拟号拦截", proxies)
                            else:
                                sms_report_func(aid, cid, False, "设密拦截", proxies)
                            continue
                        print(f"[{cfg.ts()}] [INFO] 密码设置成功，正在触发请求短信验证码...")
                        try:
                            nav_headers = {
                                "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                                "accept-language": "zh-CN,zh;q=0.9",
                                "referer": "https://auth.openai.com/create-account/password",
                                "upgrade-insecure-requests": "1",
                                "user-agent": current_ua
                            }
                            trigger_resp = s_reg.get(
                                "https://auth.openai.com/api/accounts/phone-otp/send",
                                headers=nav_headers, proxies=proxies, timeout=15, allow_redirects=True
                            )
                            time.sleep(2)
                        except Exception as e:
                            print(f"[{cfg.ts()}] [WARNING] 请求触发发信时异常: {e}")

                        print(f"[{cfg.ts()}] [INFO] 正在等待 {login_username} 的短信验证码...")
                        sms_code = ""
                        for resend_attempt in range(max(1, cfg.MAX_OTP_RETRIES)):
                            if getattr(cfg, 'GLOBAL_STOP', False): return None, None

                            if resend_attempt > 0:
                                print(
                                    f"\n[{cfg.ts()}] [INFO] 未收到短信，准备请求重新发送手机验证码 {resend_attempt}/{cfg.MAX_OTP_RETRIES}...")
                                try:
                                    resend_headers = {
                                        "accept": "*/*", "accept-language": "zh-CN,zh;q=0.9",
                                        "content-length": "0", "origin": "https://auth.openai.com",
                                        "referer": "https://auth.openai.com/contact-verification",
                                        "user-agent": current_ua
                                    }
                                    resend_resp = _post_with_retry(
                                        s_reg, "https://auth.openai.com/api/accounts/phone-otp/resend",
                                        headers=resend_headers, json_body={}, proxies=proxies, timeout=15,
                                    )
                                    if resend_resp.status_code == 200:
                                        print(f"[{cfg.ts()}] [SUCCESS] 手机验证码重发请求已受理！")
                                    else:
                                        try:
                                            err_json = resend_resp.json()
                                            err_code = str(err_json.get("error", {}).get("code", ""))
                                            err_msg = str(err_json.get("error", {}).get("message", ""))
                                        except:
                                            err_code, err_msg = "unknown", resend_resp.text[:50]
                                        if err_code == "fraud_guard" or "suspicious behavior" in err_msg:
                                            print(f"[{cfg.ts()}] [ERROR] 触发 OpenAI 号段风控！当前号码池已脏。")
                                            sms_report_func(aid, cid, False, "fraud_guard号段拦截", proxies)
                                            break
                                    time.sleep(3)
                                except Exception as e:
                                    pass

                            sms_code = sms_wait_func(aid, proxies)
                            if sms_code: break

                        if not sms_code:
                            sms_report_func(aid, cid, False, "重发后接码依然超时", proxies)
                            continue

                        print(f"[{cfg.ts()}] [INFO] 获取到验证码 {sms_code}，正在提交核验...")
                        sentinel_otp = generate_payload(did=did, flow="authorize_continue", proxy=proxy,
                                                        user_agent=current_ua, impersonate="chrome", ctx=reg_ctx)
                        val_headers = _oai_headers(did, {"Referer": "https://auth.openai.com/phone-verification",
                                                         "content-type": "application/json"})
                        if sentinel_otp: val_headers["openai-sentinel-token"] = sentinel_otp

                        val_resp = _post_with_retry(
                            s_reg, "https://auth.openai.com/api/accounts/phone-otp/validate",
                            headers=val_headers, json_body={"code": sms_code}, proxies=proxies,
                        )

                        if val_resp.status_code == 200:
                            sms_report_func(aid, cid, True, "", proxies)
                            print(f"[{cfg.ts()}] [SUCCESS] 手机 {login_username} 注册核验全流程通过！")
                            try:
                                code_account_url = val_resp.json().get("continue_url", "")
                            except:
                                code_account_url = ""
                        else:
                            sms_report_func(aid, cid, False, "验证码错误", proxies)
                            continue

                        code_account_url = code_account_url.strip()
                        if code_account_url.endswith("/about-you"):
                            try:
                                _, create_account_resp = _create_account_about_you(
                                    session=s_reg, email=login_username, did=did, current_ua=current_ua,
                                    proxy=proxy, proxies=proxies, ctx=reg_ctx,
                                )
                                target_continue_url = str(create_account_resp.json().get("continue_url") or "").strip()
                            except Exception:
                                target_continue_url = ""
                        else:
                            try:
                                target_continue_url = str(val_resp.json().get("continue_url") or "").strip()
                            except Exception:
                                target_continue_url = ""
                    else:
                        signup_json = signup_resp.json()
                        continue_url = signup_json.get("continue_url", "")
                        if is_overspeed or "log-in" in continue_url or "/email-verification" in continue_url:
                            is_openai_cpa = getattr(cfg, 'EMAIL_API_MODE', '')
                            force_original_pwd = getattr(cfg, 'USE_ORIGINAL_PASSWORD_FLOW', False)
                            if is_overspeed or (is_openai_cpa == "openai_cpa" and force_original_pwd):
                                pass
                            else:
                                print(f"[{cfg.ts()}] [WARNING] （{masked_login}）该邮箱无需密码注册！准备走【无密码通道】进行接管...")
                                is_takeover = True
                                login_ctx = reg_ctx.copy() if reg_ctx else {}
                                sentinel_login = generate_payload(did=did, flow="authorize_continue", proxy=proxy, user_agent=current_ua,
                                                                  impersonate="chrome", ctx=login_ctx)
                                login_send_headers = _oai_headers(did, {
                                    "Referer": "https://auth.openai.com/log-in/password",
                                    "content-type": "application/json",
                                })
                                if sentinel_login: login_send_headers["openai-sentinel-token"] = sentinel_login

                                if cfg.EMAIL_API_MODE == "luckmail":
                                    try:
                                        from utils.email_providers.luckmail_service import LuckMailService
                                        print(f"[{cfg.ts()}] [INFO] 正在检测 LuckMail 邮箱（{masked_login}）是否存活...")
                                        lm_service = LuckMailService(
                                            api_key=cfg.LUCKMAIL_API_KEY,
                                            proxies=proxies if getattr(cfg, 'USE_PROXY_FOR_EMAIL', True) else None
                                        )
                                        if not lm_service.check_token_alive(email_jwt):
                                            print(f"[{cfg.ts()}] [ERROR] （{masked_login}）邮箱 已失效，放弃当前注册并重试！")
                                            return None, None
                                    except Exception as e:
                                        print(f"[{cfg.ts()}] [WARNING] LuckMail 可用性检测异常(忽略并继续): {e}")

                                print(f"[{cfg.ts()}] [INFO] （{masked_login}）无密码通道注册发信...")
                                sentinel_login_resp = _post_with_retry(
                                    s_reg,
                                    "https://auth.openai.com/api/accounts/passwordless/send-otp",
                                    headers=login_send_headers, proxies=proxies, timeout=30,
                                )

                                if sentinel_login_resp.status_code != 200:
                                    print(f"[{cfg.ts()}] [ERROR] （{masked_login}）无密码通道邮件发送异常, 返回: {sentinel_login_resp.status_code}")
                                    return None, None

                                login_code = ""
                                code_resp = None
                                for resend_attempt in range(max(1, cfg.MAX_OTP_RETRIES)):
                                    if getattr(cfg, 'GLOBAL_STOP', False): return None, None
                                    if resend_attempt > 0:
                                        print(f"\n[{cfg.ts()}] [INFO] 无密码通道正在请求重新发送登录验证码 {resend_attempt}/{cfg.MAX_OTP_RETRIES}...")
                                        try:
                                            sentinel_resend = generate_payload(did=did, flow="authorize_continue", proxy=proxy,
                                                                               user_agent=current_ua, impersonate="chrome",
                                                                               ctx=login_ctx)
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
                                            time.sleep(3)
                                        except Exception as e:
                                            print(f"[{cfg.ts()}] [WARNING] （{masked_login}）无密码通道重新发送请求异常: {e}")

                                    login_code = get_oai_code(email, jwt=email_jwt, proxies=proxies,
                                                              processed_mail_ids=processed_mails)

                                    if not login_code:
                                        print(f"[{cfg.ts()}] [WARNING] {masked_login}无密码通道本轮未拉取到验证码，准备重发...")
                                        continue

                                    login_sentinel_otp = generate_payload(did=did, flow="authorize_continue", proxy=proxy,
                                                                          user_agent=current_ua,
                                                                          impersonate="chrome", ctx=login_ctx)
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
                                        print(f"[{cfg.ts()}] [SUCCESS] （{masked_login}）无密码通道接管验证通过！")
                                        password = "Takeover_NoPassword"
                                        break
                                    else:
                                        err_json = code_resp.json()
                                        print(f"[{cfg.ts()}] [WARNING] （{masked_login}）无密码通道接管验证失败: {code_resp.status_code}")
                                        print(f"[{cfg.ts()}] [INFO] （{masked_login}）无密码通道准备请求新的验证码并重试...")
                                        login_code = ""
                                        continue

                                if not login_code and (code_resp is None or code_resp.status_code != 200):
                                    print(
                                        f"[{cfg.ts()}] [ERROR] 无密码通道验证码重试达上限 ({cfg.MAX_OTP_RETRIES} 次)，丢弃当前 {masked_login} 邮箱。")
                                    if run_ctx is not None:
                                        run_ctx['discarded_email_failure'] = True
                                        run_ctx['mail_domain_failure_reason'] = 'discarded_email'
                                    return None, None

                                code_url = str(code_resp.json().get("continue_url") or "").strip()
                                if code_url.endswith("/about-you"):
                                    _, create_account_resp = _create_account_about_you(
                                        session=s_reg, email=email, did=did, current_ua=current_ua,
                                        proxy=proxy, proxies=proxies, ctx=login_ctx,
                                    )
                                    try:
                                        target_continue_url = str(create_account_resp.json().get("continue_url") or "").strip()
                                    except Exception:
                                        target_continue_url = ""
                                else:
                                    try:
                                        target_continue_url = str(code_resp.json().get("continue_url") or "").strip()
                                    except Exception:
                                        target_continue_url = ""
                except Exception as e:
                    pass

                if not is_takeover and is_phone_mode != "phone":
                    sentinel_pwd = generate_payload(did=did, flow="username_password_create", proxy=proxy, user_agent=current_ua,
                                                    impersonate="chrome", ctx=reg_ctx)
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
                            print(f"[{cfg.ts()}] [ERROR] （{masked_login}）遭遇底层影子风控 (无明确代码拦截)！当前 IP或者域名 可能已黑。")
                            if run_ctx is not None: run_ctx['pwd_blocked'] = True
                            return None, None
                        print(f"[{cfg.ts()}] [ERROR] （{masked_login}）设密码环节被拦截，返回: {pwd_resp.status_code}，该提示可忽略，不影响后面执行流程")
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
                                print(f"[{cfg.ts()}] [INFO] 正在检测 LuckMail 邮箱（{masked_login}）是否存活...")
                                lm_service = LuckMailService(
                                    api_key=cfg.LUCKMAIL_API_KEY,
                                    proxies=proxies if getattr(cfg, 'USE_PROXY_FOR_EMAIL', True) else None
                                )
                                if not lm_service.check_token_alive(email_jwt):
                                    print(f"[{cfg.ts()}] [ERROR] （{masked_login}）邮箱 已失效，放弃当前注册并重试！")
                                    return None, None
                            except Exception as e:
                                print(f"[{cfg.ts()}] [WARNING] LuckMail 可用性检测异常(忽略并继续): {e}")
                        is_openai_cpa = getattr(cfg, 'EMAIL_API_MODE', '')
                        force_original_pwd = getattr(cfg, 'USE_ORIGINAL_PASSWORD_FLOW', False)
                        if is_openai_cpa == "openai_cpa" and force_original_pwd:
                            old_raw = code_pool.get(email, "")
                            old_code = _extract_otp_code(old_raw)
                        print(f"\n[{cfg.ts()}] [INFO] 正在向 {masked_login} 主动请求发送验证码...")
                        send_otp_url = "https://auth.openai.com/api/accounts/email-otp/send"

                        try:
                            sentinel_send = generate_payload(did=did, flow="authorize_continue", proxy=proxy, user_agent=current_ua,
                                                             impersonate="chrome", ctx=reg_ctx)
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
                            print(f"[{cfg.ts()}] [WARNING] （{masked_login}）OTP 初始发送请求异常: {e}")

                        code = ""
                        code_resp = None
                        for resend_attempt in range(max(1, cfg.MAX_OTP_RETRIES)):
                            if getattr(cfg, 'GLOBAL_STOP', False): return None, None
                            if resend_attempt > 0:
                                is_openai_cpa = getattr(cfg, 'EMAIL_API_MODE', '')
                                force_original_pwd = getattr(cfg, 'USE_ORIGINAL_PASSWORD_FLOW', False)
                                if is_openai_cpa == "openai_cpa" and force_original_pwd:
                                    code_pool.pop(email, None)
                                    old_raw = code_pool.get(email, "")
                                    old_code = _extract_otp_code(old_raw)
                                print(f"\n[{cfg.ts()}] [INFO] 正在重试 {resend_attempt}/{cfg.MAX_OTP_RETRIES}...")
                                try:
                                    sentinel_resend = generate_payload(did=did, flow="authorize_continue", proxy=proxy,
                                                                       user_agent=current_ua, impersonate="chrome", ctx=reg_ctx)
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
                                    print(f"[{cfg.ts()}] [WARNING] （{masked_login}）重新发送请求异常: {e}")
                            is_openai_cpa = getattr(cfg, 'EMAIL_API_MODE', '')
                            force_original_pwd = getattr(cfg, 'USE_ORIGINAL_PASSWORD_FLOW', False)
                            if is_openai_cpa == "openai_cpa" and force_original_pwd:
                                code = get_oai_code(email, jwt=email_jwt, proxies=proxies,
                                                processed_mail_ids=processed_mails,ignore_code=old_code)
                            else:
                                code = get_oai_code(email, jwt=email_jwt, proxies=proxies,
                                                processed_mail_ids=processed_mails)

                            if not code:
                                print(f"[{cfg.ts()}] [WARNING] {masked_login} 本轮未拉取到验证码，准备重发...")
                                continue

                            sentinel_otp = generate_payload(did=did, flow="authorize_continue", proxy=proxy, user_agent=current_ua,
                                                            impersonate="chrome", ctx=reg_ctx)
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
                                print(f"[{cfg.ts()}] [WARNING] （{masked_login}）验证码校验未通过: {code_resp.status_code}，准备重新请求...")
                                code = ""
                                continue
                            elif code_resp.status_code == 200:
                                break

                        if not code or code_resp is None or code_resp.status_code != 200:
                            print(f"[{cfg.ts()}] [ERROR] 重试次数达上限，或验证码最终校验未通过，丢弃当前 {masked_login} 邮箱。")
                            if run_ctx is not None:
                                run_ctx['discarded_email_failure'] = True
                                run_ctx['mail_domain_failure_reason'] = 'discarded_email'
                            return None, None
                        code_account_json = code_resp.json()
                        code_account_url = code_account_json.get("continue_url", "")

                        if "/add-phone" in code_account_url:
                            print(f"[{cfg.ts()}] [WARNING] （{masked_login}） 账号创建过程触发手机风控...")
                            if not bool(cfg.HERO_SMS_ENABLED):
                                if attempt < MAX_REG_RETRIES - 1:
                                    print(
                                        f"[{cfg.ts()}] [INFO] （{masked_login}） 准备重置环境，重新进行第 {attempt + 2} 次 注册流程尝试...")
                                    continue
                            print(f"[{cfg.ts()}] [WARNING] （{masked_login}） 账号创建过程多次尝试仍触发手机风控，进入 HeroSMS 手机号验证流程...")
                            print(f"[{cfg.ts()}] [WARNING] （{masked_login}） 重点提示：有些邮箱接码后也无法创建成功账号，可能Oauth阶段还需要接码，请自行斟酌...")
                            if getattr(cfg, 'SMSBOWER_ENABLED', False) and getattr(cfg, 'SMSBOWER_VERIFY_ON_REGISTER', False):
                                provider_name = "SmsBower"
                                ok, next_url_or_reason = handle_smsbower_verification(
                                    session=s_reg, proxies=proxies, hint_url=code_account_url, device_id=did , user_agent=current_ua , run_ctx=reg_ctx, proxy=proxy
                                )
                            elif getattr(cfg, 'HERO_SMS_ENABLED', False) and getattr(cfg, 'HERO_SMS_VERIFY_ON_REGISTER', False):
                                provider_name = "HeroSMS"
                                ok, next_url_or_reason = _try_verify_phone_via_hero_sms(
                                    session=s_reg, proxies=proxies, hint_url=code_account_url, device_id=did , user_agent=current_ua , run_ctx=reg_ctx, proxy=proxy
                                )
                            elif getattr(cfg, 'FIVESIM_ENABLED', False) and getattr(cfg, 'FIVESIM_VERIFY_ON_REGISTER', False):
                                provider_name = "5SIM"
                                ok, next_url_or_reason = try_verify_phone_via_fivesim(
                                    session=s_reg, proxies=proxies, hint_url=code_account_url, device_id=did , user_agent=current_ua , run_ctx=reg_ctx, proxy=proxy
                                )
                            else:
                                print(f"[{cfg.ts()}] [WARNING] （{masked_login}） 接码主开关或创建时接码开关未开启，如果不想花钱接码请忽略该提示")
                                if run_ctx is not None: run_ctx['phone_verify'] = True
                                return None, None

                            if ok and next_url_or_reason:
                                print(f"[{cfg.ts()}] [INFO] （{masked_login}） {provider_name} 手机验证成功，继续创建账号{next_url_or_reason}")
                            else:
                                print(f"[{cfg.ts()}] [ERROR] （{masked_login}） {provider_name}验证失败: {next_url_or_reason}")
                                if run_ctx is not None: run_ctx['phone_verify'] = True
                                return None, None

                    _, create_account_resp = _create_account_about_you(
                        session=s_reg, email=email, did=did, current_ua=current_ua,
                        proxy=proxy, proxies=proxies, ctx=reg_ctx,
                    )

                    if create_account_resp.status_code != 200:
                        err_json = create_account_resp.json()
                        err_code = str(err_json.get("error", {}).get("code", "")).strip()
                        err_msg = str(err_json.get("error", {}).get("message", "")).strip()
                        if err_code == "identity_provider_mismatch" or err_code == "user_already_exists":
                            if getattr(cfg, 'DISABLE_FORCED_TAKEOVER', True):
                                print(
                                    f"[{cfg.ts()}] [ERROR] （{masked_login}）该邮箱标记为第三方登录账号，因开启了[放弃强行变道]开关，直接丢弃以节省接码成本。")
                                if run_ctx is not None: run_ctx['signup_blocked'] = True
                                return None, None
                            else:
                                try:
                                    is_takeover = True
                                    print(f"[{cfg.ts()}] [WARNING] （{masked_login}）检测到第三方登录账号！因关闭了[放弃强行变道]开关，强行变道走无密码 OTP...")
                                    print(f"[{cfg.ts()}] [WARNING] （{masked_login}）已打上接管标记，交由 OAuth 提取流程进行无密码登录...")
                                except Exception as e:
                                    pass

                        if not is_takeover:
                            if "been deleted or deactivated" in err_msg:
                                print(f"[{cfg.ts()}] [WARNING] （{masked_login}）您没有帐户，因为它已被删除或停用。如果您认为这是一个错误，请通过我们的帮助中心help.openai.com.与我们联系")
                                run_ctx['signup_blocked'] = True
                                return None, None
                            run_ctx['signup_blocked'] = True
                            print(f"[{cfg.ts()}] [WARNING] （{masked_login}）账户创建受阻，疑似被标记为账号已存在，返回: {create_account_resp.status_code}，该提示可忽略，不影响后面执行流程")
                            return None, None

                    try:
                        target_continue_url = str(create_account_resp.json().get("continue_url") or "").strip()
                    except Exception:
                        target_continue_url = ""

                wait_time = random.randint(cfg.LOGIN_DELAY_MIN, cfg.LOGIN_DELAY_MAX)

                if not target_continue_url:
                    print(f"[{cfg.ts()}] [INFO] （{masked_login}）账号创建被拦截，域名已被风控。请更换域名...")
                    return None, None

                print(f"[{cfg.ts()}] [INFO] （{masked_login}）账号已通过，等待 {wait_time} 秒后同步最终状态...")


                if cfg.ENABLE_CPA_MODE:
                    should_retain = cfg.SAVE_TO_LOCAL_IN_CPA_MODE and cfg.CPA_RETAIN_REG_ONLY
                    mode_label = "CPA模式"
                elif cfg.ENABLE_SUB2API_MODE:
                    should_retain = cfg.SUB2API_SAVE_TO_LOCAL and cfg.SUB2API_RETAIN_REG_ONLY
                    mode_label = "Sub2API模式"
                else:
                    should_sync_normal = True
                    should_retain = should_sync_normal and cfg.RETAIN_REG_ONLY
                    mode_label = "常规模式"

                if should_retain:
                    try:
                        from utils import db_manager
                        db_manager.save_account_to_db(login_username, password, '{"status": "仅注册成功"}')
                        print(f"[{cfg.ts()}] [INFO] [{mode_label}] （{masked_login}）账号已注册成功，根据配置提前作为半成品写入本地库。")
                    except Exception as e:
                        pass
                data = image2api_data(s_reg, target_continue_url, proxies)


                if mode_label == "常规模式":
                    if getattr(cfg, "ENABLE_IMAGE2API_MODE", False):
                        print(f"[{cfg.ts()}] [INFO] [IMAGE2API] （{masked_login}）根据配置将同步至IMAGE2API平台。")
                        if data:
                            client = Image2APIClient()
                            ok, msg = client.add_accounts([data])
                            if ok:
                                print(f"[{cfg.ts()}] [SUCCESS] [IMAGE2API] （{masked_login}）同步成功")
                            else:
                                print(f"[{cfg.ts()}] [ERROR] [IMAGE2API] （{masked_login}）同步失败: {msg}")
                    if getattr(cfg, "IMAGE2API_IMG_ONLY_MODE", False):
                        print(f"[{cfg.ts()}] [INFO] 当前为仅注册img模式")
                        if data:
                            res_payload = json.dumps({"email": email, "status": "image2api", "access_token": data, "device_id": did, "user_agent": current_ua})
                            return res_payload, password
                        else:
                            print(f"[{cfg.ts()}] [WARNING] img账号未成功获取数据，暂不保存")
                            return None, None

                    elif getattr(cfg, "NORMAL_SAVE_IMG_TO_LOCAL", False):
                        try:
                            from utils import db_manager
                            if data:
                                image2api_token_data = json.dumps({
                                    "status": "image2api",
                                    "access_token": data
                                })
                                db_manager.save_account_to_db(login_username, password, image2api_token_data)
                                print(f"[{cfg.ts()}] [INFO] [IMAGE2API] （{masked_login}）账号已注册成功，已将 image2api 写回本地库。")
                        except Exception as e:
                            print(f"[{cfg.ts()}] [ERROR] 写入本地库失败: {e}")
                else:
                    if getattr(cfg, "ENABLE_IMAGE2API_MODE", False):
                        print(f"[{cfg.ts()}] [INFO] [IMAGE2API] （{masked_login}）根据配置将同步至IMAGE2API平台。")
                        if data:
                            client = Image2APIClient()
                            ok, msg = client.add_accounts([data])
                            if ok:
                                print(f"[{cfg.ts()}] [SUCCESS] [IMAGE2API] （{masked_login}）同步成功")
                            else:
                                print(f"[{cfg.ts()}] [ERROR] [IMAGE2API] （{masked_login}）同步失败: {msg}")
                    if getattr(cfg, "IMAGE2API_IMG_ONLY_MODE", False):
                        print(f"[{cfg.ts()}] [INFO] 当前为仅注册img模式")
                        if data:
                            res_payload = json.dumps({"email": login_username, "status": "image2api", "access_token": data, "device_id": did, "user_agent": current_ua})
                            return res_payload, password
                        else:
                            print(f"[{cfg.ts()}] [WARNING] img账号未成功获取数据，暂不保存")
                            return None, None
                    elif getattr(cfg, "IMAGE2API_RETAIN_REG_ONLY", False):
                        try:
                            from utils import db_manager
                            image2api_token_data = json.dumps({
                                "status": "image2api",
                                "access_token": data
                            })
                            db_manager.save_account_to_db(login_username, password, image2api_token_data)
                            print(f"[{cfg.ts()}] [INFO] [IMAGE2API] （{masked_login}）账号已注册成功，根据配置已将 image2api 写回本地库。")
                        except Exception as e:
                            print(f"[{cfg.ts()}] [ERROR] 写入本地库失败: {e}")
                if data:
                    saved_temp_at = data
                    if getattr(cfg, 'TEAM_MODE_ENABLE', False):
                        print(f"[{cfg.ts()}] [INFO] （{masked_login}）即将进入团队静默流程")
                        time.sleep(random.uniform(0.1, 0.5))
                        is_alloc, sys_handle_a, sys_handle_b, sys_handle_c = sys_node_allocate(s_reg, did, saved_temp_at, proxies)
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
                    print(f"[{cfg.ts()}] [SUCCESS] （{masked_login}）检测到工作区，正在确认并提取最终凭据...")
                    target_workspace_id = ""
                    if getattr(cfg, 'TEAM_MODE_ENABLE', False):
                        for ws in workspaces:
                            ws_title = str(ws.get("title", ws.get("name", "Unknown")))
                            if "Personal" in ws_title or "个人" in ws_title or ws.get("is_personal"):
                                target_workspace_id = str(ws.get("id", ""))
                                break

                        if not target_workspace_id and workspaces:
                            target_workspace_id = str(workspaces[-1].get("id", ""))
                    else:
                        if workspaces:
                            target_workspace_id = str(workspaces[0].get("id", ""))
                    if target_workspace_id:
                        select_resp = _post_with_retry(
                            s_reg,
                            "https://auth.openai.com/api/accounts/workspace/select",
                            headers=_oai_headers(did, {"Referer": current_url, "content-type": "application/json"}),
                            json_body={"workspace_id": target_workspace_id},
                            proxies=proxies,
                        )
                        if select_resp.status_code == 200:
                            try:
                                next_url = str(select_resp.json().get("continue_url") or "").strip()
                            except Exception:
                                next_url = ""
                            if next_url:
                                _, final_url = _follow_redirect_chain_local(s_reg, next_url, proxies)
                                if "code=" in final_url and "state=" in final_url:
                                    print(f"[{cfg.ts()}] [SUCCESS] （{masked_login}）凭据提取成功！一气呵成！")
                                    return submit_callback_url(
                                        callback_url=final_url,
                                        expected_state=oauth_reg.state,
                                        code_verifier=oauth_reg.code_verifier,
                                        proxies=proxies
                                    ), password

                print(f"[{cfg.ts()}] [INFO] （{masked_login}）账号登录完毕，执行静默获取 Token...")
                OAUTH_MAX_RETRIES = 2

                for oauth_attempt in range(OAUTH_MAX_RETRIES):
                    s_log = requests.Session(proxies=proxies, impersonate="chrome")
                    s_log.headers.update({"Connection": "close"})
                    s_log.cookies.clear()
                    s_log.timeout = 30
                    oauth_log = generate_oauth_url()

                    resp, current_url = _follow_redirect_chain_local(s_log, oauth_log.auth_url, proxies)
                    if "code=" in current_url and "state=" in current_url:
                        token_resp = submit_callback_url(
                            callback_url=current_url,
                            code_verifier=oauth_log.code_verifier,
                            redirect_uri=oauth_log.redirect_uri,
                            expected_state=oauth_log.state,
                            proxies=proxies,
                        ), password
                        return token_resp, password
                    log_did = s_log.cookies.get("oai-did") or did

                    log_ctx = reg_ctx.copy() if reg_ctx else {}
                    log_ctx["session_id"] = str(uuid.uuid4())
                    now_ms = int(time.time() * 1000)
                    log_ctx["time_origin"] = float(now_ms - random.randint(20000, 300000))

                    sentinel_log = generate_payload(did=log_did, flow="authorize_continue", proxy=proxy, user_agent=current_ua,
                                                    impersonate="chrome", ctx=log_ctx)
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
                        json_body={"username": {"value": login_username, "kind": login_kind}},
                        proxies=proxies, allow_redirects=False,
                    )

                    if login_start_resp.status_code != 200:
                        print(f"[{cfg.ts()}] [ERROR] （{masked_login}）登录环节第一步请求被拒: HTTP {login_start_resp.status_code}")
                        return None, None

                    if is_takeover:
                        # log_send_headers = generate_payload(did=log_did, flow="authorize_continue", proxy=proxy, user_agent=current_ua,
                        #                                   impersonate="chrome", ctx=log_ctx)
                        # login_send_headers = _oai_headers(log_did, {
                        #     "Referer": "https://auth.openai.com/email-verification",
                        #     "content-type": "application/json",
                        # })
                        # if log_send_headers: login_send_headers["openai-sentinel-token"] = log_send_headers
                        if cfg.EMAIL_API_MODE == "luckmail":
                            try:
                                from utils.email_providers.luckmail_service import LuckMailService
                                print(f"[{cfg.ts()}] [INFO] 正在检测 LuckMail 邮箱（{masked_login}）是否存活...")
                                lm_service = LuckMailService(
                                    api_key=cfg.LUCKMAIL_API_KEY,
                                    proxies=proxies if getattr(cfg, 'USE_PROXY_FOR_EMAIL', True) else None
                                )
                                if not lm_service.check_token_alive(email_jwt):
                                    print(f"[{cfg.ts()}] [ERROR] （{masked_login}）邮箱 已失效，放弃当前注册并重试！")
                                    return None, None
                            except Exception as e:
                                print(f"[{cfg.ts()}] [WARNING] LuckMail 可用性检测异常(忽略并继续): {e}")
                        print(f"[{cfg.ts()}] [INFO] （{masked_login}）无密码通道OAuth登录发信...")
                        # sentinel_login_resp = _post_with_retry(
                        #     s_log,
                        #     "https://auth.openai.com/api/accounts/passwordless/send-otp",
                        #     headers=login_send_headers, proxies=proxies, timeout=30,
                        # )
                        #
                        # if sentinel_login_resp.status_code != 200:
                        #     print(f"[{cfg.ts()}] [ERROR] （{mask_email(email)}）邮件发送异常, 返回: {sentinel_login_resp.status_code}")
                        #     return None, None

                        login_code_oauth = ""
                        login_code_resp = None
                        for login_code_attempt in range(max(1, cfg.MAX_OTP_RETRIES)):
                            if getattr(cfg, 'GLOBAL_STOP', False): return None, None
                            if login_code_attempt > 0:
                                print(
                                    f"\n[{cfg.ts()}] [INFO] （{masked_login}）无密码通道OAuth 阶段未收到验证码或验证失败，正在重试 {login_code_attempt}/{cfg.MAX_OTP_RETRIES}...")
                                try:
                                    login_code_resend = generate_payload(did=log_did, flow="authorize_continue",
                                                                         proxy=proxy, user_agent=current_ua,
                                                                         impersonate="chrome", ctx=log_ctx)
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
                                    print(f"[{cfg.ts()}] [WARNING] （{masked_login}）无密码通道重新发送请求异常: {e}")

                            login_code_oauth = get_oai_code(email, jwt=email_jwt, proxies=proxies,
                                                            processed_mail_ids=processed_mails)
                            if not login_code_oauth:
                                print(f"[{cfg.ts()}] [WARNING] （{masked_login}）无密码通道本轮未拉取到验证码，准备重发...")
                                continue

                            login_sentinel_otp = generate_payload(did=log_did, flow="authorize_continue", proxy=proxy,
                                                                  user_agent=current_ua,
                                                                  impersonate="chrome", ctx=log_ctx)
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
                                print(f"[{cfg.ts()}] [SUCCESS] （{masked_login}）无密码通道OAuth阶段验证码通过！")
                                password = "Takeover_NoPassword"
                                break
                            else:
                                try:
                                    err_json = login_code_resp.json()
                                except:
                                    err_json = {}
                                print(f"[{cfg.ts()}] [WARNING] （{masked_login}）无密码通道OAuth 阶段验证失败: {login_code_resp.status_code}")
                                print(f"[{cfg.ts()}] [INFO] （{masked_login}）无密码通道准备请求新的验证码并重试...")
                                login_code_oauth = ""
                                continue

                        if not login_code_oauth and (login_code_resp is None or login_code_resp.status_code != 200):
                            print(
                                f"[{cfg.ts()}] [ERROR] 无密码通道重试次数达上限 ({cfg.MAX_OTP_RETRIES} 次)，丢弃当前 {masked_login} 邮箱，放弃接管。")
                            if run_ctx is not None:
                                run_ctx['discarded_email_failure'] = True
                                run_ctx['mail_domain_failure_reason'] = 'discarded_email'
                            return None, None

                        login_code_url = str(login_code_resp.json().get("continue_url") or "").strip()
                        if login_code_url.endswith("/about-you"):
                            _, create_account_resp = _create_account_about_you(
                                session=s_log, email=email, did=did, current_ua=current_ua,
                                proxy=proxy, proxies=proxies, ctx=log_ctx,
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
                                                            impersonate="chrome", ctx=log_ctx)
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
                            print(f"[{cfg.ts()}] [ERROR] （{masked_login}）最终静默登录验证失败: HTTP {pwd_login_resp.status_code}")
                            return None, None

                        pwd_json = pwd_login_resp.json()
                        next_url = _extract_next_url(pwd_json)
                        resp, current_url = _follow_redirect_chain_local(s_log, next_url, proxies)

                        if current_url.endswith("/email-verification"):
                            if cfg.EMAIL_API_MODE == "luckmail":
                                try:
                                    from utils.email_providers.luckmail_service import LuckMailService
                                    print(f"[{cfg.ts()}] [INFO] 正在检测 LuckMail 邮箱（{masked_login}）是否存活...")
                                    lm_service = LuckMailService(
                                        api_key=cfg.LUCKMAIL_API_KEY,
                                        proxies=proxies if getattr(cfg, 'USE_PROXY_FOR_EMAIL', True) else None
                                    )
                                    if not lm_service.check_token_alive(email_jwt):
                                        print(f"[{cfg.ts()}] [ERROR] （{masked_login}）邮箱 已失效，放弃当前注册并重试！")
                                        return None, None
                                except Exception as e:
                                    print(f"[{cfg.ts()}] [WARNING] LuckMail 可用性检测异常(忽略并继续): {e}")

                            print(f"\n[{cfg.ts()}] [INFO] （{masked_login}）静默登录需要验证码，主动触发发送...")

                            code2 = ""
                            code2_resp = None
                            for resend_attempt in range(max(1, cfg.MAX_OTP_RETRIES)):
                                if getattr(cfg, 'GLOBAL_STOP', False): return None, None
                                if resend_attempt > 0:
                                    print(
                                        f"\n[{cfg.ts()}] [INFO] （{masked_login}）二次安全验证未收到验证码或校验失败，正在重试 {resend_attempt}/{cfg.MAX_OTP_RETRIES}...")
                                    try:
                                        sentinel_log_resend = generate_payload(did=log_did, flow="authorize_continue", proxy=proxy,
                                                                               user_agent=current_ua, impersonate="chrome", ctx=log_ctx)
                                        log_resend_headers = _oai_headers(log_did, {
                                            "Referer": "https://auth.openai.com/email-verification",
                                            "content-type": "application/json"
                                        })
                                        if sentinel_log_resend:
                                            log_resend_headers["openai-sentinel-token"] = sentinel_log_resend
                                        _post_with_retry(
                                            s_log,
                                            "https://auth.openai.com/api/accounts/email-otp/send",
                                            headers=log_resend_headers,
                                            json_body={}, proxies=proxies, timeout=15,
                                        )
                                        time.sleep(2)
                                    except Exception as e:
                                        print(f"[{cfg.ts()}] [WARNING] （{masked_login}）重新发送请求异常: {e}")
                                code2 = get_oai_code(email, jwt=email_jwt, proxies=proxies,
                                                     processed_mail_ids=processed_mails)

                                if not code2:
                                    print(f"[{cfg.ts()}] [WARNING] （{masked_login}）本轮未拉取到二次安全验证码，准备重发...")
                                    continue

                                sentinel_otp2 = generate_payload(did=log_did, flow="authorize_continue", proxy=proxy,
                                                                 user_agent=current_ua, impersonate="chrome", ctx=log_ctx)
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

                                if code2_resp.status_code == 200:
                                    print(f"[{cfg.ts()}] [SUCCESS] （{masked_login}）二次安全验证 OTP 校验通过！")
                                    break
                                else:
                                    print(
                                        f"[{cfg.ts()}] [WARNING] （{masked_login}）二次安全验证 OTP 校验失败: {code2_resp.status_code}")
                                    print(f"[{cfg.ts()}] [INFO] （{masked_login}）准备请求新的二次安全验证码并重试...")
                                    code2 = ""
                                    continue
                            if not code2 and (code2_resp is None or code2_resp.status_code != 200):
                                print(f"[{cfg.ts()}] [ERROR] （{masked_login}）重试次数达上限，二次安全验证彻底失败，放弃接管。")
                                return None, None

                            next_url = str(code2_resp.json().get("continue_url") or "").strip()
                            resp, current_url = _follow_redirect_chain_local(s_log, next_url, proxies)

                    url_code = ""
                    error_reason = ""
                    oauth_needs_retry = False
                    while True:
                        if "code=" in current_url:
                            token_resp = submit_callback_url(
                                callback_url=current_url,
                                code_verifier=oauth_log.code_verifier,
                                redirect_uri=oauth_log.redirect_uri,
                                expected_state=oauth_log.state,
                                proxies=proxies,
                            )
                            return token_resp, password
                        elif current_url.endswith("/about-you"):
                            _, create_account_resp = _create_account_about_you(
                                session=s_log, email=email, did=did, current_ua=current_ua,
                                proxy=proxy, proxies=proxies, ctx=log_ctx,
                            )
                            url_code = create_account_resp.json()
                            current_url = str(create_account_resp.json().get("continue_url") or "").strip()
                            continue
                        if current_url.endswith("/consent") or current_url.endswith("/workspace"):
                            auth_cookie2 = s_log.cookies.get("oai-client-auth-session") or ""
                            workspaces2 = _parse_workspace_from_auth_cookie(auth_cookie2)
                            if workspaces2:
                                print(f"[{cfg.ts()}] [SUCCESS] （{masked_login}）检测到工作区，正在确认并流转...")
                                target_workspace_id2 = ""
                                if getattr(cfg, 'TEAM_MODE_ENABLE', False):
                                    for ws in workspaces2:
                                        ws_title = str(ws.get("title", ws.get("name", "Unknown")))
                                        if "Personal" in ws_title or "个人" in ws_title or ws.get("is_personal"):
                                            target_workspace_id2 = str(ws.get("id", ""))
                                            break
                                    if not target_workspace_id2:
                                        target_workspace_id2 = str(workspaces2[-1].get("id", ""))
                                else:
                                    target_workspace_id2 = str(workspaces2[0].get("id", ""))
                                if target_workspace_id2:
                                    select_resp = _post_with_retry(
                                        s_log, "https://auth.openai.com/api/accounts/workspace/select",
                                        headers=_oai_headers(s_log.cookies.get("oai-did") or "",
                                                             {"Referer": current_url,
                                                              "content-type": "application/json"}),
                                        json_body={"workspace_id": target_workspace_id2}, proxies=proxies,
                                    )
                                    final_url = _extract_next_url(
                                        select_resp.json()) if select_resp.status_code == 200 else ""
                                    _, final_loc = _follow_redirect_chain_local(s_log, final_url, proxies)
                                    current_url = final_loc
                                    continue
                            else:
                                break
                        elif "/add-phone" in current_url:
                            if oauth_attempt == 0 and getattr(cfg, 'TEAM_MODE_ENABLE', False):
                                print(
                                    f"[{cfg.ts()}] [WARNING] （{masked_login}） OAuth重试中...")
                                time.sleep(random.uniform(2.0, 4.0))
                                oauth_needs_retry = True
                                break
                            print(f"[{cfg.ts()}] [INFO] （{masked_login}） OAuth链路触发风控，进入手机号验证...")
                            if getattr(cfg, 'SMSBOWER_ENABLED', False):
                                provider_name = "SmsBower"
                                ok, next_url_or_reason = handle_smsbower_verification(
                                    session=s_log, proxies=proxies, hint_url=current_url, device_id=did , user_agent=current_ua , run_ctx=reg_ctx, proxy=proxy
                                )
                            elif getattr(cfg, 'HERO_SMS_ENABLED', False):
                                provider_name = "HeroSMS"
                                ok, next_url_or_reason = _try_verify_phone_via_hero_sms(
                                    session=s_log, proxies=proxies, hint_url=current_url, device_id=did , user_agent=current_ua , run_ctx=reg_ctx, proxy=proxy
                                )
                            elif getattr(cfg, 'FIVESIM_ENABLED', False):
                                provider_name = "5SIM"
                                ok, next_url_or_reason = try_verify_phone_via_fivesim(
                                    session=s_log, proxies=proxies, hint_url=current_url, device_id=did , user_agent=current_ua , run_ctx=reg_ctx, proxy=proxy
                                )
                            else:
                                break
                            if ok and next_url_or_reason:
                                print(f"[{cfg.ts()}] [INFO] （{masked_login}） {provider_name} 手机验证成功，继续获取凭证")
                                current_url = next_url_or_reason
                                continue
                            else:
                                print(f"[{cfg.ts()}] [ERROR] （{masked_login}） {provider_name}验证失败: {next_url_or_reason}")
                                error_reason = next_url_or_reason
                                break
                        elif "/add-email" in current_url:
                            print(f"[{cfg.ts()}] [INFO] （{masked_login}） 触发绑定邮箱流程，正在提交预备邮箱 {mask_email(email)}...")
                            add_email_hdrs = _oai_headers(log_did, {
                                "Referer": current_url,
                                "content-type": "application/json"
                            })
                            sentinel_add_email = generate_payload(did=log_did, flow="authorize_continue",
                                                                  proxy=proxy, user_agent=current_ua,
                                                                  impersonate="chrome", ctx=log_ctx)
                            if sentinel_add_email:
                                add_email_hdrs["openai-sentinel-token"] = sentinel_add_email

                            add_email_resp = _post_with_retry(
                                s_log, "https://auth.openai.com/api/accounts/add-email/send",
                                headers=add_email_hdrs, json_body={"email": email}, proxies=proxies
                            )

                            if add_email_resp.status_code != 200:
                                print(f"[{cfg.ts()}] [ERROR] 绑定邮箱首次发码失败 HTTP {add_email_resp.status_code}")
                                error_reason = "绑定邮箱发信被拒"
                                break

                            email_code = ""
                            val_resp = None

                            for resend_attempt in range(max(1, getattr(cfg, 'MAX_OTP_RETRIES', 3))):
                                if getattr(cfg, 'GLOBAL_STOP', False): break

                                if resend_attempt > 0:
                                    print(
                                        f"\n[{cfg.ts()}] [INFO] （{masked_login}）未收到绑定验证码或验证失败，准备请求重发 {resend_attempt}/{getattr(cfg, 'MAX_OTP_RETRIES', 3)}...")
                                    try:
                                        resend_hdrs = _oai_headers(log_did, {
                                            "Referer": "https://auth.openai.com/email-verification",
                                            "content-type": "application/json"
                                        })
                                        sentinel_resend = generate_payload(did=log_did, flow="authorize_continue",
                                                                           proxy=proxy, user_agent=current_ua,
                                                                           impersonate="chrome", ctx=log_ctx)
                                        if sentinel_resend:
                                            resend_hdrs["openai-sentinel-token"] = sentinel_resend

                                        _post_with_retry(
                                            s_log, "https://auth.openai.com/api/accounts/email-otp/resend",
                                            headers=resend_hdrs, json_body={}, proxies=proxies, timeout=15
                                        )
                                        time.sleep(2)
                                    except Exception as e:
                                        print(f"[{cfg.ts()}] [WARNING] （{masked_login}）绑定邮箱重发请求异常: {e}")

                                print(f"[{cfg.ts()}] [INFO] 正在轮询邮箱验证码...")
                                email_code = get_oai_code(email, jwt=email_jwt, proxies=proxies,
                                                          processed_mail_ids=processed_mails)

                                if not email_code:
                                    print(f"[{cfg.ts()}] [WARNING] {mask_email(email)}本轮未拉取到绑定邮箱验证码，准备重发...")
                                    continue

                                print(f"[{cfg.ts()}] [INFO] 获取到邮箱验证码 {email_code}，正在进行绑定验证...")
                                val_hdrs = _oai_headers(log_did, {
                                    "Referer": "https://auth.openai.com/email-verification",
                                    "content-type": "application/json"
                                })
                                sentinel_val_email = generate_payload(did=log_did, flow="authorize_continue",
                                                                      proxy=proxy, user_agent=current_ua,
                                                                      impersonate="chrome", ctx=log_ctx)
                                if sentinel_val_email:
                                    val_hdrs["openai-sentinel-token"] = sentinel_val_email

                                val_resp = _post_with_retry(
                                    s_log, "https://auth.openai.com/api/accounts/email-otp/validate",
                                    headers=val_hdrs, json_body={"code": email_code}, proxies=proxies
                                )

                                if val_resp.status_code == 200:
                                    print(f"[{cfg.ts()}] [SUCCESS] 邮箱绑定成功！继续流转提取 Token...")
                                    break
                                else:
                                    print(
                                        f"[{cfg.ts()}] [WARNING] 绑定邮箱验证码校验失败 HTTP {val_resp.status_code}，准备重发验证码...")
                                    email_code = ""
                                    continue

                            if not email_code or val_resp is None or val_resp.status_code != 200:
                                print(f"[{cfg.ts()}] [ERROR] 绑定邮箱重试次数达上限，放弃绑定。")
                                error_reason = "绑定邮箱失败/接码超时"
                                break

                            next_url = str(val_resp.json().get("continue_url") or "").strip()
                            _, current_url = _follow_redirect_chain_local(s_log, next_url, proxies)
                            continue
                        else:
                            break
                    if oauth_needs_retry:
                        try:
                            s_log.close()
                        except:
                            pass
                        continue
                    if run_ctx is not None: run_ctx['phone_verify'] = True
                    try:
                        url_code = url_code.get("error", {}).get("code")
                    except Exception as e:
                        pass
                    if "identity_provider_mismatch" in url_code:
                        url_code = "当前账号被阻断"
                    else:
                        url_code = "未开启接码开关，不接码可忽略该条提示"
                    if not error_reason:
                        error_reason = url_code
                    print(f"[{cfg.ts()}] [ERROR] （{masked_login}） OAuth 授权链路追踪失败！当前死在网页: {current_url}")
                    print(f"[{cfg.ts()}] [ERROR] （{masked_login}） 阻断原因: {error_reason}")
                    return None, None

            except Exception as e:
                print(f"[{cfg.ts()}] [ERROR] （{masked_login}） 注册主流程发生严重异常: {e}")
                if attempt < MAX_REG_RETRIES - 1:
                    print(f"[{cfg.ts()}] [INFO] 正在准备重试...")
                    time.sleep(2)
                    continue
                return None, None
        return None, None
    finally:
        if getattr(cfg, 'TEAM_MODE_ENABLE', False):
            try:
                time.sleep(random.uniform(0.1, 0.5))
                sys_node_release(saved_temp_at, sys_handle_a, sys_handle_b, sys_handle_c, proxies, original_email=email)
            except Exception:
                pass
        if s_reg is not None:
            try:
                s_reg.close()
            except Exception:
                pass
        if s_log is not None:
            try:
                s_log.close()
            except Exception:
                pass


def run_oauth_only(email: str, password: str, proxy: Optional[str], run_ctx: dict = None, access_token: str = "", device_id: str = "", user_agent: str = "") -> tuple:
    processed_mails: set = set()
    proxy = cfg.format_docker_url(proxy)
    if proxy and proxy.startswith("socks5://"):
        proxy = proxy.replace("socks5://", "socks5h://")
    proxies = {"http": proxy, "https": proxy} if proxy else None

    s_log = None

    saved_temp_at = access_token
    sys_handle_a = ""
    sys_handle_b = ""
    sys_handle_c = ""
    reg_ctx = {}
    email_jwt = ""

    try:
        s_init = requests.Session(proxies=proxies, impersonate="chrome")
        if device_id and user_agent:
            did = device_id
            current_ua = user_agent
        else:
            did, current_ua = init_auth(session=s_init, email=email, masked_email=mask_email(email), proxies=proxies,
                                        verify=_ssl_verify())
        if run_ctx is not None:
            run_ctx['device_id'] = did
            run_ctx['user_agent'] = current_ua
        if getattr(cfg, 'TEAM_MODE_ENABLE', False) and saved_temp_at:
            print(f"[{cfg.ts()}] [INFO] （{mask_email(email)}）即将进入团队静默流程")
            time.sleep(random.uniform(0.1, 0.5))
            is_alloc, sys_handle_a, sys_handle_b, sys_handle_c = sys_node_allocate(s_init, did, saved_temp_at, proxies)

        s_init.close()
    except Exception as e:
        print(f"[{cfg.ts()}] [ERROR] （{mask_email(email)}）提权初始化环境失败: {e}")
        return None, None

    is_openai_cpa = getattr(cfg, 'EMAIL_API_MODE', '')
    force_original_pwd = getattr(cfg, 'USE_ORIGINAL_PASSWORD_FLOW', False)
    if password == "Takeover_NoPassword":
        is_takeover = True
    else:
        if is_openai_cpa == "openai_cpa" and force_original_pwd:
            is_takeover = False
        else:
            is_takeover = True

    try:
        print(f"[{cfg.ts()}] [INFO] （{mask_email(email)}）执行静默获取 Token...")
        OAUTH_MAX_RETRIES = 2
        oauth_log = generate_oauth_url()
        for oauth_attempt in range(OAUTH_MAX_RETRIES):
            s_log = requests.Session(proxies=proxies, impersonate="chrome")
            s_log.headers.update({"Connection": "close"})
            s_log.cookies.clear()
            s_log.timeout = 30

            resp, current_url = _follow_redirect_chain_local(s_log, oauth_log.auth_url, proxies)
            if "code=" in current_url and "state=" in current_url:
                token_resp = submit_callback_url(
                    callback_url=current_url,
                    code_verifier=oauth_log.code_verifier,
                    redirect_uri=oauth_log.redirect_uri,
                    expected_state=oauth_log.state,
                    proxies=proxies,
                )
                return token_resp, password
            log_did = s_log.cookies.get("oai-did") or did

            log_ctx = reg_ctx.copy() if reg_ctx else {}
            log_ctx["session_id"] = str(uuid.uuid4())
            now_ms = int(time.time() * 1000)
            log_ctx["time_origin"] = float(now_ms - random.randint(20000, 300000))

            sentinel_log = generate_payload(did=log_did, flow="authorize_continue", proxy=proxy, user_agent=current_ua,
                                            impersonate="chrome", ctx=log_ctx)
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
                print(f"[{cfg.ts()}] [INFO] （{mask_email(email)}）无密码通道OAuth登录发信...")

                login_code_oauth = ""
                login_code_resp = None
                for login_code_attempt in range(max(1, cfg.MAX_OTP_RETRIES)):
                    if getattr(cfg, 'GLOBAL_STOP', False): return None, None
                    if login_code_attempt > 0:
                        print(
                            f"\n[{cfg.ts()}] [INFO] （{mask_email(email)}）无密码通道OAuth 阶段未收到验证码或验证失败，正在重试 {login_code_attempt}/{cfg.MAX_OTP_RETRIES}...")
                        try:
                            login_code_resend = generate_payload(did=log_did, flow="authorize_continue",
                                                                 proxy=proxy, user_agent=current_ua,
                                                                 impersonate="chrome", ctx=log_ctx)
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
                            print(f"[{cfg.ts()}] [WARNING] （{mask_email(email)}）无密码通道重新发送请求异常: {e}")

                    login_code_oauth = get_oai_code(email, jwt=email_jwt, proxies=proxies,
                                                    processed_mail_ids=processed_mails)
                    if not login_code_oauth:
                        print(f"[{cfg.ts()}] [WARNING] （{mask_email(email)}）无密码通道本轮未拉取到验证码，准备重发...")
                        continue

                    login_sentinel_otp = generate_payload(did=log_did, flow="authorize_continue", proxy=proxy,
                                                          user_agent=current_ua,
                                                          impersonate="chrome", ctx=log_ctx)
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
                        print(f"[{cfg.ts()}] [SUCCESS] （{mask_email(email)}）无密码通道OAuth阶段验证码通过！")
                        password = "Takeover_NoPassword"
                        break
                    else:
                        try:
                            err_json = login_code_resp.json()
                        except:
                            err_json = {}
                        print(
                            f"[{cfg.ts()}] [WARNING] （{mask_email(email)}）无密码通道OAuth 阶段验证失败: {login_code_resp.status_code}")
                        print(f"[{cfg.ts()}] [INFO] （{mask_email(email)}）无密码通道准备请求新的验证码并重试...")
                        login_code_oauth = ""
                        continue

                if not login_code_oauth and (login_code_resp is None or login_code_resp.status_code != 200):
                    print(
                        f"[{cfg.ts()}] [ERROR] 无密码通道重试次数达上限 ({cfg.MAX_OTP_RETRIES} 次)，丢弃当前 {mask_email(email)} 邮箱，放弃接管。")
                    return None, None

                login_code_url = str(login_code_resp.json().get("continue_url") or "").strip()
                if login_code_url.endswith("/about-you"):
                    _, create_account_resp = _create_account_about_you(
                        session=s_log, email=email, did=did, current_ua=current_ua,
                        proxy=proxy, proxies=proxies, ctx=log_ctx,
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

                sentinel_pwd_log = generate_payload(did=log_did, flow="password_verify", proxy=proxy,
                                                    user_agent=current_ua,
                                                    impersonate="chrome", ctx=log_ctx)
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
                    code2 = ""
                    code2_resp = None
                    for resend_attempt in range(max(1, cfg.MAX_OTP_RETRIES)):
                        if getattr(cfg, 'GLOBAL_STOP', False): return None, None
                        if resend_attempt > 0:
                            print(
                                f"\n[{cfg.ts()}] [INFO] （{mask_email(email)}）二次安全验证未收到验证码或校验失败，正在重试 {resend_attempt}/{cfg.MAX_OTP_RETRIES}...")
                            try:
                                sentinel_log_resend = generate_payload(did=log_did, flow="authorize_continue",
                                                                       proxy=proxy,
                                                                       user_agent=current_ua, impersonate="chrome",
                                                                       ctx=log_ctx)
                                log_resend_headers = _oai_headers(log_did, {
                                    "Referer": "https://auth.openai.com/email-verification",
                                    "content-type": "application/json"
                                })
                                if sentinel_log_resend:
                                    log_resend_headers["openai-sentinel-token"] = sentinel_log_resend
                                _post_with_retry(
                                    s_log,
                                    "https://auth.openai.com/api/accounts/email-otp/send",
                                    headers=log_resend_headers,
                                    json_body={}, proxies=proxies, timeout=15,
                                )
                                time.sleep(2)
                            except Exception as e:
                                print(f"[{cfg.ts()}] [WARNING] （{mask_email(email)}）重新发送请求异常: {e}")
                        code2 = get_oai_code(email, jwt=email_jwt, proxies=proxies,
                                             processed_mail_ids=processed_mails)

                        if not code2:
                            print(f"[{cfg.ts()}] [WARNING] （{mask_email(email)}）本轮未拉取到二次安全验证码，准备重发...")
                            continue

                        sentinel_otp2 = generate_payload(did=log_did, flow="authorize_continue", proxy=proxy,
                                                         user_agent=current_ua, impersonate="chrome", ctx=log_ctx)
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

                        if code2_resp.status_code == 200:
                            print(f"[{cfg.ts()}] [SUCCESS] （{mask_email(email)}）二次安全验证 OTP 校验通过！")
                            break
                        else:
                            print(
                                f"[{cfg.ts()}] [WARNING] （{mask_email(email)}）二次安全验证 OTP 校验失败: {code2_resp.status_code}")
                            print(f"[{cfg.ts()}] [INFO] （{mask_email(email)}）准备请求新的二次安全验证码并重试...")
                            code2 = ""
                            continue
                    if not code2 and (code2_resp is None or code2_resp.status_code != 200):
                        print(f"[{cfg.ts()}] [ERROR] （{mask_email(email)}）重试次数达上限，二次安全验证彻底失败，放弃接管。")
                        return None, None

                    next_url = str(code2_resp.json().get("continue_url") or "").strip()
                    resp, current_url = _follow_redirect_chain_local(s_log, next_url, proxies)

            url_code = ""
            error_reason = ""
            oauth_needs_retry = False
            while True:
                if "code=" in current_url:
                    token_resp = submit_callback_url(
                        callback_url=current_url,
                        code_verifier=oauth_log.code_verifier,
                        redirect_uri=oauth_log.redirect_uri,
                        expected_state=oauth_log.state,
                        proxies=proxies,
                    )
                    return token_resp, password
                elif current_url.endswith("/about-you"):
                    _, create_account_resp = _create_account_about_you(
                        session=s_log, email=email, did=did, current_ua=current_ua,
                        proxy=proxy, proxies=proxies, ctx=log_ctx,
                    )
                    url_code = create_account_resp.json()
                    current_url = str(create_account_resp.json().get("continue_url") or "").strip()
                    continue
                if current_url.endswith("/consent") or current_url.endswith("/workspace"):
                    auth_cookie2 = s_log.cookies.get("oai-client-auth-session") or ""
                    workspaces2 = _parse_workspace_from_auth_cookie(auth_cookie2)
                    if workspaces2:
                        print(f"[{cfg.ts()}] [SUCCESS] （{mask_email(email)}）检测到工作区，正在确认并流转...")
                        target_workspace_id2 = ""
                        if getattr(cfg, 'TEAM_MODE_ENABLE', False):
                            for ws in workspaces2:
                                ws_title = str(ws.get("title", ws.get("name", "Unknown")))
                                if "Personal" in ws_title or "个人" in ws_title or ws.get("is_personal"):
                                    target_workspace_id2 = str(ws.get("id", ""))
                                    break
                            if not target_workspace_id2:
                                target_workspace_id2 = str(workspaces2[-1].get("id", ""))
                        else:
                            target_workspace_id2 = str(workspaces2[0].get("id", ""))
                        if target_workspace_id2:
                            select_resp = _post_with_retry(
                                s_log, "https://auth.openai.com/api/accounts/workspace/select",
                                headers=_oai_headers(s_log.cookies.get("oai-did") or "",
                                                     {"Referer": current_url,
                                                      "content-type": "application/json"}),
                                json_body={"workspace_id": target_workspace_id2}, proxies=proxies,
                            )
                            final_url = _extract_next_url(
                                select_resp.json()) if select_resp.status_code == 200 else ""
                            _, final_loc = _follow_redirect_chain_local(s_log, final_url, proxies)
                            current_url = final_loc
                            continue
                    else:
                        break
                elif "/add-phone" in current_url or "/select-channel" in current_url:
                    # if oauth_attempt == 0 and getattr(cfg, 'TEAM_MODE_ENABLE', False):
                    if oauth_attempt == 0:
                        print(
                            f"[{cfg.ts()}] [WARNING] （{mask_email(email)}） OAuth重试中...")
                        time.sleep(random.uniform(2.0, 4.0))
                        oauth_needs_retry = True
                        break
                    print(f"[{cfg.ts()}] [INFO] （{mask_email(email)}） OAuth链路触发风控，进入手机号验证...")
                    if getattr(cfg, 'SMSBOWER_ENABLED', False):
                        provider_name = "SmsBower"
                        ok, next_url_or_reason = handle_smsbower_verification(
                            session=s_log, proxies=proxies, hint_url=current_url, device_id=did, user_agent=current_ua,
                            run_ctx=reg_ctx, proxy=proxy
                        )
                    elif getattr(cfg, 'HERO_SMS_ENABLED', False):
                        provider_name = "HeroSMS"
                        ok, next_url_or_reason = _try_verify_phone_via_hero_sms(
                            session=s_log, proxies=proxies, hint_url=current_url, device_id=did, user_agent=current_ua,
                            run_ctx=reg_ctx, proxy=proxy
                        )
                    elif getattr(cfg, 'FIVESIM_ENABLED', False):
                        provider_name = "5SIM"
                        ok, next_url_or_reason = try_verify_phone_via_fivesim(
                            session=s_log, proxies=proxies, hint_url=current_url, device_id=did, user_agent=current_ua,
                            run_ctx=reg_ctx, proxy=proxy
                        )
                    else:
                        break
                    if ok and next_url_or_reason:
                        print(f"[{cfg.ts()}] [INFO] （{mask_email(email)}） {provider_name} 手机验证成功，继续获取凭证")
                        current_url = next_url_or_reason
                        continue
                    else:
                        print(f"[{cfg.ts()}] [ERROR] （{mask_email(email)}） {provider_name}验证失败: {next_url_or_reason}")
                        error_reason = next_url_or_reason
                        break
                else:
                    break
            if oauth_needs_retry:
                try:
                    s_log.close()
                except:
                    pass
                continue
            if run_ctx is not None: run_ctx['phone_verify'] = True
            try:
                url_code = url_code.get("error", {}).get("code")
            except Exception as e:
                pass
            if "identity_provider_mismatch" in url_code:
                url_code = "当前账号被阻断"
            else:
                url_code = "未开启接码开关，不接码可忽略该条提示"
            if not error_reason:
                error_reason = url_code
            print(f"[{cfg.ts()}] [ERROR] （{mask_email(email)}） OAuth 授权链路追踪失败！当前死在网页: {current_url}")
            print(f"[{cfg.ts()}] [ERROR] （{mask_email(email)}） 阻断原因: {error_reason}")
            return None, None

    except Exception as e:
        print(f"[{cfg.ts()}] [ERROR] 提权异常: {e}")
        return None, None
    finally:
        if getattr(cfg, 'TEAM_MODE_ENABLE', False):
            try:
                time.sleep(random.uniform(0.1, 0.5))
                sys_node_release(saved_temp_at, sys_handle_a, sys_handle_b, sys_handle_c, proxies, original_email=email)
            except Exception:
                pass
        if s_log is not None:
            try:
                s_log.close()
            except:
                pass

    return None, None