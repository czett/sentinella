#!/bin/sh
# =============================================================================
#  Sentinella - Signal notification hook
# =============================================================================
#  Called by bin/check_soc.sh, detached, once per signal transition:
#
#      notify_signal.sh trigger 96
#      notify_signal.sh reset   89
#
#  Verify the configured credentials from a terminal without touching the
#  battery state (this sends a clearly marked test message):
#
#      notify_signal.sh --test
#
#  Sends a message through the CallMeBot Signal gateway (or any compatible
#  endpoint). The main cron job never waits for this script.
#
#  Messages follow UI_LANGUAGE from .env ("en" or "de", default "en"). They
#  contain no URLs or host names; only the measured values are interpolated.
#
#  Safety features:
#    - curl --data-urlencode performs the percent-encoding, so spaces and
#      German umlauts work without python, jq or iconv on BusyBox.
#    - A per-event cooldown (SIGNAL_COOLDOWN, default 3600 s) prevents
#      duplicate alerts when cron runs repeatedly or the state file was lost.
#    - --test never writes notification or battery state files.
#
#  Exit codes: 0 = sent (or notifications disabled/suppressed),
#              1 = send failed, 2 = invalid call/configuration.
# =============================================================================

PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 077

APP_DIR=$(cd "$(dirname "$0")/.." 2>/dev/null && pwd)
[ -n "$APP_DIR" ] || APP_DIR=/usr/local/addons/sentinella

ENV_FILE=${SMA_MONITOR_ENV:-}
if [ -z "$ENV_FILE" ]; then
    if [ -r "$APP_DIR/.env" ]; then
        ENV_FILE=$APP_DIR/.env
    elif [ -r "$APP_DIR/config.env" ]; then
        ENV_FILE=$APP_DIR/config.env
    else
        ENV_FILE=$APP_DIR/.env
    fi
fi

STATE_DIR=
NOTIFY_LOCK_DIR=
NOTIFY_LOCK_OWNED=0

log() {
    _ts=$(date '+%Y-%m-%d %H:%M:%S' 2>/dev/null)
    [ -n "$_ts" ] || _ts=-
    printf '%s %s\n' "$_ts" "$*" >&2
    if command -v logger >/dev/null 2>&1; then
        logger -t sentinella_notify "$*" 2>/dev/null || true
    fi
}

is_uint() {
    case "$1" in
        ''|*[!0-9]*) return 1 ;;
    esac
    return 0
}

trim_ws() {
    printf '%s' "${1:-}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'
}

usage() {
    cat <<'EOF'
Usage: notify_signal.sh trigger|reset SOC
       notify_signal.sh --test
       notify_signal.sh --help

  trigger SOC   send the overflow/trigger message
  reset SOC     send the recovery/reset message
  --test        send a test message and validate phone/apikey credentials
  --help        show this help

The script reads the same .env as bin/check_soc.sh. It is normally started in
the background by the monitor; curl timeouts therefore never block cron.
EOF
}

cleanup() {
    _rc=$?
    trap - EXIT HUP INT TERM
    if [ "$NOTIFY_LOCK_OWNED" = "1" ] && [ -n "$NOTIFY_LOCK_DIR" ]; then
        rm -rf "$NOTIFY_LOCK_DIR" 2>/dev/null || true
    fi
    # Drop a cooldown stamp left half-written by an aborted run.
    [ -n "${_file:-}" ] && rm -f "$_file.tmp" 2>/dev/null
    exit "$_rc"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

sed_escape_repl() {
    printf '%s' "${1:-}" | sed -e 's/[\\&|]/\\&/g'
}

render_template() {
    _tpl=$1
    _host=$(sed_escape_repl "$SMA_IP")
    _out=$(printf '%s' "$_tpl" \
        | sed -e "s|{SOC}|$SOC|g" \
              -e "s|{TRIGGER_SOC}|$THRESHOLD_TRIGGER|g" \
              -e "s|{RESET_SOC}|$THRESHOLD_RESET|g" \
              -e "s|{HOST}|$_host|g" \
              -e "s|%SOC%|$SOC|g" \
              -e "s|%TRIGGER%|$THRESHOLD_TRIGGER|g" \
              -e "s|%RESET%|$THRESHOLD_RESET|g" \
              -e "s|%HOST%|$_host|g")
    # "%%" is an escaped literal percent sign (legacy templates).
    printf '%s' "$_out" | sed 's/%%/%/g'
}

load_config() {
    if [ ! -r "$ENV_FILE" ]; then
        log "ERROR: configuration file $ENV_FILE is missing or unreadable"
        return 2
    fi
    # shellcheck source=/dev/null
    if ! . "$ENV_FILE"; then
        log "ERROR: cannot parse $ENV_FILE"
        return 2
    fi

    SIGNAL_ENABLED=${SIGNAL_ENABLED:-false}
    SIGNAL_PHONE=$(trim_ws "${SIGNAL_PHONE:-}")
    SIGNAL_APIKEY=$(trim_ws "${SIGNAL_APIKEY:-${SIGNAL_API_KEY:-}}")
    SIGNAL_API_URL=${SIGNAL_API_URL:-https://signal.callmebot.com/signal/send.php}
    SIGNAL_TIMEOUT=${SIGNAL_TIMEOUT:-15}
    SIGNAL_RETRIES=${SIGNAL_RETRIES:-2}
    SIGNAL_COOLDOWN=${SIGNAL_COOLDOWN:-3600}
    THRESHOLD_TRIGGER=${THRESHOLD_TRIGGER:-95}
    THRESHOLD_RESET=${THRESHOLD_RESET:-90}
    SMA_IP=${SMA_IP:-}

    # The thresholds and host end up inside a sed replacement and the message
    # body. Normalise them here so a hand-edited .env cannot corrupt output.
    case "$THRESHOLD_TRIGGER" in ''|*[!0-9]*) THRESHOLD_TRIGGER=95 ;; esac
    case "$THRESHOLD_RESET" in ''|*[!0-9]*) THRESHOLD_RESET=90 ;; esac
    case "$SMA_IP" in *[!A-Za-z0-9._:-]*) SMA_IP= ;; esac

    # UI_LANGUAGE selects the built-in notification text. Unknown values fall
    # back to English so a typo never silences an alert.
    case "${UI_LANGUAGE:-}" in
        de|DE|de_DE|de-DE) UI_LANGUAGE=de ;;
        *) UI_LANGUAGE=en ;;
    esac

    if [ "$UI_LANGUAGE" = "de" ]; then
        _default_overflow='Sentinella: Batteriespeicher bei {SOC}% (Schwelle: {TRIGGER_SOC}%). Überschuss verfügbar.'
        _default_reset='Sentinella: Batteriespeicher unter {RESET_SOC}% gefallen ({SOC}%). Normalbetrieb aktiv.'
        _default_test='Sentinella: Verbindung erfolgreich getestet. Alarmsystem bereit.'
    else
        _default_overflow='Sentinella: Battery reached {SOC}% (Trigger: {TRIGGER_SOC}%). Excess energy available.'
        _default_reset='Sentinella: Battery dropped below {RESET_SOC}% ({SOC}%). Overflow mode inactive.'
        _default_test='Sentinella: Connection verified. Telemetry and alert channel ready.'
    fi

    # Explicit templates win, including the legacy SIGNAL_TEXT_* names. Leave
    # them unset to follow UI_LANGUAGE.
    SIGNAL_MESSAGE_OVERFLOW=${SIGNAL_MESSAGE_OVERFLOW:-${SIGNAL_TEXT_TRIGGER:-$_default_overflow}}
    SIGNAL_MESSAGE_RESET=${SIGNAL_MESSAGE_RESET:-${SIGNAL_TEXT_RESET:-$_default_reset}}
    SIGNAL_MESSAGE_TEST=${SIGNAL_MESSAGE_TEST:-$_default_test}

    STATE_DIR=${STATE_DIR:-$APP_DIR/state}
    NOTIFY_LOCK_DIR=$STATE_DIR/.notify.lock

    case "$SIGNAL_API_URL" in
        http://*|https://*) ;;
        *) log "ERROR: SIGNAL_API_URL must start with http:// or https://"; return 2 ;;
    esac
    if ! is_uint "$SIGNAL_TIMEOUT" || [ "$SIGNAL_TIMEOUT" -lt 1 ]; then
        SIGNAL_TIMEOUT=15
    fi
    if ! is_uint "$SIGNAL_RETRIES" || [ "$SIGNAL_RETRIES" -lt 1 ]; then
        SIGNAL_RETRIES=2
    fi
    if ! is_uint "$SIGNAL_COOLDOWN"; then
        SIGNAL_COOLDOWN=3600
    fi
    return 0
}

notify_state_file() {
    printf '%s/notify_%s' "$STATE_DIR" "$1"
}

already_sent_recently() {
    _event=$1
    _file=$(notify_state_file "$_event")
    [ -r "$_file" ] || return 1

    _last=$(cat "$_file" 2>/dev/null)
    is_uint "$_last" || return 1
    _now=$(date +%s 2>/dev/null)
    is_uint "$_now" || return 1

    if [ "$_now" -ge "$_last" ]; then
        _age=$((_now - _last))
    else
        # The clock moved backwards; conservatively treat the alert as recent.
        _age=0
    fi
    [ "$_age" -lt "$SIGNAL_COOLDOWN" ]
}

record_sent() {
    _event=$1
    _file=$(notify_state_file "$_event")
    _now=$(date +%s 2>/dev/null)
    is_uint "$_now" || _now=0

    if mkdir -p "$STATE_DIR" 2>/dev/null \
        && printf '%s\n' "$_now" > "$_file.tmp" 2>/dev/null \
        && mv -f "$_file.tmp" "$_file" 2>/dev/null; then
        chmod 600 "$_file" 2>/dev/null || true
        return 0
    fi
    log "WARNING: cannot persist notification cooldown in $STATE_DIR"
    return 1
}

acquire_lock() {
    mkdir -p "$STATE_DIR" 2>/dev/null || return 1

    if mkdir "$NOTIFY_LOCK_DIR" 2>/dev/null; then
        printf '%s\n' "$$" > "$NOTIFY_LOCK_DIR/pid" 2>/dev/null || true
        NOTIFY_LOCK_OWNED=1
        return 0
    fi

    _oldpid=$(cat "$NOTIFY_LOCK_DIR/pid" 2>/dev/null)
    if [ -n "$_oldpid" ] && is_uint "$_oldpid" && kill -0 "$_oldpid" 2>/dev/null; then
        return 1
    fi

    # Stale lock left behind by a killed notification process.
    rm -rf "$NOTIFY_LOCK_DIR" 2>/dev/null || true
    if mkdir "$NOTIFY_LOCK_DIR" 2>/dev/null; then
        printf '%s\n' "$$" > "$NOTIFY_LOCK_DIR/pid" 2>/dev/null || true
        NOTIFY_LOCK_OWNED=1
        return 0
    fi
    return 1
}

send_to_recipient() {
    _phone=$1
    _apikey=$2
    _text=$3
    _attempt=1
    _resp=

    while [ "$_attempt" -le "$SIGNAL_RETRIES" ]; do
        # -G (--get) + --data-urlencode passes the UUID and API key and
        # percent-encodes spaces and umlauts in the message text inside curl.
        # The recipient and key are supplied through a curl config on stdin so
        # they never appear in /proc/<pid>/cmdline (BusyBox printf is a builtin).
        _esc_phone=$(printf '%s' "$_phone" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')
        _esc_key=$(printf '%s' "$_apikey" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')
        _resp=$(
            {
                printf 'data-urlencode = "phone=%s"\n' "$_esc_phone"
                printf 'data-urlencode = "apikey=%s"\n' "$_esc_key"
            } | curl -fsS --connect-timeout 5 -m "$SIGNAL_TIMEOUT" -G \
                --config - \
                --data-urlencode "text=$_text" \
                "$SIGNAL_API_URL" 2>&1
        )
        _rc=$?
        if [ "$_rc" -eq 0 ]; then
            return 0
        fi

        if [ "$_attempt" -lt "$SIGNAL_RETRIES" ]; then
            sleep 3
        fi
        _attempt=$((_attempt + 1))
    done

    log "ERROR: notification failed for recipient $_phone ($EVENT, SoC ${SOC}%): $(printf '%s' "$_resp" | tr -d '\r\n' | cut -c1-160)"
    return 1
}

send_message() {
    _text=$1
    _phones_norm=$(printf '%s' "$SIGNAL_PHONE" | tr ',;' '  ')
    _keys_norm=$(printf '%s' "$SIGNAL_APIKEY" | tr ',;' '  ')

    _p_count=0
    for _p in $_phones_norm; do
        _p_count=$((_p_count + 1))
    done

    _k_count=0
    _first_key=
    for _k in $_keys_norm; do
        [ -n "$_first_key" ] || _first_key=$_k
        _k_count=$((_k_count + 1))
    done

    if [ "$_p_count" -eq 0 ]; then
        log "ERROR: no Signal recipient configured"
        return 1
    fi

    _idx=0
    _failures=0
    for _p in $_phones_norm; do
        _idx=$((_idx + 1))
        _rec_phone=$_p
        _rec_key=

        case "$_p" in
            *:*)
                _rec_phone=${_p%%:*}
                _rec_key=${_p#*:}
                ;;
            *)
                if [ "$_k_count" -eq 1 ]; then
                    _rec_key=$_first_key
                else
                    _j=0
                    for _k in $_keys_norm; do
                        _j=$((_j + 1))
                        if [ "$_j" -eq "$_idx" ]; then
                            _rec_key=$_k
                            break
                        fi
                    done
                fi
                ;;
        esac

        if [ -z "$_rec_phone" ] || [ -z "$_rec_key" ]; then
            log "ERROR: missing UUID or API key for recipient #$_idx ($_p)"
            _failures=$((_failures + 1))
            continue
        fi

        if ! send_to_recipient "$_rec_phone" "$_rec_key" "$_text"; then
            _failures=$((_failures + 1))
        fi
    done

    [ "$_failures" -eq 0 ]
}

main() {
    EVENT=
    SOC=
    TEST=0

    while [ "$#" -gt 0 ]; do
        case "$1" in
            --test)
                TEST=1
                shift
                ;;
            --help|-h)
                usage
                return 0
                ;;
            -*)
                log "ERROR: unknown option $1"
                usage >&2
                return 2
                ;;
            *)
                break
                ;;
        esac
    done

    if [ "$TEST" = "1" ]; then
        EVENT="test"
        SOC=0
    else
        EVENT=${1:-}
        SOC=${2:-}

        case "$EVENT" in
            trigger|reset) ;;
            *) log "ERROR: first argument must be 'trigger' or 'reset'"; return 2 ;;
        esac
        if ! is_uint "$SOC" || [ "$SOC" -lt 0 ] || [ "$SOC" -gt 100 ]; then
            log "ERROR: second argument must be a SoC integer between 0 and 100"
            return 2
        fi
    fi

    load_config || return $?

    if [ "$TEST" = "1" ]; then
        if [ -z "$SIGNAL_PHONE" ]; then
            log "ERROR: SIGNAL_PHONE and SIGNAL_APIKEY must be set"
            return 2
        fi
        case "$SIGNAL_PHONE" in
            *:*) ;;
            *)
                if [ -z "$SIGNAL_APIKEY" ]; then
                    log "ERROR: SIGNAL_PHONE and SIGNAL_APIKEY must be set"
                    return 2
                fi
                ;;
        esac
        if [ "$SIGNAL_ENABLED" != "true" ]; then
            log "WARNING: SIGNAL_ENABLED is not 'true'; sending the explicit --test message anyway"
        fi
        TEXT=$(render_template "$SIGNAL_MESSAGE_TEST")
        if send_message "$TEXT"; then
            log "test notification sent successfully"
            return 0
        fi
        return 1
    fi

    if [ "$SIGNAL_ENABLED" != "true" ]; then
        return 0
    fi
    if [ -z "$SIGNAL_PHONE" ]; then
        log "ERROR: SIGNAL_PHONE and SIGNAL_APIKEY must be set"
        return 2
    fi
    case "$SIGNAL_PHONE" in
        *:*) ;;
        *)
            if [ -z "$SIGNAL_APIKEY" ]; then
                log "ERROR: SIGNAL_PHONE and SIGNAL_APIKEY must be set"
                return 2
            fi
            ;;
    esac

    if ! acquire_lock; then
        log "notification already in progress; skipping duplicate ($EVENT, SoC ${SOC}%)"
        return 0
    fi

    if already_sent_recently "$EVENT"; then
        log "notification suppressed: duplicate $EVENT within ${SIGNAL_COOLDOWN}s (SoC ${SOC}%)"
        return 0
    fi

    if [ "$EVENT" = "trigger" ]; then
        TEXT=$SIGNAL_MESSAGE_OVERFLOW
    else
        TEXT=$SIGNAL_MESSAGE_RESET
    fi
    TEXT=$(render_template "$TEXT")

    if send_message "$TEXT"; then
        record_sent "$EVENT" || true
        log "notification sent ($EVENT, SoC ${SOC}%)"
        return 0
    fi
    return 1
}

main "$@"
