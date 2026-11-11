# Wenfxl Codex Manager Web Console

[![Telegram Group](https://img.shields.io/badge/Telegram-Community_Chat-0088cc?style=for-the-badge&logo=telegram)](https://t.me/+srBiKuPvn4A3YmNl)
[![License](https://img.shields.io/badge/License-CC_BY--NC_4.0-lightgrey?style=for-the-badge)](https://creativecommons.org/licenses/by-nc/4.0/legalcode)

> ⚠️ **CRITICAL UPDATE (April 29, 2026) 20:29**
>
> The official Telegram community has been fully migrated! The original group is no longer active.
>
> **ATTENTION:** The authentication system of Wenfxl Codex Manager is strictly bound to our official group. All users MUST **join the new group immediately**. Failure to do so will result in an HTTP 403 error and automatic service suspension during the next silent authorization check.
>
> 👉 **[Click Here to Join the NEW Official Group](https://t.me/+srBiKuPvn4A3YmNl)**

An advanced Distributed Automation Platform for high-concurrency account registration and full-lifecycle inventory management. It serves as a centralized Web Orchestration Hub that seamlessly synchronizes distributed browser extension workers (Classic Mode), multi-backend mailbox engines, and enterprise-grade cloud warehouses (CPA/Sub2API) into a unified master-worker ecosystem.

It coordinates:

- multi-node / multi-worker task distribution
- multi-backend mailbox and OTP retrieval
- browser-assisted workflows and local challenge handling
- proxy / Clash / Mihomo routing
- cloud warehouse synchronization (CPA / Sub2API)
- local inventory, export, archival, and real-time log streaming
- AI-assisted profile and subdomain generation

The console supports multiple **runtime providers / business modes** under the same control plane, so different task pipelines can share mailbox, proxy, warehouse, and inventory infrastructure without changing the overall architecture.

It also supports **random multi-level subdomain generation**, designed to work with customized mailbox backends such as:

- <https://github.com/wenfxl/freemail>
- <https://github.com/wenfxl/cloud-mail>
- <https://github.com/wenfxl/cloudflare_temp_email_worker>

> Use only in systems and environments you own or are explicitly authorized to operate.
> Make sure your use complies with applicable laws, platform rules, and service terms.

---

## 🚀 Supported Environments
* **Windows**: Native Support (**Python 3.12.6 or Python 3.12** recommended).
* **Linux**: Native Support (**AMD64** & **ARM64**).
* **macOS**: Native Support (**Apple Silicon M1/M2/M3/M4/intel**).
* **Docker**: **Full Platform Support (Highly Recommended)**.
* Multi-arch images provided for seamless deployment across all cloud and local environments.

## ⚠ Important Runtime Notes
* **Native macOS / Linux**: You **MUST** use **Python 3.11** for native execution to ensure compatibility with the core engine.
* **Native Windows**: Please use **Python 3.12.6 or Python 3.12** to match the core engine requirements.
* **Docker Deployment**: This is the **preferred method**. The image comes pre-configured with the optimized environment, offering a true "out-of-the-box" experience without worrying about Python versions.

---

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
python -m camoufox fetch
```

### Cloak runtime installation
dependencies need to be installed when using Cloak.
Windows:

```cmd
python -m cloakbrowser install
python -c "from cloakbrowser.geoip import _ensure_geoip_db; print(_ensure_geoip_db())"
```

macOS:

```bash
python3 -m cloakbrowser install
python3 -c "from cloakbrowser.geoip import _ensure_geoip_db; print(_ensure_geoip_db())"
```

### 2. Start the Web Console

```bash
python wfxl_openai_regst.py
```

Open:

```text
http://127.0.0.1:8000
```

Default password:

```text
admin
```

### 3. Recommended first-run workflow

1. Log in to the Web Console
2. Select the active **runtime provider / business mode**
3. Configure mailbox, proxy, and warehouse settings
4. Start or stop tasks from the dashboard
5. Monitor logs, progress, and inventory in real time

---

## Buy me a coffee

If this platform helps your workflow, consider supporting continued maintenance:

- **Afdian:** [https://ifdian.net/a/wenfxl](https://ifdian.net/a/wenfxl)
- **USDT (TRX / Tron / TRC20):** `TLMNmyfUajfGSBhUfJ1orqxpvv7BWFnDqN`
- **USDT (BSC / BNB Smart Chain / BEP20):** `0x2da26c472097f269217390e44ff00c7d087699a6`

---

## Web Console Preview

<details>
<summary><strong>Click to expand Web Console screenshots</strong></summary>

### 1. Login Screen

![Login Screen](./assets/manager1.png)

### 2. Main Dashboard

![Main Dashboard](./assets/manager2.png)

### 3. Cluster Control

![Cluster Control](./assets/manager3.png)

### 4. Mailbox Configuration / Multi-level Subdomain Settings

![Mailbox Configuration / Multi-level Subdomain Settings](./assets/manager4.png)

### 5. Microsoft Mail Lib

![Microsoft Mail Lib](./assets/manager5.png)

### 6. Account Inventory

![Account Inventory](./assets/manager6.png)

### 7. Cloud Inventory

![Cloud Inventory](./assets/manager7.png)

### 8. SMS Verification

![SMS Verification](./assets/manager8.png)

### 9. Network Proxy Settings

![Network Proxy Settings](./assets/manager9.png)

### 10. Transit Warehouse

![Transit Warehouse](./assets/manager10.png)

### 11. Notifications

![Notifications](./assets/manager11.png)

### 12. Concurrency and System Settings

![Concurrency and System Settings](./assets/manager12.png)

</details>

---


## Features

### Web console and runtime control

- **Web visual console**: The current version is managed mainly through a browser-based control panel instead of a config-only workflow.
- **Seamless Config Upgrades**: The backend automatically detects missing configuration keys and merges defaults from `config.example.yaml`, ensuring zero downtime or white-screens during system updates.
- **Runtime provider switch**: Switch active business mode from the console (`reg_provider`).
- **Distributed Cluster Control**: Supports a true multi-node architecture. When deployed across multiple machines, any node can serve as the master control center. You can remotely orchestrate start/stop commands, stream cross-machine logs, and extract accounts from the entire cluster directly through a single **Cluster Control** panel.
- **Password login + Bearer session**: The console uses password login and token-based authenticated API operations.
- **Real-time log streaming**: Backend logs are pushed to the page through SSE for live monitoring.
- **Task orchestration**: Supports one-click start / stop and automatically identifies `normal`, `CPA`, or `Sub2API` mode.
- **Live statistics dashboard**: Shows success, failure, retries, elapsed time, progress, and current mode in real time.
- **Multi-channel notifications**: Supports real-time task completion reports, stock alerts, and system exceptions via Webhooks or Telegram bots, configured in the **Notifications** panel.

### Runtime providers / business modes

The platform keeps a shared control plane for mailbox, proxy, warehouse, and inventory, while different providers plug into different execution pipelines.

#### Mode A

- Protocol-oriented task pipeline and optional distributed browser extension workers
- Credential packaging and warehouse push for CPA / Sub2API
- AI-assisted profile and subdomain keyword generation

#### Mode B

- Local challenge handling integrated
- Device-flow style token acquisition for complete credential sets
- Local SQLite archival
- Direct Sub2API OAuth-style account import
- Sub2API export auto-recognizes this mode's credential shape
- Cloud inventory / warehouse sync where enabled

### Distributed Browser Extension Mode ("Classic" Plugin Architecture)

- **Centralized Master, Decentralized Workers**: Deploy the core manager just once. By installing our custom browser extension on multiple browsers (even across different physical machines), they automatically connect back to the master console.
- **True Browser Fingerprints**: Bypasses the strict bot detection often triggered by headless automation frameworks (like Playwright or Puppeteer). Tasks are executed in genuine browser environments.
- **Plug-and-Play Worker Nodes**: Any browser with the extension installed instantly becomes a distributed worker node. The Web Console orchestrates task distribution, collects execution logs, and extracts generated accounts centrally.

### AI profile & subdomain enhancement

- **Realistic profile generation**: AI-assisted name / profile fields for task payloads.
- **Smart tech subdomains**: Generates tech-style keywords for multi-level subdomain construction.

### Mailbox and OTP workflow
- **Multi-backend mailbox support**: Supports `cloudflare_temp_email`, `freemail`, `imap`, `cloudmail`, `mail_curl`, `luckmail`, `openai_cpa_cloud`, `TempMail.org`, `Tempmail.lol`, `Duckmail`, `Generator`, `hotmail/outlook` and `GmailOauth`.
- **Multi-domain rotation**: Supports comma-separated mailbox domains and randomized selection when generating addresses.
- **Random multi-level subdomain generation**: Can generate random subdomains in batches, including multi-level subdomain structures.
- **Subdomain pool takeover**: When subdomain mode is enabled, generated subdomains can directly replace the normal mailbox domain pool for subsequent registration tasks.
- **Backend-compatible subdomain workflow**: Multi-level subdomain generation is intended to work together with customized mailbox backends / wildcard-domain backends such as `freemail`, `cloud-mail`, and `cloudflare_temp_email_worker`.
- **HeroSMS Integration**: Full support for SMS verification with live balance checking, real-time global pricing/stock panels, and auto-country picking to avoid blacklists and timeouts.
- **LuckMail Advanced Controls**: Built-in support to directly buy emails via API, auto-tag purchases, use a "history reuse" mode to save costs, and a manual bulk-purchase console.
- **Microsoft Asset Isolation**: A dedicated **Microsoft Mail Lib** module for storing, categorizing, and managing Microsoft-specific accounts separate from the standard local inventory.

### Proxy management and network resilience

- **Clash / Mihomo node rotation**: Switch outbound nodes through the Clash API before task runs.
- **Fastest-node preferred mode**: Latency-based preferred selection (`fastest_mode: true`).
- **Multi-threaded Clash proxy-pool mode**: Multi-container / multi-port pool via `clash_proxy_pool.pool_mode` + `warp_proxy_list`.
- **Docker-aware proxy adaptation**: Rewrites `127.0.0.1` / `localhost` to `host.docker.internal` inside containers when needed.
- **Region-aware liveness checks**: Verifies outbound connectivity and rejects blocked / unsuitable regions such as `CN` / `HK`.
- **Retry handling**: Retry and cooling logic for unstable networks, OTP polling, and temporary request failures.

### Inventory maintenance and warehouse operations

- **Cloud inventory monitoring**: Real-time tracking of remote API balances, stock levels, and account statuses through the **Cloud Inventory** dashboard.
- **Standalone Liveness Check**: A dedicated "Manual Check" button in the Web Console exclusively scans and cleans up dead accounts in your CPA/Sub2API warehouse without triggering the main registration loop.
- **Fast Replenish Toggle**: An `auto_check` toggle to skip full inventory inspections before replenishing, drastically speeding up the loop based purely on cloud API total counts.
- **Local SQLite inventory**: Paginated local browsing and centralized storage.
- **Batch export / delete**: Supports exporting selected accounts as JSON or TXT and deleting selected accounts in bulk.
- **Optional CPA maintenance mode**: Can periodically inspect CPA inventory and replenish stock automatically when valid account count is low.
- **Multi-threaded CPA inspection**: CPA health checks are processed concurrently, and worker count is controlled by `cpa_mode.threads`.
- **CPA upload integration**: Can upload newly generated credentials directly to CPA and trigger push actions from the panel.
- **Sub2API warehouse mode**: Supports periodic inspection, replenishment, push synchronization, and token refresh handling for Sub2API.
- **Sub2API direct push**: Selected accounts can be pushed to Sub2API directly from the Web Console.
- **Quota-threshold handling**: Supports configurable weekly quota threshold logic using remaining weekly percentage thresholds.
- **Disable or delete behavior controls**: You can choose whether exhausted or permanently dead accounts should be disabled only or physically removed by configuration.
- **Credential refresh rescue**: When stored credentials become invalid, the script can attempt refresh-token recovery and update CPA / Sub2API storage.

### Archival output and privacy protection

- **Local SQLite database**: Credentials and tokens are stored centrally instead of scattered files.
- **Optional local retention in CPA / Sub2API mode**: Keep a local DB copy after cloud push when enabled.
- **Console export support**: Structured JSON or `email----password` TXT export.
- **Log masking**: Masks mailbox domains / emails in console output.

---

## Project Structure

```text
.
├── wfxl_openai_regst.py     # Main Web Console entry point
├── global_state.py          # Global state (tokens, nodes, cluster locks)
├── routers/                 # API routing endpoints
├── utils/                   # Core engine and configuration
│   ├── email_providers/     # Mailbox backend implementations
│   ├── grok_auth/           # assisted provider pipeline
│   └── integrations/        # Sub2API, TG Bot, HeroSMS, Clash, etc.
├── turnstile-solver/        # Local challenge helper components
├── luckmail/                # LuckMail service integration
├── static/                  # Web Console frontend assets
├── assets/                  # README screenshots
├── plugin/ / public/        # Distributed browser extension files
├── data/                    # Runtime data, SQLite DB, local config, exports
├── index.html               # Frontend UI entry
├── Dockerfile               # Container image definition
├── docker-compose.yml       # Docker compose example
├── docker-compose2.yml      # Cloud MySQL compose example
├── config.example.yaml      # Configuration fallback template
├── requirements.txt         # Python dependencies
└── README.md                # Project documentation
```

---

## Configuration Notes

Provider / business-mode switch:

```yaml
# runtime provider switch (console also supports this)
reg_provider: "openai"

# browser-assisted mode options
grok:
  oauth_timeout: 180
```

Other major areas are configured from the Web Console and `config.example.yaml`:

- mailbox backends / domains / subdomains
- proxy and Clash pool
- CPA mode
- Sub2API mode
- notifications
- concurrency / system settings

Missing keys are auto-merged from `config.example.yaml` on upgrade.

---


## Running with Docker Compose

The repository includes a ready-to-use `docker-compose.yml` for starting the **Wenfxl Codex Manager Web Console** with persistent config and data mounts.

Current compose example:

```yaml
version: '3.8'

services:
  codex-web:
    image: wenfxl/wenfxl-codex-manager:latest
    container_name: wenfxl_codex_manager
    ports:
      - "8000:8000"
    restart: always
    extra_hosts:
      - "host.docker.internal:host-gateway"
    environment:
      - HOST_PROJECT_PATH=${PWD}
    volumes:
      - ./data:/app/data
      - /var/run/docker.sock:/var/run/docker.sock
      - /usr/bin/docker:/usr/bin/docker
      - .:${PWD}
    labels:
      - "com.centurylinklabs.watchtower.enable=true"
      - "com.centurylinklabs.watchtower.scope=openai-cpa"

  watchtower:
    image: nickfedor/watchtower:latest
    container_name: watchtower
    restart: always
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
    command: --label-enable --scope openai-cpa --interval 86400 --cleanup



```

The repository includes a ready-to-use `docker-compose2.yml` for starting the **Wenfxl Codex Manager Web Console** with persistent config and data mounts. Stateless containers can connect to a cloud MySQL database, and all configuration parameters and data will be stored in the cloud database.

Current compose example:

```yaml
version: '3.8'

services:
  codex-web:
    image: wenfxl/wenfxl-codex-manager:latest
    container_name: wenfxl_codex_manager
    ports:
      - "8000:8000"
    restart: always
    extra_hosts:
      - "host.docker.internal:host-gateway"
    volumes:
      - ./data:/app/data
      - /var/run/docker.sock:/var/run/docker.sock
      - /usr/bin/docker:/usr/bin/docker
      - .:${PWD}
    environment:
      - HOST_PROJECT_PATH=${PWD}
      - TZ=Asia/Shanghai
      - DB_TYPE=mysql
      - DB_HOST=你的云端MySQL地址
      - DB_PORT=3306
      - DB_USER=root
      - DB_PASS=你的数据库密码
      - DB_NAME=wenfxl_manager
```

### Docker deployment steps

1. Place `docker-compose.yml` and `config.yaml` in the same directory.
2. Start:

```bash
docker compose up -d
```

3. Logs:

```bash
docker compose logs -f
```

4. Stop:

```bash
docker compose down
```

5. Update image:

```bash
docker compose pull
docker compose up -d
```

Notes:

- `./data:/app/data` persists runtime data, local DB, and exports
- Web Console default port: `8000`
- Default password: `admin`
- Image: `wenfxl/wenfxl-codex-manager:latest`

---

## Running Mihomo / Clash on a server

To use Clash-based node rotation on a server, run Mihomo (Clash Meta compatible core) in the background and expose both a local mixed proxy port and the Clash API.

### 1. Prepare a working directory

```bash
mkdir -p /opt/clash && cd /opt/clash
```

### 2. Download the Mihomo binary

Example for Linux x86_64:

```bash
wget https://github.com/MetaCubeX/mihomo/releases/download/v1.18.1/mihomo-linux-amd64-v1.18.1.gz
gzip -d mihomo-linux-amd64-v1.18.1.gz
mv mihomo-linux-amd64-v1.18.1 mihomo
chmod +x mihomo
```

### 3. Download your subscription-derived config

```bash
wget -U "Clash-meta" -O /opt/clash/config.yaml 'YOUR_SUBSCRIPTION_CONVERTER_URL'
```

### 4. Check important fields in `config.yaml`

- `mixed-port`
- `external-controller`
- `secret`

Example:

```yaml
mixed-port: 7897
external-controller: 127.0.0.1:9097
secret: your-secret
```

Then align your project config:

```yaml
default_proxy: "http://127.0.0.1:7897"

clash_proxy_pool:
  enable: true
  pool_mode: false
  api_url: "http://127.0.0.1:9097"
  secret: "your-secret"
  test_proxy_url: "http://127.0.0.1:7897"
```

### 5. Start Mihomo in the background

```bash
nohup /opt/clash/mihomo -d /opt/clash > /opt/clash/clash.log 2>&1 &
```

### 6. Stop Mihomo

```bash
pkill mihomo
```

### 7. Multi-container proxy-pool idea

For concurrent workers with independent Clash instances, expose multiple local proxy ports under `warp_proxy_list`, then enable:

```yaml
enable_multi_thread_reg: true

clash_proxy_pool:
  enable: true
  pool_mode: true
```

### 8. Deploy a Clash Proxy Cluster via Web Console (Recommended)

The legacy shell script deployment has been deprecated and replaced by the powerful built directly into the web console. You can now dynamically scale, configure, and route your Mihomo (Clash) containers without ever touching the command line.

#### Step 1: Access the Proxy Settings
Log in to the Wenfxl Web Console and navigate to the **[Network Proxy]** tab.

#### Step 2: Scale the Cluster
Locate the **Mihomo Instance Cluster Control** panel. Enter your desired number of container instances (e.g., `5`) and click **[Sync Scale]**. The backend will automatically create and map the Docker containers for you.
*(Note: Proxy ports are automatically mapped starting from 41001, and API ports from 42001).*

#### Step 3: Distribute Subscription
In the **Subscription Update** section, paste your proxy subscription URL and click **[Distribute]**. The system will automatically fetch the nodes, apply necessary patches (enabling LAN and API access), and restart the instances. 

#### Step 4: One-Click Pool Sync
Click the purple **[🔗 Sync to Exclusive Pool]** button at the top of the panel. This will automatically calculate the internal routing addresses of your new cluster and link them directly to the smart proxy pool for load balancing.

---

## Troubleshooting

### Console returns 403 / auth fails

- Join the official Telegram group bound to this project
- Confirm you are using the current official group invite link

### challenge fails

- Run `python -m camoufox fetch` on native installs
- Confirm outbound network / soft-router path can reach the target sites
- Prefer stable proxy quality; slow or blocked exits often cause challenge timeouts

### Warehouse push succeeds but later token refresh fails

- Ensure the stored credential set includes both access and refresh material
- revoked refresh usually means the refresh token is already dead; regenerate credentials and push again
- Export and push share the same credential packaging path for browser-assisted mode

### Clash node switching does not work

- `mixed-port` / `external-controller` / `secret` match project config
- `api_url` can reach the controller
- selected proxy group is valid
- `test_proxy_url` points to a working local proxy port
- blacklist is not too strict

### Multi-threaded proxy pool does not work as expected

- `enable_multi_thread_reg: true`
- `clash_proxy_pool.enable: true`
- `clash_proxy_pool.pool_mode: true`
- `warp_proxy_list` is not empty
- each listed local proxy endpoint is reachable
- each proxy / container has a matching controller API

### Gmail IMAP login fails

- IMAP enabled
- 2-Step Verification enabled if App Passwords are required
- using an App Password, not the normal mailbox password

### No email arrives

- spam folder
- proxy routing breaks mailbox connectivity
- mailbox backend credentials invalid
- domain configuration wrong
- backend API not returning the expected message list

### OTP is not extracted

- unusual email body encoding
- code is not a 6-digit number
- message format does not match extraction patterns
- code exists only in detail endpoint, not list view

### CPA inspection or replenishment behaves unexpectedly

- `cpa_mode.enable` is correct
- `cpa_mode.api_url` and `api_token` are correct
- `cpa_mode.threads` is not too high for your server / API capacity
- `remove_on_limit_reached` / `remove_dead_accounts` match your policy

---

## Security Notes

- Do not expose databases or token outputs publicly.
- Prefer stronger secret handling for mailbox credentials, CPA tokens, Sub2API keys, and Clash controller secrets.
- Restrict access to the data directory.
- In team environments, add audit logging and permission boundaries.

---

## Contributors

Thanks to all the developers who have contributed to this project:

<a href="https://github.com/kamill7779"><img src="https://wsrv.nl/?url=github.com/kamill7779.png&mask=circle" width="80" title="kamill7779" alt="kamill7779"></a>
<a href="https://github.com/s12ryt"><img src="https://wsrv.nl/?url=github.com/s12ryt.png&mask=circle" width="80" title="s12ryt" alt="s12ryt"></a>
<a href="https://github.com/SYFATP"><img src="https://wsrv.nl/?url=github.com/SYFATP.png&mask=circle" width="80" title="SYFATP" alt="SYFATP"></a>
<a href="https://github.com/YuHaiA"><img src="https://wsrv.nl/?url=github.com/YuHaiA.png&mask=circle" width="80" title="YuHaiA" alt="YuHaiA"></a>
<a href="https://github.com/haocenchen-debug"><img src="https://wsrv.nl/?url=github.com/haocenchen-debug.png&mask=circle" width="80" title="haocenchen-debug" alt="haocenchen-debug"></a>
<a href="https://github.com/bohu-t"><img src="https://wsrv.nl/?url=github.com/bohu-t.png&mask=circle" width="80" title="bohu-t" alt="bohu-t"></a>
---

## Terms of Use & License

This project is a **"Source-Available"** private project, licensed under the **CC BY-NC 4.0** (Creative Commons Attribution-NonCommercial 4.0 International) license.

* **Author**: wfxl (GitHub: [wenfxl](https://github.com/wenfxl))
* **License File**: [`LICENSE`](https://github.com/wenfxl/openai-cpa/blob/master/LICENSE)
* **Full License**: [CC BY-NC 4.0 Legal Code](https://creativecommons.org/licenses/by-nc/4.0/legalcode)

### 🚫 Strict Compliance & No Commercial Use
This project is **NOT** Free and Open-Source Software (FOSS) in the strict sense. All users must strictly adhere to the following guidelines:

1. ✅ **Allowed**: Limited strictly to individual developers for technical learning, code research, and non-profit local testing.
2. ⚠ **Attribution Required (BY)**: If you copy, distribute, or modify this code, you **MUST** clearly attribute the original author (**wfxl**) and provide a link to this original repository. Removing the author's copyright notice and claiming the code as your own is strictly prohibited.
3. ❌ **Strictly Prohibited (NC)**: Any individual, team, or enterprise is strictly prohibited from using this project (and any modified versions thereof) for any form of commercial monetization. This includes, but is not limited to:
   - Packaging as closed-source, encrypting, or hiding the code for secondary reselling;
   - Deploying it as a paid SaaS service (e.g., paid registration platforms, token-selling sites) for public use;
   - Bundling it within other commercial traffic-generating products.

**If any unauthorized commercial use or copyright infringement (e.g., failure to attribute) is discovered, the author reserves the right to pursue full legal action and claim financial compensation.**

> **Disclaimer**
> This project is strictly for technical learning, automated research, and educational exchange. Please ensure that your usage complies with local laws and regulations, as well as the Terms of Service of the platforms involved (e.g., OpenAI, Cloudflare, etc.). The user assumes full and sole responsibility for any legal disputes, account suspensions, or asset losses resulting from improper or illegal use. The author bears no liability or joint responsibility whatsoever.
