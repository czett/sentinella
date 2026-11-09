# Sentinella troubleshooting

Start with the foreground run. It shows the real error and does not depend on cron:

```sh
/usr/local/addons/sentinella/bin/check_soc.sh; echo "exit=$?"
```

Exit codes:

| Exit | Meaning |
|---|---|
| `0` | success, or another instance is already running |
| `1` | runtime error; the previous CCU3 values were kept |
| `2` | configuration error; nothing was written |

The log is written to stderr, syslog and (by default) `/usr/local/addons/sentinella/state/check_soc.log`:

```sh
tail -n 80 /usr/local/addons/sentinella/state/check_soc.log
logread | grep -E 'sentinella'
```

## Configuration

### `configuration file ... is missing or unreadable`

`deploy.sh` installs `.env` next to `bin/` as `/usr/local/addons/sentinella/.env`. Check:

```sh
ls -l /usr/local/addons/sentinella/.env
```

The file must be readable by root. If you use the legacy `config.env`, the engine still falls back to it only when `.env` does not exist.

### `SMA_IP must be ...` / `CCU_REGA_PORT must be ...`

The validator rejects schemes and paths in host fields. Use `192.168.178.123`, not `https://192.168.178.123/`. Ports are integers from 1 to 65535.

### `THRESHOLD_TRIGGER ... must be greater than THRESHOLD_RESET`

Set a real dead band, for example `95` and `90`. Equal values are rejected because the hysteresis would be undefined.

### `SIGNAL_PHONE and SIGNAL_APIKEY are required`

The check is strict by design. Either fill both values or set `SIGNAL_ENABLED="false"`. The CCU3 variables are not updated while the configuration is invalid.

### Warning about mode `644`

Fix it:

```sh
chown root:root /usr/local/addons/sentinella/.env
chmod 600 /usr/local/addons/sentinella/.env
```

The warning does not stop the run, but the file contains the inverter password and the CallMeBot key.

## Inverter connection

### `login failed` / `err 401`

- Verify the password in the inverter WebUI.
- Try `SMA_ROLE="usr"` and `SMA_ROLE="user"`; the installer account is usually `usr`.
- Confirm `SMA_IP` and `SMA_PORT`.
- Test from the CCU3, because the CCU may be on a different VLAN:

```sh
curl -k -sS -o /dev/null -w '%{http_code}\n' https://192.168.178.123/
```

A TCP/TLS error means routing, firewall, VLAN or a wrong address. An HTTP error means the host is reachable but the endpoint or credentials are wrong.

### `getValues request failed` / repeated retries

- The session may have been invalidated. Run the script once in the foreground and watch whether logout is attempted.
- Check for duplicate cron entries. Two add-ons polling the same inverter can exhaust the session table.
- Confirm that no browser session is open on the inverter.
- Reduce `SMA_RETRIES` to `1` while debugging.

### Session lockout

The inverter allows only a few concurrent sessions. If login fails although the password is correct:

1. Comment out the cron entry or stop the monitor.
2. Close every browser tab with the inverter WebUI.
3. Wait 5 to 10 minutes.
4. Retry once.
5. If necessary, power-cycle the inverter according to its manual.
6. Keep `SMA_RETRIES="1"` until the setup is stable.

This project logs out in a trap, but `SIGKILL` cannot be trapped. Avoid `kill -9`; use `SIGTERM` and let the cleanup run.

### `no numeric value for 6100_00295A00`

- The battery may be disconnected or in a state without a measurement.
- The key may be wrong for the firmware. See [PROTOCOL.md](PROTOCOL.md#discovering-keys-on-your-firmware).
- The value may be `null` at night or during a firmware update.
- The monitor deliberately keeps the last CCU3 value in this case.

### Value with decimals

The CCU3 SoC is defined as an integer 0..100. A decimal value is rejected rather than rounded. If your firmware reports a decimal SoC and you want it, change the contract consciously: the dashboard and the system variable would need a matching type and the validation in `bin/check_soc.sh` would have to be relaxed.

### TLS errors

Default is `SMA_TLS_VERIFY="false"`. If you set it to `"true"`, the CCU3 must trust the inverter certificate. For a self-signed certificate, either install it in the CCU trust store or go back to `"false"`.

## CCU3 / ReGaHSS

### `CCU3 ReGa request failed`

Test ReGaHSS directly on the CCU3:

```sh
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8181/tclrega.exe
```

- Connection refused: ReGaHSS is not running or the port is different. Check `CCU_REGA_PORT`.
- HTTP 404: wrong URL or a reverse proxy in front of ReGa.
- Timeout: the CCU3 is overloaded; check CPU and the number of add-ons.

The monitor uses `http://<CCU_IP>:<CCU_REGA_PORT>/tclrega.exe`. If you run it on the CCU3, keep `CCU_IP="127.0.0.1"`.

### `CCU3 did not return the confirmation marker`

Some firmware versions execute the ReGa script but do not return `WriteLine()` output. The variables are normally still written. Check them manually under **Status and Control → System variables**. The run is still considered successful because the HTTP request succeeded.

### Variables are missing

```sh
cd /usr/local/addons/sentinella
sh setup_variables.sh
```

Then check:

- **Status and Control → System variables**
- the exact names `SV_Batterie_SoC` and `SV_Batterie_95_Signal` (case-sensitive)
- whether a CCU program or a second add-on deletes or renames them

The ReGa setup script is idempotent; running it twice does not create duplicates.

### Variables exist but never change

- Run the monitor in the foreground and check the exit code.
- Verify that the SoC value from the inverter is in 0..100.
- Check the log line `SoC=... signal=...`.
- If the signal is `unchanged`, the state file is missing and the SoC is inside the dead band. This is intentional; see the hysteresis section in the README.
- Check that a CCU program is not overwriting the variable.

## Cron

### The monitor never runs

```sh
crontab -l
ps | grep crond
/etc/init.d/S71crond restart
```

The expected entry is:

```
*/2 * * * * /usr/local/addons/sentinella/bin/check_soc.sh >/dev/null 2>&1
```

The script sets its own `PATH`; a minimal cron environment is fine. If the entry is missing after a firmware update, re-run `./deploy.sh`.

### Runs overlap

The lock directory `state/.lock` prevents this. If a run is killed with `SIGKILL`, the lock remains until the next run detects that the PID is gone. Check:

```sh
ls -la /usr/local/addons/sentinella/state/.lock
cat /usr/local/addons/sentinella/state/.lock/pid
```

Do not delete a live lock. If the PID is gone, the next run removes the lock automatically. A PID can be reused by another process; in that rare case, remove the lock manually after confirming no monitor is running.

### Runs are late or skipped

A full cycle can take up to `SMA_RETRIES × (connect + max time + delay)`, plus a ReGa request. With the defaults this is bounded to roughly 45 seconds, well under the two-minute interval. If the inverter is slow, the next run may find the lock and exit quietly; the following run proceeds. Check the timestamps in the log.

## Dashboard

### "Waiting for the first measurement"

- The first cron run has not completed. Wait up to two minutes.
- Check that `WEB_ENABLED="true"`.
- Check that `WEB_STATUS_FILE` points into the lighttpd add-on directory.
- Check that the status file exists:

```sh
ls -l /usr/local/etc/config/addons/www/sentinella/status.json
```

### The page is blank or shows a 404

The add-on URL is:

```
http://<ccu>/addons/sentinella/
```

(Legacy `http://<ccu>/addons/sma_battery/` is also kept active via symlink.)

Check the directory and permissions:

```sh
ls -la /usr/local/etc/config/addons/www/sentinella/
ls -la /www/addons/sentinella
```

Files must be readable by the lighttpd user. `deploy.sh` sets `755` on the directory and `644` on the files. If you changed `WEB_DIR`, update `WEB_STATUS_FILE` in `.env` to match.

### "Stale"

The last successful run is older than 10 minutes. This is a real monitoring problem, not a dashboard problem. Check cron, the log and the inverter connection.

### "Cannot read status.json"

- The file is missing (404): see the first-measurement section.
- HTTP 403: permissions.
- Fetch error: the dashboard was opened from `file://` without `?demo=1`, or a reverse proxy blocks the path.
- Invalid JSON: inspect the file; the monitor writes it atomically, so a partial file should not occur.

### The demo label does not go away

The page was opened with `?demo=1`, or via `file://`. Open the normal CCU URL without the query string.

## Plugins and plugin manager

### A plugin card is missing

1. Check the enable flag in `config/plugins.env`:
   ```sh
   cat /usr/local/addons/sentinella/config/plugins.env
   ```
   `PLUGIN_<NAME>='0'` disables the plugin and removes its card.
2. Check that the card file exists and is valid JSON:
   ```sh
   ls -l /usr/local/addons/sentinella/state/plugins/
   ```
3. Run the plugin in the foreground and inspect its exit code:
   ```sh
   /usr/local/addons/sentinella/plugins.d/15_weather.sh; echo "exit=$?"
   ```
4. For Weather, check `config/plugins/weather.env`. Missing location values are auto-detected once via `ipapi.co` and cached there; explicit values always win. For PV Forecast, confirm the coordinates in `config/plugins/forecast_solar.env`.
5. For CCU3 Health, confirm that `tclsh` and `tclrpc.so` are present. Without XML-RPC access the plugin falls back to `hm_sysinfo` and the duty-cycle files, and reports `N/A` when no source answers.

### Plugin cards do not refresh after a cron run

Poll-time plugin execution is opt-in. Add `SENTINELLA_RUN_PLUGINS=1` to the cron environment, for example:

```
*/2 * * * * SENTINELLA_RUN_PLUGINS=1 /usr/local/addons/sentinella/bin/check_soc.sh >/dev/null 2>&1
```

The installer runs each plugin once after deployment, so a missing refresh usually points to this variable rather than a broken plugin.

### A toggle does not persist

The dashboard sends a same-origin `POST` with `X-Requested-With: XMLHttpRequest` to `api/plugin_manager.cgi`. A direct `curl` request without these headers is rejected. Check that `config/plugins.env` is writable by the CGI user and stays mode `600`:

```sh
ls -l /usr/local/addons/sentinella/config/plugins.env
```

## Settings panel and CGI

The settings drawer reads and writes `api/config.cgi`, `api/test_signal.cgi`, and `api/plugin_manager.cgi` below the dashboard directory. The browser never needs SSH or `.env` access.

### The drawer says "Settings are not available"

Check the endpoint from the CCU itself:

```sh
curl -i http://127.0.0.1/addons/sentinella/api/config.cgi
```

Expected: `HTTP/1.1 200 OK` and a JSON object. If the response is `403`, `404` or the file is offered as a download, lighttpd is not executing CGI:

```sh
ls -l /usr/local/etc/config/addons/www/sentinella/api/
```

All `.cgi` files must be mode `755`, and `addon_dir` must contain the add-on path (mode `644`). `deploy.sh` sets this automatically.

CCU3 and RaspberryMatic normally ship lighttpd with `mod_cgi` and a `.cgi` mapping. Some images map `.cgi` to `/bin/tclsh`, which cannot execute these shell scripts; `deploy.sh` detects that mapping in `/etc/lighttpd/conf.d/cgi.conf` and switches it to `".cgi" => ""` (remounting `/` read-write only for the edit). If your firmware keeps the mapping elsewhere or does not execute the endpoints, back up the lighttpd configuration, add:

```conf
server.modules += ( "mod_cgi" )
cgi.assign = ( ".cgi" => "" )
```

then restart lighttpd (find the init script with `ls /etc/init.d/*lighttpd*`, usually `S50lighttpd`). Keep the setting when a firmware update replaces the file, or re-apply it through your add-on.

### Saving fails with "not writable"

The CGI must be able to read and replace `/usr/local/addons/sentinella/.env`. On a stock CCU3 lighttpd and its CGI handlers run as root. If you hardened lighttpd to drop privileges, the settings API cannot read a `600` root-owned `.env`; either run the CGI as root or adjust ownership, but do not make `.env` world-readable.

### A test notification fails

- Save the UUID and API key first. The test button saves the form before it runs.
- Empty secret fields intentionally keep the stored value; type a new value to replace it.
- Check `state/notify_test.log` on the CCU and the `sentinella_notify` syslog tag.
- Verify that the CCU can reach `SIGNAL_API_URL` (HTTPS, port 443).

### After saving, the dashboard still shows the old thresholds for a moment

The dashboard applies saved values immediately, but `status.json` is only rewritten by the next cron run (up to two minutes). No restart is required.

## Signal notifications

### No message arrives

- `SIGNAL_ENABLED="true"`?
- `SIGNAL_PHONE` set to your Signal Account UUID?
- `SIGNAL_APIKEY` correct and activated in CallMeBot?
- Is the notifier executable?

```sh
ls -l /usr/local/addons/sentinella/lib/notify_signal.sh
```

- Test credentials and delivery without changing `state/signal`:

```sh
/usr/local/addons/sentinella/lib/notify_signal.sh --test; echo "exit=$?"
```

- Check the log and syslog tag `sentinella_notify`.
- Messages are only sent on a transition after a successful CCU3 write. Lower `THRESHOLD_TRIGGER` temporarily to force a `false → true` transition, or set `SIGNAL_NOTIFY_ON_START="true"` for one run if the signal is already active.
- The same event is suppressed for `SIGNAL_COOLDOWN` seconds (default 3600). A "notification suppressed" log line means this protection fired. Remove `state/notify_trigger` or `state/notify_reset` only if you intentionally want to resend immediately.

### Message text is wrong

`UI_LANGUAGE` selects the built-in text: `en` (default) or `de`. The dashboard `EN / DE` switch writes the same key, so the interface and the next message stay in sync. Check that the saved value is one of those two spellings.

Explicit templates override the built-in text. They use `{SOC}`, `{TRIGGER_SOC}`, `{RESET_SOC}` and `{HOST}`. The legacy spellings `%SOC%`, `%TRIGGER%`, `%RESET%`, `%HOST%` and `%%` are still accepted. Quote templates that contain spaces. Example:

```sh
SIGNAL_MESSAGE_OVERFLOW="Speicher voll: {SOC}% (Schwelle {TRIGGER_SOC}%)"
```

Leave `SIGNAL_MESSAGE_OVERFLOW`, `SIGNAL_MESSAGE_RESET` and `SIGNAL_MESSAGE_TEST` unset to follow `UI_LANGUAGE`. Umlauts and `ß` are sent as UTF-8 and percent-encoded by `curl --data-urlencode`; no python or jq is required. After editing `.env`, no service restart is needed; the next run reads the file.

### CallMeBot rate limit

CallMeBot may throttle repeated messages. The monitor sends only on transitions and enforces a 60-minute duplicate guard per event, so a normal day produces at most a few messages. Do not use the notifier as a periodic heartbeat.

## Deployment

### `deploy.sh: missing .env`

```sh
cp .env.example .env
chmod 600 .env
# edit .env
```

### `cannot connect to root@...`

- Check the CCU3 SSH service and the host address.
- The CCU3 uses Dropbear. Password login must be enabled, or your public key must be in `/root/.ssh/authorized_keys`.
- `deploy.sh` uses `StrictHostKeyChecking=accept-new`; the first connection adds the host key.
- Try `ssh -p 22 root@<ccu>` manually.

### `file transfer failed`

The transfer uses `tar` over SSH, not `rsync`. Both sides need `tar`; the CCU3 BusyBox image has it. Check free space:

```sh
df -h /usr/local
```

### Cron installed but the dashboard is missing

`deploy.sh --skip-web` skips the dashboard. Re-run without that option, or copy the three files manually.

## Log interpretation

Examples:

```
2026-11-06 17:30:00 SoC=91% signal=1 (held, discharging)
```

Normal run. The signal is held at `true` because the SoC is inside the dead band.

```
2026-11-06 17:32:00 another instance is already running (pid 12345), exiting
```

The previous run is still active. This is not an error; the next cron run will try again.

```
2026-11-06 17:34:00 could not read SoC from 192.168.178.123, keeping last CCU3 values
```

The inverter was not readable. The CCU3 variables were not changed.

```
2026-11-06 17:36:00 SoC=92% signal=unchanged (no valid hysteresis state yet)
```

The state file is missing and the SoC is between the thresholds, so the boolean was deliberately left untouched.

```
2026-11-06 17:38:00 WARNING: cannot publish dashboard status to ...
```

The monitor worked, but the web directory is missing or not writable. Fix `WEB_STATUS_FILE` or the directory permissions.

## Diagnostic checklist

```sh
# 1. configuration and permissions
ls -l /usr/local/addons/sentinella/.env
ls -l /usr/local/addons/sentinella/bin/check_soc.sh

# 2. foreground run
/usr/local/addons/sentinella/bin/check_soc.sh; echo "exit=$?"

# 3. state
cat /usr/local/addons/sentinella/state/signal
cat /usr/local/addons/sentinella/state/status.json

# 4. cron
crontab -l
ps | grep crond

# 5. CCU3 variables
#    Status and Control -> System variables

# 6. dashboard
ls -l /usr/local/etc/config/addons/www/sentinella/

# 7. network from the CCU3
curl -k -sS -o /dev/null -w '%{http_code}\n' https://<inverter-ip>/
```

If the foreground run succeeds but cron does not, the problem is almost always cron (entry missing, crond not running, or a stale lock). If cron runs but the dashboard is empty, the problem is `WEB_STATUS_FILE` or lighttpd permissions. If both run but the CCU variables do not change, check ReGaHSS and the variable names.
