#!/bin/sh
# =============================================================================
#  Sentinella - plugin manager API (CGI)
# =============================================================================
#  BusyBox ash only. Reads and toggles the enabled state of the bundled
#  plugins, persisted in <addon>/config/plugins.env (PLUGIN_<NAME>=0|1).
#
#  Endpoints:
#      GET  /addons/sentinella/api/plugin_manager.cgi
#           -> {"ok":true,"plugins":{"10_ccu_health":true,...}}
#      POST /addons/sentinella/api/plugin_manager.cgi
#           body {"id":"15_weather","enabled":false}
#           -> same payload as GET after persisting
#
#  Plugin IDs are matched against a fixed whitelist; nothing from the request
#  is ever used to build a path or a shell variable name.
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
        if [ -n "$_candidate" ] && [ -d "$_candidate/state" ]; then
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
PLUGINS_ENV=$ADDON_DIR/config/plugins.env
STATE_DIR=$ADDON_DIR/state/plugins
LOCK_DIR=$ADDON_DIR/config/.plugins.lock

# Whitelist: <plugin id> <env variable> <state card file>
PLUGIN_TABLE='10_ccu_health PLUGIN_CCU_HEALTH ccu_health.json
15_weather PLUGIN_WEATHER weather.json
20_renault_zoe PLUGIN_RENAULT_ZOE renault_zoe.json
25_shelly_em PLUGIN_SHELLY_EM shelly_em.json
30_forecast_solar PLUGIN_FORECAST_SOLAR forecast_solar.json
35_tibber PLUGIN_TIBBER tibber.json'

headers() {
    [ -n "$1" ] && printf 'Status: %s\r\n' "$1"
    printf 'Content-Type: application/json; charset=utf-8\r\n'
    printf 'Cache-Control: no-store, max-age=0\r\n'
    printf 'Pragma: no-cache\r\n'
    printf 'X-Content-Type-Options: nosniff\r\n'
    printf 'Referrer-Policy: same-origin\r\n\r\n'
}

fail() {
    headers "$1"
    printf '{"ok":false,"error":"%s"}\n' "$2"
    exit 0
}

# Prints 1 or 0 for the given variable. Optional plugins (PV forecast,
# Renault E-Tech, Shelly EM, and Dynamic Tariff) default to 0 (disabled) on a
# clean install; the core CCU3 health and weather plugins default to 1 (enabled).
flag_value() {
    _v=""
    if [ -f "$PLUGINS_ENV" ] && [ ! -L "$PLUGINS_ENV" ]; then
        _v=$(sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "$PLUGINS_ENV" 2>/dev/null \
            | tail -n 1 | tr -d "\"' \r")
    fi
    case "$_v" in
        1|true|TRUE|on|ON|yes|YES) printf '1' ;;
        0|false|FALSE|off|OFF|no|NO)
            if [ "$1" = "PLUGIN_SHELLY_EM" ] && [ -f "$ADDON_DIR/config/plugins/shelly_em.env" ]; then
                _se=$(sed -n 's/^[[:space:]]*SHELLY_ENABLED[[:space:]]*=[[:space:]]*//p' \
                    "$ADDON_DIR/config/plugins/shelly_em.env" 2>/dev/null | tail -n 1 | tr -d "\"' \r")
                case "$_se" in
                    1|true|TRUE|on|ON|yes|YES) printf '1'; return 0 ;;
                esac
            elif [ "$1" = "PLUGIN_TIBBER" ] && [ -f "$ADDON_DIR/config/plugins/tibber.env" ]; then
                _te=$(sed -n 's/^[[:space:]]*TARIFF_ENABLED[[:space:]]*=[[:space:]]*//p' \
                    "$ADDON_DIR/config/plugins/tibber.env" 2>/dev/null | tail -n 1 | tr -d "\"' \r")
                case "$_te" in
                    1|true|TRUE|on|ON|yes|YES) printf '1'; return 0 ;;
                esac
            fi
            printf '0'
            ;;
        *)
            if [ "$1" = "PLUGIN_SHELLY_EM" ] && [ -f "$ADDON_DIR/config/plugins/shelly_em.env" ]; then
                _se=$(sed -n 's/^[[:space:]]*SHELLY_ENABLED[[:space:]]*=[[:space:]]*//p' \
                    "$ADDON_DIR/config/plugins/shelly_em.env" 2>/dev/null | tail -n 1 | tr -d "\"' \r")
                case "$_se" in
                    1|true|TRUE|on|ON|yes|YES) printf '1'; return 0 ;;
                esac
            elif [ "$1" = "PLUGIN_TIBBER" ] && [ -f "$ADDON_DIR/config/plugins/tibber.env" ]; then
                _te=$(sed -n 's/^[[:space:]]*TARIFF_ENABLED[[:space:]]*=[[:space:]]*//p' \
                    "$ADDON_DIR/config/plugins/tibber.env" 2>/dev/null | tail -n 1 | tr -d "\"' \r")
                case "$_te" in
                    1|true|TRUE|on|ON|yes|YES) printf '1'; return 0 ;;
                esac
            fi
            if [ "$1" = "PLUGIN_FORECAST_SOLAR" ] || [ "$1" = "PLUGIN_RENAULT_ZOE" ] || \
               [ "$1" = "PLUGIN_SHELLY_EM" ] || [ "$1" = "PLUGIN_TIBBER" ]; then
                printf '0'
            else
                printf '1'
            fi
            ;;
    esac
}

emit_state() {
    headers ""
    printf '{"ok":true,"plugins":{'
    _sep=""
    printf '%s\n' "$PLUGIN_TABLE" | while read -r _id _var _card; do
        [ "$(flag_value "$_var")" = "1" ] && _b=true || _b=false
        printf '%s"%s":%s' "$_sep" "$_id" "$_b"
        _sep=","
    done
    printf '}}\n'
}

REQUEST_METHOD=${REQUEST_METHOD:-GET}
case "$REQUEST_METHOD" in
    GET) emit_state; exit 0 ;;
    HEAD) headers ""; exit 0 ;;
    POST) ;;
    *) fail "405 Method Not Allowed" "Use GET or POST." ;;
esac

# --- CSRF guard: JSON media type, same-origin, custom header over HTTP ---
_ct=$(printf '%s' "${CONTENT_TYPE:-}" | tr '[:upper:]' '[:lower:]')
case "$_ct" in
    application/json|application/json\;*|application/json\ *) ;;
    *) fail "400 Bad Request" "Content-Type must be application/json." ;;
esac
_host=$(printf '%s' "${HTTP_HOST:-}" | tr '[:upper:]' '[:lower:]')
case "$_host" in
    *:80) _host_http=${_host%:80}; _host_https=$_host ;;
    *:443) _host_http=$_host; _host_https=${_host%:443} ;;
    *) _host_http=$_host; _host_https=$_host ;;
esac
if [ -n "${HTTP_ORIGIN:-}" ]; then
    [ -n "$_host" ] || fail "403 Forbidden" "Cross-origin request rejected."
    _origin=$(printf '%s' "$HTTP_ORIGIN" | tr '[:upper:]' '[:lower:]')
    _origin=${_origin%/}
    case "$_origin" in
        http://*:80) _origin=${_origin%:80} ;;
        https://*:443) _origin=${_origin%:443} ;;
    esac
    case "$_origin" in
        "http://$_host"|"http://$_host_http"|"https://$_host"|"https://$_host_https") ;;
        *) fail "403 Forbidden" "Cross-origin request rejected." ;;
    esac
elif [ -n "${HTTP_REFERER:-}" ]; then
    [ -n "$_host" ] || fail "403 Forbidden" "Cross-origin request rejected."
    _ref=$(printf '%s' "$HTTP_REFERER" | tr '[:upper:]' '[:lower:]')
    _ref_scheme=${_ref%%://*}
    _ref_auth=${_ref#*://}
    _ref_auth=${_ref_auth%%/*}
    case "$_ref_scheme:$_ref_auth" in
        http:*:80) _ref_auth=${_ref_auth%:80} ;;
        https:*:443) _ref_auth=${_ref_auth%:443} ;;
    esac
    case "$_ref_scheme:$_ref_auth" in
        "http:$_host"|"http:$_host_http"|"https:$_host"|"https:$_host_https") ;;
        *) fail "403 Forbidden" "Cross-origin request rejected." ;;
    esac
elif [ -n "${HTTP_HOST:-}${GATEWAY_INTERFACE:-}" ] && [ "${HTTP_X_REQUESTED_WITH:-}" != "XMLHttpRequest" ]; then
    fail "403 Forbidden" "Cross-origin request rejected."
fi

# --- Body ---
CONTENT_LENGTH=${CONTENT_LENGTH:-0}
case "$CONTENT_LENGTH" in
    ''|*[!0-9]*) fail "400 Bad Request" "Invalid Content-Length." ;;
esac
[ "$CONTENT_LENGTH" -gt 0 ] && [ "$CONTENT_LENGTH" -le 512 ] \
    || fail "400 Bad Request" "Invalid request body size."
BODY=$(dd bs=1 count="$CONTENT_LENGTH" 2>/dev/null | tr -d '\r\n\t')

REQ_ID=$(printf '%s' "$BODY" | sed -n 's/.*"id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
REQ_EN=$(printf '%s' "$BODY" | sed -n 's/.*"enabled"[[:space:]]*:[[:space:]]*\([a-z]*\).*/\1/p')

TARGET_VAR=""
TARGET_CARD=""
while read -r _id _var _card; do
    if [ "$_id" = "$REQ_ID" ]; then
        TARGET_VAR=$_var
        TARGET_CARD=$_card
    fi
done <<EOF
$PLUGIN_TABLE
EOF
[ -n "$TARGET_VAR" ] || fail "400 Bad Request" "Unknown plugin id."
case "$REQ_EN" in
    true) NEW=1 ;;
    false) NEW=0 ;;
    *) fail "400 Bad Request" "Field enabled must be true or false." ;;
esac

# --- Persist atomically under a mkdir lock ---
mkdir -p "$ADDON_DIR/config" 2>/dev/null
_tries=0
until mkdir "$LOCK_DIR" 2>/dev/null; do
    _tries=$((_tries + 1))
    [ "$_tries" -lt 20 ] || fail "503 Service Unavailable" "Plugin config is locked."
    sleep 1
done
TMP=$PLUGINS_ENV.tmp.$$
trap 'rm -f "$TMP" 2>/dev/null; rmdir "$LOCK_DIR" 2>/dev/null' EXIT INT TERM
{
    printf '# =============================================================================\n'
    printf '#  Sentinella - Master Plugin State Configuration\n'
    printf '# =============================================================================\n'
    printf '#  Persists enabled (1) or disabled (0) state for each installed plugin.\n'
    printf '#  Managed automatically via the WebUI Settings Drawer.\n'
    printf '# =============================================================================\n'
    printf '%s\n' "$PLUGIN_TABLE" | while read -r _id _var _card; do
        if [ "$_var" = "$TARGET_VAR" ]; then
            _val=$NEW
        else
            _val=$(flag_value "$_var")
        fi
        printf "%s='%s'\n" "$_var" "$_val"
    done
} > "$TMP" 2>/dev/null || { rm -f "$TMP"; fail "500 Internal Server Error" "Cannot write plugin config."; }
chmod 600 "$TMP" 2>/dev/null
mv -f "$TMP" "$PLUGINS_ENV" || { rm -f "$TMP"; fail "500 Internal Server Error" "Cannot write plugin config."; }
sync 2>/dev/null || true

# Disabling hides the card immediately instead of waiting for the next run.
if [ "$NEW" = "0" ] && [ -f "$STATE_DIR/$TARGET_CARD" ]; then
    rm -f "$STATE_DIR/$TARGET_CARD" 2>/dev/null || true
elif [ "$NEW" = "1" ]; then
    SCRIPT="${ADDON_DIR}/plugins.d/${REQ_ID}.sh"
    if [ -x "$SCRIPT" ]; then
        # Run once to immediately re-generate the state JSON card
        SMA_MONITOR_ADDON_DIR="$ADDON_DIR" "$SCRIPT" </dev/null >/dev/null 2>&1 &
    fi
fi

emit_state
