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

# Only read-only methods are accepted. The endpoint never mutates state, so a
# POST/PUT/DELETE is always a mistake or an attack attempt.
REQUEST_METHOD=${REQUEST_METHOD:-GET}
case "$REQUEST_METHOD" in
    GET|HEAD) ;;
    *)
        printf 'Status: 405 Method Not Allowed\r\n'
        printf 'Content-Type: application/json; charset=utf-8\r\n'
        printf 'Cache-Control: no-store, max-age=0\r\n'
        printf 'X-Content-Type-Options: nosniff\r\n'
        printf 'Allow: GET, HEAD\r\n\r\n'
        printf '{"ok":false,"error":"Use GET or HEAD for this endpoint."}\n'
        exit 0
        ;;
esac

# Output CGI HTTP headers. Content-Type must remain the first line: the
# integration harness and the deploy.sh probe read it as the status marker.
printf 'Content-Type: application/json; charset=utf-8\r\n'
printf 'Cache-Control: no-store, max-age=0\r\n'
printf 'Pragma: no-cache\r\n'
printf 'X-Content-Type-Options: nosniff\r\n'
printf 'Referrer-Policy: same-origin\r\n\r\n'

[ "$REQUEST_METHOD" = "HEAD" ] && exit 0

# Scan and stream JSON array. Only regular, non-symlink files whose content is
# a JSON object are published. A symlink dropped into state/plugins (possibly
# pointing at .env or /etc/shadow) is never followed by this root CGI.
printf '['

first=1
if [ -d "$PLUGINS_STATE_DIR" ]; then
    for f in "$PLUGINS_STATE_DIR"/*.json; do
        [ -f "$f" ] || continue
        [ ! -L "$f" ] || continue
        _trimmed=$(tr -d '\r\n\t ' < "$f" 2>/dev/null)
        case "$_trimmed" in
            '{'*'}') ;;
            *) continue ;;
        esac
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
