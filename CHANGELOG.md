# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
