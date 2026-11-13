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
# No pathname expansion: request-derived lists are word-split unquoted, and a
# value like "*" must never expand to file names.
set -f

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
        *) _dir=/usr/local/addons/sentinella ;;
    esac
    case "$_dir" in
        *..*) _dir=/usr/local/addons/sentinella ;;
    esac
    printf '%s\n' "$_dir"
}

ADDON_DIR=$(resolve_addon_dir)
ENV_FILE=${SMA_MONITOR_ENV:-}
if [ -z "$ENV_FILE" ]; then
    if [ -r "$ADDON_DIR/.env" ]; then
        ENV_FILE=$ADDON_DIR/.env
    elif [ -r "$ADDON_DIR/config/config.env" ]; then
        ENV_FILE=$ADDON_DIR/config/config.env
    elif [ -r "$ADDON_DIR/config.env" ]; then
        ENV_FILE=$ADDON_DIR/config.env
    else
        ENV_FILE=$ADDON_DIR/.env
    fi
fi
CONFIG_ENV_FILE=$ADDON_DIR/config/config.env

STATE_DIR=$ADDON_DIR/state
RENAULT_CONFIG_DIR=$ADDON_DIR/config/plugins
RENAULT_ENV_FILE=${RENAULT_ENV:-$RENAULT_CONFIG_DIR/renault.env}
RENAULT_PLUGIN=$ADDON_DIR/plugins.d/20_renault_zoe.sh
RENAULT_SESSION_FILE=$STATE_DIR/plugins/.renault_session
RENAULT_CARD_FILE=$STATE_DIR/plugins/renault_zoe.json
RENAULT_TMP_FILE=
CCU_HEALTH_CONFIG_DIR=$ADDON_DIR/config/plugins
CCU_HEALTH_ENV_FILE=${CCU_HEALTH_ENV:-$CCU_HEALTH_CONFIG_DIR/ccu_health.env}
CCU_HEALTH_TMP_FILE=
WEATHER_CONFIG_DIR=$ADDON_DIR/config/plugins
WEATHER_ENV_FILE=${WEATHER_ENV:-$WEATHER_CONFIG_DIR/weather.env}
WEATHER_TMP_FILE=
FORECAST_CONFIG_DIR=$ADDON_DIR/config/plugins
FORECAST_ENV_FILE=${FORECAST_SOLAR_ENV:-$FORECAST_CONFIG_DIR/forecast_solar.env}
FORECAST_TMP_FILE=
SHELLY_CONFIG_DIR=$ADDON_DIR/config/plugins
SHELLY_ENV_FILE=${SHELLY_ENV:-$SHELLY_CONFIG_DIR/shelly_em.env}
SHELLY_TMP_FILE=
TIBBER_CONFIG_DIR=$ADDON_DIR/config/plugins
TIBBER_ENV_FILE=${TIBBER_ENV:-$TIBBER_CONFIG_DIR/tibber.env}
TIBBER_TMP_FILE=
PLUGINS_ENV_FILE=$ADDON_DIR/config/plugins.env
NOTIFY_SCRIPT=$ADDON_DIR/lib/notify_signal.sh
TEST_RESULT=$STATE_DIR/notify_test.result
TEST_LOG=$STATE_DIR/notify_test.log
TEST_LOCK=$STATE_DIR/.test_signal.lock
RENAULT_TEST_LOCK=$STATE_DIR/.test_renault.lock
CONFIG_LOCK=$STATE_DIR/.config.lock
TEST_WRAPPER=
UI_LANGUAGE=en
HERO_SOURCE=auto
NOTIFY_DISABLED=0
SHELLY_ENABLED=false
SHELLY_IP=
SHELLY_USER=
SHELLY_PASS=
SHELLY_INTERVAL=60
TARIFF_ENABLED=false
TARIFF_PROVIDER=awattar_de
TIBBER_TOKEN=
TARIFF_SURCHARGE_CT=0
TARIFF_INTERVAL=900
RENAULT_USER=
RENAULT_PASS=
RENAULT_VIN=
RENAULT_NOTIFY_LOW_ENABLED=0
RENAULT_NOTIFY_LOW_SOC=20
RENAULT_NOTIFY_HIGH_ENABLED=0
RENAULT_NOTIFY_HIGH_SOC=80
CCU_NOTIFY_DC_ENABLED=0
CCU_NOTIFY_DC_THRESHOLD=80
WEATHER_CITY=LOKAL
WEATHER_LAT=51.51
WEATHER_LON=7.46
FORECAST_KWP=
FORECAST_DEC=35
FORECAST_AZ=0
FORECAST_LAT=
FORECAST_LON=

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

is_float() {
    case "$1" in
        ''|*[!0-9.-]*) return 1 ;;
    esac
    _f_rest=${1#-}
    case "$_f_rest" in
        ''|.*|*.|*.*.*|*..*|*.-*|*--*|*[!0-9.]*) return 1 ;;
    esac
    return 0
}

is_float_in_range() {
    is_float "$1" || return 1
    awk -v v="$1" -v lo="$2" -v hi="$3" 'BEGIN { exit !((v + 0) >= (lo + 0) && (v + 0) <= (hi + 0)) }' 2>/dev/null
}

is_safe_label() {
    case "$1" in
        *'"'*|*\\*|*'$'*|*'`'*) return 1 ;;
    esac
    if printf '%s' "$1" | grep -q '[[:cntrl:]]'; then
        return 1
    fi
    [ "${#1}" -le 64 ]
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

normalize_host() {
    _nh=$(trim_ws "${1:-}")
    case "$_nh" in
        [Hh][Tt][Tt][Pp]://*|[Hh][Tt][Tt][Pp][Ss]://*)
            _nh=${_nh#*://}
            _nh=${_nh%%/*}
            _nh=${_nh%%\?*}
            _nh=${_nh%%\#*}
            ;;
        *)
            while :; do
                case "$_nh" in
                    */) _nh=${_nh%/} ;;
                    *) break ;;
                esac
            done
            ;;
    esac
    trim_ws "$_nh"
}

is_signal_uuid() {
    printf '%s' "$1" | grep -Eq \
        '^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$'
}

is_apikey_token() {
    case "$1" in
        *[![:print:]]*) return 1 ;;
    esac
    printf '%s' "$1" | grep -Eq '^[A-Za-z0-9._:-]{1,128}$'
}

is_signal_recipient() {
    case "$1" in
        *[![:print:]]*) return 1 ;;
    esac
    is_signal_uuid "$1" && return 0
    printf '%s' "$1" | grep -Eq '^\+?[0-9]{7,15}$'
}

is_signal_phone_list() {
    _list=$(printf '%s' "${1:-}" | tr ',;' '  ')
    _count=0
    for _item in $_list; do
        _count=$((_count + 1))
        case "$_item" in
            *:*)
                _u=${_item%%:*}
                _k=${_item#*:}
                is_signal_recipient "$_u" || return 1
                is_apikey_token "$_k" || return 1
                ;;
            *)
                is_signal_recipient "$_item" || return 1
                ;;
        esac
    done
    [ "$_count" -ge 1 ]
}

is_apikey() {
    _list=$(printf '%s' "${1:-}" | tr ',;' '  ')
    _count=0
    for _item in $_list; do
        _count=$((_count + 1))
        is_apikey_token "$_item" || return 1
    done
    [ "$_count" -ge 1 ]
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

is_masked_secret() {
    case "$1" in
        ''|'••••••••'*|'********'*) return 0 ;;
    esac
    printf '%s' "$1" | grep -Eq '^[•*]+$'
}

is_renault_user() {
    case "$1" in
        ''|*[[:space:]]*|*'"'*|*\\*) return 1 ;;
    esac
    if printf '%s' "$1" | grep -q '[[:cntrl:]]'; then
        return 1
    fi
    printf '%s' "$1" | grep -Eq '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$' || return 1
    [ "${#1}" -le 128 ]
}

is_renault_vin() {
    case "$1" in
        *[![:print:]]*) return 1 ;;
    esac
    printf '%s' "$1" | grep -Eq '^[A-Z0-9]{6,24}$'
}

# SIGNAL_PHONE may carry inline per-recipient CallMeBot keys ("uuid:key").
# The key part is a credential: it is replaced by a fixed mask before the list
# ever leaves the CGI, and a masked key in a POST is restored from the stored
# list for the same recipient.
SECRET_MASK='********'

mask_phone_list() {
    _mpl_out=
    for _mpl_item in $(printf '%s' "${1:-}" | tr ',;' '  '); do
        case "$_mpl_item" in
            *:*) _mpl_item="${_mpl_item%%:*}:$SECRET_MASK" ;;
        esac
        _mpl_out="${_mpl_out:+$_mpl_out,}$_mpl_item"
    done
    printf '%s' "$_mpl_out"
}

restore_phone_list() {
    _rpl_out=
    for _rpl_item in $(printf '%s' "${1:-}" | tr ',;' '  '); do
        case "$_rpl_item" in
            *:*)
                _rpl_u=${_rpl_item%%:*}
                if is_masked_secret "${_rpl_item#*:}"; then
                    _rpl_item=$_rpl_u
                    for _rpl_old in $(printf '%s' "${2:-}" | tr ',;' '  '); do
                        case "$_rpl_old" in
                            "$_rpl_u":?*) _rpl_item=$_rpl_old; break ;;
                        esac
                    done
                fi
                ;;
        esac
        _rpl_out="${_rpl_out:+$_rpl_out,}$_rpl_item"
    done
    printf '%s' "$_rpl_out"
}

# Runtime credential files must never be group/world readable, whoever
# created them (installer, manual edit, older release).
harden_env_perms() {
    for _hp in "$ENV_FILE" "$CONFIG_ENV_FILE" "$RENAULT_ENV_FILE" \
        "$CCU_HEALTH_ENV_FILE" "$WEATHER_ENV_FILE" "$FORECAST_ENV_FILE" \
        "$SHELLY_ENV_FILE" "$TIBBER_ENV_FILE" \
        "$ADDON_DIR/config/plugins.env" "$ADDON_DIR/config.env"; do
        [ -f "$_hp" ] && [ ! -L "$_hp" ] && chmod 600 "$_hp" 2>/dev/null
    done
    return 0
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
        de:notify_disabled_bool) printf 'Ungültiger Wert für die Benachrichtigungs-Stummschaltung.' ;;
        en:notify_disabled_bool) printf 'Invalid value for the notification mute switch.' ;;
        de:phone_invalid) printf 'Geben Sie eine gültige Signal Account UUID oder Telefonnummer im E.164-Format ein (zum Beispiel 98526357-0cec-4e7f-9d60-d854432414d3 oder +491701234567).' ;;
        en:phone_invalid) printf 'Enter a valid Signal Account UUID or E.164 phone number (for example 98526357-0cec-4e7f-9d60-d854432414d3 or +491701234567).' ;;
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
        de:write_notify_disabled) printf 'Die Benachrichtigungs-Stummschaltung kann nicht geschrieben werden.' ;;
        en:write_notify_disabled) printf 'Cannot write the notification mute switch.' ;;
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
        de:renault_user_invalid) printf 'Geben Sie eine gültige Renault-E-Mail-Adresse ein.' ;;
        en:renault_user_invalid) printf 'Enter a valid Renault email address.' ;;
        de:renault_pass_invalid) printf 'Das Renault-Passwort enthält nicht unterstützte Zeichen.' ;;
        en:renault_pass_invalid) printf 'The Renault password contains unsupported characters.' ;;
        de:renault_vin_invalid) printf 'Geben Sie eine gültige Fahrzeug-Identifizierungsnummer (VIN, z. B. VF1...) ein.' ;;
        en:renault_vin_invalid) printf 'Enter a valid vehicle identification number (VIN, e.g. VF1...).' ;;
        de:write_renault) printf 'Die Renault-Konfiguration kann nicht geschrieben werden.' ;;
        en:write_renault) printf 'Cannot write the Renault configuration.' ;;
        de:renault_test_credentials) printf 'Speichern Sie zuerst Renault E-Mail, Passwort und VIN.' ;;
        en:renault_test_credentials) printf 'Enter Renault email, password and VIN before testing.' ;;
        de:renault_plugin_missing) printf 'Das Renault-Plugin fehlt oder ist nicht ausführbar.' ;;
        en:renault_plugin_missing) printf 'The Renault plugin is missing or not executable.' ;;
        de:renault_test_ok) printf 'Renault: Verbindung erfolgreich hergestellt.' ;;
        en:renault_test_ok) printf 'Renault: Connection verified.' ;;
        de:renault_test_failed) printf 'Renault-Verbindung fehlgeschlagen. Prüfen Sie E-Mail, Passwort und VIN.' ;;
        en:renault_test_failed) printf 'Renault connection failed. Check email, password and VIN.' ;;
        de:renault_low_soc_invalid) printf 'Geben Sie einen Schwellenwert für niedrigen SoC zwischen 0 und 100 ein.' ;;
        en:renault_low_soc_invalid) printf 'Enter a low SoC threshold between 0 and 100.' ;;
        de:renault_low_en_bool) printf 'Ungültiger Wert für Warnung bei niedrigem SoC.' ;;
        en:renault_low_en_bool) printf 'Invalid boolean for low battery warning.' ;;
        de:renault_high_soc_invalid) printf 'Geben Sie ein Ladeziel zwischen 0 und 100 ein.' ;;
        en:renault_high_soc_invalid) printf 'Enter a target SoC between 0 and 100.' ;;
        de:renault_high_en_bool) printf 'Ungültiger Wert für Benachrichtigung bei Ladeziel.' ;;
        en:renault_high_en_bool) printf 'Invalid boolean for target charge notification.' ;;
        de:ccu_dc_threshold_invalid) printf 'Geben Sie einen Duty-Cycle-Schwellenwert zwischen 0 und 100 ein.' ;;
        en:ccu_dc_threshold_invalid) printf 'Enter a Duty Cycle threshold between 0 and 100.' ;;
        de:ccu_dc_en_bool) printf 'Ungültiger Wert für Duty-Cycle-Warnung.' ;;
        en:ccu_dc_en_bool) printf 'Invalid boolean for Duty Cycle warning.' ;;
        de:write_ccu_health) printf 'Die CCU3-System-Konfiguration kann nicht geschrieben werden.' ;;
        en:write_ccu_health) printf 'Cannot write the CCU3 system configuration.' ;;
        de:weather_city_invalid) printf 'Geben Sie einen gültigen Stadtnamen/Standort ein.' ;;
        en:weather_city_invalid) printf 'Enter a valid city or location label.' ;;
        de:weather_lat_invalid) printf 'Geben Sie einen gültigen Breitengrad (-90 bis 90) ein.' ;;
        en:weather_lat_invalid) printf 'Enter a valid latitude (-90 to 90).' ;;
        de:weather_lon_invalid) printf 'Geben Sie einen gültigen Längengrad (-180 bis 180) ein.' ;;
        en:weather_lon_invalid) printf 'Enter a valid longitude (-180 to 180).' ;;
        de:write_weather) printf 'Die Wetter-Konfiguration kann nicht geschrieben werden.' ;;
        en:write_weather) printf 'Cannot write the weather configuration.' ;;
        de:forecast_kwp_invalid) printf 'Geben Sie eine gültige Peak-Leistung in kWp (> 0) ein.' ;;
        en:forecast_kwp_invalid) printf 'Enter a valid peak power in kWp (> 0).' ;;
        de:forecast_dec_invalid) printf 'Geben Sie eine gültige Dachneigung zwischen 0 und 90° ein.' ;;
        en:forecast_dec_invalid) printf 'Enter a valid roof tilt between 0 and 90°.' ;;
        de:forecast_az_invalid) printf 'Geben Sie eine gültige Ausrichtung zwischen -180 und 180° ein.' ;;
        en:forecast_az_invalid) printf 'Enter a valid azimuth between -180 and 180°.' ;;
        de:forecast_lat_invalid) printf 'Geben Sie einen gültigen Breitengrad (-90 bis 90) ein.' ;;
        en:forecast_lat_invalid) printf 'Enter a valid latitude (-90 to 90).' ;;
        de:forecast_lon_invalid) printf 'Geben Sie einen gültigen Längengrad (-180 bis 180) ein.' ;;
        en:forecast_lon_invalid) printf 'Enter a valid longitude (-180 to 180).' ;;
        de:write_forecast) printf 'Die PV-Prognose-Konfiguration kann nicht geschrieben werden.' ;;
        en:write_forecast) printf 'Cannot write the PV forecast configuration.' ;;
        de:write_failed) printf 'Die Konfiguration konnte nicht geschrieben werden.' ;;
        en:write_failed) printf 'Could not write the configuration.' ;;
        de:shelly_ip_invalid) printf 'Geben Sie eine gültige IPv4-Adresse oder einen Hostnamen des Shelly ein.' ;;
        en:shelly_ip_invalid) printf 'Enter a valid Shelly IPv4 address or hostname.' ;;
        *) printf 'Request failed.' ;;
    esac
}

# shellcheck disable=SC2317,SC2329  # invoked indirectly through trap
cleanup() {
    [ -n "${TMP_FILE:-}" ] && rm -f "$TMP_FILE" 2>/dev/null
    [ -n "${RENAULT_TMP_FILE:-}" ] && rm -f "$RENAULT_TMP_FILE" 2>/dev/null
    [ -n "${CCU_HEALTH_TMP_FILE:-}" ] && rm -f "$CCU_HEALTH_TMP_FILE" 2>/dev/null
    [ -n "${WEATHER_TMP_FILE:-}" ] && rm -f "$WEATHER_TMP_FILE" 2>/dev/null
    [ -n "${FORECAST_TMP_FILE:-}" ] && rm -f "$FORECAST_TMP_FILE" 2>/dev/null
    [ -n "${SHELLY_TMP_FILE:-}" ] && rm -f "$SHELLY_TMP_FILE" 2>/dev/null
    [ -n "${TIBBER_TMP_FILE:-}" ] && rm -f "$TIBBER_TMP_FILE" 2>/dev/null
    [ -n "$TEST_WRAPPER" ] && rm -f "$TEST_WRAPPER" 2>/dev/null
    if [ "${CONFIG_LOCK_OWNED:-0}" = "1" ]; then
        rm -rf "$CONFIG_LOCK" 2>/dev/null
    fi
    if [ "${TEST_LOCK_OWNED:-0}" = "1" ]; then
        rm -rf "$TEST_LOCK" 2>/dev/null
    fi
    if [ "${RENAULT_TEST_LOCK_OWNED:-0}" = "1" ]; then
        rm -rf "$RENAULT_TEST_LOCK" 2>/dev/null
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
    # State-changing requests must be JSON. Browsers cannot send this media
    # type cross-origin from a plain HTML form without a CORS preflight, which
    # this endpoint never grants.
    # Media types and host names are case-insensitive (RFC 9110 / RFC 3986).
    _ct=$(printf '%s' "${CONTENT_TYPE:-}" | tr '[:upper:]' '[:lower:]')
    case "$_ct" in
        application/json|application/json\;*|application/json\ *) ;;
        *) fail "400 Bad Request" "$(msg cross_origin)" ;;
    esac

    _host=$(printf '%s' "${HTTP_HOST:-}" | tr '[:upper:]' '[:lower:]')
    # Strip a default port so "ccu3:80" and "ccu3" compare equal.
    case "$_host" in
        *:80) _host_http=${_host%:80}; _host_https=$_host ;;
        *:443) _host_http=$_host; _host_https=${_host%:443} ;;
        *) _host_http=$_host; _host_https=$_host ;;
    esac

    # A present Origin must match this host. An Origin without a Host header
    # can never be verified and is rejected ("http://" must not match "").
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

    # Otherwise fall back to Referer when the browser supplied one.
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

    # Over HTTP (a Host header is present) require either the custom header set
    # by app.js or one of the two origin headers above. CLI test harnesses do
    # not set HTTP_HOST and remain allowed.
    if [ -n "${HTTP_HOST:-}" ] && [ "${HTTP_X_REQUESTED_WITH:-}" != "XMLHttpRequest" ]; then
        fail "403 Forbidden" "$(msg cross_origin)"
    fi
    return 0
}

# POST bodies are flat JSON. The UI sends exactly these keys; unknown keys are
# ignored. body_lines splits the object on structural '{', '}' and ',' outside
# of string literals, so a value containing a comma is never truncated and a
# key name embedded inside another value is never matched.
body_lines() {
    # Only depth-1 members are emitted: keys inside nested objects/arrays can
    # never shadow a top-level setting, and nested values collapse to empty.
    printf '%s\n' "$BODY" | tr -d '\r\n' | awk '
    {
        s = $0; n = length(s); q = 0; esc = 0; d = 0; out = ""
        for (i = 1; i <= n; i++) {
            c = substr(s, i, 1)
            if (esc) { if (d == 1) out = out c; esc = 0; continue }
            if (q && c == "\\") { if (d == 1) out = out c; esc = 1; continue }
            if (c == "\"") { q = (!q); if (d == 1) out = out c; continue }
            if (!q && (c == "{" || c == "[")) {
                d++
                if (d == 1) out = out "\n"
                continue
            }
            if (!q && (c == "}" || c == "]")) {
                if (d == 1) out = out "\n"
                if (d > 0) d--
                continue
            }
            if (!q && c == "," && d == 1) { out = out "\n"; continue }
            if (d == 1) out = out c
        }
        printf "%s", out
    }'
}

body_has() {
    body_lines | grep -Eq "^[[:space:]]*\"$1\"[[:space:]]*:"
}

# Extract and decode a JSON string value. Escaped quotes and backslashes are
# honoured, so "a\"b" yields a"b instead of being truncated at the escape.
body_str() {
    body_lines | awk -v k="$1" '
    {
        line = $0
        sub(/^[ \t]*/, "", line)
        if (substr(line, 1, length(k) + 2) != "\"" k "\"") next
        line = substr(line, length(k) + 3)
        sub(/^[ \t]*:[ \t]*/, "", line)
        if (substr(line, 1, 1) != "\"") next
        n = length(line); out = ""; ok = 0
        for (i = 2; i <= n; i++) {
            c = substr(line, i, 1)
            if (c == "\\") {
                i++; e = substr(line, i, 1)
                # Control escapes (\n \r \t \b \f \uXXXX) are dropped: a decoded
                # newline must never reach the root-sourced env files.
                if (e == "n" || e == "r" || e == "t" || e == "b" || e == "f") continue
                if (e == "u") { i += 4; continue }
                out = out e
                continue
            }
            if (c == "\"") { ok = 1; break }
            out = out c
        }
        if (ok) { printf "%s\n", out; exit }
    }'
}

body_num() {
    body_lines \
        | sed -n 's/^[[:space:]]*"'"$1"'"[[:space:]]*:[[:space:]]*\(-\{0,1\}[0-9][0-9]*\).*$/\1/p' \
        | head -n 1
}

body_float() {
    _bf_raw=$(body_str "$1")
    if [ -z "$_bf_raw" ]; then
        _bf_raw=$(body_lines \
            | sed -n 's/^[[:space:]]*"'"$1"'"[[:space:]]*:[[:space:]]*\(-\{0,1\}[0-9][0-9]*\(\.[0-9][0-9]*\)\{0,1\}\).*$/\1/p' \
            | head -n 1)
    fi
    printf '%s' "$_bf_raw" | tr ',' '.'
}

body_bool() {
    _bool_raw=$(body_str "$1")
    if [ -z "$_bool_raw" ]; then
        _bool_raw=$(body_lines \
            | sed -n 's/^[[:space:]]*"'"$1"'"[[:space:]]*:[[:space:]]*\(true\|false\).*$/\1/p' \
            | head -n 1)
    fi
    case "$_bool_raw" in
        true|TRUE|True|1|on|ON|On|yes|YES) printf 'true' ;;
        false|FALSE|False|0|off|OFF|Off|no|NO) printf 'false' ;;
        *) printf '' ;;
    esac
}

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
load_renault_env() {
    RENAULT_USER=
    RENAULT_PASS=
    RENAULT_VIN=
    RENAULT_NOTIFY_LOW_ENABLED=0
    RENAULT_NOTIFY_LOW_SOC=20
    RENAULT_NOTIFY_HIGH_ENABLED=0
    RENAULT_NOTIFY_HIGH_SOC=80
    if [ -r "$RENAULT_ENV_FILE" ]; then
        # shellcheck source=/dev/null
        . "$RENAULT_ENV_FILE" 2>/dev/null || true
    fi
    RENAULT_USER=$(trim_ws "${RENAULT_USER:-}")
    RENAULT_PASS=${RENAULT_PASS:-}
    RENAULT_VIN=$(trim_ws "${RENAULT_VIN:-}" | tr '[:lower:]' '[:upper:]')
    case "$RENAULT_NOTIFY_LOW_ENABLED" in 1|true|TRUE|on|ON|yes|YES) RENAULT_NOTIFY_LOW_ENABLED=1 ;; *) RENAULT_NOTIFY_LOW_ENABLED=0 ;; esac
    case "$RENAULT_NOTIFY_HIGH_ENABLED" in 1|true|TRUE|on|ON|yes|YES) RENAULT_NOTIFY_HIGH_ENABLED=1 ;; *) RENAULT_NOTIFY_HIGH_ENABLED=0 ;; esac
    is_uint "$RENAULT_NOTIFY_LOW_SOC" || RENAULT_NOTIFY_LOW_SOC=20
    is_uint "$RENAULT_NOTIFY_HIGH_SOC" || RENAULT_NOTIFY_HIGH_SOC=80
    return 0
}

load_ccu_health_env() {
    CCU_NOTIFY_DC_ENABLED=0
    CCU_NOTIFY_DC_THRESHOLD=80
    if [ -r "$CCU_HEALTH_ENV_FILE" ]; then
        # shellcheck source=/dev/null
        . "$CCU_HEALTH_ENV_FILE" 2>/dev/null || true
    fi
    case "$CCU_NOTIFY_DC_ENABLED" in 1|true|TRUE|on|ON|yes|YES) CCU_NOTIFY_DC_ENABLED=1 ;; *) CCU_NOTIFY_DC_ENABLED=0 ;; esac
    is_uint "$CCU_NOTIFY_DC_THRESHOLD" || CCU_NOTIFY_DC_THRESHOLD=80
    return 0
}

load_weather_env() {
    WEATHER_CITY=LOKAL
    WEATHER_LAT=51.51
    WEATHER_LON=7.46
    if [ -r "$WEATHER_ENV_FILE" ]; then
        # shellcheck source=/dev/null
        . "$WEATHER_ENV_FILE" 2>/dev/null || true
    fi
    WEATHER_CITY=$(trim_ws "${WEATHER_CITY:-}")
    [ -n "$WEATHER_CITY" ] && is_safe_label "$WEATHER_CITY" || WEATHER_CITY=LOKAL
    is_float "${WEATHER_LAT:-}" || WEATHER_LAT=51.51
    is_float "${WEATHER_LON:-}" || WEATHER_LON=7.46
    return 0
}

load_forecast_env() {
    FORECAST_KWP=
    FORECAST_DEC=35
    FORECAST_AZ=0
    FORECAST_LAT=
    FORECAST_LON=
    if [ -r "$FORECAST_ENV_FILE" ]; then
        # shellcheck source=/dev/null
        . "$FORECAST_ENV_FILE" 2>/dev/null || true
    fi
    FORECAST_KWP=$(trim_ws "${FORECAST_KWP:-}" | tr ',' '.')
    is_float "$FORECAST_KWP" || FORECAST_KWP=
    is_float "${FORECAST_DEC:-}" || FORECAST_DEC=35
    is_float "${FORECAST_AZ:-}" || FORECAST_AZ=0
    FORECAST_LAT=$(trim_ws "${FORECAST_LAT:-}" | tr ',' '.')
    is_float "$FORECAST_LAT" || FORECAST_LAT=
    FORECAST_LON=$(trim_ws "${FORECAST_LON:-}" | tr ',' '.')
    is_float "$FORECAST_LON" || FORECAST_LON=
    return 0
}

load_shelly_env() {
    SHELLY_ENABLED=false
    SHELLY_IP=
    SHELLY_USER=
    SHELLY_PASS=
    SHELLY_INTERVAL=60
    if [ -r "$SHELLY_ENV_FILE" ]; then
        # shellcheck source=/dev/null
        . "$SHELLY_ENV_FILE" 2>/dev/null || true
    fi
    return 0
}

load_tibber_env() {
    TARIFF_ENABLED=false
    TARIFF_PROVIDER=awattar_de
    TIBBER_TOKEN=
    TARIFF_SURCHARGE_CT=0
    TARIFF_INTERVAL=900
    if [ -r "$TIBBER_ENV_FILE" ]; then
        # shellcheck source=/dev/null
        . "$TIBBER_ENV_FILE" 2>/dev/null || true
    fi
    return 0
}

load_env() {
    load_renault_env
    load_ccu_health_env
    load_weather_env
    load_forecast_env
    load_shelly_env
    load_tibber_env
    [ -r "$ENV_FILE" ] || return 1
    # shellcheck source=/dev/null
    . "$ENV_FILE" || return 1

    case "${THRESHOLD_TRIGGER:-}" in ''|*[!0-9]*) THRESHOLD_TRIGGER=95 ;; esac
    case "${THRESHOLD_RESET:-}" in ''|*[!0-9]*) THRESHOLD_RESET=90 ;; esac
    SIGNAL_ENABLED=${SIGNAL_ENABLED:-false}
    SIGNAL_PHONE=$(trim_ws "${SIGNAL_PHONE:-}")
    SIGNAL_APIKEY=$(trim_ws "${SIGNAL_APIKEY:-${SIGNAL_API_KEY:-}}")
    SMA_IP=${SMA_IP:-}
    SMA_ENABLED=${SMA_ENABLED:-}
    if [ -z "$SMA_ENABLED" ]; then
        if [ -n "$SMA_IP" ]; then SMA_ENABLED=1; else SMA_ENABLED=0; fi
    fi
    case "$SMA_ENABLED" in
        1|true|TRUE|on|ON|yes|YES) SMA_ENABLED=1 ;;
        *) SMA_ENABLED=0 ;;
    esac
    SMA_PORT=${SMA_PORT:-443}
    SMA_PASS=${SMA_PASS:-}
    case "${UI_LANGUAGE:-}" in
        de|DE|de_DE|de-DE) UI_LANGUAGE=de ;;
        *) UI_LANGUAGE=en ;;
    esac
    case "${HERO_SOURCE:-auto}" in
        auto|sma|ccu|shelly) HERO_SOURCE=${HERO_SOURCE:-auto} ;;
        *) HERO_SOURCE=auto ;;
    esac
    case "${NOTIFY_DISABLED:-}" in
        1|true|TRUE|on|ON|yes|YES) NOTIFY_DISABLED=1 ;;
        *) NOTIFY_DISABLED=0 ;;
    esac
    load_renault_env
    load_ccu_health_env
    load_weather_env
    load_forecast_env
    load_shelly_env
    load_tibber_env
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
    _renault_pass_set=false
    [ -n "$RENAULT_PASS" ] && _renault_pass_set=true
    _shelly_pass_set=false
    [ -n "$SHELLY_PASS" ] && _shelly_pass_set=true
    _tibber_token_set=false
    [ -n "$TIBBER_TOKEN" ] && _tibber_token_set=true

    _writable=false
    [ -w "$ENV_FILE" ] && _writable=true

    _renault_low_en=false
    [ "$RENAULT_NOTIFY_LOW_ENABLED" = "1" ] && _renault_low_en=true
    _renault_high_en=false
    [ "$RENAULT_NOTIFY_HIGH_ENABLED" = "1" ] && _renault_high_en=true
    _ccu_dc_en=false
    [ "$CCU_NOTIFY_DC_ENABLED" = "1" ] && _ccu_dc_en=true
    _notify_disabled=false
    [ "$NOTIFY_DISABLED" = "1" ] && _notify_disabled=true

    case "$SMA_PORT" in
        ''|*[!0-9]*) _port=443 ;;
        *) _port=$SMA_PORT ;;
    esac

    printf '{"ok":true'
    printf ',"ui_language":%s' "$(json_str "$UI_LANGUAGE")"
    printf ',"hero_source":%s' "$(json_str "$HERO_SOURCE")"
    printf ',"trigger_soc":%s' "$_trigger"
    printf ',"reset_soc":%s' "$_reset"
    printf ',"signal_enabled":%s' "$(json_bool_str "$_enabled")"
    printf ',"notify_disabled":%s' "$_notify_disabled"
    printf ',"signal_phone":%s' "$(json_str "$(mask_phone_list "$SIGNAL_PHONE")")"
    printf ',"signal_apikey_set":%s' "$_apikey_set"
    _sma_enabled=false
    [ "$SMA_ENABLED" = "1" ] && _sma_enabled=true
    printf ',"sma_enabled":%s' "$_sma_enabled"
    printf ',"sma_ip":%s' "$(json_str "$SMA_IP")"
    printf ',"sma_port":%s' "$_port"
    printf ',"sma_password_set":%s' "$_password_set"
    printf ',"renault_user":%s' "$(json_str "$RENAULT_USER")"
    printf ',"renault_vin":%s' "$(json_str "$RENAULT_VIN")"
    printf ',"renault_pass_set":%s' "$_renault_pass_set"
    printf ',"renault_password_set":%s' "$_renault_pass_set"
    printf ',"renault_notify_low_enabled":%s' "$_renault_low_en"
    printf ',"renault_notify_low_soc":%s' "$RENAULT_NOTIFY_LOW_SOC"
    printf ',"renault_notify_high_enabled":%s' "$_renault_high_en"
    printf ',"renault_notify_high_soc":%s' "$RENAULT_NOTIFY_HIGH_SOC"
    printf ',"ccu_notify_dc_enabled":%s' "$_ccu_dc_en"
    printf ',"ccu_notify_dc_threshold":%s' "$CCU_NOTIFY_DC_THRESHOLD"
    printf ',"weather_city":%s' "$(json_str "$WEATHER_CITY")"
    printf ',"weather_lat":%s' "$(json_str "$WEATHER_LAT")"
    printf ',"weather_lon":%s' "$(json_str "$WEATHER_LON")"
    printf ',"shelly_enabled":%s' "$(json_bool_str "$SHELLY_ENABLED")"
    printf ',"shelly_ip":%s' "$(json_str "$SHELLY_IP")"
    printf ',"shelly_user":%s' "$(json_str "$SHELLY_USER")"
    printf ',"shelly_pass_set":%s' "$_shelly_pass_set"
    printf ',"shelly_interval":%s' "${SHELLY_INTERVAL:-60}"
    printf ',"tariff_enabled":%s' "$(json_bool_str "$TARIFF_ENABLED")"
    printf ',"tariff_provider":%s' "$(json_str "$TARIFF_PROVIDER")"
    printf ',"tibber_token_set":%s' "$_tibber_token_set"
    printf ',"tariff_surcharge_ct":%s' "${TARIFF_SURCHARGE_CT:-0}"
    printf ',"tariff_interval":%s' "${TARIFF_INTERVAL:-900}"
    printf ',"forecast_kwp":%s' "$(json_str "$FORECAST_KWP")"
    printf ',"forecast_dec":%s' "$(json_str "$FORECAST_DEC")"
    printf ',"forecast_az":%s' "$(json_str "$FORECAST_AZ")"
    printf ',"forecast_lat":%s' "$(json_str "$FORECAST_LAT")"
    printf ',"forecast_lon":%s' "$(json_str "$FORECAST_LON")"
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

save_renault_env() {
    _r_user=$1
    _r_pass=$2
    _r_vin=$3
    _r_low_en=${4:-$RENAULT_NOTIFY_LOW_ENABLED}
    _r_low_soc=${5:-$RENAULT_NOTIFY_LOW_SOC}
    _r_high_en=${6:-$RENAULT_NOTIFY_HIGH_ENABLED}
    _r_high_soc=${7:-$RENAULT_NOTIFY_HIGH_SOC}

    mkdir -p "$RENAULT_CONFIG_DIR" 2>/dev/null || return 1
    chmod 700 "$ADDON_DIR/config" "$RENAULT_CONFIG_DIR" 2>/dev/null || true

    RENAULT_TMP_FILE="$RENAULT_ENV_FILE.tmp.$$"
    (
        umask 077
        if [ -f "$RENAULT_ENV_FILE" ]; then
            cp -p "$RENAULT_ENV_FILE" "$RENAULT_TMP_FILE" 2>/dev/null
        else
            cat > "$RENAULT_TMP_FILE" <<'EOF'
# Sentinella - Renault Zoe plugin credentials (mode 600)
RENAULT_USER=''
RENAULT_PASS=''
RENAULT_VIN=''
RENAULT_NOTIFY_LOW_ENABLED='0'
RENAULT_NOTIFY_LOW_SOC='20'
RENAULT_NOTIFY_HIGH_ENABLED='0'
RENAULT_NOTIFY_HIGH_SOC='80'
EOF
        fi
    ) || return 1

    chmod 600 "$RENAULT_TMP_FILE" 2>/dev/null || true
    env_set "$RENAULT_TMP_FILE" RENAULT_USER "$_r_user" || return 1
    env_set "$RENAULT_TMP_FILE" RENAULT_PASS "$_r_pass" || return 1
    env_set "$RENAULT_TMP_FILE" RENAULT_VIN "$_r_vin" || return 1
    env_set "$RENAULT_TMP_FILE" RENAULT_NOTIFY_LOW_ENABLED "$_r_low_en" || return 1
    env_set "$RENAULT_TMP_FILE" RENAULT_NOTIFY_LOW_SOC "$_r_low_soc" || return 1
    env_set "$RENAULT_TMP_FILE" RENAULT_NOTIFY_HIGH_ENABLED "$_r_high_en" || return 1
    env_set "$RENAULT_TMP_FILE" RENAULT_NOTIFY_HIGH_SOC "$_r_high_soc" || return 1
    chmod 600 "$RENAULT_TMP_FILE" 2>/dev/null || true

    mv -f "$RENAULT_TMP_FILE" "$RENAULT_ENV_FILE" 2>/dev/null || return 1
    chmod 600 "$RENAULT_ENV_FILE" 2>/dev/null || true
    RENAULT_TMP_FILE=

    # Invalidate cached token whenever Renault credentials are updated
    rm -f "$RENAULT_SESSION_FILE" 2>/dev/null || true
    if [ -z "$_r_user" ] || [ -z "$_r_vin" ]; then
        rm -f "$RENAULT_CARD_FILE" 2>/dev/null || true
    fi
    return 0
}

save_ccu_health_env() {
    _c_dc_en=${1:-$CCU_NOTIFY_DC_ENABLED}
    _c_dc_thresh=${2:-$CCU_NOTIFY_DC_THRESHOLD}

    mkdir -p "$CCU_HEALTH_CONFIG_DIR" 2>/dev/null || return 1
    chmod 700 "$ADDON_DIR/config" "$CCU_HEALTH_CONFIG_DIR" 2>/dev/null || true

    CCU_HEALTH_TMP_FILE="$CCU_HEALTH_ENV_FILE.tmp.$$"
    (
        umask 077
        if [ -f "$CCU_HEALTH_ENV_FILE" ]; then
            cp -p "$CCU_HEALTH_ENV_FILE" "$CCU_HEALTH_TMP_FILE" 2>/dev/null
        else
            cat > "$CCU_HEALTH_TMP_FILE" <<'EOF'
# Sentinella - CCU3 Health plugin configuration (mode 600)
CCU_NOTIFY_DC_ENABLED='0'
CCU_NOTIFY_DC_THRESHOLD='80'
EOF
        fi
    ) || return 1

    chmod 600 "$CCU_HEALTH_TMP_FILE" 2>/dev/null || true
    env_set "$CCU_HEALTH_TMP_FILE" CCU_NOTIFY_DC_ENABLED "$_c_dc_en" || return 1
    env_set "$CCU_HEALTH_TMP_FILE" CCU_NOTIFY_DC_THRESHOLD "$_c_dc_thresh" || return 1
    chmod 600 "$CCU_HEALTH_TMP_FILE" 2>/dev/null || true

    mv -f "$CCU_HEALTH_TMP_FILE" "$CCU_HEALTH_ENV_FILE" 2>/dev/null || return 1
    chmod 600 "$CCU_HEALTH_ENV_FILE" 2>/dev/null || true
    CCU_HEALTH_TMP_FILE=
    return 0
}

save_weather_env() {
    _w_city=${1:-$WEATHER_CITY}
    _w_lat=${2:-$WEATHER_LAT}
    _w_lon=${3:-$WEATHER_LON}

    mkdir -p "$WEATHER_CONFIG_DIR" 2>/dev/null || return 1
    chmod 700 "$ADDON_DIR/config" "$WEATHER_CONFIG_DIR" 2>/dev/null || true

    WEATHER_TMP_FILE="$WEATHER_ENV_FILE.tmp.$$"
    (
        umask 077
        if [ -f "$WEATHER_ENV_FILE" ]; then
            cp -p "$WEATHER_ENV_FILE" "$WEATHER_TMP_FILE" 2>/dev/null
        else
            cat > "$WEATHER_TMP_FILE" <<'EOF'
# Sentinella - Weather plugin configuration (mode 600)
WEATHER_CITY='LOKAL'
WEATHER_LAT='51.51'
WEATHER_LON='7.46'
EOF
        fi
    ) || return 1

    chmod 600 "$WEATHER_TMP_FILE" 2>/dev/null || true
    env_set "$WEATHER_TMP_FILE" WEATHER_CITY "$_w_city" || return 1
    env_set "$WEATHER_TMP_FILE" WEATHER_LAT "$_w_lat" || return 1
    env_set "$WEATHER_TMP_FILE" WEATHER_LON "$_w_lon" || return 1
    chmod 600 "$WEATHER_TMP_FILE" 2>/dev/null || true

    mv -f "$WEATHER_TMP_FILE" "$WEATHER_ENV_FILE" 2>/dev/null || return 1
    chmod 600 "$WEATHER_ENV_FILE" 2>/dev/null || true
    WEATHER_TMP_FILE=
    return 0
}

save_forecast_env() {
    _f_kwp=${1:-$FORECAST_KWP}
    _f_dec=${2:-$FORECAST_DEC}
    _f_az=${3:-$FORECAST_AZ}
    _f_lat=${4:-$FORECAST_LAT}
    _f_lon=${5:-$FORECAST_LON}

    mkdir -p "$FORECAST_CONFIG_DIR" 2>/dev/null || return 1
    chmod 700 "$ADDON_DIR/config" "$FORECAST_CONFIG_DIR" 2>/dev/null || true

    FORECAST_TMP_FILE="$FORECAST_ENV_FILE.tmp.$$"
    (
        umask 077
        if [ -f "$FORECAST_ENV_FILE" ]; then
            cp -p "$FORECAST_ENV_FILE" "$FORECAST_TMP_FILE" 2>/dev/null
        else
            cat > "$FORECAST_TMP_FILE" <<'EOF'
# Sentinella - PV Forecast (EXPERIMENTAL) plugin configuration (mode 600)
FORECAST_KWP=''
FORECAST_DEC='35'
FORECAST_AZ='0'
FORECAST_LAT=''
FORECAST_LON=''
EOF
        fi
    ) || return 1

    chmod 600 "$FORECAST_TMP_FILE" 2>/dev/null || true
    env_set "$FORECAST_TMP_FILE" FORECAST_KWP "$_f_kwp" || return 1
    env_set "$FORECAST_TMP_FILE" FORECAST_DEC "$_f_dec" || return 1
    env_set "$FORECAST_TMP_FILE" FORECAST_AZ "$_f_az" || return 1
    env_set "$FORECAST_TMP_FILE" FORECAST_LAT "$_f_lat" || return 1
    env_set "$FORECAST_TMP_FILE" FORECAST_LON "$_f_lon" || return 1
    chmod 600 "$FORECAST_TMP_FILE" 2>/dev/null || true

    mv -f "$FORECAST_TMP_FILE" "$FORECAST_ENV_FILE" 2>/dev/null || return 1
    chmod 600 "$FORECAST_ENV_FILE" 2>/dev/null || true
    FORECAST_TMP_FILE=
    return 0
}

save_shelly_env() {
    _s_en=${1:-$SHELLY_ENABLED}
    _s_ip=${2:-$SHELLY_IP}
    _s_user=${3:-$SHELLY_USER}
    _s_pass=${4:-$SHELLY_PASS}
    _s_int=${5:-$SHELLY_INTERVAL}

    mkdir -p "$SHELLY_CONFIG_DIR" 2>/dev/null || return 1
    chmod 700 "$ADDON_DIR/config" "$SHELLY_CONFIG_DIR" 2>/dev/null || true

    SHELLY_TMP_FILE=$SHELLY_ENV_FILE.tmp.$$
    (
        umask 077
        if [ -r "$SHELLY_ENV_FILE" ]; then
            cat "$SHELLY_ENV_FILE" > "$SHELLY_TMP_FILE"
        else
            cat > "$SHELLY_TMP_FILE" <<'EOF'
# Sentinella - Shelly EM plugin configuration (mode 600)
SHELLY_ENABLED='false'
SHELLY_IP=''
SHELLY_USER=''
SHELLY_PASS=''
SHELLY_INTERVAL='60'
EOF
        fi
    ) || return 1

    chmod 600 "$SHELLY_TMP_FILE" 2>/dev/null || true
    env_set "$SHELLY_TMP_FILE" SHELLY_ENABLED "$_s_en" || return 1
    env_set "$SHELLY_TMP_FILE" SHELLY_IP "$_s_ip" || return 1
    env_set "$SHELLY_TMP_FILE" SHELLY_USER "$_s_user" || return 1
    env_set "$SHELLY_TMP_FILE" SHELLY_PASS "$_s_pass" || return 1
    env_set "$SHELLY_TMP_FILE" SHELLY_INTERVAL "$_s_int" || return 1
    chmod 600 "$SHELLY_TMP_FILE" 2>/dev/null || true

    mv -f "$SHELLY_TMP_FILE" "$SHELLY_ENV_FILE" 2>/dev/null || return 1
    chmod 600 "$SHELLY_ENV_FILE" 2>/dev/null || true
    SHELLY_TMP_FILE=
    return 0
}

save_tibber_env() {
    _t_en=${1:-$TARIFF_ENABLED}
    _t_prov=${2:-$TARIFF_PROVIDER}
    _t_tok=${3:-$TIBBER_TOKEN}
    _t_sur=${4:-$TARIFF_SURCHARGE_CT}
    _t_int=${5:-$TARIFF_INTERVAL}

    mkdir -p "$TIBBER_CONFIG_DIR" 2>/dev/null || return 1
    chmod 700 "$ADDON_DIR/config" "$TIBBER_CONFIG_DIR" 2>/dev/null || true

    TIBBER_TMP_FILE=$TIBBER_ENV_FILE.tmp.$$
    (
        umask 077
        if [ -r "$TIBBER_ENV_FILE" ]; then
            cat "$TIBBER_ENV_FILE" > "$TIBBER_TMP_FILE"
        else
            cat > "$TIBBER_TMP_FILE" <<'EOF'
# Sentinella - Dynamic Tariff plugin configuration (mode 600)
TARIFF_ENABLED='false'
TARIFF_PROVIDER='awattar_de'
TIBBER_TOKEN=''
TARIFF_SURCHARGE_CT='0'
TARIFF_INTERVAL='900'
EOF
        fi
    ) || return 1

    chmod 600 "$TIBBER_TMP_FILE" 2>/dev/null || true
    env_set "$TIBBER_TMP_FILE" TARIFF_ENABLED "$_t_en" || return 1
    env_set "$TIBBER_TMP_FILE" TARIFF_PROVIDER "$_t_prov" || return 1
    env_set "$TIBBER_TMP_FILE" TIBBER_TOKEN "$_t_tok" || return 1
    env_set "$TIBBER_TMP_FILE" TARIFF_SURCHARGE_CT "$_t_sur" || return 1
    env_set "$TIBBER_TMP_FILE" TARIFF_INTERVAL "$_t_int" || return 1
    chmod 600 "$TIBBER_TMP_FILE" 2>/dev/null || true

    mv -f "$TIBBER_TMP_FILE" "$TIBBER_ENV_FILE" 2>/dev/null || return 1
    chmod 600 "$TIBBER_ENV_FILE" 2>/dev/null || true
    TIBBER_TMP_FILE=
    return 0
}

sync_plugins_env() {
    _pe_key=$1
    _pe_val=$2
    mkdir -p "$ADDON_DIR/config" 2>/dev/null || return 1
    _pe_tmp=$PLUGINS_ENV_FILE.tmp.$$
    (
        umask 077
        if [ -r "$PLUGINS_ENV_FILE" ]; then
            cat "$PLUGINS_ENV_FILE" > "$_pe_tmp"
        else
            cat > "$_pe_tmp" <<'EOF'
# Sentinella - Plugin Manager configuration
PLUGIN_CCU_HEALTH=1
PLUGIN_WEATHER=1
PLUGIN_RENAULT_ZOE=0
PLUGIN_FORECAST_SOLAR=0
PLUGIN_SHELLY_EM=0
PLUGIN_TIBBER=0
EOF
        fi
    ) || return 1
    chmod 600 "$_pe_tmp" 2>/dev/null || true
    env_set "$_pe_tmp" "$_pe_key" "$_pe_val" || { rm -f "$_pe_tmp" 2>/dev/null || true; return 1; }
    mv -f "$_pe_tmp" "$PLUGINS_ENV_FILE" 2>/dev/null || { rm -f "$_pe_tmp" 2>/dev/null || true; return 1; }
    chmod 600 "$PLUGINS_ENV_FILE" 2>/dev/null || true
    return 0
}

parse_and_validate_renault() {
    _ruser=$RENAULT_USER
    _rpass=$RENAULT_PASS
    _rvin=$RENAULT_VIN
    _r_low_en_val=$RENAULT_NOTIFY_LOW_ENABLED
    _r_low_soc_val=$RENAULT_NOTIFY_LOW_SOC
    _r_high_en_val=$RENAULT_NOTIFY_HIGH_ENABLED
    _r_high_soc_val=$RENAULT_NOTIFY_HIGH_SOC
    _want_renault=0

    _ruser_field=
    if body_has renault_user; then
        _ruser_field=renault_user
    elif body_has renault_email; then
        _ruser_field=renault_email
    fi
    if [ -n "$_ruser_field" ]; then
        _ruser=$(trim_ws "$(body_str "$_ruser_field")")
        if [ -n "$_ruser" ] && ! is_renault_user "$_ruser"; then
            fail "400 Bad Request" "$(msg renault_user_invalid)" "renault_user"
        fi
        _want_renault=1
    fi

    if body_has renault_vin; then
        _rvin=$(trim_ws "$(body_str renault_vin)" | tr '[:lower:]' '[:upper:]')
        if [ -n "$_rvin" ] && ! is_renault_vin "$_rvin"; then
            fail "400 Bad Request" "$(msg renault_vin_invalid)" "renault_vin"
        fi
        _want_renault=1
    fi

    _rpass_field=
    if body_has renault_pass; then
        _rpass_field=renault_pass
    elif body_has renault_password; then
        _rpass_field=renault_password
    fi
    if [ -n "$_rpass_field" ]; then
        _new_rpass=$(body_str "$_rpass_field")
        if [ -n "$_new_rpass" ] && ! is_masked_secret "$_new_rpass"; then
            is_password "$_new_rpass" || fail "400 Bad Request" \
                "$(msg renault_pass_invalid)" "renault_pass"
            _rpass=$_new_rpass
            _want_renault=1
        fi
    fi

    if body_has renault_notify_low_enabled; then
        _r_low_en=$(body_bool renault_notify_low_enabled)
        if [ -z "$_r_low_en" ]; then
            fail "400 Bad Request" "$(msg renault_low_en_bool)" "renault_notify_low_enabled"
        fi
        [ "$_r_low_en" = "true" ] && _r_low_en_val=1 || _r_low_en_val=0
        _want_renault=1
    fi

    if body_has renault_notify_low_soc; then
        _r_low_soc=$(body_num renault_notify_low_soc)
        if ! is_uint "$_r_low_soc" || [ "$_r_low_soc" -lt 0 ] || [ "$_r_low_soc" -gt 100 ]; then
            fail "400 Bad Request" "$(msg renault_low_soc_invalid)" "renault_notify_low_soc"
        fi
        _r_low_soc_val=$_r_low_soc
        _want_renault=1
    fi

    if body_has renault_notify_high_enabled; then
        _r_high_en=$(body_bool renault_notify_high_enabled)
        if [ -z "$_r_high_en" ]; then
            fail "400 Bad Request" "$(msg renault_high_en_bool)" "renault_notify_high_enabled"
        fi
        [ "$_r_high_en" = "true" ] && _r_high_en_val=1 || _r_high_en_val=0
        _want_renault=1
    fi

    if body_has renault_notify_high_soc; then
        _r_high_soc=$(body_num renault_notify_high_soc)
        if ! is_uint "$_r_high_soc" || [ "$_r_high_soc" -lt 0 ] || [ "$_r_high_soc" -gt 100 ]; then
            fail "400 Bad Request" "$(msg renault_high_soc_invalid)" "renault_notify_high_soc"
        fi
        _r_high_soc_val=$_r_high_soc
        _want_renault=1
    fi
}

parse_and_validate_ccu_health() {
    _c_dc_en_val=$CCU_NOTIFY_DC_ENABLED
    _c_dc_thresh_val=$CCU_NOTIFY_DC_THRESHOLD
    _want_ccu_health=0

    if body_has ccu_notify_dc_enabled; then
        _c_dc_en=$(body_bool ccu_notify_dc_enabled)
        if [ -z "$_c_dc_en" ]; then
            fail "400 Bad Request" "$(msg ccu_dc_en_bool)" "ccu_notify_dc_enabled"
        fi
        [ "$_c_dc_en" = "true" ] && _c_dc_en_val=1 || _c_dc_en_val=0
        _want_ccu_health=1
    fi

    if body_has ccu_notify_dc_threshold; then
        _c_dc_thresh=$(body_num ccu_notify_dc_threshold)
        if ! is_uint "$_c_dc_thresh" || [ "$_c_dc_thresh" -lt 0 ] || [ "$_c_dc_thresh" -gt 100 ]; then
            fail "400 Bad Request" "$(msg ccu_dc_threshold_invalid)" "ccu_notify_dc_threshold"
        fi
        _c_dc_thresh_val=$_c_dc_thresh
        _want_ccu_health=1
    fi
}

parse_and_validate_weather() {
    _w_city_val=$WEATHER_CITY
    _w_lat_val=$WEATHER_LAT
    _w_lon_val=$WEATHER_LON
    _want_weather=0

    if body_has weather_city; then
        _w_city=$(trim_ws "$(body_str weather_city)")
        [ -n "$_w_city" ] || _w_city=LOKAL
        if ! is_safe_label "$_w_city"; then
            fail "400 Bad Request" "$(msg weather_city_invalid)" "weather_city"
        fi
        _w_city_val=$_w_city
        _want_weather=1
    fi

    if body_has weather_lat; then
        _w_lat=$(trim_ws "$(body_float weather_lat)")
        if [ -n "$_w_lat" ]; then
            is_float_in_range "$_w_lat" -90 90 || fail "400 Bad Request" "$(msg weather_lat_invalid)" "weather_lat"
            _w_lat_val=$_w_lat
        fi
        _want_weather=1
    fi

    if body_has weather_lon; then
        _w_lon=$(trim_ws "$(body_float weather_lon)")
        if [ -n "$_w_lon" ]; then
            is_float_in_range "$_w_lon" -180 180 || fail "400 Bad Request" "$(msg weather_lon_invalid)" "weather_lon"
            _w_lon_val=$_w_lon
        fi
        _want_weather=1
    fi
}

parse_and_validate_forecast() {
    _f_kwp_val=$FORECAST_KWP
    _f_dec_val=$FORECAST_DEC
    _f_az_val=$FORECAST_AZ
    _f_lat_val=$FORECAST_LAT
    _f_lon_val=$FORECAST_LON
    _want_forecast=0

    if body_has forecast_kwp; then
        _f_kwp=$(trim_ws "$(body_float forecast_kwp)")
        if [ -n "$_f_kwp" ]; then
            is_float_in_range "$_f_kwp" 0.01 10000 || fail "400 Bad Request" "$(msg forecast_kwp_invalid)" "forecast_kwp"
        fi
        _f_kwp_val=$_f_kwp
        _want_forecast=1
    fi

    if body_has forecast_dec; then
        _f_dec=$(trim_ws "$(body_float forecast_dec)")
        if [ -n "$_f_dec" ]; then
            is_float_in_range "$_f_dec" 0 90 || fail "400 Bad Request" "$(msg forecast_dec_invalid)" "forecast_dec"
            _f_dec_val=$_f_dec
        fi
        _want_forecast=1
    fi

    if body_has forecast_az; then
        _f_az=$(trim_ws "$(body_float forecast_az)")
        if [ -n "$_f_az" ]; then
            is_float_in_range "$_f_az" -180 180 || fail "400 Bad Request" "$(msg forecast_az_invalid)" "forecast_az"
            _f_az_val=$_f_az
        fi
        _want_forecast=1
    fi

    if body_has forecast_lat; then
        _f_lat=$(trim_ws "$(body_float forecast_lat)")
        if [ -n "$_f_lat" ]; then
            is_float_in_range "$_f_lat" -90 90 || fail "400 Bad Request" "$(msg forecast_lat_invalid)" "forecast_lat"
        fi
        _f_lat_val=$_f_lat
        _want_forecast=1
    fi

    if body_has forecast_lon; then
        _f_lon=$(trim_ws "$(body_float forecast_lon)")
        if [ -n "$_f_lon" ]; then
            is_float_in_range "$_f_lon" -180 180 || fail "400 Bad Request" "$(msg forecast_lon_invalid)" "forecast_lon"
        fi
        _f_lon_val=$_f_lon
        _want_forecast=1
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
    _sma_enabled=$SMA_ENABLED
    _port=$SMA_PORT
    _password=$SMA_PASS
    _language=$UI_LANGUAGE
    _hero_source=$HERO_SOURCE
    _notify_disabled=$NOTIFY_DISABLED

    _want_trigger=0
    _want_reset=0
    _want_enabled=0
    _want_notify_disabled=0
    _want_sma_enabled=0
    _want_phone=0
    _want_apikey=0
    _want_ip=0
    _want_port=0
    _want_password=0
    _want_language=0
    _want_hero_source=0

    if body_has ui_language; then
        _language=$(body_str ui_language)
        case "$_language" in
            en|de) ;;
            *) fail "400 Bad Request" "$(msg language_invalid)" "ui_language" ;;
        esac
        _want_language=1
    fi

    if body_has hero_source; then
        _hero_source=$(body_str hero_source)
        case "$_hero_source" in
            auto|sma|ccu|shelly) ;;
            *) fail "400 Bad Request" "Invalid hero_source" "hero_source" ;;
        esac
        _want_hero_source=1
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

    if body_has notify_disabled; then
        _notify_disabled=$(body_bool notify_disabled)
        if [ -z "$_notify_disabled" ]; then
            fail "400 Bad Request" \
                "$(msg notify_disabled_bool)" \
                "notify_disabled"
        fi
        [ "$_notify_disabled" = "true" ] && _notify_disabled=1 || _notify_disabled=0
        _want_notify_disabled=1
    fi

    if body_has signal_phone; then
        _phone=$(trim_ws "$(body_str signal_phone)")
        case "$_phone" in
            *:*) _phone=$(restore_phone_list "$_phone" "$SIGNAL_PHONE") ;;
        esac
        if [ -n "$_phone" ] && ! is_signal_phone_list "$_phone"; then
            fail "400 Bad Request" \
                "$(msg phone_invalid)" \
                "signal_phone"
        fi
        _want_phone=1
    fi

    _key_field=
    if body_has signal_apikey; then
        _key_field=signal_apikey
    elif body_has signal_api_key; then
        _key_field=signal_api_key
    fi
    if [ -n "$_key_field" ]; then
        _newkey=$(trim_ws "$(body_str "$_key_field")")
        if [ -n "$_newkey" ]; then
            is_apikey "$_newkey" || fail "400 Bad Request" \
                "$(msg apikey_invalid)" \
                "$_key_field"
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
        case "$_phone" in
            *:*) ;;
            *)
                [ -n "$_apikey" ] || fail "400 Bad Request" \
                    "$(msg apikey_required)" \
                    "signal_apikey"
                ;;
        esac
    fi

    if body_has sma_enabled; then
        _sma_enabled_raw=$(body_bool sma_enabled) || fail "400 Bad Request" "Invalid sma_enabled" "sma_enabled"
        if [ "$_sma_enabled_raw" = "true" ]; then _sma_enabled=1; else _sma_enabled=0; fi
        _want_sma_enabled=1
    fi

    if body_has sma_ip; then
        _raw_ip=$(body_str sma_ip)
        _ip=$(normalize_host "$_raw_ip")
        if [ -n "$(trim_ws "$_raw_ip")" ] && [ -z "$_ip" ]; then
            fail "400 Bad Request" "$(msg ip_invalid)" "sma_ip"
        fi
        if [ -n "$_ip" ]; then
            is_safe_host "$_ip" || fail "400 Bad Request" \
                "$(msg ip_invalid)" "sma_ip"
        fi
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

    parse_and_validate_renault
    parse_and_validate_ccu_health
    parse_and_validate_weather
    parse_and_validate_forecast

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
    if [ "$_want_hero_source" = "1" ]; then
        env_set "$TMP_FILE" HERO_SOURCE "$_hero_source" || \
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
    if [ "$_want_notify_disabled" = "1" ]; then
        env_set "$TMP_FILE" NOTIFY_DISABLED "$_notify_disabled" || \
            fail "500 Internal Server Error" "$(msg write_notify_disabled)"
    fi
    if [ "$_want_phone" = "1" ]; then
        env_set "$TMP_FILE" SIGNAL_PHONE "$_phone" || \
            fail "500 Internal Server Error" "$(msg write_phone)"
    fi
    if [ "$_want_apikey" = "1" ]; then
        env_set "$TMP_FILE" SIGNAL_APIKEY "$_apikey" || \
            fail "500 Internal Server Error" "$(msg write_apikey)"
    fi
    if [ "$_want_sma_enabled" = "1" ]; then
        env_set "$TMP_FILE" SMA_ENABLED "$_sma_enabled" || \
            fail "500 Internal Server Error" "Failed to write SMA_ENABLED"
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

    if [ "$_want_language" = "1" ]; then
        mkdir -p "$ADDON_DIR/config" 2>/dev/null || true
        chmod 700 "$ADDON_DIR/config" 2>/dev/null || true
        if [ -f "$CONFIG_ENV_FILE" ]; then
            env_set "$CONFIG_ENV_FILE" UI_LANGUAGE "$_language" 2>/dev/null || true
        else
            (
                umask 077
                printf "UI_LANGUAGE='%s'\n" "$_language" > "$CONFIG_ENV_FILE.tmp.$$" 2>/dev/null \
                    && mv -f "$CONFIG_ENV_FILE.tmp.$$" "$CONFIG_ENV_FILE" 2>/dev/null
            ) || true
        fi
        chmod 600 "$CONFIG_ENV_FILE" 2>/dev/null || true
    fi

    if [ "$_want_notify_disabled" = "1" ]; then
        mkdir -p "$ADDON_DIR/config" 2>/dev/null || true
        chmod 700 "$ADDON_DIR/config" 2>/dev/null || true
        if [ -f "$CONFIG_ENV_FILE" ]; then
            env_set "$CONFIG_ENV_FILE" NOTIFY_DISABLED "$_notify_disabled" 2>/dev/null \
                || fail "500 Internal Server Error" "$(msg write_notify_disabled)"
        else
            _notify_tmp="$CONFIG_ENV_FILE.tmp.$$"
            (umask 077; printf "NOTIFY_DISABLED='%s'\n" "$_notify_disabled" > "$_notify_tmp") \
                || fail "500 Internal Server Error" "$(msg write_notify_disabled)"
            mv -f "$_notify_tmp" "$CONFIG_ENV_FILE" 2>/dev/null \
                || fail "500 Internal Server Error" "$(msg write_notify_disabled)"
        fi
        chmod 600 "$CONFIG_ENV_FILE" 2>/dev/null || true
    fi

    if [ "$_want_renault" = "1" ]; then
        save_renault_env "$_ruser" "$_rpass" "$_rvin" \
            "$_r_low_en_val" "$_r_low_soc_val" "$_r_high_en_val" "$_r_high_soc_val" \
            || fail "500 Internal Server Error" "$(msg write_renault)"
    fi

    if [ "$_want_ccu_health" = "1" ]; then
        save_ccu_health_env "$_c_dc_en_val" "$_c_dc_thresh_val" \
            || fail "500 Internal Server Error" "$(msg write_ccu_health)"
    fi

    if [ "$_want_weather" = "1" ]; then
        save_weather_env "$_w_city_val" "$_w_lat_val" "$_w_lon_val" \
            || fail "500 Internal Server Error" "$(msg write_weather)"
    fi

    if [ "$_want_forecast" = "1" ]; then
        save_forecast_env "$_f_kwp_val" "$_f_dec_val" "$_f_az_val" "$_f_lat_val" "$_f_lon_val" \
            || fail "500 Internal Server Error" "$(msg write_forecast)"
    fi

    _s_en_val=$SHELLY_ENABLED
    _s_ip_val=$SHELLY_IP
    _s_user_val=$SHELLY_USER
    _s_pass_val=$SHELLY_PASS
    _s_int_val=$SHELLY_INTERVAL
    _want_shelly=0
    if body_has shelly_enabled; then
        case "$(body_bool shelly_enabled)" in
            true|1) _s_en_val=true ;;
            *) _s_en_val=false ;;
        esac
        _want_shelly=1
    fi
    if body_has shelly_ip; then
        _raw_s_ip=$(body_str shelly_ip)
        _s_ip_val=$(normalize_host "$_raw_s_ip")
        if [ -n "$(trim_ws "$_raw_s_ip")" ] && [ -z "$_s_ip_val" ]; then
            fail "400 Bad Request" "$(msg shelly_ip_invalid)" "shelly_ip"
        fi
        if [ -n "$_s_ip_val" ]; then
            is_safe_host "$_s_ip_val" || fail "400 Bad Request" "$(msg shelly_ip_invalid)" "shelly_ip"
        fi
        _want_shelly=1
    fi
    if body_has shelly_user; then
        _s_user_val=$(trim_ws "$(body_str shelly_user)")
        if [ -n "$_s_user_val" ] && ! is_safe_label "$_s_user_val"; then
            fail "400 Bad Request" "$(msg password_invalid)" "shelly_user"
        fi
        _want_shelly=1
    fi
    if body_has shelly_pass; then
        _sp_new=$(body_str shelly_pass)
        if [ -n "$_sp_new" ] && ! is_masked_secret "$_sp_new"; then
            is_password "$_sp_new" || fail "400 Bad Request" "$(msg password_invalid)" "shelly_pass"
            _s_pass_val=$_sp_new
        fi
        _want_shelly=1
    fi
    if body_has shelly_interval; then
        _s_int_val=$(body_num shelly_interval)
        is_uint "$_s_int_val" && [ "$_s_int_val" -ge 10 ] || fail "400 Bad Request" "Invalid shelly_interval" "shelly_interval"
        _want_shelly=1
    fi
    _run_shelly=0
    if [ "$_want_shelly" = "1" ]; then
        save_shelly_env "$_s_en_val" "$_s_ip_val" "$_s_user_val" "$_s_pass_val" "$_s_int_val" \
            || fail "500 Internal Server Error" "$(msg write_failed)"
        if [ "$_s_en_val" = "true" ]; then
            sync_plugins_env PLUGIN_SHELLY_EM 1 || true
            rm -f "$ADDON_DIR/plugins.d/25_shelly_em.sh.disabled" 2>/dev/null || true
            _run_shelly=1
            if [ ! -f "$STATE_DIR/plugins/shelly_em.json" ]; then
                mkdir -p "$STATE_DIR/plugins" 2>/dev/null || true
                chmod 755 "$STATE_DIR/plugins" 2>/dev/null || true
                _sh_ts=$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || printf '')
                _sh_tmp="$STATE_DIR/plugins/shelly_em.json.tmp.$$"
                if [ -n "$_s_ip_val" ]; then
                    _sh_sec="[{\"label\":\"HOST\",\"value\":$(json_str "$_s_ip_val")},{\"label\":\"STATUS\",\"value\":\"Connecting...\"}]"
                else
                    _sh_sec='[{"label":"STATUS","value":"Enter IP"}]'
                fi
                printf '%s\n' "{\"id\":\"shelly_em\",\"layout\":\"stat\",\"title\":\"SHELLY EM\",\"status_badge\":\"SETUP\",\"status_color\":\"blue\",\"timestamp\":\"$_sh_ts\",\"primary\":{\"value\":\"SETUP\",\"unit\":\"\",\"label\":\"STATUS\"},\"secondary_metrics\":$_sh_sec}" > "$_sh_tmp" 2>/dev/null && {
                    chmod 644 "$_sh_tmp" 2>/dev/null || true
                    mv -f "$_sh_tmp" "$STATE_DIR/plugins/shelly_em.json" 2>/dev/null || rm -f "$_sh_tmp" 2>/dev/null || true
                } || rm -f "$_sh_tmp" 2>/dev/null || true
            fi
        else
            sync_plugins_env PLUGIN_SHELLY_EM 0 || true
            rm -f "$STATE_DIR/plugins/shelly_em.json" 2>/dev/null || true
            _run_shelly=1
        fi
    elif [ "$SHELLY_ENABLED" = "true" ] && [ ! -f "$STATE_DIR/plugins/shelly_em.json" ]; then
        sync_plugins_env PLUGIN_SHELLY_EM 1 || true
        rm -f "$ADDON_DIR/plugins.d/25_shelly_em.sh.disabled" 2>/dev/null || true
        _run_shelly=1
        mkdir -p "$STATE_DIR/plugins" 2>/dev/null || true
        chmod 755 "$STATE_DIR/plugins" 2>/dev/null || true
        _sh_ts=$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || printf '')
        _sh_tmp="$STATE_DIR/plugins/shelly_em.json.tmp.$$"
        if [ -n "$SHELLY_IP" ]; then
            _sh_sec="[{\"label\":\"HOST\",\"value\":$(json_str "$SHELLY_IP")},{\"label\":\"STATUS\",\"value\":\"Connecting...\"}]"
        else
            _sh_sec='[{"label":"STATUS","value":"Enter IP"}]'
        fi
        printf '%s\n' "{\"id\":\"shelly_em\",\"layout\":\"stat\",\"title\":\"SHELLY EM\",\"status_badge\":\"SETUP\",\"status_color\":\"blue\",\"timestamp\":\"$_sh_ts\",\"primary\":{\"value\":\"SETUP\",\"unit\":\"\",\"label\":\"STATUS\"},\"secondary_metrics\":$_sh_sec}" > "$_sh_tmp" 2>/dev/null && {
            chmod 644 "$_sh_tmp" 2>/dev/null || true
            mv -f "$_sh_tmp" "$STATE_DIR/plugins/shelly_em.json" 2>/dev/null || rm -f "$_sh_tmp" 2>/dev/null || true
        } || rm -f "$_sh_tmp" 2>/dev/null || true
    fi

    _t_en_val=$TARIFF_ENABLED
    _t_prov_val=$TARIFF_PROVIDER
    _t_tok_val=$TIBBER_TOKEN
    _t_sur_val=$TARIFF_SURCHARGE_CT
    _t_int_val=$TARIFF_INTERVAL
    _want_tariff=0
    if body_has tariff_enabled; then
        case "$(body_bool tariff_enabled)" in
            true|1) _t_en_val=true ;;
            *) _t_en_val=false ;;
        esac
        _want_tariff=1
    fi
    if body_has tariff_provider; then
        _t_prov_val=$(body_str tariff_provider)
        case "$_t_prov_val" in
            awattar_de|awattar_at|tibber) ;;
            *) fail "400 Bad Request" "Invalid tariff_provider" "tariff_provider" ;;
        esac
        _want_tariff=1
    fi
    if body_has tibber_token; then
        _tt_new=$(trim_ws "$(body_str tibber_token)")
        if [ -n "$_tt_new" ] && ! is_masked_secret "$_tt_new"; then
            is_apikey_token "$_tt_new" || fail "400 Bad Request" "$(msg apikey_invalid)" "tibber_token"
            _t_tok_val=$_tt_new
        fi
        _want_tariff=1
    fi
    if body_has tariff_surcharge_ct; then
        _t_sur_val=$(trim_ws "$(body_float tariff_surcharge_ct)")
        [ -n "$_t_sur_val" ] || _t_sur_val=0
        is_float_in_range "$_t_sur_val" -1000 1000 || fail "400 Bad Request" "Invalid tariff_surcharge_ct" "tariff_surcharge_ct"
        _want_tariff=1
    fi
    if body_has tariff_interval; then
        _t_int_val=$(body_num tariff_interval)
        is_uint "$_t_int_val" && [ "$_t_int_val" -ge 10 ] || fail "400 Bad Request" "Invalid tariff_interval" "tariff_interval"
        _want_tariff=1
    fi
    _run_tariff=0
    if [ "$_want_tariff" = "1" ]; then
        save_tibber_env "$_t_en_val" "$_t_prov_val" "$_t_tok_val" "$_t_sur_val" "$_t_int_val" \
            || fail "500 Internal Server Error" "$(msg write_failed)"
        if [ "$_t_en_val" = "true" ]; then
            sync_plugins_env PLUGIN_TIBBER 1 || true
            rm -f "$ADDON_DIR/plugins.d/35_tibber.sh.disabled" 2>/dev/null || true
            _run_tariff=1
        else
            sync_plugins_env PLUGIN_TIBBER 0 || true
            rm -f "$STATE_DIR/plugins/tibber.json" 2>/dev/null || true
            _run_tariff=1
        fi
    elif [ "$TARIFF_ENABLED" = "true" ] && [ ! -f "$STATE_DIR/plugins/tibber.json" ]; then
        sync_plugins_env PLUGIN_TIBBER 1 || true
        rm -f "$ADDON_DIR/plugins.d/35_tibber.sh.disabled" 2>/dev/null || true
        _run_tariff=1
    fi

    # Notification edge state belongs to the state machine, not to the
    # settings UI. A save must never delete, truncate or rewrite it; a lost
    # state file would re-arm alerts the moment the threshold is exceeded.
    mkdir -p "$STATE_DIR/notifications" 2>/dev/null || true
    chmod 700 "$STATE_DIR/notifications" 2>/dev/null || true

    release_config_lock
    if [ "$_want_weather" = "1" ] && [ -x "$ADDON_DIR/plugins.d/15_weather.sh" ]; then
        WEATHER_ADDON_DIR="$ADDON_DIR" WEATHER_ENV="$WEATHER_ENV_FILE" \
            "$ADDON_DIR/plugins.d/15_weather.sh" </dev/null >/dev/null 2>&1 &
    fi
    if [ "$_want_forecast" = "1" ] && [ -x "$ADDON_DIR/plugins.d/30_forecast_solar.sh" ]; then
        FORECAST_SOLAR_ADDON_DIR="$ADDON_DIR" FORECAST_SOLAR_ENV="$FORECAST_ENV_FILE" \
            "$ADDON_DIR/plugins.d/30_forecast_solar.sh" </dev/null >/dev/null 2>&1 &
    fi
    if [ "$_run_shelly" = "1" ] && [ -x "$ADDON_DIR/plugins.d/25_shelly_em.sh" ]; then
        SHELLY_ADDON_DIR="$ADDON_DIR" SHELLY_ENV="$SHELLY_ENV_FILE" \
            SHELLY_CONNECT_TIMEOUT=2 SHELLY_MAX_TIME=4 \
            "$ADDON_DIR/plugins.d/25_shelly_em.sh" </dev/null >/dev/null 2>&1 &
    fi
    if [ "$_run_tariff" = "1" ] && [ -x "$ADDON_DIR/plugins.d/35_tibber.sh" ]; then
        TIBBER_ADDON_DIR="$ADDON_DIR" TIBBER_ENV="$TIBBER_ENV_FILE" \
            "$ADDON_DIR/plugins.d/35_tibber.sh" </dev/null >/dev/null 2>&1 &
    fi
    if [ -x "$ADDON_DIR/bin/check_soc.sh" ]; then
        SENTINELLA_RUN_PLUGINS=1 "$ADDON_DIR/bin/check_soc.sh" </dev/null >/dev/null 2>&1 &
    fi
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

renault_test_lock_is_fresh() {
    [ -d "$RENAULT_TEST_LOCK" ] || return 1
    _started=$(sed -n '1p' "$RENAULT_TEST_LOCK/started" 2>/dev/null)
    is_uint "$_started" || return 1
    _now=$(date +%s 2>/dev/null)
    is_uint "$_now" || return 1
    [ "$_now" -ge "$_started" ] || return 0
    # The full auth chain can take up to four 15 s requests.
    [ $((_now - _started)) -lt 180 ]
}

start_test() {
    load_env || fail "500 Internal Server Error" \
        "$(msg config_missing)"

    [ -x "$NOTIFY_SCRIPT" ] || fail "500 Internal Server Error" \
        "$(msg notify_missing)"

    if [ -z "$SIGNAL_PHONE" ]; then
        fail "400 Bad Request" \
            "$(msg test_credentials)"
    fi
    case "$SIGNAL_PHONE" in
        *:*) ;;
        *)
            if [ -z "$SIGNAL_APIKEY" ]; then
                fail "400 Bad Request" \
                    "$(msg test_credentials)"
            fi
            ;;
    esac

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
    : > "$TEST_LOG" 2>/dev/null || true
    chmod 600 "$TEST_LOG" 2>/dev/null || true

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
# Renault Zoe connection test (synchronous dry-run + card refresh)
# ---------------------------------------------------------------------------
run_renault_test() {
    load_env 2>/dev/null || load_renault_env

    parse_and_validate_renault
    if [ "$_want_renault" = "1" ]; then
        acquire_config_lock || fail "409 Conflict" "$(msg config_busy)"
        save_renault_env "$_ruser" "$_rpass" "$_rvin" \
            || fail "500 Internal Server Error" "$(msg write_renault)"
        release_config_lock
        load_renault_env
    fi

    if [ -z "$RENAULT_USER" ] || [ -z "$RENAULT_PASS" ] || [ -z "$RENAULT_VIN" ]; then
        fail "400 Bad Request" "$(msg renault_test_credentials)"
    fi

    [ -x "$RENAULT_PLUGIN" ] || fail "500 Internal Server Error" \
        "$(msg renault_plugin_missing)"

    # Serialize the synchronous connection test. Without this, a burst of POSTs
    # spawns concurrent plugin runs that race on the session cache and can lock
    # the upstream Gigya account.
    if renault_test_lock_is_fresh; then
        fail "409 Conflict" "$(msg test_running)"
    fi
    rm -rf "$RENAULT_TEST_LOCK" 2>/dev/null || true
    mkdir -p "$STATE_DIR" 2>/dev/null \
        || fail "500 Internal Server Error" "$(msg state_dir)"
    mkdir "$RENAULT_TEST_LOCK" 2>/dev/null \
        || fail "500 Internal Server Error" "$(msg test_lock)"
    RENAULT_TEST_LOCK_OWNED=1
    printf '%s\n' "$(date +%s 2>/dev/null)" > "$RENAULT_TEST_LOCK/started" 2>/dev/null || true

    rm -f "$RENAULT_SESSION_FILE" 2>/dev/null || true

    if RENAULT_ADDON_DIR="$ADDON_DIR" RENAULT_ENV="$RENAULT_ENV_FILE" \
        "$RENAULT_PLUGIN" --test >/dev/null 2>&1; then
        RENAULT_TEST_LOCK_OWNED=0
        rm -rf "$RENAULT_TEST_LOCK" 2>/dev/null || true
        emit_json "200 OK" \
            "{\"ok\":true,\"status\":\"connected\",\"message\":$(json_str "$(msg renault_test_ok)")}"
    fi

    RENAULT_TEST_LOCK_OWNED=0
    rm -rf "$RENAULT_TEST_LOCK" 2>/dev/null || true
    fail "502 Bad Gateway" "$(msg renault_test_failed)"
}

report_renault_test() {
    load_renault_env
    if [ -f "$RENAULT_CARD_FILE" ]; then
        emit_json "200 OK" \
            "{\"ok\":true,\"status\":\"connected\",\"message\":$(json_str "$(msg renault_test_ok)")}"
    fi
    emit_json "200 OK" '{"ok":true,"status":"idle"}'
}

# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------
ACTION=${SMA_CGI_ACTION:-}
if [ -z "$ACTION" ]; then
    case "${PATH_INFO:-}" in
        */test_renault*) ACTION=test_renault ;;
        */test_signal*) ACTION=test_signal ;;
        */settings*|*/config*) ACTION=config ;;
    esac
fi
if [ -z "$ACTION" ]; then
    case "${SCRIPT_NAME##*/}" in
        test_renault.cgi|test_renault) ACTION=test_renault ;;
        test_signal.cgi|test_signal) ACTION=test_signal ;;
        *) ACTION=config ;;
    esac
fi

# Load the saved language before validating the request, so even early 400
# responses are rendered in the user's language.
load_env 2>/dev/null || true
harden_env_perms

case "$ACTION" in
    config|settings)
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
    test_renault)
        case "$REQUEST_METHOD" in
            GET|HEAD)
                report_renault_test
                ;;
            POST)
                check_origin
                read_body
                run_renault_test
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
