#!/bin/sh
# =============================================================================
#  Sentinella - Current version and GitHub release check CGI endpoint
# =============================================================================
#  GET /addons/sentinella/api/update_check.cgi
#
#  Returns the installed Sentinella version and, when online, compares it with
#  the latest GitHub release tag.
# =============================================================================

PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 077

SCRIPT_DIR=$(cd "$(dirname "$0")" 2>/dev/null && pwd)
[ -n "$SCRIPT_DIR" ] || SCRIPT_DIR=.

resolve_addon_dir() {
    _dir=${SMA_MONITOR_ADDON_DIR:-}

    if [ -z "$_dir" ] && [ -r "$SCRIPT_DIR/addon_dir" ]; then
        _dir=$(sed -n '1p' "$SCRIPT_DIR/addon_dir" 2>/dev/null)
    fi

    if [ -z "$_dir" ]; then
        _candidate=$(cd "$SCRIPT_DIR/../.." 2>/dev/null && pwd)
        if [ -n "$_candidate" ] && { [ -r "$_candidate/VERSION" ] || [ -r "$_candidate/.env" ] || [ -d "$_candidate/plugins.d" ]; }; then
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

json_escape() {
    printf '%s' "$1" | tr -d '\r\n' \
        | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/\t/\\t/g'
}

json_str() {
    printf '"%s"' "$(json_escape "$1")"
}

emit_json() {
    _status=$1
    _payload=$2
    printf 'Status: %s\r\n' "$_status"
    printf 'Content-Type: application/json; charset=utf-8\r\n'
    printf 'Cache-Control: no-store, max-age=0\r\n'
    printf 'Pragma: no-cache\r\n'
    printf 'X-Content-Type-Options: nosniff\r\n'
    printf 'Referrer-Policy: same-origin\r\n'
    printf '\r\n'
    printf '%s\n' "$_payload"
    exit 0
}

REQUEST_METHOD=${REQUEST_METHOD:-GET}
if [ "$REQUEST_METHOD" != "GET" ]; then
    emit_json "405 Method Not Allowed" '{"ok":false,"error":"Use GET for this endpoint."}'
fi

INSTALLED_VERSION=
for _vfile in \
    "$ADDON_DIR/VERSION" \
    "$SCRIPT_DIR/../VERSION" \
    "$SCRIPT_DIR/../../VERSION" \
    /usr/local/addons/sentinella/VERSION
do
    if [ -r "$_vfile" ]; then
        INSTALLED_VERSION=$(sed -n '1p' "$_vfile" 2>/dev/null | tr -d '[:space:]')
        [ -n "$INSTALLED_VERSION" ] && break
    fi
done

if [ -z "$INSTALLED_VERSION" ] && [ -r "$ADDON_DIR/sentinella.conf" ]; then
    INSTALLED_VERSION=$(sed -n 's/^[[:space:]]*VERSION[[:space:]]*=[[:space:]]*//p' "$ADDON_DIR/sentinella.conf" 2>/dev/null | head -n 1 | tr -d '"\047[:space:]')
fi

INSTALLED_VERSION=${INSTALLED_VERSION#v}
case "$INSTALLED_VERSION" in
    ''|*[!0-9A-Za-z._-]*) INSTALLED_VERSION="1.1.0" ;;
esac

API_URL=${SENTINELLA_UPDATE_URL:-https://api.github.com/repos/czett/sentinella/releases/latest}
case "$API_URL" in
    http://*|https://*) ;;
    *) API_URL="https://api.github.com/repos/czett/sentinella/releases/latest" ;;
esac

LATEST_VERSION=$INSTALLED_VERSION
ONLINE="false"
UPDATE_AVAILABLE="false"

if [ "${SENTINELLA_OFFLINE:-0}" != "1" ] && command -v curl >/dev/null 2>&1; then
    RAW_REL=$(curl -fsSL --connect-timeout 3 --max-time 5 \
        -H "Accept: application/vnd.github+json" \
        "$API_URL" 2>/dev/null) || RAW_REL=
    if [ -n "$RAW_REL" ]; then
        TAG=$(printf '%s' "$RAW_REL" | tr ',{}' '\n' |
            sed -n 's/^[[:space:]]*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*$/\1/p' |
            head -n 1)
        TAG=${TAG#v}
        case "$TAG" in
            [0-9]*)
                LATEST_VERSION=$TAG
                ONLINE="true"
                ;;
        esac
    fi
fi

version_lt() {
    awk -v a="$1" -v b="$2" 'BEGIN {
        na = split(a, pa, ".");
        nb = split(b, pb, ".");
        n = (na > nb) ? na : nb;
        for (i = 1; i <= n; i++) {
            va = (i <= na) ? pa[i] + 0 : 0;
            vb = (i <= nb) ? pb[i] + 0 : 0;
            if (va < vb) exit 0;
            if (va > vb) exit 1;
        }
        exit 1;
    }'
}

if [ "$ONLINE" = "true" ] && version_lt "$INSTALLED_VERSION" "$LATEST_VERSION"; then
    UPDATE_AVAILABLE="true"
fi

emit_json "200 OK" \
    "{\"ok\":true,\"version\":$(json_str "$INSTALLED_VERSION"),\"installed_version\":$(json_str "$INSTALLED_VERSION"),\"latest_version\":$(json_str "$LATEST_VERSION"),\"update_available\":$UPDATE_AVAILABLE,\"online\":$ONLINE}"
