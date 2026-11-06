#!/bin/sh
# =============================================================================
#  Sentinella - settings API (CGI)
# =============================================================================
#  Runs behind lighttpd on the CCU3 / RaspberryMatic. BusyBox ash only:
#  no jq, no python, no external JSON library.
#
#  Endpoints (installed as .cgi files because lighttpd maps by suffix):
#
#      GET  /addons/sentinella/api/config.cgi
#      POST /addons/sentinella/api/config.cgi
#      POST /addons/sentinella/api/test_signal.cgi
#      GET  /addons/sentinella/api/test_signal.cgi   (async result)
#
#  Also reachable under the legacy /addons/sma_battery/api/ path.
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
UI_LANGUAGE=en

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

trim_ws() {
    printf '%s' "${1:-}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'
}

is_signal_uuid() {
    printf '%s' "$1" | grep -Eq \
        '^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$'
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

# Localized, user-facing API messages. UI_LANGUAGE is loaded from .env before
# any request is validated; unknown values fall back to English.
msg() {
    case "$UI_LANGUAGE:${1:-}" in
        de:invalid_content_length) printf 'Ungültige Content-Length.' ;;
        en:invalid_content_length) printf 'Invalid Content-Length.' ;;
        de:body_too_large) printf 'Der Anfrageinhalt ist zu groß.' ;;
        en:body_too_large) printf 'Request body is too large.' ;;
        de:incomplete_body) printf 'Der Anfrageinhalt ist unvollständig.' ;;
        en:incomplete_body) printf 'Incomplete request body.' ;;
        de:cross_origin) printf 'Änderungen an den Einstellungen von einer anderen Origin sind nicht erlaubt.' ;;
        en:cross_origin) printf 'Cross-origin settings changes are not allowed.' ;;
        de:config_missing) printf 'Die Konfigurationsdatei fehlt oder ist nicht lesbar.' ;;
        en:config_missing) printf 'The configuration file is missing or unreadable.' ;;
        de:config_not_writable) printf 'Die Konfigurationsdatei ist für den Webserver nicht beschreibbar.' ;;
        en:config_not_writable) printf 'The configuration file is not writable by the web server.' ;;
        de:trigger_invalid) printf 'Die Triggerschwelle muss eine ganze Zahl zwischen 0 und 100 sein.' ;;
        en:trigger_invalid) printf 'Trigger level must be a whole number between 0 and 100.' ;;
        de:reset_invalid) printf 'Die Rücksetzschwelle muss eine ganze Zahl zwischen 0 und 100 sein.' ;;
        en:reset_invalid) printf 'Reset level must be a whole number between 0 and 100.' ;;
        de:trigger_order) printf 'Die Triggerschwelle muss größer als die Rücksetzschwelle sein.' ;;
        en:trigger_order) printf 'Trigger level must be greater than reset level.' ;;
        de:signal_bool) printf 'Signalbenachrichtigungen müssen aktiviert oder deaktiviert sein.' ;;
        en:signal_bool) printf 'Signal alerts must be either enabled or disabled.' ;;
        de:phone_invalid) printf 'Geben Sie eine gültige Signal Account UUID ein (zum Beispiel 98526357-0cec-4e7f-9d60-d854432414d3).' ;;
        en:phone_invalid) printf 'Enter a valid Signal Account UUID (for example 98526357-0cec-4e7f-9d60-d854432414d3).' ;;
        de:apikey_invalid) printf 'Der CallMeBot-API-Schlüssel darf nur Buchstaben, Ziffern, Punkt, Bindestrich, Unterstrich und Doppelpunkt enthalten.' ;;
        en:apikey_invalid) printf 'The CallMeBot API key may only contain letters, digits, dot, dash, underscore and colon.' ;;
        de:phone_required) printf 'Wenn Benachrichtigungen aktiviert sind, ist eine Signal Account UUID erforderlich.' ;;
        en:phone_required) printf 'A Signal Account UUID is required when alerts are enabled.' ;;
        de:apikey_required) printf 'Wenn Benachrichtigungen aktiviert sind, ist ein CallMeBot-API-Schlüssel erforderlich.' ;;
        en:apikey_required) printf 'A CallMeBot API key is required when alerts are enabled.' ;;
        de:ip_invalid) printf 'Geben Sie eine gültige IPv4-Adresse oder einen Hostnamen des Wechselrichters ein.' ;;
        en:ip_invalid) printf 'Enter a valid inverter IPv4 address or hostname.' ;;
        de:port_invalid) printf 'Der Wechselrichter-Port muss zwischen 1 und 65535 liegen.' ;;
        en:port_invalid) printf 'Inverter port must be between 1 and 65535.' ;;
        de:password_invalid) printf 'Das Wechselrichter-Passwort enthält nicht unterstützte Zeichen.' ;;
        en:password_invalid) printf 'The inverter password contains unsupported characters.' ;;
        de:language_invalid) printf 'Die Sprache muss "en" oder "de" sein.' ;;
        en:language_invalid) printf 'The language must be "en" or "de".' ;;
        de:config_busy) printf 'Eine andere Konfigurationsänderung läuft gerade. Bitte erneut versuchen.' ;;
        en:config_busy) printf 'Another configuration change is in progress. Try again.' ;;
        de:tmp_create) printf 'Temporäre Konfigurationsdatei kann nicht erstellt werden.' ;;
        en:tmp_create) printf 'Cannot create a temporary configuration file.' ;;
        de:write_trigger) printf 'Die Triggerschwelle kann nicht geschrieben werden.' ;;
        en:write_trigger) printf 'Cannot write the trigger level.' ;;
        de:write_language) printf 'Die Sprache kann nicht geschrieben werden.' ;;
        en:write_language) printf 'Cannot write the language.' ;;
        de:write_reset) printf 'Die Rücksetzschwelle kann nicht geschrieben werden.' ;;
        en:write_reset) printf 'Cannot write the reset level.' ;;
        de:write_signal) printf 'Der Signal-Schalter kann nicht geschrieben werden.' ;;
        en:write_signal) printf 'Cannot write the Signal switch.' ;;
        de:write_phone) printf 'Die Signal Account UUID kann nicht geschrieben werden.' ;;
        en:write_phone) printf 'Cannot write the Signal Account UUID.' ;;
        de:write_apikey) printf 'Der API-Schlüssel kann nicht geschrieben werden.' ;;
        en:write_apikey) printf 'Cannot write the API key.' ;;
        de:write_ip) printf 'Die Wechselrichter-Adresse kann nicht geschrieben werden.' ;;
        en:write_ip) printf 'Cannot write the inverter address.' ;;
        de:write_port) printf 'Der Wechselrichter-Port kann nicht geschrieben werden.' ;;
        en:write_port) printf 'Cannot write the inverter port.' ;;
        de:write_password) printf 'Das Wechselrichter-Passwort kann nicht geschrieben werden.' ;;
        en:write_password) printf 'Cannot write the inverter password.' ;;
        de:replace_config) printf 'Die Konfigurationsdatei kann nicht ersetzt werden.' ;;
        en:replace_config) printf 'Cannot replace the configuration file.' ;;
        de:readback) printf 'Die Konfiguration wurde gespeichert, kann aber nicht erneut gelesen werden.' ;;
        en:readback) printf 'The configuration was saved but cannot be read back.' ;;
        de:notify_missing) printf 'Das Benachrichtigungsskript fehlt oder ist nicht ausführbar.' ;;
        en:notify_missing) printf 'The notification script is missing or not executable.' ;;
        de:test_credentials) printf 'Speichern Sie zuerst eine Signal Account UUID und einen CallMeBot-API-Schlüssel.' ;;
        en:test_credentials) printf 'Save a Signal Account UUID and CallMeBot API key before testing.' ;;
        de:test_running) printf 'Es läuft bereits eine Testbenachrichtigung.' ;;
        en:test_running) printf 'A test notification is already running.' ;;
        de:state_dir) printf 'Das Statusverzeichnis kann nicht erstellt werden.' ;;
        en:state_dir) printf 'Cannot create the state directory.' ;;
        de:test_lock) printf 'Die Testsperre kann nicht erstellt werden.' ;;
        en:test_lock) printf 'Cannot create the test lock.' ;;
        de:test_runner) printf 'Der Testrunner kann nicht erstellt werden.' ;;
        en:test_runner) printf 'Cannot create the test runner.' ;;
        de:queued) printf 'Testbenachrichtigung eingereiht.' ;;
        en:queued) printf 'Test notification queued.' ;;
        de:test_sent) printf 'Sentinella: Verbindung bestätigt. Bereit für Benachrichtigungen.' ;;
        en:test_sent) printf 'Sentinella: Connection verified. Ready for alerts.' ;;
        de:test_failed) printf 'Testbenachrichtigung fehlgeschlagen. Prüfen Sie Signal Account UUID, API-Schlüssel und die Internetverbindung der CCU.' ;;
        en:test_failed) printf 'Test notification failed. Check the Signal Account UUID, API key and the CCU internet connection.' ;;
        de:test_pending) printf 'Testbenachrichtigung wird gesendet.' ;;
        en:test_pending) printf 'Test notification is being sent.' ;;
        de:method) printf 'Verwenden Sie GET oder POST für diesen Endpunkt.' ;;
        en:method) printf 'Use GET or POST for this endpoint.' ;;
        de:unknown_action) printf 'Unbekannte API-Aktion.' ;;
        en:unknown_action) printf 'Unknown API action.' ;;
        *) printf 'Request failed.' ;;
    esac
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
    is_uint "$CONTENT_LENGTH" || fail "400 Bad Request" "$(msg invalid_content_length)"
    [ "$CONTENT_LENGTH" -le 16384 ] \
        || fail "413 Payload Too Large" "$(msg body_too_large)"
    if [ "$CONTENT_LENGTH" -gt 0 ]; then
        BODY=$(dd bs=1 count="$CONTENT_LENGTH" 2>/dev/null)
        [ -n "$BODY" ] || fail "400 Bad Request" "$(msg incomplete_body)"
    fi
}

check_origin() {
    [ -n "${HTTP_ORIGIN:-}" ] || return 0
    case "$HTTP_ORIGIN" in
        "http://${HTTP_HOST:-}"|"https://${HTTP_HOST:-}") return 0 ;;
    esac
    fail "403 Forbidden" "$(msg cross_origin)"
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
    SIGNAL_PHONE=$(trim_ws "${SIGNAL_PHONE:-}")
    SIGNAL_APIKEY=${SIGNAL_APIKEY:-}
    SMA_IP=${SMA_IP:-}
    SMA_PORT=${SMA_PORT:-443}
    SMA_PASS=${SMA_PASS:-}
    case "${UI_LANGUAGE:-}" in
        de|DE|de_DE|de-DE) UI_LANGUAGE=de ;;
        *) UI_LANGUAGE=en ;;
    esac
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
    printf ',"ui_language":%s' "$(json_str "$UI_LANGUAGE")"
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
        "$(msg config_missing)"

    [ -w "$ENV_FILE" ] || fail "500 Internal Server Error" \
        "$(msg config_not_writable)"

    _trigger=$THRESHOLD_TRIGGER
    _reset=$THRESHOLD_RESET
    _enabled=$SIGNAL_ENABLED
    _phone=$SIGNAL_PHONE
    _apikey=$SIGNAL_APIKEY
    _ip=$SMA_IP
    _port=$SMA_PORT
    _password=$SMA_PASS
    _language=$UI_LANGUAGE

    _want_trigger=0
    _want_reset=0
    _want_enabled=0
    _want_phone=0
    _want_apikey=0
    _want_ip=0
    _want_port=0
    _want_password=0
    _want_language=0

    if body_has ui_language; then
        _language=$(body_str ui_language)
        case "$_language" in
            en|de) ;;
            *) fail "400 Bad Request" "$(msg language_invalid)" "ui_language" ;;
        esac
        _want_language=1
    fi

    if body_has trigger_soc; then
        _trigger=$(body_num trigger_soc)
        if ! is_uint "$_trigger" || [ "$_trigger" -gt 100 ]; then
            fail "400 Bad Request" \
                "$(msg trigger_invalid)" \
                "trigger_soc"
        fi
        _want_trigger=1
    fi

    if body_has reset_soc; then
        _reset=$(body_num reset_soc)
        if ! is_uint "$_reset" || [ "$_reset" -gt 100 ]; then
            fail "400 Bad Request" \
                "$(msg reset_invalid)" \
                "reset_soc"
        fi
        _want_reset=1
    fi

    if [ "$_trigger" -le "$_reset" ]; then
        fail "400 Bad Request" \
            "$(msg trigger_order)" \
            "trigger_soc"
    fi

    if body_has signal_enabled; then
        _enabled=$(body_bool signal_enabled)
        if [ -z "$_enabled" ]; then
            _raw=$(body_str signal_enabled)
            case "$_raw" in
                true|false) _enabled=$_raw ;;
                *) fail "400 Bad Request" \
                    "$(msg signal_bool)" \
                    "signal_enabled" ;;
            esac
        fi
        _want_enabled=1
    fi

    if body_has signal_phone; then
        _phone=$(trim_ws "$(body_str signal_phone)")
        if [ -n "$_phone" ] && ! is_signal_uuid "$_phone"; then
            fail "400 Bad Request" \
                "$(msg phone_invalid)" \
                "signal_phone"
        fi
        _want_phone=1
    fi

    if body_has signal_apikey; then
        _newkey=$(body_str signal_apikey)
        if [ -n "$_newkey" ]; then
            is_apikey "$_newkey" || fail "400 Bad Request" \
                "$(msg apikey_invalid)" \
                "signal_apikey"
            _apikey=$_newkey
            _want_apikey=1
        fi
        # An empty field means "keep the stored key"; it is never erased by a
        # browser that simply shows a masked placeholder.
    fi

    if [ "$_enabled" = "true" ]; then
        [ -n "$_phone" ] || fail "400 Bad Request" \
            "$(msg phone_required)" \
            "signal_phone"
        [ -n "$_apikey" ] || fail "400 Bad Request" \
            "$(msg apikey_required)" \
            "signal_apikey"
    fi

    if body_has sma_ip; then
        _ip=$(body_str sma_ip)
        is_safe_host "$_ip" || fail "400 Bad Request" \
            "$(msg ip_invalid)" "sma_ip"
        _want_ip=1
    fi

    if body_has sma_port; then
        _port=$(body_num sma_port)
        if ! is_uint "$_port" || [ "$_port" -lt 1 ] || [ "$_port" -gt 65535 ]; then
            fail "400 Bad Request" \
                "$(msg port_invalid)" "sma_port"
        fi
        _want_port=1
    fi

    if body_has sma_password; then
        _newpw=$(body_str sma_password)
        if [ -n "$_newpw" ]; then
            is_password "$_newpw" || fail "400 Bad Request" \
                "$(msg password_invalid)" \
                "sma_password"
            _password=$_newpw
            _want_password=1
        fi
    fi

    acquire_config_lock || fail "409 Conflict" \
        "$(msg config_busy)"

    TMP_FILE="$ENV_FILE.tmp.$$"
    cp -p "$ENV_FILE" "$TMP_FILE" 2>/dev/null \
        || fail "500 Internal Server Error" "$(msg tmp_create)"
    chmod 600 "$TMP_FILE" 2>/dev/null || true

    if [ "$_want_language" = "1" ]; then
        env_set "$TMP_FILE" UI_LANGUAGE "$_language" || \
            fail "500 Internal Server Error" "$(msg write_language)"
    fi
    if [ "$_want_trigger" = "1" ]; then
        env_set "$TMP_FILE" THRESHOLD_TRIGGER "$_trigger" || \
            fail "500 Internal Server Error" "$(msg write_trigger)"
    fi
    if [ "$_want_reset" = "1" ]; then
        env_set "$TMP_FILE" THRESHOLD_RESET "$_reset" || \
            fail "500 Internal Server Error" "$(msg write_reset)"
    fi
    if [ "$_want_enabled" = "1" ]; then
        env_set "$TMP_FILE" SIGNAL_ENABLED "$_enabled" || \
            fail "500 Internal Server Error" "$(msg write_signal)"
    fi
    if [ "$_want_phone" = "1" ]; then
        env_set "$TMP_FILE" SIGNAL_PHONE "$_phone" || \
            fail "500 Internal Server Error" "$(msg write_phone)"
    fi
    if [ "$_want_apikey" = "1" ]; then
        env_set "$TMP_FILE" SIGNAL_APIKEY "$_apikey" || \
            fail "500 Internal Server Error" "$(msg write_apikey)"
    fi
    if [ "$_want_ip" = "1" ]; then
        env_set "$TMP_FILE" SMA_IP "$_ip" || \
            fail "500 Internal Server Error" "$(msg write_ip)"
    fi
    if [ "$_want_port" = "1" ]; then
        env_set "$TMP_FILE" SMA_PORT "$_port" || \
            fail "500 Internal Server Error" "$(msg write_port)"
    fi
    if [ "$_want_password" = "1" ]; then
        env_set "$TMP_FILE" SMA_PASS "$_password" || \
            fail "500 Internal Server Error" "$(msg write_password)"
    fi

    chmod 600 "$TMP_FILE" 2>/dev/null || true
    mv -f "$TMP_FILE" "$ENV_FILE" 2>/dev/null \
        || fail "500 Internal Server Error" "$(msg replace_config)"
    TMP_FILE=

    release_config_lock
    load_env || fail "500 Internal Server Error" \
        "$(msg readback)"
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
        "$(msg config_missing)"

    [ -x "$NOTIFY_SCRIPT" ] || fail "500 Internal Server Error" \
        "$(msg notify_missing)"

    if [ -z "$SIGNAL_PHONE" ] || [ -z "$SIGNAL_APIKEY" ]; then
        fail "400 Bad Request" \
            "$(msg test_credentials)"
    fi

    if test_lock_is_fresh; then
        fail "409 Conflict" "$(msg test_running)"
    fi
    rm -rf "$TEST_LOCK" 2>/dev/null || true
    mkdir -p "$STATE_DIR" 2>/dev/null \
        || fail "500 Internal Server Error" "$(msg state_dir)"
    mkdir "$TEST_LOCK" 2>/dev/null \
        || fail "500 Internal Server Error" "$(msg test_lock)"
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
        || fail "500 Internal Server Error" "$(msg test_runner)"

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
        "{\"ok\":true,\"status\":\"queued\",\"message\":$(json_str "$(msg queued)")}"
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
    load_env 2>/dev/null || true
    if read_test_result; then
        if [ "$TEST_RC" = "0" ]; then
            emit_json "200 OK" \
                "{\"ok\":true,\"status\":\"sent\",\"message\":$(json_str "$(msg test_sent)")}"
        fi
        emit_json "200 OK" \
            "{\"ok\":false,\"status\":\"failed\",\"error\":$(json_str "$(msg test_failed)")}"
    fi

    if test_lock_is_fresh; then
        emit_json "200 OK" \
            "{\"ok\":true,\"status\":\"pending\",\"message\":$(json_str "$(msg test_pending)")}"
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

# Load the saved language before validating the request, so even early 400
# responses are rendered in the user's language.
load_env 2>/dev/null || true

case "$ACTION" in
    config)
        case "$REQUEST_METHOD" in
            GET|HEAD)
                load_env || fail "500 Internal Server Error" \
                    "$(msg config_missing)"
                emit_json "200 OK" "$(config_payload)"
                ;;
            POST)
                check_origin
                read_body
                save_config
                ;;
            *)
                emit_json "405 Method Not Allowed" \
                    "{\"ok\":false,\"error\":$(json_str "$(msg method)")}"
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
                    "{\"ok\":false,\"error\":$(json_str "$(msg method)")}"
                ;;
        esac
        ;;
    *)
        emit_json "404 Not Found" \
            "{\"ok\":false,\"error\":$(json_str "$(msg unknown_action)")}"
        ;;
esac
