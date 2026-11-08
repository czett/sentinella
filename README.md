# Sentinella

![Sentinella — SMA Battery Monitor for CCU3](docs/assets/readme-hero-banner.png)

Sentinella queries an SMA Sunny Tripower or Sunny Boy Storage inverter every two minutes over its local WebConnect API, updates two HomeMatic CCU3 / RaspberryMatic system variables (`SV_Batterie_SoC` and `SV_Batterie_95_Signal`), and turns the CCU3 into an active energy and EV telemetry controller.

👉 **Want to write a plugin or contribute?** Read [**CONTRIBUTING.md**](CONTRIBUTING.md) for the pure POSIX `/bin/sh` standard, the 3 card archetypes (`stat`, `delta`, `list`), and the security checklist.

## Feature Highlights

- **SMA Inverter Core & Hysteresis Switch** — Polls battery State of Charge (`6100_00295A00`) and charge/discharge power over HTTPS WebConnect, drives `SV_Batterie_SoC` and `SV_Batterie_95_Signal` with configurable trigger/reset thresholds, and guarantees session logout via POSIX signal traps.
- **Renault Zoe Live-Telemetrie + HVAC Remote Control** — Queries MY Renault (Gigya + Kamereon cloud API) with a 6-hour cached session token (`state/plugins/.renault_session`) for live battery SoC, remaining range, plug status, and charging state, plus one-click remote pre-conditioning (`--start-hvac` / `api/action_zoe.cgi` to heat/cool to 21 °C).
- **Solar-Forecast (`forecast.solar`)** — Zero-API-key daily and next-day PV yield prognosis (`kWh` and `kWh/kWp` peak ratio) with automatic weather badge classification (`SUNNY` / `MODERATE` / `CLOUDY`).
- **Signal Notifications (CallMeBot)** — Detached background alert delivery on threshold transitions with 60-minute per-event cooldown, localized DE/EN templates, and one-paste onboarding in the WebUI settings drawer.
- **Swiss-Poster Design System & Real-Time i18n** — Zero-dependency light/dark CSS grid with tabular monospace hero figures, charging pulse animations (`.is-charging`), and instant bidirectional **DE ↔ EN** switching (`translatePluginText`) without page reload or polling delay.

## Architecture

- Runs natively on stock CCU3 or RaspberryMatic firmware using `/bin/sh` (BusyBox `ash`), `curl`, `sed`, `awk`, and `lighttpd`.
- Zero external runtime dependencies: no Python, no Node.js, no `jq`, and no external CDNs on the CCU3.
- Strict privilege and directory separation between the root-only backend (`$ADDON_DIR`, mode `700`) and the isolated `lighttpd` document root (`$WEB_DIR`, mode `755`).

![SMA inverter over HTTPS, two-minute cron job, CCU3 system variables, and Signal alert](docs/assets/feature-diagram-telemetry.png)

### System & Directory Separation

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
│ │   ├── 20_renault_zoe.sh (--start-hvac)   ├── config.cgi & settings.cgi     │
│ │   └── 30_forecast_solar.sh         │     ├── plugins.cgi (JSON validator)  │
│ ├── config/ (mode 600 secrets)       │     ├── action_zoe.cgi (60s lock)     │
│ │   ├── config.env                   │     ├── test_signal.cgi               │
│ │   └── plugins/*.env                │     ├── test_renault.cgi              │
│ └── state/ (mode 700 private state)  │     └── update_check.cgi              │
│     ├── plugins/*.json               │                                       │
│     └── .renault_session (mode 600)  │  Swiss-Poster UI Layout Cards:        │
│                                      │   [ stat ]    [ delta ]    [ list ]   │
└──────────────────────────────────────┴───────────────────────────────────────┘
```

### How a poll works

1. `bin/check_soc.sh` loads `.env`, validates parameters, and acquires an atomic lock directory in `state/` (automatically recovering stale PIDs).
2. It logs into the inverter (`POST /dyn/login.json` with credentials piped via `stdin`), reads battery SoC (`6100_00295A00`) and charge/discharge power channels, and logs out (`POST /dyn/logout.json`).
3. If the reading is not an integer between `0` and `100`, the run exits `1` and leaves CCU3 variables untouched.
4. `SV_Batterie_95_Signal` turns `true` at `THRESHOLD_TRIGGER` (default `95%`), turns `false` at `THRESHOLD_RESET` (default `90%`), and preserves its previous state inside the deadband.
5. Both variables are written in a single `tclrega.exe` call, `status.json` is published atomically, and on a threshold edge `lib/notify_signal.sh` dispatches detached in the background.

![BusyBox ash, under 75 KB compressed, no external CDNs, served by lighttpd](docs/assets/feature-card-native-ccu.png)

### Verified Footprint & Performance Benchmarks

![System Footprint](docs/assets/feature-card-footprint.png)

Verified via [`tools/benchmark_footprint.sh`](tools/benchmark_footprint.sh):

| Metric / Target | Verified Value | Budget Guard | Status |
| :--- | :---: | :---: | :---: |
| **Release Tarball Size** (`sentinella-<version>.tar.gz`) | **~69 KB** (73 KB w/ HVAC & packaging) | Informational | OK |
| **Installed Add-on Footprint** (`/usr/local/addons/sentinella/`) | **~143 KB** (158 KB w/ all CGIs) | `<= 250 KB` | **PASS** |
| **Core Execution Latency** (`bin/check_soc.sh --dry-run`) | **~47 ms** (16–47 ms avg) | `<= 500 ms` | **PASS** |
| **Plugin Execution Latency** (`plugins.d/20_renault_zoe.sh`) | **~57 ms** (22–57 ms avg) | `<= 500 ms` | **PASS** |
| **Resident RAM** | **0 MB** (stateless cron + CGI) | `0 MB` | **PASS** |

### Plugin Contract & Card Archetypes

A plugin in `plugins.d/` reads optional config from `config/plugins/<name>.env` (mode `600`), runs once, and atomically writes one JSON card to `state/plugins/<id>.json`. `web/api/plugins.cgi` validates every file with a strict RFC 8259 parser before streaming the array to the browser.

Every card shares `id`, `layout`, `title`, `status_badge`, `status_color` (`green`, `blue`, `red`, or `neutral`), and `timestamp`. Three Swiss-Poster layout archetypes are supported:

| Layout | Fields | Typical Use |
|---|---|---|
| `stat` | `primary: {value, unit, label}`, `secondary_metrics[]` | EV SoC & range (`20_renault_zoe.sh`), battery power, temperatures |
| `delta` | `primary`, `delta: {direction, value, comparison}`, `secondary_metrics[]` | Solar yield prognosis (`30_forecast_solar.sh`), dynamic tariffs |
| `list` | `secondary_metrics[]` | Wallbox phases, heat pump registers, multi-row device status |

## Requirements

- **CCU3 / RaspberryMatic**: ReGaHSS on port `8181`, root SSH access (or WebUI Add-on installer), and `<= 250 KB` free under `/usr/local/`.
- **SMA inverter**: HTTPS WebConnect reachable from the CCU3, read-capable WebUI password (`usr` or `user`), and battery channel `6100_00295A00` (see [docs/PROTOCOL.md](docs/PROTOCOL.md)).
- **Workstation** (for `deploy.sh`, `tools/build_addon.sh`, and tests): `sh`/`bash`, `ssh`, `tar`, `curl`, `openssl`, Python 3.8+.

## Installation

### Option A: Official CCU3 Release Package (`sentinella-<version>.tar.gz`)

Build the HomeMatic CCU3 / RaspberryMatic add-on archive (including `update_script` and `rc.d/sentinella`):

```sh
./tools/build_addon.sh
# Outputs: dist/sentinella-1.1.0.tar.gz
```

Upload `dist/sentinella-<version>.tar.gz` in the CCU3 WebUI under **Settings → Control Panel → Additional Software**, or deploy directly over SSH with `./deploy.sh`.

### Option B: Quick SSH Deploy (`deploy.sh`)

```sh
git clone https://github.com/czett/sentinella.git
cd sentinella
cp .env.example .env
chmod 600 .env
${EDITOR:-vi} .env
```

Set at least `SMA_IP` and `SMA_PASS` in `.env`, then deploy to the CCU3:

```sh
./deploy.sh -H 192.168.178.10
```

`deploy.sh` copies the add-on to `/usr/local/addons/sentinella`, installs the web UI under `/usr/local/etc/config/addons/www/sentinella/`, enforces strict permissions (`600` on `.env` and plugin configs, `700` on private directories, `755` on scripts and CGI endpoints), registers the two-minute cron job, creates `SV_Batterie_SoC` and `SV_Batterie_95_Signal`, and runs an initial check.

Options:

```sh
./deploy.sh --dry-run                  # preview actions without changing anything
./deploy.sh -H ccu3.local --skip-vars  # skip system variable creation
./deploy.sh -H ccu3.local --skip-cron  # skip crontab installation
```

Add the cron entry (`crontab -e`, see [`examples/crontab.txt`](examples/crontab.txt)):

```
*/2 * * * * /usr/local/addons/sentinella/bin/check_soc.sh >/dev/null 2>&1
```

## Dashboard & Settings UI

Open `http://<ccu-ip>/addons/sentinella/` (`?demo=1` loads preview data when testing locally).

The Web UI is a Swiss-Poster dashboard served by the CCU3's native `lighttpd`. It refreshes `status.json` every 30 seconds and provides live SoC telemetry, hysteresis switch status, a light/dark theme toggle, and an **EN / DE** language toggle (persisted to `.env` and `config/config.env` via `api/config.cgi` with instant in-DOM translation of all plugin cards). The settings drawer manages thresholds, CallMeBot Signal onboarding, inverter credentials, and Renault Zoe access. Plugin cards render in section **05 Extensions**.

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

## Switching Logic

![Thresholds: off at 90 %, on at 95 %, previous state kept in between](docs/assets/feature-diagram-hysteresis.png)

```
SoC
100 ┤
 95 ┤──────────── trigger ────────────  → true
 92 ┤            kept                     (previous state unchanged)
 90 ┤──────────── reset ──────────────  → false
  0 ┤
```

- If `state/signal` is missing and the first reading lands between `THRESHOLD_RESET` and `THRESHOLD_TRIGGER`, `SV_Batterie_95_Signal` is left untouched until a threshold is crossed.

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
| `SMA_TLS_VERIFY` | `false` | Verify inverter TLS certificate (`false` passes `curl -k`) |
| `SMA_CONNECT_TIMEOUT` | `5` | TCP connect timeout (s) |
| `SMA_MAX_TIME` | `15` | Total request timeout (s) |
| `SMA_RETRIES` | `2` | Login/read/logout attempts |
| `SMA_RETRY_DELAY` | `5` | Delay between retries (s) |

### CCU3 & Thresholds

| Key | Default | Description |
|---|---|---|
| `CCU_IP` | `127.0.0.1` | CCU3 address |
| `CCU_REGA_PORT` | `8181` | ReGaHSS HTTP port |
| `SV_SOC_NAME` | `SV_Batterie_SoC` | Numeric system variable (`0..100 %`) |
| `SV_SIGNAL_NAME` | `SV_Batterie_95_Signal` | Boolean system variable |
| `THRESHOLD_TRIGGER` | `95` | Turn signal `true` at or above this SoC |
| `THRESHOLD_RESET` | `90` | Turn signal `false` at or below this SoC |
| `UI_LANGUAGE` | `en` | `en` or `de` for UI, plugin cards, and default Signal texts |

### Signal Notifications

#### CallMeBot setup

Sentinella uses the [CallMeBot Signal API](https://www.callmebot.com/blog/free-api-signal-send-messages/) to deliver alerts:

1. Send `I allow callmebot to send me messages` to CallMeBot on Signal.
2. Paste the reply into the **CallMeBot Onboarding Message** field in Sentinella's Settings drawer. Sentinella extracts your Signal Account UUID and API key automatically.

Test Signal delivery from the shell without altering battery state:

```sh
/usr/local/addons/sentinella/lib/notify_signal.sh --test; echo "exit=$?"
```

## Plugin Configuration & Active Control

Plugins are optional. Unconfigured plugins exit `0` before any network request. Configuration is stored in `config/plugins/<name>.env` (mode `600`).

### Renault Zoe (Telemetry + Remote HVAC Pre-Conditioning)

Set `RENAULT_USER`, `RENAULT_PASS`, and `RENAULT_VIN` in the Settings drawer under **RENAULT ZOE**, or in `config/plugins/renault.env` (mode `600`). The plugin caches the Gigya `id_token` and Kamereon `accountId` in `state/plugins/.renault_session` for 6 hours and refreshes them automatically on `401`/`403`.

- **Connection test:**
  ```sh
  /usr/local/addons/sentinella/plugins.d/20_renault_zoe.sh --test; echo "exit=$?"
  ```
- **Remote HVAC Pre-Conditioning (21 °C):**
  Trigger pre-heating/cooling directly from the **KLIMATISIERUNG STARTEN / START PRE-CONDITIONING** button on the Renault Zoe dashboard card, via CLI, or from a CCU3 program:
  ```sh
  # CLI trigger
  /usr/local/addons/sentinella/plugins.d/20_renault_zoe.sh --start-hvac

  # HTTP CGI trigger (POST, 60-second cooldown lock in state/.zoe_hvac.lock)
  curl -sS -X POST -H "Content-Type: application/json" \
    -H "X-Requested-With: XMLHttpRequest" \
    -d '{"action":"start_hvac"}' \
    http://127.0.0.1/addons/sentinella/api/action_zoe.cgi
  ```

### forecast.solar

No API key is required. Override latitude (`FORECAST_LAT`), longitude (`FORECAST_LON`), roof declination (`FORECAST_DEC`), azimuth (`FORECAST_AZ`), and peak power (`FORECAST_KWP`) in `config/plugins/forecast_solar.env`.

## Developer Quickstart: Writing a Custom Plugin

See [**CONTRIBUTING.md**](CONTRIBUTING.md) for the full developer guide, i18n rules, and security requirements.

A minimal 10-line POSIX shell plugin that publishes a live `stat` card atomically:

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

Save as `plugins.d/40_example.sh`, run `chmod 755 plugins.d/40_example.sh`, execute it once, and the card appears in section **05 Extensions**.

## Tests, Benchmarks & Packaging

```sh
# Syntax checks
sh -n bin/check_soc.sh lib/notify_signal.sh setup_variables.sh \
      plugins.d/20_renault_zoe.sh plugins.d/30_forecast_solar.sh \
      web/api/config.cgi web/api/action_zoe.cgi web/api/update_check.cgi \
      tools/benchmark_footprint.sh tools/build_addon.sh
node --check web/app.js

# Footprint & latency budget verification
./tools/benchmark_footprint.sh

# Build CCU3 release archive (dist/sentinella-<version>.tar.gz)
./tools/build_addon.sh

# Full integration & security test suite (SEN-01..SEN-11, plugins, HVAC, packaging)
python3 tests/integration.py
```

## Troubleshooting

See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) and [docs/PROTOCOL.md](docs/PROTOCOL.md) for detailed diagnostics.

## License

MIT — see [LICENSE](LICENSE).
