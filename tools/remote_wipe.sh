#!/bin/sh
# =============================================================================
#  Sentinella - remote wipe helper
# =============================================================================
#  Thin wrapper around `deploy.sh --wipe`. Removes Sentinella from a CCU3 /
#  RaspberryMatic over SSH using the idempotent uninstaller, then exits.
#
#  Usage:
#    ./tools/remote_wipe.sh -H ccu3-webui
#    ./tools/remote_wipe.sh -H 192.168.178.10 -u root -p 22
#
#  Host defaults come from .env, exactly like deploy.sh.
# =============================================================================

set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
exec "$ROOT/deploy.sh" --wipe "$@"
