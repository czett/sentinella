# Contributing to Sentinella

Thank you for helping improve **Sentinella**, the high-precision SMA battery and telemetry monitor for Homematic CCU3 and RaspberryMatic. Because Sentinella runs directly on an embedded Buildroot environment with tight flash and RAM budgets, contributions are reviewed against strict portability, security, and failure-handling standards.

---

## 1. Architecture Philosophy

- **Zero External Runtime Dependencies:** Sentinella runs natively on a stock CCU3 or RaspberryMatic image. **No Python, no Node.js, no `jq`, and no compiled binaries** may be required on the CCU3 at runtime.
- **Stateless & Event-Driven:** Polling scripts run on-demand via `crond` (0 MB resident RAM between runs). CGI endpoints run behind the CCU3's native `lighttpd`.
- **Self-Contained WebUI:** Vanilla HTML5, CSS3, and ES6 JavaScript (`web/index.html`, `web/style.css`, `web/app.js`). No external CDNs, web fonts, frontend frameworks, or required build steps.
- **Fail-Safe Telemetry:** A transient network error, HTTP timeout, or malformed upstream response must **never** overwrite the last valid CCU3 system variable (`SV_Batterie_SoC`, `SV_Batterie_95_Signal`) or delete an existing dashboard card.

---

## 2. Pure POSIX `/bin/sh` Standard (BusyBox `ash`)

All scripts in `bin/`, `lib/`, `plugins.d/`, `web/api/`, and `tools/benchmark_footprint.sh` must execute cleanly under `/bin/sh` (BusyBox `ash` and `dash`):

- **No bashisms:** Do not use `local`, arrays, `[[ ... ]]`, `(( ... ))`, `source` (use `.`), process substitution `<(...)`, `read -p`, `date -d`, or `${var//pat/rep}`.
- **Standard Userland Only:** Rely only on utilities guaranteed on CCU3 Buildroot: `/bin/sh`, `curl`, `sed`, `grep`, `awk`, `tr`, `cut`, `head`, `tail`, `date`, `mv`, `cp`, `rm`, `mkdir`, `chmod`, `stat`, and `logger`.
- **Output & Quoting:** Prefer `printf` over `echo` for any variable data. Quote all parameter expansions (`"$var"`) unless word splitting is explicitly intended.
- **Session Hygiene:** Any code path that opens an authenticated session (such as SMA WebConnect `login.json`) must register an `EXIT` / signal `trap` to guarantee `logout.json` is called and temporary files or lock directories are cleaned up.
- **Atomic File Writes:** Never write directly to a live state, config, or JSON file. Write to a private temporary file in the same directory (`$OUT_FILE.tmp.$$`) and atomically replace the target with `mv -f`.

---

## 3. Plugin Standard Contract

Plugins live in `plugins.d/` (mode `755`, e.g., `plugins.d/20_renault_zoe.sh`, `plugins.d/30_forecast_solar.sh`). Each plugin runs independently, reads optional configuration from `config/plugins/<name>.env`, and atomically writes one JSON object to `state/plugins/<id>.json`.

### `/state/plugins/*.json` Schema

`web/api/plugins.cgi` validates each file with a strict RFC 8259 JSON object validator and streams them as a JSON array to `web/app.js`. Every plugin card must emit:

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

- `status_color`: Must be one of `"green"`, `"blue"`, `"red"`, or `"neutral"`.
- Unconfigured plugins must exit `0` cleanly without writing a card or logging errors (unless invoked with `--test`, which exits `2` for missing credentials or `1` for connection failure).

### The 3 Layout Archetypes

1. **`stat` (Hero Metric + Key-Value Rows):**
   - Requires `primary: { value, unit, label }` and optional `secondary_metrics: [ { label, value, unit? } ]`.
   - Best for state of charge, live power, or single-device telemetry (e.g., `20_renault_zoe.sh`).
2. **`delta` (Hero Metric + Trend Badge + Comparison):**
   - Requires `primary: { value, unit, label }`, `delta: { direction: "up"|"down"|"neutral", value, comparison }`, and optional `secondary_metrics`.
   - Best for solar forecasts, day-over-day comparisons, and dynamic tariffs (e.g., `30_forecast_solar.sh`).
3. **`list` (Structured Key-Value Table):**
   - Uses `secondary_metrics: [ { label, value, unit? } ]` rendered as a clean typographic ledger.
   - Best for multi-parameter devices such as wallboxes or heat pumps.

### Mandatory i18n Handling (DE / EN)

Every plugin must support both German (`de`) and English (`en`):
- Read `UI_LANGUAGE` from `$ADDON_DIR/config/config.env` or `$ADDON_DIR/.env` (defaulting to `de` in plugin scripts if unset or invalid).
- Maintain localized string maps in POSIX `sh` for `title`, `status_badge`, `primary.label`, `delta.comparison`, and `secondary_metrics[].label` / `value`.
- Mirror any new standard plugin keys in the `PLUGIN_I18N` dictionary in `web/app.js` so toggling the WebUI language switcher translates cards immediately with zero flicker.

---

## 4. Security Checklist

See [SECURITY.md](SECURITY.md) for the vulnerability disclosure policy. Every contribution must satisfy these controls:

- [ ] **Pass Secrets via `stdin`, Never `argv`:** Passwords, API keys, session IDs, and bearer tokens must never appear on the `curl` command line (where they are exposed in `/proc/<pid>/cmdline`). Pipe them via `curl --config -` or `--data-binary @-`.
- [ ] **Strict Credential Permissions (`chmod 600` & `umask 077`):** Set `umask 077` at the top of backend scripts. Enforce `chmod 600` on `.env`, `config/config.env`, `config/plugins/*.env`, `.renault_session`, and log/history files.
- [ ] **Private Scratch Files:** Never create predictable temporary files in world-writable `/tmp`. Place scratch files inside `$STATE_DIR` (mode `700`) using `$$` suffixes and clean them up in an `EXIT` trap.
- [ ] **Input Validation (`is_safe_id` & Allowlists):** Validate every external or user-supplied value before using it in URLs, headers, or shell assignments:
  - Use `is_safe_id` (`[A-Za-z0-9_-]`, max 128 chars), `is_safe_host`, `is_uint`, and `is_number`.
  - Quote values with single-quote escaping (`shquote`) when writing `.env` files.
- [ ] **CGI & Web Hardening:**
  - Never return stored passwords or API keys in CGI GET/POST responses (`*_set: true|false` only).
  - Enforce same-origin checks (`Origin`, `Referer`, and `X-Requested-With: XMLHttpRequest`) on all state-changing `POST` requests.
  - Escape all untrusted strings with `escapeHtml` in `web/app.js` and keep `Content-Security-Policy` intact in `web/index.html`.

---

## 5. PR & Testing Workflow

Before opening a pull request, run the syntax checks, footprint benchmark, and full integration test suite from the repository root:

```sh
# 1. POSIX sh syntax verification
sh -n bin/check_soc.sh lib/notify_signal.sh setup_variables.sh \
      web/api/config.cgi web/api/plugins.cgi \
      plugins.d/20_renault_zoe.sh plugins.d/30_forecast_solar.sh \
      tools/benchmark_footprint.sh

# 2. JavaScript syntax verification
node --check web/app.js

# 3. Footprint & execution budget guard (<= 250 KB installed, <= 500 ms runtime)
./tools/benchmark_footprint.sh

# 4. End-to-end integration test suite (mandatory)
python3 tests/integration.py
```

All changes **must** pass `python3 tests/integration.py` with `0` regressions. When adding a new plugin, CGI route, or UI behavior, add corresponding assertions to `tests/integration.py`.

## License

By contributing to Sentinella, you agree that your contributions will be licensed under the [MIT License](LICENSE).
