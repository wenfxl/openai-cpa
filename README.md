# Wenfxl Codex Manager Web Console

A Python-based registration and inventory management tool that is now centered around a **Web Console workflow** instead of a script-only workflow.

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


## Environment Setup
[!IMPORTANT]
Supported OS: Windows (Python 3.12.6 or Python 3.12) and Linux/Docker.
﻿
Note: macOS is currently NOT natively supported due to binary dependencies (.pyd and .so files).

Install Python Dependencies Install the required base libraries using the requirements.txt file in the root directory:

```bash
pip install -r requirements.txt
```
After installing the dependencies, you need to continue executing the following commands

```bash
playwright install --with-deps chromium
```

## Web Console Preview

<details>
<summary><strong>Click to expand Web Console screenshots</strong></summary>

### 1. Login Screen

![Login Screen](./assets/manager1.png)

### 2. Main Dashboard

![Main Dashboard](./assets/manager2.png)

### 3. Account Inventory

![Account Inventory](./assets/manager3.png)

### 4. Mailbox Configuration / Multi-level Subdomain Settings

![Mailbox Configuration / Multi-level Subdomain Settings](./assets/manager4.png)

### 5. Cloudflare Route Management

![Cloudflare Route Management](./assets/manager5.png)

### 6. Network Proxy Settings

![Network Proxy Settings](./assets/manager6.png)

### 7. Relay / Warehouse Management

![Relay / Warehouse Management](./assets/manager7.png)

### 8. Concurrency and System Settings

![Concurrency and System Settings](./assets/manager8.png)

</details>

## Features

### Web console and runtime control
- **Web visual console**: The current version is managed mainly through a browser-based control panel instead of a config-only workflow.
- **Seamless Config Upgrades**: The backend automatically detects missing configuration keys and merges defaults from `config.example.yaml`, ensuring zero downtime or white-screens during system updates.
- **Password login + Bearer session**: The console uses password login and token-based authenticated API operations.
- **Real-time log streaming**: Backend logs are pushed to the page through SSE for live monitoring.
- **Task orchestration**: Supports one-click start / stop and automatically identifies `normal`, `CPA`, or `Sub2API` mode.
- **Live statistics dashboard**: Shows success, failure, retries, elapsed time, progress, and current mode in real time.

### AI Profile & Subdomain Enhancement (Codex)
  - **Realistic Profile Generation**: Automatically calls AI models (e.g., `gpt-5.1-codex`) to generate realistic European/American names (`firstname.lastname`) for registration.
  - **Smart Tech Subdomains**: Generates trending tech/AI keywords (e.g., `vector-database`, `neural`) to be seamlessly injected into the multi-level subdomain generator, significantly increasing account credibility.

### Mailbox and OTP workflow
- **Multi-backend mailbox support**: Supports `cloudflare_temp_email`, `freemail`, `imap`, `cloudmail`, `mail_curl`, and `luckmail`.
- **Multi-domain rotation**: Supports comma-separated mailbox domains and randomized selection when generating addresses.
- **Random multi-level subdomain generation**: Can generate random subdomains in batches, including multi-level subdomain structures.
- **Subdomain pool takeover**: When subdomain mode is enabled, generated subdomains can directly replace the normal mailbox domain pool for subsequent registration tasks.
- **Backend-compatible subdomain workflow**: Multi-level subdomain generation is intended to work together with customized mailbox backends / wildcard-domain backends such as `freemail`, `cloud-mail`, and `cloudflare_temp_email_worker`.
- **HeroSMS Integration**: Full support for SMS verification with live balance checking, real-time global pricing/stock panels, and auto-country picking to avoid blacklists and timeouts.
- **LuckMail Advanced Controls**: Built-in support to directly buy emails via API, auto-tag purchases, use a "history reuse" mode to save costs, and a manual bulk-purchase console.

### Proxy management and network resilience
- **Clash / Mihomo node rotation**: Can switch outbound nodes through the Clash API before registration tasks.
- **Fastest-node preferred mode**: Supports `fastest_mode: true` for latency-based preferred selection.
- **Multi-threaded Clash proxy-pool mode**: Supports a multi-container / multi-port proxy pool via `clash_proxy_pool.pool_mode` + `warp_proxy_list`.
- **Docker-aware proxy adaptation**: Automatically rewrites `127.0.0.1` / `localhost` to `host.docker.internal` inside containers when needed.
- **Region-aware liveness checks**: Verifies outbound connectivity and rejects blocked or unsuitable regions such as `CN` / `HK`.
- **Retry handling**: Includes retry and cooling logic for unstable networks, OTP polling, and temporary request failures.

### Inventory maintenance and warehouse operations
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
- **Local JSON backup**: Saves generated tokens to local JSON files.
- **Optional local backup in CPA / Sub2API mode**: Upload workflows can still keep local backups when enabled.
- **TXT export support**: Selected accounts can be exported as `email----password` text files.
- **Log masking**: Supports masking mailbox domains in console output.
## Usage

Start the Web Console service locally:

```bash
python wfxl_openai_regst.py
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
      - "8899:8000"
    restart: always
    extra_hosts:
      - "host.docker.internal:host-gateway"
    volumes:
      - ./data:/app/data

  watchtower:
    image: containrrr/watchtower
    container_name: watchtower
    restart: always
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
    command: --interval 86400 --cleanup
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

### 8. Create a Clash proxy pool with a deployment script

You can also create a Clash proxy pool on a server by generating multiple Mihomo containers through a shell script.

#### Step 1: remove the old script if it exists

```bash
rm -f /root/run_clash.sh
```

#### Step 2: create the script file

```bash
nano /root/run_clash.sh
```

After pasting the script content:
- press `Ctrl+O`
- press `Enter`
- press `Ctrl+X`

#### Step 3: grant execute permission

```bash
chmod +x /root/run_clash.sh
```

#### Step 4: run the script

```bash
/root/run_clash.sh
```

#### Script example

```bash
#!/bin/bash

# ================= Configuration =================
# Mode selection: 1 = single-subscription mode (1 URL distributed to 10 containers)
#                 2 = multi-subscription mode (10 URLs mapped to 10 containers)
MODE=1

# If MODE=1, fill this single URL
SINGLE_URL="https://你的链接"

# If MODE=2, fill up to 10 URLs in order.
# If fewer URLs are filled, only that many containers will be created.
URLS=(
 "https://链接1"
 "https://链接2"
 "https://链接3"
 "https://链接4"
 "https://链接5"
 "https://链接6"
 "https://链接7"
 "https://链接8"
 "https://链接9"
 "https://链接10"
)
# ================================================

WORK_DIR="/root/clash-pool"
mkdir -p $WORK_DIR && cd $WORK_DIR

if [ "$MODE" == "1" ]; then
 COUNT=10
else
 COUNT=${#URLS[@]}
fi

echo "--- Current mode: $MODE [1:single-subscription, 2:multi-subscription] ---"

cat <<EOF > docker-compose.yml
version: "3"
services:
$(for ((i=1; i<=COUNT; i++)); do
 PROXY_PORT=$((41000 + i))
 API_PORT=$((42000 + i))
 echo " clash_$i:
 image: metacubex/mihomo:latest
 container_name: clash_$i
 restart: always
 volumes:
 - ./config_$i/config.yaml:/root/.config/mihomo/config.yaml
 ports:
 - \"$PROXY_PORT:7890\"
 - \"$API_PORT:9090\""
done)
EOF

docker compose down --remove-orphans

if [ "$MODE" == "1" ]; then
 echo "--- Running single-subscription distribution mode ---"
 mkdir -p config_1
 wget -q -U "Clash-meta" -O ./config_1/config.yaml "$SINGLE_URL"
 if [ -s "./config_1/config.yaml" ]; then
  for ((i=2; i<=COUNT; i++)); do
   mkdir -p "config_$i"
   \cp -f "./config_1/config.yaml" "./config_$i/config.yaml"
  done
 fi
else
 echo "--- Running multi-subscription download mode ---"
 for ((i=1; i<=COUNT; i++)); do
  idx=$((i-1))
  CURRENT_URL=${URLS[$idx]}
  mkdir -p "config_$i"
  wget -q -U "Clash-meta" -O "./config_$i/config.yaml" "$CURRENT_URL"
  echo " -> container $i download complete"
 done
fi

for ((i=1; i<=COUNT; i++)); do
 CONF="./config_$i/config.yaml"
 if [ -f "$CONF" ]; then
  grep -q "allow-lan:" "$CONF" && sed -i 's/allow-lan: .*/allow-lan: true/g' "$CONF" || echo "allow-lan: true" >> "$CONF"
  grep -q "external-controller:" "$CONF" && sed -i 's/external-controller: .*/external-controller: 0.0.0.0:9090/g' "$CONF" || echo "external-controller: 0.0.0.0:9090" >> "$CONF"
 fi
done

docker compose up -d

echo ""
echo "=========================================="
echo " Copy the following into your script config: "
echo "=========================================="
echo "warp_proxy_list:"
for ((i=1; i<=COUNT; i++)); do
 echo " - \"http://127.0.0.1:$((41000 + i))\""
done
echo "=========================================="
echo ""

echo "--- Deployment completed! Started $COUNT containers ---"
```

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