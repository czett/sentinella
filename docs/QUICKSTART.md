# Sentinella Quickstart (CCU3 / RaspberryMatic)

This guide gets Sentinella running and connected to your first HomeMatic CCU3 automation program in under three minutes.

## 1. Choose Your Operating Mode

Configure everything directly in the browser via the **Settings (`+`)** drawer after installation, or pre-populate `.env` before running `./deploy.sh`.

| Mode | Required Settings | Hero Tile (`HERO_SOURCE`) | Dashboard Behavior |
|---|---|---|---|
| **SMA + CCU3** | `SMA_ENABLED=1`, `SMA_IP`, `SMA_PASS` | `auto` (selects `sma`) | Full battery SoC hero, hysteresis rail, SoC history (`03`), and system register (`04`) |
| **CCU3-Only (Zero Inverter)** | Leave `SMA_IP` empty or set `SMA_ENABLED=0` | `auto` (falls back to `ccu`) or `ccu` | CCU3 Duty Cycle hero; battery tiles `03` and `04` are automatically hidden |
| **Shelly EM + Dynamic Tariff** | `SHELLY_ENABLED=true`, `SHELLY_IP`, `TARIFF_ENABLED=true`, `TARIFF_PROVIDER=awattar_de` (or `awattar_at`) | `auto` (selects `shelly` if SMA off) or `shelly` | Live grid power hero + zero-config hourly spot price card in `05 Extensions` |

Tip: When pasting an address like `http://192.168.178.50/rpc` into **SMA IP** or **Shelly IP**, Sentinella automatically strips the scheme and path to `192.168.178.50`.

## 2. Automatic CCU3 System Variables

On installation (and on `sh setup_variables.sh`), Sentinella automatically creates two ReGaHSS system variables on the CCU3 if they do not yet exist, and `bin/check_soc.sh` updates them every two minutes:

- **`SV_Batterie_SoC`** *(Number / Zahl, `0..100 %`)* — Current battery state of charge from the SMA inverter.
- **`SV_Batterie_95_Signal`** *(Boolean / Logikwert, `true`/`false`)* — Hysteresis excess signal: switches to `true` (`wahr`) at `THRESHOLD_TRIGGER` (default `95 %`) and back to `false` (`falsch`) at or below `THRESHOLD_RESET` (default `90 %`).

Check live values anytime in the CCU3 WebUI under **Status und Bedienung → Systemvariable** (**Status and Control → System Variables**).

## 3. First CCU3 WebUI Program ("Programme & Verknüpfungen")

Use `SV_Batterie_95_Signal` to switch surplus consumers automatically (for example a Homematic switchable socket `HmIP-PSM`, an immersion heater relay, or a wallbox enable contact):

1. Open the CCU3 WebUI and navigate to **Programme & Verknüpfungen → Programme → Neu** (*Programs and Connections → Programs → New*).
2. Give the program a name, e.g. `PV_Ueberschuss_Verbraucher`.
3. **Bedingung: Wenn…** (*Condition: If…*):
   - Click **+** → **Systemzustand** → select **`SV_Batterie_95_Signal`**
   - Set condition to **`bei wahr`** (*when true*) and **`bei Änderung auslösen`** (*trigger on change*).
4. **Aktivität: Dann…** (*Activity: Then…*):
   - Click **+** → **Geräteauswahl** → select your actuator (e.g., **`HmIP-PSM`** / wallbox relay)
   - Set **`sofort`** (*immediately*) → **`Schaltzustand: Ein`** (*Switch state: On*).
5. Click **Sonst, wenn…** (*Else, if…*):
   - **Systemzustand** → **`SV_Batterie_95_Signal`** → **`bei falsch`** (*when false*) → **`bei Änderung auslösen`** (*trigger on change*).
6. **Aktivität: Dann…**:
   - **Geräteauswahl** → **`HmIP-PSM`** → **`sofort`** → **`Schaltzustand: Aus`** (*Switch state: Off*).
7. Save with **OK**.

Why **bei Änderung auslösen** (*on change*)? Sentinella writes to ReGa every two minutes. Triggering *on change* ensures your actuator only receives an RF command when the hysteresis state actually flips, keeping your CCU3 Duty Cycle low.

## 4. Next Steps

- **Signal Push Alerts**: Open Settings (`+`) → **03 Signal notifications**, send `I allow callmebot to send me messages` to CallMeBot, and paste the reply into **CallMeBot Onboarding Message**.
- **Custom Telemetry Cards**: Start from [`docs/PLUGIN_TEMPLATE.sh`](PLUGIN_TEMPLATE.sh) to build your own POSIX `/bin/sh` card in `plugins.d/`.
