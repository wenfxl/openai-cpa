# Email OTP Retrieval, OpenAI Registration, and CPA Inventory Utility

A Python utility that provides multi-backend mailbox support, multi-domain rotation, OTP polling and extraction, Clash/Mihomo node switching, fastest-node preferred selection, multi-threaded proxy-pool registration, CPA multi-threaded inventory inspection, account health checking, credential recovery, low-stock replenishment, local token archival, and log privacy masking.

> Use only in systems and environments you own or are explicitly authorized to test.
> Make sure your use complies with applicable laws, platform rules, and service terms.

## Web Console Preview

### Dashboard

![Web Console Preview 1](./assets/manager1.png)

### Task / Account View

![Web Console Preview 2](./assets/manager2.png)

### Configuration / Management View

![Web Console Preview 3](./assets/manager3.png)

## Features

### Flexible mailbox and verification workflow
- **Multi-backend mailbox support**: Supports `cloudflare_temp_email`, `freemail`, `imap`, `cloudmail`, and `mail_curl`.
- **Multi-domain rotation**: Supports comma-separated mailbox domains and randomized selection when generating addresses.
- **OTP polling and extraction**: Can extract 6-digit OTP codes from subject lines, plain text, HTML content, and raw email payloads.
- **Robust parsing**: Includes MIME decoding, HTML-to-text cleanup, raw message parsing, and common encoding handling.

### Proxy management and network resilience
- **Clash / Mihomo node rotation**: Can switch outbound nodes through the Clash API before registration tasks.
- **Fastest-node preferred mode**: Supports `fastest_mode: true` for latency-based preferred node selection. When enabled, the workflow prefers the lowest-latency available node instead of random blind selection. If one node is consistently the fastest, it may be selected repeatedly.
- **Multi-threaded Clash proxy-pool mode**: Supports a multi-container / multi-port proxy pool via `clash_proxy_pool.pool_mode` + `warp_proxy_list`, so concurrent registration workers can use independent local proxy channels instead of sharing one global port.
- **Single-instance and pool-mode support**: Works both in local single-Clash mode and in server-side multi-container mode.
- **Flexible proxy routing**: Supports a global registration proxy and separate proxy behavior for mailbox API / IMAP traffic.
- **Region-aware liveness checks**: Verifies outbound connectivity and rejects blocked or unsuitable regions such as `CN` / `HK`.
- **Retry handling**: Includes retry and cooling logic for unstable networks, OTP polling, and temporary request failures.

### CPA inventory maintenance and operations
- **Optional CPA maintenance mode**: Can periodically inspect CPA inventory and replenish stock automatically when valid account count is low.
- **Multi-threaded CPA inspection**: CPA health checks are processed concurrently, and worker count is controlled by `cpa_mode.threads`.
- **Configurable maintenance switch**: CPA inspection / replenishment is enabled or disabled through `cpa_mode.enable`.
- **Quota-threshold handling**: Supports configurable weekly quota threshold logic using `min_remaining_weekly_percent`.
- **Disable or delete behavior controls**: You can choose whether exhausted or permanently dead accounts should be disabled only or physically removed by configuration.
- **Credential refresh rescue**: When stored credentials become invalid, the script can attempt refresh-token recovery and update CPA storage.

### Archival output and privacy protection
- **Local JSON backup**: Saves generated tokens to local JSON files.
- **Optional local backup in CPA mode**: In CPA replenishment mode, `save_to_local` controls whether local backup is still kept when uploading remotely.
- **CPA upload integration**: Can upload newly generated credentials directly to CPA.
- **Log masking**: Supports masking mailbox domains in console output.
- **Centralized YAML configuration**: Runtime behavior is controlled through `config.yaml`.

## Table of Contents

- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
  - [1. `email_api_mode`](#1-email_api_mode)
  - [2. `mail_domains`](#2-mail_domains)
  - [3. `gptmail_base`](#3-gptmail_base)
  - [4. `admin_auth`](#4-admin_auth)
  - [5. `imap`](#5-imap)
  - [6. `freemail`](#6-freemail)
  - [7. `cloudmail`](#7-cloudmail)
  - [8. `mail_curl`](#8-mail_curl)
  - [9. `default_proxy`](#9-default_proxy)
  - [10. `enable_multi_thread_reg`](#10-enable_multi_thread_reg)
  - [11. `reg_threads`](#11-reg_threads)
  - [12. `clash_proxy_pool`](#12-clash_proxy_pool)
  - [13. `warp_proxy_list`](#13-warp_proxy_list)
  - [14. `max_otp_retries`](#14-max_otp_retries)
  - [15. `use_proxy_for_email`](#15-use_proxy_for_email)
  - [16. `enable_email_masking`](#16-enable_email_masking)
  - [17. `token_output_dir`](#17-token_output_dir)
  - [18. `cpa_mode`](#18-cpa_mode)
  - [19. `normal_mode`](#19-normal_mode)
  - [20. Configuration suggestions](#20-configuration-suggestions)
- [Usage](#usage)
- [Running Mihomo / Clash on a server](#running-mihomo--clash-on-a-server)
- [Output Files](#output-files)
- [Troubleshooting](#troubleshooting)
- [Security Notes](#security-notes)

## Requirements

- Python 3.10+
- `PyYAML`
- `curl_cffi`
- `PySocks` (only needed if you want IMAP connections to go through a proxy)
- `requests` (used by `proxy_manager.py` for Clash controller access and proxy liveness checks)

## Installation

Install required packages:

```bash
pip install PyYAML PySocks curl_cffi requests
```

## Configuration

The project uses `config.yaml` in the repository root as the main configuration file.

A full example based on the current config template:

```yaml
# [Mailbox API mode selection]
# Optional values: "imap" / "freemail" / "cloudflare_temp_email" / "cloudmail" / "mail_curl"
email_api_mode: "cloudflare_temp_email"

# [Shared settings for cloudflare_temp_email / imap / cloudmail]
mail_domains: "domain1.com,domain2.xyz,domain3.net"
gptmail_base: "https://your-domain.com"

# [cloudflare_temp_email-specific]
admin_auth: ""

# [imap-specific]
imap:
  server: "imap.gmail.com"
  port: 993
  user: ""
  pass: ""

# [freemail-specific]
freemail:
  api_url: "https://your-domain.com"
  api_token: ""

# [cloudmail-specific]
cloudmail:
  api_url: "https://your-domain.com"
  admin_email: "admin@your-domain.com"
  admin_password: "your-admin-password"

# [mail_curl-specific]
mail_curl:
  api_base: "https://your-domain.com"
  key: ""

# [Proxy settings]
default_proxy: ""
enable_multi_thread_reg: false
reg_threads: 10

# [Clash proxy pool]
# fastest_mode: true means latency-based preferred selection;
# false means random blind selection. If one node is always the lowest latency,
# it may be selected repeatedly.
clash_proxy_pool:
  enable: false
  pool_mode: false
  api_url: "http://127.0.0.1:9097"
  fastest_mode: false
  group_name: "节点选择"
  secret: "set-your-secret"
  test_proxy_url: ""
  blacklist:
    - "自动"
    - "故障"
    - "剩余"
    - "到期"
    - "官网"
    - "Traffic"
    - "DIRECT"
    - "REJECT"
    - "港"
    - "HK"
    - "Hongkong"
    - "台"
    - "TW"
    - "Taiwan"
    - "中"
    - "CN"
    - "China"
    - "回国"

warp_proxy_list:
  - "http://127.0.0.1:41001"
  - "http://127.0.0.1:41002"
  - "http://127.0.0.1:41003"
  - "http://127.0.0.1:41004"
  - "http://127.0.0.1:41005"
  - "http://127.0.0.1:41006"
  - "http://127.0.0.1:41007"
  - "http://127.0.0.1:41008"
  - "http://127.0.0.1:41009"
  - "http://127.0.0.1:41010"

# [OTP resend retries]
max_otp_retries: 5

# [Mailbox-side proxy settings]
use_proxy_for_email: false
enable_email_masking: true

# [Token output directory]
token_output_dir: ""

# [CPA mode]
cpa_mode:
  enable: false
  save_to_local: true
  api_url: "http://your-domain.com:8317"
  api_token: "xxxx"
  min_accounts_threshold: 30
  batch_reg_count: 1
  min_remaining_weekly_percent: 80
  remove_on_limit_reached: false
  remove_dead_accounts: false
  enable_token_revive: false
  check_interval_minutes: 60
  threads: 10

# [Normal mode]
normal_mode:
  sleep_min: 5
  sleep_max: 30
  target_count: 2
```

### 1. `email_api_mode`

Selects which mailbox backend mode to use.

Supported values:
- `cloudflare_temp_email`
- `freemail`
- `imap`
- `cloudmail`
- `mail_curl`

Mode summary:
- **`cloudflare_temp_email`**: requires `gptmail_base` + `admin_auth`
- **`freemail`**: requires `freemail.api_url` + `freemail.api_token`
- **`imap`**: requires `imap.server / port / user / pass`
- **`cloudmail`**: requires `cloudmail.api_url / admin_email / admin_password`
- **`mail_curl`**: requires `mail_curl.api_base / key`

### 2. `mail_domains`

Defines the mailbox domain pool. Multiple domains can be separated with commas.

Example:

```yaml
mail_domains: "a.com,b.net,c.org"
```

Used by `cloudflare_temp_email`, `imap`, and `cloudmail` mailbox-generation flows.

### 3. `gptmail_base`

Base URL for the `cloudflare_temp_email` backend.

Example:

```yaml
gptmail_base: "https://mail-api.example.com"
```

Notes:
- do not include a trailing slash
- this should be the backend API address, not a frontend panel URL

### 4. `admin_auth`

Administrator credential for `cloudflare_temp_email` mode.

```yaml
admin_auth: "your_admin_secret"
```

### 5. `imap`

Used when `email_api_mode: "imap"`.

```yaml
imap:
  server: "imap.gmail.com"
  port: 993
  user: "your_mailbox@example.com"
  pass: "your_app_password"
```

Field notes:
- `server`: IMAP server address
- `port`: usually `993`
- `user`: mailbox login account
- `pass`: IMAP password or app password

### 6. `freemail`

Used when `email_api_mode: "freemail"`.

```yaml
freemail:
  api_url: "https://your-domain.com"
  api_token: ""
```

Field notes:
- `api_url`: Freemail-compatible API base URL
- `api_token`: Bearer token used for authentication

### 7. `cloudmail`

Used when `email_api_mode: "cloudmail"`.

```yaml
cloudmail:
  api_url: "https://your-domain.com"
  admin_email: "admin@your-domain.com"
  admin_password: "your-admin-password"
```

### 8. `mail_curl`

Used when `email_api_mode: "mail_curl"`.

```yaml
mail_curl:
  api_base: "https://your-domain.com"
  key: "your-api-key"
```

### 9. `default_proxy`

Global proxy address used for primary registration traffic.

Examples:

```yaml
default_proxy: "http://127.0.0.1:7897"
```

```yaml
default_proxy: "socks5://127.0.0.1:1080"
```

Leave empty if the runtime environment already has suitable direct connectivity.

### 10. `enable_multi_thread_reg`

Controls whether registration runs concurrently.

```yaml
enable_multi_thread_reg: false
```

- `false`: single-thread registration
- `true`: multi-thread registration using a thread pool

This applies to:
- normal registration mode
- CPA replenishment registration mode

### 11. `reg_threads`

Maximum concurrent registration thread count when multi-thread mode is enabled.

```yaml
reg_threads: 10
```

Recommendations:
- start with a small value
- scale up gradually based on proxy quality and mailbox throughput
- avoid aggressive values in single-port shared-proxy mode

### 12. `clash_proxy_pool`

Optional configuration block for automatic Clash / Mihomo node switching.

This block now includes `fastest_mode`, which controls whether node selection prefers the lowest-latency node or uses random blind selection.

```yaml
clash_proxy_pool:
  enable: false
  pool_mode: false
  api_url: "http://127.0.0.1:9097"
  fastest_mode: true
  group_name: "节点选择"
  secret: "set-your-secret"
  test_proxy_url: ""
  blacklist:
    - "港"
    - "HK"
    - "台"
    - "TW"
    - "中"
    - "CN"
```

Field notes:
- `enable`: whether to enable automatic Clash node switching
- `pool_mode`:
  - `false` = local single-instance / single-container mode
  - `true` = multi-container proxy-pool mode for concurrent registration
- `api_url`: Clash API address
- `fastest_mode`:
  - `true` = prefer latency-tested / fastest available node
  - `false` = random blind selection from the filtered node list
  - note: if node A is consistently the lowest-latency node, it may be selected repeatedly
- `group_name`: proxy-group keyword to locate the real selectable Clash group
- `secret`: controller authentication secret
- `test_proxy_url`: local proxy endpoint used for post-switch liveness verification
- `blacklist`: keywords used to filter out unwanted nodes

Behavior summary:
- queries the Clash controller for proxy groups
- fuzzy-matches the configured group name
- filters node candidates using the blacklist
- switches to a random valid node
- verifies region and liveness after switching

#### Multi-threaded proxy-pool explanation

This is the part commonly missed when reading the config:

- When `enable_multi_thread_reg: true` but `clash_proxy_pool.pool_mode: false`, registration workers still share one global proxy / controller context.
- When `enable_multi_thread_reg: true` **and** `clash_proxy_pool.pool_mode: true`, workers can pull different proxy endpoints from `warp_proxy_list`, which is the intended multi-threaded Clash proxy-pool mode.
- In pool mode, each local proxy endpoint is treated as an independent channel. The code also derives the controller port from the proxy port pattern, so a proxy like `41001` maps to an API port like `42001`.

Use cases:
- **Local single Clash client**: `enable: true`, `pool_mode: false`
- **Server-side multi-container concurrent mode**: `enable: true`, `pool_mode: true`, and fill `warp_proxy_list`

### 13. `warp_proxy_list`

List of local proxy endpoints used only in proxy-pool mode.

```yaml
warp_proxy_list:
  - "http://127.0.0.1:41001"
  - "http://127.0.0.1:41002"
  - "http://127.0.0.1:41003"
```

Important notes:
- only used when `clash_proxy_pool.enable: true` and `clash_proxy_pool.pool_mode: true`
- each endpoint should represent an independent outbound proxy/container
- if pool mode is disabled, this list is ignored

### 14. `max_otp_retries`

Maximum number of OTP resend / retry attempts.

```yaml
max_otp_retries: 5
```

### 15. `use_proxy_for_email`

Controls whether mailbox-side traffic also uses the configured proxy.

```yaml
use_proxy_for_email: false
```

- `false`: mailbox API / IMAP traffic is direct by default
- `true`: mailbox API / IMAP traffic also goes through the proxy

### 16. `enable_email_masking`

Controls whether mailbox domain information is masked in logs.

```yaml
enable_email_masking: true
```

- `true`: mask mailbox domain information in console output
- `false`: print the full mailbox address

### 17. `token_output_dir`

Controls where local token JSON files and `accounts.txt` are saved.

```yaml
token_output_dir: ""
```

- empty = save next to the script / current working directory
- set a path = save to the specified directory

### 18. `cpa_mode`

Optional CPA inventory inspection and replenishment configuration block.

```yaml
cpa_mode:
  enable: false
  save_to_local: true
  api_url: "http://your-domain.com:8317"
  api_token: "xxxx"
  min_accounts_threshold: 30
  batch_reg_count: 1
  min_remaining_weekly_percent: 80
  remove_on_limit_reached: false
  remove_dead_accounts: false
  enable_token_revive: false
  check_interval_minutes: 60
  threads: 10
```

Field notes:
- `enable`: master switch for CPA inspection / replenishment mode
- `save_to_local`: whether to keep local backup while uploading to CPA
- `api_url`: CPA API endpoint
- `api_token`: CPA API credential
- `min_accounts_threshold`: replenish when valid stock falls below this value
- `batch_reg_count`: number of accounts to register per replenishment cycle
- `min_remaining_weekly_percent`: weekly remaining-quota threshold
- `remove_on_limit_reached`: whether to physically delete exhausted accounts instead of disabling them
- `remove_dead_accounts`: whether to physically delete permanently dead accounts instead of disabling them
- `enable_token_revive`：reset tokens
- `check_interval_minutes`: CPA inspection interval
- `threads`: concurrent worker count for CPA health inspection

#### CPA multi-threaded inspection behavior

When `cpa_mode.enable: true`, the script can inspect CPA inventory concurrently.

- `cpa_mode.threads` controls inspection worker count
- each account can be checked, refreshed, disabled, or deleted independently
- replenishment registration can also run concurrently if registration multi-threading is enabled

This makes CPA mode not just a simple periodic check, but a configurable multi-threaded inspection and replenishment workflow.

### 19. `normal_mode`

Configuration for standard local registration mode.

```yaml
normal_mode:
  sleep_min: 5
  sleep_max: 30
  target_count: 2
```

Field notes:
- `sleep_min`: minimum cooldown between cycles
- `sleep_max`: maximum cooldown between cycles
- `target_count`: number of successful registrations to complete before stopping automatically

Rules:
- `target_count: 0` = run continuously until interrupted
- `target_count > 0` = stop automatically when the target is reached

### 20. Configuration suggestions

Typical combinations:

- **IMAP only**: `email_api_mode`, `mail_domains`, `imap`, `use_proxy_for_email`
- **Freemail only**: `email_api_mode`, `freemail.api_url`, `freemail.api_token`
- **Cloudflare temp mail only**: `email_api_mode`, `mail_domains`, `gptmail_base`, `admin_auth`
- **CloudMail only**: `email_api_mode`, `mail_domains`, `cloudmail`
- **Mail Curl only**: `email_api_mode`, `mail_curl`
- **Single local Clash client**: `default_proxy`, `clash_proxy_pool.enable: true`, `pool_mode: false`
- **Multi-threaded Clash proxy pool**: `enable_multi_thread_reg: true`, `clash_proxy_pool.enable: true`, `pool_mode: true`, and fill `warp_proxy_list`
- **CPA inspection mode**: enable the full `cpa_mode` block and tune `threads`, `min_accounts_threshold`, and deletion behavior

## Usage

Run normally:

```bash
python wfxl_openai_regst.py
```

## Running with Docker Compose

The repository also includes a ready-to-use `docker-compose.yml`:

```yaml
version: '3.8'

services:
  codex-web:
    image: wenfxl/wenfxl-codex-manager:v1.0
    container_name: wenfxl_codex_manager
    ports:
      - "8000:8000"
    restart: always
    extra_hosts:
      - "host.docker.internal:host-gateway"
    volumes:
      - ./config.yaml:/app/config.yaml
      - ./data:/app/data
```

### Docker deployment steps

1. Place `docker-compose.yml` and `config.yaml` in the same directory.
2. Start the container:

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

Notes:
- `./config.yaml:/app/config.yaml` means the container reads your host-side config directly.
- `./data:/app/data` is used to persist runtime output data.

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

- Do not expose `accounts.txt` or token JSON outputs publicly.
- Prefer stronger secret handling for mailbox admin credentials, CPA tokens, and Clash controller secrets.
- Restrict access to the output directory.
- If used in a team environment, add audit logging and permission boundaries.

## Author

- wenfxl
