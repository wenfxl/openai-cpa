import urllib.parse
import random
import time
import requests as std_requests
from datetime import datetime
import yaml
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from utils.clash_group_utils import resolve_group_name

CLASH_API_URL = ""
LOCAL_PROXY_URL = ""
ENABLE_NODE_SWITCH = False
POOL_MODE = False
FASTEST_MODE = False
PROXY_GROUP_NAME = "节点选择"
CLASH_SECRET = ""
NODE_BLACKLIST = []
TESTED_NODES_MAP = {}
_IS_IN_DOCKER = os.path.exists('/.dockerenv')
_global_switch_lock = threading.Lock()
_last_switch_time = 0
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(CURRENT_DIR)

def format_docker_url(url: str) -> str:
    """智能检测：如果在 Docker 中运行，自动把 127.0.0.1 转为宿主机魔法地址"""
    if not url or not isinstance(url, str):
        return url
    if _IS_IN_DOCKER:
        if "127.0.0.1" in url:
            return url.replace("127.0.0.1", "host.docker.internal")
        if "localhost" in url:
            return url.replace("localhost", "host.docker.internal")
    return url

def reload_proxy_config():
    global CLASH_API_URL, LOCAL_PROXY_URL, ENABLE_NODE_SWITCH, POOL_MODE, \
           FASTEST_MODE, PROXY_GROUP_NAME, CLASH_SECRET, NODE_BLACKLIST, TESTED_NODES_MAP
    config_dir = os.path.join(BASE_DIR, "data")
    config_path = os.path.join(config_dir, "config.yaml")
    if not os.path.exists(config_path):
        print(f"[{ts()}] [WARNING] 配置文件 {config_path} 不存在，使用默认代理设置。")
        conf_data = {}
    else:
        with open(config_path, "r", encoding="utf-8") as f:
            conf_data = yaml.safe_load(f) or {}

    clash_conf = conf_data.get("clash_proxy_pool", {})
    ENABLE_NODE_SWITCH = clash_conf.get("enable", False)
    POOL_MODE = clash_conf.get("pool_mode", False)
    FASTEST_MODE = clash_conf.get("fastest_mode", False)
    CLASH_API_URL = format_docker_url(clash_conf.get("api_url", "http://127.0.0.1:9090"))
    LOCAL_PROXY_URL = format_docker_url(clash_conf.get("test_proxy_url", "http://127.0.0.1:7890"))
    
    PROXY_GROUP_NAME = clash_conf.get("group_name", "节点选择")
    CLASH_SECRET = clash_conf.get("secret", "")
    NODE_BLACKLIST = clash_conf.get("blacklist", ["港", "HK", "台", "TW", "中国", "CN"])
    TESTED_NODES_MAP = clash_conf.get("tested_nodes", {}) if isinstance(clash_conf.get("tested_nodes", {}), dict) else {}
   
    print(f"[{ts()}] [系统] 代理管理模块配置已同步更新。")

def ts() -> str:
    """获取当前时间戳字符串，用于日志"""
    return datetime.now().strftime("%H:%M:%S")

def clean_for_log(text: str) -> str:
    """用于日志输出：过滤掉字符串中的国旗、飞机、火箭等 Emoji 符号"""
    emoji_pattern = re.compile(
        r'[\U0001F1E6-\U0001F1FF]'
        r'|[\U0001F300-\U0001F6FF]'
        r'|[\U0001F900-\U0001F9FF]'
        r'|[\U00002600-\U000027BF]'
        r'|[\uFE0F]'
    )
    return emoji_pattern.sub('', text).strip()

def get_display_name(proxy_url: str) -> str:
    """统一日志脱敏：将 URL 转换为 [X号机] 或隐藏域名"""
    if not proxy_url:
        return "全局单机"
    try:
        parsed = urllib.parse.urlparse(proxy_url)
        if parsed.port and 41000 < parsed.port <= 41050:
            return f"{parsed.port - 41000}号机"
        return f"端口:{parsed.port}"
    except:
        return "未知通道"

def get_api_url_for_proxy(proxy_url: str) -> str:
    """根据开关决定是独立容器 API，还是使用固定 API"""
    if not POOL_MODE or not proxy_url:
        return CLASH_API_URL
    try:
        parsed = urllib.parse.urlparse(proxy_url)
        port = parsed.port
        if port and 41000 < port <= 41050:
            api_port = port + 1000
            return format_docker_url(f"http://{parsed.hostname}:{api_port}")
    except Exception:
        pass
    return CLASH_API_URL

def test_proxy_liveness(proxy_url=None):
    """测试当前代理是否可用 (脱敏)"""
    raw_url = proxy_url if proxy_url else LOCAL_PROXY_URL
    target_proxy = format_docker_url(raw_url)
    proxies = {"http": target_proxy, "https": target_proxy}
    display_name = get_display_name(proxy_url if proxy_url else LOCAL_PROXY_URL)
    
    try:
        res = std_requests.get("https://cloudflare.com/cdn-cgi/trace", proxies=proxies, timeout=5)
        if res.status_code == 200:
            loc = "UNKNOWN"
            for line in res.text.split('\n'):
                if line.startswith("loc="):
                    loc = line.split("=")[1].strip()

            blocked_regions = ["CN", "HK"]
            if loc in blocked_regions:
                print(f"[{ts()}] [代理测活] {display_name} 地区受限 ({loc})，弃用！")
                return False
                
            print(f"[{ts()}] [代理测活] {display_name} 成功！地区 ({loc})，延迟: {res.elapsed.total_seconds():.2f}s")
            return True
        return False
    except Exception:
        print(f"[{ts()}] [代理测活] {display_name} 链路中断或超时。")
        return False


def smart_switch_node(proxy_url=None):
    global _last_switch_time
    if not ENABLE_NODE_SWITCH:
        return True

    # 如果是独立代理池模式，互相不影响，不需要锁
    if POOL_MODE and proxy_url:
        return _do_smart_switch(proxy_url)

    with _global_switch_lock:
        if time.time() - _last_switch_time < 10:
            print(f"[{ts()}] [代理池] 其他线程刚完成切换，跳过本次请求...")
            return True

        success = _do_smart_switch(proxy_url)
        if success:
            _last_switch_time = time.time()
        return success

def _do_smart_switch(proxy_url=None):
    """智能切换节点并测活的核心逻辑 (脱敏)"""
    if not ENABLE_NODE_SWITCH:
        return True
        
    current_api_url = get_api_url_for_proxy(proxy_url)
    headers = {"Authorization": f"Bearer {CLASH_SECRET}"} if CLASH_SECRET else {}
    
    display_name = get_display_name(proxy_url)

    api_display = get_display_name(current_api_url).replace("号机", "号API")
    
    try:
        resp = std_requests.get(f"{current_api_url}/proxies", headers=headers, timeout=5)
        if resp.status_code != 200:
            print(f"[{ts()}] [ERROR] 无法连接 Clash API ({api_display})，请检查容器状态。")
            return False
            
        proxies_data = resp.json().get('proxies', {})

        actual_group_name = resolve_group_name(proxies_data, PROXY_GROUP_NAME)
                
        if not actual_group_name:
            available_groups = [
                key for key, value in proxies_data.items()
                if isinstance(value, dict) and 'all' in value
            ]
            print(
                f"[{ts()}] [ERROR] {display_name} 找不到策略组关键词 '{PROXY_GROUP_NAME}'。"
                f" 当前可用策略组: {', '.join(clean_for_log(g) for g in available_groups[:8])}"
            )
            return False
            
        safe_group_name = urllib.parse.quote(actual_group_name)
        all_nodes = proxies_data[actual_group_name].get('all', [])
        
        valid_nodes = [
            n for n in all_nodes 
            if not any(kw.upper() in n.upper() for kw in NODE_BLACKLIST)
        ]

        tested_candidates = TESTED_NODES_MAP.get(actual_group_name, [])
        if isinstance(tested_candidates, list):
            tested_candidates = [n for n in tested_candidates if n in valid_nodes]
            if tested_candidates:
                valid_nodes = tested_candidates
                print(f"[{ts()}] [代理池] {display_name} 已锁定到测速通过节点池，共 {len(valid_nodes)} 个。")
        
        if not valid_nodes:
            print(f"[{ts()}] [ERROR] {display_name} 过滤后无可用节点，请检查黑名单。")
            return False

        nodes_with_delay = []
        try:
            for node_name in valid_nodes:
                history = proxies_data.get(node_name, {}).get("history", [])
                if not history:
                    continue
                delay = history[-1].get("delay", 0)
                if isinstance(delay, (int, float)) and delay > 0:
                    nodes_with_delay.append((node_name, float(delay)))
        except Exception:
            nodes_with_delay = []

        if FASTEST_MODE:
            print(f"\n[{ts()}] [代理池] {display_name} 开启优选模式，并发测速 {len(valid_nodes)} 个节点...")
            
            session = std_requests.Session()
            
            def trigger_delay(n):
                enc_n = urllib.parse.quote(n, safe="")
                try:
                    session.get(
                        f"{current_api_url}/proxies/{enc_n}/delay?timeout=2000&url=http://www.gstatic.com/generate_204", 
                        headers=headers, timeout=2.5
                    )
                except:
                    pass

            thread_count = min(10, len(valid_nodes))
            with ThreadPoolExecutor(max_workers=thread_count) as executor:
                executor.map(trigger_delay, valid_nodes)
                
            session.close()
                
            time.sleep(1.5)
            
            try:
                resp2 = std_requests.get(f"{current_api_url}/proxies", headers=headers, timeout=5)
                if resp2.status_code == 200:
                    p_data = resp2.json().get('proxies', {})
                    best_node = None
                    min_delay = float('inf')
                    
                    for n in valid_nodes:
                        history = p_data.get(n, {}).get("history", [])
                        if history:
                            delay = history[-1].get("delay", 0)
                            if 0 < delay < min_delay:
                                min_delay = delay
                                best_node = n
                    
                    if best_node:
                        print(f"[{ts()}] [代理池] {display_name} 测速完成，最快节点: [{clean_for_log(best_node)}] ({min_delay}ms)")
                        switch_resp = std_requests.put(
                            f"{current_api_url}/proxies/{safe_group_name}", 
                            headers=headers, json={"name": best_node}, timeout=5
                        )
                        if switch_resp.status_code == 204:
                            time.sleep(1)
                            if test_proxy_liveness(proxy_url):
                                return True
                            print(f"[{ts()}] [代理池] {display_name} 最快节点测活失败，回退到随机抽卡模式...")
                    else:
                        print(f"[{ts()}] [代理池] {display_name} 所有节点均超时，回退到随机抽卡模式...")
            except Exception as e:
                print(f"[{ts()}] [代理池] {display_name} 优选模式异常: {e}，回退到随机抽卡模式...")

        random_candidates = [name for name, _ in sorted(nodes_with_delay, key=lambda item: item[1])]
        if not random_candidates:
            random_candidates = list(valid_nodes)
            print(f"[{ts()}] [代理池] {display_name} 未发现带有效延迟记录的节点，回退为全量候选抽卡。")

        max_retries = 10
        for i in range(1, max_retries + 1):
            selected_node = random.choice(random_candidates)
            
            print(f"\n[{ts()}] [代理池] {display_name} 尝试切换节点: [{clean_for_log(selected_node)}] ({i}/{max_retries})")
            
            switch_resp = std_requests.put(
                f"{current_api_url}/proxies/{safe_group_name}", 
                headers=headers, json={"name": selected_node}, timeout=5
            )
            
            if switch_resp.status_code == 204:
                time.sleep(1.5)
                if test_proxy_liveness(proxy_url):
                    return True
                print(f"[{ts()}] [代理池] {display_name} 测活失败，重新抽卡...")
            else:
                print(f"[{ts()}] [代理池] {display_name} 指令下发失败 (HTTP {switch_resp.status_code})。")
                
        print(f"\n[{ts()}] [代理池] {display_name} 连续 10 次抽卡均不可用！")
        return False
        
    except Exception as e:
        print(f"[{ts()}] [ERROR] {display_name} 切换节点异常: {e}")
        return False

reload_proxy_config()
