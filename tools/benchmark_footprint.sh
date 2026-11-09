#!/bin/sh
# =============================================================================
#  Sentinella - Automated Footprint & Benchmark Tool
# =============================================================================
#  Calculates exact tarball package size (KB), installed disk space footprint
#  (KB), and benchmarks execution time of `bin/check_soc.sh --dry-run` and
#  `plugins.d/20_renault_zoe.sh` (average of 5 runs in ms).
#
#  Prints a clean Markdown table with timestamp suitable for README.md and
#  enforces strict embedded budget guards:
#    - Tarball package size <= 200 KB (informational warning)
#    - Installed disk footprint <= 400 KB (409600 B)
#    - Core execution average <= 150 ms
#    - Plugin execution average <= 250 ms
#
#  Target : POSIX /bin/sh (BusyBox ash / dash / bash compatible)
# =============================================================================

PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 077

ROOT_DIR=$(cd "$(dirname "$0")/.." 2>/dev/null && pwd)
[ -n "$ROOT_DIR" ] || ROOT_DIR=.

MAX_TARBALL_KB=${BENCHMARK_MAX_TARBALL_KB:-200}
MAX_INSTALLED_KB=${BENCHMARK_MAX_INSTALLED_KB:-400}
MAX_INSTALLED_BYTES=$((MAX_INSTALLED_KB * 1024))
MAX_CORE_EXEC_MS=${BENCHMARK_MAX_CORE_EXEC_MS:-${BENCHMARK_MAX_EXEC_MS:-150}}
MAX_PLUGIN_EXEC_MS=${BENCHMARK_MAX_PLUGIN_EXEC_MS:-${BENCHMARK_MAX_EXEC_MS:-250}}
RUNS=${BENCHMARK_RUNS:-5}

WORK_DIR=$(mktemp -d "${TMPDIR:-/tmp}/sentinella-bench.XXXXXX" 2>/dev/null)
if [ -z "$WORK_DIR" ] || [ ! -d "$WORK_DIR" ]; then
    WORK_DIR="/tmp/sentinella-bench.$$"
    mkdir -p "$WORK_DIR" || exit 1
fi

cleanup() {
    rm -rf "$WORK_DIR" 2>/dev/null || true
}
trap cleanup EXIT HUP INT TERM

# ---------------------------------------------------------------------------
# 1. Stage runtime add-on payload & build release tarball
# ---------------------------------------------------------------------------
STAGE_DIR="$WORK_DIR/sentinella"
TARBALL="$WORK_DIR/sentinella-package.tar.gz"

mkdir -p \
    "$STAGE_DIR/bin" \
    "$STAGE_DIR/lib" \
    "$STAGE_DIR/plugins.d" \
    "$STAGE_DIR/config/plugins" \
    "$STAGE_DIR/web/api" \
    "$STAGE_DIR/state/plugins" || exit 1

cp -f "$ROOT_DIR/bin/check_soc.sh" "$STAGE_DIR/bin/check_soc.sh"
cp -f "$ROOT_DIR/lib/notify_signal.sh" "$STAGE_DIR/lib/notify_signal.sh"
cp -f "$ROOT_DIR/lib/plugin_notify.sh" "$STAGE_DIR/lib/plugin_notify.sh"
cp -f "$ROOT_DIR/lib/notify.sh" "$STAGE_DIR/lib/notify.sh"
cp -f "$ROOT_DIR/setup_variables.sh" "$STAGE_DIR/setup_variables.sh"
cp -f "$ROOT_DIR/plugins.d/10_ccu_health.sh" "$STAGE_DIR/plugins.d/10_ccu_health.sh"
cp -f "$ROOT_DIR/plugins.d/15_weather.sh" "$STAGE_DIR/plugins.d/15_weather.sh"
cp -f "$ROOT_DIR/plugins.d/20_renault_zoe.sh" "$STAGE_DIR/plugins.d/20_renault_zoe.sh"
cp -f "$ROOT_DIR/plugins.d/30_forecast_solar.sh" "$STAGE_DIR/plugins.d/30_forecast_solar.sh"
cp -f "$ROOT_DIR/config/plugins/weather.env.example" "$STAGE_DIR/config/plugins/weather.env.example"
cp -f "$ROOT_DIR/config/plugins/renault.env.example" "$STAGE_DIR/config/plugins/renault.env.example"
cp -f "$ROOT_DIR/config/plugins/ccu_health.env.example" "$STAGE_DIR/config/plugins/ccu_health.env.example"
cp -f "$ROOT_DIR/config/plugins/forecast_solar.env.example" "$STAGE_DIR/config/plugins/forecast_solar.env.example"
cp -f "$ROOT_DIR/.env.example" "$STAGE_DIR/.env.example"
cp -f "$ROOT_DIR/web/index.html" "$STAGE_DIR/web/index.html"
cp -f "$ROOT_DIR/web/style.css" "$STAGE_DIR/web/style.css"
cp -f "$ROOT_DIR/web/app.js" "$STAGE_DIR/web/app.js"
cp -f "$ROOT_DIR/web/api/config.cgi" "$STAGE_DIR/web/api/config.cgi"
cp -f "$ROOT_DIR/web/api/settings.cgi" "$STAGE_DIR/web/api/settings.cgi"
cp -f "$ROOT_DIR/web/api/test_signal.cgi" "$STAGE_DIR/web/api/test_signal.cgi"
cp -f "$ROOT_DIR/web/api/test_renault.cgi" "$STAGE_DIR/web/api/test_renault.cgi"
cp -f "$ROOT_DIR/web/api/action_zoe.cgi" "$STAGE_DIR/web/api/action_zoe.cgi"
cp -f "$ROOT_DIR/web/api/update_check.cgi" "$STAGE_DIR/web/api/update_check.cgi"
cp -f "$ROOT_DIR/web/api/plugins.cgi" "$STAGE_DIR/web/api/plugins.cgi"
cp -f "$ROOT_DIR/web/api/plugin_manager.cgi" "$STAGE_DIR/web/api/plugin_manager.cgi"

chmod 755 \
    "$STAGE_DIR/bin/check_soc.sh" \
    "$STAGE_DIR/lib/notify_signal.sh" \
    "$STAGE_DIR/lib/plugin_notify.sh" \
    "$STAGE_DIR/lib/notify.sh" \
    "$STAGE_DIR/setup_variables.sh" \
    "$STAGE_DIR/plugins.d/10_ccu_health.sh" \
    "$STAGE_DIR/plugins.d/15_weather.sh" \
    "$STAGE_DIR/plugins.d/20_renault_zoe.sh" \
    "$STAGE_DIR/plugins.d/30_forecast_solar.sh" \
    "$STAGE_DIR/web/api/"*.cgi 2>/dev/null || true

tar -czf "$TARBALL" -C "$STAGE_DIR" . 2>/dev/null || exit 1

# ---------------------------------------------------------------------------
# 2. Calculate tarball package size (KB) and installed disk footprint (KB)
# ---------------------------------------------------------------------------
TARBALL_BYTES=$(wc -c < "$TARBALL" 2>/dev/null | tr -d '[:space:]')
[ -n "$TARBALL_BYTES" ] || TARBALL_BYTES=0
TARBALL_KB=$(awk -v b="$TARBALL_BYTES" 'BEGIN { printf "%d", int((b + 1023) / 1024) }')

# Installed footprint of the runtime add-on tree (bin, lib, plugins.d, config,
# setup_variables.sh, and web/api CGI endpoints; static web assets live on the
# lighttpd document root).
INSTALLED_BYTES=$(
    find "$STAGE_DIR/bin" "$STAGE_DIR/lib" "$STAGE_DIR/plugins.d" \
         "$STAGE_DIR/config" "$STAGE_DIR/web/api" \
         "$STAGE_DIR/setup_variables.sh" "$STAGE_DIR/.env.example" \
         -type f -exec wc -c {} + 2>/dev/null \
    | awk 'END { print $1 + 0 }'
)
[ -n "$INSTALLED_BYTES" ] || INSTALLED_BYTES=0
INSTALLED_KB=$(awk -v b="$INSTALLED_BYTES" 'BEGIN { printf "%d", int((b + 1023) / 1024) }')

# ---------------------------------------------------------------------------
# 3. High-resolution millisecond timer helper (POSIX sh + /proc/uptime or date)
# ---------------------------------------------------------------------------
now_ms() {
    _ns=$(date +%s%N 2>/dev/null)
    case "$_ns" in
        *[!0-9]*|'')
            if [ -r /proc/uptime ]; then
                awk '{ printf "%.0f\n", $1 * 1000 }' /proc/uptime
            else
                printf '%s000\n' "$(date +%s)"
            fi
            ;;
        *)
            awk -v n="$_ns" 'BEGIN { printf "%.0f\n", n / 1000000 }'
            ;;
    esac
}

# Prepare a self-contained .env for check_soc.sh --dry-run
BENCH_ENV="$WORK_DIR/bench.env"
cat > "$BENCH_ENV" <<EOF
SMA_IP="127.0.0.1"
SMA_PORT="443"
SMA_ROLE="usr"
SMA_PASS="bench-secret"
CCU_IP="127.0.0.1"
CCU_REGA_PORT="8181"
THRESHOLD_TRIGGER="95"
THRESHOLD_RESET="90"
SIGNAL_ENABLED="false"
WEB_ENABLED="false"
LOG_FILE="$WORK_DIR/bench.log"
EOF
chmod 600 "$BENCH_ENV" 2>/dev/null || true

# Prepare mock fixture for 20_renault_zoe.sh so it benchmarks full config load,
# telemetry extraction, awk maths, and atomic JSON card write without network I/O.
RENAULT_BENCH_ENV="$STAGE_DIR/config/plugins/renault.env"
cat > "$RENAULT_BENCH_ENV" <<EOF
RENAULT_USER="bench@example.com"
RENAULT_PASS="bench-secret"
RENAULT_VIN="VF1AG000012345678"
EOF
chmod 600 "$RENAULT_BENCH_ENV" 2>/dev/null || true

RENAULT_MOCK_JSON="$WORK_DIR/mock_battery.json"
cat > "$RENAULT_MOCK_JSON" <<'EOF'
{"data":{"type":"Car","id":"VF1AG000012345678","attributes":{"timestamp":"2026-11-08T12:00:00Z","batteryLevel":78,"batteryAutonomy":214,"plugStatus":1,"chargingStatus":1.0}}}
EOF

# Renault is an optional plugin and disabled on clean installs. The benchmark
# enables it explicitly so the measurement covers the full telemetry path.
BENCH_PLUGINS_ENV="$WORK_DIR/bench-plugins.env"
printf "PLUGIN_RENAULT_ZOE='1'\n" > "$BENCH_PLUGINS_ENV"
chmod 600 "$BENCH_PLUGINS_ENV" 2>/dev/null || true

benchmark_cmd() {
    _total=0
    _i=1
    while [ "$_i" -le "$RUNS" ]; do
        _t0=$(now_ms)
        "$@" >/dev/null 2>&1
        _t1=$(now_ms)
        _diff=$((_t1 - _t0))
        [ "$_diff" -ge 0 ] || _diff=0
        _total=$((_total + _diff))
        _i=$((_i + 1))
    done
    awk -v tot="$_total" -v n="$RUNS" 'BEGIN { printf "%d", int((tot / n) + 0.5) }'
}

CHECK_SOC_MS=$(
    SMA_MONITOR_ENV="$BENCH_ENV" \
    benchmark_cmd "$STAGE_DIR/bin/check_soc.sh" --dry-run
)

RENAULT_ZOE_MS=$(
    RENAULT_ADDON_DIR="$STAGE_DIR" \
    RENAULT_ENV="$RENAULT_BENCH_ENV" \
    RENAULT_MOCK_BATTERY_JSON="$RENAULT_MOCK_JSON" \
    SENTINELLA_PLUGINS_ENV="$WORK_DIR/bench-plugins.env" \
    benchmark_cmd "$STAGE_DIR/plugins.d/20_renault_zoe.sh"
)

# ---------------------------------------------------------------------------
# 4. Evaluate budget guards & print Markdown report
# ---------------------------------------------------------------------------
BUDGET_FAILED=0

if [ "$TARBALL_KB" -gt "$MAX_TARBALL_KB" ]; then
    TARBALL_STATUS="WARN (> ${MAX_TARBALL_KB} KB)"
else
    TARBALL_STATUS="Informational"
fi

if [ "$INSTALLED_BYTES" -gt "$MAX_INSTALLED_BYTES" ] || [ "$INSTALLED_KB" -gt "$MAX_INSTALLED_KB" ]; then
    INSTALLED_STATUS="FAIL (> ${MAX_INSTALLED_KB} KB)"
    BUDGET_FAILED=1
else
    INSTALLED_STATUS="PASS (<= ${MAX_INSTALLED_KB} KB)"
fi

if [ "$CHECK_SOC_MS" -gt "$MAX_CORE_EXEC_MS" ]; then
    CHECK_SOC_STATUS="FAIL (> ${MAX_CORE_EXEC_MS} ms)"
    BUDGET_FAILED=1
else
    CHECK_SOC_STATUS="PASS (<= ${MAX_CORE_EXEC_MS} ms)"
fi

if [ "$RENAULT_ZOE_MS" -gt "$MAX_PLUGIN_EXEC_MS" ]; then
    RENAULT_STATUS="FAIL (> ${MAX_PLUGIN_EXEC_MS} ms)"
    BUDGET_FAILED=1
else
    RENAULT_STATUS="PASS (<= ${MAX_PLUGIN_EXEC_MS} ms)"
fi

TIMESTAMP=$(date -u '+%Y-%m-%d %H:%M:%S UTC' 2>/dev/null || date)

cat <<EOF
### Sentinella Footprint & Benchmark Report

_Generated: ${TIMESTAMP}_

| Metric / Target | Measured | Budget Guard | Status |
| :--- | :---: | :---: | :---: |
| Release Tarball Package Size | **${TARBALL_KB} KB** (${TARBALL_BYTES} B) | <= ${MAX_TARBALL_KB} KB (Informational) | ${TARBALL_STATUS} |
| Installed Add-on Footprint | **${INSTALLED_KB} KB** (${INSTALLED_BYTES} B) | <= ${MAX_INSTALLED_KB} KB (${MAX_INSTALLED_BYTES} B) | ${INSTALLED_STATUS} |
| \`bin/check_soc.sh --dry-run\` (avg of ${RUNS} runs) | **${CHECK_SOC_MS} ms** | <= ${MAX_CORE_EXEC_MS} ms | ${CHECK_SOC_STATUS} |
| \`plugins.d/20_renault_zoe.sh\` (avg of ${RUNS} runs) | **${RENAULT_ZOE_MS} ms** | <= ${MAX_PLUGIN_EXEC_MS} ms | ${RENAULT_STATUS} |
EOF

if [ "$BUDGET_FAILED" -ne 0 ]; then
    printf '\nERROR: Footprint or execution time exceeded budget guard.\n' >&2
    exit 1
fi

exit 0
