#!/bin/sh
# =============================================================================
#  Sentinella - idempotent CCU3 / RaspberryMatic uninstaller
# =============================================================================
#  Removes every trace of a Sentinella installation: background workers, cron
#  jobs, rc.d scripts, WebUI links, the add-on registry entry and the add-on
#  directory itself. Missing paths are ignored, so the script can be run any
#  number of times and from any lifecycle state.
#
#  Installed as $ADDON_DIR/uninstall.sh. deploy.sh --wipe runs it remotely.
# =============================================================================

PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH

# Optional prefix makes the script fully testable without touching a live CCU.
ROOT_PREFIX=${SENTINELLA_ROOT:-}

ADDON_DIR=${SENTINELLA_ADDON_DIR:-$ROOT_PREFIX/usr/local/addons/sentinella}
WEB_DIR=${SENTINELLA_WEB_DIR:-$ROOT_PREFIX/usr/local/etc/config/addons/www/sentinella}
WWW_DIR=${SENTINELLA_WWW_DIR:-$ROOT_PREFIX/www/addons}
RCD_DIR=${SENTINELLA_RCD_DIR:-$ROOT_PREFIX/usr/local/etc/config/rc.d}
RCD_LEGACY_DIR=${SENTINELLA_RCD_LEGACY_DIR:-$ROOT_PREFIX/etc/config/rc.d}
CRON_USER=${SENTINELLA_CRON_USER:-$ROOT_PREFIX/usr/local/crontabs/root}
CRON_SYSTEM=${SENTINELLA_CRON_SYSTEM:-$ROOT_PREFIX/etc/crontabs/root}
ADDONS_CFG=${SENTINELLA_ADDONS_CFG:-$ROOT_PREFIX/usr/local/etc/config/addons.cfg}
ADDONS_CFG_LEGACY=${SENTINELLA_ADDONS_CFG_LEGACY:-$ROOT_PREFIX/etc/config/addons.cfg}
LEGACY_ADDON_LINK=${SENTINELLA_LEGACY_ADDON_LINK:-$ROOT_PREFIX/usr/local/addons/sma_battery}

info() {
    printf '[sentinella] %s\n' "$*"
}

# A shell reading a script that disappears underneath it can abort halfway.
# Re-exec from /tmp before the add-on directory is deleted.
case "$0" in
    "$ADDON_DIR"/*)
        if [ -z "${SENTINELLA_UNINSTALL_RELAY:-}" ]; then
            _relay=$(mktemp "${TMPDIR:-/tmp}/sentinella-uninstall.XXXXXX" 2>/dev/null || true)
            if [ -n "$_relay" ] && cp -f "$0" "$_relay" 2>/dev/null; then
                chmod 700 "$_relay" 2>/dev/null || true
                SENTINELLA_UNINSTALL_RELAY=$_relay
                export SENTINELLA_UNINSTALL_RELAY
                exec sh "$_relay" "$@"
            fi
        fi
        ;;
esac

remove_path() {
    _path=$1
    case "$_path" in
        /?*) ;;
        *)
            info "skipped unsafe path: $_path"
            return 0
            ;;
    esac
    [ "$_path" = "/" ] && return 0
    if [ -e "$_path" ] || [ -L "$_path" ]; then
        rm -rf "$_path" 2>/dev/null || true
        info "removed $_path"
    fi
    return 0
}

kill_worker() {
    _pattern=$1
    _found=0
    if command -v pkill >/dev/null 2>&1; then
        pkill -f "$_pattern" 2>/dev/null && _found=1
    else
        for _pid in $(ps 2>/dev/null | grep -F "$_pattern" | grep -v grep | awk '{print $1}'); do
            case "$_pid" in
                ''|*[!0-9]*) continue ;;
            esac
            [ "$_pid" = "$$" ] && continue
            kill "$_pid" 2>/dev/null && _found=1
        done
    fi
    [ "$_found" -eq 1 ] && info "stopped worker matching $_pattern"
    return 0
}

clean_cron_file() {
    _file=$1
    [ -f "$_file" ] || return 0
    _tmp="${_file}.sentinella.$$"
    grep -v -E 'sentinella|sma_battery|check_soc\.sh' "$_file" > "$_tmp" 2>/dev/null || true
    if cmp -s "$_file" "$_tmp" 2>/dev/null; then
        rm -f "$_tmp"
        return 0
    fi
    cat "$_tmp" > "$_file" 2>/dev/null || true
    rm -f "$_tmp"
    CRON_CHANGED=1
    info "cleaned cron entries in $_file"
    return 0
}

clean_registry() {
    _file=$1
    [ -f "$_file" ] || return 0
    _tmp="${_file}.sentinella.$$"
    grep -v -i -E 'sentinella|sma_battery' "$_file" > "$_tmp" 2>/dev/null || true
    if cmp -s "$_file" "$_tmp" 2>/dev/null; then
        rm -f "$_tmp"
        return 0
    fi
    cat "$_tmp" > "$_file" 2>/dev/null || true
    rm -f "$_tmp"
    info "removed add-on registry entry from $_file"
    return 0
}

case "$ADDON_DIR" in
    /?*) ;;
    *)
        info "refusing unsafe add-on directory: $ADDON_DIR"
        exit 1
        ;;
esac

info "uninstalling Sentinella from ${ROOT_PREFIX:-/}"

# 1. Stop cron-driven and background workers. All patterns are specific to
#    Sentinella, so unrelated CCU3 processes stay untouched.
for _worker in check_soc.sh notify_signal.sh plugin_notify.sh \
              10_ccu_health.sh 15_weather.sh 20_renault_zoe.sh \
              25_shelly_em.sh 30_forecast_solar.sh 35_tibber.sh; do
    kill_worker "$_worker"
done

# 2. Remove cron jobs and reload crond when something actually changed.
CRON_CHANGED=0
clean_cron_file "$CRON_USER"
clean_cron_file "$CRON_SYSTEM"
if [ "$CRON_CHANGED" -eq 1 ] && [ -z "$ROOT_PREFIX" ]; then
    if command -v killall >/dev/null 2>&1 && killall -HUP crond 2>/dev/null; then
        info "reloaded crond"
    elif [ -x /etc/init.d/S71crond ]; then
        /etc/init.d/S71crond restart >/dev/null 2>&1 || true
        info "restarted crond"
    elif [ -x /etc/init.d/S98crond ]; then
        /etc/init.d/S98crond restart >/dev/null 2>&1 || true
        info "restarted crond"
    fi
fi

# 3. Remove rc.d / init scripts.
for _rcd in "$RCD_DIR/sentinella" "$RCD_DIR/sma_battery" \
            "$RCD_LEGACY_DIR/sentinella" "$RCD_LEGACY_DIR/sma_battery"; do
    remove_path "$_rcd"
done

# 4. Remove WebUI links and the served dashboard.
remove_path "$WWW_DIR/sentinella"
remove_path "$WWW_DIR/sma_battery"
remove_path "$WEB_DIR"
remove_path "$(dirname "$WEB_DIR")/sma_battery"

# 5. Drop the CCU3 add-on registry entries.
clean_registry "$ADDONS_CFG"
clean_registry "$ADDONS_CFG_LEGACY"

# 6. Remove the add-on directory itself (credentials, config, state, code).
remove_path "$ADDON_DIR"

# 7. Remove the legacy compatibility symlink, never a real directory.
if [ -L "$LEGACY_ADDON_LINK" ]; then
    rm -f "$LEGACY_ADDON_LINK" 2>/dev/null || true
    info "removed legacy link $LEGACY_ADDON_LINK"
fi

sync 2>/dev/null || true
info "Sentinella uninstall complete."

if [ -n "${SENTINELLA_UNINSTALL_RELAY:-}" ]; then
    rm -f "$SENTINELLA_UNINSTALL_RELAY" 2>/dev/null || true
fi
exit 0
