#!/bin/sh
# =============================================================================
#  homematic-sma-battery-monitor - settings API (CGI)
# =============================================================================
#  Runs behind lighttpd on the CCU3 / RaspberryMatic. BusyBox ash only:
#  no jq, no python, no external JSON library.
#
#  Endpoints (installed as .cgi files because lighttpd maps by suffix):
#
#      GET  /addons/sma_battery/api/config.cgi
#      POST /addons/sma_battery/api/config.cgi
#      POST /addons/sma_battery/api/test_signal.cgi
#      GET  /addons/sma_battery/api/test_signal.cgi   (async result)
#
#  The handler can also be routed as /api/config and /api/test_signal through
#  PATH_INFO, and the action can be forced with SMA_CGI_ACTION for tests.
#
#  Design rules:
#    - the .env file is never rewritten in place. A private copy is edited and
#      moved over the original with rename(2), so a cron run either reads the
#      complete old file or the complete new one.
#    - secrets (SMA_PASS, SIGNAL_APIKEY) are never returned to the browser.
#    - every value is validated before it reaches .env.
#    - only same-origin POSTs are accepted.
# =============================================================================

PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 077

SCRIPT_DIR=$(cd "$(dirname "$0")" 2>/dev/null && pwd)
[ -n "$SCRIPT_DIR" ] || SCRIPT_DIR=.

# ---------------------------------------------------------------------------
# Locate the add-on directory and the .env file
# ---------------------------------------------------------------------------
resolve_addon_dir() {
    _dir=${SMA_MONITOR_ADDON_DIR:-}

    if [ -z "$_dir" ] && [ -r "$SCRIPT_DIR/addon_dir" ]; then
        _dir=$(sed -n '1p' "$SCRIPT_DIR/addon_dir" 2>/dev/null)
    fi

    if [ -z "$_dir" ]; then
        _candidate=$(cd "$SCRIPT_DIR/../.." 2>/dev/null && pwd)
        if [ -n "$_candidate" ] && [ -r "$_candidate/.env" ]; then
            _dir=$_candidate
        fi
    fi

    case "$_dir" in
        /?*) ;;
        *) _dir=/usr/local/addons/sma_battery ;;
    esac
    case "$_dir" in
        *..*) _dir=/usr/local/addons/sma_battery ;;
    esac
    printf '%s\n' "$_dir"
}

ADDON_DIR=$(resolve_addon_dir)
ENV_FILE=${SMA_MONITOR_ENV:-}
if [ -z "$ENV_FILE" ]; then
    if [ -r "$ADDON_DIR/.env" ]; then
        ENV_FILE=$ADDON_DIR/.env
    elif [ -r "$ADDON_DIR/config.env" ]; then
        ENV_FILE=$ADDON_DIR/config.env
    else
        ENV_FILE=$ADDON_DIR/.env
    fi
fi

STATE_DIR=$ADDON_DIR/state
NOTIFY_SCRIPT=$ADDON_DIR/lib/notify_signal.sh
TEST_RESULT=$STATE_DIR/notify_test.result
TEST_LOG=$STATE_DIR/notify_test.log
TEST_LOCK=$STATE_DIR/.test_signal.lock
CONFIG_LOCK=$STATE_DIR/.config.lock
TEST_WRAPPER=

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
json_escape() {
    printf '%s' "$1" | tr -d '\r\n' \
        | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/\t/\\t/g'
}

json_str() {
    printf '"%s"' "$(json_escape "$1")"
}

json_bool_str() {
    case "$1" in
        true) printf 'true' ;;
        *) printf 'false' ;;
    esac
}

is_uint() {
    case "$1" in
        ''|*[!0-9]*) return 1 ;;
    esac
    return 0
}

is_safe_host() {
    case "$1" in
        ''|*[!A-Za-z0-9._-]*) return 1 ;;
    esac
    case "$1" in
        *..*|.*|*.) return 1 ;;
    esac
    return 0
}

is_phone_or_uuid() {
    case "$1" in
        +*)
            # E.164: a leading +, a non-zero country digit, 7..15 digits total.
            _digits=${1#+}
            case "$_digits" in
                ''|*[!0-9]*) return 1 ;;
            esac
            [ "${#_digits}" -ge 7 ] || return 1
            [ "${#_digits}" -le 15 ] || return 1
            case "$_digits" in
                0*) return 1 ;;
            esac
            return 0
            ;;
        *)
            # Signal UUID (any common version) as accepted by CallMeBot.
            printf '%s' "$1" | grep -Eq \
                '^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$'
            return $?
            ;;
    esac
}

is_apikey() {
    printf '%s' "$1" | grep -Eq '^[A-Za-z0-9._:-]{1,128}$'
}

is_password() {
    # The value is written single-quoted, but the JSON transport cannot carry
    # unescaped quotes/backslashes. Reject control characters as well.
    case "$1" in
        *'"'*|*\\*) return 1 ;;
    esac
    if printf '%s' "$1" | grep -q '[[:cntrl:]]'; then
        return 1
    fi
    [ "${#1}" -ge 1 ] && [ "${#1}" -le 128 ]
}

shquote() {
    printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}

# shellcheck disable=SC2317,SC2329  # invoked indirectly through trap
cleanup() {
    [ -n "${TMP_FILE:-}" ] && rm -f "$TMP_FILE" 2>/dev/null
    [ -n "$TEST_WRAPPER" ] && rm -f "$TEST_WRAPPER" 2>/dev/null
    if [ "${CONFIG_LOCK_OWNED:-0}" = "1" ]; then
        rm -rf "$CONFIG_LOCK" 2>/dev/null
    fi
    if [ "${TEST_LOCK_OWNED:-0}" = "1" ]; then
        rm -rf "$TEST_LOCK" 2>/dev/null
    fi
    exit 0
}
trap cleanup EXIT HUP INT TERM

# ---------------------------------------------------------------------------
# HTTP response
# ---------------------------------------------------------------------------
emit_json() {
    _status=$1
    _payload=$2
    printf 'Status: %s\r\n' "$_status"
    printf 'Content-Type: application/json; charset=utf-8\r\n'
    printf 'Cache-Control: no-store, max-age=0\r\n'
    printf 'Pragma: no-cache\r\n'
    printf 'X-Content-Type-Options: nosniff\r\n'
    printf 'Referrer-Policy: same-origin\r\n'
    printf '\r\n'
    printf '%s\n' "$_payload"
    exit 0
}

fail() {
    _status=$1
    _message=$2
    _field=${3:-}
    _out="{\"ok\":false,\"error\":$(json_str "$_message")"
    [ -n "$_field" ] && _out="$_out,\"field\":$(json_str "$_field")"
    emit_json "$_status" "$_out}"
}

# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------
REQUEST_METHOD=${REQUEST_METHOD:-GET}
CONTENT_LENGTH=${CONTENT_LENGTH:-0}
CONTENT_TYPE=${CONTENT_TYPE:-}
BODY=

read_body() {
    is_uint "$CONTENT_LENGTH" || fail "400 Bad Request" "Invalid Content-Length."
    [ "$CONTENT_LENGTH" -le 16384 ] \
        || fail "413 Payload Too Large" "Request body is too large."
    if [ "$CONTENT_LENGTH" -gt 0 ]; then
        BODY=$(dd bs=1 count="$CONTENT_LENGTH" 2>/dev/null)
        [ -n "$BODY" ] || fail "400 Bad Request" "Incomplete request body."
    fi
}

check_origin() {
    [ -n "${HTTP_ORIGIN:-}" ] || return 0
    case "$HTTP_ORIGIN" in
        "http://${HTTP_HOST:-}"|"https://${HTTP_HOST:-}") return 0 ;;
    esac
    fail "403 Forbidden" "Cross-origin settings changes are not allowed."
}

# POST bodies are flat JSON. The UI sends exactly these keys; unknown keys are
# ignored. Escaped quotes/backslashes are rejected by the validators.
body_has() {
    printf '%s' "$BODY" | grep -q "\"$1\"[[:space:]]*:"
}

body_str() {
    printf '%s' "$BODY" \
        | sed -n 's/.*"'"$1"'"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
        | head -n 1
}

body_num() {
    printf '%s' "$BODY" \
        | sed -n 's/.*"'"$1"'"[[:space:]]*:[[:space:]]*\(-\{0,1\}[0-9][0-9]*\).*/\1/p' \
        | head -n 1
}

body_bool() {
    printf '%s' "$BODY" \
        | sed -n 's/.*"'"$1"'"[[:space:]]*:[[:space:]]*\(true\|false\).*/\1/p' \
        | head -n 1
}

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
load_env() {
    [ -r "$ENV_FILE" ] || return 1
    # shellcheck source=/dev/null
    . "$ENV_FILE" || return 1

    case "${THRESHOLD_TRIGGER:-}" in ''|*[!0-9]*) THRESHOLD_TRIGGER=95 ;; esac
    case "${THRESHOLD_RESET:-}" in ''|*[!0-9]*) THRESHOLD_RESET=90 ;; esac
    SIGNAL_ENABLED=${SIGNAL_ENABLED:-false}
    SIGNAL_PHONE=${SIGNAL_PHONE:-}
    SIGNAL_APIKEY=${SIGNAL_APIKEY:-}
    SMA_IP=${SMA_IP:-}
    SMA_PORT=${SMA_PORT:-443}
    SMA_PASS=${SMA_PASS:-}
    return 0
}

config_payload() {
    _trigger=$THRESHOLD_TRIGGER
    _reset=$THRESHOLD_RESET
    _enabled=$SIGNAL_ENABLED

    _apikey_set=false
    [ -n "$SIGNAL_APIKEY" ] && _apikey_set=true
    _password_set=false
    [ -n "$SMA_PASS" ] && _password_set=true

    _writable=false
    [ -w "$ENV_FILE" ] && _writable=true

    case "$SMA_PORT" in
        ''|*[!0-9]*) _port=443 ;;
        *) _port=$SMA_PORT ;;
    esac

    printf '{"ok":true'
    printf ',"trigger_soc":%s' "$_trigger"
    printf ',"reset_soc":%s' "$_reset"
    printf ',"signal_enabled":%s' "$(json_bool_str "$_enabled")"
    printf ',"signal_phone":%s' "$(json_str "$SIGNAL_PHONE")"
    printf ',"signal_apikey_set":%s' "$_apikey_set"
    printf ',"sma_ip":%s' "$(json_str "$SMA_IP")"
    printf ',"sma_port":%s' "$_port"
    printf ',"sma_password_set":%s' "$_password_set"
    printf ',"writable":%s' "$_writable"
    printf '}\n'
}

# Edit a key in an existing (working copy) file. The caller passes a private
# copy; this function itself is never applied to the live .env.
env_set() {
    _file=$1
    _key=$2
    _value=$3
    _escaped=$(printf '%s' "$_value" | sed "s/'/'\\\\''/g")
    _next="$_file.next.$$"
    _found=0

    while IFS= read -r _line || [ -n "$_line" ]; do
        case "$_line" in
            "$_key="*)
                printf '%s=%s\n' "$_key" "'$_escaped'"
                _found=1
                ;;
            *)
                printf '%s\n' "$_line"
                ;;
        esac
    done < "$_file" > "$_next" || return 1

    if [ "$_found" = "0" ]; then
        printf '%s=%s\n' "$_key" "'$_escaped'" >> "$_next" || return 1
    fi
    mv -f "$_next" "$_file"
}

acquire_config_lock() {
    mkdir -p "$STATE_DIR" 2>/dev/null || return 1
    if mkdir "$CONFIG_LOCK" 2>/dev/null; then
        printf '%s\n' "$$" > "$CONFIG_LOCK/pid" 2>/dev/null || true
        CONFIG_LOCK_OWNED=1
        return 0
    fi

    _oldpid=$(cat "$CONFIG_LOCK/pid" 2>/dev/null)
    if [ -n "$_oldpid" ] && is_uint "$_oldpid" && kill -0 "$_oldpid" 2>/dev/null; then
        return 1
    fi
    rm -rf "$CONFIG_LOCK" 2>/dev/null || true
    if mkdir "$CONFIG_LOCK" 2>/dev/null; then
        printf '%s\n' "$$" > "$CONFIG_LOCK/pid" 2>/dev/null || true
        CONFIG_LOCK_OWNED=1
        return 0
    fi
    return 1
}

release_config_lock() {
    if [ "${CONFIG_LOCK_OWNED:-0}" = "1" ]; then
        rm -rf "$CONFIG_LOCK" 2>/dev/null || true
        CONFIG_LOCK_OWNED=0
    fi
}

save_config() {
    load_env || fail "500 Internal Server Error" \
        "The configuration file is missing or unreadable."

    [ -w "$ENV_FILE" ] || fail "500 Internal Server Error" \
        "The configuration file is not writable by the web server."

    _trigger=$THRESHOLD_TRIGGER
    _reset=$THRESHOLD_RESET
    _enabled=$SIGNAL_ENABLED
    _phone=$SIGNAL_PHONE
    _apikey=$SIGNAL_APIKEY
    _ip=$SMA_IP
    _port=$SMA_PORT
    _password=$SMA_PASS

    _want_trigger=0
    _want_reset=0
    _want_enabled=0
    _want_phone=0
    _want_apikey=0
    _want_ip=0
    _want_port=0
    _want_password=0

    if body_has trigger_soc; then
        _trigger=$(body_num trigger_soc)
        if ! is_uint "$_trigger" || [ "$_trigger" -gt 100 ]; then
            fail "400 Bad Request" \
                "Trigger level must be a whole number between 0 and 100." \
                "trigger_soc"
        fi
        _want_trigger=1
    fi

    if body_has reset_soc; then
        _reset=$(body_num reset_soc)
        if ! is_uint "$_reset" || [ "$_reset" -gt 100 ]; then
            fail "400 Bad Request" \
                "Reset level must be a whole number between 0 and 100." \
                "reset_soc"
        fi
        _want_reset=1
    fi

    if [ "$_trigger" -le "$_reset" ]; then
        fail "400 Bad Request" \
            "Trigger level must be greater than reset level." \
            "trigger_soc"
    fi

    if body_has signal_enabled; then
        _enabled=$(body_bool signal_enabled)
        if [ -z "$_enabled" ]; then
            _raw=$(body_str signal_enabled)
            case "$_raw" in
                true|false) _enabled=$_raw ;;
                *) fail "400 Bad Request" \
                    "Signal alerts must be either enabled or disabled." \
                    "signal_enabled" ;;
            esac
        fi
        _want_enabled=1
    fi

    if body_has signal_phone; then
        _phone=$(body_str signal_phone)
        if [ -n "$_phone" ] && ! is_phone_or_uuid "$_phone"; then
            fail "400 Bad Request" \
                "Enter an E.164 phone number (for example +491701234567) or a Signal UUID." \
                "signal_phone"
        fi
        _want_phone=1
    fi

    if body_has signal_apikey; then
        _newkey=$(body_str signal_apikey)
        if [ -n "$_newkey" ]; then
            is_apikey "$_newkey" || fail "400 Bad Request" \
                "The CallMeBot API key may only contain letters, digits, dot, dash, underscore and colon." \
                "signal_apikey"
            _apikey=$_newkey
            _want_apikey=1
        fi
        # An empty field means "keep the stored key"; it is never erased by a
        # browser that simply shows a masked placeholder.
    fi

    if [ "$_enabled" = "true" ]; then
        [ -n "$_phone" ] || fail "400 Bad Request" \
            "A phone number or Signal UUID is required when alerts are enabled." \
            "signal_phone"
        [ -n "$_apikey" ] || fail "400 Bad Request" \
            "A CallMeBot API key is required when alerts are enabled." \
            "signal_apikey"
    fi

    if body_has sma_ip; then
        _ip=$(body_str sma_ip)
        is_safe_host "$_ip" || fail "400 Bad Request" \
            "Enter a valid inverter IPv4 address or hostname." "sma_ip"
        _want_ip=1
    fi

    if body_has sma_port; then
        _port=$(body_num sma_port)
        if ! is_uint "$_port" || [ "$_port" -lt 1 ] || [ "$_port" -gt 65535 ]; then
            fail "400 Bad Request" \
                "Inverter port must be between 1 and 65535." "sma_port"
        fi
        _want_port=1
    fi

    if body_has sma_password; then
        _newpw=$(body_str sma_password)
        if [ -n "$_newpw" ]; then
            is_password "$_newpw" || fail "400 Bad Request" \
                "The inverter password contains unsupported characters." \
                "sma_password"
            _password=$_newpw
            _want_password=1
        fi
    fi

    acquire_config_lock || fail "409 Conflict" \
        "Another configuration change is in progress. Try again."

    TMP_FILE="$ENV_FILE.tmp.$$"
    cp -p "$ENV_FILE" "$TMP_FILE" 2>/dev/null \
        || fail "500 Internal Server Error" "Cannot create a temporary configuration file."
    chmod 600 "$TMP_FILE" 2>/dev/null || true

    if [ "$_want_trigger" = "1" ]; then
        env_set "$TMP_FILE" THRESHOLD_TRIGGER "$_trigger" || \
            fail "500 Internal Server Error" "Cannot write the trigger level."
    fi
    if [ "$_want_reset" = "1" ]; then
        env_set "$TMP_FILE" THRESHOLD_RESET "$_reset" || \
            fail "500 Internal Server Error" "Cannot write the reset level."
    fi
    if [ "$_want_enabled" = "1" ]; then
        env_set "$TMP_FILE" SIGNAL_ENABLED "$_enabled" || \
            fail "500 Internal Server Error" "Cannot write the Signal switch."
    fi
    if [ "$_want_phone" = "1" ]; then
        env_set "$TMP_FILE" SIGNAL_PHONE "$_phone" || \
            fail "500 Internal Server Error" "Cannot write the phone number."
    fi
    if [ "$_want_apikey" = "1" ]; then
        env_set "$TMP_FILE" SIGNAL_APIKEY "$_apikey" || \
            fail "500 Internal Server Error" "Cannot write the API key."
    fi
    if [ "$_want_ip" = "1" ]; then
        env_set "$TMP_FILE" SMA_IP "$_ip" || \
            fail "500 Internal Server Error" "Cannot write the inverter address."
    fi
    if [ "$_want_port" = "1" ]; then
        env_set "$TMP_FILE" SMA_PORT "$_port" || \
            fail "500 Internal Server Error" "Cannot write the inverter port."
    fi
    if [ "$_want_password" = "1" ]; then
        env_set "$TMP_FILE" SMA_PASS "$_password" || \
            fail "500 Internal Server Error" "Cannot write the inverter password."
    fi

    chmod 600 "$TMP_FILE" 2>/dev/null || true
    mv -f "$TMP_FILE" "$ENV_FILE" 2>/dev/null \
        || fail "500 Internal Server Error" "Cannot replace the configuration file."
    TMP_FILE=

    release_config_lock
    load_env || fail "500 Internal Server Error" \
        "The configuration was saved but cannot be read back."
    emit_json "200 OK" "$(config_payload)"
}

# ---------------------------------------------------------------------------
# Test notification: start detached, report the result asynchronously
# ---------------------------------------------------------------------------
test_lock_is_fresh() {
    [ -d "$TEST_LOCK" ] || return 1
    _started=$(sed -n '1p' "$TEST_LOCK/started" 2>/dev/null)
    is_uint "$_started" || return 1
    _now=$(date +%s 2>/dev/null)
    is_uint "$_now" || return 1
    [ "$_now" -ge "$_started" ] || return 0
    [ $((_now - _started)) -lt 120 ]
}

start_test() {
    load_env || fail "500 Internal Server Error" \
        "The configuration file is missing or unreadable."

    [ -x "$NOTIFY_SCRIPT" ] || fail "500 Internal Server Error" \
        "The notification script is missing or not executable."

    if [ -z "$SIGNAL_PHONE" ] || [ -z "$SIGNAL_APIKEY" ]; then
        fail "400 Bad Request" \
            "Save a phone number and CallMeBot API key before testing."
    fi

    if test_lock_is_fresh; then
        fail "409 Conflict" "A test notification is already running."
    fi
    rm -rf "$TEST_LOCK" 2>/dev/null || true
    mkdir -p "$STATE_DIR" 2>/dev/null \
        || fail "500 Internal Server Error" "Cannot create the state directory."
    mkdir "$TEST_LOCK" 2>/dev/null \
        || fail "500 Internal Server Error" "Cannot create the test lock."
    TEST_LOCK_OWNED=1
    printf '%s\n' "$(date +%s 2>/dev/null)" > "$TEST_LOCK/started" 2>/dev/null || true

    rm -f "$TEST_RESULT" "$TEST_RESULT.tmp" 2>/dev/null || true

    TEST_WRAPPER="$STATE_DIR/.test_signal.$$.sh"
    {
        printf '#!/bin/sh\n'
        printf 'SMA_MONITOR_ENV=%s\n' "$(shquote "$ENV_FILE")"
        printf 'export SMA_MONITOR_ENV\n'
        printf 'umask 077\n'
        printf '%s --test >>%s 2>&1\n' \
            "$(shquote "$NOTIFY_SCRIPT")" "$(shquote "$TEST_LOG")"
        printf '_rc=$?\n'
        # shellcheck disable=SC2016  # this text is written into the wrapper
        printf 'printf "%%s\\n%%s\\n" "$_rc" "$(date +%%s)" >%s\n' \
            "$(shquote "$TEST_RESULT.tmp")"
        printf 'mv -f %s %s\n' \
            "$(shquote "$TEST_RESULT.tmp")" "$(shquote "$TEST_RESULT")"
        printf 'rm -rf %s\n' "$(shquote "$TEST_LOCK")"
        # shellcheck disable=SC2016  # "$0" must stay literal in the wrapper
        printf 'rm -f "$0"\n'
    } > "$TEST_WRAPPER" 2>/dev/null \
        || fail "500 Internal Server Error" "Cannot create the test runner."

    chmod 700 "$TEST_WRAPPER" 2>/dev/null || true

    if command -v setsid >/dev/null 2>&1 && command -v nohup >/dev/null 2>&1; then
        setsid nohup /bin/sh "$TEST_WRAPPER" </dev/null >/dev/null 2>&1 &
    elif command -v nohup >/dev/null 2>&1; then
        nohup /bin/sh "$TEST_WRAPPER" </dev/null >/dev/null 2>&1 &
    else
        /bin/sh "$TEST_WRAPPER" </dev/null >/dev/null 2>&1 &
    fi
    TEST_WRAPPER=
    # The wrapper removes the lock when the notification finishes.
    TEST_LOCK_OWNED=0

    emit_json "202 Accepted" \
        '{"ok":true,"status":"queued","message":"Test notification queued."}'
}

read_test_result() {
    [ -r "$TEST_RESULT" ] || return 1
    TEST_RC=$(sed -n '1p' "$TEST_RESULT" 2>/dev/null)
    _ts=$(sed -n '2p' "$TEST_RESULT" 2>/dev/null)
    is_uint "$TEST_RC" || return 1
    is_uint "$_ts" || _ts=0
    _now=$(date +%s 2>/dev/null)
    is_uint "$_now" || return 0
    if [ "$_now" -ge "$_ts" ] && [ $((_now - _ts)) -gt 300 ]; then
        return 1
    fi
    return 0
}

report_test() {
    if read_test_result; then
        if [ "$TEST_RC" = "0" ]; then
            emit_json "200 OK" \
                '{"ok":true,"status":"sent","message":"Test notification sent."}'
        fi
        emit_json "200 OK" \
            '{"ok":false,"status":"failed","error":"Test notification failed. Check the phone number, API key and the CCU internet connection."}'
    fi

    if test_lock_is_fresh; then
        emit_json "200 OK" \
            '{"ok":true,"status":"pending","message":"Test notification is being sent."}'
    fi

    emit_json "200 OK" '{"ok":true,"status":"idle"}'
}

# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------
ACTION=${SMA_CGI_ACTION:-}
if [ -z "$ACTION" ]; then
    case "${PATH_INFO:-}" in
        */test_signal*) ACTION=test_signal ;;
        */config*) ACTION=config ;;
    esac
fi
if [ -z "$ACTION" ]; then
    case "${SCRIPT_NAME##*/}" in
        test_signal.cgi|test_signal) ACTION=test_signal ;;
        *) ACTION=config ;;
    esac
fi

case "$ACTION" in
    config)
        case "$REQUEST_METHOD" in
            GET|HEAD)
                load_env || fail "500 Internal Server Error" \
                    "The configuration file is missing or unreadable."
                emit_json "200 OK" "$(config_payload)"
                ;;
            POST)
                check_origin
                read_body
                save_config
                ;;
            *)
                emit_json "405 Method Not Allowed" \
                    '{"ok":false,"error":"Use GET or POST for this endpoint."}'
                ;;
        esac
        ;;
    test_signal)
        case "$REQUEST_METHOD" in
            GET|HEAD)
                report_test
                ;;
            POST)
                check_origin
                read_body
                start_test
                ;;
            *)
                emit_json "405 Method Not Allowed" \
                    '{"ok":false,"error":"Use GET or POST for this endpoint."}'
                ;;
        esac
        ;;
    *)
        emit_json "404 Not Found" \
            '{"ok":false,"error":"Unknown API action."}'
        ;;
esac
