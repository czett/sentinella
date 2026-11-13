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
PLUGIN_SHELLY_EM=""
if [ -n "${SENTINELLA_PLUGINS_ENV:-}" ] && [ -r "$SENTINELLA_PLUGINS_ENV" ]; then
    # shellcheck source=/dev/null
    . "$SENTINELLA_PLUGINS_ENV" 2>/dev/null || true
    case "${PLUGIN_SHELLY_EM:-}" in
        0|false|FALSE|off|OFF|no|NO)
            rm -f "$OUT_FILE" 2>/dev/null || true
            exit 0
            ;;
    esac
else
    for _pm_env in \
        "$ADDON_DIR/config/plugins.env" \
        /usr/local/addons/sentinella/config/plugins.env
    do
        if [ -r "$_pm_env" ]; then
            # shellcheck source=/dev/null
            . "$_pm_env" 2>/dev/null || true
            break
        fi
    done
fi
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

_shelly_env=${SHELLY_ENV:-$ADDON_DIR/config/plugins/shelly_em.env}
if [ -r "$_shelly_env" ]; then
    # shellcheck source=/dev/null
    . "$_shelly_env" 2>/dev/null || true
fi

if [ "$SHELLY_ENABLED" != "true" ] && [ "${PLUGIN_SHELLY_EM:-0}" != "1" ]; then
    rm -f "$OUT_FILE" 2>/dev/null || true
    exit 0
fi

mkdir -p "$STATE_DIR" 2>/dev/null || exit 0
chmod 755 "$STATE_DIR" 2>/dev/null || true

is_safe_host() {
    case "$1" in
        ''|*[!A-Za-z0-9._-]*|-*|*-|*..*|.*|*.) return 1 ;;
    esac
    return 0
}

curl_config_escape() {
    printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'
}

write_json() {
    _tmp="$OUT_FILE.tmp.$$"
    printf '%s\n' "$1" > "$_tmp" 2>/dev/null || { rm -f "$_tmp" 2>/dev/null || true; return 1; }
    chmod 644 "$_tmp" 2>/dev/null || true
    mv -f "$_tmp" "$OUT_FILE" 2>/dev/null || { rm -f "$_tmp" 2>/dev/null || true; return 1; }
}

should_write_fallback() {
    [ ! -f "$OUT_FILE" ] && return 0
    grep -Eq '"status_badge":"(SETUP|OFFLINE)"' "$OUT_FILE" 2>/dev/null
}

NOW_ISO=$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || printf '')

if [ -z "${SHELLY_BASE_URL:-}" ] && [ -z "$SHELLY_IP" ]; then
    write_json "{\"id\":\"shelly_em\",\"layout\":\"stat\",\"title\":\"SHELLY EM\",\"status_badge\":\"SETUP\",\"status_color\":\"blue\",\"timestamp\":\"$NOW_ISO\",\"primary\":{\"value\":\"SETUP\",\"unit\":\"\",\"label\":\"STATUS\"},\"secondary_metrics\":[{\"label\":\"STATUS\",\"value\":\"Enter IP\"}]}"
    exit 0
fi

if [ -n "${SHELLY_BASE_URL:-}" ]; then
    BASE_URL=$SHELLY_BASE_URL
else
    _raw_host=$(printf '%s' "$SHELLY_IP" | sed -e 's#^https\{0,1\}://##' -e 's#/.*$##')
    if ! is_safe_host "$_raw_host"; then
        write_json "{\"id\":\"shelly_em\",\"layout\":\"stat\",\"title\":\"SHELLY EM\",\"status_badge\":\"SETUP\",\"status_color\":\"blue\",\"timestamp\":\"$NOW_ISO\",\"primary\":{\"value\":\"SETUP\",\"unit\":\"\",\"label\":\"STATUS\"},\"secondary_metrics\":[{\"label\":\"STATUS\",\"value\":\"Invalid IP\"}]}"
        exit 0
    fi
    case "$SHELLY_IP" in
        https://*) BASE_URL="https://$_raw_host" ;;
        *) BASE_URL="http://$_raw_host" ;;
    esac
fi
BASE_URL=$(printf '%s' "$BASE_URL" | sed 's:/*$::')

_tls_flag=""
if [ "${SHELLY_TLS_VERIFY:-false}" = "false" ]; then
    _tls_flag="-k"
fi

SHELLY_CONNECT_TIMEOUT=${SHELLY_CONNECT_TIMEOUT:-5}
SHELLY_MAX_TIME=${SHELLY_MAX_TIME:-12}
SHELLY_HOST_LABEL=$(printf '%s' "${SHELLY_IP:-${SHELLY_BASE_URL:-}}" | tr -d '\000-\037"\\')

shelly_get() {
    _url=$1
    if [ -n "$SHELLY_PASS" ]; then
        _auth_user=${SHELLY_USER:-admin}
        _esc_auth=$(curl_config_escape "$_auth_user:$SHELLY_PASS")
        printf 'anyauth\nuser = "%s"\n' "$_esc_auth" | \
            curl $_tls_flag -fsS --connect-timeout "$SHELLY_CONNECT_TIMEOUT" --max-time "$SHELLY_MAX_TIME" \
                --config - "$_url" 2>/dev/null || true
    else
        curl $_tls_flag -fsS --connect-timeout "$SHELLY_CONNECT_TIMEOUT" --max-time "$SHELLY_MAX_TIME" \
            "$_url" </dev/null 2>/dev/null || true
    fi
}

# Try Gen2/Pro first
RAW=$(shelly_get "$BASE_URL/rpc/Shelly.GetStatus")
GEN=2
if [ -z "$RAW" ]; then
    # Fallback to Gen1
    RAW=$(shelly_get "$BASE_URL/status")
    GEN=1
fi

if [ -z "$RAW" ]; then
    if should_write_fallback; then
        write_json "{\"id\":\"shelly_em\",\"layout\":\"stat\",\"title\":\"SHELLY EM\",\"status_badge\":\"OFFLINE\",\"status_color\":\"red\",\"timestamp\":\"$NOW_ISO\",\"primary\":{\"value\":\"OFFLINE\",\"unit\":\"\",\"label\":\"STATUS\"},\"secondary_metrics\":[{\"label\":\"HOST\",\"value\":\"$SHELLY_HOST_LABEL\"},{\"label\":\"STATUS\",\"value\":\"Unreachable\"}]}"
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
        } else if (match(buf, /"act_power"[[:space:]]*:[[:space:]]*-?[0-9]+(\.[0-9]+)?/)) {
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
    if should_write_fallback; then
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
