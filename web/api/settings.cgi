#!/bin/sh
# =============================================================================
#  Sentinella - settings endpoint alias
# =============================================================================
#  lighttpd maps CGI by suffix, so this wrapper exposes POST/GET /api/settings.cgi
#  while sharing all configuration and plugin credential logic in config.cgi.
# =============================================================================

SCRIPT_DIR=$(cd "$(dirname "$0")" 2>/dev/null && pwd)
[ -n "$SCRIPT_DIR" ] || SCRIPT_DIR=.

SMA_CGI_ACTION=config
export SMA_CGI_ACTION

exec /bin/sh "$SCRIPT_DIR/config.cgi"
