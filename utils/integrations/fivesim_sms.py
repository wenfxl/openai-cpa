import time
import threading
from typing import Any, Dict, Optional, Tuple
from curl_cffi import requests
from utils import db_manager
from utils import config as cfg
from utils.integrations.smsbower_sms import UserStoppedError, _sleep_interruptible, _post_with_retry, _extract_next_url
from utils.auth_core import generate_payload

def _ssl_verify() -> bool: return True


def _info(msg): print(f"[{cfg.ts()}] [5SIM] {msg}")


def _warn(msg): print(f"[{cfg.ts()}] [5SIM] {msg}")


def _raise_if_stopped() -> None:
    if getattr(cfg, 'GLOBAL_STOP', False): raise UserStoppedError("stopped")

def _fivesim_enabled() -> bool: return bool(getattr(cfg, 'FIVESIM_ENABLED', False)) and bool(_fivesim_api_key())


def _fivesim_api_key() -> str: return str(getattr(cfg, 'FIVESIM_API_KEY', '')).strip()


def _fivesim_min_balance() -> float: return float(getattr(cfg, 'FIVESIM_MIN_BALANCE', 0.0))


def _fivesim_max_price() -> float: return float(getattr(cfg, 'FIVESIM_MAX_PRICE', 0.0))


def _fivesim_min_price() -> float: return float(getattr(cfg, 'FIVESIM_MIN_PRICE', 0.0))


def _fivesim_max_tries() -> int: return int(getattr(cfg, 'FIVESIM_MAX_TRIES', 3))


def _fivesim_poll_timeout() -> int: return int(getattr(cfg, 'FIVESIM_POLL_TIMEOUT_SEC', 180))


def _fivesim_auto_pick() -> bool: return bool(getattr(cfg, 'FIVESIM_AUTO_PICK_COUNTRY', True))


def _fivesim_reuse_enabled() -> bool: return bool(getattr(cfg, 'FIVESIM_REUSE_PHONE', True))


_FIVESIM_PRICE_CACHE_LOCK = threading.Lock()
_FIVESIM_PRICE_CACHE: dict[str, Any] = {"service": "", "updated_at": 0.0, "items": []}
_FIVESIM_VERIFY_LOCK = threading.Lock()


_FIVESIM_REUSE_LOCK = threading.Lock()
_FIVESIM_COUNTRY_ZH = {
    "afghanistan": "阿富汗", "albania": "阿尔巴尼亚", "algeria": "阿尔及利亚", "angola": "安哥拉",
    "antiguaandbarbuda": "安提瓜和巴布达", "argentina": "阿根廷", "armenia": "亚美尼亚", "aruba": "阿鲁巴",
    "australia": "澳大利亚", "austria": "奥地利", "azerbaijan": "阿塞拜疆", "bahamas": "巴哈马",
    "bahrain": "巴林", "bangladesh": "孟加拉国", "barbados": "巴巴多斯", "belgium": "比利时",
    "belize": "伯利兹", "benin": "贝宁", "bhutane": "不丹", "bih": "波斯尼亚和黑塞哥维那",
    "bolivia": "玻利维亚", "botswana": "博茨瓦纳", "brazil": "巴西", "bulgaria": "保加利亚",
    "burkinafaso": "布基纳法索", "burundi": "布隆迪", "cambodia": "柬埔寨", "cameroon": "喀麦隆",
    "canada": "加拿大", "capeverde": "佛得角", "chad": "乍得", "chile": "智利", "colombia": "哥伦比亚",
    "comoros": "科摩罗", "congo": "刚果", "costarica": "哥斯达黎加", "croatia": "克罗地亚",
    "cyprus": "塞浦路斯", "czech": "捷克", "denmark": "丹麦", "djibouti": "吉布提", "dominicana": "多米尼加",
    "easttimor": "东帝汶", "ecuador": "厄瓜多尔", "egypt": "埃及", "england": "英国",
    "equatorialguinea": "赤道几内亚", "estonia": "爱沙尼亚", "ethiopia": "埃塞俄比亚", "finland": "芬兰",
    "france": "法国", "frenchguiana": "法属圭亚那", "gabon": "加蓬", "gambia": "冈比亚",
    "georgia": "格鲁吉亚", "germany": "德国", "ghana": "加纳", "greece": "希腊", "guadeloupe": "瓜德罗普",
    "guatemala": "危地马拉", "guinea": "几内亚", "guineabissau": "几内亚比绍", "guyana": "圭亚那",
    "haiti": "海地", "honduras": "洪都拉斯", "hongkong": "中国香港", "hungary": "匈牙利", "india": "印度",
    "indonesia": "印度尼西亚", "ireland": "爱尔兰", "israel": "以色列", "italy": "意大利",
    "ivorycoast": "科特迪瓦", "jamaica": "牙买加", "jordan": "约旦", "kazakhstan": "哈萨克斯坦",
    "kenya": "肯尼亚", "kuwait": "科威特", "kyrgyzstan": "吉尔吉斯斯坦", "laos": "老挝", "latvia": "拉脱维亚",
    "lesotho": "莱索托", "liberia": "利比里亚", "lithuania": "立陶宛", "luxembourg": "卢森堡",
    "macau": "中国澳门", "madagascar": "马达加斯加", "malawi": "马拉维", "malaysia": "马来西亚",
    "maldives": "马尔代夫", "mauritania": "毛里塔尼亚", "mauritius": "毛里求斯", "mexico": "墨西哥",
    "moldova": "摩尔达维亚", "mongolia": "蒙古", "montenegro": "黑山", "morocco": "摩洛哥",
    "mozambique": "莫桑比克", "namibia": "纳米比亚", "nepal": "尼泊尔", "netherlands": "荷兰",
    "newcaledonia": "新喀里多尼亚", "nicaragua": "尼加拉瓜", "nigeria": "尼日利亚",
    "northmacedonia": "北马其顿", "norway": "挪威", "oman": "阿曼", "pakistan": "巴基斯坦",
    "panama": "巴拿马", "papuanewguinea": "巴布亚新几内亚", "paraguay": "巴拉圭", "peru": "秘鲁",
    "philippines": "菲律宾", "poland": "波兰", "portugal": "葡萄牙", "puertorico": "波多黎各",
    "reunion": "留尼汪岛", "romania": "罗马尼亚", "russia": "俄罗斯", "rwanda": "卢旺达",
    "saintkittsandnevis": "圣基茨和尼维斯", "saintlucia": "圣卢西亚",
    "saintvincentandgrenadines": "圣文森特和格林纳丁斯", "salvador": "萨尔瓦多", "samoa": "萨摩亚",
    "saudiarabia": "沙特阿拉伯", "senegal": "塞内加尔", "serbia": "塞尔维亚", "seychelles": "塞舌尔",
    "sierraleone": "塞拉利昂", "slovakia": "斯洛伐克", "slovenia": "斯洛文尼亚", "solomonislands": "所罗门群岛",
    "southafrica": "南非", "spain": "西班牙", "srilanka": "斯里兰卡", "suriname": "苏里南",
    "swaziland": "斯威士兰", "sweden": "瑞典", "taiwan": "中国台湾", "tajikistan": "塔吉克斯坦",
    "tanzania": "坦桑尼亚", "thailand": "泰国", "tit": "特立尼达和多巴哥", "togo": "多哥",
    "tunisia": "突尼斯", "turkmenistan": "土库曼斯坦", "uganda": "乌干达", "ukraine": "乌克兰",
    "uruguay": "乌拉圭", "usa": "美国", "uzbekistan": "乌兹别克斯坦", "venezuela": "委内瑞拉",
    "vietnam": "越南", "zambia": "赞比亚"
}

_FIVESIM_REUSE_STATE: dict[str, Any] = {"order_id": "", "phone": "", "service": "", "country": "", "uses": 0, "updated_at": 0.0}


def _load_fivesim_reuse_state():
    saved = db_manager.get_sys_kv("fivesim_reuse_data")
    if saved and isinstance(saved, dict):
        with _FIVESIM_REUSE_LOCK: _FIVESIM_REUSE_STATE.update(saved)


_load_fivesim_reuse_state()


def _sync_fivesim_reuse():
    db_manager.set_sys_kv("fivesim_reuse_data", _FIVESIM_REUSE_STATE)


def _fivesim_reuse_get(service: str, country: str) -> tuple[str, str, int]:
    now = time.time()
    max_uses = int(getattr(cfg, 'FIVESIM_REUSE_MAX', 2))
    with _FIVESIM_REUSE_LOCK:
        order_id = str(_FIVESIM_REUSE_STATE.get("order_id") or "").strip()
        phone = str(_FIVESIM_REUSE_STATE.get("phone") or "").strip()
        state_svc = str(_FIVESIM_REUSE_STATE.get("service") or "").strip()
        state_country = str(_FIVESIM_REUSE_STATE.get("country") or "").strip()
        uses = int(_FIVESIM_REUSE_STATE.get("uses") or 0)
        updated = float(_FIVESIM_REUSE_STATE.get("updated_at") or 0.0)

        if order_id and phone and state_svc == service and state_country == country and uses < max_uses and (now - updated) < 280:
            return order_id, phone, uses
    return "", "", 0


def _fivesim_reuse_set(order_id: str, phone: str, service: str, country: str):
    if not phone or not order_id: return
    with _FIVESIM_REUSE_LOCK:
        _FIVESIM_REUSE_STATE.update(
            {"order_id": order_id, "phone": phone, "service": service, "country": country, "uses": 0, "updated_at": time.time()})
    _sync_fivesim_reuse()


def _fivesim_reuse_touch(increase: bool = False):
    with _FIVESIM_REUSE_LOCK:
        if increase: _FIVESIM_REUSE_STATE["uses"] = int(_FIVESIM_REUSE_STATE.get("uses") or 0) + 1
        _FIVESIM_REUSE_STATE["updated_at"] = time.time()
    _sync_fivesim_reuse()


def _fivesim_reuse_clear():
    with _FIVESIM_REUSE_LOCK:
        _FIVESIM_REUSE_STATE.update({"order_id": "", "phone": "", "service": "", "country": "", "uses": 0, "updated_at": 0.0})
    _sync_fivesim_reuse()

def _fivesim_request(method: str, endpoint: str, proxies: Any, params: dict = None, json_data: dict = None) -> tuple[
    bool, str, Any]:
    key = _fivesim_api_key()
    if not key: return False, "NO_KEY", None

    url = f"https://5sim.net/v1/{endpoint}"
    headers = {
        "Authorization": f"Bearer {key}",
        "Accept": "application/json"
    }
    try:
        if method.upper() == "GET":
            resp = requests.get(url, headers=headers, params=params, proxies=proxies, verify=_ssl_verify(), timeout=20,
                                impersonate="chrome110")
        else:
            resp = requests.post(url, headers=headers, json=json_data, proxies=proxies, verify=_ssl_verify(),
                                 timeout=20, impersonate="chrome110")
    except Exception as e:
        return False, f"REQUEST_ERROR: {e}", None

    code = resp.status_code
    try:
        data = resp.json()
    except:
        data = None

    if 200 <= code < 300:
        return True, resp.text, data
    error_msg = resp.text.strip() if resp.text else f"HTTP {code}"
    if isinstance(data, dict) and "message" in data:
        error_msg = str(data["message"])
    return False, error_msg, data

def fivesim_get_balance(proxies: Any = None) -> tuple[float, str]:
    _info("正在查询 5SIM 账户余额...")
    ok, text, data = _fivesim_request("GET", "user/profile", proxies)
    if ok and data and "balance" in data:
        bal = float(data["balance"])
        _info(f"✅ 5SIM 当前余额: {bal:.2f} $")
        return bal, ""
    _warn(f"5SIM 余额查询失败: {text}")
    return -1.0, text


def _fivesim_prices_by_service(service_code: str, proxies: Any, force_refresh: bool = False) -> list[dict]:
    svc = str(service_code or "openai").strip().lower()
    now = time.time()
    with _FIVESIM_PRICE_CACHE_LOCK:
        if not force_refresh and _FIVESIM_PRICE_CACHE.get("service") == svc and (
                now - _FIVESIM_PRICE_CACHE.get("updated_at", 0)) <= 90:
            return list(_FIVESIM_PRICE_CACHE.get("items", []))

    ok, text, data = _fivesim_request("GET", f"guest/prices?product={svc}", proxies)
    country_groups = {}

    if ok and isinstance(data, dict) and svc in data:
        for country_name, operators in data[svc].items():
            zh_name = _FIVESIM_COUNTRY_ZH.get(country_name.lower(), str(country_name).capitalize())

            routes = []
            min_cost = 999999.0
            total_count = 0
            for operator_name, op_data in operators.items():
                count = int(op_data.get("count", 0))
                cost = float(op_data.get("cost", 999))
                if count > 0 and operator_name.lower() != "any":
                    routes.append({
                        "provider": operator_name,
                        "cost": cost,
                        "count": count
                    })
                    min_cost = min(min_cost, cost)
                    total_count += count
            if routes:
                country_groups[country_name] = {
                    "country": country_name,
                    "name": zh_name,
                    "total_count": total_count,
                    "min_cost": min_cost,
                    "routes": routes
                }

    rows = list(country_groups.values())
    if rows:
        for r in rows:
            r["routes"].sort(key=lambda x: (x["cost"], -x["count"]))
        rows.sort(key=lambda x: (x.get("min_cost", 999), -x["total_count"]))

        with _FIVESIM_PRICE_CACHE_LOCK:
            _FIVESIM_PRICE_CACHE.update({"service": svc, "updated_at": now, "items": rows})

    return rows


def _fivesim_pick_country(proxies: Any, service_code: str, pref_country: str, excluded: set) -> str:
    if not _fivesim_auto_pick(): return pref_country
    rows = _fivesim_prices_by_service(service_code, proxies)
    limit_max = _fivesim_max_price()
    limit_min = _fivesim_min_price()

    valid_options = []
    for r in rows:
        cname = r["country"]
        count = r.get("total_count", 0)
        if cname in excluded or count <= 0: continue
        cost = r.get("min_cost", 999.0)
        if limit_max > 0 and cost > limit_max: continue
        if limit_min > 0 and cost < limit_min: continue

        score = -cost * 100 + min(count, 10000) / 100.0
        if cname == pref_country: score += 50
        valid_options.append((score, cname))

    if not valid_options: return pref_country
    valid_options.sort(key=lambda x: -x[0])
    return valid_options[0][1]


def _fivesim_get_number(proxies: Any, service: str, country: str, enable_reuse: bool = False) -> tuple[
    str, str, str, str]:
    limit_max = _fivesim_max_price()
    limit_min = _fivesim_min_price()
    operator = str(getattr(cfg, 'FIVESIM_OPERATOR', '')).strip()
    if limit_max > 0 or limit_min > 0:
        rows = _fivesim_prices_by_service(service, proxies)
        actual_cost = -1.0
        for r in rows:
            if r.get("country") == country:
                routes = r.get("routes", [])
                for route in routes:
                    if not operator or route.get("provider") == operator:
                        actual_cost = float(route.get("cost", -1.0))
                        if not operator:
                            operator = route.get("provider")
                        break
                break

        if actual_cost > 0:
            if limit_max > 0 and actual_cost > limit_max:
                return "", "", f"价格拦截: 当前线路价格 ({actual_cost}$) 高于最高限价 ({limit_max}$)", ""
            if limit_min > 0 and actual_cost < limit_min:
                return "", "", f"价格拦截: 当前线路价格 ({actual_cost}$) 低于最低限价 ({limit_min}$)", ""

    c = country or "any"
    op = operator if operator else "any"
    endpoint = f"user/buy/activation/{c}/{op}/{service}"

    params = {}
    if limit_max > 0: params["maxPrice"] = limit_max
    if enable_reuse: params["reuse"] = "1"

    ok, text, data = _fivesim_request("GET", endpoint, proxies, params=params)
    if ok and data and "id" in data:
        return str(data["id"]), str(data["phone"]), "", str(data.get("price", "未知"))
    return "", "", text or "取号失败", ""


def _fivesim_set_status(action: str, order_id: str, proxies: Any):
    if not order_id: return
    _fivesim_request("GET", f"user/{action}/{order_id}", proxies)

def _fivesim_poll_code(order_id: str, proxies: Any, expected_sms_index: int = 0) -> str:
    timeout_sec = _fivesim_poll_timeout()
    started = time.time()
    _info(f"⏳ 正在等待 5SIM 验证码 (第 {expected_sms_index + 1} 条, 最大等待 {timeout_sec} 秒)...")
    last_print = time.time()

    while time.time() - started < timeout_sec:
        _raise_if_stopped()
        ok, text, data = _fivesim_request("GET", f"user/check/{order_id}", proxies)
        if time.time() - last_print > 8:
            status = data.get('status', 'WAITING') if data else 'UNKNOWN'
            _info(f"🔄 仍在等待短信中... (当前状态: {status})")
            last_print = time.time()

        if ok and data and data.get("status") in ["RECEIVED", "PENDING"]:
            sms_list = data.get("sms", [])
            if sms_list and len(sms_list) > expected_sms_index:
                code = str(sms_list[expected_sms_index].get("code", ""))
                if code:
                    _info(f"🎉 成功接收到第 {expected_sms_index + 1} 条短信验证码: {code}")
                    return code

        elif ok and data and data.get("status") in ["CANCELED", "BANNED", "TIMEOUT"]:
            _warn(f"❌ 接码被平台取消: {data.get('status')}")
            return ""

        _sleep_interruptible(3.0)

    _warn("⚠️ 5SIM 接码彻底超时！")
    return ""


def try_verify_phone_via_fivesim(session: requests.Session, *, proxies: Any, hint_url: str = "", device_id: str = "",
                                 user_agent: str = "", run_ctx: dict = None, proxy: Optional[str] = None) -> tuple[
    bool, str]:
    if not _fivesim_enabled(): return False, "5SIM 未配置或未开启"
    max_tries = _fivesim_max_tries()
    started = time.time()
    lock_acquired = False

    while True:
        _raise_if_stopped()
        if _FIVESIM_VERIFY_LOCK.acquire(timeout=0.5):
            lock_acquired = True
            break
        if time.time() - started >= 180: return False, "5SIM 排队超时"

    def _verify_once(aid: str, phone_number: str, source: str, current_use_index: int) -> tuple[bool, str, str]:
        try:
            _info(f"[{source}] 正在向 OpenAI 提交号码 {phone_number}...")
            hdrs = {"referer": "https://auth.openai.com/add-phone", "accept": "application/json",
                    "content-type": "application/json"}

            send_sentinel = generate_payload(
                did=device_id, flow="authorize_continue", proxy=proxy,
                user_agent=user_agent, impersonate="chrome110", ctx=run_ctx
            )
            if send_sentinel: hdrs["openai-sentinel-token"] = send_sentinel

            send_resp = _post_with_retry(session, "https://auth.openai.com/api/accounts/add-phone/send", headers=hdrs,
                                         json_body={"phone_number": phone_number}, proxies=proxies)
            if send_resp.status_code != 200:
                try:
                    err_msg = send_resp.json()
                except:
                    err_msg = send_resp.text
                fail_reason = f"号码被拦截 HTTP {send_resp.status_code} {err_msg}"
                _warn(f"[{source}] ❌ {fail_reason}")
                return False, "", fail_reason

            _info(f"[{source}] ✅ OpenAI 接受了号码，开始轮询验证码...")
            sms_code = _fivesim_poll_code(aid, proxies, expected_sms_index=current_use_index)
            if not sms_code:
                return False, "", "接码超时"

            v_hdrs = {"referer": "https://auth.openai.com/phone-verification", "accept": "application/json",
                      "content-type": "application/json"}

            sentinel = generate_payload(
                did=device_id, flow="authorize_continue", proxy=proxy,
                user_agent=user_agent, impersonate="chrome110", ctx=run_ctx
            )
            if sentinel: v_hdrs["openai-sentinel-token"] = sentinel
            v_resp = _post_with_retry(session, "https://auth.openai.com/api/accounts/phone-otp/validate",
                                      headers=v_hdrs, json_body={"code": sms_code}, proxies=proxies)

            if v_resp.status_code == 200:
                _info(f"[{source}] 🎊 验证码核验通过！")
                vj = v_resp.json()
                next_url = _extract_next_url(vj) or hint_url
                return True, next_url, ""
            else:
                _warn(f"[{source}] ❌ 验证码校验失败 HTTP {v_resp.status_code}")
                return False, "", f"校验失败 HTTP {v_resp.status_code}"
        except UserStoppedError:
            raise
        except Exception as e:
            return False, "", f"验证异常: {e}"

    try:
        service_code = str(getattr(cfg, 'FIVESIM_SERVICE', 'openai')).strip()
        if not service_code:
            service_code = "openai"
        pref_country = str(getattr(cfg, 'FIVESIM_COUNTRY', 'any')).strip()
        excluded = set()
        last_reason = "验证失败"
        reuse_on = _fivesim_reuse_enabled()
        max_uses = int(getattr(cfg, 'FIVESIM_REUSE_MAX', 2))

        if reuse_on:
            raid, rphone, rused = _fivesim_reuse_get(service_code, pref_country if not _fivesim_auto_pick() else "")
            if raid and rphone:
                _info(f"♻️ 触发【同单多码】复用: {rphone} (订单: {raid}, 已用 {rused} 次)")

                ok_r, next_r, reason_r = _verify_once(raid, rphone, source="同单复用", current_use_index=rused)

                if ok_r:
                    _fivesim_reuse_touch(increase=True)
                    if (rused + 1) >= max_uses:
                        _info(f"🛑 该号码已达到最大复用次数 ({max_uses})，完成订单。")
                        _fivesim_set_status("finish", raid, proxies)
                        _fivesim_reuse_clear()
                    return True, next_r

                _warn(f"⚠️ 复用中途失败: {reason_r}，放弃该号码。")
                _fivesim_set_status("ban", raid, proxies)
                _fivesim_reuse_clear()

        for attempt in range(1, max_tries + 1):
            _raise_if_stopped()
            country = _fivesim_pick_country(proxies, service_code, pref_country, excluded)
            _info(f"[{attempt}/{max_tries}] 正在向 5SIM 请求新号码 (国家: {country})...")

            aid, phone, gerr, cost = _fivesim_get_number(proxies, service_code, country, enable_reuse=False)
            if not aid:
                _warn(f"⚠️ 第 {attempt} 次取号失败: {gerr}")
                last_reason = gerr
                if attempt < max_tries and _fivesim_auto_pick() and "no free phones" in str(gerr).lower():
                    excluded.add(country)
                    _info("🔄 自动切换备选国家...")
                _sleep_interruptible(2.0)
                continue

            _info(f"📱 成功取到新号码: {phone} (订单: {aid} | 扣费: {cost} $)")

            ok_n, next_n, reason_n = _verify_once(aid, phone, source=f"新号#{attempt}", current_use_index=0)
            if ok_n:
                if reuse_on and max_uses > 1:
                    _info(f"📥 号码已存入复用池，等待 5 分钟内下一次接码。")
                    _fivesim_reuse_set(aid, phone, service_code, country)
                    _fivesim_reuse_touch(increase=True)
                else:
                    _fivesim_set_status("finish", aid, proxies)
                return True, next_n

            last_reason = reason_n
            _fivesim_set_status("ban", aid, proxies)
            _sleep_interruptible(1.5)

        return False, last_reason
    finally:
        if lock_acquired: _FIVESIM_VERIFY_LOCK.release()