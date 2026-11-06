#!/bin/sh
# =============================================================================
#  homematic-sma-battery-monitor - test notification endpoint
# =============================================================================
#  lighttpd maps CGI by suffix, so this small wrapper exposes the
#  POST/GET /api/test_signal action while sharing all logic in config.cgi.
# =============================================================================

SCRIPT_DIR=$(cd "$(dirname "$0")" 2>/dev/null && pwd)
[ -n "$SCRIPT_DIR" ] || SCRIPT_DIR=.

SMA_CGI_ACTION=test_signal
export SMA_CGI_ACTION

exec /bin/sh "$SCRIPT_DIR/config.cgi"
