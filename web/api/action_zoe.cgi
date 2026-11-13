#!/bin/sh
# =============================================================================
#  Sentinella - Renault Zoe remote HVAC pre-conditioning CGI endpoint
# =============================================================================
#  POST /addons/sentinella/api/action_zoe.cgi
#
#  Security (SEN-01 / SEN-06):
#    - POST only (405 Method Not Allowed otherwise)
#    - Same-origin & application/json Content-Type enforcement
#    - Remote HVAC control is shelved: every valid request gets 503. The
#      unreachable dispatch path was removed to shrink the attack surface;
#      the plugin backend (20_renault_zoe.sh --start-hvac) remains in place.
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

    if [ -n "${HTTP_HOST:-}${GATEWAY_INTERFACE:-}" ] && [ "${HTTP_X_REQUESTED_WITH:-}" != "XMLHttpRequest" ]; then
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
