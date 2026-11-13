# Sentinella security policy

## Supported versions

The latest release on the default branch receives security fixes.

## Reporting a vulnerability

Please do not open a public issue for a vulnerability. Send a private report
to the repository maintainer with:

- affected version or commit,
- a description of the issue and its impact,
- reproduction steps or a proof of concept,
- any suggested fix.

You will receive an acknowledgement as soon as possible. Please allow a
reasonable window for a fix before publishing details.

## Secrets and credentials in this project

Sentinella stores user credentials and tokens exclusively in git-ignored, mode `600` files on the CCU:

- `.env` — `SMA_PASS` (inverter WebUI password), `SIGNAL_APIKEY` (CallMeBot API key), `SIGNAL_PHONE` (Signal recipient UUID or E.164 number),
- `config/plugins/renault.env` — `RENAULT_PASS` (MY Renault password),
- `config/plugins/shelly_em.env` — `SHELLY_PASS` (Shelly WebUI password),
- `config/plugins/tibber.env` — `TIBBER_TOKEN` (Tibber GraphQL Bearer token),
- `state/plugins/.renault_session` — cached Renault Gigya/Kamereon session token (mode `600`).

Note: `plugins.d/20_renault_zoe.sh` embeds Renault's public mobile-app client keys (`GIGYA_API_KEY`, `KAMEREON_API_KEY`), which are non-secret application identifiers, not personal credentials.

Security properties enforced by Sentinella:

- The dashboard's `status.json` and `state/plugins/*.json` contain no credentials, session ids, or API keys.
- `GET /api/config.cgi` never returns plaintext `SMA_PASS`, `SIGNAL_APIKEY`, `RENAULT_PASS`, `SHELLY_PASS`, or `TIBBER_TOKEN` (only `*_set` booleans), and masks E.164 phone numbers (`+49****4567`).
- Changing `sma_ip` or `shelly_ip` to a different host without simultaneously supplying a new password automatically clears the stored `SMA_PASS` / `SHELLY_PASS` to prevent LAN host-redirection credential capture.
- All HTTP `curl` calls pass credentials/tokens via `stdin` (`--config -` or `--data-binary @-`), never in process arguments (`/proc/<pid>/cmdline`).
- Configuration files are validated in POSIX shell, serialized via single-quote escaping, written to private mode `600` temp files, and moved into place atomically.
- If you believe a secret was committed, rotate it immediately and remove it from the Git history, not only from the working tree.

## Network exposure & trusted-LAN model

- **Trusted-LAN UI:** Sentinella is served by the CCU3/RaspberryMatic `lighttpd` web server at `/addons/sentinella/` without separate CCU3 `sid` session enforcement, so it works directly inside the local network. **Do not expose the CCU3 web interface or port `8181` to the internet or untrusted IoT VLANs**; isolate the CCU on a trusted management/home LAN or put an authenticating reverse proxy / VPN in front of remote access.
- **Local vs. public TLS:**
  - `SMA_TLS_VERIFY="false"` (and `SHELLY_TLS_VERIFY="false"`) are local-LAN defaults to accommodate self-signed device certificates on inverters and Shelly meters.
  - All public internet APIs (`api.tibber.com`, `api.awattar.*`, `api.open-meteo.com`, `ipapi.co`, `api.forecast.solar`, `signal.callmebot.com`, Renault Gigya/Kamereon, GitHub Releases) enforce strict TLS certificate verification.
- **CSRF & SSRF guards:** State-mutating `POST` CGI endpoints enforce `Content-Type: application/json`, same-origin `Origin`/`Referer` + `X-Requested-With: XMLHttpRequest` checks, and reject loopback/link-local targets (`127.*`, `localhost`, `0.0.0.0`, `169.254.*`) on configurable device hosts.
