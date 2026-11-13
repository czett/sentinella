#!/bin/sh
# =============================================================================
#  Sentinella - plugin: Dynamic Electricity Tariff (35_tibber.sh)
# =============================================================================
#  Fetches current hourly electricity price from aWATTar (DE/AT, zero-config)
#  or Tibber (GraphQL, requires API token).
#
#  Target : Homematic CCU3 / RaspberryMatic (Buildroot, BusyBox ash)
#  Needs  : /bin/sh, curl, sed, awk, tr, date, mv
#  Install: <addon>/plugins.d/35_tibber.sh, mode 755, owner root
#  Config : <addon>/config/plugins/tibber.env
# =============================================================================

PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 077

ADDON_DIR=${TIBBER_ADDON_DIR:-${SMA_MONITOR_ADDON_DIR:-}}
if [ -z "$ADDON_DIR" ]; then
    ADDON_DIR=$(cd "$(dirname "$0")/.." 2>/dev/null && pwd)
fi
[ -n "$ADDON_DIR" ] || ADDON_DIR=/usr/local/addons/sentinella
APP_DIR=$ADDON_DIR
STATE_DIR=${TIBBER_STATE_DIR:-$APP_DIR/state/plugins}
OUT_FILE=$STATE_DIR/tibber.json

# ---------------------------------------------------------------------------
# Plugin Manager Execution Guard (config/plugins.env or .disabled marker)
# ---------------------------------------------------------------------------
PLUGIN_TIBBER=""
for _pm_env in \
    "${SENTINELLA_PLUGINS_ENV:-}" \
    "$ADDON_DIR/config/plugins.env" \
    /usr/local/addons/sentinella/config/plugins.env
do
    if [ -n "$_pm_env" ] && [ -r "$_pm_env" ]; then
        # shellcheck source=/dev/null
        . "$_pm_env" 2>/dev/null || true
        break
    fi
done
case "${PLUGIN_TIBBER:-}" in
    0|false|FALSE|off|OFF|no|NO)
        rm -f "$OUT_FILE" 2>/dev/null || true
        exit 0
        ;;
esac
if [ -f "$ADDON_DIR/plugins.d/35_tibber.sh.disabled" ]; then
    rm -f "$OUT_FILE" 2>/dev/null || true
    exit 0
fi

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
TARIFF_ENABLED=false
TARIFF_PROVIDER=awattar_de
TIBBER_TOKEN=
TARIFF_SURCHARGE_CT=0
TARIFF_INTERVAL=900

_tibber_env=${TIBBER_ENV:-$ADDON_DIR/config/plugins/tibber.env}
if [ -r "$_tibber_env" ]; then
    # shellcheck source=/dev/null
    . "$_tibber_env" 2>/dev/null || true
fi

if [ "$TARIFF_ENABLED" != "true" ] && [ "${PLUGIN_TIBBER:-0}" != "1" ]; then
    rm -f "$OUT_FILE" 2>/dev/null || true
    exit 0
fi

mkdir -p "$STATE_DIR" 2>/dev/null || exit 0
chmod 755 "$STATE_DIR" 2>/dev/null || true

write_json() {
    _tmp="$OUT_FILE.tmp.$$"
    printf '%s\n' "$1" > "$_tmp" 2>/dev/null || { rm -f "$_tmp" 2>/dev/null || true; return 1; }
    chmod 644 "$_tmp" 2>/dev/null || true
    mv -f "$_tmp" "$OUT_FILE" 2>/dev/null || { rm -f "$_tmp" 2>/dev/null || true; return 1; }
}

NOW_SEC=$(date +%s 2>/dev/null || printf '0')
HOUR_START_MS=$(( (NOW_SEC / 3600) * 3600000 ))

PRICE_CT=""
PROVIDER_LABEL="aWATTar DE"

if [ "$TARIFF_PROVIDER" = "tibber" ]; then
    PROVIDER_LABEL="Tibber"
    if [ -z "$TIBBER_TOKEN" ]; then
        NOW_ISO=$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || printf '')
        write_json "{\"id\":\"tibber\",\"layout\":\"stat\",\"title\":\"DYNAMIC TARIFF\",\"status_badge\":\"OFFLINE\",\"status_color\":\"neutral\",\"timestamp\":\"$NOW_ISO\",\"primary\":{\"value\":\"--\",\"unit\":\"ct/kWh\",\"label\":\"PRICE\"},\"secondary_metrics\":[{\"label\":\"PROVIDER\",\"value\":\"Tibber\"},{\"label\":\"STATUS\",\"value\":\"Missing token\"}]}"
        exit 0
    fi
    TIBBER_URL=${TIBBER_API_URL:-https://api.tibber.com/v1-beta/gql}
    GQL='{"query":"{ viewer { homes { currentSubscription { priceInfo { current { total level } } } } } }"}'
    RAW=$(curl -kfsS --connect-timeout 5 --max-time 12 \
        -H "Authorization: Bearer $TIBBER_TOKEN" \
        -H "Content-Type: application/json" \
        -d "$GQL" \
        "$TIBBER_URL" </dev/null 2>/dev/null || true)
    if [ -n "$RAW" ]; then
        PRICE_CT=$(printf '%s' "$RAW" | awk -v sur="${TARIFF_SURCHARGE_CT:-0}" '
        { buf = buf $0 " " }
        END {
            if (match(buf, /"total"[[:space:]]*:[[:space:]]*[0-9]+(\.[0-9]+)?/)) {
                s = substr(buf, RSTART, RLENGTH)
                sub(/^[^:]*:[[:space:]]*/, "", s)
                printf "%.2f\n", (s * 100.0) + (sur + 0.0)
            }
        }')
    fi
else
    case "$TARIFF_PROVIDER" in
        awattar_at)
            PROVIDER_LABEL="aWATTar AT"
            AWATTAR_URL=${AWATTAR_API_URL:-https://api.awattar.at/v1/marketdata}
            ;;
        *)
            PROVIDER_LABEL="aWATTar DE"
            AWATTAR_URL=${AWATTAR_API_URL:-https://api.awattar.de/v1/marketdata}
            ;;
    esac
    RAW=$(curl -kfsS --connect-timeout 5 --max-time 12 "$AWATTAR_URL" </dev/null 2>/dev/null || true)
    if [ -n "$RAW" ]; then
        PRICE_CT=$(printf '%s' "$RAW" | awk -v hms="$HOUR_START_MS" -v sur="${TARIFF_SURCHARGE_CT:-0}" '
        { buf = buf $0 " " }
        END {
            # Split by start_timestamp
            n = split(buf, parts, "\"start_timestamp\"")
            best = ""
            for (i = 2; i <= n; i++) {
                seg = parts[i]
                if (match(seg, /^[[:space:]]*:[[:space:]]*[0-9]+/)) {
                    ts = substr(seg, RSTART, RLENGTH)
                    sub(/^[^0-9]*/, "", ts)
                    if (ts == hms || best == "") {
                        if (match(seg, /"marketprice"[[:space:]]*:[[:space:]]*-?[0-9]+(\.[0-9]+)?/)) {
                            mp = substr(seg, RSTART, RLENGTH)
                            sub(/^[^:]*:[[:space:]]*/, "", mp)
                            # EUR/MWh -> ct/kWh: divide by 10
                            best = sprintf("%.2f", (mp / 10.0) + (sur + 0.0))
                            if (ts == hms) break
                        }
                    }
                }
            }
            if (best != "") print best
        }')
    fi
fi

NOW_ISO=$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || printf '')

if [ -z "$PRICE_CT" ]; then
    if [ ! -f "$OUT_FILE" ]; then
        write_json "{\"id\":\"tibber\",\"layout\":\"stat\",\"title\":\"DYNAMIC TARIFF\",\"status_badge\":\"OFFLINE\",\"status_color\":\"red\",\"timestamp\":\"$NOW_ISO\",\"primary\":{\"value\":\"--\",\"unit\":\"ct/kWh\",\"label\":\"PRICE\"},\"secondary_metrics\":[{\"label\":\"PROVIDER\",\"value\":\"$PROVIDER_LABEL\"},{\"label\":\"SURCHARGE\",\"value\":\"${TARIFF_SURCHARGE_CT:-0} ct\"}]}"
    fi
    exit 0
fi

COLOR=$(awk -v p="$PRICE_CT" 'BEGIN { if (p < 15) print "green"; else if (p > 30) print "red"; else print "blue" }')
STATUS_TAG="NORMAL"
case "$COLOR" in
    green) STATUS_TAG="LOW" ;;
    red) STATUS_TAG="HIGH" ;;
esac

write_json "{\"id\":\"tibber\",\"layout\":\"stat\",\"title\":\"DYNAMIC TARIFF\",\"status_badge\":\"$STATUS_TAG\",\"status_color\":\"$COLOR\",\"timestamp\":\"$NOW_ISO\",\"primary\":{\"value\":\"$PRICE_CT\",\"unit\":\"ct/kWh\",\"label\":\"PRICE\"},\"secondary_metrics\":[{\"label\":\"PROVIDER\",\"value\":\"$PROVIDER_LABEL\"},{\"label\":\"SURCHARGE\",\"value\":\"${TARIFF_SURCHARGE_CT:-0} ct\"}]}"
exit 0
