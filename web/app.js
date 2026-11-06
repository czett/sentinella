/* ==========================================================================
   homematic-sma-battery-monitor - dashboard logic
   Vanilla JavaScript, no dependencies. Reads ./status.json.
   ========================================================================== */

(function () {
    "use strict";

    var STATUS_URL = new URL("./status.json", window.location.href);
    var POLL_MS = 30000;
    var RELATIVE_MS = 30000;
    var CIRCUMFERENCE = 2 * Math.PI * 100;
    var STALE_AFTER_SECONDS = 600;

    var demoRequested =
        new URLSearchParams(window.location.search).has("demo") ||
        window.location.protocol === "file:";

    var current = null;
    var pollTimer = null;
    var relativeTimer = null;
    var loading = false;

    var el = {
        connection: document.getElementById("connection"),
        connectionText: document.getElementById("connection-text"),
        lastUpdate: document.getElementById("last-update"),

        notice: document.getElementById("notice"),
        noticeTitle: document.getElementById("notice-title"),
        noticeText: document.getElementById("notice-text"),
        retry: document.getElementById("retry"),

        gauge: document.getElementById("gauge"),
        gaugeProgress: document.getElementById("gauge-progress"),
        socValue: document.getElementById("soc-value"),
        socState: document.getElementById("soc-state"),
        socCaption: document.getElementById("soc-caption"),

        signalChip: document.getElementById("signal-chip"),
        signalCopy: document.getElementById("signal-copy"),

        rail: document.getElementById("rail"),
        railMarkerLabel: document.getElementById("rail-marker-label"),
        railResetValue: document.getElementById("rail-reset-value"),
        railTriggerValue: document.getElementById("rail-trigger-value"),

        chargeState: document.getElementById("charge-state"),
        powerState: document.getElementById("power-state"),
        ccuState: document.getElementById("ccu-state"),

        chart: document.getElementById("chart"),
        chartLayer: document.getElementById("chart-layer"),
        chartEmpty: document.getElementById("chart-empty"),
        chartRange: document.getElementById("chart-range"),
        historyCount: document.getElementById("history-count"),

        metaHost: document.getElementById("meta-host"),
        metaSocVar: document.getElementById("meta-soc-var"),
        metaSignalVar: document.getElementById("meta-signal-var"),
        metaNotify: document.getElementById("meta-notify"),
        metaLast: document.getElementById("meta-last")
    };

    el.gaugeProgress.style.strokeDasharray = CIRCUMFERENCE + " " + CIRCUMFERENCE;
    el.gaugeProgress.style.strokeDashoffset = String(CIRCUMFERENCE);

    /* ----------------------------------------------------------------------
       Small helpers
       ---------------------------------------------------------------------- */

    function clamp(value, min, max) {
        return Math.min(max, Math.max(min, value));
    }

    function toNumber(value) {
        return typeof value === "number" && Number.isFinite(value) ? value : null;
    }

    function formatRelative(iso) {
        var date = new Date(iso);
        if (Number.isNaN(date.getTime())) {
            return "Not available";
        }
        var seconds = Math.round((Date.now() - date.getTime()) / 1000);
        var abs = Math.abs(seconds);
        if (abs < 45) {
            return "just now";
        }
        var minutes = Math.round(abs / 60);
        if (minutes < 60) {
            return minutes + " min " + (seconds > 0 ? "ago" : "from now");
        }
        var hours = Math.round(minutes / 60);
        if (hours < 24) {
            return hours + " h " + (seconds > 0 ? "ago" : "from now");
        }
        var days = Math.round(hours / 24);
        return days + " d " + (seconds > 0 ? "ago" : "from now");
    }

    function formatTime(iso) {
        var date = new Date(iso);
        if (Number.isNaN(date.getTime())) {
            return "Not available";
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

    function formatWatts(value) {
        if (value === null) {
            return "Not available";
        }
        return Math.round(value) + " W";
    }

    function chargeLabel(value) {
        switch (value) {
            case "charging":
                return "Charging";
            case "discharging":
                return "Discharging";
            case "idle":
                return "Idle";
            default:
                return "Unknown";
        }
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

    function setText(node, value) {
        if (node) {
            node.textContent = value;
        }
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

    /* ----------------------------------------------------------------------
       Rendering
       ---------------------------------------------------------------------- */

    function render(data) {
        var soc = data.soc;
        var thresholds = normalizeThresholds(data);
        var history = Array.isArray(data.history) ? data.history : [];
        var chargeState = chargeLabel(data.charging);

        current = data;
        document.body.dataset.state = "ready";

        el.socValue.textContent = String(soc);
        el.socState.textContent = chargeState;
        el.gauge.setAttribute("aria-label", "Battery state of charge " + soc + " percent");
        el.gaugeProgress.style.strokeDashoffset = String(
            CIRCUMFERENCE * (1 - soc / 100)
        );

        el.socCaption.textContent = captionFor(data, thresholds);

        el.signalChip.textContent = signalLabel(data);
        el.signalChip.dataset.tone = signalTone(data);
        el.signalCopy.textContent = signalCopyFor(data, thresholds);

        el.rail.style.setProperty("--soc", String(soc));
        el.rail.style.setProperty("--reset", String(thresholds.reset));
        el.rail.style.setProperty("--trigger", String(thresholds.trigger));
        el.rail.setAttribute(
            "aria-label",
            "State of charge " + soc + " percent. Reset at " +
            thresholds.reset + " percent, trigger at " + thresholds.trigger + " percent."
        );
        el.railMarkerLabel.textContent = soc + " %";
        el.railResetValue.textContent = String(thresholds.reset);
        el.railTriggerValue.textContent = String(thresholds.trigger);

        el.chargeState.textContent = chargeState;
        el.powerState.textContent = powerText(data);
        setCcuState(data.ccu_status);

        el.metaHost.textContent = data.host || "Not available";
        el.metaSocVar.textContent = data.variables && data.variables.soc
            ? data.variables.soc
            : "Not available";
        el.metaSignalVar.textContent = data.variables && data.variables.signal
            ? data.variables.signal
            : "Not available";
        el.metaNotify.textContent = data.signal_notifications ? "Enabled" : "Disabled";
        el.metaLast.textContent = formatTime(data.generated_at);

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
                return "Triggered at " + data.soc + " %. It stays active until the SoC falls to " +
                    thresholds.reset + " % or below.";
            }
            if (data.signal_state === "held") {
                return "Held active inside the hysteresis band. It resets at " +
                    thresholds.reset + " % or below.";
            }
            return "Active. It resets at " + thresholds.reset + " % or below.";
        }
        if (data.signal === false) {
            if (data.signal_state === "reset") {
                return "Reset at " + data.soc + " %. It triggers again at " +
                    thresholds.trigger + " % or above.";
            }
            return "Inactive. It triggers at " + thresholds.trigger + " % or above.";
        }
        return "The monitor holds the boolean between " + thresholds.reset +
            " % and " + thresholds.trigger + " % when no previous state is known.";
    }

    function powerText(data) {
        var charge = toNumber(data.charge_power_w);
        var discharge = toNumber(data.discharge_power_w);

        if (charge !== null && charge > 0 && discharge !== null && discharge > 0) {
            return formatWatts(charge) + " in / " + formatWatts(discharge) + " out";
        }
        if (charge !== null && charge > 0) {
            return formatWatts(charge) + " charging";
        }
        if (discharge !== null && discharge > 0) {
            return formatWatts(discharge) + " discharging";
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
            el.ccuState.textContent = "Not available";
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
        var height = 240;
        var pad = { top: 18, right: 14, bottom: 28, left: 36 };
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

        points.sort(function (a, b) {
            return a.t - b.t;
        });

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
                y: lineY + 4,
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

        el.chartLayer.appendChild(svgNode("path", {
            d: areaPath,
            "class": "chart-area"
        }));
        el.chartLayer.appendChild(svgNode("path", {
            d: linePath,
            "class": "chart-line"
        }));
        el.chartLayer.appendChild(svgNode("circle", {
            cx: x(last.t),
            cy: y(last.soc),
            r: 4,
            "class": "chart-dot"
        }));

        var spanSeconds = last.t - first.t;
        el.chartLayer.appendChild(svgNode("text", {
            x: pad.left,
            y: height - 8,
            "class": "chart-label",
            "text-anchor": "start"
        }, formatTimeShort(first.t, spanSeconds)));
        el.chartLayer.appendChild(svgNode("text", {
            x: width - pad.right,
            y: height - 8,
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
       Data loading and states
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
                        "The dashboard reads status.json, which the monitor writes " +
                        "after its first successful inverter read. This can take up " +
                        "to two minutes after installation.",
                        true
                    );
                } else {
                    setConnection("error", "Unavailable");
                    showNotice(
                        "error",
                        "Cannot read status.json",
                        "The dashboard could not load its data (" + error.message +
                        "). Check that the monitor has run and that lighttpd serves " +
                        "the add-on directory.",
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
            return {
                t: now - (values.length - 1 - index) * 600,
                soc: soc
            };
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
            variables: {
                soc: "SV_Batterie_SoC",
                signal: "SV_Batterie_95_Signal"
            },
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
            "status.json is not reachable. These values are generated for preview " +
            "only; open the dashboard through the CCU3 to see live measurements.",
            false
        );
    }

    /* ----------------------------------------------------------------------
       Startup
       ---------------------------------------------------------------------- */

    el.retry.addEventListener("click", function () {
        clearTimeout(pollTimer);
        load();
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
