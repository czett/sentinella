/* ==========================================================================
   homematic-sma-battery-monitor — dashboard logic
   Vanilla ES6, no dependencies. Reads ./status.json.
   ========================================================================== */

(function () {
    "use strict";

    var $ = function (id) { return document.getElementById(id); };

    var STATUS_URL = new URL("./status.json", window.location.href);
    var CONFIG_URL = new URL("./api/config.cgi", window.location.href);
    var TEST_URL = new URL("./api/test_signal.cgi", window.location.href);

    var POLL_MS = 30000;
    var RELATIVE_MS = 10000;
    var STALE_AFTER_SECONDS = 600;
    var TEST_POLL_MS = 1000;
    var TEST_POLL_LIMIT = 30;

    var demoRequested =
        new URLSearchParams(window.location.search).has("demo") ||
        window.location.protocol === "file:";

    var current = null;
    var pollTimer = null;
    var relativeTimer = null;
    var loading = false;

    var settingsOpen = false;
    var settingsAvailable = true;
    var settingsBusy = false;
    var settingsFocusBefore = null;
    var thresholdOverride = null;
    var toastTimer = null;
    var liveConfig = null;

    var pullStart = null;
    var pullDistance = 0;
    var pullActive = false;

    var el = {
        main: $("main"),
        mainContent: $("main-content"),
        pull: $("pull"),
        pullLabel: $("pull-label"),

        connection: $("connection"),
        connectionText: $("connection-text"),
        lastUpdate: $("last-update"),

        notice: $("notice"),
        noticeTitle: $("notice-title"),
        noticeText: $("notice-text"),
        retry: $("retry"),

        gauge: $("gauge"),
        socValue: $("soc-value"),
        socState: $("soc-state"),
        socCaption: $("soc-caption"),

        signalChip: $("signal-chip"),
        signalCopy: $("signal-copy"),

        rail: $("rail"),
        railMarkerLabel: $("rail-marker-label"),
        railResetValue: $("rail-reset-value"),
        railTriggerValue: $("rail-trigger-value"),

        chargeState: $("charge-state"),
        powerState: $("power-state"),
        ccuState: $("ccu-state"),
        metricLast: $("metric-last"),

        chart: $("chart"),
        chartLayer: $("chart-layer"),
        chartEmpty: $("chart-empty"),
        chartRange: $("chart-range"),
        historyCount: $("history-count"),

        metaHost: $("meta-host"),
        metaSocVar: $("meta-soc-var"),
        metaSignalVar: $("meta-signal-var"),
        metaNotify: $("meta-notify"),
        metaLast: $("meta-last"),

        settingsOpen: $("settings-open"),
        settingsLayer: $("settings-layer"),
        settingsBackdrop: $("settings-backdrop"),
        settingsDrawer: $("settings-drawer"),
        settingsClose: $("settings-close"),
        settingsForm: $("settings-form"),
        settingsStatus: $("settings-status"),
        settingsSave: $("settings-save"),
        settingsTest: $("settings-test"),

        triggerSoc: $("trigger-soc"),
        triggerRange: $("trigger-range"),
        resetSoc: $("reset-soc"),
        resetRange: $("reset-range"),
        signalEnabled: $("signal-enabled"),
        signalPhone: $("signal-phone"),
        signalApikey: $("signal-apikey"),
        apikeyHint: $("apikey-hint"),
        smaIp: $("sma-ip"),
        smaPort: $("sma-port"),
        smaPassword: $("sma-password"),
        smaPasswordHint: $("sma-password-hint"),

        toast: $("toast")
    };

    /* ----------------------------------------------------------------------
       Helpers
       ---------------------------------------------------------------------- */

    function clamp(value, min, max) {
        return Math.min(max, Math.max(min, value));
    }

    function toNumber(value) {
        return typeof value === "number" && Number.isFinite(value) ? value : null;
    }

    function setText(node, value) {
        if (node) {
            node.textContent = value;
        }
    }

    function formatRelative(iso) {
        var date = new Date(iso);
        if (Number.isNaN(date.getTime())) {
            return "not yet";
        }
        var seconds = Math.max(0, Math.round((Date.now() - date.getTime()) / 1000));
        if (seconds < 60) {
            return seconds + "s ago";
        }
        var minutes = Math.round(seconds / 60);
        if (minutes < 60) {
            return minutes + "m ago";
        }
        var hours = Math.round(minutes / 60);
        if (hours < 24) {
            return hours + "h ago";
        }
        return Math.round(hours / 24) + "d ago";
    }

    function formatTime(iso) {
        var date = new Date(iso);
        if (Number.isNaN(date.getTime())) {
            return "—";
        }
        return date.toLocaleString(undefined, {
            day: "2-digit",
            month: "short",
            hour: "2-digit",
            minute: "2-digit"
        });
    }

    function formatTimeShort(epochSeconds, spanSeconds) {
        var options = spanSeconds > 86400
            ? { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }
            : { hour: "2-digit", minute: "2-digit" };
        return new Date(epochSeconds * 1000).toLocaleTimeString(undefined, options);
    }

    function chargeLabel(value) {
        switch (value) {
            case "charging": return "Charging";
            case "discharging": return "Discharging";
            case "idle": return "Idle";
            default: return "Unknown";
        }
    }

    function chargeTag(value) {
        return chargeLabel(value).toUpperCase();
    }

    function signalLabel(data) {
        if (data.signal === true) {
            return "Active";
        }
        if (data.signal === false) {
            return "Inactive";
        }
        return "Unknown";
    }

    function signalTone(data) {
        if (data.signal === true) {
            return "active";
        }
        if (data.signal === false) {
            return "inactive";
        }
        return "unknown";
    }

    function normalizeThresholds(data) {
        if (thresholdOverride && Date.now() < thresholdOverride.until) {
            return {
                trigger: thresholdOverride.trigger,
                reset: thresholdOverride.reset
            };
        }
        var trigger = toNumber(data.thresholds && data.thresholds.trigger);
        var reset = toNumber(data.thresholds && data.thresholds.reset);
        if (trigger === null) {
            trigger = 95;
        }
        if (reset === null) {
            reset = 90;
        }
        trigger = clamp(trigger, 0, 100);
        reset = clamp(reset, 0, 100);
        if (reset >= trigger) {
            trigger = 95;
            reset = 90;
        }
        return { trigger: trigger, reset: reset };
    }

    function setConnection(state, label) {
        el.connection.dataset.state = state;
        el.connectionText.textContent = label;
    }

    function showNotice(tone, title, text, offerRetry) {
        el.notice.hidden = false;
        el.notice.dataset.tone = tone;
        el.noticeTitle.textContent = title;
        el.noticeText.textContent = text;
        el.retry.hidden = !offerRetry;
    }

    function hideNotice() {
        el.notice.hidden = true;
    }

    function powerText(data) {
        var charge = toNumber(data.charge_power_w);
        var discharge = toNumber(data.discharge_power_w);

        if (charge !== null && charge > 0 && discharge !== null && discharge > 0) {
            return Math.round(charge) + " W in / " + Math.round(discharge) + " W out";
        }
        if (charge !== null && charge > 0) {
            return Math.round(charge) + " W charging";
        }
        if (discharge !== null && discharge > 0) {
            return Math.round(discharge) + " W discharging";
        }
        if (charge === 0 || discharge === 0) {
            return "0 W";
        }
        return "Not reported";
    }

    function setCcuState(status) {
        if (status === "ok") {
            el.ccuState.textContent = "Written";
            el.ccuState.dataset.tone = "ok";
        } else if (status === "error") {
            el.ccuState.textContent = "Write failed";
            el.ccuState.dataset.tone = "error";
        } else {
            el.ccuState.textContent = "—";
            delete el.ccuState.dataset.tone;
        }
    }

    function updateLastUpdate(iso) {
        el.lastUpdate.dateTime = iso || "";
        el.lastUpdate.textContent = formatRelative(iso);
        el.lastUpdate.title = formatTime(iso);
    }

    function updateRelativeTime() {
        if (current && current.generated_at) {
            el.lastUpdate.textContent = formatRelative(current.generated_at);
        }
    }

    /* ----------------------------------------------------------------------
       Rendering
       ---------------------------------------------------------------------- */

    function render(data) {
        var soc = data.soc;
        var thresholds = normalizeThresholds(data);
        var history = Array.isArray(data.history) ? data.history : [];
        var charge = data.charging || "unknown";
        var last = formatTime(data.generated_at);

        current = data;
        document.body.dataset.state = "ready";

        el.socValue.textContent = String(soc);
        el.socState.textContent = chargeTag(charge);
        el.socState.dataset.charge = charge;
        el.gauge.setAttribute(
            "aria-label",
            "Battery state of charge " + soc + " percent, " + chargeLabel(charge).toLowerCase()
        );
        el.socCaption.textContent = captionFor(data, thresholds);

        el.signalChip.textContent = signalLabel(data);
        el.signalChip.dataset.tone = signalTone(data);
        el.signalCopy.textContent = signalCopyFor(data, thresholds);

        el.rail.style.setProperty("--soc", String(soc));
        el.rail.style.setProperty("--reset", String(thresholds.reset));
        el.rail.style.setProperty("--trigger", String(thresholds.trigger));
        el.rail.dataset.active = data.signal === true ? "true" : "false";
        el.rail.setAttribute(
            "aria-label",
            "State of charge " + soc + " percent. Reset at " +
            thresholds.reset + " percent, trigger at " + thresholds.trigger + " percent."
        );
        el.railMarkerLabel.textContent = soc + " %";
        el.railResetValue.textContent = String(thresholds.reset);
        el.railTriggerValue.textContent = String(thresholds.trigger);

        el.chargeState.textContent = chargeLabel(charge);
        el.chargeState.dataset.charge = charge;
        el.powerState.textContent = powerText(data);
        setCcuState(data.ccu_status);
        el.metricLast.textContent = last;

        el.metaHost.textContent = data.host || "—";
        el.metaSocVar.textContent = data.variables && data.variables.soc
            ? data.variables.soc
            : "—";
        el.metaSignalVar.textContent = data.variables && data.variables.signal
            ? data.variables.signal
            : "—";
        el.metaNotify.textContent = data.signal_notifications ? "Enabled" : "Disabled";
        el.metaLast.textContent = last;

        el.historyCount.textContent = history.length
            ? history.length + (history.length === 1 ? " sample" : " samples")
            : "No samples";
        el.historyCount.dataset.tone = "unknown";

        renderChart(history, thresholds);
        updateLastUpdate(data.generated_at);
    }

    function captionFor(data, thresholds) {
        if (data.signal === true) {
            return "At " + data.soc + " % the signal is active. It resets at " +
                thresholds.reset + " % or below.";
        }
        if (data.signal === false) {
            return "At " + data.soc + " % the signal is inactive. It triggers at " +
                thresholds.trigger + " % or above.";
        }
        return "No definitive signal state yet. Between " + thresholds.reset +
            " % and " + thresholds.trigger + " % the previous state is held.";
    }

    function signalCopyFor(data, thresholds) {
        if (data.signal === true) {
            if (data.signal_state === "triggered") {
                return "Triggered at " + data.soc + " %. Active until the state of charge falls to " +
                    thresholds.reset + " % or below.";
            }
            if (data.signal_state === "held") {
                return "Held active inside the hysteresis band. Resets at " +
                    thresholds.reset + " % or below.";
            }
            return "Active. Resets at " + thresholds.reset + " % or below.";
        }
        if (data.signal === false) {
            if (data.signal_state === "reset") {
                return "Reset at " + data.soc + " %. Triggers again at " +
                    thresholds.trigger + " % or above.";
            }
            return "Inactive. Triggers at " + thresholds.trigger + " % or above.";
        }
        return "The boolean is held between " + thresholds.reset +
            " % and " + thresholds.trigger + " % when no previous state is known.";
    }

    /* ----------------------------------------------------------------------
       History chart
       ---------------------------------------------------------------------- */

    function svgNode(tag, attributes, text) {
        var node = document.createElementNS("http://www.w3.org/2000/svg", tag);
        Object.keys(attributes || {}).forEach(function (key) {
            node.setAttribute(key, attributes[key]);
        });
        if (text !== undefined) {
            node.textContent = text;
        }
        return node;
    }

    function renderChart(history, thresholds) {
        var width = 760;
        var height = 220;
        var pad = { top: 14, right: 10, bottom: 28, left: 30 };
        var plotWidth = width - pad.left - pad.right;
        var plotHeight = height - pad.top - pad.bottom;
        var points = [];

        while (el.chartLayer.firstChild) {
            el.chartLayer.removeChild(el.chartLayer.firstChild);
        }

        (history || []).forEach(function (entry) {
            var t = toNumber(entry && entry.t);
            var soc = toNumber(entry && entry.soc);
            if (t !== null && soc !== null && soc >= 0 && soc <= 100) {
                points.push({ t: t, soc: soc });
            }
        });

        points.sort(function (a, b) { return a.t - b.t; });

        if (!points.length) {
            el.chartEmpty.hidden = false;
            el.chartRange.textContent = "No data";
            el.chart.setAttribute("aria-label", "No state of charge history yet");
            return;
        }

        el.chartEmpty.hidden = true;

        var first = points[0];
        var last = points[points.length - 1];
        var span = Math.max(1, last.t - first.t);

        function x(t) {
            if (points.length === 1) {
                return pad.left + plotWidth / 2;
            }
            return pad.left + ((t - first.t) / span) * plotWidth;
        }

        function y(value) {
            return pad.top + (1 - value / 100) * plotHeight;
        }

        [0, 25, 50, 75, 100].forEach(function (value) {
            var lineY = y(value);
            el.chartLayer.appendChild(svgNode("line", {
                x1: pad.left,
                y1: lineY,
                x2: width - pad.right,
                y2: lineY,
                "class": "chart-grid"
            }));
            el.chartLayer.appendChild(svgNode("text", {
                x: pad.left - 8,
                y: lineY + 3,
                "class": "chart-label",
                "text-anchor": "end"
            }, String(value)));
        });

        [thresholds.reset, thresholds.trigger].forEach(function (value) {
            var lineY = y(clamp(value, 0, 100));
            el.chartLayer.appendChild(svgNode("line", {
                x1: pad.left,
                y1: lineY,
                x2: width - pad.right,
                y2: lineY,
                "class": "chart-threshold"
            }));
        });

        var linePath = points.map(function (point, index) {
            return (index ? "L" : "M") + x(point.t).toFixed(1) + "," + y(point.soc).toFixed(1);
        }).join(" ");

        var baseY = y(0);
        var areaPath = "M" + x(first.t).toFixed(1) + "," + baseY.toFixed(1) + " " +
            points.map(function (point) {
                return "L" + x(point.t).toFixed(1) + "," + y(point.soc).toFixed(1);
            }).join(" ") +
            " L" + x(last.t).toFixed(1) + "," + baseY.toFixed(1) + " Z";

        el.chartLayer.appendChild(svgNode("path", { d: areaPath, "class": "chart-area" }));
        el.chartLayer.appendChild(svgNode("path", { d: linePath, "class": "chart-line" }));
        el.chartLayer.appendChild(svgNode("circle", {
            cx: x(last.t),
            cy: y(last.soc),
            r: 3.5,
            "class": "chart-dot"
        }));

        var spanSeconds = last.t - first.t;
        el.chartLayer.appendChild(svgNode("text", {
            x: pad.left,
            y: height - 6,
            "class": "chart-label",
            "text-anchor": "start"
        }, formatTimeShort(first.t, spanSeconds)));
        el.chartLayer.appendChild(svgNode("text", {
            x: width - pad.right,
            y: height - 6,
            "class": "chart-label",
            "text-anchor": "end"
        }, formatTimeShort(last.t, spanSeconds)));

        el.chartRange.textContent =
            formatTimeShort(first.t, spanSeconds) + " – " +
            formatTimeShort(last.t, spanSeconds);
        el.chart.setAttribute(
            "aria-label",
            "State of charge history, " + points.length + " samples, latest " +
            last.soc + " percent at " + formatTimeShort(last.t, spanSeconds) + "."
        );
    }

    /* ----------------------------------------------------------------------
       Loading and connection states
       ---------------------------------------------------------------------- */

    function validatePayload(data) {
        if (!data || typeof data !== "object") {
            throw new Error("empty payload");
        }
        if (!Number.isInteger(data.soc) || data.soc < 0 || data.soc > 100) {
            throw new Error("invalid SoC");
        }
    }

    function schedule(delay) {
        clearTimeout(pollTimer);
        pollTimer = setTimeout(load, delay);
    }

    function load() {
        if (loading) {
            return;
        }
        loading = true;
        clearTimeout(pollTimer);

        if (!current) {
            document.body.dataset.state = "loading";
            setConnection("loading", "Connecting");
        }

        var url = new URL(STATUS_URL.href);
        url.searchParams.set("t", String(Date.now()));

        fetch(url.toString(), { cache: "no-store" })
            .then(function (response) {
                if (!response.ok) {
                    var error = new Error("HTTP " + response.status);
                    error.status = response.status;
                    throw error;
                }
                return response.json();
            })
            .then(function (data) {
                validatePayload(data);
                render(data);
                hideNotice();

                var age = (Date.now() - new Date(data.generated_at).getTime()) / 1000;
                if (Number.isFinite(age) && age > STALE_AFTER_SECONDS) {
                    setConnection("stale", "Stale");
                } else {
                    setConnection("live", "Live");
                }
                schedule(POLL_MS);
            })
            .catch(function (error) {
                if (demoRequested) {
                    renderDemo();
                    return;
                }
                if (current) {
                    setConnection("stale", "Stale");
                    showNotice(
                        "warning",
                        "Showing the last known state",
                        "The latest request failed (" + error.message +
                        "). The dashboard retries automatically.",
                        true
                    );
                } else if (error.status === 404) {
                    setConnection("empty", "No data");
                    showNotice(
                        "empty",
                        "Waiting for the first measurement",
                        "The dashboard reads status.json, which the monitor writes after " +
                        "its first successful inverter read. This can take up to two " +
                        "minutes after installation.",
                        true
                    );
                } else {
                    setConnection("error", "Unavailable");
                    showNotice(
                        "error",
                        "Cannot read status.json",
                        "The dashboard could not load its data (" + error.message +
                        "). Check that the monitor has run and that lighttpd serves the " +
                        "add-on directory.",
                        true
                    );
                }
                schedule(POLL_MS);
            })
            .finally(function () {
                loading = false;
            });
    }

    function demoData() {
        var now = Math.floor(Date.now() / 1000);
        var values = [
            76, 78, 81, 83, 86, 88, 89, 91, 92, 93, 94, 95,
            94, 93, 91, 89, 88, 87, 86, 85, 84, 83, 82, 81
        ];
        var history = values.map(function (soc, index) {
            return { t: now - (values.length - 1 - index) * 600, soc: soc };
        });

        return {
            schema: 1,
            generated_at: new Date().toISOString(),
            generated_epoch: now,
            host: "192.168.178.123 (demo)",
            soc: 81,
            previous_soc: 82,
            delta: -1,
            charging: "discharging",
            charge_power_w: 0,
            discharge_power_w: 420,
            signal: false,
            signal_state: "reset",
            signal_notifications: false,
            thresholds: { trigger: 95, reset: 90 },
            variables: { soc: "SV_Batterie_SoC", signal: "SV_Batterie_95_Signal" },
            ccu_status: "ok",
            history: history
        };
    }

    function renderDemo() {
        render(demoData());
        setConnection("demo", "Demo");
        el.lastUpdate.textContent = "demo";
        showNotice(
            "warning",
            "Demo data",
            "status.json is not reachable. These values are generated for preview only; " +
            "open the dashboard through the CCU3 to see live measurements.",
            false
        );
    }

    /* ----------------------------------------------------------------------
       Settings drawer
       ---------------------------------------------------------------------- */

    function requestJSON(url, options) {
        var opts = Object.assign(
            { cache: "no-store", credentials: "same-origin" },
            options || {}
        );

        return fetch(url.toString(), opts).then(function (response) {
            return response.text().then(function (text) {
                var data = {};
                if (text) {
                    try {
                        data = JSON.parse(text);
                    } catch (error) {
                        data = {};
                    }
                }
                if (!response.ok) {
                    var failure = new Error(
                        (data && data.error) ||
                        ("Request failed (HTTP " + response.status + ").")
                    );
                    failure.status = response.status;
                    failure.data = data;
                    throw failure;
                }
                return data;
            });
        });
    }

    function showToast(tone, text) {
        el.toast.hidden = false;
        el.toast.dataset.tone = tone || "success";
        el.toast.textContent = text;
        clearTimeout(toastTimer);
        toastTimer = setTimeout(function () {
            el.toast.hidden = true;
        }, 6000);
    }

    function showSettingsStatus(tone, text) {
        if (!text) {
            el.settingsStatus.hidden = true;
            el.settingsStatus.textContent = "";
            delete el.settingsStatus.dataset.tone;
            return;
        }
        el.settingsStatus.hidden = false;
        el.settingsStatus.dataset.tone = tone || "pending";
        el.settingsStatus.textContent = text;
    }

    function setSettingsBusy(busy) {
        settingsBusy = busy;
        el.settingsSave.disabled = busy;
        el.settingsTest.disabled = busy;
        el.settingsForm.setAttribute("aria-busy", busy ? "true" : "false");
    }

    function fillSettingsForm(data) {
        el.triggerSoc.value = String(data.trigger_soc);
        el.resetSoc.value = String(data.reset_soc);
        el.triggerRange.value = String(data.trigger_soc);
        el.resetRange.value = String(data.reset_soc);

        el.signalEnabled.checked = data.signal_enabled === true;
        el.signalPhone.value = data.signal_phone || "";
        el.signalApikey.value = "";
        el.signalApikey.placeholder = data.signal_apikey_set ? "Stored · keep" : "Required";
        el.apikeyHint.hidden = !data.signal_apikey_set;

        el.smaIp.value = data.sma_ip || "";
        el.smaPort.value = String(data.sma_port || 443);
        el.smaPassword.value = "";
        el.smaPassword.placeholder = data.sma_password_set ? "Stored · keep" : "Required";
        el.smaPasswordHint.hidden = !data.sma_password_set;
    }

    function adoptConfig(data) {
        liveConfig = data;
        if (typeof data.trigger_soc !== "number" || typeof data.reset_soc !== "number") {
            return;
        }
        thresholdOverride = {
            trigger: data.trigger_soc,
            reset: data.reset_soc,
            until: Date.now() + 180000
        };
        if (current) {
            current.thresholds = {
                trigger: data.trigger_soc,
                reset: data.reset_soc
            };
            current.signal_notifications = data.signal_enabled === true;
            render(current);
        }
    }

    function validateSettings() {
        var triggerRaw = el.triggerSoc.value.trim();
        var resetRaw = el.resetSoc.value.trim();
        var trigger = Number(triggerRaw);
        var reset = Number(resetRaw);

        if (!triggerRaw || !Number.isInteger(trigger) || trigger < 0 || trigger > 100) {
            return {
                field: "trigger-soc",
                text: "Trigger level must be a whole number between 0 and 100."
            };
        }
        if (!resetRaw || !Number.isInteger(reset) || reset < 0 || reset > 100) {
            return {
                field: "reset-soc",
                text: "Reset level must be a whole number between 0 and 100."
            };
        }
        if (reset >= trigger) {
            return {
                field: "reset-soc",
                text: "Reset level must be lower than the trigger level."
            };
        }

        var phone = el.signalPhone.value.trim();
        if (phone && !/^(\+[1-9]\d{6,14}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/i.test(phone)) {
            return {
                field: "signal-phone",
                text: "Enter an E.164 phone number (for example +491701234567) or a Signal UUID."
            };
        }

        var apikey = el.signalApikey.value.trim();
        if (el.signalEnabled.checked) {
            if (!phone) {
                return {
                    field: "signal-phone",
                    text: "A phone number or Signal UUID is required when alerts are enabled."
                };
            }
            if (!apikey && !(liveConfig && liveConfig.signal_apikey_set)) {
                return {
                    field: "signal-apikey",
                    text: "A CallMeBot API key is required when alerts are enabled."
                };
            }
        }

        var ip = el.smaIp.value.trim();
        if (!ip || !/^[A-Za-z0-9._-]+$/.test(ip) || ip.indexOf("..") >= 0) {
            return {
                field: "sma-ip",
                text: "Enter a valid inverter IPv4 address or hostname."
            };
        }

        var portRaw = el.smaPort.value.trim();
        var port = Number(portRaw);
        if (!portRaw || !Number.isInteger(port) || port < 1 || port > 65535) {
            return {
                field: "sma-port",
                text: "Inverter port must be a whole number between 1 and 65535."
            };
        }

        return null;
    }

    function buildPayload() {
        var payload = {
            trigger_soc: Number(el.triggerSoc.value.trim()),
            reset_soc: Number(el.resetSoc.value.trim()),
            signal_enabled: el.signalEnabled.checked,
            signal_phone: el.signalPhone.value.trim(),
            sma_ip: el.smaIp.value.trim(),
            sma_port: Number(el.smaPort.value.trim())
        };

        var apikey = el.signalApikey.value.trim();
        if (apikey) {
            payload.signal_apikey = apikey;
        }
        var password = el.smaPassword.value;
        if (password) {
            payload.sma_password = password;
        }
        return payload;
    }

    function saveSettings(silent) {
        if (!settingsAvailable) {
            return Promise.reject(new Error(
                "Settings are not available. Open the dashboard through the CCU3."
            ));
        }

        var invalid = validateSettings();
        if (invalid) {
            var field = $(invalid.field);
            if (field) {
                field.focus();
            }
            return Promise.reject(new Error(invalid.text));
        }

        if (!silent) {
            showSettingsStatus("pending", "Saving configuration…");
        }

        return requestJSON(CONFIG_URL, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(buildPayload())
        }).then(function (data) {
            fillSettingsForm(data);
            adoptConfig(data);
            if (!silent) {
                showSettingsStatus(
                    "success",
                    "Configuration saved. The monitor applies it on its next run."
                );
            }
            return data;
        });
    }

    function pollTestResult(attempt) {
        return new Promise(function (resolve, reject) {
            if (attempt > TEST_POLL_LIMIT) {
                reject(new Error(
                    "The test is taking too long. Check state/notify_test.log on the CCU."
                ));
                return;
            }
            requestJSON(TEST_URL).then(function (data) {
                if (data.status === "sent") {
                    resolve(data);
                    return;
                }
                if (data.status === "failed") {
                    reject(new Error(data.error || "Test notification failed."));
                    return;
                }
                if (data.status === "idle" && attempt > 3) {
                    reject(new Error(
                        "The test runner did not start. Check state/notify_test.log on the CCU."
                    ));
                    return;
                }
                setTimeout(function () {
                    pollTestResult(attempt + 1).then(resolve, reject);
                }, TEST_POLL_MS);
            }, reject);
        });
    }

    function sendTestNotification() {
        return requestJSON(TEST_URL, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: "{}"
        }).then(function (response) {
            if (response.status === "sent") {
                return response;
            }
            return pollTestResult(0);
        });
    }

    function verifySignalAfterSave() {
        // Runs after the save has completed and must not lock the form: the
        // user can keep editing while the CCU delivers the test message.
        showSettingsStatus("pending", "Configuration saved. Sending a test message…");
        showToast("pending", "Configuration saved. Testing Signal…");

        sendTestNotification().then(function () {
            var success = "Settings saved. Test message sent to your Signal app!";
            showSettingsStatus("success", success);
            showToast("success", success);
        }).catch(function (error) {
            var warning =
                "Settings saved, but test message failed. Check phone/UUID and API key.";
            showSettingsStatus("error", warning + " " + error.message);
            showToast("error", warning);
        });
    }

    function runTest() {
        if (settingsBusy) {
            return;
        }
        setSettingsBusy(true);
        showSettingsStatus("pending", "Saving configuration before the test…");

        saveSettings(true).then(function () {
            showSettingsStatus("pending", "Sending test notification…");
            return sendTestNotification();
        }).then(function (result) {
            var message = result.message || "Test notification sent.";
            showSettingsStatus("success", message);
            showToast("success", message);
        }).catch(function (error) {
            showSettingsStatus("error", error.message);
            showToast("error", error.message);
        }).finally(function () {
            setSettingsBusy(false);
        });
    }

    function loadSettings() {
        settingsAvailable = true;
        setSettingsBusy(false);
        showSettingsStatus("pending", "Loading settings…");

        requestJSON(CONFIG_URL).then(function (data) {
            liveConfig = data;
            fillSettingsForm(data);
            showSettingsStatus("", "");
        }).catch(function () {
            settingsAvailable = false;
            el.settingsSave.disabled = true;
            el.settingsTest.disabled = true;
            showSettingsStatus(
                "error",
                "Settings are not available. Open the dashboard through the CCU3 and " +
                "make sure CGI is enabled for the add-on directory."
            );
        });
    }

    function openSettings() {
        if (settingsOpen) {
            return;
        }
        settingsOpen = true;
        settingsFocusBefore = document.activeElement;
        el.settingsLayer.hidden = false;
        el.settingsOpen.setAttribute("aria-expanded", "true");
        document.body.classList.add("drawer-open");
        window.setTimeout(function () {
            el.settingsDrawer.focus();
        }, 30);
        loadSettings();
    }

    function closeSettings() {
        if (!settingsOpen) {
            return;
        }
        settingsOpen = false;
        el.settingsLayer.hidden = true;
        el.settingsOpen.setAttribute("aria-expanded", "false");
        document.body.classList.remove("drawer-open");
        showSettingsStatus("", "");
        if (settingsFocusBefore && typeof settingsFocusBefore.focus === "function") {
            settingsFocusBefore.focus();
        }
    }

    /* ----------------------------------------------------------------------
       Pull to refresh (mobile)
       ---------------------------------------------------------------------- */

    function setPull(distance) {
        pullDistance = distance;
        el.mainContent.style.transition = "none";
        el.mainContent.style.transform = "translate3d(0," + distance.toFixed(1) + "px,0)";
        el.pull.dataset.active = distance > 4 ? "true" : "false";
        el.pull.dataset.ready = distance >= 52 ? "true" : "false";
        el.pullLabel.textContent = distance >= 52 ? "Release to refresh" : "Pull to refresh";
    }

    function resetPull() {
        pullDistance = 0;
        pullActive = false;
        el.pull.dataset.active = "false";
        el.pull.dataset.ready = "false";
        el.mainContent.style.transition = "transform 220ms cubic-bezier(0.2, 0.7, 0.2, 1)";
        el.mainContent.style.transform = "";
        setTimeout(function () {
            el.mainContent.style.transition = "";
        }, 240);
    }

    el.main.addEventListener("touchstart", function (event) {
        if (settingsOpen || event.touches.length !== 1 || el.main.scrollTop > 0) {
            return;
        }
        pullStart = event.touches[0].clientY;
        pullDistance = 0;
    }, { passive: true });

    el.main.addEventListener("touchmove", function (event) {
        if (pullStart === null) {
            return;
        }
        var dy = event.touches[0].clientY - pullStart;
        if (dy <= 0 || el.main.scrollTop > 0) {
            if (pullDistance) {
                resetPull();
            }
            pullStart = null;
            return;
        }
        if (dy < 6) {
            return;
        }
        if (event.cancelable) {
            event.preventDefault();
        }
        pullActive = true;
        setPull(Math.min(84, (dy - 6) * 0.45));
    }, { passive: false });

    el.main.addEventListener("touchend", function () {
        if (pullStart === null) {
            return;
        }
        pullStart = null;
        if (pullActive && pullDistance >= 52) {
            el.pullLabel.textContent = "Refreshing";
            el.pull.dataset.ready = "true";
            resetPull();
            clearTimeout(pollTimer);
            load();
        } else {
            resetPull();
        }
        pullActive = false;
    });

    el.main.addEventListener("touchcancel", function () {
        pullStart = null;
        resetPull();
    });

    /* ----------------------------------------------------------------------
       Wiring
       ---------------------------------------------------------------------- */

    el.triggerSoc.addEventListener("input", function () {
        if (el.triggerSoc.value !== "") {
            el.triggerRange.value = el.triggerSoc.value;
        }
    });

    el.triggerRange.addEventListener("input", function () {
        el.triggerSoc.value = el.triggerRange.value;
    });

    el.resetSoc.addEventListener("input", function () {
        if (el.resetSoc.value !== "") {
            el.resetRange.value = el.resetSoc.value;
        }
    });

    el.resetRange.addEventListener("input", function () {
        el.resetSoc.value = el.resetRange.value;
    });

    el.retry.addEventListener("click", function () {
        clearTimeout(pollTimer);
        load();
    });

    el.settingsOpen.addEventListener("click", openSettings);
    el.settingsClose.addEventListener("click", closeSettings);
    el.settingsBackdrop.addEventListener("click", closeSettings);
    el.settingsTest.addEventListener("click", runTest);

    el.settingsForm.addEventListener("submit", function (event) {
        event.preventDefault();
        if (settingsBusy) {
            return;
        }
        setSettingsBusy(true);
        saveSettings(false).then(function (data) {
            if (data.signal_enabled === true) {
                // A save that leaves Signal enabled is verified right away, so
                // a wrong phone/UUID or API key surfaces immediately instead
                // of waiting for the battery to reach the trigger level.
                verifySignalAfterSave();
            } else {
                showToast("success", "Configuration saved.");
            }
        }).catch(function (error) {
            showSettingsStatus("error", error.message);
            showToast("error", error.message);
        }).finally(function () {
            setSettingsBusy(false);
        });
    });

    el.settingsLayer.addEventListener("keydown", function (event) {
        if (event.key === "Escape") {
            event.preventDefault();
            closeSettings();
            return;
        }
        if (event.key !== "Tab") {
            return;
        }
        var focusable = el.settingsDrawer.querySelectorAll(
            'button:not([disabled]), input:not([disabled]), select:not([disabled]), ' +
            'textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])'
        );
        if (!focusable.length) {
            return;
        }
        var first = focusable[0];
        var last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
    });

    document.addEventListener("visibilitychange", function () {
        if (document.visibilityState === "visible" && !demoRequested) {
            load();
        }
    });

    relativeTimer = setInterval(updateRelativeTime, RELATIVE_MS);

    if (demoRequested) {
        renderDemo();
    } else {
        load();
    }
})();
