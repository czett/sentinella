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
        printf 'Pragma: no-cache\r\n'
        printf 'X-Content-Type-Options: nosniff\r\n'
        printf 'Referrer-Policy: same-origin\r\n'
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

# Strict RFC 8259 validator (object at top level). Rejects trailing commas,
# truncated or concatenated documents, bare control characters and 0-byte
# files, so one broken plugin file can never corrupt the response array.
json_object_valid() {
    awk '
    BEGIN { RS = "\001" }
    function ws() { while (p <= n && index(" \t\r\n", substr(s, p, 1))) p++ }
    function str(   c) {
        if (substr(s, p, 1) != "\"") return 0
        p++
        while (p <= n) {
            c = substr(s, p, 1)
            if (c == "\"") { p++; return 1 }
            if (c == "\\") {
                c = substr(s, p + 1, 1)
                if (c == "u") {
                    if (substr(s, p + 2, 4) !~ /^[0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f]$/) return 0
                    p += 6; continue
                }
                if (!index("\"\\/bfnrt", c) || c == "") return 0
                p += 2; continue
            }
            if (c < " ") return 0
            p++
        }
        return 0
    }
    function num(   m) {
        m = substr(s, p)
        if (!match(m, /^-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][-+]?[0-9]+)?/)) return 0
        p += RLENGTH; return 1
    }
    function val(   c) {
        if (++depth > 64) return 0
        ws(); c = substr(s, p, 1)
        if (c == "{") { r = obj() }
        else if (c == "[") { r = arr() }
        else if (c == "\"") { r = str() }
        else if (substr(s, p, 4) == "true" || substr(s, p, 4) == "null") { p += 4; r = 1 }
        else if (substr(s, p, 5) == "false") { p += 5; r = 1 }
        else { r = num() }
        depth--
        return r
    }
    function obj() {
        p++; ws()
        if (substr(s, p, 1) == "}") { p++; return 1 }
        while (1) {
            ws(); if (!str()) return 0
            ws(); if (substr(s, p, 1) != ":") return 0
            p++; if (!val()) return 0
            ws(); c = substr(s, p, 1); p++
            if (c == "}") return 1
            if (c != ",") return 0
        }
    }
    function arr() {
        p++; ws()
        if (substr(s, p, 1) == "]") { p++; return 1 }
        while (1) {
            if (!val()) return 0
            ws(); c = substr(s, p, 1); p++
            if (c == "]") return 1
            if (c != ",") return 0
        }
    }
    { s = s $0 }
    END {
        n = length(s); p = 1; depth = 0
        ws()
        if (substr(s, p, 1) != "{") exit 1
        if (!val()) exit 1
        ws()
        exit (p <= n) ? 1 : 0
    }'
}

first=1
if [ -d "$PLUGINS_STATE_DIR" ]; then
    for f in "$PLUGINS_STATE_DIR"/*.json; do
        [ -f "$f" ] || continue
        [ ! -L "$f" ] || continue
        [ -s "$f" ] || continue
        # Read once (capped at 64 KiB) and validate/emit the same bytes, so a
        # writer replacing the file mid-request cannot slip unvalidated data in.
        _content=$(head -c 65536 "$f" 2>/dev/null) || continue
        printf '%s' "$_content" | json_object_valid || continue
        if [ "$first" -eq 1 ]; then
            first=0
        else
            printf ','
        fi
        printf '%s' "$_content"
    done
fi

printf ']\n'
exit 0
