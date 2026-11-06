#!/usr/bin/env bash
# =============================================================================
#  homematic-sma-battery-monitor - deployment helper (runs on your workstation)
# =============================================================================
#  Copies the add-on to a CCU3 / RaspberryMatic over SSH, sets permissions,
#  installs the dashboard into the lighttpd add-on directory, registers the
#  two-minute cron job and creates the ReGa system variables.
#
#  Requires: bash, ssh, tar (all on the workstation). No rsync needed.
#
#  Usage:
#    ./deploy.sh -H 192.168.178.10
#    ./deploy.sh -H ccu3.local -u root --skip-vars
#    ./deploy.sh --dry-run
#
#  Configuration is read from .env in this directory. DEPLOY_HOST,
#  DEPLOY_USER and DEPLOY_PORT can be overridden on the command line.
# =============================================================================

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ADDON_DIR=
WEB_DIR=
HOST=
SSH_USER=
SSH_PORT=
SKIP_CRON=0
SKIP_VARS=0
SKIP_WEB=0
SKIP_TEST=0
DRY_RUN=0

usage() {
    cat <<'EOF'
Usage: ./deploy.sh [options]

Options:
  -H, --host HOST        SSH host of the CCU3 (default: DEPLOY_HOST from .env)
  -u, --user USER        SSH user (default: DEPLOY_USER or root)
  -p, --port PORT        SSH port (default: DEPLOY_PORT or 22)
      --dir PATH         Remote add-on directory (default: /usr/local/addons/sma_battery)
      --web-dir PATH     Remote dashboard directory
                         (default: /usr/local/etc/config/addons/www/sma_battery)
  -n, --dry-run          Show what would be done, change nothing
      --skip-cron        Do not install or restart the cron job
      --skip-vars        Do not create the ReGa system variables
      --skip-web         Do not install the dashboard files
      --skip-test        Do not run a first check after deployment
  -h, --help             Show this help

Examples:
  ./deploy.sh -H 192.168.178.10
  ./deploy.sh -H ccu3.local --skip-vars --skip-cron
  ./deploy.sh --dry-run
EOF
}

die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

info() {
    printf '==> %s\n' "$*"
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        -H|--host)
            [ "$#" -ge 2 ] || die "$1 needs a value"
            HOST=$2
            shift 2
            ;;
        -u|--user)
            [ "$#" -ge 2 ] || die "$1 needs a value"
            SSH_USER=$2
            shift 2
            ;;
        -p|--port)
            [ "$#" -ge 2 ] || die "$1 needs a value"
            SSH_PORT=$2
            shift 2
            ;;
        --dir)
            [ "$#" -ge 2 ] || die "$1 needs a value"
            ADDON_DIR=$2
            shift 2
            ;;
        --web-dir)
            [ "$#" -ge 2 ] || die "$1 needs a value"
            WEB_DIR=$2
            shift 2
            ;;
        -n|--dry-run)
            DRY_RUN=1
            shift
            ;;
        --skip-cron)
            SKIP_CRON=1
            shift
            ;;
        --skip-vars)
            SKIP_VARS=1
            shift
            ;;
        --skip-web)
            SKIP_WEB=1
            shift
            ;;
        --skip-test)
            SKIP_TEST=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            usage >&2
            exit 2
            ;;
    esac
done

if [ -f "$ROOT/.env" ]; then
    # shellcheck disable=SC1091
    . "$ROOT/.env"
else
    die "missing $ROOT/.env - copy .env.example to .env and fill it in"
fi

HOST=${HOST:-${DEPLOY_HOST:-}}
SSH_USER=${SSH_USER:-${DEPLOY_USER:-root}}
SSH_PORT=${SSH_PORT:-${DEPLOY_PORT:-22}}
ADDON_DIR=${ADDON_DIR:-${DEPLOY_ADDON_DIR:-/usr/local/addons/sma_battery}}
WEB_DIR=${WEB_DIR:-${DEPLOY_WEB_DIR:-/usr/local/etc/config/addons/www/sma_battery}}

[ -n "$HOST" ] || die "no CCU host given (use -H or set DEPLOY_HOST in .env)"
[ -n "$SSH_USER" ] || die "empty SSH user"
case "$SSH_PORT" in
    ''|*[!0-9]*) die "SSH port must be numeric" ;;
esac
[ "$SSH_PORT" -ge 1 ] && [ "$SSH_PORT" -le 65535 ] || die "SSH port out of range"

case "$ADDON_DIR" in
    /?*) ;;
    *) die "--dir must be an absolute path" ;;
esac
case "$ADDON_DIR" in
    *..*|*"'"*|*[!A-Za-z0-9._/-]*) die "--dir contains unsupported characters" ;;
esac
case "$WEB_DIR" in
    /?*) ;;
    *) die "--web-dir must be an absolute path" ;;
esac
case "$WEB_DIR" in
    *..*|*"'"*|*[!A-Za-z0-9._/-]*) die "--web-dir contains unsupported characters" ;;
esac

for tool in ssh tar; do
    command -v "$tool" >/dev/null 2>&1 || die "required tool not found: $tool"
done

for file in .env bin/check_soc.sh lib/notify_signal.sh setup_variables.sh \
            web/index.html web/style.css web/app.js; do
    [ -f "$ROOT/$file" ] || die "missing required file: $file"
done

ENV_MODE=$(stat -c %a "$ROOT/.env" 2>/dev/null || true)
if [ -n "$ENV_MODE" ] && [ "$ENV_MODE" != "600" ]; then
    printf 'WARNING: %s has mode %s, expected 600\n' "$ROOT/.env" "$ENV_MODE" >&2
fi

REMOTE="${SSH_USER}@${HOST}"
LOG_FILE_REMOTE=${LOG_FILE:-$ADDON_DIR/state/check_soc.log}
DASHBOARD_URL="http://${HOST}/addons/$(basename "$WEB_DIR")/"

if [ "$DRY_RUN" -eq 1 ]; then
    cat <<EOF
Dry run - nothing will be changed.

  SSH target    : ${REMOTE}:${SSH_PORT}
  Add-on dir    : ${ADDON_DIR}
  Dashboard dir : ${WEB_DIR}
  Dashboard URL : ${DASHBOARD_URL}
  Log file      : ${LOG_FILE_REMOTE}
  Cron          : $([ "$SKIP_CRON" -eq 1 ] && echo skipped || echo "*/2 * * * * ${ADDON_DIR}/bin/check_soc.sh")
  Variables     : $([ "$SKIP_VARS" -eq 1 ] && echo skipped || echo "create via ReGa")
  Dashboard     : $([ "$SKIP_WEB" -eq 1 ] && echo skipped || echo "install")
  First check   : $([ "$SKIP_TEST" -eq 1 ] && echo skipped || echo "run")
EOF
    exit 0
fi

STAGE=$(mktemp -d "${TMPDIR:-/tmp}/sma-battery-deploy.XXXXXX")
cleanup() {
    rm -rf "$STAGE"
}
trap cleanup EXIT HUP INT TERM

mkdir -p "$STAGE/bin" "$STAGE/lib" "$STAGE/web"
install -m 700 "$ROOT/bin/check_soc.sh"      "$STAGE/bin/check_soc.sh"
install -m 700 "$ROOT/lib/notify_signal.sh"  "$STAGE/lib/notify_signal.sh"
install -m 700 "$ROOT/setup_variables.sh"    "$STAGE/setup_variables.sh"
install -m 600 "$ROOT/.env"                  "$STAGE/.env"
install -m 644 "$ROOT/web/index.html"        "$STAGE/web/index.html"
install -m 644 "$ROOT/web/style.css"         "$STAGE/web/style.css"
install -m 644 "$ROOT/web/app.js"            "$STAGE/web/app.js"

SSH_OPTS=(-o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new -p "$SSH_PORT")

info "checking SSH connection to $REMOTE"
ssh "${SSH_OPTS[@]}" "$REMOTE" 'true' \
    || die "cannot connect to $REMOTE (check host, key and root login)"

info "copying add-on to $ADDON_DIR"
# shellcheck disable=SC2029  # the paths are intentionally expanded locally
tar -cf - -C "$STAGE" . \
    | ssh "${SSH_OPTS[@]}" "$REMOTE" "mkdir -p '$ADDON_DIR' && tar -xf - -C '$ADDON_DIR'" \
    || die "file transfer failed"

info "configuring permissions, dashboard, cron and system variables"
# shellcheck disable=SC2029  # the paths are intentionally expanded locally
ssh "${SSH_OPTS[@]}" "$REMOTE" \
    "ADDON_DIR='$ADDON_DIR' WEB_DIR='$WEB_DIR' SKIP_CRON='$SKIP_CRON' SKIP_VARS='$SKIP_VARS' SKIP_WEB='$SKIP_WEB' SKIP_TEST='$SKIP_TEST' sh -s" <<'REMOTE_SCRIPT'
set -e

mkdir -p "$ADDON_DIR/state"
chmod 700 "$ADDON_DIR" "$ADDON_DIR/bin" "$ADDON_DIR/lib" "$ADDON_DIR/state"
chmod 700 "$ADDON_DIR/bin/check_soc.sh" "$ADDON_DIR/lib/notify_signal.sh" "$ADDON_DIR/setup_variables.sh"
chmod 600 "$ADDON_DIR/.env"
if [ -d "$ADDON_DIR/web" ]; then
    chmod 755 "$ADDON_DIR/web"
fi

# Warn when the dashboard install path and the status path drift apart.
(
    . "$ADDON_DIR/.env"
    case "${WEB_STATUS_FILE:-}" in
        "$WEB_DIR"/*) ;;
        *) printf 'WARNING: WEB_STATUS_FILE (%s) is outside WEB_DIR (%s)\n' "${WEB_STATUS_FILE:-unset}" "$WEB_DIR" >&2 ;;
    esac
)

if [ "$SKIP_WEB" != "1" ]; then
    mkdir -p "$WEB_DIR"
    chmod 755 "$WEB_DIR"
    for file in index.html style.css app.js; do
        cp -f "$ADDON_DIR/web/$file" "$WEB_DIR/$file"
        chmod 644 "$WEB_DIR/$file"
    done
    printf 'Dashboard installed in %s\n' "$WEB_DIR"
fi

if [ "$SKIP_CRON" != "1" ]; then
    CRON_LINE="*/2 * * * * $ADDON_DIR/bin/check_soc.sh >/dev/null 2>&1"
    if command -v crontab >/dev/null 2>&1; then
        CRON_TMP=/tmp/sma_battery_cron.$$
        crontab -l 2>/dev/null | grep -vF "$ADDON_DIR/bin/check_soc.sh" > "$CRON_TMP" || true
        printf '%s\n' "$CRON_LINE" >> "$CRON_TMP"
        crontab "$CRON_TMP"
        rm -f "$CRON_TMP"
    else
        CRON_FILE=/etc/crontabs/root
        mkdir -p /etc/crontabs
        touch "$CRON_FILE"
        CRON_TMP=$CRON_FILE.tmp.$$
        grep -vF "$ADDON_DIR/bin/check_soc.sh" "$CRON_FILE" > "$CRON_TMP" || true
        printf '%s\n' "$CRON_LINE" >> "$CRON_TMP"
        mv -f "$CRON_TMP" "$CRON_FILE"
    fi
    /etc/init.d/S71crond restart >/dev/null 2>&1 \
        || /etc/init.d/S98crond restart >/dev/null 2>&1 \
        || true
    printf 'Cron installed: %s\n' "$CRON_LINE"
fi

if [ "$SKIP_VARS" != "1" ]; then
    sh "$ADDON_DIR/setup_variables.sh"
fi

if [ "$SKIP_TEST" != "1" ]; then
    if "$ADDON_DIR/bin/check_soc.sh"; then
        printf 'First check completed successfully.\n'
    else
        printf 'WARNING: first check failed; see log or run the script manually.\n' >&2
    fi
fi
REMOTE_SCRIPT

cat <<EOF

Deployment complete.

  Add-on      : ${REMOTE}:${ADDON_DIR}
  Dashboard   : ${DASHBOARD_URL}
  Cron        : */2 * * * * ${ADDON_DIR}/bin/check_soc.sh
  Log         : ssh -p ${SSH_PORT} ${REMOTE} 'tail -f ${LOG_FILE_REMOTE}'

Next steps:
  1. Open the dashboard after the first cron run (up to 2 minutes).
  2. Check the variables in CCU3: Status and Control -> System variables.
  3. Set SIGNAL_ENABLED="true" in .env and re-run ./deploy.sh to enable alerts.
EOF
