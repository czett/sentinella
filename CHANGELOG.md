# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.2.1] - 2026-11-13

### Added
- CCU3 WebUI program guide, `docs/PLUGIN_TEMPLATE.sh` starter template, and 32-section integration test suite.

### Changed
- Unified plugin activation (`.disabled` marker cleanup, `SETUP`/`OFFLINE` pre-seeding, instant toggles) and automatic host/IP URL normalization.

### Security
- Credential clearing on IP change, `stdin` curl secret streaming, E.164 phone masking, SSRF/CSRF checks, and `script-src 'self'` CSP.

## [1.2.0] - 2026-11-12

### Added
- CCU-Only mode (`SMA_ENABLED`), configurable Hero tile (`HERO_SOURCE`), Shelly EM plugin, and zero-config aWATTar/Tibber dynamic tariff plugin.
- Browser Settings Drawer (`EN`/`DE`), Signal cooldown/templates/self-test, responsive 3-column plugin grid, and Swiss-grid dashboard redesign.

### Security
- Atomic `0600` `.env` writes and masked secrets in CGI responses.

## [1.0.0] - 2026-11-06

### Added
- Initial release: POSIX/BusyBox SMA WebConnect polling engine (`bin/check_soc.sh`), hysteresis + ReGa `SV_Batterie_95_Signal` automation, CallMeBot Signal alerts, dark dashboard, and SSH tar deployment.
