#!/usr/bin/env bash
# =============================================================================
#  Sentinella - deployment helper (runs on your workstation)
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
#  An existing remote .env is preserved; only a first install copies the
#  workstation file. With SIGNAL_ENABLED="true", a Signal --test is run.
# =============================================================================

set -eu
# Enable pipefail when the shell supports it (bash); harmless under POSIX sh.
if (set -o pipefail) 2>/dev/null; then
    set -o pipefail
fi

ROOT=$(cd "$(dirname "$0")" && pwd)
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
      --dir PATH         Remote add-on directory (default: /usr/local/addons/sentinella)
      --web-dir PATH     Remote dashboard directory
                         (default: /usr/local/etc/config/addons/www/sentinella)
  -n, --dry-run          Show what would be done, change nothing
      --skip-cron        Do not install or restart the cron job
      --skip-vars        Do not create the ReGa system variables
      --skip-web         Do not install the dashboard files
      --skip-test        Do not run a first check after deployment
      --demo             Deprecated: mock plugin cards are no longer shipped
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
        --demo)
            printf 'WARNING: --demo is deprecated; mock plugin cards are no longer deployed.\n' >&2
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
ADDON_DIR=${ADDON_DIR:-${DEPLOY_ADDON_DIR:-/usr/local/addons/sentinella}}
WEB_DIR=${WEB_DIR:-${DEPLOY_WEB_DIR:-/usr/local/etc/config/addons/www/sentinella}}

[ -n "$HOST" ] || die "no CCU host given (use -H or set DEPLOY_HOST in .env)"
[ -n "$SSH_USER" ] || die "empty SSH user"
case "$SSH_PORT" in
    ''|*[!0-9]*) die "SSH port must be numeric" ;;
esac
if [ "$SSH_PORT" -lt 1 ] || [ "$SSH_PORT" -gt 65535 ]; then
    die "SSH port out of range"
fi

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

for file in VERSION .env bin/check_soc.sh lib/notify_signal.sh setup_variables.sh \
            plugins.d/10_ccu_health.sh \
            plugins.d/15_weather.sh \
            plugins.d/20_renault_zoe.sh \
            plugins.d/30_forecast_solar.sh \
            config/plugins/weather.env.example \
            config/plugins/renault.env.example \
            config/plugins/forecast_solar.env.example \
            web/index.html web/style.css web/app.js \
            web/api/config.cgi web/api/settings.cgi \
            web/api/test_signal.cgi web/api/test_renault.cgi \
            web/api/action_zoe.cgi web/api/update_check.cgi \
            web/api/plugins.cgi web/api/plugin_manager.cgi; do
    [ -f "$ROOT/$file" ] || die "missing required file: $file"
done

ENV_MODE=$(stat -c %a "$ROOT/.env" 2>/dev/null || true)
if [ -n "$ENV_MODE" ] && [ "$ENV_MODE" != "600" ]; then
    printf 'WARNING: %s has mode %s, expected 600\n' "$ROOT/.env" "$ENV_MODE" >&2
fi

REMOTE="${SSH_USER}@${HOST}"
LOG_FILE_REMOTE=${LOG_FILE:-$ADDON_DIR/state/check_soc.log}
DASHBOARD_URL="http://${HOST}/addons/sentinella/"

if [ "$DRY_RUN" -eq 1 ]; then
    cat <<EOF
Dry run - nothing will be changed.

  SSH target    : ${REMOTE}:${SSH_PORT}
  Add-on dir    : ${ADDON_DIR}
  Dashboard dir : ${WEB_DIR}
  Dashboard URL : ${DASHBOARD_URL}
  Log file      : ${LOG_FILE_REMOTE}
  Cron          : $([ "$SKIP_CRON" -eq 1 ] && echo skipped || echo "ensure */2 * * * * ${ADDON_DIR}/bin/check_soc.sh")
  Ownership     : root:root for the add-on and dashboard trees
  Variables     : $([ "$SKIP_VARS" -eq 1 ] && echo skipped || echo "create via ReGa")
  Dashboard     : $([ "$SKIP_WEB" -eq 1 ] && echo skipped || echo "install")
  Settings API  : $([ "$SKIP_WEB" -eq 1 ] && echo skipped || echo "install + probe via lighttpd")
  First check   : $([ "$SKIP_TEST" -eq 1 ] && echo skipped || echo "run")
  Signal test   : $([ "$SKIP_TEST" -eq 1 ] && echo skipped || echo "run when SIGNAL_ENABLED=true")
  Plugins       : sync plugins.d/ (mode 755)
  Forecast cfg  : install forecast_solar.env.example, keep existing .env
  Mock cards    : purge legacy sample cards from state/plugins (never installed)
  Remote .env   : keep existing credentials, install only when missing
EOF
    exit 0
fi

STAGE=$(mktemp -d "${TMPDIR:-/tmp}/sentinella-deploy.XXXXXX")
cleanup() {
    rm -rf "$STAGE"
}
trap cleanup EXIT HUP INT TERM

mkdir -p "$STAGE/bin" "$STAGE/lib" "$STAGE/web/api" \
         "$STAGE/plugins.d" "$STAGE/config/plugins"
install -m 644 "$ROOT/VERSION"               "$STAGE/VERSION"
install -m 700 "$ROOT/bin/check_soc.sh"      "$STAGE/bin/check_soc.sh"
install -m 700 "$ROOT/lib/notify_signal.sh"  "$STAGE/lib/notify_signal.sh"
install -m 700 "$ROOT/setup_variables.sh"    "$STAGE/setup_variables.sh"
install -m 755 "$ROOT/plugins.d/10_ccu_health.sh" \
    "$STAGE/plugins.d/10_ccu_health.sh"
install -m 755 "$ROOT/plugins.d/15_weather.sh" \
    "$STAGE/plugins.d/15_weather.sh"
install -m 755 "$ROOT/plugins.d/20_renault_zoe.sh" \
    "$STAGE/plugins.d/20_renault_zoe.sh"
install -m 755 "$ROOT/plugins.d/30_forecast_solar.sh" \
    "$STAGE/plugins.d/30_forecast_solar.sh"
install -m 644 "$ROOT/config/plugins/weather.env.example" \
    "$STAGE/config/plugins/weather.env.example"
install -m 644 "$ROOT/config/plugins/renault.env.example" \
    "$STAGE/config/plugins/renault.env.example"
install -m 644 "$ROOT/config/plugins/forecast_solar.env.example" \
    "$STAGE/config/plugins/forecast_solar.env.example"
# Staged separately: an existing remote .env keeps its credentials.
install -m 600 "$ROOT/.env"                  "$STAGE/.env.new"
install -m 644 "$ROOT/web/index.html"        "$STAGE/web/index.html"
install -m 644 "$ROOT/web/style.css"         "$STAGE/web/style.css"
install -m 644 "$ROOT/web/app.js"            "$STAGE/web/app.js"
install -m 755 "$ROOT/web/api/config.cgi"       "$STAGE/web/api/config.cgi"
install -m 755 "$ROOT/web/api/settings.cgi"     "$STAGE/web/api/settings.cgi"
install -m 755 "$ROOT/web/api/test_signal.cgi"  "$STAGE/web/api/test_signal.cgi"
install -m 755 "$ROOT/web/api/test_renault.cgi" "$STAGE/web/api/test_renault.cgi"
install -m 755 "$ROOT/web/api/action_zoe.cgi"   "$STAGE/web/api/action_zoe.cgi"
install -m 755 "$ROOT/web/api/update_check.cgi" "$STAGE/web/api/update_check.cgi"
install -m 755 "$ROOT/web/api/plugin_manager.cgi" "$STAGE/web/api/plugin_manager.cgi"
install -m 755 "$ROOT/web/api/plugins.cgi"      "$STAGE/web/api/plugins.cgi"

ssh_ccu() {
    ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new \
        -p "$SSH_PORT" "$@"
}

info "checking SSH connection to $REMOTE"
ssh_ccu "$REMOTE" 'true' \
    || die "cannot connect to $REMOTE (check host, key and root login)"

info "copying add-on to $ADDON_DIR"
# shellcheck disable=SC2029  # the paths are intentionally expanded locally
tar -cf - -C "$STAGE" . \
    | ssh_ccu "$REMOTE" "mkdir -p '$ADDON_DIR' && tar -xf - -C '$ADDON_DIR'" \
    || die "file transfer failed"

info "configuring permissions, dashboard, cron and system variables"
# shellcheck disable=SC2029  # the paths are intentionally expanded locally
ssh_ccu "$REMOTE" \
    "ADDON_DIR='$ADDON_DIR' WEB_DIR='$WEB_DIR' SKIP_CRON='$SKIP_CRON' SKIP_VARS='$SKIP_VARS' SKIP_WEB='$SKIP_WEB' SKIP_TEST='$SKIP_TEST' sh -s" <<'REMOTE_SCRIPT'
set -e
umask 077

mkdir -p "$ADDON_DIR/state" "$ADDON_DIR/plugins.d" "$ADDON_DIR/config/plugins" \
         "$ADDON_DIR/state/plugins"
# The add-on root is never a web document root: only the isolated $WEB_DIR is
# served. Credentials, state and helpers therefore stay root-only.
chmod 700 "$ADDON_DIR" "$ADDON_DIR/bin" "$ADDON_DIR/lib" "$ADDON_DIR/state" \
    "$ADDON_DIR/plugins.d" "$ADDON_DIR/config" "$ADDON_DIR/config/plugins" \
    "$ADDON_DIR/state/plugins"
chmod 755 "$ADDON_DIR/bin/check_soc.sh" "$ADDON_DIR/lib/notify_signal.sh" "$ADDON_DIR/setup_variables.sh"

# Plugins are independently executable. Glob so future plugins inherit 755
# without another deploy.sh edit.
for _plugin in "$ADDON_DIR/plugins.d"/*.sh; do
    [ -f "$_plugin" ] || continue
    chmod 755 "$_plugin"
done

# Ship the plugin defaults as an example only. An operator's
# forecast_solar.env and renault.env must survive every redeployment untouched.
if [ -f "$ADDON_DIR/config/plugins/weather.env.example" ]; then
    chmod 644 "$ADDON_DIR/config/plugins/weather.env.example"
fi
if [ -f "$ADDON_DIR/config/plugins/weather.env" ]; then
    chmod 600 "$ADDON_DIR/config/plugins/weather.env" 2>/dev/null || true
fi
if [ -f "$ADDON_DIR/config/plugins/forecast_solar.env.example" ]; then
    chmod 644 "$ADDON_DIR/config/plugins/forecast_solar.env.example"
    if [ ! -f "$ADDON_DIR/config/plugins/forecast_solar.env" ]; then
        printf 'Plugin defaults available in %s/config/plugins/forecast_solar.env.example\n' \
            "$ADDON_DIR"
    fi
fi
if [ -f "$ADDON_DIR/config/plugins/renault.env.example" ]; then
    chmod 644 "$ADDON_DIR/config/plugins/renault.env.example"
fi
if [ -f "$ADDON_DIR/config/plugins/renault.env" ]; then
    chmod 600 "$ADDON_DIR/config/plugins/renault.env" 2>/dev/null || true
fi
if [ -f "$ADDON_DIR/config/plugins/forecast_solar.env" ]; then
    chmod 600 "$ADDON_DIR/config/plugins/forecast_solar.env" 2>/dev/null || true
fi
if [ -f "$ADDON_DIR/state/plugins/.renault_session" ]; then
    chmod 600 "$ADDON_DIR/state/plugins/.renault_session" 2>/dev/null || true
fi

# Production state carries real telemetry only. Purge sample cards left behind by
# older deployments and drop the fixture directory that used to ship with them.
for _mock in 01_zoe.json 02_forecast.json 03_wallbox.json; do
    rm -f "$ADDON_DIR/state/plugins/$_mock"
done
for _fixture_dir in "$ADDON_DIR/fixtures/dev" "$ADDON_DIR/fixtures"; do
    [ -d "$_fixture_dir" ] || continue
    rm -f "$_fixture_dir"/*.json 2>/dev/null || true
    rmdir "$_fixture_dir" 2>/dev/null || true
done
printf 'Mock plugin cards purged; state/plugins contains real plugins only.\n'

# Redeployments must never overwrite live credentials. Only the first install
# (or an intentionally removed .env) receives the workstation copy.
if [ -f "$ADDON_DIR/.env" ]; then
    rm -f "$ADDON_DIR/.env.new"
    printf 'Keeping existing %s/.env (credentials preserved).\n' "$ADDON_DIR"
else
    if [ ! -f "$ADDON_DIR/.env.new" ]; then
        printf 'WARNING: neither .env nor .env.new present in %s\n' "$ADDON_DIR" >&2
    else
        mv -f "$ADDON_DIR/.env.new" "$ADDON_DIR/.env"
        printf 'Installed new %s/.env from workstation.\n' "$ADDON_DIR"
    fi
fi

# Backwards compatibility: link legacy /usr/local/addons/sma_battery to /usr/local/addons/sentinella
if [ "$ADDON_DIR" = "/usr/local/addons/sentinella" ]; then
    ln -sfn /usr/local/addons/sentinella /usr/local/addons/sma_battery 2>/dev/null || true
fi
# Enforce ownership and secret permissions after the .env install. This keeps
# a fresh deploy from leaving the tree owned by avahi (tar extraction) and
# keeps credentials readable by root only, even after an interrupted run.
chmod 600 "$ADDON_DIR/.env" 2>/dev/null || true
chown -R root:root "$ADDON_DIR"
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
    # Some CCU3 / RaspberryMatic firmware maps .cgi to tclsh. Our handlers are
    # POSIX sh scripts with a shebang and cannot run under tclsh. Switch the
    # mapping to the shebang handler, but only when the old mapping is present
    # so redeployments stay idempotent and custom setups are left alone.
    patch_lighttpd_cgi() {
        _cgi_conf=/etc/lighttpd/conf.d/cgi.conf
        [ -f "$_cgi_conf" ] || return 0
        grep -q '"[.]cgi"[[:space:]]*=>[[:space:]]*"/bin/tclsh"' \
            "$_cgi_conf" 2>/dev/null || return 0

        _root_was_ro=0
        case "$(mount 2>/dev/null | grep ' on / type ' | head -n 1)" in
            *'(ro,'*|*'(ro)'*) _root_was_ro=1 ;;
        esac

        if ! mount -o remount,rw / 2>/dev/null; then
            printf 'WARNING: cannot remount / writable; left %s unchanged.\n' \
                "$_cgi_conf" >&2
            return 0
        fi

        _tmp="${_cgi_conf}.sma.$$"
        if sed 's#"[.]cgi"[[:space:]]*=>[[:space:]]*"/bin/tclsh"#".cgi" => ""#g' \
            "$_cgi_conf" > "$_tmp" 2>/dev/null &&
            ! grep -q '"[.]cgi"[[:space:]]*=>[[:space:]]*"/bin/tclsh"' "$_tmp" 2>/dev/null; then
            cat "$_tmp" > "$_cgi_conf"
            rm -f "$_tmp"
            if [ "$_root_was_ro" -eq 1 ]; then
                mount -o remount,ro / 2>/dev/null \
                    || printf 'WARNING: could not remount / read-only again.\n' >&2
            fi
            /etc/init.d/S50lighttpd restart >/dev/null 2>&1 || true
            printf 'Enabled POSIX CGI handler for .cgi in %s\n' "$_cgi_conf"
        else
            rm -f "$_tmp"
            if [ "$_root_was_ro" -eq 1 ]; then
                mount -o remount,ro / 2>/dev/null || true
            fi
            printf 'WARNING: could not patch %s; settings API may stay unavailable.\n' \
                "$_cgi_conf" >&2
        fi
    }
    patch_lighttpd_cgi

    mkdir -p "$WEB_DIR/api"
    chown -R root:root "$WEB_DIR"
    chmod 755 "$WEB_DIR" "$WEB_DIR/api"
    for file in index.html style.css app.js; do
        cp -f "$ADDON_DIR/web/$file" "$WEB_DIR/$file"
        chmod 644 "$WEB_DIR/$file"
    done

    # The CGI needs to find the add-on even though lighttpd serves it from a
    # different directory. The path is written once at deploy time.
    for file in config.cgi settings.cgi test_signal.cgi test_renault.cgi action_zoe.cgi update_check.cgi plugins.cgi; do
        cp -f "$ADDON_DIR/web/api/$file" "$WEB_DIR/api/$file"
        chmod 755 "$WEB_DIR/api/$file"
    done
    printf '%s\n' "$ADDON_DIR" > "$WEB_DIR/api/addon_dir"
    chmod 644 "$WEB_DIR/api/addon_dir"
    chown -R root:root "$WEB_DIR"
    printf 'Dashboard installed in %s\n' "$WEB_DIR"

    # Also mirror the web files and addon_dir into $ADDON_DIR so a direct
    # symlink (/www/addons/sentinella -> $ADDON_DIR) or the lighttpd add-on
    # root (/usr/local/etc/config/addons/www/sentinella -> $WEB_DIR) both serve
    # the dashboard, status.json and CGI endpoints without 404s.
    mkdir -p "$ADDON_DIR/api"
    chmod 755 "$ADDON_DIR/api"
    for file in index.html style.css app.js; do
        cp -f "$ADDON_DIR/web/$file" "$ADDON_DIR/$file"
        chmod 644 "$ADDON_DIR/$file"
    done
    for file in config.cgi settings.cgi test_signal.cgi test_renault.cgi action_zoe.cgi update_check.cgi plugins.cgi; do
        cp -f "$ADDON_DIR/web/api/$file" "$ADDON_DIR/api/$file"
        chmod 755 "$ADDON_DIR/api/$file"
    done
    printf '%s\n' "$ADDON_DIR" > "$ADDON_DIR/api/addon_dir"
    chmod 644 "$ADDON_DIR/api/addon_dir"
    ln -sfn "$WEB_DIR/status.json" "$ADDON_DIR/status.json" 2>/dev/null || true
    chown -h root:root "$ADDON_DIR/status.json" 2>/dev/null || true

    # Canonical /addons/sentinella/ and legacy /addons/sma_battery/ symlinks
    if [ "$ADDON_DIR" = "/usr/local/addons/sentinella" ]; then
        ln -sfn /usr/local/addons/sentinella /usr/local/addons/sma_battery 2>/dev/null || true
    fi
    if [ "$WEB_DIR" = "/usr/local/etc/config/addons/www/sentinella" ]; then
        ln -sfn /usr/local/etc/config/addons/www/sentinella /usr/local/etc/config/addons/www/sma_battery 2>/dev/null || true
    fi
    if [ -d /www/addons ]; then
        # Serve the isolated web root only. Pointing these symlinks at
        # $ADDON_DIR would expose .env, config/ and state/ over HTTP.
        ln -sfn "$WEB_DIR" /www/addons/sma_battery 2>/dev/null || true
        ln -sfn "$WEB_DIR" /www/addons/sentinella 2>/dev/null || true
    fi

    # Best-effort probe: CGI may need to be enabled in lighttpd on some
    # firmware versions. Never fail the deployment because of it.
    WEB_PATH="/addons/sentinella"
    if command -v curl >/dev/null 2>&1; then
        # A static .cgi download would not have the CGI Content-Type header.
        _content_type=$(curl -fsSL -m 5 -o /dev/null \
            -w '%{content_type}' \
            "http://127.0.0.1${WEB_PATH}/api/config.cgi" 2>/dev/null) || _content_type=
        case "$_content_type" in
            application/json*)
                printf 'Settings API reachable at %s/api/\n' "$WEB_PATH"
                ;;
            *)
                printf 'WARNING: settings API not reachable yet. Enable CGI for .cgi in lighttpd\n' >&2
                printf '         (see docs/TROUBLESHOOTING.md) and open %s again.\n' "$WEB_PATH" >&2
                ;;
        esac
    fi
fi

if [ "$SKIP_CRON" != "1" ]; then
    CRON_TARGET="$ADDON_DIR/bin/check_soc.sh"
    CRON_LINE="*/2 * * * * $CRON_TARGET >/dev/null 2>&1"

    # Match only active lines: a commented-out entry must not stop us from
    # re-enabling the schedule, and existing entries stay byte-for-byte intact.
    cron_has_target() {
        grep -v '^[[:space:]]*#' "$1" 2>/dev/null | grep -qF "$CRON_TARGET"
    }

    if command -v crontab >/dev/null 2>&1; then
        CRON_TMP=$ADDON_DIR/state/.cron.$$
        crontab -l > "$CRON_TMP" 2>/dev/null || : > "$CRON_TMP"
        if cron_has_target "$CRON_TMP"; then
            printf 'Cron entry already present; crontab left untouched.\n'
            rm -f "$CRON_TMP"
        else
            printf '%s\n' "$CRON_LINE" >> "$CRON_TMP"
            crontab "$CRON_TMP"
            rm -f "$CRON_TMP"
            printf 'Cron installed: %s\n' "$CRON_LINE"
        fi
    else
        CRON_FILE=/etc/crontabs/root
        mkdir -p /etc/crontabs
        touch "$CRON_FILE"
        if cron_has_target "$CRON_FILE"; then
            printf 'Cron entry already present in %s; left untouched.\n' "$CRON_FILE"
        else
            printf '%s\n' "$CRON_LINE" >> "$CRON_FILE"
            printf 'Cron installed in %s: %s\n' "$CRON_FILE" "$CRON_LINE"
        fi
    fi

    # BusyBox crond reads the crontab only when it starts. Restart it so a
    # freshly installed schedule runs immediately; fall back to the firmware
    # init script when crond is not a bare binary in PATH.
    killall crond 2>/dev/null || true
    crond 2>/dev/null \
        || /etc/init.d/S71crond restart >/dev/null 2>&1 \
        || /etc/init.d/S98crond restart >/dev/null 2>&1 \
        || true
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

    if [ -x "$ADDON_DIR/plugins.d/10_ccu_health.sh" ]; then
        if "$ADDON_DIR/plugins.d/10_ccu_health.sh"; then
            printf 'ccu_health plugin checked.\n'
        else
            printf 'WARNING: ccu_health plugin failed; existing card kept.\n' >&2
        fi
    fi

    if [ -x "$ADDON_DIR/plugins.d/15_weather.sh" ]; then
        if "$ADDON_DIR/plugins.d/15_weather.sh"; then
            printf 'weather plugin checked.\n'
        else
            printf 'WARNING: weather plugin failed; existing card kept.\n' >&2
        fi
    fi

    if [ -x "$ADDON_DIR/plugins.d/20_renault_zoe.sh" ]; then
        if "$ADDON_DIR/plugins.d/20_renault_zoe.sh"; then
            printf 'renault_zoe plugin checked.\n'
        else
            printf 'WARNING: renault_zoe plugin failed; existing card kept.\n' >&2
        fi
    fi

    # Populate the forecast card immediately. The plugin exits 0 when offline
    # and keeps any card that is already present, so this cannot fail a deploy.
    if [ -x "$ADDON_DIR/plugins.d/30_forecast_solar.sh" ]; then
        if "$ADDON_DIR/plugins.d/30_forecast_solar.sh"; then
            printf 'forecast.solar plugin refreshed.\n'
        else
            printf 'WARNING: forecast.solar plugin failed; existing card kept.\n' >&2
        fi
    fi

    # A --test message validates phone/apikey without touching state/signal.
    SIGNAL_TEST_ENABLED=$(
        . "$ADDON_DIR/.env" 2>/dev/null || exit 1
        [ "${SIGNAL_ENABLED:-false}" = "true" ] && printf 'yes' || printf 'no'
    ) || SIGNAL_TEST_ENABLED=no
    if [ "$SIGNAL_TEST_ENABLED" = "yes" ]; then
        printf 'Validating Signal credentials with a test message...\n'
        if "$ADDON_DIR/lib/notify_signal.sh" --test; then
            printf 'Signal test message sent successfully.\n'
        else
            printf 'WARNING: Signal test failed; check SIGNAL_PHONE/SIGNAL_APIKEY.\n' >&2
        fi
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
  2. Use the gear icon to edit thresholds, Signal credentials and the inverter
     address directly in the browser. No .env editing or terminal needed.
  3. Check the variables in CCU3: Status and Control -> System variables.
EOF
