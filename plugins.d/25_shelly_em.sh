#!/bin/sh
# =============================================================================
#  Sentinella - plugin: Shelly EM / Pro 3EM (25_shelly_em.sh)
# =============================================================================
#  Reads live grid/load power (W) and phase breakdown from a local Shelly
#  Gen2/Pro (/rpc/Shelly.GetStatus) or Gen1 (/status) energy meter.
#
#  Target : Homematic CCU3 / RaspberryMatic (Buildroot, BusyBox ash)
#  Needs  : /bin/sh, curl, sed, awk, tr, date, mv
#  Install: <addon>/plugins.d/25_shelly_em.sh, mode 755, owner root
#  Config : <addon>/config/plugins/shelly_em.env
# =============================================================================

PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 077

ADDON_DIR=${SHELLY_ADDON_DIR:-${SMA_MONITOR_ADDON_DIR:-}}
if [ -z "$ADDON_DIR" ]; then
    ADDON_DIR=$(cd "$(dirname "$0")/.." 2>/dev/null && pwd)
fi
[ -n "$ADDON_DIR" ] || ADDON_DIR=/usr/local/addons/sentinella
APP_DIR=$ADDON_DIR
STATE_DIR=${SHELLY_STATE_DIR:-$APP_DIR/state/plugins}
OUT_FILE=$STATE_DIR/shelly_em.json

# ---------------------------------------------------------------------------
# Plugin Manager Execution Guard (config/plugins.env or .disabled marker)
# ---------------------------------------------------------------------------
PLUGIN_SHELLY_EM=0
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
case "${PLUGIN_SHELLY_EM:-0}" in
    0|false|FALSE|off|OFF|no|NO)
        rm -f "$OUT_FILE" 2>/dev/null || true
        exit 0
        ;;
esac
if [ -f "$ADDON_DIR/plugins.d/25_shelly_em.sh.disabled" ]; then
    rm -f "$OUT_FILE" 2>/dev/null || true
    exit 0
fi

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SHELLY_ENABLED=false
SHELLY_IP=
SHELLY_USER=
SHELLY_PASS=
SHELLY_INTERVAL=60

if [ -r "$ADDON_DIR/config/plugins/shelly_em.env" ]; then
    # shellcheck source=/dev/null
    . "$ADDON_DIR/config/plugins/shelly_em.env" 2>/dev/null || true
fi

if [ "$SHELLY_ENABLED" != "true" ] && [ "${PLUGIN_SHELLY_EM:-0}" != "1" ]; then
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

NOW_ISO=$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || printf '')

if [ -z "$SHELLY_BASE_URL" ] && [ -z "$SHELLY_IP" ]; then
    write_json "{\"id\":\"shelly_em\",\"layout\":\"stat\",\"title\":\"SHELLY EM\",\"status_badge\":\"OFFLINE\",\"status_color\":\"neutral\",\"timestamp\":\"$NOW_ISO\",\"primary\":{\"value\":\"--\",\"unit\":\"W\",\"label\":\"GRID POWER\"},\"secondary_metrics\":[{\"label\":\"STATUS\",\"value\":\"No IP configured\"}]}"
    exit 0
fi

BASE_URL=${SHELLY_BASE_URL:-http://$SHELLY_IP}
BASE_URL=$(printf '%s' "$BASE_URL" | sed 's:/*$::')

CURL_AUTH=""
if [ -n "$SHELLY_USER" ] && [ -n "$SHELLY_PASS" ]; then
    CURL_AUTH="--anyauth -u $SHELLY_USER:$SHELLY_PASS"
fi

# Try Gen2/Pro first
# shellcheck disable=SC2086
RAW=$(curl -kfsS --connect-timeout 5 --max-time 12 $CURL_AUTH "$BASE_URL/rpc/Shelly.GetStatus" </dev/null 2>/dev/null || true)
GEN=2
if [ -z "$RAW" ]; then
    # Fallback to Gen1
    # shellcheck disable=SC2086
    RAW=$(curl -kfsS --connect-timeout 5 --max-time 12 $CURL_AUTH "$BASE_URL/status" </dev/null 2>/dev/null || true)
    GEN=1
fi

if [ -z "$RAW" ]; then
    if [ ! -f "$OUT_FILE" ]; then
        write_json "{\"id\":\"shelly_em\",\"layout\":\"stat\",\"title\":\"SHELLY EM\",\"status_badge\":\"OFFLINE\",\"status_color\":\"red\",\"timestamp\":\"$NOW_ISO\",\"primary\":{\"value\":\"--\",\"unit\":\"W\",\"label\":\"GRID POWER\"},\"secondary_metrics\":[{\"label\":\"STATUS\",\"value\":\"Unreachable\"}]}"
    fi
    exit 0
fi

# Parse power values with awk
PARSED=$(printf '%s' "$RAW" | awk -v gen="$GEN" '
{
    buf = buf $0 " "
}
END {
    total = ""; pa = ""; pb = ""; pc = ""
    if (gen == 2) {
        if (match(buf, /"total_act_power"[[:space:]]*:[[:space:]]*-?[0-9]+(\.[0-9]+)?/)) {
            s = substr(buf, RSTART, RLENGTH)
            sub(/^[^:]*:[[:space:]]*/, "", s)
            total = s
        } else if (match(buf, /"apower"[[:space:]]*:[[:space:]]*-?[0-9]+(\.[0-9]+)?/)) {
            s = substr(buf, RSTART, RLENGTH)
            sub(/^[^:]*:[[:space:]]*/, "", s)
            total = s
        }
        if (match(buf, /"a_act_power"[[:space:]]*:[[:space:]]*-?[0-9]+(\.[0-9]+)?/)) {
            s = substr(buf, RSTART, RLENGTH); sub(/^[^:]*:[[:space:]]*/, "", s); pa = s
        }
        if (match(buf, /"b_act_power"[[:space:]]*:[[:space:]]*-?[0-9]+(\.[0-9]+)?/)) {
            s = substr(buf, RSTART, RLENGTH); sub(/^[^:]*:[[:space:]]*/, "", s); pb = s
        }
        if (match(buf, /"c_act_power"[[:space:]]*:[[:space:]]*-?[0-9]+(\.[0-9]+)?/)) {
            s = substr(buf, RSTART, RLENGTH); sub(/^[^:]*:[[:space:]]*/, "", s); pc = s
        }
    } else {
        if (match(buf, /"total_power"[[:space:]]*:[[:space:]]*-?[0-9]+(\.[0-9]+)?/)) {
            s = substr(buf, RSTART, RLENGTH)
            sub(/^[^:]*:[[:space:]]*/, "", s)
            total = s
        } else if (match(buf, /"power"[[:space:]]*:[[:space:]]*-?[0-9]+(\.[0-9]+)?/)) {
            s = substr(buf, RSTART, RLENGTH)
            sub(/^[^:]*:[[:space:]]*/, "", s)
            total = s
        }
    }
    if (total != "") {
        printf "%d|%s|%s|%s\n", int(total + (total >= 0 ? 0.5 : -0.5)), pa, pb, pc
    }
}')

if [ -z "$PARSED" ]; then
    if [ ! -f "$OUT_FILE" ]; then
        write_json "{\"id\":\"shelly_em\",\"layout\":\"stat\",\"title\":\"SHELLY EM\",\"status_badge\":\"OFFLINE\",\"status_color\":\"neutral\",\"timestamp\":\"$NOW_ISO\",\"primary\":{\"value\":\"--\",\"unit\":\"W\",\"label\":\"GRID POWER\"},\"secondary_metrics\":[{\"label\":\"STATUS\",\"value\":\"Parse error\"}]}"
    fi
    exit 0
fi

IFS='|' read -r TOTAL_W PA PB PC <<EOF
$PARSED
EOF

if [ "$TOTAL_W" -lt -10 ] 2>/dev/null; then
    STATUS_TAG="FEED-IN"
    COLOR="green"
elif [ "$TOTAL_W" -gt 10 ] 2>/dev/null; then
    STATUS_TAG="GRID DRAW"
    COLOR="blue"
else
    STATUS_TAG="IDLE"
    COLOR="neutral"
fi

METRICS="{\"label\":\"GEN\",\"value\":\"Gen$GEN\"}"
if [ -n "$PA" ]; then
    METRICS="$METRICS,{\"label\":\"L1\",\"value\":\"${PA} W\"}"
fi
if [ -n "$PB" ]; then
    METRICS="$METRICS,{\"label\":\"L2\",\"value\":\"${PB} W\"}"
fi
if [ -n "$PC" ]; then
    METRICS="$METRICS,{\"label\":\"L3\",\"value\":\"${PC} W\"}"
fi

write_json "{\"id\":\"shelly_em\",\"layout\":\"stat\",\"title\":\"SHELLY EM\",\"status_badge\":\"$STATUS_TAG\",\"status_color\":\"$COLOR\",\"timestamp\":\"$NOW_ISO\",\"primary\":{\"value\":$TOTAL_W,\"unit\":\"W\",\"label\":\"GRID POWER\"},\"secondary_metrics\":[$METRICS]}"
exit 0
