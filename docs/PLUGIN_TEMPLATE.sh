#!/bin/sh
# =============================================================================
#  Sentinella - Custom Plugin Starter Template (BusyBox ash / POSIX /bin/sh)
# =============================================================================
#  Copy to plugins.d/40_my_plugin.sh, then run: chmod 755 plugins.d/40_my_plugin.sh
# =============================================================================
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 077

[ -f "$0.disabled" ] && exit 0

ADDON_DIR=${SMA_MONITOR_ADDON_DIR:-$(cd "$(dirname "$0")/.." 2>/dev/null && pwd)}
[ -n "$ADDON_DIR" ] || ADDON_DIR=/usr/local/addons/sentinella
STATE_DIR=${PLUGIN_STATE_DIR:-$ADDON_DIR/state/plugins}
OUT_FILE=$STATE_DIR/my_plugin.json
TMP_FILE=$OUT_FILE.tmp.$$

cleanup() {
    rm -f "$TMP_FILE" 2>/dev/null || true
}
trap cleanup EXIT HUP INT TERM

# Optional config: [ -r "$ADDON_DIR/config/plugins/my_plugin.env" ] && . "$ADDON_DIR/config/plugins/my_plugin.env"
VALUE=42
if [ -n "${PLUGIN_API_URL:-}" ]; then
    RAW=$(curl -fsS --connect-timeout 5 --max-time 12 "$PLUGIN_API_URL" </dev/null 2>/dev/null || true)
    [ -n "$RAW" ] || exit 0
    PARSED=$(printf '%s' "$RAW" | sed -n 's/.*"value"[[:space:]]*:[[:space:]]*\([0-9][0-9.]*\).*/\1/p' | head -n 1)
    [ -n "$PARSED" ] && VALUE=$PARSED
fi

mkdir -p "$STATE_DIR" 2>/dev/null || exit 0
NOW_ISO=$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || printf '')

cat > "$TMP_FILE" <<EOF || exit 0
{"id":"my_plugin","layout":"stat","title":"MY SENSOR","status_badge":"READY","status_color":"green","timestamp":"$NOW_ISO","primary":{"value":$VALUE,"unit":"W","label":"POWER"},"secondary_metrics":[{"label":"SOURCE","value":"Local"}]}
EOF
chmod 644 "$TMP_FILE" 2>/dev/null || true
mv -f "$TMP_FILE" "$OUT_FILE"
exit 0
