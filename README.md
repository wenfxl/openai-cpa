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

It combines:
- multi-backend mailbox OTP retrieval
- registration task orchestration
- proxy / Clash / Mihomo switching
- CPA warehouse maintenance
- Sub2API warehouse maintenance
- AI-powered profile & subdomain generation (Codex)
- local account inventory, export, deletion, and real-time log streaming

It also supports **random multi-level subdomain generation**, designed to work together with customized mailbox backends such as:
- <https://github.com/wenfxl/freemail>
- <https://github.com/wenfxl/cloud-mail>
- <https://github.com/wenfxl/cloudflare_temp_email_worker>

> Use only in systems and environments you own or are explicitly authorized to test.
> Make sure your use complies with applicable laws, platform rules, and service terms.

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

## Environment Setup

Install Python dependencies. `uv` is the recommended workflow for local development and runtime consistency.

Recommended with `uv`:

```bash
uv sync
```

Start the project with `uv`:

```bash
uv run python wfxl_openai_regst.py
```

Stop the local project process on Windows:

```powershell
powershell -ExecutionPolicy Bypass -File .\stop_openai_cpa.ps1
```

Compatibility fallback with `pip`:

```bash
pip install -r requirements.txt
```

## ☕ Buy me a coffee

If you find this tool helpful or if it has saved you time, consider buying me a coffee! Your support is a great motivation for continuous maintenance and updates.
- ⚡ **Afdian:** [https://ifdian.net/a/wenfxl](https://ifdian.net/a/wenfxl)
- 🪙 **USDT (TRX/Tron/TRC20):** `TLMNmyfUajfGSBhUfJ1orqxpvv7BWFnDqN`

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

## Features

### Web console and runtime control
- **Web visual console**: The current version is managed mainly through a browser-based control panel instead of a config-only workflow.
- **Seamless Config Upgrades**: The backend automatically detects missing configuration keys and merges defaults from `config.example.yaml`, ensuring zero downtime or white-screens during system updates.
- **Distributed Cluster Control**: Supports a true multi-node architecture. When deployed across multiple machines, any node can serve as the master control center. You can remotely orchestrate start/stop commands, stream cross-machine logs, and extract accounts from the entire cluster directly through a single **Cluster Control** panel.
- **Password login + Bearer session**: The console uses password login and token-based authenticated API operations.
- **Real-time log streaming**: Backend logs are pushed to the page through SSE for live monitoring.
- **Task orchestration**: Supports one-click start / stop and automatically identifies `normal`, `CPA`, or `Sub2API` mode.
- **Live statistics dashboard**: Shows success, failure, retries, elapsed time, progress, and current mode in real time.
- **Multi-channel notifications**: Supports real-time task completion reports, stock alerts, and system exceptions via Webhooks or Telegram bots, configured in the **Notifications** panel.

### Distributed Browser Extension Mode ("Classic" Plugin Architecture)
- **Centralized Master, Decentralized Workers**: Deploy the core manager just once. By installing our custom browser extension on multiple browsers (even across different physical machines), they automatically connect back to the master console.
- **True Browser Fingerprints**: Bypasses the strict bot detection often triggered by headless automation frameworks (like Playwright or Puppeteer). Tasks are executed in genuine browser environments.
- **Plug-and-Play Worker Nodes**: Any browser with the extension installed instantly becomes a distributed worker node. The Web Console orchestrates task distribution, collects execution logs, and extracts generated accounts centrally.

### AI Profile & Subdomain Enhancement (Codex)
  - **Realistic Profile Generation**: Automatically calls AI models (e.g., `gpt-5.1-codex`) to generate realistic European/American names (`firstname.lastname`) for registration.
  - **Smart Tech Subdomains**: Generates trending tech/AI keywords (e.g., `vector-database`, `neural`) to be seamlessly injected into the multi-level subdomain generator, significantly increasing account credibility.

### Mailbox and OTP workflow
- **Multi-backend mailbox support**: Supports `cloudflare_temp_email`, `freemail`, `imap`, `cloudmail`, `mail_curl`, `luckmail`, `TempMail.org`, `Tempmail.lol`, `Duckmail`, `Generator`, `hotmail/outlook` and `GmailOauth`.
- **Multi-domain rotation**: Supports comma-separated mailbox domains and randomized selection when generating addresses.
- **Random multi-level subdomain generation**: Can generate random subdomains in batches, including multi-level subdomain structures.
- **Subdomain pool takeover**: When subdomain mode is enabled, generated subdomains can directly replace the normal mailbox domain pool for subsequent registration tasks.
- **Backend-compatible subdomain workflow**: Multi-level subdomain generation is intended to work together with customized mailbox backends / wildcard-domain backends such as `freemail`, `cloud-mail`, and `cloudflare_temp_email_worker`.
- **HeroSMS Integration**: Full support for SMS verification with live balance checking, real-time global pricing/stock panels, and auto-country picking to avoid blacklists and timeouts.
- **LuckMail Advanced Controls**: Built-in support to directly buy emails via API, auto-tag purchases, use a "history reuse" mode to save costs, and a manual bulk-purchase console.
- **Microsoft Asset Isolation**: A dedicated **Microsoft Mail Lib** module for storing, categorizing, and managing Microsoft-specific accounts separate from the standard local inventory.

### Proxy management and network resilience
- **Clash / Mihomo node rotation**: Can switch outbound nodes through the Clash API before registration tasks.
- **Fastest-node preferred mode**: Supports `fastest_mode: true` for latency-based preferred selection.
- **Multi-threaded Clash proxy-pool mode**: Supports a multi-container / multi-port proxy pool via `clash_proxy_pool.pool_mode` + `warp_proxy_list`.
- **Docker-aware proxy adaptation**: Automatically rewrites `127.0.0.1` / `localhost` to `host.docker.internal` inside containers when needed.
- **Region-aware liveness checks**: Verifies outbound connectivity and rejects blocked or unsuitable regions such as `CN` / `HK`.
- **Retry handling**: Includes retry and cooling logic for unstable networks, OTP polling, and temporary request failures.

### Inventory maintenance and warehouse operations
- **Cloud inventory monitoring**: Real-time tracking of remote API balances, stock levels, and account statuses through the **Cloud Inventory** dashboard.
- **Standalone Liveness Check**: A dedicated "Manual Check" button in the Web Console exclusively scans and cleans up dead accounts in your CPA/Sub2API warehouse without triggering the main registration loop.
- **Fast Replenish Toggle**: An `auto_check` toggle to skip full inventory inspections before replenishing, drastically speeding up the loop based purely on cloud API total counts.
- **Local SQLite inventory**: Stores accounts locally and provides paginated inventory browsing in the panel.
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
- **Local SQLite Database**: Generated tokens and account credentials are now securely stored in a centralized local database rather than individual JSON files, providing a much cleaner file system.
- **Optional local retention in CPA / Sub2API mode**: Upload workflows can still retain a local database copy of the accounts when enabled, even after successfully pushing them to the cloud warehouse.
- **Console Export Support**: Accounts stored in the database can be seamlessly selected and exported directly from the Web Console as structured JSON or `email----password` TXT files.
- **Log masking**: Supports masking mailbox domains in console output to protect sensitive domain configurations.

## Project Structure

An overview of the core directories and files in this repository:

```text
.
├── wfxl_openai_regst.py     # Main Web Console entry point
├── global_state.py          # Global state management (tokens, nodes, and cluster locks)
├── routers/                 # API routing endpoints
├── utils/                   # Core engine and configuration management
│   ├── email_providers/     # Mailbox backend implementations
│   └── integrations/        # 3rd-party API integrations (Sub2API, TG Bot, HeroSMS)
├── luckmail/                # Advanced LuckMail service integration
├── static/                  # Web Console frontend assets (Vue.js, CSS)
├── assets/                  # README screenshot resources
├── public/                  # Distributed Browser Extension files ("Classic" mode)
├── data/                    # Runtime data, SQLite DB, local config, and exports
├── index.html               # Frontend UI entry point
├── Dockerfile               # Container image definition
├── docker-compose.yml       # Docker compose deployment example
├── config.example.yaml      # Configuration fallback template
├── requirements.txt         # Python dependency list
└── README.md                # Project documentation
```

## Usage

Start the Web Console service locally:

```bash
uv run python wfxl_openai_regst.py
```

Stop the local service process on Windows:

```powershell
powershell -ExecutionPolicy Bypass -File .\stop_openai_cpa.ps1
```

After startup, open the Web Console in your browser:

```text
http://127.0.0.1:8000
```

Default Web Console password:

```text
admin
```

Recommended workflow:
The repository includes a ready-to-use `docker-compose.yml` for starting the **Wenfxl Codex Manager Web Console** with persistent config and data mounts.
- log in to the Web Console
- configure mailbox / proxy / warehouse settings in the UI
- start or stop tasks from the dashboard
- monitor logs, task status, and account inventory in real time

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
    environment:
      - TZ=Asia/Shanghai
      - DB_TYPE=mysql
      - DB_HOST=MySQL IP
      - DB_PORT=3306
      - DB_USER=root
      - DB_PASS=password
      - DB_NAME=wenfxl_manager
```

### Docker deployment steps

1. Place `docker-compose.yml` and `config.yaml` in the same directory.
2. Start the Web Console container:

```bash
docker compose up -d
```

3. View logs if needed:

```bash
docker compose logs -f
```

4. Stop the container:

```bash
docker compose down
```

5. update:

```bash
docker-compose pull wenfxl/wenfxl-codex-manager:latest
```
config directly
Notes:
- `./data:/app/data` is used to persist runtime data, local database content, and exports.
- The Docker Web Console is exposed on port `8000` by default.
- Default Web Console password: `admin`
- The current compose file uses image tag `wenfxl/wenfxl-codex-manager:latest`.

## Running Mihomo / Clash on a server

If you want to use Clash-based node rotation on a server, you can run Mihomo (Clash Meta compatible core) in the background and expose both a local mixed proxy port and the Clash API.

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

Inspect these fields in your Mihomo config:
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

If you use server-side concurrent registration and want each worker to use an independent Clash instance, you can expose multiple local proxy ports such as:

- `41001`
- `41002`
- `41003`

and pair them with corresponding controller APIs. Then fill `warp_proxy_list` and enable `pool_mode: true`.

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

## Output Files

Typical output files include:

### JSON files

Example:

```text
token_user_example.com_1711111111.json
```

These store structured token / credential output data.

### `accounts.txt`

Example:

```text
example@gmail.com----password123
```

This stores local account-password pairs when applicable.

## Troubleshooting

### Clash node switching fails
Check the following:
- Clash API is enabled
- `clash_proxy_pool.api_url` is correct
- the controller `secret` is correct if authentication is enabled
- `group_name` matches a real selectable proxy group
- `test_proxy_url` points to a working local proxy port
- the blacklist is not too strict

### Multi-threaded proxy pool does not work as expected
Check the following:
- `enable_multi_thread_reg: true`
- `clash_proxy_pool.enable: true`
- `clash_proxy_pool.pool_mode: true`
- `warp_proxy_list` is not empty
- each listed local proxy endpoint is actually reachable
- each proxy/container has a matching controller API

### Gmail IMAP login fails
Check the following:
- IMAP is enabled
- 2-Step Verification is enabled if App Passwords are required
- you are using an App Password, not the normal mailbox password

### No email arrives
Possible causes:
- the email landed in spam
- proxy routing breaks mailbox connectivity
- mailbox backend credentials are invalid
- domain configuration is wrong
- the backend API is not returning the expected message list

### OTP is not extracted
Possible causes:
- the email body encoding is unusual
- the verification code is not a 6-digit number
- the message format does not match the extraction patterns
- the code exists only in the detail endpoint, not in the list view

### CPA inspection or replenishment behaves unexpectedly
Check the following:
- `cpa_mode.enable` is set correctly
- `cpa_mode.api_url` and `api_token` are correct
- `cpa_mode.threads` is not set too high for your server/API capacity
- `remove_on_limit_reached` / `remove_dead_accounts` match your intended policy

## Security Notes

- Do not expose `db` or token JSON outputs publicly.
- Prefer stronger secret handling for mailbox admin credentials, CPA tokens, and Clash controller secrets.
- Restrict access to the output directory.
- If used in a team environment, add audit logging and permission boundaries.

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
