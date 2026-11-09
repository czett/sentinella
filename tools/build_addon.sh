#!/bin/sh
# =============================================================================
#  Sentinella - modular CCU3 energy and system monitor packager
# =============================================================================
#  Builds a deterministic HomeMatic CCU3 / RaspberryMatic installation package
#  for SMA storage, Renault E-Tech, CCU3 host telemetry, weather, and alerts:
#    dist/sentinella-<version>.tar.gz
#
#  Archive structure:
#    - update_script             (mode 755, CCU3 standard installer hook)
#    - uninstall.sh              (mode 755, idempotent lifecycle remover)
#    - rc.d/sentinella           (mode 755, CCU3 startup/info rc.d script)
#    - VERSION                   (mode 644)
#    - bin/check_soc.sh          (mode 755)
#    - lib/notify_signal.sh      (mode 755)
#    - setup_variables.sh        (mode 755)
#    - plugins.d/*.sh            (mode 755)
#    - config/plugins/*.example  (mode 644)
#    - .env.example              (mode 644)
#    - web/index.html, style.css, app.js, status.example.json (mode 644)
#    - web/favicon.svg, favicon.ico (mode 644)
#    - web/api/*.cgi             (mode 755)
# =============================================================================

set -eu
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 022

ROOT_DIR=$(cd "$(dirname "$0")/.." 2>/dev/null && pwd)
[ -n "$ROOT_DIR" ] || ROOT_DIR=.

extract_version() {
    _ver=${SENTINELLA_VERSION:-}
    if [ -z "$_ver" ] && [ -r "$ROOT_DIR/VERSION" ]; then
        _ver=$(sed -n '1p' "$ROOT_DIR/VERSION" 2>/dev/null | tr -d '[:space:]')
    fi
    if [ -z "$_ver" ] && [ -r "$ROOT_DIR/sentinella.conf" ]; then
        _ver=$(sed -n 's/^[[:space:]]*VERSION[[:space:]]*=[[:space:]]*//p' "$ROOT_DIR/sentinella.conf" 2>/dev/null | head -n 1 | tr -d '"\047[:space:]')
    fi
    _ver=${_ver#v}
    case "$_ver" in
        ''|*[!0-9A-Za-z._-]*) _ver="1.1.0" ;;
    esac
    printf '%s\n' "$_ver"
}

VERSION=$(extract_version)
DIST_DIR=${SENTINELLA_DIST_DIR:-$ROOT_DIR/dist}
OUT_TAR="$DIST_DIR/sentinella-$VERSION.tar.gz"

STAGE_DIR=$(mktemp -d "${TMPDIR:-/tmp}/sentinella-build.XXXXXX" 2>/dev/null)
if [ -z "$STAGE_DIR" ] || [ ! -d "$STAGE_DIR" ]; then
    STAGE_DIR="/tmp/sentinella-build.$$"
    rm -rf "$STAGE_DIR"
    mkdir -p "$STAGE_DIR"
fi

cleanup() {
    rm -rf "$STAGE_DIR" 2>/dev/null || true
}
trap cleanup EXIT HUP INT TERM

PKG_DIR="$STAGE_DIR/pkg"
mkdir -p \
    "$PKG_DIR/rc.d" \
    "$PKG_DIR/bin" \
    "$PKG_DIR/lib" \
    "$PKG_DIR/plugins.d" \
    "$PKG_DIR/config/plugins" \
    "$PKG_DIR/web/api" \
    "$DIST_DIR"

printf '%s\n' "$VERSION" > "$PKG_DIR/VERSION"

# 1. Generate CCU3 standard installer hook: update_script
cat > "$PKG_DIR/update_script" <<'EOF'
#!/bin/sh
# =============================================================================
#  Sentinella - HomeMatic CCU3 / RaspberryMatic Add-on Installer (update_script)
# =============================================================================
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 077

SRC_DIR=$(cd "$(dirname "$0")" 2>/dev/null && pwd)
[ -n "$SRC_DIR" ] || SRC_DIR=.

ADDON_DIR=${SENTINELLA_ADDON_DIR:-/usr/local/addons/sentinella}
WEB_DIR=${SENTINELLA_WEB_DIR:-/usr/local/etc/config/addons/www/sentinella}
RCD_DIR=${SENTINELLA_RCD_DIR:-/usr/local/etc/config/rc.d}

mkdir -p \
    "$ADDON_DIR/bin" \
    "$ADDON_DIR/lib" \
    "$ADDON_DIR/plugins.d" \
    "$ADDON_DIR/config/plugins" \
    "$ADDON_DIR/state/plugins" \
    "$ADDON_DIR/web/api" \
    "$WEB_DIR/api" \
    "$RCD_DIR"

cp -f "$SRC_DIR/VERSION" "$ADDON_DIR/VERSION" 2>/dev/null || true
cp -f "$SRC_DIR/bin/check_soc.sh" "$ADDON_DIR/bin/check_soc.sh"
for _lib in "$SRC_DIR/lib"/*.sh; do
    [ -f "$_lib" ] || continue
    cp -f "$_lib" "$ADDON_DIR/lib/$(basename "$_lib")"
    chmod 755 "$ADDON_DIR/lib/$(basename "$_lib")"
done
cp -f "$SRC_DIR/setup_variables.sh" "$ADDON_DIR/setup_variables.sh"
cp -f "$SRC_DIR/.env.example" "$ADDON_DIR/.env.example"

for _p in "$SRC_DIR/plugins.d"/*.sh; do
    [ -f "$_p" ] || continue
    cp -f "$_p" "$ADDON_DIR/plugins.d/$(basename "$_p")"
    chmod 755 "$ADDON_DIR/plugins.d/$(basename "$_p")"
done

for _ex in "$SRC_DIR/config/plugins"/*.example; do
    [ -f "$_ex" ] || continue
    cp -f "$_ex" "$ADDON_DIR/config/plugins/$(basename "$_ex")"
    chmod 644 "$ADDON_DIR/config/plugins/$(basename "$_ex")"
done

if [ ! -f "$ADDON_DIR/.env" ]; then
    cp -f "$SRC_DIR/.env.example" "$ADDON_DIR/.env"
fi
chmod 600 "$ADDON_DIR/.env" 2>/dev/null || true

for _w in index.html style.css app.js favicon.svg favicon.ico status.example.json; do
    [ -f "$SRC_DIR/web/$_w" ] || continue
    cp -f "$SRC_DIR/web/$_w" "$ADDON_DIR/web/$_w"
    cp -f "$SRC_DIR/web/$_w" "$WEB_DIR/$_w"
    chmod 644 "$ADDON_DIR/web/$_w" "$WEB_DIR/$_w"
done

for _cgi in "$SRC_DIR/web/api"/*.cgi; do
    [ -f "$_cgi" ] || continue
    _base=$(basename "$_cgi")
    cp -f "$_cgi" "$ADDON_DIR/web/api/$_base"
    cp -f "$_cgi" "$WEB_DIR/api/$_base"
    chmod 755 "$ADDON_DIR/web/api/$_base" "$WEB_DIR/api/$_base"
done

printf '%s\n' "$ADDON_DIR" > "$WEB_DIR/api/addon_dir"
chmod 644 "$WEB_DIR/api/addon_dir"

if [ -f "$SRC_DIR/rc.d/sentinella" ]; then
    cp -f "$SRC_DIR/rc.d/sentinella" "$RCD_DIR/sentinella"
    chmod 755 "$RCD_DIR/sentinella"
fi

if [ -f "$SRC_DIR/uninstall.sh" ]; then
    cp -f "$SRC_DIR/uninstall.sh" "$ADDON_DIR/uninstall.sh"
    chmod 755 "$ADDON_DIR/uninstall.sh"
fi

chmod 700 "$ADDON_DIR" "$ADDON_DIR/bin" "$ADDON_DIR/lib" "$ADDON_DIR/state" \
    "$ADDON_DIR/plugins.d" "$ADDON_DIR/config" "$ADDON_DIR/config/plugins" \
    "$ADDON_DIR/state/plugins" 2>/dev/null || true
chmod 755 "$ADDON_DIR/bin/check_soc.sh" "$ADDON_DIR/lib/notify_signal.sh" \
    "$ADDON_DIR/setup_variables.sh" "$WEB_DIR" "$WEB_DIR/api" 2>/dev/null || true

if [ -d /www/addons ]; then
    ln -sfn "$WEB_DIR" /www/addons/sentinella 2>/dev/null || true
fi

exit 0
EOF

# 2. Generate CCU3 rc.d startup/control script
cat > "$PKG_DIR/rc.d/sentinella" <<'EOF'
#!/bin/sh
# =============================================================================
#  Sentinella - HomeMatic CCU3 rc.d lifecycle script
# =============================================================================
ADDON_DIR=/usr/local/addons/sentinella
WEB_DIR=/usr/local/etc/config/addons/www/sentinella
VERSION_FILE=$ADDON_DIR/VERSION

get_version() {
    if [ -r "$VERSION_FILE" ]; then
        sed -n '1p' "$VERSION_FILE" 2>/dev/null | tr -d '[:space:]'
    else
        printf '1.1.0\n'
    fi
}

case "${1:-}" in
    ""|start)
        if [ -x "$ADDON_DIR/bin/check_soc.sh" ]; then
            "$ADDON_DIR/bin/check_soc.sh" >/dev/null 2>&1 &
        fi
        ;;
    stop)
        ;;
    restart)
        "$0" stop
        "$0" start
        ;;
    info)
        _ver=$(get_version)
        printf 'Info: <b>Sentinella</b><br>Leichtgewichtiges Dashboard und Benachrichtigungssystem f&uuml;r PV-Speicher (SMA), E-Mobilit&auml;t (Renault E-Tech), Systemgesundheit und Wetter.<br>\n'
        printf 'Name: Sentinella\n'
        printf 'Version: %s\n' "$_ver"
        printf 'Operations: uninstall\n'
        printf 'Config-Url: /addons/sentinella/\n'
        printf 'Update: /addons/sentinella/api/update_check.cgi\n'
        ;;
    uninstall)
        if [ -x "$ADDON_DIR/uninstall.sh" ]; then
            "$ADDON_DIR/uninstall.sh"
        else
            rm -rf "$ADDON_DIR" "$WEB_DIR" /www/addons/sentinella \
                /usr/local/etc/config/rc.d/sentinella 2>/dev/null || true
        fi
        ;;
    *)
        printf 'Usage: %s {start|stop|restart|info|uninstall}\n' "$0" >&2
        exit 1
        ;;
esac
exit 0
EOF

# 3. Copy add-on scripts, plugins, config templates, and web assets
cp -f "$ROOT_DIR/bin/check_soc.sh" "$PKG_DIR/bin/check_soc.sh"
for _lib in "$ROOT_DIR/lib"/*.sh; do
    [ -f "$_lib" ] || continue
    cp -f "$_lib" "$PKG_DIR/lib/$(basename "$_lib")"
    chmod 755 "$PKG_DIR/lib/$(basename "$_lib")"
done
cp -f "$ROOT_DIR/setup_variables.sh" "$PKG_DIR/setup_variables.sh"
cp -f "$ROOT_DIR/package/uninstall.sh" "$PKG_DIR/uninstall.sh"
cp -f "$ROOT_DIR/.env.example" "$PKG_DIR/.env.example"
cp -f "$ROOT_DIR/LICENSE" "$PKG_DIR/LICENSE"
cp -f "$ROOT_DIR/README.md" "$PKG_DIR/README.md"

for _plugin in "$ROOT_DIR/plugins.d"/*.sh; do
    [ -f "$_plugin" ] || continue
    cp -f "$_plugin" "$PKG_DIR/plugins.d/$(basename "$_plugin")"
done

for _cfg in "$ROOT_DIR/config/plugins"/*.example; do
    [ -f "$_cfg" ] || continue
    cp -f "$_cfg" "$PKG_DIR/config/plugins/$(basename "$_cfg")"
done

for _web in index.html style.css app.js favicon.svg favicon.ico status.example.json; do
    [ -f "$ROOT_DIR/web/$_web" ] || continue
    cp -f "$ROOT_DIR/web/$_web" "$PKG_DIR/web/$_web"
done

for _cgi in "$ROOT_DIR/web/api"/*.cgi; do
    [ -f "$_cgi" ] || continue
    cp -f "$_cgi" "$PKG_DIR/web/api/$(basename "$_cgi")"
done

# 4. Enforce strict file modes inside archive:
#    - Directories: 755
#    - Regular files: 644
#    - Executables / CGIs / update_script / rc.d: 755
find "$PKG_DIR" -type d -exec chmod 755 {} +
find "$PKG_DIR" -type f -exec chmod 644 {} +

chmod 755 \
    "$PKG_DIR/update_script" \
    "$PKG_DIR/uninstall.sh" \
    "$PKG_DIR/rc.d/sentinella" \
    "$PKG_DIR/bin/check_soc.sh" \
    "$PKG_DIR/lib/notify_signal.sh" \
    "$PKG_DIR/setup_variables.sh" \
    "$PKG_DIR/plugins.d"/*.sh \
    "$PKG_DIR/web/api"/*.cgi

# Normalize mtimes for deterministic archive output
find "$PKG_DIR" -exec touch -t 202611080000.00 {} + 2>/dev/null || true

# 5. Build deterministic gzipped tarball
rm -f "$OUT_TAR"
(
    cd "$PKG_DIR"
    FILE_LIST=$(find . -mindepth 1 | sed 's|^\./||' | LC_ALL=C sort)
    if tar --version 2>/dev/null | grep -q 'GNU tar'; then
        # shellcheck disable=SC2086
        tar --owner=0 --group=0 --numeric-owner --no-recursion -cf - $FILE_LIST | gzip -n -9 > "$OUT_TAR"
    else
        # shellcheck disable=SC2086
        tar -cf - $FILE_LIST | gzip -n -9 > "$OUT_TAR"
    fi
)

[ -s "$OUT_TAR" ] || {
    printf 'ERROR: Failed to create %s\n' "$OUT_TAR" >&2
    exit 1
}

TAR_BYTES=$(wc -c < "$OUT_TAR" | tr -d '[:space:]')
printf 'Built %s (%s bytes, version %s)\n' "$OUT_TAR" "$TAR_BYTES" "$VERSION"
exit 0
