#!/bin/sh
# =============================================================================
#  Sentinella - Modular Stateful Plugin Threshold Notification Helper
# =============================================================================
#  Provides reusable edge-triggered, hysteresis-guarded threshold notifications
#  for Sentinella plugins via CallMeBot / Signal gateway.
#
#  Usage (shell function or CLI):
#    notify_plugin_threshold "$plugin_id" "$metric" "$current_val" "$threshold_val" "$direction" "$message" ["$margin"]
#
#  Directions:
#    above - triggers when current_val >= threshold_val
#            resets when current_val <= (threshold_val - margin)
#    below - triggers when current_val <= threshold_val
#            resets when current_val >= (threshold_val + margin)
#
#  State file:
#    $STATE_DIR/notifications/${plugin_id}_${metric}.state
# =============================================================================

notify_plugin_threshold() {
    _plugin_id=$1
    _metric=$2
    _current_val=$3
    _threshold_val=$4
    _direction=$5
    _message=$6
    _margin=${7:-2}

    [ -n "$_plugin_id" ] || return 1
    [ -n "$_metric" ] || return 1
    [ -n "$_current_val" ] || return 1
    [ -n "$_threshold_val" ] || return 1

    case "$_direction" in
        above|below) ;;
        *) return 1 ;;
    esac

    # Sanitize IDs to avoid path traversal
    _clean_plugin=$(printf '%s' "$_plugin_id" | tr -cd 'A-Za-z0-9_-')
    _clean_metric=$(printf '%s' "$_metric" | tr -cd 'A-Za-z0-9_-')
    [ -n "$_clean_plugin" ] || return 1
    [ -n "$_clean_metric" ] || return 1

    # Determine STATE_DIR:
    # Prefer $NOTIFY_STATE_DIR, then $STATE_DIR.
    # If the provided directory ends in /plugins, use its parent state directory.
    _notif_base=${NOTIFY_STATE_DIR:-${STATE_DIR:-}}
    if [ -z "$_notif_base" ]; then
        if [ -n "${APP_DIR:-}" ]; then
            _notif_base="$APP_DIR/state"
        elif [ -n "${ADDON_DIR:-}" ]; then
            _notif_base="$ADDON_DIR/state"
        else
            _addon_cand=$(cd "$(dirname "$0")/.." 2>/dev/null && pwd)
            [ -n "$_addon_cand" ] && _notif_base="$_addon_cand/state" || _notif_base=/usr/local/addons/sentinella/state
        fi
    elif [ "${_notif_base##*/}" = "plugins" ]; then
        _notif_base="${_notif_base%/plugins}"
    fi

    _notif_dir="$_notif_base/notifications"
    _state_file="$_notif_dir/${_clean_plugin}_${_clean_metric}.state"

    # Evaluate transition using awk
    _action=$(awk -v c="$_current_val" -v t="$_threshold_val" -v m="$_margin" -v dir="$_direction" 'BEGIN {
        c_num = c + 0;
        t_num = t + 0;
        m_num = m + 0;
        if (dir == "above") {
            if (c_num >= t_num) {
                print "trigger";
            } else if (c_num <= (t_num - m_num)) {
                print "reset";
            } else {
                print "hold";
            }
        } else if (dir == "below") {
            if (c_num <= t_num) {
                print "trigger";
            } else if (c_num >= (t_num + m_num)) {
                print "reset";
            } else {
                print "hold";
            }
        } else {
            print "invalid";
        }
    }')

    _prev_state=0
    if [ -r "$_state_file" ]; then
        _val=$(head -n 1 "$_state_file" 2>/dev/null | tr -cd '0-9')
        [ "$_val" = "1" ] && _prev_state=1
    fi

    case "$_action" in
        trigger)
            if [ "$_prev_state" = "0" ]; then
                # Crossing threshold boundary: trigger notification!
                mkdir -p "$_notif_dir" 2>/dev/null || true
                printf '1\n' > "$_state_file.tmp.$$" 2>/dev/null \
                    && mv -f "$_state_file.tmp.$$" "$_state_file" 2>/dev/null || true
                chmod 600 "$_state_file" 2>/dev/null || true

                # Dispatch notification via existing Signal gateway
                _notify_script=
                for _cand in \
                    "${NOTIFY_SIGNAL_SCRIPT:-}" \
                    "${APP_DIR:-}/lib/notify_signal.sh" \
                    "${ADDON_DIR:-}/lib/notify_signal.sh" \
                    "$(dirname "$0")/notify_signal.sh" \
                    "$(dirname "$0")/../lib/notify_signal.sh" \
                    "/usr/local/addons/sentinella/lib/notify_signal.sh"
                do
                    if [ -n "$_cand" ] && [ -x "$_cand" ]; then
                        _notify_script=$_cand
                        break
                    elif [ -n "$_cand" ] && [ -f "$_cand" ]; then
                        _notify_script="sh $_cand"
                        break
                    fi
                done

                if [ -n "$_notify_script" ]; then
                    if [ "${NOTIFY_ASYNC:-1}" = "0" ]; then
                        $_notify_script --message "$_message"
                    else
                        $_notify_script --message "$_message" >/dev/null 2>&1 &
                    fi
                fi
                return 0
            fi
            # Already triggered: ignore subsequent runs while condition remains active
            return 0
            ;;
        reset)
            if [ "$_prev_state" = "1" ]; then
                # Safely returned back across threshold with hysteresis margin: reset/re-arm
                mkdir -p "$_notif_dir" 2>/dev/null || true
                printf '0\n' > "$_state_file.tmp.$$" 2>/dev/null \
                    && mv -f "$_state_file.tmp.$$" "$_state_file" 2>/dev/null || true
                chmod 600 "$_state_file" 2>/dev/null || true
            fi
            return 0
            ;;
        hold)
            # Inside deadband / hysteresis band: keep current state
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

case "${0##*/}" in
    plugin_notify.sh|notify.sh)
        if [ "$#" -ge 6 ]; then
            notify_plugin_threshold "$@"
        fi
        ;;
esac
