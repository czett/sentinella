#!/bin/sh
# =============================================================================
#  Sentinella - plugin: ccu_health
# =============================================================================
#  Local-only core telemetry plugin for CCU3 / RaspberryMatic host system health.
#  Collects Homematic CCU3 / RaspberryMatic Duty Cycle, CPU load averages,
#  RAM usage, and system uptime directly from kernel procfs and CCU sysinfo.
#  Zero external network calls.
#
#  Target : Homematic CCU3 / RaspberryMatic (Buildroot, BusyBox ash)
#  Needs  : /bin/sh, sed, awk, date, mv
#  Install: <addon>/plugins.d/10_ccu_health.sh, mode 755, owner root
#  Config : <addon>/config/plugins/ccu_health.env (optional)
#
#  The card is written atomically to <addon>/state/plugins/ccu_health.json.
#
#  Exit codes:
#    0  success (card written atomically)
# =============================================================================

PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 077

ADDON_DIR=${CCU_HEALTH_ADDON_DIR:-${SMA_MONITOR_ADDON_DIR:-}}
if [ -z "$ADDON_DIR" ]; then
    ADDON_DIR=$(cd "$(dirname "$0")/.." 2>/dev/null && pwd)
fi
[ -n "$ADDON_DIR" ] || ADDON_DIR=/usr/local/addons/sentinella
APP_DIR=$ADDON_DIR
STATE_DIR=${CCU_HEALTH_STATE_DIR:-$APP_DIR/state/plugins}
OUT_FILE=$STATE_DIR/ccu_health.json

# ---------------------------------------------------------------------------
# Plugin Manager Execution Guard (config/plugins.env or .disabled marker)
# ---------------------------------------------------------------------------
PLUGIN_CCU_HEALTH=1
for _pm_env in \
    "${SENTINELLA_PLUGINS_ENV:-}" \
    "$ADDON_DIR/config/plugins.env" \
    /usr/local/addons/sentinella/config/plugins.env
do
    if [ -n "$_pm_env" ] && [ -r "$_pm_env" ]; then
        # shellcheck source=/dev/null
        . "$_pm_env" 2>/dev/null || true
        break
    fi
done
case "${PLUGIN_CCU_HEALTH:-1}" in
    0|false|FALSE|off|OFF|no|NO)
        rm -f "$OUT_FILE" 2>/dev/null || true
        exit 0
        ;;
esac
if [ -f "$ADDON_DIR/plugins.d/10_ccu_health.sh.disabled" ]; then
    rm -f "$OUT_FILE" 2>/dev/null || true
    exit 0
fi

# ---------------------------------------------------------------------------
# UI Language & Localized String Maps (DE / EN)
# ---------------------------------------------------------------------------
load_ui_language() {
    _lang=${UI_LANGUAGE:-}
    if [ -z "$_lang" ]; then
        for _env_cand in \
            "${SMA_MONITOR_ENV:-}" \
            "$ADDON_DIR/config/config.env" \
            "$ADDON_DIR/.env" \
            "$ADDON_DIR/config.env" \
            /usr/local/addons/sentinella/config/config.env \
            /usr/local/addons/sentinella/.env
        do
            [ -n "$_env_cand" ] && [ -r "$_env_cand" ] || continue
            _raw=$(sed -n 's/^[[:space:]]*UI_LANGUAGE[[:space:]]*=[[:space:]]*//p' "$_env_cand" 2>/dev/null | tail -n 1)
            _raw=$(printf '%s' "$_raw" | sed -e "s/^['\"]//" -e "s/['\"][[:space:]]*$//")
            if [ -n "$_raw" ]; then
                _lang=$_raw
                break
            fi
        done
    fi
    case "$_lang" in
        en|EN|en_US|en-US|en_GB|en-GB) UI_LANGUAGE=en ;;
        *) UI_LANGUAGE=de ;;
    esac
}

load_ui_language

# ---------------------------------------------------------------------------
# Configuration (first readable file wins)
# ---------------------------------------------------------------------------
CONFIG_FILE=${CCU_HEALTH_ENV:-}
if [ -z "$CONFIG_FILE" ]; then
    for _candidate in \
        "$ADDON_DIR/config/plugins/ccu_health.env" \
        /usr/local/addons/sentinella/config/plugins/ccu_health.env
    do
        if [ -r "$_candidate" ]; then
            CONFIG_FILE=$_candidate
            break
        fi
    done
fi
if [ -n "$CONFIG_FILE" ] && [ -r "$CONFIG_FILE" ]; then
    chmod 600 "$CONFIG_FILE" 2>/dev/null || true
    # shellcheck source=/dev/null
    . "$CONFIG_FILE"
fi

is_uint() {
    case "$1" in
        ''|*[!0-9]*) return 1 ;;
    esac
    return 0
}

CCU_NOTIFY_DC_ENABLED=${CCU_NOTIFY_DC_ENABLED:-0}
CCU_NOTIFY_DC_THRESHOLD=${CCU_NOTIFY_DC_THRESHOLD:-80}

case "$CCU_NOTIFY_DC_ENABLED" in 1|true|TRUE|on|ON|yes|YES) CCU_NOTIFY_DC_ENABLED=1 ;; *) CCU_NOTIFY_DC_ENABLED=0 ;; esac
is_uint "$CCU_NOTIFY_DC_THRESHOLD" || CCU_NOTIFY_DC_THRESHOLD=80

for _pnotify in \
    "$ADDON_DIR/lib/plugin_notify.sh" \
    "$APP_DIR/lib/plugin_notify.sh" \
    /usr/local/addons/sentinella/lib/plugin_notify.sh
do
    if [ -r "$_pnotify" ]; then
        # shellcheck source=/dev/null
        . "$_pnotify"
        break
    fi
done

case "${UI_LANGUAGE:-}" in
    en|EN|en_US|en-US|en_GB|en-GB) UI_LANGUAGE=en ;;
    *) UI_LANGUAGE=de ;;
esac

if [ "$UI_LANGUAGE" = "en" ]; then
    I18N_TITLE="CCU3 SYSTEM"
    I18N_LABEL="DUTY CYCLE"
    I18N_BADGE_OPTIMAL="OPTIMAL"
    I18N_BADGE_WARNING="WARNING"
    I18N_BADGE_CRITICAL="CRITICAL"
    I18N_RAM="RAM"
    I18N_LOAD="LOAD"
    I18N_UPTIME="UPTIME"
else
    I18N_TITLE="CCU3 SYSTEM"
    I18N_LABEL="DUTY CYCLE"
    I18N_BADGE_OPTIMAL="OPTIMAL"
    I18N_BADGE_WARNING="WARNUNG"
    I18N_BADGE_CRITICAL="KRITISCH"
    I18N_RAM="RAM"
    I18N_LOAD="LOAD"
    I18N_UPTIME="UPTIME"
fi

STATE_DIR=${CCU_HEALTH_STATE_DIR:-$APP_DIR/state/plugins}
OUT_FILE=$STATE_DIR/ccu_health.json
MOCK_PROC_DIR=${CCU_HEALTH_PROC_DIR:-/proc}

log() {
    _ts=$(date '+%Y-%m-%d %H:%M:%S' 2>/dev/null)
    [ -n "$_ts" ] || _ts=-
    printf '%s %s\n' "$_ts" "$*" >&2
    if command -v logger >/dev/null 2>&1; then
        logger -t sentinella-ccu-health "$*" 2>/dev/null || true
    fi
}

cleanup() {
    rm -f "$OUT_FILE.tmp.$$" 2>/dev/null || true
}
trap cleanup EXIT HUP INT TERM

mkdir -p "$STATE_DIR" 2>/dev/null || true

# ---------------------------------------------------------------------------
# 1. Collect Duty Cycle
# Preferred order:
#   a) tclrpc.so XML-RPC query (BidCoS port 2001, HmIP fallback port 2010)
#   b) /bin/hm_sysinfo (or custom path via $CCU_SYSINFO_BIN)
#   c) /var/status/dutycycle or /tmp/dutycycle (or custom path via $CCU_DUTYCYCLE_FILE)
#   d) Simulated / environment override / clean fallback to "N/A"
# ---------------------------------------------------------------------------
DUTY_CYCLE=""

# Test / override env
if [ -n "${CCU_DUTY_CYCLE_OVERRIDE:-}" ]; then
    DUTY_CYCLE="$CCU_DUTY_CYCLE_OVERRIDE"
fi

# Query the CCU's own XML-RPC endpoint through tclrpc.so. This is the same
# interface the CCU web UI uses, so it reports the real Duty Cycle even when
# hm_sysinfo or the dutycycle status files are unavailable.
# Port 2001 serves BidCoS; HmIP-only installations may answer on 2010, so the
# Tcl script tries 2001 first and falls back to 2010.
get_duty_cycle_tcl() {
    _tcl_script='load tclrpc.so; if {![catch {set res [xmlrpc http://127.0.0.1:2001/ listBidcosInterfaces]}]} { foreach if $res { if {[regexp {DUTY_CYCLE\s+([0-9]+)} $if -> dc]} { puts $dc; exit 0 } } }; if {![catch {set res [xmlrpc http://127.0.0.1:2010/ listBidcosInterfaces]}]} { foreach if $res { if {[regexp {DUTY_CYCLE\s+([0-9]+)} $if -> dc]} { puts $dc; exit 0 } } }; puts ""'
    if command -v timeout >/dev/null 2>&1; then
        printf '%s\n' "$_tcl_script" | timeout 5 tclsh 2>/dev/null
    else
        printf '%s\n' "$_tcl_script" | tclsh 2>/dev/null
    fi
}

if [ -z "$DUTY_CYCLE" ] && command -v tclsh >/dev/null 2>&1; then
    _dc_tcl=$(get_duty_cycle_tcl | tr -d '\r' | head -n 1)
    case "$_dc_tcl" in
        ''|*[!0-9]*) ;;
        *) DUTY_CYCLE="$_dc_tcl" ;;
    esac
fi

SYSINFO_BIN=${CCU_SYSINFO_BIN:-/bin/hm_sysinfo}
if [ -z "$DUTY_CYCLE" ] && [ -x "$SYSINFO_BIN" ]; then
    # Typical RaspberryMatic output format or flag
    # Try reading duty cycle from sysinfo
    _sysout=$("$SYSINFO_BIN" 2>/dev/null || "$SYSINFO_BIN" -d 2>/dev/null || true)
    _dc_val=$(printf '%s\n' "$_sysout" | sed -n -e 's/.*[Dd]uty[ -]*[Cc]ycle[ :|=]*\([0-9][0-9]*\).*/\1/p' -e 's/^[Dd][Cc][ :|=]*\([0-9][0-9]*\).*/\1/p' | head -n 1)
    if [ -n "$_dc_val" ]; then
        DUTY_CYCLE="$_dc_val"
    fi
fi

if [ -z "$DUTY_CYCLE" ]; then
    for _dc_file in \
        "${CCU_DUTYCYCLE_FILE:-}" \
        /var/status/dutycycle \
        /tmp/dutycycle \
        /var/run/dutycycle
    do
        [ -n "$_dc_file" ] && [ -r "$_dc_file" ] || continue
        # Possible formats: single integer "14", or "CCU2: 14%", or "dutycycle=14"
        _dc_cand=$(sed -n -e 's/.*:[[:space:]]*\([0-9][0-9]*\).*/\1/p' \
                          -e 's/.*=[[:space:]]*\([0-9][0-9]*\).*/\1/p' \
                          -e 's/^[[:space:]]*\([0-9][0-9]*\)[^0-9]*$/\1/p' "$_dc_file" 2>/dev/null | head -n 1)
        if [ -n "$_dc_cand" ]; then
            DUTY_CYCLE="$_dc_cand"
            break
        fi
    done
fi

if [ -z "$DUTY_CYCLE" ]; then
    # Clean fallback for desktop/mock/non-CCU environment
    DUTY_CYCLE="N/A"
fi

# ---------------------------------------------------------------------------
# 2. Collect CPU Load Average (/proc/loadavg)
# ---------------------------------------------------------------------------
LOAD_1="0.00"
LOAD_5="0.00"
LOAD_15="0.00"

LOADAVG_FILE="$MOCK_PROC_DIR/loadavg"
if [ -r "$LOADAVG_FILE" ]; then
    _loads=$(awk '{ printf "%s|%s|%s\n", $1, $2, $3 }' "$LOADAVG_FILE" 2>/dev/null)
    IFS='|' read -r LOAD_1 LOAD_5 LOAD_15 <<EOF
$_loads
EOF
fi
[ -n "$LOAD_1" ] || LOAD_1="0.00"
[ -n "$LOAD_5" ] || LOAD_5="0.00"
[ -n "$LOAD_15" ] || LOAD_15="0.00"
LOAD_STR="$LOAD_1, $LOAD_5, $LOAD_15"

# ---------------------------------------------------------------------------
# 3. Collect RAM Usage (/proc/meminfo)
# ---------------------------------------------------------------------------
MEMINFO_FILE="$MOCK_PROC_DIR/meminfo"
RAM_STR="N/A"
if [ -r "$MEMINFO_FILE" ]; then
    RAM_CALC=$(awk '
    /^MemTotal:/ { total = $2 }
    /^MemAvailable:/ { avail = $2 }
    /^MemFree:/ { free = $2 }
    /^Buffers:/ { buf = $2 }
    /^Cached:/ { cache = $2 }
    END {
        if (!total) total = 0;
        if (!avail) avail = free + buf + cache;
        used = total - avail;
        if (used < 0) used = 0;
        total_mb = int((total + 1023) / 1024);
        used_mb = int((used + 1023) / 1024);
        pct = (total > 0) ? int((used * 100) / total) : 0;
        printf "%d MB / %d MB (%d%%)\n", used_mb, total_mb, pct;
    }' "$MEMINFO_FILE" 2>/dev/null)
    if [ -n "$RAM_CALC" ]; then
        RAM_STR="$RAM_CALC"
    fi
fi

# ---------------------------------------------------------------------------
# 4. Collect Uptime (/proc/uptime)
# Format into human-readable string (e.g. "14 T. 6 Std." / "14d 6h")
# ---------------------------------------------------------------------------
UPTIME_FILE="$MOCK_PROC_DIR/uptime"
UPTIME_STR="N/A"
if [ -r "$UPTIME_FILE" ]; then
    UPTIME_CALC=$(awk -v lang="$UI_LANGUAGE" '
    {
        s = int($1);
        days = int(s / 86400);
        rem = s % 86400;
        hours = int(rem / 3600);
        mins = int((rem % 3600) / 60);

        if (lang == "en") {
            if (days > 0) printf "%dd %dh\n", days, hours;
            else if (hours > 0) printf "%dh %dm\n", hours, mins;
            else printf "%dm\n", mins;
        } else {
            if (days > 0) printf "%d T. %d Std.\n", days, hours;
            else if (hours > 0) printf "%d Std. %d Min.\n", hours, mins;
            else printf "%d Min.\n", mins;
        }
    }' "$UPTIME_FILE" 2>/dev/null)
    if [ -n "$UPTIME_CALC" ]; then
        UPTIME_STR="$UPTIME_CALC"
    fi
fi

# ---------------------------------------------------------------------------
# 5. Evaluate Status Badge & Color
# Badge rules (Duty Cycle numeric):
#   - Duty Cycle < 50%            -> "OPTIMAL" (green)
#   - Duty Cycle 50% to 80%       -> "WARNUNG" / "WARNING" (blue / amber style)
#   - Duty Cycle > 80%            -> "KRITISCH" / "CRITICAL" (red)
# If no Duty Cycle is available, load average drives the badge as before.
# ---------------------------------------------------------------------------
STATUS_CALC=$(awk -v dc="$DUTY_CYCLE" -v l1="$LOAD_1" \
    -v b_opt="$I18N_BADGE_OPTIMAL" \
    -v b_warn="$I18N_BADGE_WARNING" \
    -v b_crit="$I18N_BADGE_CRITICAL" \
    'BEGIN {
    l1_num = l1 + 0;
    if (dc ~ /^[0-9]+$/) {
        dc_num = dc + 0;
        if (dc_num > 80) {
            badge = b_crit;
            color = "red";
        } else if (dc_num >= 50) {
            badge = b_warn;
            color = "blue";
        } else {
            badge = b_opt;
            color = "green";
        }
    } else {
        if (l1_num > 4.0) {
            badge = b_crit; color = "red";
        } else if (l1_num >= 2.0) {
            badge = b_warn; color = "blue";
        } else {
            badge = b_opt; color = "green";
        }
    }
    printf "%s|%s\n", badge, color;
}')

IFS='|' read -r BADGE BADGE_COLOR <<EOF
$STATUS_CALC
EOF
[ -n "$BADGE" ] || BADGE="$I18N_BADGE_OPTIMAL"
[ -n "$BADGE_COLOR" ] || BADGE_COLOR="green"

# ---------------------------------------------------------------------------
# 6. JSON Formatting & Atomic Output Contract
# Primary Hero Metric:
#   If DUTY_CYCLE is numeric: { "value": <num>, "unit": "%", "label": "DUTY CYCLE" }
#   If DUTY_CYCLE is "N/A":   { "value": "N/A", "unit": "", "label": "DUTY CYCLE" }
# ---------------------------------------------------------------------------
NOW_ISO=$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null) || NOW_ISO=

if [ "$DUTY_CYCLE" = "N/A" ]; then
    PRIMARY_JSON="{\"value\": \"N/A\", \"unit\": \"\", \"label\": \"$I18N_LABEL\"}"
else
    PRIMARY_JSON="{\"value\": $DUTY_CYCLE, \"unit\": \"%\", \"label\": \"$I18N_LABEL\"}"
fi

cat > "$OUT_FILE.tmp.$$" <<EOF
{
  "id": "ccu_health",
  "layout": "stat",
  "title": "$I18N_TITLE",
  "status_badge": "$BADGE",
  "status_color": "$BADGE_COLOR",
  "timestamp": "$NOW_ISO",
  "primary": $PRIMARY_JSON,
  "secondary_metrics": [
    { "label": "$I18N_RAM", "value": "$RAM_STR" },
    { "label": "$I18N_LOAD", "value": "$LOAD_STR" },
    { "label": "$I18N_UPTIME", "value": "$UPTIME_STR" }
  ]
}
EOF

if mv -f "$OUT_FILE.tmp.$$" "$OUT_FILE" 2>/dev/null; then
    chmod 644 "$OUT_FILE" 2>/dev/null || true
    log "ccu_health card updated: duty cycle $DUTY_CYCLE%, load $LOAD_STR"
else
    rm -f "$OUT_FILE.tmp.$$" 2>/dev/null || true
    log "WARNING: could not publish $OUT_FILE"
    exit 1
fi

# ---------------------------------------------------------------------------
# Threshold Notifications (CallMeBot / Signal Gateway)
# ---------------------------------------------------------------------------
if [ "$CCU_NOTIFY_DC_ENABLED" = "1" ] && is_uint "$DUTY_CYCLE" && command -v notify_plugin_threshold >/dev/null 2>&1; then
    _msg_dc="[CCU3] Duty Cycle hoch: $DUTY_CYCLE% (Limit: $CCU_NOTIFY_DC_THRESHOLD%)"
    notify_plugin_threshold "ccu_health" "duty_cycle" "$DUTY_CYCLE" "$CCU_NOTIFY_DC_THRESHOLD" "above" "$_msg_dc"
fi

exit 0
