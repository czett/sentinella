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

## Secrets in this project

The add-on handles two secrets:

- `SMA_PASS` - the inverter WebUI password,
- `SIGNAL_APIKEY` - the CallMeBot API key.

Both live in `.env`, which is ignored by Git and installed with mode `600`.
The dashboard's `status.json` contains no credentials, session ids or API keys.
The settings API reads `.env` on the CCU, but never returns `SIGNAL_APIKEY` or
`SMA_PASS`; it reports only whether a value is stored, and an empty secret field
keeps the existing value. Values are validated in POSIX shell, written to a
private copy and moved into place atomically, and the file stays mode `600`.
If you believe a secret was committed, rotate it immediately and remove it from
the Git history, not only from the working tree.

## Network exposure

- The SMA API is contacted over HTTPS. The default `SMA_TLS_VERIFY="false"`
  accepts the inverter's self-signed certificate; this is an intentional
  trade-off for a local device.
- ReGaHSS is contacted over HTTP on the CCU3. Do not expose port `8181` to the
  internet.
- The dashboard is served by the CCU3 web server on the local network. Its
  settings API accepts only same-origin POSTs and writes only the documented
  fields. Do not expose the CCU3 web interface to the internet; use a VPN for
  remote access.
