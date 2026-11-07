#!/bin/sh
# =============================================================================
#  Sentinella - Renault Zoe connection test endpoint
# =============================================================================
#  lighttpd maps CGI by suffix, so this wrapper exposes POST/GET
#  /api/test_renault.cgi while sharing all logic in config.cgi.
# =============================================================================

SCRIPT_DIR=$(cd "$(dirname "$0")" 2>/dev/null && pwd)
[ -n "$SCRIPT_DIR" ] || SCRIPT_DIR=.

SMA_CGI_ACTION=test_renault
export SMA_CGI_ACTION

exec /bin/sh "$SCRIPT_DIR/config.cgi"
