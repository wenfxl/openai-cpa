import os
import shutil
import signal
import socket
import subprocess
import time
import urllib.parse

import docker
import requests
import yaml

import utils.config as cfg

BASE_PATH = os.path.join(os.getcwd(), "data", "mihomo-pool")
os.makedirs(BASE_PATH, exist_ok=True)

HOST_PROJECT_PATH = os.getenv("HOST_PROJECT_PATH", os.getcwd())
HOST_BASE_PATH = os.path.join(HOST_PROJECT_PATH, "data", "mihomo-pool")

IMAGE_NAME = "metacubex/mihomo:latest"
MANUAL_SUBSCRIPTION_PATH = os.path.join(BASE_PATH, "manual-subscription.txt")
MANUAL_CONFIG_PATH = os.path.join(BASE_PATH, "manual-config.yaml")
SINGLE_CORE_LOG_PATH = os.path.join(BASE_PATH, "mihomo-core.log")
SINGLE_CORE_PID_PATH = os.path.join(BASE_PATH, "mihomo-core.pid")


def get_client():
    try:
        return docker.from_env()
    except Exception as e:
        print(f"[!] Docker 连接失败: {e}")
        return None


def _read_runtime_config() -> dict:
    config_path = os.path.join(os.getcwd(), "data", "config.yaml")
    if not os.path.exists(config_path):
        return {}
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _persist_sub_url(url: str) -> None:
    config_data = _read_runtime_config()
    clash_conf = config_data.get("clash_proxy_pool", {})
    if not isinstance(clash_conf, dict):
        clash_conf = {}
    clash_conf["sub_url"] = str(url or "").strip()
    config_data["clash_proxy_pool"] = clash_conf
    cfg.reload_all_configs(new_config_dict=config_data)


def _build_requests_proxies() -> dict | None:
    proxy_url = str(getattr(cfg, "DEFAULT_PROXY", "") or "").strip()
    if not proxy_url:
        return None
    return {"http": proxy_url, "https": proxy_url}


def _extract_port_from_url(url: str, fallback: int) -> int:
    text = str(url or "").strip()
    if not text:
        return fallback
    try:
        if "://" not in text:
            text = f"http://{text}"
        parsed = urllib.parse.urlparse(text)
        return int(parsed.port or fallback)
    except Exception:
        return fallback


def _collect_groups_from_config(config_path: str) -> list[dict]:
    groups = []
    if not os.path.exists(config_path):
        return groups
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        for group in data.get("proxy-groups", []):
            groups.append(
                {
                    "name": group.get("name", "N/A"),
                    "count": len(group.get("proxies", [])),
                    "type": group.get("type", "N/A"),
                }
            )
    except Exception:
        pass
    return groups


def _read_pid() -> int | None:
    if not os.path.exists(SINGLE_CORE_PID_PATH):
        return None
    try:
        return int(open(SINGLE_CORE_PID_PATH, "r", encoding="utf-8").read().strip())
    except Exception:
        return None


def _is_pid_running(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _write_pid(pid: int) -> None:
    with open(SINGLE_CORE_PID_PATH, "w", encoding="utf-8") as f:
        f.write(str(pid))


def _stop_single_core() -> None:
    pid = _read_pid()
    if _is_pid_running(pid):
        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(1.0)
        except Exception:
            pass
        if _is_pid_running(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except Exception:
                pass
    if os.path.exists(SINGLE_CORE_PID_PATH):
        try:
            os.remove(SINGLE_CORE_PID_PATH)
        except Exception:
            pass


def _probe_local_ports(api_port: int, proxy_port: int, secret: str = "") -> bool:
    try:
        headers = {"Authorization": f"Bearer {secret}"} if secret else {}
        res = requests.get(f"http://127.0.0.1:{api_port}/version", timeout=1.5, headers=headers)
        if res.status_code in {200, 401}:
            return True
    except Exception:
        pass
    try:
        with socket.create_connection(("127.0.0.1", proxy_port), 0.8):
            return True
    except Exception:
        return False


def _build_local_gui_status() -> dict:
    config_data = _read_runtime_config()
    clash_conf = config_data.get("clash_proxy_pool", {}) if isinstance(config_data.get("clash_proxy_pool"), dict) else {}
    proxy_url = str(config_data.get("default_proxy") or "").strip()
    api_url = str(clash_conf.get("api_url") or "").strip()
    proxy_port = _extract_port_from_url(proxy_url, 7897)
    api_port = _extract_port_from_url(api_url, 9097)
    running = _probe_local_ports(api_port, proxy_port, str(clash_conf.get("secret") or "").strip())
    return {
        "mode": "local_gui",
        "instances": [
            {
                "name": "local-gui",
                "status": "running" if running else "external",
                "ports": f"{proxy_url or '-'} / {api_url or '-'}",
            }
        ],
        "groups": _collect_groups_from_config(MANUAL_CONFIG_PATH),
        "message": (
            "当前未检测到 Docker，已切换为本地 GUI 模式。网页仅保存订阅链接与 YAML 配置，不直接接管 GUI 进程。"
            + (" 已检测到本地 Clash/Mihomo 正在运行。" if running else " 暂未检测到本地 Clash/Mihomo 控制口或代理口。")
        ),
    }


def _build_single_core_status() -> dict:
    config_data = _read_runtime_config()
    clash_conf = config_data.get("clash_proxy_pool", {}) if isinstance(config_data.get("clash_proxy_pool"), dict) else {}
    proxy_url = str(config_data.get("default_proxy") or "").strip()
    api_url = str(clash_conf.get("api_url") or "").strip()
    pid = _read_pid()
    running = _is_pid_running(pid)
    return {
        "mode": "linux_single_core",
        "instances": [
            {
                "name": "mihomo-local",
                "status": "running" if running else "stopped",
                "ports": f"{proxy_url or '-'} / {api_url or '-'}",
                "pid": pid or "-",
            }
        ],
        "groups": _collect_groups_from_config(MANUAL_CONFIG_PATH),
        "message": "当前为 Linux 单核心模式。网页会直接写入 Mihomo 配置并重启本机内核。",
    }


def _detect_runtime_mode(client) -> str:
    if client:
        return "docker_pool"
    if os.name != "nt" and shutil.which("mihomo"):
        return "linux_single_core"
    return "local_gui"


def _build_sample_container_config():
    return {"allow-lan": True, "mixed-port": 7890}


def _write_single_core_config(raw_yaml: dict) -> dict:
    config_data = _read_runtime_config()
    clash_conf = config_data.get("clash_proxy_pool", {}) if isinstance(config_data.get("clash_proxy_pool"), dict) else {}
    mixed_port = _extract_port_from_url(config_data.get("default_proxy"), 7897)
    controller_port = _extract_port_from_url(clash_conf.get("api_url"), 9097)
    patched = dict(raw_yaml)
    patched.update(
        {
            "mixed-port": mixed_port,
            "allow-lan": True,
            "external-controller": f"127.0.0.1:{controller_port}",
        }
    )
    secret = str(clash_conf.get("secret") or "").strip()
    if secret:
        patched["secret"] = secret
    with open(MANUAL_CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(patched, f, allow_unicode=True, sort_keys=False)
    return patched


def _start_single_core() -> tuple[bool, str]:
    mihomo_bin = shutil.which("mihomo")
    if not mihomo_bin:
        return False, "未找到 mihomo 可执行文件。请先在 Linux 服务器安装 mihomo。"
    if not os.path.exists(MANUAL_CONFIG_PATH):
        return False, "未找到 manual-config.yaml，无法启动 mihomo。"

    _stop_single_core()
    with open(SINGLE_CORE_LOG_PATH, "a", encoding="utf-8") as log_fp:
        proc = subprocess.Popen(
            [mihomo_bin, "-f", MANUAL_CONFIG_PATH],
            stdout=log_fp,
            stderr=log_fp,
            cwd=BASE_PATH,
            start_new_session=True,
        )
    _write_pid(proc.pid)

    config_data = _read_runtime_config()
    clash_conf = config_data.get("clash_proxy_pool", {}) if isinstance(config_data.get("clash_proxy_pool"), dict) else {}
    api_port = _extract_port_from_url(clash_conf.get("api_url"), 9097)
    secret = str(clash_conf.get("secret") or "").strip()
    last_error = ""
    for _ in range(12):
        time.sleep(0.5)
        try:
            headers = {"Authorization": f"Bearer {secret}"} if secret else {}
            res = requests.get(f"http://127.0.0.1:{api_port}/version", timeout=1.5, headers=headers)
            if res.status_code in {200, 401}:
                return True, "Mihomo 单核心已启动并可响应控制接口。"
            last_error = f"HTTP {res.status_code}"
        except Exception as e:
            last_error = str(e)
    return False, f"Mihomo 已尝试启动，但控制接口未就绪：{last_error}"


def get_pool_status():
    client = get_client()
    mode = _detect_runtime_mode(client)
    if mode == "local_gui":
        return _build_local_gui_status()
    if mode == "linux_single_core":
        return _build_single_core_status()

    instances = []
    containers = client.containers.list(all=True, filters={"name": "clash_"})
    for c in containers:
        p_map = c.attrs.get("HostConfig", {}).get("PortBindings", {})
        ports = [f"{b[0]['HostPort']}->{p.split('/')[0]}" for p, b in p_map.items() if b]
        instances.append({"name": c.name, "status": c.status, "ports": ", ".join(ports)})
    instances.sort(key=lambda x: int(x["name"].split("_")[1]) if "_" in x["name"] else 999)
    return {
        "mode": "docker_pool",
        "instances": instances,
        "groups": _collect_groups_from_config(os.path.join(BASE_PATH, "clash_1", "config.yaml")),
        "message": "当前为 Docker 集群模式。网页可直接调度 Mihomo 容器实例。",
    }


def deploy_clash_pool(count):
    client = get_client()
    mode = _detect_runtime_mode(client)
    if mode == "local_gui":
        return False, "当前未检测到 Docker。本地 Windows GUI 模式无需同步实例，请直接在本机 Clash/Mihomo 中导入订阅。"
    if mode == "linux_single_core":
        return True, "当前为 Linux 单核心模式，无需同步实例规模。"

    for c in client.containers.list(all=True, filters={"name": "clash_"}):
        try:
            if int(c.name.split("_")[1]) > count:
                c.remove(force=True)
        except Exception:
            pass

    for i in range(1, count + 1):
        name = f"clash_{i}"
        inst_dir = os.path.join(BASE_PATH, name)
        os.makedirs(inst_dir, exist_ok=True)
        cfg_file = os.path.join(inst_dir, "config.yaml")
        if not os.path.exists(cfg_file):
            with open(cfg_file, "w", encoding="utf-8") as f:
                yaml.dump(_build_sample_container_config(), f, allow_unicode=True, sort_keys=False)

        try:
            client.containers.get(name)
        except docker.errors.NotFound:
            client.containers.run(
                IMAGE_NAME,
                name=name,
                detach=True,
                restart_policy={"Name": "always"},
                ports={"7890/tcp": 41000 + i, "9090/tcp": 42000 + i},
                volumes={
                    os.path.join(HOST_BASE_PATH, name, "config.yaml"): {
                        "bind": "/root/.config/mihomo/config.yaml",
                        "mode": "rw",
                    }
                },
            )
    return True, f"成功同步 {count} 个实例"


def patch_and_update(url, target):
    client = get_client()
    mode = _detect_runtime_mode(client)
    try:
        headers = {
            "User-Agent": "Clash-meta",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        request_kwargs = {"headers": headers, "timeout": 30}
        proxies = _build_requests_proxies()
        if proxies:
            request_kwargs["proxies"] = proxies
        r = requests.get(url, **request_kwargs)
        r.raise_for_status()
        raw_text = str(r.text or "")
        _persist_sub_url(url)
        os.makedirs(BASE_PATH, exist_ok=True)
        with open(MANUAL_SUBSCRIPTION_PATH, "w", encoding="utf-8") as f:
            f.write(raw_text)

        raw_yaml = yaml.safe_load(raw_text)
        if not isinstance(raw_yaml, dict):
            if mode == "local_gui":
                return True, "订阅链接已保存，但内容不是 YAML。当前为本地 GUI 模式，请让 GUI 自己导入该订阅链接。"
            return False, "订阅内容不是 Clash/Mihomo YAML，无法直接下发到核心。请使用 YAML 订阅链接。"

        if mode == "local_gui":
            _write_single_core_config(raw_yaml)
            return True, "订阅链接已保存。检测到 YAML 配置，已写入 data/mihomo-pool/manual-config.yaml，方便本地 GUI 导入。"

        if mode == "linux_single_core":
            _write_single_core_config(raw_yaml)
            ok, msg = _start_single_core()
            return ok, ("订阅已更新并已下发到 Linux 单核心 Mihomo。 " + msg) if ok else msg

        conts = client.containers.list(all=True, filters={"name": "clash_"})
        indices = range(1, len(conts) + 1) if target == "all" else [int(target)]
        for i in indices:
            name = f"clash_{i}"
            patched = dict(raw_yaml)
            patched.update({"mixed-port": 7890, "allow-lan": True, "external-controller": "0.0.0.0:9090"})
            with open(os.path.join(BASE_PATH, name, "config.yaml"), "w", encoding="utf-8") as f:
                yaml.dump(patched, f, allow_unicode=True, sort_keys=False)
            try:
                client.containers.get(name).restart()
            except Exception:
                pass

        return True, "订阅已更新并应用补丁"
    except Exception as e:
        return False, str(e)
