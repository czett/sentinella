#!/bin/sh
# =============================================================================
#  Sentinella - plugin: Renault Zoe (MY Renault / Gigya + Kamereon)
# =============================================================================
#  Fetches live EV telemetry (batteryLevel, batteryAutonomy, plugStatus,
#  chargingStatus) from the MY Renault cloud API and publishes a stat card.
#
#  Target : Homematic CCU3 / RaspberryMatic (Buildroot, BusyBox ash)
#  Needs  : /bin/sh, curl, sed, grep, awk, tr, date, mv, chmod
#  Install: <addon>/plugins.d/20_renault_zoe.sh, mode 755, owner root
#  Config : <addon>/config/plugins/renault.env (mode 600)
#  Cache  : <addon>/state/plugins/.renault_session (mode 600, 6h TTL)
#  Output : <addon>/state/plugins/renault_zoe.json
#
#  Exit codes:
#    0  success, unconfigured no-op, or handled transient network failure
#    1  connection/parse failure when invoked with --test
#    2  missing credentials when invoked with --test
# =============================================================================

PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 022

TEST_MODE=0
if [ "${1:-}" = "--test" ]; then
    TEST_MODE=1
fi

ADDON_DIR=${RENAULT_ADDON_DIR:-${SMA_MONITOR_ADDON_DIR:-}}
if [ -z "$ADDON_DIR" ]; then
    ADDON_DIR=$(cd "$(dirname "$0")/.." 2>/dev/null && pwd)
fi
[ -n "$ADDON_DIR" ] || ADDON_DIR=/usr/local/addons/sentinella
APP_DIR=$ADDON_DIR

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
CONFIG_FILE=${RENAULT_ENV:-}
if [ -z "$CONFIG_FILE" ]; then
    for _candidate in \
        "$ADDON_DIR/config/plugins/renault.env" \
        /usr/local/addons/sentinella/config/plugins/renault.env
    do
        if [ -r "$_candidate" ]; then
            CONFIG_FILE=$_candidate
            break
        fi
    done
fi

if [ -n "$CONFIG_FILE" ] && [ -r "$CONFIG_FILE" ]; then
    # shellcheck source=/dev/null
    . "$CONFIG_FILE"
fi

trim_ws() {
    printf '%s' "${1:-}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'
}

RENAULT_USER=$(trim_ws "${RENAULT_USER:-}")
RENAULT_PASS=${RENAULT_PASS:-}
RENAULT_VIN=$(trim_ws "${RENAULT_VIN:-}" | tr '[:lower:]' '[:upper:]')

# Clean no-op when credentials are unset or incomplete
if [ -z "$RENAULT_USER" ] || [ -z "$RENAULT_PASS" ] || [ -z "$RENAULT_VIN" ]; then
    if [ "$TEST_MODE" = "1" ]; then
        printf 'Renault credentials (RENAULT_USER, RENAULT_PASS, RENAULT_VIN) are incomplete.\n' >&2
        exit 2
    fi
    exit 0
fi

GIGYA_API_KEY=${RENAULT_GIGYA_API_KEY:-3_VgdkgtIRH3AdHvJm-cjV2ug2EFE0lxt0IJzMC4MFqZjFpn_GYFXVdNZ19L7wZX0N}
KAMEREON_API_KEY=${RENAULT_KAMEREON_API_KEY:-YjkKtHmGfaceeuExUDKGxrLZGGvtVS0J}
GIGYA_URL=${RENAULT_GIGYA_URL:-https://accounts.eu1.gigya.com}
KAMEREON_URL=${RENAULT_KAMEREON_URL:-https://api-wired-prod-1-euw1.wrd-aws.com}
RENAULT_COUNTRY=${RENAULT_COUNTRY:-DE}

CONNECT_TIMEOUT=${RENAULT_CONNECT_TIMEOUT:-8}
MAX_TIME=${RENAULT_MAX_TIME:-15}
CACHE_TTL=${RENAULT_CACHE_TTL:-21600}

STATE_DIR=${RENAULT_STATE_DIR:-$ADDON_DIR/state/plugins}
SESSION_FILE=${RENAULT_SESSION_FILE:-$STATE_DIR/.renault_session}
OUT_FILE=${RENAULT_OUT_FILE:-$STATE_DIR/renault_zoe.json}
TMP_AUTH=${TMPDIR:-/tmp}/sentinella_renault_auth.$$.json
RAW_FILE=${TMPDIR:-/tmp}/sentinella_renault_bat.$$.json

log() {
    _ts=$(date '+%Y-%m-%d %H:%M:%S' 2>/dev/null)
    [ -n "$_ts" ] || _ts=-
    printf '%s %s\n' "$_ts" "$*" >&2
    if command -v logger >/dev/null 2>&1; then
        logger -t sentinella-renault "$*" 2>/dev/null || true
    fi
}

cleanup() {
    rm -f "$TMP_AUTH" "$RAW_FILE" "$OUT_FILE.tmp" "$SESSION_FILE.tmp.$$" 2>/dev/null || true
}
trap cleanup EXIT HUP INT TERM

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
is_uint() {
    case "$1" in
        ''|*[!0-9]*) return 1 ;;
    esac
    return 0
}

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

shquote() {
    printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}

is_uint "$CONNECT_TIMEOUT" || CONNECT_TIMEOUT=8
is_uint "$MAX_TIME" || MAX_TIME=15
is_uint "$CACHE_TTL" || CACHE_TTL=21600

mkdir -p "$STATE_DIR" 2>/dev/null || true

extract_json_str() {
    _file=$1
    _key=$2
    tr ',{}' '\n' < "$_file" 2>/dev/null |
        sed -n 's/^[[:space:]]*"'"$_key"'"[[:space:]]*:[[:space:]]*"\([^"]*\)".*$/\1/p' |
        head -n 1
}

extract_json_num() {
    _file=$1
    _key=$2
    tr ',{}' '\n' < "$_file" 2>/dev/null |
        sed -n 's/^[[:space:]]*"'"$_key"'"[[:space:]]*:[[:space:]]*\(-\{0,1\}[0-9][0-9]*\(\.[0-9][0-9]*\)\{0,1\}\).*$/\1/p' |
        head -n 1
}

extract_myrenault_account_id() {
    _file=$1
    _acc=$(tr -d '\r\n' < "$_file" 2>/dev/null |
        tr '}' '\n' |
        grep '"accountType"[[:space:]]*:[[:space:]]*"MYRENAULT"' |
        sed -n 's/.*"accountId"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' |
        head -n 1)
    if [ -z "$_acc" ]; then
        _acc=$(extract_json_str "$_file" accountId)
    fi
    printf '%s\n' "$_acc"
}

# ---------------------------------------------------------------------------
# Session Cache ($ADDON_DIR/state/plugins/.renault_session)
# ---------------------------------------------------------------------------
ID_TOKEN=
ACCOUNT_ID=
TOKEN_TS=

load_cached_session() {
    [ -r "$SESSION_FILE" ] || return 1
    ID_TOKEN=
    ACCOUNT_ID=
    TOKEN_TS=
    # shellcheck source=/dev/null
    . "$SESSION_FILE" 2>/dev/null || return 1
    [ -n "$ID_TOKEN" ] || return 1
    [ -n "$ACCOUNT_ID" ] || return 1
    is_uint "$TOKEN_TS" || return 1
    _now=$(date +%s 2>/dev/null)
    is_uint "$_now" || return 1
    [ "$_now" -ge "$TOKEN_TS" ] || return 1
    [ $((_now - TOKEN_TS)) -lt "$CACHE_TTL" ] || return 1
    return 0
}

save_cached_session() {
    _now=$(date +%s 2>/dev/null)
    is_uint "$_now" || _now=0
    _tmp="$SESSION_FILE.tmp.$$"
    (
        umask 077
        {
            printf 'ID_TOKEN=%s\n' "$(shquote "$ID_TOKEN")"
            printf 'ACCOUNT_ID=%s\n' "$(shquote "$ACCOUNT_ID")"
            printf 'TOKEN_TS=%s\n' "$(shquote "$_now")"
        } > "$_tmp"
    ) 2>/dev/null || {
        rm -f "$_tmp" 2>/dev/null || true
        return 1
    }
    chmod 600 "$_tmp" 2>/dev/null || true
    mv -f "$_tmp" "$SESSION_FILE" 2>/dev/null || {
        rm -f "$_tmp" 2>/dev/null || true
        return 1
    }
    return 0
}

# ---------------------------------------------------------------------------
# 3-Step Authentication Chain (Gigya login -> Gigya JWT -> Kamereon person)
# ---------------------------------------------------------------------------
authenticate_renault() {
    # 1. Gigya accounts.login -> sessionInfo.cookieValue
    _login_code=$(curl -sS -o "$TMP_AUTH" -w '%{http_code}' \
        --connect-timeout "$CONNECT_TIMEOUT" --max-time "$MAX_TIME" \
        -X POST \
        --data-urlencode "apiKey=$GIGYA_API_KEY" \
        --data-urlencode "loginID=$RENAULT_USER" \
        --data-urlencode "password=$RENAULT_PASS" \
        "$GIGYA_URL/accounts.login" 2>/dev/null) || _login_code=000

    if [ "$_login_code" != "200" ] || [ ! -s "$TMP_AUTH" ]; then
        log "WARNING: Gigya accounts.login failed (HTTP $_login_code)"
        return 1
    fi

    COOKIE_VALUE=$(extract_json_str "$TMP_AUTH" cookieValue)
    if [ -z "$COOKIE_VALUE" ]; then
        _err=$(extract_json_str "$TMP_AUTH" errorDetails)
        [ -n "$_err" ] || _err=$(extract_json_str "$TMP_AUTH" errorMessage)
        log "WARNING: Gigya login rejected (${_err:-no cookieValue in response})"
        return 1
    fi

    # 2. Gigya accounts.getJWT (multipart POST) -> id_token and data.personId
    _jwt_code=$(curl -sS -o "$TMP_AUTH" -w '%{http_code}' \
        --connect-timeout "$CONNECT_TIMEOUT" --max-time "$MAX_TIME" \
        -X POST \
        -F "ApiKey=$GIGYA_API_KEY" \
        -F "login_token=$COOKIE_VALUE" \
        -F "fields=data.personId,data.gigyaDataCenter" \
        -F "expiration=86400" \
        "$GIGYA_URL/accounts.getJWT" 2>/dev/null) || _jwt_code=000

    if [ "$_jwt_code" != "200" ] || [ ! -s "$TMP_AUTH" ]; then
        log "WARNING: Gigya accounts.getJWT failed (HTTP $_jwt_code)"
        return 1
    fi

    ID_TOKEN=$(extract_json_str "$TMP_AUTH" id_token)
    PERSON_ID=$(extract_json_str "$TMP_AUTH" personId)
    if [ -z "$ID_TOKEN" ] || [ -z "$PERSON_ID" ]; then
        log "WARNING: Gigya getJWT response missing id_token or personId"
        return 1
    fi

    # 3. Kamereon persons/{personId}?country=DE -> MYRENAULT accountId
    _person_code=$(curl -sS -o "$TMP_AUTH" -w '%{http_code}' \
        --connect-timeout "$CONNECT_TIMEOUT" --max-time "$MAX_TIME" \
        -H "apikey: $KAMEREON_API_KEY" \
        -H "x-gigya-id_token: $ID_TOKEN" \
        "$KAMEREON_URL/commerce/v1/persons/$PERSON_ID?country=$RENAULT_COUNTRY" \
        2>/dev/null) || _person_code=000

    if [ "$_person_code" != "200" ] || [ ! -s "$TMP_AUTH" ]; then
        log "WARNING: Kamereon person lookup failed (HTTP $_person_code)"
        return 1
    fi

    ACCOUNT_ID=$(extract_myrenault_account_id "$TMP_AUTH")
    if [ -z "$ACCOUNT_ID" ]; then
        log "WARNING: No MYRENAULT accountId found in Kamereon response"
        return 1
    fi

    save_cached_session || true
    return 0
}

# ---------------------------------------------------------------------------
# Step 4: Telemetry Fetch (/battery-status)
# ---------------------------------------------------------------------------
fetch_battery_status() {
    curl -sS -o "$RAW_FILE" -w '%{http_code}' \
        --connect-timeout "$CONNECT_TIMEOUT" --max-time "$MAX_TIME" \
        -H "apikey: $KAMEREON_API_KEY" \
        -H "x-gigya-id_token: $ID_TOKEN" \
        "$KAMEREON_URL/commerce/v1/accounts/$ACCOUNT_ID/kamereon/kca/car-adapter/v2/cars/$RENAULT_VIN/battery-status" \
        2>/dev/null || printf '000'
}

if [ -n "${RENAULT_MOCK_BATTERY_JSON:-}" ] && [ -r "${RENAULT_MOCK_BATTERY_JSON:-}" ]; then
    cp -f "$RENAULT_MOCK_BATTERY_JSON" "$RAW_FILE"
    HTTP_CODE=200
else
    USED_CACHE=0
    if load_cached_session; then
        USED_CACHE=1
    else
        if ! authenticate_renault; then
            [ "$TEST_MODE" = "1" ] && exit 1
            exit 0
        fi
    fi

    HTTP_CODE=$(fetch_battery_status)

    if [ "$USED_CACHE" = "1" ] && { [ "$HTTP_CODE" = "401" ] || [ "$HTTP_CODE" = "403" ]; }; then
        log "Renault session expired or rejected (HTTP $HTTP_CODE); refreshing token"
        rm -f "$SESSION_FILE" 2>/dev/null || true
        if ! authenticate_renault; then
            [ "$TEST_MODE" = "1" ] && exit 1
            exit 0
        fi
        HTTP_CODE=$(fetch_battery_status)
    fi
fi

if [ "$HTTP_CODE" != "200" ] || [ ! -s "$RAW_FILE" ]; then
    log "WARNING: Kamereon battery-status failed (HTTP $HTTP_CODE); keeping existing card"
    [ "$TEST_MODE" = "1" ] && exit 1
    exit 0
fi

# ---------------------------------------------------------------------------
# Parse Telemetry & Build Stat Card
# ---------------------------------------------------------------------------
BATTERY_LEVEL=$(extract_json_num "$RAW_FILE" batteryLevel)
BATTERY_AUTONOMY=$(extract_json_num "$RAW_FILE" batteryAutonomy)
PLUG_STATUS=$(extract_json_num "$RAW_FILE" plugStatus)
CHARGING_STATUS=$(extract_json_num "$RAW_FILE" chargingStatus)

if ! is_uint "$BATTERY_LEVEL" || [ "$BATTERY_LEVEL" -gt 100 ]; then
    log "WARNING: battery-status response contains no valid batteryLevel"
    [ "$TEST_MODE" = "1" ] && exit 1
    exit 0
fi
BATTERY_LEVEL=$(printf '%s' "$BATTERY_LEVEL" | sed 's/^0*//')
[ -n "$BATTERY_LEVEL" ] || BATTERY_LEVEL=0

if is_number "$BATTERY_AUTONOMY"; then
    BATTERY_AUTONOMY=$(awk -v a="$BATTERY_AUTONOMY" 'BEGIN { printf "%d", a + 0 }')
else
    BATTERY_AUTONOMY=0
fi

is_number "$PLUG_STATUS" || PLUG_STATUS=0
is_number "$CHARGING_STATUS" || CHARGING_STATUS=0.0

CALC=$(awk -v p="$PLUG_STATUS" -v c="$CHARGING_STATUS" 'BEGIN {
    is_charging = (c + 0 == 1.0);
    is_plugged = (p + 0 == 1);
    if (is_charging) {
        badge = "LÄDT";
        color = "green";
    } else if (is_plugged) {
        badge = "VERBUNDEN";
        color = "blue";
    } else {
        badge = "BEREIT";
        color = "neutral";
    }
    plug_text = is_plugged ? "Gesteckt" : "Getrennt";
    printf "%s|%s|%s\n", badge, color, plug_text;
}')

IFS='|' read -r BADGE COLOR PLUG_TEXT <<EOF
$CALC
EOF

case "$COLOR" in
    green|blue|red|neutral) ;;
    *) COLOR=neutral ;;
esac
[ -n "$BADGE" ] || BADGE="BEREIT"
[ -n "$PLUG_TEXT" ] || PLUG_TEXT="Getrennt"

NOW_ISO=$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null) || NOW_ISO=

cat > "$OUT_FILE.tmp" <<EOF
{
  "id": "renault_zoe",
  "layout": "stat",
  "title": "Renault Zoe",
  "status_badge": "$BADGE",
  "status_color": "$COLOR",
  "timestamp": "$NOW_ISO",
  "primary": {
    "value": $BATTERY_LEVEL,
    "unit": "%",
    "label": "BATTERIE SOC"
  },
  "secondary_metrics": [
    { "label": "REICHWEITE", "value": "${BATTERY_AUTONOMY} km" },
    { "label": "STECKER", "value": "$PLUG_TEXT" }
  ]
}
EOF

if mv -f "$OUT_FILE.tmp" "$OUT_FILE" 2>/dev/null; then
    chmod 644 "$OUT_FILE" 2>/dev/null || true
    log "Renault Zoe card updated: SoC ${BATTERY_LEVEL}%, range ${BATTERY_AUTONOMY} km ($BADGE)"
else
    rm -f "$OUT_FILE.tmp" 2>/dev/null || true
    log "WARNING: could not publish $OUT_FILE"
    [ "$TEST_MODE" = "1" ] && exit 1
fi

exit 0
