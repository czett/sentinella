#!/bin/sh
# =============================================================================
#  Sentinella - core engine
# =============================================================================
#  Reads the battery state of charge (SoC) from an SMA Sunny Tripower /
#  Sunny Boy Storage inverter through its local WebConnect JSON API, maps it
#  to a Homematic CCU3 system variable and drives a 95 % hysteresis signal.
#
#  Target : Homematic CCU3 / RaspberryMatic (Buildroot, BusyBox ash)
#  Needs  : /bin/sh, curl, sed, tr, cut, head, tail, date, logger (optional)
#  Install: <addon>/bin/check_soc.sh, mode 700, owner root
#  Config : <addon>/.env (mode 600) or <addon>/config.env (legacy)
#
#  Exit codes:
#    0  success (including "another instance is running")
#    1  runtime error - previous CCU3 values were kept
#    2  configuration error
#
#  The script never writes a partial, stale or out-of-range SoC. A failed
#  inverter read leaves both system variables untouched.
# =============================================================================

PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 077

DRY_RUN=0
if [ "${1:-}" = "--dry-run" ] || [ "${1:-}" = "-n" ]; then
    DRY_RUN=1
fi

# ---------------------------------------------------------------------------
# Paths and state
# ---------------------------------------------------------------------------
APP_DIR=$(cd "$(dirname "$0")/.." 2>/dev/null && pwd)
[ -n "$APP_DIR" ] || APP_DIR=/usr/local/addons/sentinella

ENV_FILE=${SMA_MONITOR_ENV:-}
if [ -z "$ENV_FILE" ]; then
    if [ -r "$APP_DIR/.env" ]; then
        ENV_FILE=$APP_DIR/.env
    elif [ -r "$APP_DIR/config/config.env" ]; then
        ENV_FILE=$APP_DIR/config/config.env
    elif [ -r "$APP_DIR/config.env" ]; then
        ENV_FILE=$APP_DIR/config.env
    elif [ "$DRY_RUN" = "1" ] && [ -r "$APP_DIR/.env.example" ]; then
        ENV_FILE=$APP_DIR/.env.example
    else
        ENV_FILE=$APP_DIR/.env
    fi
fi

STATE_DIR=$APP_DIR/state
LOG_FILE=
LOG_DIR=
SIGNAL_STATE_FILE=
SOC_STATE_FILE=
HISTORY_FILE=
LOCK_DIR=
NOTIFY_SCRIPT=$APP_DIR/lib/notify_signal.sh

SID=
LOCK_OWNED=0

# Runtime values shared with the status writer.
NOW=
SOC=
PREV_SOC=
DELTA=
CHARGE_POWER=
DISCHARGE_POWER=
CHARGE_STATE=unknown
SIGNAL=
PREV_SIGNAL=
SIGNAL_STATE=unknown
CCU_OK=0
MEASUREMENTS=

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log() {
    _ts=$(date '+%Y-%m-%d %H:%M:%S' 2>/dev/null)
    [ -n "$_ts" ] || _ts=-
    printf '%s %s\n' "$_ts" "$*" >&2
    if [ -n "$LOG_FILE" ] && [ -n "$LOG_DIR" ] && [ -d "$LOG_DIR" ]; then
        printf '%s %s\n' "$_ts" "$*" >> "$LOG_FILE" 2>/dev/null || true
        chmod 600 "$LOG_FILE" 2>/dev/null || true
    fi
    if command -v logger >/dev/null 2>&1; then
        logger -t sentinella "$*" 2>/dev/null || true
    fi
}

rotate_log() {
    [ -n "$LOG_FILE" ] || return 0
    [ -f "$LOG_FILE" ] || return 0
    _size=$(wc -c < "$LOG_FILE" 2>/dev/null | tr -d ' ')
    is_uint "$_size" || return 0
    if [ "$_size" -gt "$LOG_MAX_BYTES" ]; then
        mv -f "$LOG_FILE" "$LOG_FILE.1" 2>/dev/null || true
    fi
}

# ---------------------------------------------------------------------------
# Small validators (POSIX, no external tools)
# ---------------------------------------------------------------------------
is_uint() {
    case "$1" in
        ''|*[!0-9]*) return 1 ;;
    esac
    return 0
}

is_bool() {
    case "$1" in
        true|false) return 0 ;;
    esac
    return 1
}

is_number() {
    case "$1" in
        ''|*[!0-9.-]*) return 1 ;;
    esac
    case "$1" in
        -*)
            _num=${1#-}
            ;;
        *)
            _num=$1
            ;;
    esac
    case "$_num" in
        ''|.*|*.|*.*.*|*[!0-9.]*) return 1 ;;
    esac
    # Reject leading zeros so the value is always a valid JSON number.
    case "$_num" in
        0|0.*|[1-9]*) ;;
        *) return 1 ;;
    esac
    return 0
}

is_zero() {
    case "$1" in
        ''|0|0.0|0.00|0.000|-0|-0.0) return 0 ;;
    esac
    return 1
}

is_safe_name() {
    case "$1" in
        ''|*[!A-Za-z0-9_]*) return 1 ;;
    esac
    return 0
}

is_safe_host() {
    case "$1" in
        ''|*[!A-Za-z0-9._-]*) return 1 ;;
    esac
    return 0
}

trim_ws() {
    printf '%s' "${1:-}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'
}

json_escape() {
    printf '%s' "$1" | tr -d '\r\n' | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'
}

json_number_or_null() {
    if is_number "$1"; then
        printf '%s' "$1"
    else
        printf 'null'
    fi
}

json_bool_or_null() {
    case "$1" in
        1) printf 'true' ;;
        0) printf 'false' ;;
        *) printf 'null' ;;
    esac
}

# ---------------------------------------------------------------------------
# Cleanup: always release the SMA session and the lock, also on signals.
# ---------------------------------------------------------------------------
# shellcheck disable=SC2317,SC2329  # invoked indirectly through trap
cleanup() {
    _rc=$?
    trap - EXIT INT TERM HUP
    trap '' INT TERM HUP

    if [ -n "$SID" ]; then
        sma_logout
    fi
    if [ "$LOCK_OWNED" = "1" ] && [ -n "$LOCK_DIR" ]; then
        rm -rf "$LOCK_DIR"
    fi
    # Never leave half-written state behind when a signal or abort hits
    # between "write .tmp" and "mv".
    for _tmp in "${SIGNAL_STATE_FILE:-}" "${SOC_STATE_FILE:-}" "${HISTORY_FILE:-}" \
        "${WEB_STATUS_FILE:-}" "${_snapshot:-}"; do
        [ -n "$_tmp" ] && rm -f "$_tmp.tmp" 2>/dev/null
    done
    exit "$_rc"
}

trap cleanup EXIT
trap 'log "interrupted (INT)"; exit 130' INT
trap 'log "interrupted (TERM)"; exit 143' TERM
trap 'log "interrupted (HUP)"; exit 129' HUP

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
load_config() {
    if [ ! -r "$ENV_FILE" ]; then
        log "ERROR: configuration file $ENV_FILE is missing or unreadable"
        log "HINT: copy .env.example to .env and chmod 600 .env"
        return 2
    fi

    _mode=$(stat -c %a "$ENV_FILE" 2>/dev/null)
    if [ -n "$_mode" ] && [ "$_mode" != "600" ]; then
        log "WARNING: $ENV_FILE has mode $_mode, expected 600"
    fi
    _owner=$(stat -c %u "$ENV_FILE" 2>/dev/null)
    if [ -n "$_owner" ] && [ "$_owner" != "0" ]; then
        log "WARNING: $ENV_FILE is not owned by root (uid $_owner)"
    fi

    # shellcheck source=/dev/null
    if ! . "$ENV_FILE"; then
        log "ERROR: cannot parse $ENV_FILE"
        return 2
    fi

    # Inverter
    SMA_IP=${SMA_IP:-}
    SMA_PORT=${SMA_PORT:-443}
    SMA_ROLE=${SMA_ROLE:-usr}
    SMA_PASS=${SMA_PASS:-}
    BATTERY_SOC_KEY=${BATTERY_SOC_KEY:-6100_00295A00}
    BATTERY_CHARGE_POWER_KEY=${BATTERY_CHARGE_POWER_KEY:-}
    BATTERY_DISCHARGE_POWER_KEY=${BATTERY_DISCHARGE_POWER_KEY:-}
    SMA_TLS_VERIFY=${SMA_TLS_VERIFY:-false}
    SMA_CONNECT_TIMEOUT=${SMA_CONNECT_TIMEOUT:-5}
    SMA_MAX_TIME=${SMA_MAX_TIME:-15}
    SMA_RETRIES=${SMA_RETRIES:-2}
    SMA_RETRY_DELAY=${SMA_RETRY_DELAY:-5}

    # CCU
    CCU_IP=${CCU_IP:-127.0.0.1}
    CCU_REGA_PORT=${CCU_REGA_PORT:-8181}
    SV_SOC_NAME=${SV_SOC_NAME:-SV_Batterie_SoC}
    SV_SIGNAL_NAME=${SV_SIGNAL_NAME:-SV_Batterie_95_Signal}

    # Hysteresis
    THRESHOLD_TRIGGER=${THRESHOLD_TRIGGER:-95}
    THRESHOLD_RESET=${THRESHOLD_RESET:-90}

    # Notifications
    SIGNAL_ENABLED=${SIGNAL_ENABLED:-false}
    SIGNAL_PHONE=$(trim_ws "${SIGNAL_PHONE:-}")
    SIGNAL_APIKEY=$(trim_ws "${SIGNAL_APIKEY:-${SIGNAL_API_KEY:-}}")
    SIGNAL_API_URL=${SIGNAL_API_URL:-https://signal.callmebot.com/signal/send.php}
    SIGNAL_MESSAGE_OVERFLOW=${SIGNAL_MESSAGE_OVERFLOW:-}
    SIGNAL_MESSAGE_RESET=${SIGNAL_MESSAGE_RESET:-}
    SIGNAL_TEXT_TRIGGER=${SIGNAL_TEXT_TRIGGER:-}
    SIGNAL_TEXT_RESET=${SIGNAL_TEXT_RESET:-}
    SIGNAL_NOTIFY_ON_START=${SIGNAL_NOTIFY_ON_START:-false}
    SIGNAL_TIMEOUT=${SIGNAL_TIMEOUT:-15}
    SIGNAL_RETRIES=${SIGNAL_RETRIES:-2}
    SIGNAL_COOLDOWN=${SIGNAL_COOLDOWN:-3600}

    # Dashboard
    WEB_ENABLED=${WEB_ENABLED:-true}
    WEB_STATUS_FILE=${WEB_STATUS_FILE:-$STATE_DIR/status.json}
    WEB_HISTORY_POINTS=${WEB_HISTORY_POINTS:-288}

    # Language for the dashboard and Signal notifications. Unknown values fall
    # back to English so a typo cannot break the monitor or the alerts.
    case "${UI_LANGUAGE:-}" in
        de|DE|de_DE|de-DE) UI_LANGUAGE=de ;;
        *) UI_LANGUAGE=en ;;
    esac

    # Logging
    LOG_FILE=${LOG_FILE:-$STATE_DIR/check_soc.log}
    LOG_MAX_BYTES=${LOG_MAX_BYTES:-262144}

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    if ! is_safe_host "$SMA_IP"; then
        log "ERROR: SMA_IP must be an IPv4 address or hostname without scheme/path"
        return 2
    fi
    if ! is_uint "$SMA_PORT" || [ "$SMA_PORT" -lt 1 ] || [ "$SMA_PORT" -gt 65535 ]; then
        log "ERROR: SMA_PORT must be an integer between 1 and 65535"
        return 2
    fi
    case "$SMA_ROLE" in
        usr|user) ;;
        *) log "ERROR: SMA_ROLE must be 'usr' or 'user'"; return 2 ;;
    esac
    if [ -z "$SMA_PASS" ]; then
        log "ERROR: SMA_PASS must be set in $ENV_FILE"
        return 2
    fi
    if ! is_safe_name "$BATTERY_SOC_KEY"; then
        log "ERROR: BATTERY_SOC_KEY contains unsupported characters"
        return 2
    fi
    if [ -n "$BATTERY_CHARGE_POWER_KEY" ] && ! is_safe_name "$BATTERY_CHARGE_POWER_KEY"; then
        log "ERROR: BATTERY_CHARGE_POWER_KEY contains unsupported characters"
        return 2
    fi
    if [ -n "$BATTERY_DISCHARGE_POWER_KEY" ] && ! is_safe_name "$BATTERY_DISCHARGE_POWER_KEY"; then
        log "ERROR: BATTERY_DISCHARGE_POWER_KEY contains unsupported characters"
        return 2
    fi
    if ! is_bool "$SMA_TLS_VERIFY"; then
        log "ERROR: SMA_TLS_VERIFY must be 'true' or 'false'"
        return 2
    fi

    if ! is_safe_host "$CCU_IP"; then
        log "ERROR: CCU_IP must be an IPv4 address or hostname without scheme/path"
        return 2
    fi
    if ! is_uint "$CCU_REGA_PORT" || [ "$CCU_REGA_PORT" -lt 1 ] || [ "$CCU_REGA_PORT" -gt 65535 ]; then
        log "ERROR: CCU_REGA_PORT must be an integer between 1 and 65535"
        return 2
    fi
    if ! is_safe_name "$SV_SOC_NAME"; then
        log "ERROR: SV_SOC_NAME contains unsupported characters"
        return 2
    fi
    if ! is_safe_name "$SV_SIGNAL_NAME"; then
        log "ERROR: SV_SIGNAL_NAME contains unsupported characters"
        return 2
    fi

    if ! is_uint "$THRESHOLD_TRIGGER" || ! is_uint "$THRESHOLD_RESET"; then
        log "ERROR: THRESHOLD_TRIGGER and THRESHOLD_RESET must be integers"
        return 2
    fi
    if [ "$THRESHOLD_TRIGGER" -gt 100 ] || [ "$THRESHOLD_RESET" -lt 0 ]; then
        log "ERROR: thresholds must be within 0..100"
        return 2
    fi
    if [ "$THRESHOLD_TRIGGER" -le "$THRESHOLD_RESET" ]; then
        log "ERROR: THRESHOLD_TRIGGER ($THRESHOLD_TRIGGER) must be greater than THRESHOLD_RESET ($THRESHOLD_RESET)"
        return 2
    fi

    if ! is_bool "$SIGNAL_ENABLED"; then
        log "ERROR: SIGNAL_ENABLED must be 'true' or 'false'"
        return 2
    fi
    if [ "$SIGNAL_ENABLED" = "true" ]; then
        if [ -z "$SIGNAL_PHONE" ]; then
            log "ERROR: SIGNAL_PHONE and SIGNAL_APIKEY are required when SIGNAL_ENABLED=true"
            return 2
        fi
        case "$SIGNAL_PHONE" in
            *:*) ;;
            *)
                if [ -z "$SIGNAL_APIKEY" ]; then
                    log "ERROR: SIGNAL_PHONE and SIGNAL_APIKEY are required when SIGNAL_ENABLED=true"
                    return 2
                fi
                ;;
        esac
        case "$SIGNAL_API_URL" in
            http://*|https://*) ;;
            *) log "ERROR: SIGNAL_API_URL must start with http:// or https://"; return 2 ;;
        esac
    fi
    if ! is_bool "$SIGNAL_NOTIFY_ON_START"; then
        log "ERROR: SIGNAL_NOTIFY_ON_START must be 'true' or 'false'"
        return 2
    fi

    if ! is_bool "$WEB_ENABLED"; then
        log "ERROR: WEB_ENABLED must be 'true' or 'false'"
        return 2
    fi
    if [ "$WEB_ENABLED" = "true" ]; then
        case "$WEB_STATUS_FILE" in
            /*) ;;
            *) log "ERROR: WEB_STATUS_FILE must be an absolute path"; return 2 ;;
        esac
    fi
    if ! is_uint "$WEB_HISTORY_POINTS" || [ "$WEB_HISTORY_POINTS" -lt 2 ]; then
        log "ERROR: WEB_HISTORY_POINTS must be an integer >= 2"
        return 2
    fi
    if [ "$WEB_HISTORY_POINTS" -gt 2000 ]; then
        WEB_HISTORY_POINTS=2000
    fi

    # Timeouts / retries
    if ! is_uint "$SMA_CONNECT_TIMEOUT" || [ "$SMA_CONNECT_TIMEOUT" -lt 1 ]; then
        SMA_CONNECT_TIMEOUT=5
    fi
    if ! is_uint "$SMA_MAX_TIME" || [ "$SMA_MAX_TIME" -lt 1 ]; then
        SMA_MAX_TIME=15
    fi
    if ! is_uint "$SMA_RETRIES" || [ "$SMA_RETRIES" -lt 1 ]; then
        SMA_RETRIES=2
    fi
    if ! is_uint "$SMA_RETRY_DELAY"; then
        SMA_RETRY_DELAY=5
    fi
    if ! is_uint "$SIGNAL_TIMEOUT" || [ "$SIGNAL_TIMEOUT" -lt 1 ]; then
        SIGNAL_TIMEOUT=15
    fi
    if ! is_uint "$SIGNAL_RETRIES" || [ "$SIGNAL_RETRIES" -lt 1 ]; then
        SIGNAL_RETRIES=2
    fi
    if ! is_uint "$SIGNAL_COOLDOWN"; then
        SIGNAL_COOLDOWN=3600
    fi
    if ! is_uint "$LOG_MAX_BYTES" || [ "$LOG_MAX_BYTES" -lt 1024 ]; then
        LOG_MAX_BYTES=262144
    fi

    # Derived values
    STATE_DIR=${STATE_DIR:-$APP_DIR/state}
    LOG_DIR=$(dirname "$LOG_FILE" 2>/dev/null)
    SIGNAL_STATE_FILE=$STATE_DIR/signal
    SOC_STATE_FILE=$STATE_DIR/last_soc
    HISTORY_FILE=$STATE_DIR/history.csv
    LOCK_DIR=$STATE_DIR/.lock
    CCU_REGA_URL="http://$CCU_IP:$CCU_REGA_PORT/tclrega.exe"

    return 0
}

# ---------------------------------------------------------------------------
# Single-instance lock (survives overlapping cron runs, cleans up stale locks)
# ---------------------------------------------------------------------------
lock_write_pid() {
    if printf '%s\n' "$$" > "$LOCK_DIR/pid" 2>/dev/null; then
        LOCK_OWNED=1
        return 0
    fi
    rm -rf "$LOCK_DIR" 2>/dev/null || true
    log "ERROR: cannot write lock PID file in $LOCK_DIR"
    return 1
}

acquire_lock() {
    mkdir -p "$STATE_DIR" 2>/dev/null || true
    chmod 700 "$STATE_DIR" 2>/dev/null || true
    if [ -n "$LOG_DIR" ] && [ ! -d "$LOG_DIR" ]; then
        mkdir -p "$LOG_DIR" 2>/dev/null || true
    fi

    if mkdir "$LOCK_DIR" 2>/dev/null; then
        lock_write_pid || return 1
        return 0
    fi

    _oldpid=$(cat "$LOCK_DIR/pid" 2>/dev/null)
    if [ -z "$_oldpid" ] || ! is_uint "$_oldpid"; then
        # The owner may have just created the directory and not yet written
        # its PID. Give it one second before treating the lock as stale.
        sleep 1
        _oldpid=$(cat "$LOCK_DIR/pid" 2>/dev/null)
    fi
    if [ -n "$_oldpid" ] && is_uint "$_oldpid" && kill -0 "$_oldpid" 2>/dev/null; then
        log "another instance is already running (pid $_oldpid), exiting"
        return 1
    fi

    log "removing stale lock"
    rm -rf "$LOCK_DIR" 2>/dev/null || true
    if mkdir "$LOCK_DIR" 2>/dev/null; then
        lock_write_pid || return 1
        return 0
    fi

    log "ERROR: cannot create lock directory $LOCK_DIR"
    return 1
}

# ---------------------------------------------------------------------------
# SMA JSON API (WebConnect)
# ---------------------------------------------------------------------------
sma_curl() {
    if [ "$SMA_TLS_VERIFY" = "false" ]; then
        curl -sS -k --connect-timeout "$SMA_CONNECT_TIMEOUT" -m "$SMA_MAX_TIME" "$@"
    else
        curl -sS --connect-timeout "$SMA_CONNECT_TIMEOUT" -m "$SMA_MAX_TIME" "$@"
    fi
}

# POST a SID-bearing request. The session ID is a credential, so both the URL
# (?sid=) and the body travel through a curl config on stdin and never show up
# in /proc/<pid>/cmdline. SID is restricted to [A-Za-z0-9._-] by sma_login.
sma_post_sid() {
    _cfg_body=$(printf '%s' "$3" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')
    {
        printf 'url = "https://%s:%s/dyn/%s?sid=%s"\n' "$SMA_IP" "$SMA_PORT" "$1" "$2"
        printf 'data-binary = "%s"\n' "$_cfg_body"
    } | sma_curl -X POST -H 'Content-Type: application/json' --config -
}

sma_login() {
    # Escape backslash and double quote for the JSON string.
    _pw=$(printf '%s' "$SMA_PASS" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')
    _body='{"right":"'"$SMA_ROLE"'","pass":"'"$_pw"'"}'

    _resp=$(printf '%s' "$_body" \
        | sma_curl -X POST -H 'Content-Type: application/json' \
            --data-binary @- \
            "https://$SMA_IP:$SMA_PORT/dyn/login.json") || return 1

    SID=$(printf '%s' "$_resp" | tr -d '\r\n' \
        | sed -n 's/.*"sid"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
        | head -n 1)

    case "$SID" in
        ''|*[!A-Za-z0-9._-]*)
            SID=
            log "login failed: $(printf '%s' "$_resp" | tr -d '\r\n' | cut -c1-160)"
            return 1
            ;;
    esac
    return 0
}

sma_logout() {
    [ -n "$SID" ] || return 0
    _sid=$SID
    SID=                         # never try twice, even if this call is killed
    _body='{"destDev":[],"sid":"'"$_sid"'"}'
    if ! sma_post_sid "logout.json" "$_sid" "$_body" >/dev/null 2>&1; then
        log "WARNING: SMA logout failed; the session may expire on its own"
    fi
}

sma_get_value() {
    _key=$1
    _type=$2
    _quiet=${3:-0}

    _body='{"destDev":[],"keys":["'"$_key"'"],"sid":"'"$SID"'"}'
    _resp=$(sma_post_sid "getValues.json" "$SID" "$_body") || {
        [ "$_quiet" = "1" ] || log "getValues request failed for $_key"
        return 1
    }

    case "$_resp" in
        *"\"$_key\""*) ;;
        *)
            [ "$_quiet" = "1" ] || log "unexpected getValues response for $_key: $(printf '%s' "$_resp" | tr -d '\r\n' | cut -c1-160)"
            return 1
            ;;
    esac

    _val=$(printf '%s' "$_resp" | tr ',' '\n' \
        | sed -n 's/.*"val"[[:space:]]*:[[:space:]]*\(-\{0,1\}[0-9][0-9]*\(\.[0-9][0-9]*\)\{0,1\}\).*/\1/p' \
        | head -n 1)

    if [ -z "$_val" ]; then
        [ "$_quiet" = "1" ] || log "no numeric value for $_key (battery absent or value null)"
        return 1
    fi

    if [ "$_type" = "integer" ] && ! is_uint "$_val"; then
        [ "$_quiet" = "1" ] || log "value for $_key is not an integer: $_val"
        return 1
    fi
    if [ "$_type" = "number" ] && ! is_number "$_val"; then
        [ "$_quiet" = "1" ] || log "value for $_key is not numeric: $_val"
        return 1
    fi

    printf '%s\n' "$_val"
    return 0
}

# Login, read SoC and optional power values, logout. Stores "soc|charge|discharge"
# in MEASUREMENTS. It must run in the main shell (not in a command substitution)
# so that SID is visible to the cleanup trap if a signal arrives.
fetch_measurements() {
    _attempt=1
    while [ "$_attempt" -le "$SMA_RETRIES" ]; do
        if sma_login; then
            if _soc=$(sma_get_value "$BATTERY_SOC_KEY" integer); then
                _charge=
                _discharge=
                if [ -n "$BATTERY_CHARGE_POWER_KEY" ]; then
                    _charge=$(sma_get_value "$BATTERY_CHARGE_POWER_KEY" number 1) || _charge=
                fi
                if [ -n "$BATTERY_DISCHARGE_POWER_KEY" ]; then
                    _discharge=$(sma_get_value "$BATTERY_DISCHARGE_POWER_KEY" number 1) || _discharge=
                fi
                sma_logout
                MEASUREMENTS="$_soc|$_charge|$_discharge"
                return 0
            fi
        fi
        sma_logout
        if [ "$_attempt" -lt "$SMA_RETRIES" ]; then
            sleep "$SMA_RETRY_DELAY"
        fi
        _attempt=$((_attempt + 1))
    done
    return 1
}

# ---------------------------------------------------------------------------
# Hysteresis: on at >= TRIGGER, off at <= RESET, hold in between.
# An empty result means "state unknown" - the boolean is left untouched so a
# lost state file can never cause a premature reset.
# ---------------------------------------------------------------------------
read_prev_signal() {
    _prev=
    if [ -r "$SIGNAL_STATE_FILE" ]; then
        _prev=$(cat "$SIGNAL_STATE_FILE" 2>/dev/null)
    fi
    case "$_prev" in
        0|1) printf '%s\n' "$_prev" ;;
        *)   printf '\n' ;;
    esac
}

compute_signal() {
    _soc=$1
    _prev=$2

    if [ "$_soc" -ge "$THRESHOLD_TRIGGER" ]; then
        printf '1\n'
    elif [ "$_soc" -le "$THRESHOLD_RESET" ]; then
        printf '0\n'
    elif [ "$_prev" = "1" ] || [ "$_prev" = "0" ]; then
        printf '%s\n' "$_prev"
    else
        printf '\n'
    fi
}

derive_signal_state() {
    case "$SIGNAL" in
        1)
            if [ "$PREV_SIGNAL" = "1" ]; then
                printf 'held\n'
            else
                printf 'triggered\n'
            fi
            ;;
        0)
            if [ "$PREV_SIGNAL" = "0" ]; then
                printf 'off\n'
            else
                printf 'reset\n'
            fi
            ;;
        *)
            printf 'unknown\n'
            ;;
    esac
}

# ---------------------------------------------------------------------------
# CCU3 write via ReGaHSS (tclrega.exe). The body is the raw ReGa script.
# ---------------------------------------------------------------------------
write_ccu() {
    _soc=$1
    _sig=$2

    _rega='var s=dom.GetObject("'"$SV_SOC_NAME"'");if(s){s.State('"$_soc"');}'

    if [ "$_sig" = "1" ]; then
        _rega="${_rega}"'var b=dom.GetObject("'"$SV_SIGNAL_NAME"'");if(b){b.State(true);}'
    elif [ "$_sig" = "0" ]; then
        _rega="${_rega}"'var b=dom.GetObject("'"$SV_SIGNAL_NAME"'");if(b){b.State(false);}'
    fi

    _rega="${_rega}"'WriteLine("SMA_SOC_OK");'

    _resp=$(curl -fsS --connect-timeout 5 -m 15 \
        -H 'Content-Type: application/x-www-form-urlencoded' \
        --data-binary "$_rega" \
        "$CCU_REGA_URL") || {
        log "ERROR: CCU3 ReGa request failed ($CCU_REGA_URL)"
        return 1
    }

    case "$_resp" in
        *SMA_SOC_OK*) ;;
        *)
            # Some firmware versions execute the script but swallow WriteLine().
            log "WARNING: CCU3 did not return the confirmation marker: $(printf '%s' "$_resp" | tr -d '\r\n' | cut -c1-160)"
            ;;
    esac
    return 0
}

# ---------------------------------------------------------------------------
# State persistence and dashboard snapshot
# ---------------------------------------------------------------------------
persist_signal() {
    _sig=$1
    if printf '%s\n' "$_sig" > "$SIGNAL_STATE_FILE.tmp" 2>/dev/null \
        && mv -f "$SIGNAL_STATE_FILE.tmp" "$SIGNAL_STATE_FILE" 2>/dev/null; then
        chmod 600 "$SIGNAL_STATE_FILE" 2>/dev/null || true
        return 0
    fi
    log "WARNING: cannot persist hysteresis state to $SIGNAL_STATE_FILE"
    return 1
}

persist_last_soc() {
    _soc=$1
    if printf '%s\n' "$_soc" > "$SOC_STATE_FILE.tmp" 2>/dev/null \
        && mv -f "$SOC_STATE_FILE.tmp" "$SOC_STATE_FILE" 2>/dev/null; then
        chmod 600 "$SOC_STATE_FILE" 2>/dev/null || true
    fi
}

read_last_soc() {
    _prev=
    if [ -r "$SOC_STATE_FILE" ]; then
        _prev=$(cat "$SOC_STATE_FILE" 2>/dev/null)
    fi
    if is_uint "$_prev" && [ "$_prev" -ge 0 ] && [ "$_prev" -le 100 ]; then
        printf '%s\n' "$_prev"
    else
        printf '\n'
    fi
}

append_history() {
    _soc=$1
    if ! printf '%s,%s\n' "$NOW" "$_soc" >> "$HISTORY_FILE" 2>/dev/null; then
        log "WARNING: cannot append to history file $HISTORY_FILE"
        return 0
    fi
    chmod 600 "$HISTORY_FILE" 2>/dev/null || true
    _lines=$(wc -l < "$HISTORY_FILE" 2>/dev/null | tr -d ' ')
    if is_uint "$_lines" && [ "$_lines" -gt "$WEB_HISTORY_POINTS" ]; then
        if tail -n "$WEB_HISTORY_POINTS" "$HISTORY_FILE" > "$HISTORY_FILE.tmp" 2>/dev/null; then
            mv -f "$HISTORY_FILE.tmp" "$HISTORY_FILE" 2>/dev/null || true
            chmod 600 "$HISTORY_FILE" 2>/dev/null || true
        fi
    fi
}

history_json() {
    _json=
    [ -r "$HISTORY_FILE" ] || {
        printf '[]'
        return 0
    }

    while IFS=, read -r _h_t _h_s; do
        is_uint "$_h_t" || continue
        is_uint "$_h_s" || continue
        [ "$_h_s" -ge 0 ] || continue
        [ "$_h_s" -le 100 ] || continue
        if [ -n "$_json" ]; then
            _json="$_json,"
        fi
        _json="$_json{\"t\":$_h_t,\"soc\":$_h_s}"
    done < "$HISTORY_FILE"

    printf '[%s]' "$_json"
}

determine_charge_state() {
    if [ -n "$CHARGE_POWER" ] && ! is_zero "$CHARGE_POWER"; then
        printf 'charging\n'
        return 0
    fi
    if [ -n "$DISCHARGE_POWER" ] && ! is_zero "$DISCHARGE_POWER"; then
        printf 'discharging\n'
        return 0
    fi
    if [ -n "$CHARGE_POWER" ] || [ -n "$DISCHARGE_POWER" ]; then
        if [ -n "$PREV_SOC" ]; then
            if [ "$SOC" -gt "$PREV_SOC" ]; then
                printf 'charging\n'
            elif [ "$SOC" -lt "$PREV_SOC" ]; then
                printf 'discharging\n'
            else
                printf 'idle\n'
            fi
        else
            printf 'idle\n'
        fi
        return 0
    fi

    # No power channels configured: fall back to the SoC trend.
    if [ -z "$PREV_SOC" ]; then
        printf 'unknown\n'
    elif [ "$SOC" -gt "$PREV_SOC" ]; then
        printf 'charging\n'
    elif [ "$SOC" -lt "$PREV_SOC" ]; then
        printf 'discharging\n'
    else
        printf 'idle\n'
    fi
}

status_json() {
    cat <<EOF
{"schema":1,"ui_language":"$UI_LANGUAGE","generated_at":"$_j_generated","generated_epoch":$NOW,"host":"$_j_host","soc":$SOC,"previous_soc":$_j_prev,"delta":$_j_delta,"charging":"$CHARGE_STATE","charge_power_w":$_j_charge,"discharge_power_w":$_j_discharge,"signal":$_j_signal,"signal_state":"$SIGNAL_STATE","signal_notifications":$_j_notify,"signal_gateway":"$_j_gateway","thresholds":{"trigger":$THRESHOLD_TRIGGER,"reset":$THRESHOLD_RESET},"variables":{"soc":"$SV_SOC_NAME","signal":"$SV_SIGNAL_NAME"},"ccu_status":"$_j_ccu","history":$_j_history}
EOF
}

write_status() {
    _j_prev=$(json_number_or_null "$PREV_SOC")
    _j_delta=$(json_number_or_null "$DELTA")
    _j_charge=$(json_number_or_null "$CHARGE_POWER")
    _j_discharge=$(json_number_or_null "$DISCHARGE_POWER")
    _j_signal=$(json_bool_or_null "$SIGNAL")
    _j_notify=false
    [ "$SIGNAL_ENABLED" = "true" ] && _j_notify=true
    _j_gateway="Deaktiviert"
    if [ -n "$SIGNAL_PHONE" ]; then
        case "$SIGNAL_PHONE" in
            *:*) _j_gateway="CallMeBot (aktiv)" ;;
            *)
                [ -n "$SIGNAL_APIKEY" ] && _j_gateway="CallMeBot (aktiv)"
                ;;
        esac
    fi
    _j_history=$(history_json)
    _j_host=$(json_escape "$SMA_IP")
    _j_generated=$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null)
    _j_ccu=error
    [ "$CCU_OK" = "1" ] && _j_ccu=ok

    # Always keep a private snapshot for debugging.
    _snapshot=$STATE_DIR/status.json
    if status_json > "$_snapshot.tmp" 2>/dev/null \
        && mv -f "$_snapshot.tmp" "$_snapshot" 2>/dev/null; then
        chmod 600 "$_snapshot" 2>/dev/null || true
    fi

    [ "$WEB_ENABLED" = "true" ] || return 0
    [ -n "$WEB_STATUS_FILE" ] || return 0

    _web_dir=$(dirname "$WEB_STATUS_FILE" 2>/dev/null)
    _web_created=0
    if [ -n "$_web_dir" ] && [ ! -d "$_web_dir" ]; then
        if mkdir -p "$_web_dir" 2>/dev/null; then
            _web_created=1
        fi
    fi
    if [ -n "$_web_dir" ] && [ -d "$_web_dir" ]; then
        # lighttpd must be able to traverse and read a directory we created,
        # but never change the mode of an existing user directory.
        if [ "$_web_created" = "1" ]; then
            chmod 755 "$_web_dir" 2>/dev/null || true
        fi
        if status_json > "$WEB_STATUS_FILE.tmp" 2>/dev/null \
            && chmod 644 "$WEB_STATUS_FILE.tmp" 2>/dev/null \
            && mv -f "$WEB_STATUS_FILE.tmp" "$WEB_STATUS_FILE" 2>/dev/null; then
            return 0
        fi
    fi
    log "WARNING: cannot publish dashboard status to $WEB_STATUS_FILE"
    return 0
}

# ---------------------------------------------------------------------------
# Notification hook - detached, never blocks the cron run
# ---------------------------------------------------------------------------
notify_event() {
    _event=$1
    _soc=$2

    [ "$SIGNAL_ENABLED" = "true" ] || return 0
    [ -x "$NOTIFY_SCRIPT" ] || {
        log "WARNING: notification script $NOTIFY_SCRIPT is missing or not executable"
        return 0
    }

    _out=/dev/null
    if [ -n "$LOG_FILE" ] && [ -n "$LOG_DIR" ] && [ -d "$LOG_DIR" ] && [ -w "$LOG_DIR" ]; then
        _out=$LOG_FILE
    fi

    # The notification runs in its own subshell. setsid/nohup detach it from
    # the cron process group, so curl retries and timeouts can never delay the
    # next measurement. The cooldown itself is enforced in notify_signal.sh.
    if command -v setsid >/dev/null 2>&1 && command -v nohup >/dev/null 2>&1; then
        ( SMA_MONITOR_ENV="$ENV_FILE" setsid nohup "$NOTIFY_SCRIPT" "$_event" "$_soc" >>"$_out" 2>&1 & )
    elif command -v nohup >/dev/null 2>&1; then
        ( SMA_MONITOR_ENV="$ENV_FILE" nohup "$NOTIFY_SCRIPT" "$_event" "$_soc" >>"$_out" 2>&1 & )
    elif command -v setsid >/dev/null 2>&1; then
        ( SMA_MONITOR_ENV="$ENV_FILE" setsid "$NOTIFY_SCRIPT" "$_event" "$_soc" >>"$_out" 2>&1 & )
    else
        ( SMA_MONITOR_ENV="$ENV_FILE" "$NOTIFY_SCRIPT" "$_event" "$_soc" >>"$_out" 2>&1 & )
    fi
    log "queued Signal notification ($_event, SoC ${_soc}%)"
    return 0
}

# ---------------------------------------------------------------------------
# Modular plugins runner (executes enabled plugins in plugins.d/*.sh)
# ---------------------------------------------------------------------------
run_plugins() {
    [ "${SENTINELLA_RUN_PLUGINS:-0}" = "1" ] || return 0
    _plugins_dir=$APP_DIR/plugins.d
    [ -d "$_plugins_dir" ] || return 0
    for _plugin in "$_plugins_dir"/*.sh; do
        [ -f "$_plugin" ] || continue
        [ ! -L "$_plugin" ] || continue
        [ -x "$_plugin" ] || continue
        SMA_MONITOR_ADDON_DIR="$APP_DIR" \
        SMA_MONITOR_ENV="$ENV_FILE" \
            "$_plugin" >/dev/null 2>&1 || true
    done
    return 0
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    load_config || exit $?

    NOW=$(date +%s 2>/dev/null)
    is_uint "$NOW" || NOW=0

    if [ "$DRY_RUN" = "1" ]; then
        SOC=85
        PREV_SOC=84
        DELTA=1
        CHARGE_POWER=1200
        DISCHARGE_POWER=0
        PREV_SIGNAL=0
        SIGNAL=$(compute_signal "$SOC" "$PREV_SIGNAL")
        SIGNAL_STATE=$(derive_signal_state)
        CHARGE_STATE=$(determine_charge_state)
        exit 0
    fi

    rotate_log
    acquire_lock || exit 0

    PREV_SOC=$(read_last_soc)

    if ! fetch_measurements; then
        log "could not read SoC from $SMA_IP, keeping last CCU3 values"
        exit 1
    fi

    # Split "soc|charge|discharge" without word splitting surprises.
    IFS='|' read -r SOC CHARGE_POWER DISCHARGE_POWER <<EOF
$MEASUREMENTS
EOF

    if ! is_uint "$SOC" || [ "$SOC" -lt 0 ] || [ "$SOC" -gt 100 ]; then
        log "invalid SoC '$SOC', keeping last CCU3 values"
        exit 1
    fi
    # Normalise leading zeros ("088" -> "88") for ReGa and JSON.
    SOC=$(printf '%s' "$SOC" | sed 's/^0*//')
    [ -n "$SOC" ] || SOC=0
    is_number "$CHARGE_POWER" || CHARGE_POWER=
    is_number "$DISCHARGE_POWER" || DISCHARGE_POWER=

    if [ -n "$PREV_SOC" ]; then
        DELTA=$((SOC - PREV_SOC))
    fi

    append_history "$SOC"
    persist_last_soc "$SOC"

    PREV_SIGNAL=$(read_prev_signal)
    SIGNAL=$(compute_signal "$SOC" "$PREV_SIGNAL")
    SIGNAL_STATE=$(derive_signal_state)
    CHARGE_STATE=$(determine_charge_state)

    if write_ccu "$SOC" "$SIGNAL"; then
        CCU_OK=1
    else
        log "CCU3 update failed, keeping previous signal state"
    fi

    if [ "$CCU_OK" = "1" ] && [ -n "$SIGNAL" ]; then
        persist_signal "$SIGNAL" || true
    fi

    write_status
    run_plugins

    if [ "$CCU_OK" = "1" ]; then
        if [ -n "$PREV_SIGNAL" ] && [ "$SIGNAL" != "$PREV_SIGNAL" ]; then
            case "$SIGNAL" in
                1) notify_event trigger "$SOC" ;;
                0) notify_event reset "$SOC" ;;
            esac
        elif [ -z "$PREV_SIGNAL" ] && [ "$SIGNAL" = "1" ] && [ "$SIGNAL_NOTIFY_ON_START" = "true" ]; then
            notify_event trigger "$SOC"
        fi
    fi

    case "$SIGNAL" in
        0|1) log "SoC=${SOC}% signal=$SIGNAL (${SIGNAL_STATE}, ${CHARGE_STATE})" ;;
        *)   log "SoC=${SOC}% signal=unchanged (no valid hysteresis state yet)" ;;
    esac

    [ "$CCU_OK" = "1" ] || exit 1
    exit 0
}

main
