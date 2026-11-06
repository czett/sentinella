# SMA WebConnect JSON API - reverse-engineering notes

These notes describe the small part of the local SMA WebUI API used by Sentinella. The interface is undocumented, differs between firmware generations and can change without notice. Treat every observation as firmware-specific and verify on your own device.

Tested against a **SMA Sunny Tripower STP 10.0** with a **BYD Battery-Box** on a current firmware. The same flow is reported on Sunny Boy Storage devices.

## Transport

| Item | Value |
|---|---|
| Base URL | `https://<inverter-ip>/dyn/` |
| Default port | `443` (configurable with `SMA_PORT`) |
| Authentication | session id (`sid`) returned by `login.json` |
| Certificate | usually self-signed, so `curl -k` or `SMA_TLS_VERIFY="false"` |
| Content type | `application/json` |
| Method | `POST` for every endpoint used here |

The API is a request/response JSON API, not a streaming or WebSocket interface. The WebUI itself polls it with `getValues.json` requests.

## Session flow

```
Client                                  Inverter
  |                                        |
  | POST /dyn/login.json                   |
  | {"right":"usr","pass":"..."}           |
  |--------------------------------------->|
  |                                        |
  | 200 {"result":{"sid":"abc123"}}        |
  |<---------------------------------------|
  |                                        |
  | POST /dyn/getValues.json?sid=abc123    |
  | {"destDev":[],"keys":["6100_00295A00"],|
  |  "sid":"abc123"}                       |
  |--------------------------------------->|
  |                                        |
  | 200 {"result":{"data":{...}}}          |
  |<---------------------------------------|
  |                                        |
  | POST /dyn/logout.json?sid=abc123       |
  | {"destDev":[],"sid":"abc123"}          |
  |--------------------------------------->|
  |                                        |
  | 200 {"result":{}}                      |
  |<---------------------------------------|
```

The `sid` is both a query parameter and a JSON field on most requests. Always call `logout.json`; an abandoned session can block further logins until it times out.

### Login

```sh
curl -k -sS -X POST \
  -H 'Content-Type: application/json' \
  --data '{"right":"usr","pass":"your-password"}' \
  https://192.168.178.123/dyn/login.json
```

Successful response:

```json
{"result":{"sid":"0123456789abcdef"}}
```

Failure response (shape varies):

```json
{"err":401,"result":{}}
```

`right` is `"usr"` for the installer account and `"user"` for a normal user. The password is JSON-escaped; the helper escapes backslashes and double quotes. Passwords containing newlines are not supported by the current parser.

### Read values

```sh
SID='0123456789abcdef'
curl -k -sS -X POST \
  -H 'Content-Type: application/json' \
  --data '{"destDev":[],"keys":["6100_00295A00"],"sid":"'"$SID"'"}' \
  "https://192.168.178.123/dyn/getValues.json?sid=$SID"
```

Typical response:

```json
{
  "result": {
    "data": {
      "1901234567": {
        "6100_00295A00": {
          "1": [
            { "val": 88 }
          ]
        }
      }
    }
  }
}
```

The outer `data` key is the device serial. The value is an object keyed by the channel key; the inner `"1"` is a value index. Some keys return several entries (for example per-phase values); the monitor requests one key at a time and uses the first numeric `"val"`.

A `null` value means the channel exists but has no measurement:

```json
{"val": null}
```

This happens when the battery is disconnected, the device is in a state where the value is not available, or the key belongs to a different device class.

### Logout

```sh
curl -k -sS -X POST \
  -H 'Content-Type: application/json' \
  --data '{"destDev":[],"sid":"'"$SID"'"}' \
  "https://192.168.178.123/dyn/logout.json?sid=$SID"
```

The monitor clears its in-memory `SID` before sending the request, so a second signal during cleanup cannot send a duplicate logout. The trap still runs if the script is interrupted while a request is in flight.

## Channel keys

Keys look like `<group>_<channel><suffix>` and are not OBIS codes, although they follow a similar structure.

| Key | Meaning | Notes |
|---|---|---|
| `6100_00295A00` | battery state of charge | integer percent, standard key on STP/SBS |
| `6100_00417800` | battery charging power | optional; observed on STP/SBS firmware |
| `6100_00417900` | battery discharging power | optional; observed on STP/SBS firmware |

The optional power keys are only used for the dashboard's charging/discharging display. If they are not available, leave them empty in `.env`; the dashboard falls back to the SoC trend. The core monitor only requires `BATTERY_SOC_KEY`.

### Discovering keys on your firmware

1. Log in with the WebUI and open the battery page.
2. Open the browser developer tools, network tab, filter for `getValues.json`.
3. Reload the page and inspect the JSON request bodies. The keys used by the WebUI are visible there.
4. Alternatively, try `getAllKeys.json` on firmware that supports it:

```sh
curl -k -sS -X POST \
  -H 'Content-Type: application/json' \
  --data '{"destDev":[],"sid":"'"$SID"'"}' \
  "https://192.168.178.123/dyn/getAllKeys.json?sid=$SID"
```

Not every firmware exposes this endpoint. If it returns an error, use the developer-tools method.

5. Test a candidate key with `getValues.json` and look for a numeric `"val"`.

A quick one-shot probe:

```sh
IP=192.168.178.123
PASS='your-password'
KEY=6100_00295A00

SID=$(curl -k -sS -X POST -H 'Content-Type: application/json' \
  --data '{"right":"usr","pass":"'"$PASS"'"}' \
  "https://$IP/dyn/login.json" \
  | sed -n 's/.*"sid"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')

curl -k -sS -X POST -H 'Content-Type: application/json' \
  --data '{"destDev":[],"keys":["'"$KEY"'"],"sid":"'"$SID"'"}' \
  "https://$IP/dyn/getValues.json?sid=$SID"

curl -k -sS -X POST -H 'Content-Type: application/json' \
  --data '{"destDev":[],"sid":"'"$SID"'"}' \
  "https://$IP/dyn/logout.json?sid=$SID"
```

## Session limits and lockout

SMA devices allow only a small number of concurrent WebUI sessions. Symptoms of a full session table:

- `login.json` returns an error even with the correct password,
- the WebUI itself refuses to log in,
- a previously working script starts failing after it was killed with `SIGKILL` or `SIGSTOP`.

Mitigations used by this project:

- logout in an `EXIT` trap and on `INT`, `TERM` and `HUP`,
- no session reuse between cron runs,
- a lock directory so two cron invocations cannot race,
- bounded `curl` timeouts so a hung request cannot keep a session forever.

If the inverter is already locked out:

1. Stop the cron job or comment the entry.
2. Close all browser tabs with the inverter WebUI.
3. Wait 5 to 10 minutes for the sessions to expire.
4. If it still refuses, power-cycle the inverter (DC switch / AC breaker) according to the manufacturer's instructions.
5. Lower `SMA_RETRIES` to `1` until the configuration is confirmed, so a wrong password does not cause repeated login attempts.

## TLS

The inverter uses a self-signed certificate. The default `SMA_TLS_VERIFY="false"` maps to `curl -k`, which keeps the traffic encrypted but does not authenticate the peer. On a trusted home LAN this is the practical default. If you install a certificate the CCU3 trusts, set `SMA_TLS_VERIFY="true"`; the script then uses normal verification.

There is no client-certificate authentication in this flow; the WebUI password is the only credential.

## Failure modes seen in practice

| Observation | Interpretation |
|---|---|
| `{"err":401}` on login | wrong password, wrong role, or a locked WebUI user |
| login succeeds, `getValues` returns `{"err":...}` | session invalidated, too many sessions, or a firmware hiccup; retry the whole cycle |
| `"val": null` | channel exists but no measurement (battery offline, key not applicable) |
| value `0` | valid; a battery can report 0 % |
| value `100` | valid; trigger threshold will be reached |
| value with decimals | the SoC parser rejects it; the CCU3 value remains unchanged |
| connection reset / timeout | inverter busy, Wi-Fi issue, or TLS handshake problem |
| HTML instead of JSON | wrong port, a reverse proxy, or the WebUI is not reachable at that host |

The monitor treats all of these conservatively: if it cannot obtain a valid integer 0..100, it keeps the last good CCU3 value.

## ReGaHSS side

The CCU3 side is a single HTTP POST to:

```
http://<ccu-ip>:8181/tclrega.exe
Content-Type: application/x-www-form-urlencoded
```

The body is a raw ReGa/Tcl script. The monitor writes the SoC and, when known, the boolean:

```tcl
var s=dom.GetObject("SV_Batterie_SoC");if(s){s.State(88);}
var b=dom.GetObject("SV_Batterie_95_Signal");if(b){b.State(true);}
WriteLine("SMA_SOC_OK");
```

`WriteLine` output is used as a confirmation marker. Some firmware versions execute the script but do not return the marker; the monitor logs a warning but still treats the HTTP success as a successful write. A missing variable is ignored by the `if(s)`/`if(b)` guards rather than aborting the run.

The setup script creates the variables with `OT_VARDP`, `ivtFloat`/`istGeneric` for the number and `ivtBinary`/`istBool` for the boolean, and registers both under `ID_SYSTEM_VARIABLES`.
