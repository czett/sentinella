# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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

- Dashboard redesign: zinc field, hairline grid, oversized Swiss SoC type,
  thin hysteresis rail, crisp history line, 100dvh shell, mobile
  pull-to-refresh and an explicit live-age indicator. Settings drawer and
  API contract are unchanged.
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
