#!/bin/sh
# =============================================================================
#  Sentinella - plugin: Open-Meteo Weather (15_weather.sh)
# =============================================================================
#  Lightweight, keyless local weather telemetry plugin using Open-Meteo.
#  Collects current temperature, relative humidity, WMO weather condition code,
#  wind speed, and daily min/max temperature.
#
#  Target : Homematic CCU3 / RaspberryMatic (Buildroot, BusyBox ash)
#  Needs  : /bin/sh, curl, sed, awk, tr, date, mv
#  Install: <addon>/plugins.d/15_weather.sh, mode 755, owner root
#  Config : <addon>/config/plugins/weather.env (optional)
#
#  The card is written atomically to <addon>/state/plugins/weather.json.
#  A failed request never removes an existing card; if no card exists yet, a
#  neutral "OFFLINE" card is published so the dashboard stays consistent.
#
#  Exit codes:
#    0  success, or a handled network/parse failure (existing card kept)
# =============================================================================

PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 077

ADDON_DIR=${WEATHER_ADDON_DIR:-${SMA_MONITOR_ADDON_DIR:-}}
if [ -z "$ADDON_DIR" ]; then
    ADDON_DIR=$(cd "$(dirname "$0")/.." 2>/dev/null && pwd)
fi
[ -n "$ADDON_DIR" ] || ADDON_DIR=/usr/local/addons/sentinella
APP_DIR=$ADDON_DIR
STATE_DIR=${WEATHER_STATE_DIR:-$ADDON_DIR/state/plugins}
OUT_FILE=$STATE_DIR/weather.json

# ---------------------------------------------------------------------------
# Plugin Manager Execution Guard (config/plugins.env or .disabled marker)
# ---------------------------------------------------------------------------
if [ "${1:-}" != "--print-coords" ] && [ "${WEATHER_PRINT_COORDS:-0}" != "1" ]; then
    PLUGIN_WEATHER=1
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
    case "${PLUGIN_WEATHER:-1}" in
        0|false|FALSE|off|OFF|no|NO)
            rm -f "$OUT_FILE" 2>/dev/null || true
            exit 0
            ;;
    esac
    if [ -f "$ADDON_DIR/plugins.d/15_weather.sh.disabled" ]; then
        rm -f "$OUT_FILE" 2>/dev/null || true
        exit 0
    fi
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
# Configuration (first readable file wins, fallback to forecast_solar.env)
# ---------------------------------------------------------------------------
CONFIG_FILE=${WEATHER_ENV:-}
if [ -z "$CONFIG_FILE" ]; then
    for _candidate in \
        "$ADDON_DIR/config/plugins/weather.env" \
        /usr/local/addons/sentinella/config/plugins/weather.env
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
    I18N_TITLE="WEATHER"
    I18N_LABEL_PRIMARY="TEMPERATURE"
    I18N_DEFAULT_CITY="LOCAL"
    I18N_MIN_MAX="MIN / MAX"
    I18N_HUMIDITY="HUMIDITY"
    I18N_WIND="WIND"
    I18N_OFFLINE_MSG="No connection to Open-Meteo"
else
    I18N_TITLE="WETTER"
    I18N_LABEL_PRIMARY="TEMPERATUR"
    I18N_DEFAULT_CITY="LOKAL"
    I18N_MIN_MAX="MIN / MAX"
    I18N_HUMIDITY="FEUCHTE"
    I18N_WIND="WIND"
    I18N_OFFLINE_MSG="Keine Verbindung zu Open-Meteo"
fi

sanitize_city() {
    printf '%s' "${1:-}" | tr -d '\r\n\t"\\$`' \
        | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'
}

trim_ws() {
    printf '%s' "${1:-}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'
}

WEATHER_CITY=$(sanitize_city "${WEATHER_CITY:-}")
WEATHER_LAT=$(trim_ws "${WEATHER_LAT:-}")
WEATHER_LON=$(trim_ws "${WEATHER_LON:-}")

STATE_DIR=${WEATHER_STATE_DIR:-$ADDON_DIR/state/plugins}
OUT_FILE=$STATE_DIR/weather.json
RAW_FILE=$STATE_DIR/.weather_raw.json
LOG_FILE=${WEATHER_LOG_FILE:-$ADDON_DIR/state/weather.log}

log() {
    _msg="[$(date '+%Y-%m-%d %H:%M:%S' 2>/dev/null || date)] $*"
    if [ -n "$LOG_FILE" ]; then
        mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null || true
        printf '%s\n' "$_msg" >> "$LOG_FILE" 2>/dev/null || true
    fi
    [ -t 1 ] && printf '%s\n' "$_msg" || true
}

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

is_lat() {
    is_number "$1" && awk -v v="$1" 'BEGIN { exit !(v >= -90 && v <= 90) }'
}

is_lon() {
    is_number "$1" && awk -v v="$1" 'BEGIN { exit !(v >= -180 && v <= 180) }'
}

# ---------------------------------------------------------------------------
# Location resolution
# ---------------------------------------------------------------------------
# Optional last-resort coordinates shared with the PV forecast, used only when
# IP geolocation is unavailable (or for --print-coords, which never touches
# the network).
load_forecast_fallback() {
    FC_FILE=${FORECAST_SOLAR_ENV:-}
    if [ -z "$FC_FILE" ]; then
        for _fc_cand in \
            "$ADDON_DIR/config/plugins/forecast_solar.env" \
            /usr/local/addons/sentinella/config/plugins/forecast_solar.env
        do
            if [ -r "$_fc_cand" ]; then
                FC_FILE=$_fc_cand
                break
            fi
        done
    fi
    if [ -n "$FC_FILE" ] && [ -r "$FC_FILE" ]; then
        _fc_lat=$(sed -n 's/^[[:space:]]*FORECAST_LAT[[:space:]]*=[[:space:]]*//p' "$FC_FILE" 2>/dev/null | tail -n 1 | sed -e "s/^['\"]//" -e "s/['\"][[:space:]]*$//")
        _fc_lon=$(sed -n 's/^[[:space:]]*FORECAST_LON[[:space:]]*=[[:space:]]*//p' "$FC_FILE" 2>/dev/null | tail -n 1 | sed -e "s/^['\"]//" -e "s/['\"][[:space:]]*$//")
        [ -z "${WEATHER_LAT:-}" ] && [ -n "$_fc_lat" ] && WEATHER_LAT="$_fc_lat"
        [ -z "${WEATHER_LON:-}" ] && [ -n "$_fc_lon" ] && WEATHER_LON="$_fc_lon"
    fi
}

# Sets key=value in an env-style file while preserving unrelated comments and
# settings. The key is always one of the fixed WEATHER_* names.
shquote() {
    printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}

cache_set() {
    _cs_file=$1
    _cs_key=$2
    _cs_val=$3
    _cs_quoted=$(shquote "$_cs_val")
    awk -v key="$_cs_key" -v qval="$_cs_quoted" '
        BEGIN { done = 0 }
        $0 ~ "^[[:space:]]*" key "[[:space:]]*=" && !done {
            print key "=" qval
            done = 1
            next
        }
        { print }
        END { if (!done) print key "=" qval }
    ' "$_cs_file" > "$_cs_file.next" 2>/dev/null || return 1
    mv -f "$_cs_file.next" "$_cs_file" 2>/dev/null || return 1
    return 0
}

# Persists only the values that came from IP geolocation. Existing user
# overrides stay untouched, so a later settings save always wins.
cache_location() {
    _cache_file=$1
    _got_city=$2
    _city=$3
    _got_lat=$4
    _lat=$5
    _got_lon=$6
    _lon=$7

    [ -n "$_cache_file" ] || return 0
    mkdir -p "$(dirname "$_cache_file")" 2>/dev/null || return 0
    _cache_tmp=$_cache_file.tmp.$$
    if [ -f "$_cache_file" ]; then
        cat "$_cache_file" > "$_cache_tmp" 2>/dev/null || return 0
    else
        printf '# Sentinella - Weather plugin configuration (auto-detected location)\n' \
            > "$_cache_tmp" 2>/dev/null || return 0
    fi

    [ "$_got_city" = "1" ] && cache_set "$_cache_tmp" WEATHER_CITY "$_city"
    [ "$_got_lat" = "1" ] && cache_set "$_cache_tmp" WEATHER_LAT "$_lat"
    [ "$_got_lon" = "1" ] && cache_set "$_cache_tmp" WEATHER_LON "$_lon"

    chmod 600 "$_cache_tmp" 2>/dev/null || true
    if mv -f "$_cache_tmp" "$_cache_file" 2>/dev/null; then
        chmod 600 "$_cache_file" 2>/dev/null || true
    else
        rm -f "$_cache_tmp" 2>/dev/null || true
    fi
    return 0
}

# Fills only empty fields via https://ipapi.co/json/. A value already present
# in weather.env (from the settings drawer) is a hard user override.
resolve_ip_location() {
    [ "${WEATHER_AUTO_LOCATION:-1}" = "0" ] && return 0
    _ip_url=${WEATHER_IP_URL:-https://ipapi.co/json/}
    [ -n "$_ip_url" ] || return 0

    _ip_json=$(curl -s --connect-timeout 5 --max-time 12 "$_ip_url" </dev/null 2>/dev/null) || return 0
    [ -n "$_ip_json" ] || return 0

    _ip_city=$(printf '%s' "$_ip_json" | tr -d '\r\n' \
        | sed -n 's/.*"city"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1)
    _ip_lat=$(printf '%s' "$_ip_json" | tr -d '\r\n' \
        | sed -n 's/.*"latitude"[[:space:]]*:[[:space:]]*\(-\{0,1\}[0-9][0-9.]*\).*/\1/p' | head -n 1)
    _ip_lon=$(printf '%s' "$_ip_json" | tr -d '\r\n' \
        | sed -n 's/.*"longitude"[[:space:]]*:[[:space:]]*\(-\{0,1\}[0-9][0-9.]*\).*/\1/p' | head -n 1)

    _ip_city=$(sanitize_city "$_ip_city")
    _got_city=0
    _got_lat=0
    _got_lon=0

    if [ -z "$WEATHER_CITY" ] && [ -n "$_ip_city" ]; then
        WEATHER_CITY=$_ip_city
        _got_city=1
    fi
    if [ -z "$WEATHER_LAT" ] && is_lat "$_ip_lat"; then
        WEATHER_LAT=$_ip_lat
        _got_lat=1
    fi
    if [ -z "$WEATHER_LON" ] && is_lon "$_ip_lon"; then
        WEATHER_LON=$_ip_lon
        _got_lon=1
    fi

    if [ "$_got_city" = "1" ] || [ "$_got_lat" = "1" ] || [ "$_got_lon" = "1" ]; then
        cache_location "$WEATHER_CACHE_FILE" \
            "$_got_city" "$WEATHER_CITY" \
            "$_got_lat" "$WEATHER_LAT" \
            "$_got_lon" "$WEATHER_LON"
        log "location resolved via IP: ${WEATHER_CITY:-?} (${WEATHER_LAT:-?}, ${WEATHER_LON:-?})"
    fi
}

WEATHER_CACHE_FILE=${CONFIG_FILE:-}
[ -n "$WEATHER_CACHE_FILE" ] || WEATHER_CACHE_FILE=$ADDON_DIR/config/plugins/weather.env

WEATHER_PRINT_ONLY=0
if [ "${1:-}" = "--print-coords" ] || [ "${WEATHER_PRINT_COORDS:-0}" = "1" ]; then
    WEATHER_PRINT_ONLY=1
fi

if [ "$WEATHER_PRINT_ONLY" = "0" ] &&
        { [ -z "$WEATHER_CITY" ] || [ -z "$WEATHER_LAT" ] || [ -z "$WEATHER_LON" ]; }; then
    resolve_ip_location
fi

if [ -z "$WEATHER_LAT" ] || [ -z "$WEATHER_LON" ]; then
    load_forecast_fallback
fi
is_lat "$WEATHER_LAT" || WEATHER_LAT=51.51
is_lon "$WEATHER_LON" || WEATHER_LON=7.46
[ -n "$WEATHER_CITY" ] || WEATHER_CITY="$I18N_DEFAULT_CITY"
is_uint "$WEATHER_CONNECT_TIMEOUT" || WEATHER_CONNECT_TIMEOUT=5
is_uint "$WEATHER_MAX_TIME" || WEATHER_MAX_TIME=12

if [ "$WEATHER_PRINT_ONLY" = "1" ]; then
    printf '%s,%s\n' "$WEATHER_LAT" "$WEATHER_LON"
    exit 0
fi

mkdir -p "$STATE_DIR" 2>/dev/null || true

# ---------------------------------------------------------------------------
# Neutral fallback card
# ---------------------------------------------------------------------------
write_neutral() {
    _ts=$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null) || _ts=
    cat > "$OUT_FILE.tmp.$$" <<EOF
{
  "id": "weather",
  "layout": "stat",
  "title": "$I18N_TITLE",
  "city": "$WEATHER_CITY",
  "status_badge": "OFFLINE",
  "status_color": "neutral",
  "timestamp": "$_ts",
  "primary": { "value": "--", "unit": "°C", "label": "$I18N_LABEL_PRIMARY" },
  "secondary_metrics": [
    { "label": "$I18N_MIN_MAX", "value": "--" },
    { "label": "$I18N_HUMIDITY", "value": "--" },
    { "label": "$I18N_WIND", "value": "--" }
  ]
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
    log "WARNING: $1; keeping existing weather card"
    if [ -f "$OUT_FILE" ]; then
        exit 0
    fi
    write_neutral || true
    exit 0
}

# ---------------------------------------------------------------------------
# WMO Code Translation
# ---------------------------------------------------------------------------
# Codes:
# 0: Klar / Clear (ok / green)
# 1-3: Bewölkt / Cloudy (neutral / gray)
# 45, 48: Nebel / Fog (neutral / gray)
# 51-57: Nieselregen / Drizzle (neutral / gray)
# 61-67: Regen / Rain (neutral / gray)
# 71-77, 85, 86: Schnee / Snow (warning / red)
# 80-82: Regenschauer / Showers (neutral / gray)
# 95, 96, 99: Gewitter / Thunderstorm (warning / red)
translate_wmo() {
    _code=$1
    _badge="WETTER"
    _color="neutral"
    case "$_code" in
        0)
            if [ "$UI_LANGUAGE" = "en" ]; then _badge="CLEAR"; else _badge="KLAR"; fi
            _color="ok"
            ;;
        1|2|3)
            if [ "$UI_LANGUAGE" = "en" ]; then _badge="CLOUDY"; else _badge="BEWÖLKT"; fi
            _color="neutral"
            ;;
        45|48)
            if [ "$UI_LANGUAGE" = "en" ]; then _badge="FOG"; else _badge="NEBEL"; fi
            _color="neutral"
            ;;
        51|53|55|56|57)
            if [ "$UI_LANGUAGE" = "en" ]; then _badge="DRIZZLE"; else _badge="NIESELREGEN"; fi
            _color="neutral"
            ;;
        61|63|65|66|67)
            if [ "$UI_LANGUAGE" = "en" ]; then _badge="RAIN"; else _badge="REGEN"; fi
            _color="neutral"
            ;;
        71|73|75|77|85|86)
            if [ "$UI_LANGUAGE" = "en" ]; then _badge="SNOW"; else _badge="SCHNEE"; fi
            _color="warning"
            ;;
        80|81|82)
            if [ "$UI_LANGUAGE" = "en" ]; then _badge="RAIN"; else _badge="REGEN"; fi
            _color="neutral"
            ;;
        95|96|99)
            if [ "$UI_LANGUAGE" = "en" ]; then _badge="THUNDERSTORM"; else _badge="GEWITTER"; fi
            _color="warning"
            ;;
        *)
            if [ "$UI_LANGUAGE" = "en" ]; then _badge="WEATHER"; else _badge="WETTER"; fi
            _color="neutral"
            ;;
    esac
    printf '%s|%s\n' "$_badge" "$_color"
}

# ---------------------------------------------------------------------------
# Network Request
# ---------------------------------------------------------------------------
API_URL="https://api.open-meteo.com/v1/forecast?latitude=${WEATHER_LAT}&longitude=${WEATHER_LON}&current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m&daily=temperature_2m_max,temperature_2m_min&timezone=auto"

if [ -n "${WEATHER_MOCK_JSON:-}" ]; then
    if [ -r "${WEATHER_MOCK_JSON:-}" ]; then
        cp -f "$WEATHER_MOCK_JSON" "$RAW_FILE" 2>/dev/null || true
        HTTP_CODE=200
    else
        HTTP_CODE=000
    fi
else
    HTTP_CODE=$(curl -s -o "$RAW_FILE" -w '%{http_code}' \
        --connect-timeout "$WEATHER_CONNECT_TIMEOUT" --max-time "$WEATHER_MAX_TIME" \
        "$API_URL" </dev/null 2>/dev/null) || HTTP_CODE=000
fi

case "$HTTP_CODE" in
    200) ;;
    *) keep_or_neutral "Open-Meteo request failed (HTTP $HTTP_CODE)" ;;
esac
[ -s "$RAW_FILE" ] || keep_or_neutral "Open-Meteo returned an empty response"

# ---------------------------------------------------------------------------
# Parse JSON (POSIX awk / tr)
# ---------------------------------------------------------------------------
PARSE_RESULT=$(tr -d '\r\n' < "$RAW_FILE" | awk '
function extract_field(block, key,    p, subpart) {
    p = index(block, "\"" key "\"")
    if (p == 0) return ""
    subpart = substr(block, p + length(key) + 2)
    sub(/^[ \t]*:[ \t]*\[?[ \t]*/, "", subpart)
    sub(/[^0-9.-].*$/, "", subpart)
    return subpart
}
{
    c_start = index($0, "\"current\"")
    if (c_start > 0) {
        c_part = substr($0, c_start)
        c_b1 = index(c_part, "{")
        c_b2 = index(c_part, "}")
        curr = substr(c_part, c_b1, c_b2 - c_b1 + 1)
    }

    d_start = index($0, "\"daily\"")
    if (d_start > 0) {
        d_part = substr($0, d_start)
        d_b1 = index(d_part, "{")
        d_b2 = index(d_part, "}")
        daily = substr(d_part, d_b1, d_b2 - d_b1 + 1)
    }

    temp = extract_field(curr, "temperature_2m")
    rh = extract_field(curr, "relative_humidity_2m")
    wcode = extract_field(curr, "weather_code")
    wind = extract_field(curr, "wind_speed_10m")

    tmax = extract_field(daily, "temperature_2m_max")
    tmin = extract_field(daily, "temperature_2m_min")

    printf "%s|%s|%s|%s|%s|%s\n", temp, rh, wcode, wind, tmax, tmin
}
')

IFS='|' read -r CUR_TEMP CUR_RH CUR_WCODE CUR_WIND CUR_TMAX CUR_TMIN <<EOF
$PARSE_RESULT
EOF

is_number "$CUR_TEMP" || keep_or_neutral "Open-Meteo current temperature missing or invalid"

WMO_INFO=$(translate_wmo "${CUR_WCODE:-999}")
IFS='|' read -r BADGE_TEXT BADGE_COLOR <<EOF
$WMO_INFO
EOF

# Format metrics nicely
FORMATTED_DATA=$(awk -v temp="$CUR_TEMP" -v rh="$CUR_RH" -v wind="$CUR_WIND" \
    -v tmin="$CUR_TMIN" -v tmax="$CUR_TMAX" \
    'BEGIN {
    # Temperature formatted to 1 decimal place if numeric
    temp_fmt = sprintf("%.1f", temp + 0);

    # Min/max formatting
    if (tmin != "" && tmax != "") {
        min_max_fmt = sprintf("%.0f°C / %.0f°C", tmin + 0, tmax + 0);
    } else {
        min_max_fmt = "--";
    }

    # Humidity formatting
    if (rh != "") {
        rh_fmt = sprintf("%.0f%%", rh + 0);
    } else {
        rh_fmt = "--";
    }

    # Wind speed formatting
    if (wind != "") {
        wind_fmt = sprintf("%.0f km/h", wind + 0);
    } else {
        wind_fmt = "--";
    }

    printf "%s|%s|%s|%s\n", temp_fmt, min_max_fmt, rh_fmt, wind_fmt;
}')

IFS='|' read -r FINAL_TEMP FINAL_MINMAX FINAL_RH FINAL_WIND <<EOF
$FORMATTED_DATA
EOF

NOW_ISO=$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null) || NOW_ISO=

# ---------------------------------------------------------------------------
# Atomic Publish
# ---------------------------------------------------------------------------
cat > "$OUT_FILE.tmp.$$" <<EOF
{
  "id": "weather",
  "layout": "stat",
  "title": "$I18N_TITLE",
  "city": "$WEATHER_CITY",
  "status_badge": "$BADGE_TEXT",
  "status_color": "$BADGE_COLOR",
  "timestamp": "$NOW_ISO",
  "primary": { "value": "$FINAL_TEMP", "unit": "°C", "label": "$I18N_LABEL_PRIMARY" },
  "secondary_metrics": [
    { "label": "$I18N_MIN_MAX", "value": "$FINAL_MINMAX" },
    { "label": "$I18N_HUMIDITY", "value": "$FINAL_RH" },
    { "label": "$I18N_WIND", "value": "$FINAL_WIND" }
  ]
}
EOF

if mv -f "$OUT_FILE.tmp.$$" "$OUT_FILE" 2>/dev/null; then
    chmod 644 "$OUT_FILE" 2>/dev/null || true
    log "weather card updated: ${FINAL_TEMP}°C, badge ${BADGE_TEXT} (${BADGE_COLOR})"
else
    rm -f "$OUT_FILE.tmp.$$" 2>/dev/null || true
    log "WARNING: could not publish $OUT_FILE"
fi

exit 0
