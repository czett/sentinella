#!/bin/sh
# =============================================================================
#  Sentinella - CCU3 system variable setup
# =============================================================================
#  Creates the two ReGaHSS system variables if they do not exist yet:
#
#    SV_Batterie_SoC          number 0..100 %
#    SV_Batterie_95_Signal    boolean
#
#  Idempotent: existing variables are left untouched. Run it on the CCU
#  (preferred, CCU_IP=127.0.0.1) or from a workstation against the CCU.
#
#  Usage: sh setup_variables.sh [-e /path/to/.env]
# =============================================================================

PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 077

APP_DIR=$(cd "$(dirname "$0")" 2>/dev/null && pwd)
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

log() {
    _ts=$(date '+%Y-%m-%d %H:%M:%S' 2>/dev/null)
    [ -n "$_ts" ] || _ts=-
    printf '%s %s\n' "$_ts" "$*" >&2
}

is_uint() {
    case "$1" in
        ''|*[!0-9]*) return 1 ;;
    esac
    return 0
}

is_safe_name() {
    case "$1" in
        ''|*[!A-Za-z0-9_]*) return 1 ;;
    esac
    return 0
}

usage() {
    cat <<'EOF'
Usage: sh setup_variables.sh [-e /path/to/.env]

Creates SV_Batterie_SoC and SV_Batterie_95_Signal on the CCU if missing.
The configuration is read from .env next to this script unless -e is given.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        -e|--env)
            [ "$#" -ge 2 ] || { usage >&2; exit 2; }
            ENV_FILE=$2
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            usage >&2
            exit 2
            ;;
    esac
done

if [ ! -r "$ENV_FILE" ]; then
    log "ERROR: configuration file $ENV_FILE is missing or unreadable"
    exit 2
fi
# shellcheck source=/dev/null
if ! . "$ENV_FILE"; then
    log "ERROR: cannot parse $ENV_FILE"
    exit 2
fi

CCU_IP=${CCU_IP:-127.0.0.1}
CCU_REGA_PORT=${CCU_REGA_PORT:-8181}
SV_SOC_NAME=${SV_SOC_NAME:-SV_Batterie_SoC}
SV_SIGNAL_NAME=${SV_SIGNAL_NAME:-SV_Batterie_95_Signal}

case "$CCU_IP" in
    ''|*[!A-Za-z0-9._-]*)
        log "ERROR: CCU_IP must be an IPv4 address or hostname without scheme/path"
        exit 2
        ;;
esac
if ! is_uint "$CCU_REGA_PORT" || [ "$CCU_REGA_PORT" -lt 1 ] || [ "$CCU_REGA_PORT" -gt 65535 ]; then
    log "ERROR: CCU_REGA_PORT must be an integer between 1 and 65535"
    exit 2
fi
if ! is_safe_name "$SV_SOC_NAME"; then
    log "ERROR: SV_SOC_NAME contains unsupported characters"
    exit 2
fi
if ! is_safe_name "$SV_SIGNAL_NAME"; then
    log "ERROR: SV_SIGNAL_NAME contains unsupported characters"
    exit 2
fi

CCU_REGA_URL="http://$CCU_IP:$CCU_REGA_PORT/tclrega.exe"

REGA=$(cat <<EOF
var hmsbSoc = dom.GetObject("$SV_SOC_NAME");
if (!hmsbSoc) {
    hmsbSoc = dom.CreateObject(OT_VARDP);
    hmsbSoc.Name("$SV_SOC_NAME");
    hmsbSoc.ValueType(ivtFloat);
    hmsbSoc.ValueSubType(istGeneric);
    hmsbSoc.ValueMin(0);
    hmsbSoc.ValueMax(100);
    hmsbSoc.ValueUnit("%");
    hmsbSoc.Internal(false);
    hmsbSoc.Visible(true);
    hmsbSoc.Channel(dom.GetObject(ID_SYSTEM_VARIABLES));
    WriteLine("SMA_VAR_SOC_CREATED");
} else {
    WriteLine("SMA_VAR_SOC_EXISTS");
}

var hmsbSig = dom.GetObject("$SV_SIGNAL_NAME");
if (!hmsbSig) {
    hmsbSig = dom.CreateObject(OT_VARDP);
    hmsbSig.Name("$SV_SIGNAL_NAME");
    hmsbSig.ValueType(ivtBinary);
    hmsbSig.ValueSubType(istBool);
    hmsbSig.Internal(false);
    hmsbSig.Visible(true);
    hmsbSig.Channel(dom.GetObject(ID_SYSTEM_VARIABLES));
    WriteLine("SMA_VAR_SIGNAL_CREATED");
} else {
    WriteLine("SMA_VAR_SIGNAL_EXISTS");
}

WriteLine("SMA_SETUP_OK");
EOF
)

log "creating system variables on $CCU_REGA_URL"

RESP=$(curl -fsS --connect-timeout 5 -m 20 \
    -H 'Content-Type: application/x-www-form-urlencoded' \
    --data-binary "$REGA" \
    "$CCU_REGA_URL") || {
    log "ERROR: ReGa request failed (is ReGaHSS running on port $CCU_REGA_PORT?)"
    exit 1
}

printf '%s\n' "$RESP" | tr -d '\r' | sed '/^[[:space:]]*$/d'

case "$RESP" in
    *SMA_SETUP_OK*) exit 0 ;;
    *)
        log "WARNING: CCU3 did not return SMA_SETUP_OK; check the variables manually"
        exit 1
        ;;
esac
