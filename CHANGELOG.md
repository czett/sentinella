# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.2.1] - 2026-11-13

### Added

- **First-3-Minutes Onboarding & CCU3 Program Guide (`README.md`, `README.de.md`, `docs/QUICKSTART.md`)**: Step-by-step CCU3 WebUI program recipe (`SV_Batterie_95_Signal` on change for `HmIP-PSM` / immersion heater / wallbox), 3-mode quick setup table, and `docs/screenshots/README.md`.
- **Custom Plugin Starter Template (`docs/PLUGIN_TEMPLATE.sh`)**: Copy-pasteable POSIX `/bin/sh` template with BusyBox `ash` compatibility, strict `curl` timeouts, and atomic `.tmp` + `mv -f` JSON output (`stat` layout), linked from `docs/PROTOCOL.md` and `CONTRIBUTING.md`.
- **Integration test coverage (`tests/integration.py`, 32 sections)**: Host URL normalization in `config.cgi` and `web/app.js`, CGI security header verification across all endpoints, `.disabled` marker cleanup, unified plugin state, `SETUP`/`OFFLINE` pre-seeding, and standalone `docs/PLUGIN_TEMPLATE.sh` validation.

### Changed

- **Unified Plugin Activation & Pre-Seeding (`web/api/config.cgi`, `web/api/plugin_manager.cgi`)**: Enabling a plugin or switching `HERO_SOURCE=shelly` automatically removes stale `.disabled` markers, keeps `config/plugins.env` and `config/plugins/*.env` in sync, and pre-seeds `SETUP`/`OFFLINE` cards without requiring a manual save.
- **Automatic Host / IP Normalization (`web/app.js`, `web/api/config.cgi`)**: Strips accidental `http://` / `https://` prefixes, path segments, and trailing slashes from `#sma-ip` and `#shelly-ip` on `blur`/`change` and defensively inside `config.cgi`; fixes `validateSettings()` to allow empty `sma_ip` in CCU-Only mode (`sma_enabled=false`).
- **Hardened CGI headers & plugin input parsing**: Added `Pragma: no-cache` and `Referrer-Policy: same-origin` to `web/api/plugin_manager.cgi` and defensive validation for Shelly/tariff fields (including decimal `tariff_surcharge_ct`) in `web/api/config.cgi`.

## [1.2.0] - 2026-11-12

### Added

- **CCU-Only Mode & Symmetric SMA Module (`SMA_ENABLED`)**: `SMA_IP` is now optional and SMA Battery Storage can be toggled cleanly via `SMA_ENABLED`. When SMA is disabled or unconfigured, Tiles `03` (SoC History) and `04` (System Register) automatically hide; when active alongside another Hero source, SMA renders symmetrically in `05 Extensions`.
- **Configurable Hero Tile (`HERO_SOURCE`)**: Choose between `auto`, `sma`, `ccu`, and `shelly` for the primary hero tile with instant auto-save in the Settings Drawer and automatic deduplication against `05 Extensions`.
- **Shelly EM Plugin (`25_shelly_em.sh`)**: Supports Shelly EM (Gen1) and Shelly Pro EM / 3EM (Gen2/Pro) for real-time grid power, phase telemetry, and energy counters using the `stat` card layout.
- **Dynamic Electricity Tariff Plugin (`35_tibber.sh`)**: Zero-config aWATTar (DE/AT) hourly electricity prices out of the box, with optional Tibber API token support and `stat` card layout.
- **Responsive 3-Column Extension Grid & Live Plugin Sync**: `.plugins-grid` caps at 3 cards per row on desktop (`>= 900px`), `deploy.sh` stages all `plugins.d/*.sh` dynamically, and plugin toggles refresh immediately.
- **Atomic state writes**: All state and JSON writes use `.tmp` + `mv -f` to prevent partial reads on the CCU3.
- `UI_LANGUAGE` (`en`/`de`, default `en`) for the dashboard and the built-in
  Signal messages. The header `EN / DE` switch updates the interface
  immediately and persists the choice through `api/config.cgi`.
- Built-in, URL-free notification texts for trigger, reset and test events in
  English and German.
- `SIGNAL_MESSAGE_OVERFLOW` and `SIGNAL_MESSAGE_RESET` templates with
  `{SOC}`, `{TRIGGER_SOC}`, `{RESET_SOC}` and `{HOST}` placeholders.
- `notify_signal.sh --test` for credential/delivery checks without touching
  the battery state; `deploy.sh` runs it when notifications are enabled.
- Per-event `SIGNAL_COOLDOWN` (default 3600 s) suppresses duplicate alerts.
- Browser settings drawer for thresholds, Signal phone/UUID, API key and
  inverter address/password. No `.env` editing after the first deployment.
- `web/api/config.cgi` and `web/api/test_signal.cgi` with validation, atomic
  `.env` writes, masked secrets and a same-origin POST check.
- Background test notification from the drawer with an async result toast.
- Saving the settings with Signal enabled immediately sends a verification
  message in the background and reports success or a credential warning.
- `deploy.sh` patches a tclsh-only `.cgi` mapping in
  `/etc/lighttpd/conf.d/cgi.conf` to the POSIX shebang handler on first install.
- Integration coverage for cooldown, `--test`, UTF-8 URL encoding, config
  reading, validation rejection, atomic save and test dispatch.

### Changed

- Project rebrand to **Sentinella**, with the dashboard title, masthead,
  footer, settings feedback and documentation carrying the new identity. The
  Signal Red square remains the visual mark, and all backend paths, CGI
  contracts and `status.json` polling are unchanged.
- Dashboard redesign: warm paper field, full-bleed ultramarine SoC hero, heavy
  Helvetica figures, hairline hysteresis rail with bold threshold labels, stark
  two-tone history chart and a 2×2 telemetry grid. Flat colour blocks replace
  dot badges, glow effects and the live-age indicator; freshness is carried by
  the values and a plain notice strip. The mobile layout stacks the colour
  blocks, keeps 48 px touch targets and pull-to-refresh. The demo mode is
  labelled "Sentinella Preview Data". Settings drawer and API contract are
  unchanged.
- Notification hook is launched with `setsid`/`nohup`, fully detached from the
  cron polling loop.
- Redeployments keep the existing remote `.env` instead of overwriting live
  credentials.
- `deploy.sh` installs the CGI endpoints with mode `755` and probes the
  settings API through lighttpd after copying.
- Legacy `SIGNAL_TEXT_*` variables and `%SOC%`-style placeholders remain
  supported.

### Security

- The settings API never returns `SIGNAL_APIKEY` or `SMA_PASS`; it reports
  only whether a secret is stored. Empty secret fields keep the old value.
- Every value is validated in POSIX shell before it reaches `.env`; the file is
  replaced atomically and stays mode `600`.

## [1.0.0] - 2026-11-06

### Added

- POSIX/BusyBox-compatible `bin/check_soc.sh` core engine.
- SMA WebConnect login, `getValues` and guaranteed logout flow.
- Optional battery charge/discharge power keys for the dashboard.
- Hysteresis with persisted state and an explicit "unknown state" hold.
- Single-instance PID lock with stale-lock recovery.
- Retry, timeout and conservative fault handling.
- Atomic `status.json` publication and history samples.
- Detached CallMeBot Signal notification hook.
- Self-contained dark dashboard with gauge, hysteresis rail and history chart.
- Idempotent ReGa system-variable setup.
- SSH deployment helper using `tar` streaming (no remote `rsync`).
- English and German documentation, protocol notes and troubleshooting guide.
- Mock SMA/ReGa integration test suite.
- Optional Speedwire diagnostic helper under `tools/`.

### Security

- `.env` is ignored by Git and installed with mode `600`.
- Dashboard output contains no credentials or session ids.
- SMA TLS verification is configurable and defaults to the self-signed device setup.
