#!/bin/sh
# =============================================================================
#  homematic-sma-battery-monitor - Signal notification hook
# =============================================================================
#  Called by bin/check_soc.sh, detached, once per signal transition:
#
#      notify_signal.sh trigger 96
#      notify_signal.sh reset   89
#
#  Sends a message through the CallMeBot Signal gateway (or any compatible
#  endpoint). The main cron job never waits for this script.
#
#  Exit codes: 0 = sent (or notifications disabled), 1 = send failed,
#              2 = invalid call/configuration.
# =============================================================================

PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 077

APP_DIR=$(cd "$(dirname "$0")/.." 2>/dev/null && pwd)
[ -n "$APP_DIR" ] || APP_DIR=/usr/local/addons/sma_battery

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

log() {
    _ts=$(date '+%Y-%m-%d %H:%M:%S' 2>/dev/null)
    [ -n "$_ts" ] || _ts=-
    printf '%s %s\n' "$_ts" "$*" >&2
    if command -v logger >/dev/null 2>&1; then
        logger -t sma_battery_notify "$*" 2>/dev/null || true
    fi
}

is_uint() {
    case "$1" in
        ''|*[!0-9]*) return 1 ;;
    esac
    return 0
}

render_template() {
    _tpl=$1
    _out=$(printf '%s' "$_tpl" \
        | sed -e "s/%SOC%/$SOC/g" \
              -e "s/%TRIGGER%/$THRESHOLD_TRIGGER/g" \
              -e "s/%RESET%/$THRESHOLD_RESET/g" \
              -e "s/%HOST%/$SMA_IP/g")
    # "%%" is an escaped literal percent sign.
    printf '%s' "$_out" | sed 's/%%/%/g'
}

main() {
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
    SIGNAL_PHONE=${SIGNAL_PHONE:-}
    SIGNAL_APIKEY=${SIGNAL_APIKEY:-}
    SIGNAL_API_URL=${SIGNAL_API_URL:-https://signal.callmebot.com/signal/send.php}
    SIGNAL_TIMEOUT=${SIGNAL_TIMEOUT:-15}
    SIGNAL_RETRIES=${SIGNAL_RETRIES:-2}
    THRESHOLD_TRIGGER=${THRESHOLD_TRIGGER:-95}
    THRESHOLD_RESET=${THRESHOLD_RESET:-90}
    SMA_IP=${SMA_IP:-}

    if [ "$SIGNAL_ENABLED" != "true" ]; then
        return 0
    fi
    if [ -z "$SIGNAL_PHONE" ] || [ -z "$SIGNAL_APIKEY" ]; then
        log "ERROR: SIGNAL_PHONE and SIGNAL_APIKEY must be set when notifications are enabled"
        return 2
    fi
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

    if [ "$EVENT" = "trigger" ]; then
        TEXT=${SIGNAL_TEXT_TRIGGER:-"Battery full: SoC %SOC% (trigger at %TRIGGER%) on %HOST%"}
    else
        TEXT=${SIGNAL_TEXT_RESET:-"Battery OK: SoC %SOC% (reset at %RESET%) on %HOST%"}
    fi
    TEXT=$(render_template "$TEXT")

    _attempt=1
    while [ "$_attempt" -le "$SIGNAL_RETRIES" ]; do
        _resp=$(curl -fsS --connect-timeout 5 -m "$SIGNAL_TIMEOUT" --get \
            --data-urlencode "phone=$SIGNAL_PHONE" \
            --data-urlencode "apikey=$SIGNAL_APIKEY" \
            --data-urlencode "text=$TEXT" \
            "$SIGNAL_API_URL" 2>&1)
        _rc=$?
        if [ "$_rc" -eq 0 ]; then
            log "notification sent ($EVENT, SoC ${SOC}%)"
            return 0
        fi

        if [ "$_attempt" -lt "$SIGNAL_RETRIES" ]; then
            sleep 3
        fi
        _attempt=$((_attempt + 1))
    done

    log "ERROR: notification failed ($EVENT, SoC ${SOC}%): $(printf '%s' "$_resp" | tr -d '\r\n' | cut -c1-160)"
    return 1
}

main "$@"
