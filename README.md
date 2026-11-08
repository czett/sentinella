# Sentinella

![Sentinella — SMA battery monitor for CCU3](docs/assets/readme-hero-banner.png)

Sentinella runs on a HomeMatic CCU3 or RaspberryMatic and polls an SMA Sunny Boy Storage or Sunny Tripower inverter every two minutes over the local WebConnect API. It writes `SV_Batterie_SoC` and `SV_Batterie_95_Signal`, publishes `status.json`, and serves a dashboard through the CCU3's native `lighttpd`.

Runtime code is pure POSIX shell. No daemon, Node.js, Python, `jq`, or external CDN is required on the CCU3.

Want to write a plugin? Read [**CONTRIBUTING.md**](CONTRIBUTING.md) for the `/bin/sh` standard, the three card layouts (`stat`, `delta`, `list`), and the security checklist.

## Capabilities

### Core monitoring

- Polls battery SoC (`6100_00295A00`) and optional charge/discharge power over HTTPS WebConnect.
- Drives `SV_Batterie_SoC` and `SV_Batterie_95_Signal` with configurable trigger and reset thresholds.
- Keeps the previous CCU3 value when the inverter returns `null`, an out-of-range value, or an HTTP error.
- Logs out in an `EXIT` trap and on `INT`, `TERM`, and `HUP`.
- Serialises runs with a PID lock that recovers stale locks.

### Plugins

| Plugin | Default | Data | Source |
|---|---|---|---|
| **CCU3 Health** (`10_ccu_health.sh`) | enabled | Duty cycle, load average, RAM, uptime | `tclrpc.so` XML-RPC on `127.0.0.1:2001`, fallback `2010`; `/proc` |
| **Weather** (`15_weather.sh`) | enabled | Temperature, humidity, WMO condition, wind, daily min/max | Open-Meteo, no API key |
| **PV Forecast** (`30_forecast_solar.sh`) | enabled | Today and tomorrow yield in `kWh` and `kWh/kWp` | forecast.solar, no API key |
| **Renault E-Tech** (`20_renault_zoe.sh`) | disabled until configured | SoC, range, plug status, charging state, odometer | MY Renault cloud; Megane, Zoe, R5 |

The plugin manager persists enable flags in `config/plugins.env` (`PLUGIN_<NAME>='0|1'`). Toggles in the settings drawer call `api/plugin_manager.cgi`, which accepts whitelisted IDs, writes the file atomically, and removes the card when a plugin is disabled. `web/app.js` filters the current card list immediately, without waiting for the next poll. Each plugin also honours a `.disabled` marker beside its script.

Poll-time plugin execution is gated by `SENTINELLA_RUN_PLUGINS=1` in the environment of the cron job. The installer runs each plugin once after deployment so the dashboard is populated before the next poll.

### Notifications and UI

- **Signal (CallMeBot):** detached alert delivery on threshold transitions, with a 60-minute per-event cooldown and DE/EN templates.
- **Swiss-Poster dashboard:** zero-dependency CSS grid, light/dark theme, tabular monospace figures, charging animation, and instant DE/EN switching without a page reload or polling delay.

## Architecture

- Runs on stock CCU3 or RaspberryMatic firmware with `/bin/sh` (BusyBox `ash`), `curl`, `sed`, `awk`, and `lighttpd`.
- No Python, Node.js, `jq`, compiled binary, or external CDN on the CCU3.
- Root-only backend (`$ADDON_DIR`, mode `700`) and a separate `lighttpd` document root (`$WEB_DIR`, mode `755`).
- CGI endpoints run on demand; there is no long-running process between polls.

![SMA inverter over HTTPS, two-minute cron job, CCU3 system variables, and Signal alert](docs/assets/feature-diagram-telemetry.png)

### Directory separation

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ HomeMatic CCU3 / RaspberryMatic (Buildroot, BusyBox ash, lighttpd)           │
├──────────────────────────────────────┬───────────────────────────────────────┤
│ Root Backend ($ADDON_DIR, mode 700)  │ Isolated Web Root ($WEB_DIR, mode 755)│
│ /usr/local/addons/sentinella/        │ /usr/local/etc/config/addons/www/     │
│                                      │   sentinella/                         │
│ ├── bin/check_soc.sh (cron */2 min)  │ ├── index.html, style.css, app.js     │
│ ├── lib/notify_signal.sh (detached)  │ ├── status.json (atomic snapshot)     │
│ ├── plugins.d/                       │ └── api/                              │
│ │   ├── 10_ccu_health.sh             │     ├── config.cgi & settings.cgi     │
│ │   ├── 15_weather.sh                │     ├── plugin_manager.cgi            │
│ │   ├── 20_renault_zoe.sh            │     ├── plugins.cgi (JSON validator)  │
│ │   └── 30_forecast_solar.sh         │     ├── action_zoe.cgi (HVAC off)     │
│ ├── config/ (mode 600 secrets)       │     ├── test_signal.cgi               │
│ │   ├── config.env                   │     ├── test_renault.cgi              │
│ │   ├── plugins.env (enable flags)   │     └── update_check.cgi              │
│ │   └── plugins/*.env                │                                       │
│ └── state/ (mode 700 private state)  │  Swiss-Poster cards:                  │
│     ├── plugins/*.json               │   [ stat ]   [ delta ]   [ list ]     │
│     └── .renault_session (mode 600)  │                                       │
└──────────────────────────────────────┴───────────────────────────────────────┘
```

### How a poll works

1. `bin/check_soc.sh` loads `.env`, validates parameters, and acquires the lock in `state/`.
2. It logs in to the inverter (`POST /dyn/login.json`, credentials piped through `stdin`), reads the SoC and power channels, and logs out (`POST /dyn/logout.json`).
3. A reading that is not an integer from `0` to `100` exits `1` and leaves the CCU3 variables untouched.
4. `SV_Batterie_95_Signal` turns `true` at `THRESHOLD_TRIGGER` (default `95`), turns `false` at `THRESHOLD_RESET` (default `90`), and keeps its previous state inside the dead band.
5. Both variables are written in one `tclrega.exe` call. `status.json` is published atomically, and a threshold edge dispatches `lib/notify_signal.sh` in the background.
6. When `SENTINELLA_RUN_PLUGINS=1`, enabled plugins run after the status write. Each plugin writes one JSON card to `state/plugins/`.

![BusyBox ash, no external CDNs, served by lighttpd](docs/assets/feature-card-native-ccu.png)

### Hardware corridor

![Hardware utilization corridor: install, RAM, execution time, and radio duty cycle](docs/assets/hardware_footprint.svg)

Measured with `BENCHMARK_RUNS=20 ./tools/benchmark_footprint.sh` on the development host. Latency figures cover local dry-run and mock-plugin runs; they exclude network I/O and CCU3 flash write time.

| Metric | Measured / bound | Guard | Status |
| :--- | :---: | :---: | :---: |
| **Release tarball** (`sentinella-<version>.tar.gz`) | **90 KB** (91,774 B) | informational | OK |
| **Installed add-on footprint** | **207 KB** (211,657 B) | `<= 250 KB` | **PASS** |
| **Core execution** (`bin/check_soc.sh --dry-run`) | **17 ms** (20-run average) | `<= 500 ms` | **PASS** |
| **Plugin execution** (`plugins.d/20_renault_zoe.sh`) | **23 ms** (20-run average) | `<= 500 ms` | **PASS** |
| **Peak process RSS** | **~1.2 MB** on CCU3 BusyBox ash (< 0.2% of 1 GB) | `<= 1.2 MB` | **PASS** |
| **Radio / RF duty cycle** | **0%** (HTTPS and local XML-RPC only) | `0%` | **PASS** |

The corridor diagram is deliberately bound-based. Point-in-time byte counts belong in the table above, not in a graphic that outlives the release.

### Plugin contract and card layouts

A plugin in `plugins.d/` reads optional configuration from `config/plugins/<name>.env` (mode `600`), runs once, and atomically writes one JSON card to `state/plugins/<id>.json`. `web/api/plugins.cgi` validates every file with a strict RFC 8259 parser before streaming the array to the browser.

Every card shares `id`, `layout`, `title`, `status_badge`, `status_color` (`green`, `blue`, `red`, or `neutral`), and `timestamp`. Three layouts are supported:

| Layout | Fields | Typical use |
|---|---|---|
| `stat` | `primary: {value, unit, label}`, `secondary_metrics[]` | EV SoC and range, battery power, temperatures |
| `delta` | `primary`, `delta: {direction, value, comparison}`, `secondary_metrics[]` | Solar yield prognosis, dynamic tariffs |
| `list` | `secondary_metrics[]` | Wallbox phases, heat pump registers, multi-row device status |

## Requirements

- **CCU3 / RaspberryMatic:** ReGaHSS on port `8181`, root SSH access or the WebUI add-on installer, and `<= 250 KB` free under `/usr/local/`.
- **SMA inverter:** HTTPS WebConnect reachable from the CCU3, a read-capable WebUI password (`usr` or `user`), and battery channel `6100_00295A00` (see [docs/PROTOCOL.md](docs/PROTOCOL.md)).
- **Workstation** for `deploy.sh`, `tools/build_addon.sh`, and tests: `sh`/`bash`, `ssh`, `tar`, `curl`, `openssl`, Python 3.8+.

## Installation

### Option A: CCU3 release package (`sentinella-<version>.tar.gz`)

Build the HomeMatic add-on archive, including `update_script` and `rc.d/sentinella`:

```sh
./tools/build_addon.sh
# Outputs: dist/sentinella-1.1.0.tar.gz
```

Upload the archive in the CCU3 WebUI under **Settings → Control Panel → Additional Software**, or deploy over SSH with `./deploy.sh`.

### Option B: SSH deploy

```sh
git clone https://github.com/czett/sentinella.git
cd sentinella
cp .env.example .env
chmod 600 .env
${EDITOR:-vi} .env
```

Set at least `SMA_IP` and `SMA_PASS`, then deploy:

```sh
./deploy.sh -H 192.168.178.10
```

`deploy.sh` copies the add-on to `/usr/local/addons/sentinella`, installs the UI under `/usr/local/etc/config/addons/www/sentinella/`, sets permissions (`600` on `.env` and plugin configs, `700` on private directories, `755` on scripts and CGI endpoints), registers the two-minute cron job, creates both system variables, and runs an initial check.

Options:

```sh
./deploy.sh --dry-run                  # preview actions without changing anything
./deploy.sh -H ccu3.local --skip-vars  # skip system variable creation
./deploy.sh -H ccu3.local --skip-cron  # skip crontab installation
```

The cron entry (`crontab -e`, see [`examples/crontab.txt`](examples/crontab.txt)):

```
*/2 * * * * /usr/local/addons/sentinella/bin/check_soc.sh >/dev/null 2>&1
```

## Dashboard and settings

Open `http://<ccu-ip>/addons/sentinella/` (`?demo=1` loads preview data locally).

The dashboard refreshes `status.json` every 30 seconds and shows SoC telemetry, hysteresis state, history, and plugin cards in section **05 Extensions**. The settings drawer manages thresholds, Signal onboarding, inverter credentials, Renault access, and plugin toggles. Language changes apply immediately and persist through `api/config.cgi`.

`status.json` schema:

```json
{
  "schema": 1,
  "ui_language": "en",
  "generated_at": "2026-11-08T12:00:00Z",
  "generated_epoch": 1762603200,
  "host": "192.168.178.123",
  "soc": 91,
  "previous_soc": 92,
  "delta": -1,
  "charging": "discharging",
  "charge_power_w": 0,
  "discharge_power_w": 620,
  "signal": true,
  "signal_state": "held",
  "signal_notifications": true,
  "thresholds": { "trigger": 95, "reset": 90 },
  "variables": { "soc": "SV_Batterie_SoC", "signal": "SV_Batterie_95_Signal" },
  "ccu_status": "ok",
  "history": [{ "t": 1762603200, "soc": 91 }]
}
```

## Switching logic

![Thresholds: off at 90 %, on at 95 %, previous state kept in between](docs/assets/feature-diagram-hysteresis.png)

```
SoC
100 ┤
 95 ┤──────────── trigger ────────────  → true
 92 ┤            kept                     (previous state unchanged)
 90 ┤──────────── reset ──────────────  → false
  0 ┤
```

If `state/signal` is missing and the first reading lands between `THRESHOLD_RESET` and `THRESHOLD_TRIGGER`, `SV_Batterie_95_Signal` is left untouched until a threshold is crossed.

| Condition | `SV_Batterie_SoC` | `SV_Batterie_95_Signal` | Exit code |
|---|---|---|---|
| Valid `0..100` | updated | updated or kept | `0` |
| `null`, missing, or out of range | unchanged | unchanged | `1` |
| Inverter or ReGaHSS HTTP error | unchanged | unchanged | `1` |
| Another poll still running | unchanged | unchanged | `0` |

## Configuration (`.env`)

### Inverter

| Key | Default | Description |
|---|---|---|
| `SMA_IP` | — | Inverter IPv4 address or hostname |
| `SMA_PORT` | `443` | HTTPS port |
| `SMA_ROLE` | `usr` | Login role (`usr` or `user`) |
| `SMA_PASS` | — | Inverter WebUI password |
| `BATTERY_SOC_KEY` | `6100_00295A00` | Battery SoC channel key |
| `BATTERY_CHARGE_POWER_KEY` | `6100_00417800` | Charge power key (empty disables) |
| `BATTERY_DISCHARGE_POWER_KEY` | `6100_00417900` | Discharge power key (empty disables) |
| `SMA_TLS_VERIFY` | `false` | Verify the inverter certificate (`false` uses `curl -k`) |
| `SMA_CONNECT_TIMEOUT` | `5` | TCP connect timeout (s) |
| `SMA_MAX_TIME` | `15` | Total request timeout (s) |
| `SMA_RETRIES` | `2` | Login/read/logout attempts |
| `SMA_RETRY_DELAY` | `5` | Delay between retries (s) |

### CCU3 and thresholds

| Key | Default | Description |
|---|---|---|
| `CCU_IP` | `127.0.0.1` | CCU3 address |
| `CCU_REGA_PORT` | `8181` | ReGaHSS HTTP port |
| `SV_SOC_NAME` | `SV_Batterie_SoC` | Numeric system variable (`0..100 %`) |
| `SV_SIGNAL_NAME` | `SV_Batterie_95_Signal` | Boolean system variable |
| `THRESHOLD_TRIGGER` | `95` | Set the signal `true` at or above this SoC |
| `THRESHOLD_RESET` | `90` | Set the signal `false` at or below this SoC |
| `UI_LANGUAGE` | `en` | `en` or `de` for the UI, plugin cards, and default Signal texts |

### Signal notifications

Sentinella uses the [CallMeBot Signal API](https://www.callmebot.com/blog/free-api-signal-send-messages/):

1. Send `I allow callmebot to send me messages` to CallMeBot on Signal.
2. Paste the reply into the **CallMeBot Onboarding Message** field in the settings drawer. Sentinella extracts the Signal Account UUID and API key.

Test delivery without changing battery state:

```sh
/usr/local/addons/sentinella/lib/notify_signal.sh --test; echo "exit=$?"
```

## Plugin configuration

Plugins are optional. An unconfigured plugin exits `0` before any network request. Configuration lives in `config/plugins/<name>.env` (mode `600`). Enable flags live in `config/plugins.env`.

### CCU3 Health

Enabled by default and local-only. Reads the BidCoS duty cycle through `tclrpc.so` XML-RPC on port `2001`, with a fallback to HmIP port `2010`; reads load, RAM, and uptime from `/proc`. If neither XML-RPC endpoint answers, it tries `CCU_SYSINFO_BIN` (default `/bin/hm_sysinfo`) and then `CCU_DUTYCYCLE_FILE` (defaults `/var/status/dutycycle`, `/tmp/dutycycle`, `/var/run/dutycycle`).

The status badge follows the duty cycle: below `50%` OPTIMAL, `50–80%` WARNING, above `80%` CRITICAL.

### Weather (Open-Meteo)

No API key. Set `WEATHER_LAT` and `WEATHER_LON` in `config/plugins/weather.env`. Without that file, the plugin falls back to the forecast coordinates or `51.51, 7.46`.

### PV forecast (forecast.solar)

No API key. Override `FORECAST_LAT`, `FORECAST_LON`, `FORECAST_DEC`, `FORECAST_AZ`, and `FORECAST_KWP` in `config/plugins/forecast_solar.env`.

### Renault E-Tech

Read-only telemetry for MY Renault E-Tech models, including Megane, Zoe, and R5. Set `RENAULT_USER`, `RENAULT_PASS`, and `RENAULT_VIN` in the settings drawer or in `config/plugins/renault.env` (mode `600`). The plugin caches the Gigya `id_token` and Kamereon `accountId` in `state/plugins/.renault_session` for six hours and refreshes them on `401`/`403`.

```sh
/usr/local/addons/sentinella/plugins.d/20_renault_zoe.sh --test; echo "exit=$?"
```

The card reports battery SoC, remaining range, plug status, charging state, and odometer when the cockpit endpoint returns it. Remote HVAC actions (`--start-hvac`, `--stop-hvac`, `api/action_zoe.cgi`) currently return `HVAC feature temporarily disabled`; the plug-in is read-only.

## Writing a plugin

See [**CONTRIBUTING.md**](CONTRIBUTING.md) for the full developer guide, i18n rules, and security requirements.

A minimal POSIX shell plugin that publishes a `stat` card atomically:

```sh
#!/bin/sh
umask 077
APP_DIR=$(cd "$(dirname "$0")/.." && pwd)
OUT="$APP_DIR/state/plugins/example.json"
mkdir -p "${OUT%/*}"
cat > "$OUT.tmp.$$" <<'EOF'
{"id":"example","layout":"stat","title":"WALLBOX","status_badge":"READY","status_color":"green","primary":{"value":11.0,"unit":"kW","label":"CHARGING POWER"},"secondary_metrics":[{"label":"PHASES","value":"3"},{"label":"CONNECTOR","value":"Connected"}]}
EOF
chmod 644 "$OUT.tmp.$$" && mv -f "$OUT.tmp.$$" "$OUT"
```

Save it as `plugins.d/40_example.sh`, run `chmod 755 plugins.d/40_example.sh`, execute it once, and the card appears in section **05 Extensions**.

## Tests, benchmarks, and packaging

```sh
# Syntax checks
sh -n bin/check_soc.sh lib/notify_signal.sh setup_variables.sh \
      plugins.d/10_ccu_health.sh plugins.d/15_weather.sh \
      plugins.d/20_renault_zoe.sh plugins.d/30_forecast_solar.sh \
      web/api/config.cgi web/api/plugin_manager.cgi web/api/plugins.cgi \
      web/api/action_zoe.cgi web/api/update_check.cgi \
      tools/benchmark_footprint.sh tools/build_addon.sh
node --check web/app.js

# Footprint and latency budgets
./tools/benchmark_footprint.sh

# CCU3 release archive (dist/sentinella-<version>.tar.gz)
./tools/build_addon.sh

# Integration and security suite (SEN-01..SEN-11, plugins, packaging)
python3 tests/integration.py
```

## Troubleshooting

See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) and [docs/PROTOCOL.md](docs/PROTOCOL.md).

## License

MIT — see [LICENSE](LICENSE).
