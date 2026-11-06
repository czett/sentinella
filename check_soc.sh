#!/bin/sh
#
# check_soc.sh - read battery SoC from an SMA STP inverter and push it to
#                Homematic CCU3 system variables.
#
# Target : Homematic CCU3 / RaspberryMatic, Buildroot + BusyBox ash
# Needs  : curl, sed, logger (all part of a standard CCU3 image)
# Install: /usr/local/addons/sma_soc/check_soc.sh  (mode 700, owner root)
# Config : /usr/local/addons/sma_soc/config.env    (mode 600, owner root)
#
# Exit codes: 0 = success, 1 = runtime error, 2 = configuration error.
#             A stale SoC never overwrites the last good CCU value.

PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 077

ADDON_DIR=/usr/local/addons/sma_soc
CONFIG_FILE=$ADDON_DIR/config.env
STATE_FILE=$ADDON_DIR/state.signal
LOCK_DIR=$ADDON_DIR/.lock

SID=""
LOCK_OWNED=0

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log() {
    printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2
    if command -v logger >/dev/null 2>&1; then
        logger -t sma_soc "$*" 2>/dev/null || true
    fi
}

# ---------------------------------------------------------------------------
# Cleanup: always release the SMA session and the lock, also on signals.
# ---------------------------------------------------------------------------
cleanup() {
    _rc=$?
    trap - EXIT INT TERM HUP

    if [ -n "$SID" ]; then
        sma_logout
    fi
    if [ "$LOCK_OWNED" = "1" ]; then
        rm -rf "$LOCK_DIR"
    fi
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
    if [ ! -r "$CONFIG_FILE" ]; then
        log "ERROR: configuration file $CONFIG_FILE is missing or unreadable"
        return 2
    fi

    # The file is sourced: it must be owned by root and mode 600.
    _mode=$(stat -c %a "$CONFIG_FILE" 2>/dev/null)
    if [ -n "$_mode" ] && [ "$_mode" != "600" ]; then
        log "WARNING: $CONFIG_FILE has mode $_mode, expected 600"
    fi

    if ! . "$CONFIG_FILE"; then
        log "ERROR: cannot parse $CONFIG_FILE"
        return 2
    fi

    SMA_HOST=${SMA_HOST:-}
    SMA_RIGHT=${SMA_RIGHT:-usr}
    SMA_PASS=${SMA_PASS:-}
    SMA_SOC_KEY=${SMA_SOC_KEY:-6100_00295A00}
    SMA_INSECURE=${SMA_INSECURE:-1}
    SMA_CONNECT_TIMEOUT=${SMA_CONNECT_TIMEOUT:-5}
    SMA_MAX_TIME=${SMA_MAX_TIME:-15}
    SMA_RETRIES=${SMA_RETRIES:-2}
    SMA_RETRY_DELAY=${SMA_RETRY_DELAY:-5}

    CCU_REGA_URL=${CCU_REGA_URL:-http://127.0.0.1:8181/tclrega.exe}
    SV_SOC_NAME=${SV_SOC_NAME:-SV_Batterie_SoC}
    SV_SIGNAL_NAME=${SV_SIGNAL_NAME:-SV_Batterie_95_Signal}
    SV_SIGNAL_ON=${SV_SIGNAL_ON:-95}
    SV_SIGNAL_OFF=${SV_SIGNAL_OFF:-90}

    if [ -z "$SMA_HOST" ] || [ -z "$SMA_PASS" ]; then
        log "ERROR: SMA_HOST and SMA_PASS must be set in $CONFIG_FILE"
        return 2
    fi

    case "$SMA_RIGHT" in
        usr|user) ;;
        *) log "ERROR: SMA_RIGHT must be 'usr' or 'user'"; return 2 ;;
    esac

    case "$SMA_SOC_KEY" in
        ''|*[!A-Za-z0-9_]*) log "ERROR: invalid SMA_SOC_KEY"; return 2 ;;
    esac

    case "$CCU_REGA_URL" in
        http://*|https://*) ;;
        *) log "ERROR: CCU_REGA_URL must start with http:// or https://"; return 2 ;;
    esac

    case "$SV_SOC_NAME" in
        ''|*[!A-Za-z0-9_]*) log "ERROR: invalid SV_SOC_NAME"; return 2 ;;
    esac
    case "$SV_SIGNAL_NAME" in
        ''|*[!A-Za-z0-9_]*) log "ERROR: invalid SV_SIGNAL_NAME"; return 2 ;;
    esac

    case "$SV_SIGNAL_ON" in ''|*[!0-9]*) SV_SIGNAL_ON=95 ;; esac
    case "$SV_SIGNAL_OFF" in ''|*[!0-9]*) SV_SIGNAL_OFF=90 ;; esac
    if [ "$SV_SIGNAL_ON" -gt 100 ] || [ "$SV_SIGNAL_OFF" -lt 0 ]; then
        log "ERROR: hysteresis thresholds must be within 0..100"
        return 2
    fi
    if [ "$SV_SIGNAL_ON" -le "$SV_SIGNAL_OFF" ]; then
        log "ERROR: SV_SIGNAL_ON ($SV_SIGNAL_ON) must be greater than SV_SIGNAL_OFF ($SV_SIGNAL_OFF)"
        return 2
    fi

    case "$SMA_RETRIES" in ''|*[!0-9]*|0) SMA_RETRIES=2 ;; esac
    case "$SMA_RETRY_DELAY" in ''|*[!0-9]*) SMA_RETRY_DELAY=5 ;; esac
    case "$SMA_CONNECT_TIMEOUT" in ''|*[!0-9]*|0) SMA_CONNECT_TIMEOUT=5 ;; esac
    case "$SMA_MAX_TIME" in ''|*[!0-9]*|0) SMA_MAX_TIME=15 ;; esac

    return 0
}

# ---------------------------------------------------------------------------
# Single-instance lock (survives cron overlapping, cleans up stale locks)
# ---------------------------------------------------------------------------
acquire_lock() {
    if mkdir "$LOCK_DIR" 2>/dev/null; then
        LOCK_OWNED=1
        printf '%s\n' "$$" > "$LOCK_DIR/pid"
        return 0
    fi

    _oldpid=$(cat "$LOCK_DIR/pid" 2>/dev/null)
    if [ -n "$_oldpid" ] && kill -0 "$_oldpid" 2>/dev/null; then
        log "another instance is already running (pid $_oldpid), exiting"
        return 1
    fi

    log "removing stale lock"
    rm -rf "$LOCK_DIR"
    if mkdir "$LOCK_DIR" 2>/dev/null; then
        LOCK_OWNED=1
        printf '%s\n' "$$" > "$LOCK_DIR/pid"
        return 0
    fi

    log "ERROR: cannot create lock directory $LOCK_DIR"
    return 1
}

# ---------------------------------------------------------------------------
# SMA JSON API (WebUI)
# ---------------------------------------------------------------------------
sma_curl() {
    if [ "$SMA_INSECURE" = "1" ]; then
        curl -sS -k --connect-timeout "$SMA_CONNECT_TIMEOUT" -m "$SMA_MAX_TIME" "$@"
    else
        curl -sS --connect-timeout "$SMA_CONNECT_TIMEOUT" -m "$SMA_MAX_TIME" "$@"
    fi
}

sma_login() {
    # Escape backslash and double quote for the JSON string.
    _pw=$(printf '%s' "$SMA_PASS" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')
    _body='{"right":"'"$SMA_RIGHT"'","pass":"'"$_pw"'"}'

    _resp=$(sma_curl -X POST -H 'Content-Type: application/json' \
        --data-binary "$_body" \
        "https://$SMA_HOST/dyn/login.json") || return 1

    SID=$(printf '%s' "$_resp" \
        | sed -n 's/.*"sid"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
        | head -n 1)

    if [ -z "$SID" ]; then
        log "login failed: $(printf '%s' "$_resp" | tr -d '\r\n' | cut -c1-160)"
        return 1
    fi
    return 0
}

sma_logout() {
    [ -n "$SID" ] || return 0
    _sid=$SID
    SID=""                         # never try twice, even if this call is killed
    _body='{"destDev":[],"sid":"'"$_sid"'"}'
    sma_curl -X POST -H 'Content-Type: application/json' \
        --data-binary "$_body" \
        "https://$SMA_HOST/dyn/logout.json?sid=$_sid" >/dev/null 2>&1 || true
}

sma_get_soc() {
    _body='{"destDev":[],"keys":["'"$SMA_SOC_KEY"'"],"sid":"'"$SID"'"}'

    _resp=$(sma_curl -X POST -H 'Content-Type: application/json' \
        --data-binary "$_body" \
        "https://$SMA_HOST/dyn/getValues.json?sid=$SID") || return 1

    case "$_resp" in
        *"$SMA_SOC_KEY"*) ;;
        *)
            log "unexpected getValues response: $(printf '%s' "$_resp" | tr -d '\r\n' | cut -c1-160)"
            return 1
            ;;
    esac

    # One JSON field per line, take the first numeric "val".
    _val=$(printf '%s' "$_resp" \
        | tr ',' '\n' \
        | sed -n 's/.*"val"[[:space:]]*:[[:space:]]*\(-\{0,1\}[0-9][0-9]*\).*/\1/p' \
        | head -n 1)

    if [ -z "$_val" ]; then
        log "no numeric value for $SMA_SOC_KEY (battery absent or value null)"
        return 1
    fi

    printf '%s\n' "$_val"
    return 0
}

# Login, read, logout. Prints only the SoC on stdout; retries the whole cycle.
fetch_soc() {
    _attempt=1
    while [ "$_attempt" -le "$SMA_RETRIES" ]; do
        if sma_login; then
            if _value=$(sma_get_soc); then
                sma_logout
                printf '%s\n' "$_value"
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
# Hysteresis:  on at >= ON (95), off at <= OFF (90), hold in between.
# Prints 1, 0, or nothing when the state is unknown and the SoC lies in the
# dead band (a lost state file must never cause a premature reset).
# ---------------------------------------------------------------------------
compute_signal() {
    _soc=$1
    _prev=""
    if [ -r "$STATE_FILE" ]; then
        _prev=$(cat "$STATE_FILE" 2>/dev/null)
    fi
    case "$_prev" in
        0|1) ;;
        *) _prev="" ;;
    esac

    if [ "$_soc" -ge "$SV_SIGNAL_ON" ]; then
        _sig=1
    elif [ "$_soc" -le "$SV_SIGNAL_OFF" ]; then
        _sig=0
    elif [ "$_prev" = "1" ]; then
        _sig=1
    elif [ "$_prev" = "0" ]; then
        _sig=0
    else
        _sig=""
    fi

    printf '%s\n' "$_sig"
}

# ---------------------------------------------------------------------------
# CCU3 write via ReGaHSS (tclrega.exe). Body is the raw ReGa script.
# An empty signal leaves the boolean untouched (unknown hysteresis state).
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
        log "ERROR: CCU3 ReGa request failed"
        return 1
    }

    case "$_resp" in
        *SMA_SOC_OK*)
            ;;
        *)
            # Values were probably written anyway; do not fail the run on a
            # firmware that does not return WriteLine() output.
            log "WARNING: CCU3 did not return the confirmation marker: $(printf '%s' "$_resp" | tr -d '\r\n' | cut -c1-160)"
            ;;
    esac

    return 0
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    load_config || exit $?
    acquire_lock || exit 0

    soc=$(fetch_soc) || {
        log "could not read SoC from $SMA_HOST, keeping last CCU3 values"
        exit 1
    }

    case "$soc" in
        ''|*[!0-9]*)
            log "invalid SoC '$soc', keeping last CCU3 values"
            exit 1
            ;;
    esac
    if [ "$soc" -lt 0 ] || [ "$soc" -gt 100 ]; then
        log "SoC out of range ($soc), keeping last CCU3 values"
        exit 1
    fi

    signal=$(compute_signal "$soc")

    write_ccu "$soc" "$signal" || exit 1

    case "$signal" in
        0|1)
            if printf '%s\n' "$signal" > "$STATE_FILE.tmp" && mv "$STATE_FILE.tmp" "$STATE_FILE"; then
                log "SoC=${soc}% signal=$signal"
            else
                log "SoC=${soc}% signal=$signal (WARNING: cannot persist hysteresis state)"
            fi
            ;;
        *)
            log "SoC=${soc}% signal=unchanged (no valid hysteresis state yet)"
            ;;
    esac

    exit 0
}

main
