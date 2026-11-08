# Contributing to Sentinella

Sentinella runs on stock HomeMatic CCU3 and RaspberryMatic firmware with tight flash and RAM budgets. Contributions are reviewed for portability, security, and failure handling before anything else.

## 1. Runtime constraints

- **No external runtime:** Python, Node.js, `jq`, and compiled binaries are not available on the CCU3. Do not add them.
- **No daemon:** polling runs from `crond`; CGI endpoints run on demand behind the native `lighttpd`.
- **No build step on the CCU3:** the WebUI is vanilla HTML5, CSS3, and ES6 (`web/index.html`, `web/style.css`, `web/app.js`), with no CDN, web font, or framework.
- **Fail safe:** a timeout, network error, or malformed upstream response must never overwrite the last valid `SV_Batterie_SoC` / `SV_Batterie_95_Signal` value or delete an existing card.

## 2. Pure POSIX `/bin/sh` standard (BusyBox `ash`)

All scripts in `bin/`, `lib/`, `plugins.d/`, `web/api/`, and `tools/benchmark_footprint.sh` must run under `/bin/sh` (BusyBox `ash` and `dash`):

- **No bashisms:** no `local`, arrays, `[[ ... ]]`, `(( ... ))`, `source` (use `.`), process substitution `<(...)`, `read -p`, `date -d`, or `${var//pat/rep}`.
- **Standard userland only:** `/bin/sh`, `curl`, `sed`, `grep`, `awk`, `tr`, `cut`, `head`, `tail`, `date`, `mv`, `cp`, `rm`, `mkdir`, `chmod`, `stat`, and `logger`.
- **Quoting:** prefer `printf` for variable data. Quote every parameter expansion (`"$var"`) unless word splitting is intended.
- **Session hygiene:** any path that opens an authenticated session must register an `EXIT` / signal `trap` that logs out and removes temporary files and lock directories.
- **Atomic writes:** never write a live state, config, or JSON file directly. Write `$OUT_FILE.tmp.$$` in the same directory and replace the target with `mv -f`.

## 3. Plugin contract

Plugins live in `plugins.d/` (mode `755`). Each plugin runs independently, reads optional configuration from `config/plugins/<name>.env`, and atomically writes one JSON object to `state/plugins/<id>.json`.

### `/state/plugins/*.json` schema

`web/api/plugins.cgi` validates each file with a strict RFC 8259 object validator and streams the array to `web/app.js`. Every card emits:

```json
{
  "id": "plugin_identifier",
  "layout": "stat",
  "title": "PLUGIN TITLE",
  "status_badge": "READY",
  "status_color": "green",
  "timestamp": "2026-11-08T12:00:00Z",
  "primary": {
    "value": 78,
    "unit": "%",
    "label": "BATTERY SOC"
  },
  "delta": {
    "direction": "up",
    "value": "+3.2 kWh",
    "comparison": "vs yesterday"
  },
  "secondary_metrics": [
    { "label": "RANGE", "value": "214 km" },
    { "label": "CONNECTOR", "value": "Connected" }
  ]
}
```

- `status_color` must be `"green"`, `"blue"`, `"red"`, or `"neutral"`.
- An unconfigured plugin exits `0` without writing a card or logging an error. With `--test`, exit `2` for missing credentials and `1` for a connection failure.
- The plugin manager stores enable flags in `config/plugins.env`. Read the matching `PLUGIN_<NAME>` flag before doing work, and exit `0` when it is `0`. Also honour a `.disabled` marker beside the script.
- A disabled plugin removes its card so the dashboard reflects the change immediately.

### The three layouts

1. **`stat`** — hero metric plus rows. Requires `primary: {value, unit, label}` and optional `secondary_metrics: [{label, value, unit?}]`. Use for state of charge, live power, or single-device telemetry.
2. **`delta`** — hero metric plus trend. Requires `primary`, `delta: {direction: "up"|"down"|"neutral", value, comparison}`, and optional `secondary_metrics`. Use for forecasts, day-over-day comparisons, and tariffs.
3. **`list`** — key-value ledger. Uses `secondary_metrics: [{label, value, unit?}]`. Use for wallboxes, heat pumps, and multi-row device status.

### i18n (DE / EN)

Every plugin supports German (`de`) and English (`en`):

- Read `UI_LANGUAGE` from `$ADDON_DIR/config/config.env` or `$ADDON_DIR/.env`; default to `de` when unset or invalid.
- Keep localized strings in POSIX `sh` maps for `title`, `status_badge`, `primary.label`, `delta.comparison`, and `secondary_metrics[].label` / `value`.
- Mirror new standard keys in `PLUGIN_I18N` in `web/app.js` so the language switch translates cards without a reload.

## 4. Security checklist

See [SECURITY.md](SECURITY.md) for the disclosure policy. Every contribution must satisfy these controls:

- [ ] **Secrets via `stdin`:** passwords, API keys, session IDs, and bearer tokens must not appear on the `curl` command line, where `/proc/<pid>/cmdline` exposes them. Pipe them through `curl --config -` or `--data-binary @-`.
- [ ] **Strict permissions:** set `umask 077` at the top of backend scripts. Enforce `chmod 600` on `.env`, `config/config.env`, `config/plugins/*.env`, `.renault_session`, and log/history files.
- [ ] **Private scratch files:** do not create predictable files in `/tmp`. Use `$STATE_DIR` (mode `700`), suffix names with `$$`, and clean up in an `EXIT` trap.
- [ ] **Input validation:** validate external values before they reach a URL, header, or shell assignment:
  - Use `is_safe_id` (`[A-Za-z0-9_-]`, max 128 chars), `is_safe_host`, `is_uint`, and `is_number`.
  - Escape single quotes with `shquote` when writing `.env` files.
- [ ] **CGI and Web hardening:**
  - Never return stored passwords or API keys from CGI GET/POST responses; report only `*_set: true|false`.
  - Enforce same-origin checks (`Origin`, `Referer`, `X-Requested-With: XMLHttpRequest`) on state-changing `POST` requests.
  - Escape untrusted strings with `escapeHtml` in `web/app.js` and keep the `Content-Security-Policy` in `web/index.html`.

## 5. Pull requests and testing

Run the syntax checks, footprint benchmark, and integration suite from the repository root:

```sh
# 1. POSIX sh syntax
sh -n bin/check_soc.sh lib/notify_signal.sh setup_variables.sh \
      web/api/config.cgi web/api/plugin_manager.cgi web/api/plugins.cgi \
      plugins.d/10_ccu_health.sh plugins.d/15_weather.sh \
      plugins.d/20_renault_zoe.sh plugins.d/30_forecast_solar.sh \
      tools/benchmark_footprint.sh

# 2. JavaScript syntax
node --check web/app.js

# 3. Footprint and execution budgets (<= 250 KB installed, <= 500 ms runtime)
./tools/benchmark_footprint.sh

# 4. End-to-end suite (mandatory)
python3 tests/integration.py
```

All changes must pass `python3 tests/integration.py` with zero regressions. Add assertions for every new plugin, CGI route, or UI behaviour.

## License

By contributing to Sentinella, you agree that your contributions are licensed under the [MIT License](LICENSE).
