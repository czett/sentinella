#!/bin/sh
# =============================================================================
#  Sentinella - plugins API (CGI)
# =============================================================================
#  Runs behind lighttpd on CCU3 / RaspberryMatic. BusyBox ash only:
#  no jq, no python, no external JSON library.
#
#  Endpoints:
#      GET /addons/sentinella/api/plugins.cgi
#
#  Scans state/plugins/*.json and concatenates them into a single JSON array:
#  [ { ... }, { ... } ]
#  If no JSON files exist, returns []
# =============================================================================

PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 022

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
PLUGINS_STATE_DIR=$ADDON_DIR/state/plugins

# Output CGI HTTP headers
printf 'Content-Type: application/json; charset=utf-8\r\n\r\n'

# Scan and stream JSON array
printf '['

first=1
if [ -d "$PLUGINS_STATE_DIR" ]; then
    for f in "$PLUGINS_STATE_DIR"/*.json; do
        [ -f "$f" ] || continue
        # Read content, trimming leading/trailing whitespace if needed, or stream directly
        if [ "$first" -eq 1 ]; then
            first=0
        else
            printf ','
        fi
        cat "$f"
    done
fi

printf ']\n'
exit 0
