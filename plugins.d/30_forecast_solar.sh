#!/bin/sh
# =============================================================================
#  Sentinella - plugin: forecast.solar
# =============================================================================
#  Publishes a dashboard card with the expected solar yield for today and
#  tomorrow from the free forecast.solar REST API. No API key is required.
#
#  Target : Homematic CCU3 / RaspberryMatic (Buildroot, BusyBox ash)
#  Needs  : /bin/sh, curl, sed, awk, tr, date, mv
#  Install: <addon>/plugins.d/30_forecast_solar.sh, mode 755, owner root
#  Config : <addon>/config/plugins/forecast_solar.env (optional)
#
#  The card is written atomically to <addon>/state/plugins/forecast_solar.json.
#  A failed request never removes an existing card; if no card exists yet, a
#  neutral "OFFLINE" card is published so the dashboard stays consistent.
#
#  Exit codes:
#    0  success, or a handled network/parse failure (existing card kept)
# =============================================================================

PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 077

ADDON_DIR=${FORECAST_ADDON_DIR:-${SMA_MONITOR_ADDON_DIR:-}}
if [ -z "$ADDON_DIR" ]; then
    ADDON_DIR=$(cd "$(dirname "$0")/.." 2>/dev/null && pwd)
fi
[ -n "$ADDON_DIR" ] || ADDON_DIR=/usr/local/addons/sentinella
APP_DIR=$ADDON_DIR
STATE_DIR=${FORECAST_STATE_DIR:-$APP_DIR/state/plugins}
OUT_FILE=$STATE_DIR/forecast_solar.json

# ---------------------------------------------------------------------------
# Plugin Manager Execution Guard (config/plugins.env or .disabled marker)
# ---------------------------------------------------------------------------
PLUGIN_FORECAST_SOLAR=0
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
case "${PLUGIN_FORECAST_SOLAR:-0}" in
    0|false|FALSE|off|OFF|no|NO)
        rm -f "$OUT_FILE" 2>/dev/null || true
        exit 0
        ;;
esac
if [ -f "$ADDON_DIR/plugins.d/30_forecast_solar.sh.disabled" ]; then
    rm -f "$OUT_FILE" 2>/dev/null || true
    exit 0
fi

# ---------------------------------------------------------------------------
# UI Language & Localized String Maps (DE / EN)
# ---------------------------------------------------------------------------
load_ui_language() {
    _lang=${UI_LANGUAGE:-}
    if [ -z "$_lang" ]; then
        for _env_cand in \
            "${SMA_MONITOR_ENV:-}" \
            "$ADDON_DIR/config/config.env" \
            "$ADDON_DIR/.env" \
            "$ADDON_DIR/config.env" \
            /usr/local/addons/sentinella/config/config.env \
            /usr/local/addons/sentinella/.env
        do
            [ -n "$_env_cand" ] && [ -r "$_env_cand" ] || continue
            _raw=$(sed -n 's/^[[:space:]]*UI_LANGUAGE[[:space:]]*=[[:space:]]*//p' "$_env_cand" 2>/dev/null | tail -n 1)
            _raw=$(printf '%s' "$_raw" | sed -e "s/^['\"]//" -e "s/['\"][[:space:]]*$//")
            if [ -n "$_raw" ]; then
                _lang=$_raw
                break
            fi
        done
    fi
    case "$_lang" in
        en|EN|en_US|en-US|en_GB|en-GB) UI_LANGUAGE=en ;;
        *) UI_LANGUAGE=de ;;
    esac
}

load_ui_language

# ---------------------------------------------------------------------------
# Configuration (first readable file wins)
# ---------------------------------------------------------------------------
FORECAST_LAT=${FORECAST_LAT:-}
FORECAST_LON=${FORECAST_LON:-}
FORECAST_DEC=${FORECAST_DEC:-}
FORECAST_AZ=${FORECAST_AZ:-}
FORECAST_KWP=${FORECAST_KWP:-}

CONFIG_FILE=${FORECAST_SOLAR_ENV:-}
if [ -z "$CONFIG_FILE" ]; then
    for _candidate in \
        "$ADDON_DIR/config/plugins/forecast_solar.env" \
        /usr/local/addons/sentinella/config/plugins/forecast_solar.env
    do
        if [ -r "$_candidate" ]; then
            CONFIG_FILE=$_candidate
            break
        fi
    done
fi
if [ -n "$CONFIG_FILE" ] && [ -r "$CONFIG_FILE" ]; then
    chmod 600 "$CONFIG_FILE" 2>/dev/null || true
    # shellcheck source=/dev/null
    . "$CONFIG_FILE"
fi

case "${UI_LANGUAGE:-}" in
    en|EN|en_US|en-US|en_GB|en-GB) UI_LANGUAGE=en ;;
    *) UI_LANGUAGE=de ;;
esac

if [ "$UI_LANGUAGE" = "en" ]; then
    I18N_TITLE="SOLAR FORECAST (EXP)"
    I18N_LABEL="TODAY FORECAST"
    I18N_COMPARISON="vs yesterday"
    I18N_BADGE_SUNNY="SUNNY [EXP]"
    I18N_BADGE_MODERATE="MODERATE [EXP]"
    I18N_BADGE_CLOUDY="CLOUDY [EXP]"
    I18N_METRIC_TOMORROW="TOMORROW"
    I18N_METRIC_PEAK="PEAK"
    I18N_OFFLINE_COMP="No connection to forecast.solar"
    I18N_SETUP_VAL="CONFIG"
    I18N_SETUP_MSG="Configure in Settings"
else
    I18N_TITLE="PV-PROGNOSE (EXP)"
    I18N_LABEL="PROGNOSE HEUTE"
    I18N_COMPARISON="von gestern"
    I18N_BADGE_SUNNY="SONNIG [EXP]"
    I18N_BADGE_MODERATE="MODERAT [EXP]"
    I18N_BADGE_CLOUDY="BEWÖLKT [EXP]"
    I18N_METRIC_TOMORROW="MORGEN"
    I18N_METRIC_PEAK="SPITZE"
    I18N_OFFLINE_COMP="Keine Verbindung zu forecast.solar"
    I18N_SETUP_VAL="KONFIG"
    I18N_SETUP_MSG="Parameter in Einstellungen definieren"
fi

API_BASE=${FORECAST_API_URL:-https://api.forecast.solar/estimate}
CONNECT_TIMEOUT=${FORECAST_CONNECT_TIMEOUT:-5}
MAX_TIME=${FORECAST_MAX_TIME:-12}

STATE_DIR=${FORECAST_STATE_DIR:-$APP_DIR/state/plugins}
OUT_FILE=$STATE_DIR/forecast_solar.json
# Private scratch file beside the state, never in world-writable /tmp.
RAW_FILE=$STATE_DIR/.forecast_raw.$$.tmp

log() {
    _ts=$(date '+%Y-%m-%d %H:%M:%S' 2>/dev/null)
    [ -n "$_ts" ] || _ts=-
    printf '%s %s\n' "$_ts" "$*" >&2
    if command -v logger >/dev/null 2>&1; then
        logger -t sentinella-forecast "$*" 2>/dev/null || true
    fi
}

cleanup() {
    rm -f "$RAW_FILE" "$OUT_FILE.tmp.$$" 2>/dev/null || true
}
trap cleanup EXIT HUP INT TERM

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------
is_number() {
    case "$1" in
        ''|*[!0-9.-]*) return 1 ;;
    esac
    _rest=${1#-}
    case "$_rest" in
        ''|*[!0-9.]*|*.*.*|*..*|*.|*.-*|*--*) return 1 ;;
    esac
    return 0
}

is_uint() {
    case "$1" in
        ''|*[!0-9]*) return 1 ;;
    esac
    return 0
}

mkdir -p "$STATE_DIR" 2>/dev/null || true

# ---------------------------------------------------------------------------
# Setup required card (no hardcoded coordinates or kWp defaults)
# ---------------------------------------------------------------------------
write_setup() {
    _ts=$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null) || _ts=
    cat > "$OUT_FILE.tmp.$$" <<EOF
{
  "id": "forecast_solar",
  "layout": "delta",
  "title": "$I18N_TITLE",
  "status_badge": "SETUP",
  "status_color": "blue",
  "timestamp": "$_ts",
  "primary": { "value": "$I18N_SETUP_VAL", "unit": "", "label": "$I18N_SETUP_MSG" },
  "delta": { "direction": "neutral", "value": "", "comparison": "$I18N_SETUP_MSG" },
  "secondary_metrics": []
}
EOF
    if mv -f "$OUT_FILE.tmp.$$" "$OUT_FILE" 2>/dev/null; then
        chmod 644 "$OUT_FILE" 2>/dev/null || true
        return 0
    fi
    rm -f "$OUT_FILE.tmp.$$" 2>/dev/null || true
    return 1
}

if ! is_number "$FORECAST_LAT" || ! is_number "$FORECAST_LON" || ! is_number "$FORECAST_KWP"; then
    write_setup || true
    exit 0
fi
if ! awk -v k="$FORECAST_KWP" 'BEGIN { exit !(k > 0) }'; then
    write_setup || true
    exit 0
fi

is_number "$FORECAST_DEC" || FORECAST_DEC=35
is_number "$FORECAST_AZ" || FORECAST_AZ=0
is_uint "$CONNECT_TIMEOUT" || CONNECT_TIMEOUT=5
is_uint "$MAX_TIME" || MAX_TIME=12

# The API base is concatenated into the request URL. Reject anything that is
# not a plain http(s) origin/path without whitespace or control characters.
case "$API_BASE" in
    http://*|https://*) ;;
    *) API_BASE=https://api.forecast.solar/estimate ;;
esac
case "$API_BASE" in
    *[[:space:]]*|*[[:cntrl:]]*) API_BASE=https://api.forecast.solar/estimate ;;
esac

# ---------------------------------------------------------------------------
# Neutral fallback card
# ---------------------------------------------------------------------------
write_neutral() {
    _ts=$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null) || _ts=
    cat > "$OUT_FILE.tmp.$$" <<EOF
{
  "id": "forecast_solar",
  "layout": "delta",
  "title": "$I18N_TITLE",
  "status_badge": "OFFLINE",
  "status_color": "neutral",
  "timestamp": "$_ts",
  "primary": { "value": 0, "unit": "kWh", "label": "$I18N_LABEL" },
  "delta": { "direction": "neutral", "value": "", "comparison": "$I18N_OFFLINE_COMP" },
  "secondary_metrics": []
}
EOF
    if mv -f "$OUT_FILE.tmp.$$" "$OUT_FILE" 2>/dev/null; then
        chmod 644 "$OUT_FILE" 2>/dev/null || true
        return 0
    fi
    rm -f "$OUT_FILE.tmp.$$" 2>/dev/null || true
    return 1
}

keep_or_neutral() {
    log "WARNING: $1; keeping existing forecast card"
    if [ -f "$OUT_FILE" ]; then
        exit 0
    fi
    write_neutral || true
    exit 0
}

# ---------------------------------------------------------------------------
# Single lightweight request
# ---------------------------------------------------------------------------
URL="$API_BASE/$FORECAST_LAT/$FORECAST_LON/$FORECAST_DEC/$FORECAST_AZ/$FORECAST_KWP"
HTTP_CODE=$(curl -s -o "$RAW_FILE" -w '%{http_code}' \
    --connect-timeout "$CONNECT_TIMEOUT" --max-time "$MAX_TIME" \
    "$URL" </dev/null 2>/dev/null) || HTTP_CODE=000

case "$HTTP_CODE" in
    200) ;;
    *) keep_or_neutral "forecast.solar request failed (HTTP $HTTP_CODE)" ;;
esac
[ -s "$RAW_FILE" ] || keep_or_neutral "forecast.solar returned an empty response"

# ---------------------------------------------------------------------------
# Extract "watt_hours_day": { "YYYY-MM-DD": Wh, ... }
# ---------------------------------------------------------------------------
extract_day_map() {
    tr -d '\r\n' < "$RAW_FILE" |
        sed -e "s/.*\"$1\"[[:space:]]*:[[:space:]]*{//" -e 's/}.*//' |
        tr ',' '\n' |
        sed -n 's/^[[:space:]]*"\([0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]\)"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*$/\1 \2/p'
}

DAY_MAP=$(extract_day_map watt_hours_day)
[ -n "$DAY_MAP" ] || keep_or_neutral "forecast.solar response has no daily yield data"

TODAY=$(date '+%Y-%m-%d' 2>/dev/null) || TODAY=
TODAY_KEY=$TODAY
TODAY_WH=$(printf '%s\n' "$DAY_MAP" | awk -v d="$TODAY" '$1 == d { print $2; exit }')
if ! is_uint "$TODAY_WH"; then
    # The API timezone can be one day ahead/behind the CCU clock: use the
    # first available day rather than dropping the whole card.
    TODAY_KEY=$(printf '%s\n' "$DAY_MAP" | awk 'NR == 1 { print $1; exit }')
    TODAY_WH=$(printf '%s\n' "$DAY_MAP" | awk 'NR == 1 { print $2; exit }')
fi
is_uint "$TODAY_WH" || keep_or_neutral "forecast.solar daily yield is not numeric"

TOMORROW_WH=$(printf '%s\n' "$DAY_MAP" | awk -v d="$TODAY_KEY" '$1 > d { print $2; exit }')
is_uint "$TOMORROW_WH" || TOMORROW_WH=0

# ---------------------------------------------------------------------------
# Layout values (awk keeps the float maths portable)
# ---------------------------------------------------------------------------
CALC=$(awk -v t="$TODAY_WH" -v m="$TOMORROW_WH" -v k="$FORECAST_KWP" \
    -v b_sunny="$I18N_BADGE_SUNNY" \
    -v b_mod="$I18N_BADGE_MODERATE" \
    -v b_cloudy="$I18N_BADGE_CLOUDY" \
    'BEGIN {
    tk = t / 1000;
    mk = m / 1000;
    d = mk - tk;

    if (d > 0.049) dir = "up";
    else if (d < -0.049) dir = "down";
    else dir = "neutral";

    if (d > 0.049) ds = sprintf("+%.1f kWh", d);
    else if (d < -0.049) ds = sprintf("%.1f kWh", d);
    else ds = "0.0 kWh";

    ratio = (k > 0) ? tk / k : 0;
    if (t <= 0) { badge = b_cloudy; color = "neutral"; }
    else if (ratio >= 2.5) { badge = b_sunny; color = "green"; }
    else if (ratio >= 1.0) { badge = b_mod; color = "blue"; }
    else { badge = b_cloudy; color = "neutral"; }

    printf "%.1f|%.1f|%s|%s|%s|%s|%.2f\n",
        tk, mk, dir, ds, badge, color, ratio;
}')

IFS='|' read -r TODAY_KWH TOMORROW_KWH DELTA_DIR DELTA_VALUE BADGE COLOR RATIO <<EOF
$CALC
EOF

case "$DELTA_DIR" in
    up|down|neutral) ;;
    *) DELTA_DIR=neutral ;;
esac
case "$COLOR" in
    green|blue|red|neutral) ;;
    *) COLOR=neutral ;;
esac
[ -n "$BADGE" ] || BADGE="$I18N_BADGE_CLOUDY"

NOW_ISO=$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null) || NOW_ISO=

# ---------------------------------------------------------------------------
# Atomic publish
# ---------------------------------------------------------------------------
cat > "$OUT_FILE.tmp.$$" <<EOF
{
  "id": "forecast_solar",
  "layout": "delta",
  "title": "$I18N_TITLE",
  "status_badge": "$BADGE",
  "status_color": "$COLOR",
  "timestamp": "$NOW_ISO",
  "primary": { "value": $TODAY_KWH, "unit": "kWh", "label": "$I18N_LABEL" },
  "delta": {
    "direction": "$DELTA_DIR",
    "value": "$DELTA_VALUE",
    "comparison": "$I18N_COMPARISON"
  },
  "secondary_metrics": [
    { "label": "$I18N_METRIC_TOMORROW", "value": "$TOMORROW_KWH", "unit": "kWh" },
    { "label": "$I18N_METRIC_PEAK", "value": "$RATIO", "unit": "kWh/kWp" }
  ]
}
EOF

if mv -f "$OUT_FILE.tmp.$$" "$OUT_FILE" 2>/dev/null; then
    chmod 644 "$OUT_FILE" 2>/dev/null || true
    log "forecast card updated: today ${TODAY_KWH} kWh, tomorrow ${TOMORROW_KWH} kWh"
else
    rm -f "$OUT_FILE.tmp.$$" 2>/dev/null || true
    log "WARNING: could not publish $OUT_FILE"
fi

exit 0
