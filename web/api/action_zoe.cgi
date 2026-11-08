#!/bin/sh
# =============================================================================
#  Sentinella - Renault Zoe remote HVAC pre-conditioning CGI endpoint
# =============================================================================
#  POST /addons/sentinella/api/action_zoe.cgi
#
#  Security (SEN-01 / SEN-06):
#    - POST only (405 Method Not Allowed otherwise)
#    - Same-origin & application/json Content-Type enforcement
#    - 60-second cooldown lock in $STATE_DIR/.zoe_hvac.lock (mode 600)
#    - Invokes plugins.d/20_renault_zoe.sh --start-hvac
# =============================================================================

PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 077

SCRIPT_DIR=$(cd "$(dirname "$0")" 2>/dev/null && pwd)
[ -n "$SCRIPT_DIR" ] || SCRIPT_DIR=.

resolve_addon_dir() {
    _dir=${SMA_MONITOR_ADDON_DIR:-}

    if [ -z "$_dir" ] && [ -r "$SCRIPT_DIR/addon_dir" ]; then
        _dir=$(sed -n '1p' "$SCRIPT_DIR/addon_dir" 2>/dev/null)
    fi

    if [ -z "$_dir" ]; then
        _candidate=$(cd "$SCRIPT_DIR/../.." 2>/dev/null && pwd)
        if [ -n "$_candidate" ] && { [ -r "$_candidate/.env" ] || [ -d "$_candidate/plugins.d" ]; }; then
            _dir=$_candidate
        fi
    fi

    case "$_dir" in
        /?*) ;;
        *) _dir=/usr/local/addons/sentinella ;;
    esac
    case "$_dir" in
        *..*) _dir=/usr/local/addons/sentinella ;;
    esac
    printf '%s\n' "$_dir"
}

ADDON_DIR=$(resolve_addon_dir)
STATE_DIR=${RENAULT_STATE_DIR:-$ADDON_DIR/state}
HVAC_LOCK=$STATE_DIR/.zoe_hvac.lock
RENAULT_PLUGIN=$ADDON_DIR/plugins.d/20_renault_zoe.sh
COOLDOWN_SEC=${ZOE_HVAC_COOLDOWN:-60}

UI_LANGUAGE=de
for _env_cand in \
    "${SMA_MONITOR_ENV:-}" \
    "$ADDON_DIR/config/config.env" \
    "$ADDON_DIR/.env"
do
    [ -n "$_env_cand" ] && [ -r "$_env_cand" ] || continue
    _raw=$(sed -n 's/^[[:space:]]*UI_LANGUAGE[[:space:]]*=[[:space:]]*//p' "$_env_cand" 2>/dev/null | tail -n 1)
    _raw=$(printf '%s' "$_raw" | sed -e "s/^['\"]//" -e "s/['\"][[:space:]]*$//")
    if [ -n "$_raw" ]; then
        case "$_raw" in
            en|EN|en_US|en-US|en_GB|en-GB) UI_LANGUAGE=en ;;
            *) UI_LANGUAGE=de ;;
        esac
        break
    fi
done

is_uint() {
    case "$1" in
        ''|*[!0-9]*) return 1 ;;
    esac
    return 0
}

is_uint "$COOLDOWN_SEC" || COOLDOWN_SEC=60

json_escape() {
    printf '%s' "$1" | tr -d '\r\n' \
        | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/\t/\\t/g'
}

json_str() {
    printf '"%s"' "$(json_escape "$1")"
}

msg() {
    case "$UI_LANGUAGE:${1:-}" in
        de:method) printf 'Verwenden Sie POST für diesen Endpunkt.' ;;
        en:method) printf 'Use POST for this endpoint.' ;;
        de:cross_origin) printf 'Anfragen von einer anderen Origin oder ohne JSON Content-Type sind nicht erlaubt.' ;;
        en:cross_origin) printf 'Cross-origin or non-JSON requests are not allowed.' ;;
        de:body_too_large) printf 'Der Anfrageinhalt ist zu groß.' ;;
        en:body_too_large) printf 'Request body is too large.' ;;
        de:rate_limited) printf 'Klimatisierung wurde gerade ausgelöst. Bitte 60 Sekunden warten.' ;;
        en:rate_limited) printf 'Pre-conditioning was just triggered. Please wait 60 seconds.' ;;
        de:plugin_missing) printf 'Das Renault-Zoe-Plugin fehlt oder ist nicht ausführbar.' ;;
        en:plugin_missing) printf 'The Renault Zoe plugin is missing or not executable.' ;;
        de:credentials_missing) printf 'Speichern Sie zuerst Renault E-Mail, Passwort und VIN in den Einstellungen.' ;;
        en:credentials_missing) printf 'Configure Renault email, password and VIN in Settings first.' ;;
        de:hvac_ok) printf 'Renault Zoe: Klimatisierung gestartet (21 °C).' ;;
        en:hvac_ok) printf 'Renault Zoe: Pre-conditioning started (21 °C).' ;;
        de:hvac_stop_ok) printf 'Renault Zoe: Klimatisierung beendet.' ;;
        en:hvac_stop_ok) printf 'Renault Zoe: Pre-conditioning stopped.' ;;
        de:hvac_stop_failed) printf 'Klimatisierung konnte nicht beendet werden. Prüfen Sie die Verbindung zum Fahrzeug.' ;;
        en:hvac_stop_failed) printf 'Could not stop pre-conditioning. Check vehicle connectivity.' ;;
        de:bad_action) printf 'Unbekannte Aktion.' ;;
        en:bad_action) printf 'Unknown action.' ;;
        de:hvac_failed) printf 'Klimatisierung konnte nicht gestartet werden. Prüfen Sie die Verbindung zum Fahrzeug.' ;;
        en:hvac_failed) printf 'Could not start pre-conditioning. Check vehicle connectivity.' ;;
        *) printf 'Request failed.' ;;
    esac
}

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
    emit_json "$_status" "{\"ok\":false,\"error\":$(json_str "$_message")}"
}

check_origin() {
    _ct=$(printf '%s' "${CONTENT_TYPE:-}" | tr '[:upper:]' '[:lower:]')
    case "$_ct" in
        application/json|application/json\;*|application/json\ *) ;;
        *) fail "400 Bad Request" "$(msg cross_origin)" ;;
    esac

    _host=$(printf '%s' "${HTTP_HOST:-}" | tr '[:upper:]' '[:lower:]')
    case "$_host" in
        *:80) _host_http=${_host%:80}; _host_https=$_host ;;
        *:443) _host_http=$_host; _host_https=${_host%:443} ;;
        *) _host_http=$_host; _host_https=$_host ;;
    esac

    if [ -n "${HTTP_ORIGIN:-}" ]; then
        [ -n "$_host" ] || fail "403 Forbidden" "$(msg cross_origin)"
        _origin=$(printf '%s' "$HTTP_ORIGIN" | tr '[:upper:]' '[:lower:]')
        _origin=${_origin%/}
        case "$_origin" in
            http://*:80) _origin=${_origin%:80} ;;
            https://*:443) _origin=${_origin%:443} ;;
        esac
        case "$_origin" in
            "http://$_host"|"http://$_host_http"|"https://$_host"|"https://$_host_https") return 0 ;;
        esac
        fail "403 Forbidden" "$(msg cross_origin)"
    fi

    if [ -n "${HTTP_REFERER:-}" ]; then
        [ -n "$_host" ] || fail "403 Forbidden" "$(msg cross_origin)"
        _ref=$(printf '%s' "$HTTP_REFERER" | tr '[:upper:]' '[:lower:]')
        _ref_scheme=${_ref%%://*}
        _ref_auth=${_ref#*://}
        _ref_auth=${_ref_auth%%/*}
        case "$_ref_scheme:$_ref_auth" in
            http:*:80) _ref_auth=${_ref_auth%:80} ;;
            https:*:443) _ref_auth=${_ref_auth%:443} ;;
        esac
        case "$_ref_scheme:$_ref_auth" in
            "http:$_host"|"http:$_host_http"|"https:$_host"|"https:$_host_https") return 0 ;;
        esac
        fail "403 Forbidden" "$(msg cross_origin)"
    fi

    if [ -n "${HTTP_HOST:-}" ] && [ "${HTTP_X_REQUESTED_WITH:-}" != "XMLHttpRequest" ]; then
        fail "403 Forbidden" "$(msg cross_origin)"
    fi
    return 0
}

REQUEST_METHOD=${REQUEST_METHOD:-GET}
if [ "$REQUEST_METHOD" != "POST" ]; then
    fail "405 Method Not Allowed" "$(msg method)"
fi

check_origin

# Active remote HVAC control is temporarily shelved. Reject any direct call
# even though the plugin backend remains present for a future re-enablement.
emit_json "503 Service Unavailable" \
    '{"ok":false,"error":"HVAC feature temporarily disabled"}'

CONTENT_LENGTH=${CONTENT_LENGTH:-0}
is_uint "$CONTENT_LENGTH" || fail "400 Bad Request" "$(msg cross_origin)"
[ "$CONTENT_LENGTH" -le 4096 ] || fail "413 Payload Too Large" "$(msg body_too_large)"
BODY=
if [ "$CONTENT_LENGTH" -gt 0 ]; then
    BODY=$(dd bs=1 count="$CONTENT_LENGTH" 2>/dev/null) || true
fi

# Accepted: "start" / "stop" (legacy "start_hvac" / "stop_hvac"). Empty body = start.
ACTION=$(printf '%s' "$BODY" | tr -d '\r\n' \
    | sed -n 's/.*"action"[[:space:]]*:[[:space:]]*"\([A-Za-z_]*\)".*/\1/p')
case "$ACTION" in
    ''|start|start_hvac) ACTION=start ;;
    stop|stop_hvac) ACTION=stop ;;
    *) fail "400 Bad Request" "$(msg bad_action)" ;;
esac

mkdir -p "$STATE_DIR" 2>/dev/null || true

NOW=$(date +%s 2>/dev/null)
is_uint "$NOW" || NOW=0

if [ "$ACTION" = "start" ] && [ -r "$HVAC_LOCK" ]; then
    LAST_TS=$(sed -n '1p' "$HVAC_LOCK" 2>/dev/null | tr -cd '0-9')
    if is_uint "$LAST_TS" && [ "$NOW" -ge "$LAST_TS" ]; then
        ELAPSED=$((NOW - LAST_TS))
        if [ "$ELAPSED" -lt "$COOLDOWN_SEC" ]; then
            RETRY_AFTER=$((COOLDOWN_SEC - ELAPSED))
            emit_json "429 Too Many Requests" \
                "{\"ok\":false,\"error\":$(json_str "$(msg rate_limited)"),\"retry_after\":$RETRY_AFTER}"
        fi
    fi
fi

[ -x "$RENAULT_PLUGIN" ] || fail "500 Internal Server Error" "$(msg plugin_missing)"

# Reserve cooldown lock before network call so concurrent requests cannot race
# (start only; cancelling must always be possible)
if [ "$ACTION" = "start" ]; then
(
    umask 077
    printf '%s\n' "$NOW" > "$HVAC_LOCK.tmp.$$"
) 2>/dev/null && {
    chmod 600 "$HVAC_LOCK.tmp.$$" 2>/dev/null || true
    mv -f "$HVAC_LOCK.tmp.$$" "$HVAC_LOCK" 2>/dev/null || rm -f "$HVAC_LOCK.tmp.$$" 2>/dev/null
}
fi

SMA_MONITOR_ADDON_DIR=$ADDON_DIR \
RENAULT_ADDON_DIR=$ADDON_DIR \
    "$RENAULT_PLUGIN" "--${ACTION}-hvac" >/dev/null 2>&1
RC=$?

if [ "$RC" = "0" ] && [ "$ACTION" = "stop" ]; then
    # Cancelled: clear the start cooldown so the user can restart right away
    rm -f "$HVAC_LOCK" 2>/dev/null || true
    emit_json "200 OK" \
        "{\"ok\":true,\"action\":\"hvac_stopped\",\"message\":$(json_str "$(msg hvac_stop_ok)")}"
fi

if [ "$RC" = "0" ]; then
    emit_json "200 OK" \
        "{\"ok\":true,\"action\":\"hvac_started\",\"message\":$(json_str "$(msg hvac_ok)")}"
fi

# Release lock on failed start so the user can retry after fixing credentials/network
[ "$ACTION" = "start" ] && rm -f "$HVAC_LOCK" 2>/dev/null

if [ "$RC" = "2" ]; then
    fail "400 Bad Request" "$(msg credentials_missing)"
fi

if [ "$ACTION" = "stop" ]; then
    fail "502 Bad Gateway" "$(msg hvac_stop_failed)"
fi
fail "502 Bad Gateway" "$(msg hvac_failed)"
