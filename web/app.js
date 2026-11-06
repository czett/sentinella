/* ==========================================================================
   Sentinella — SMA / CCU3 battery monitor dashboard logic
   Vanilla ES6, no dependencies. Reads ./status.json.
   ========================================================================== */

(function () {
    "use strict";

    var $ = function (id) { return document.getElementById(id); };

    var STATUS_URL = new URL("./status.json", window.location.href);
    var CONFIG_URL = new URL("./api/config.cgi", window.location.href);
    var TEST_URL = new URL("./api/test_signal.cgi", window.location.href);

    var POLL_MS = 30000;
    var TEST_POLL_MS = 1000;
    var TEST_POLL_LIMIT = 30;

    var demoRequested =
        new URLSearchParams(window.location.search).has("demo") ||
        window.location.protocol === "file:";

    var current = null;
    var pollTimer = null;
    var loading = false;

    var settingsOpen = false;
    var settingsAvailable = true;
    var settingsBusy = false;
    var settingsFocusBefore = null;
    var thresholdOverride = null;
    var toastTimer = null;
    var liveConfig = null;
    var noticeRenderer = null;

    var LANG_KEY = "sentinella.language";
    var currentLanguage = "en";
    var languagePinned = false;
    var languageSynced = false;
    var configLanguageTried = false;

    var THEME_KEY = "sentinella_theme";
    var currentTheme = "light";
    var themeMedia = window.matchMedia
        ? window.matchMedia("(prefers-color-scheme: dark)")
        : null;

    var I18N = {
        en: {
            pageTitle: "Sentinella — SMA / CCU3 Battery Monitor",
            skipToContent: "Skip to content",
            retryNow: "Retry now",
            brandSub: "Battery Monitor & Hysteresis Sentinel",
            languageLabel: "Language",
            themeLabel: "Color theme",
            lightTheme: "Light theme",
            darkTheme: "Dark theme",
            openSettings: "Open settings",
            settings: "Settings",
            close: "Close",
            closeSettings: "Close settings",
            stateOfCharge: "State of charge",
            waitingFirstMeasurement: "Waiting for the first measurement.",
            hysteresisSignal: "Hysteresis signal",
            signalCopyDefault: "The signal is written to the CCU3 system variable on every run. Between the thresholds the previous state is held.",
            thresholdCorridor: "Threshold corridor (0—100 %)",
            reset: "Reset",
            trigger: "Trigger",
            chargingState: "Charging state",
            batteryPower: "Battery power",
            ccuWrite: "CCU write",
            lastSample: "Last sample",
            historyTitle: "State of charge history",
            historyDescription: "One sample per monitor run. Dashed rules mark reset and trigger thresholds.",
            noSamples: "No samples",
            noHistory: "No history recorded yet.",
            legendSoc: "SoC trajectory",
            legendBounds: "Hysteresis bounds",
            noData: "No data",
            systemRegister: "System register",
            systemRegisterDescription: "Parameters reported by the latest completed run.",
            inverterHost: "Inverter host",
            socVariable: "SoC variable",
            signalVariable: "Signal variable",
            signalGateway: "Signal gateway",
            timestamp: "Timestamp",
            footerTagline: "BATTERY MONITOR & HYSTERESIS SENTINEL · LOCAL",
            systemConfiguration: "System configuration",
            batteryThresholds: "Battery thresholds",
            thresholdsNote: "The signal turns on at the trigger level and off again at the reset level. Reset must stay below trigger.",
            triggerLevel: "Trigger level",
            triggerSlider: "Trigger level slider",
            resetLevel: "Reset level",
            resetSlider: "Reset level slider",
            signalAlerts: "Signal alerts",
            sendNotifications: "Send notifications",
            sendNotificationsNote: "Calls the CallMeBot Signal gateway when the signal changes.",
            phoneLabel: "Phone / UUID",
            phoneAria: "Phone number or Signal UUID",
            apikeyLabel: "CallMeBot API key",
            required: "Required",
            storedKeepHint: "An API key is stored. Leave the field empty to keep it.",
            inverter: "Inverter",
            smaIp: "SMA IP address",
            smaIpAria: "SMA inverter IP address or hostname",
            httpsPort: "HTTPS port",
            inverterPassword: "Inverter password",
            unchanged: "Unchanged",
            storedPasswordHint: "A password is stored. Leave the field empty to keep it.",
            testNotification: "Test notification",
            saveSettings: "Save settings",
            footnotePrefix: "Values are written to",
            footnoteSuffix: "on the CCU. The next monitor run applies them, at most two minutes away.",

            charging: "Charging",
            discharging: "Discharging",
            idle: "Idle",
            unknown: "Unknown",
            active: "Active",
            inactive: "Inactive",
            powerBoth: "{charge} W in / {discharge} W out",
            powerCharging: "{power} W charging",
            powerDischarging: "{power} W discharging",
            powerZero: "0 W",
            powerNotReported: "Not reported",
            ccuWritten: "Written",
            ccuWriteFailed: "Write failed",
            ariaSoc: "Battery state of charge {soc} percent, {state}",
            ariaRail: "State of charge {soc} percent. Reset at {reset} percent, trigger at {trigger} percent.",
            ariaNoHistory: "No state of charge history yet",
            ariaHistory: "State of charge history, {count} samples, latest {soc} percent at {time}.",
            captionActive: "At {soc}% the overflow signal is active. Resets at {reset}% or below.",
            captionInactive: "At {soc}% the overflow signal is inactive. Triggers at {trigger}% or above.",
            captionUnknown: "No definitive signal state yet. Between {reset}% and {trigger}% the previous state is held.",
            signalCopyTriggered: "Triggered at {soc}%. Active until state of charge falls to {reset}% or below.",
            signalCopyHeld: "Held active inside the hysteresis band. Resets at {reset}% or below.",
            signalCopyActive: "Active. Resets at {reset}% or below.",
            signalCopyReset: "Reset at {soc}%. Triggers again at {trigger}% or above.",
            signalCopyInactive: "Inactive. Triggers at {trigger}% or above.",
            signalCopyUnknown: "The boolean is held between {reset}% and {trigger}% when no previous state is known.",
            sampleOne: "1 sample",
            sampleMany: "{count} samples",
            enabled: "Enabled",
            disabled: "Disabled",
            historyRange: "{first} — {last}",
            pullRefresh: "Pull to refresh",
            releaseRefresh: "Release to refresh",
            refreshing: "Refreshing",

            lastStateTitle: "Showing last recorded state",
            lastStateText: "The latest request failed ({error}). Polling continues automatically.",
            waitingTitle: "Waiting for first measurement",
            waitingText: "The dashboard reads status.json after the monitor completes its first inverter poll (up to two minutes after installation).",
            statusErrorTitle: "Cannot read status.json",
            statusErrorText: "The dashboard could not load telemetry ({error}). Verify that the monitor has run and lighttpd serves the add-on directory.",
            demoTitle: "Sentinella Preview Data",
            demoText: "status.json is not reachable. Showing synthesized telemetry; open the dashboard via the CCU3 for live inverter measurements.",

            savingConfig: "Saving configuration…",
            configSavedApply: "Configuration saved. The monitor applies it on its next run.",
            configSaved: "Configuration saved.",
            loadingSettings: "Loading settings…",
            settingsUnavailable: "Settings are not available. Open the dashboard through the CCU3 and make sure CGI is enabled for the add-on directory.",
            settingsUnavailableShort: "Settings are not available. Open the dashboard through the CCU3.",
            testTakingTooLong: "The test is taking too long. Check state/notify_test.log on the CCU.",
            testRunnerDidNotStart: "The test runner did not start. Check state/notify_test.log on the CCU.",
            testFailedFallback: "Test notification failed.",
            testSavedSending: "Configuration saved. Sending a test message…",
            testSavedTesting: "Configuration saved. Testing Signal…",
            testSuccess: "Sentinella: Connection verified. Ready for alerts.",
            testFailedWarning: "Settings saved, but test message failed. Check phone/UUID and API key.",
            savingBeforeTest: "Saving configuration before the test…",
            sendingTest: "Sending test notification…",
            languageSaveFailed: "Language saved locally. The backend could not be updated.",
            triggerWhole: "Trigger level must be a whole number between 0 and 100.",
            resetWhole: "Reset level must be a whole number between 0 and 100.",
            resetLower: "Reset level must be lower than the trigger level.",
            phoneInvalid: "Enter an E.164 phone number (for example +491701234567) or a Signal UUID.",
            phoneRequired: "A phone number or Signal UUID is required when alerts are enabled.",
            apikeyRequired: "A CallMeBot API key is required when alerts are enabled.",
            ipInvalid: "Enter a valid inverter IPv4 address or hostname.",
            portInvalid: "Inverter port must be a whole number between 1 and 65535."
        },
        de: {
            pageTitle: "Sentinella — SMA / CCU3 Batteriemonitor",
            skipToContent: "Zum Inhalt springen",
            retryNow: "Jetzt erneut versuchen",
            brandSub: "Batteriemonitor & Hysterese-Wächter",
            languageLabel: "Sprache",
            themeLabel: "Farbschema",
            lightTheme: "Helles Design",
            darkTheme: "Dunkles Design",
            openSettings: "Einstellungen öffnen",
            settings: "Einstellungen",
            close: "Schließen",
            closeSettings: "Einstellungen schließen",
            stateOfCharge: "Ladezustand",
            waitingFirstMeasurement: "Warte auf die erste Messung.",
            hysteresisSignal: "Hysteresesignal",
            signalCopyDefault: "Das Signal wird bei jedem Lauf in die CCU3-Systemvariable geschrieben. Zwischen den Schwellen bleibt der vorherige Zustand erhalten.",
            thresholdCorridor: "Schwellenkorridor (0—100 %)",
            reset: "Rücksetzen",
            trigger: "Auslösen",
            chargingState: "Ladeverhalten",
            batteryPower: "Batterieleistung",
            ccuWrite: "CCU-Schreibzugriff",
            lastSample: "Letzte Messung",
            historyTitle: "Verlauf des Ladezustands",
            historyDescription: "Eine Messung pro Monitorlauf. Gestrichelte Linien markieren Rücksetz- und Auslöseschwelle.",
            noSamples: "Keine Messwerte",
            noHistory: "Noch kein Verlauf aufgezeichnet.",
            legendSoc: "SoC-Verlauf",
            legendBounds: "Hysteresegrenzen",
            noData: "Keine Daten",
            systemRegister: "Systemregister",
            systemRegisterDescription: "Parameter aus dem letzten abgeschlossenen Lauf.",
            inverterHost: "Wechselrichter",
            socVariable: "SoC-Variable",
            signalVariable: "Signalvariable",
            signalGateway: "Signal-Gateway",
            timestamp: "Zeitstempel",
            footerTagline: "BATTERIEMONITOR & HYSTERESE-WÄCHTER · LOKAL",
            systemConfiguration: "Systemkonfiguration",
            batteryThresholds: "Batterieschwellen",
            thresholdsNote: "Das Signal schaltet bei der Auslöseschwelle ein und bei der Rücksetzschwelle wieder aus. Die Rücksetzschwelle muss unter der Auslöseschwelle liegen.",
            triggerLevel: "Auslöseschwelle",
            triggerSlider: "Schieberegler Auslöseschwelle",
            resetLevel: "Rücksetzschwelle",
            resetSlider: "Schieberegler Rücksetzschwelle",
            signalAlerts: "Signalbenachrichtigungen",
            sendNotifications: "Benachrichtigungen senden",
            sendNotificationsNote: "Ruft das CallMeBot-Signal-Gateway auf, wenn sich das Signal ändert.",
            phoneLabel: "Telefon / UUID",
            phoneAria: "Telefonnummer oder Signal-UUID",
            apikeyLabel: "CallMeBot-API-Schlüssel",
            required: "Erforderlich",
            storedKeepHint: "Ein API-Schlüssel ist gespeichert. Feld leer lassen, um ihn zu behalten.",
            inverter: "Wechselrichter",
            smaIp: "SMA-IP-Adresse",
            smaIpAria: "IP-Adresse oder Hostname des SMA-Wechselrichters",
            httpsPort: "HTTPS-Port",
            inverterPassword: "Wechselrichter-Passwort",
            unchanged: "Unverändert",
            storedPasswordHint: "Ein Passwort ist gespeichert. Feld leer lassen, um es zu behalten.",
            testNotification: "Testbenachrichtigung",
            saveSettings: "Einstellungen speichern",
            footnotePrefix: "Werte werden in",
            footnoteSuffix: "auf der CCU geschrieben. Der nächste Monitorlauf übernimmt sie, spätestens nach zwei Minuten.",

            charging: "Lädt",
            discharging: "Entlädt",
            idle: "Leerlauf",
            unknown: "Unbekannt",
            active: "Aktiv",
            inactive: "Inaktiv",
            powerBoth: "{charge} W rein / {discharge} W raus",
            powerCharging: "{power} W Ladung",
            powerDischarging: "{power} W Entladung",
            powerZero: "0 W",
            powerNotReported: "Nicht gemeldet",
            ccuWritten: "Geschrieben",
            ccuWriteFailed: "Schreiben fehlgeschlagen",
            ariaSoc: "Batterieladezustand {soc} Prozent, {state}",
            ariaRail: "Ladezustand {soc} Prozent. Rücksetzen bei {reset} Prozent, Auslösen bei {trigger} Prozent.",
            ariaNoHistory: "Noch kein Ladezustandsverlauf",
            ariaHistory: "Ladezustandsverlauf, {count} Messwerte, zuletzt {soc} Prozent um {time}.",
            captionActive: "Bei {soc}% ist das Überschusssignal aktiv. Rücksetzen bei {reset}% oder darunter.",
            captionInactive: "Bei {soc}% ist das Überschusssignal inaktiv. Auslösen bei {trigger}% oder darüber.",
            captionUnknown: "Noch kein eindeutiger Signalzustand. Zwischen {reset}% und {trigger}% bleibt der vorherige Zustand erhalten.",
            signalCopyTriggered: "Ausgelöst bei {soc}%. Aktiv, bis der Ladezustand auf {reset}% oder darunter fällt.",
            signalCopyHeld: "Im Hysteresebereich aktiv gehalten. Rücksetzen bei {reset}% oder darunter.",
            signalCopyActive: "Aktiv. Rücksetzen bei {reset}% oder darunter.",
            signalCopyReset: "Zurückgesetzt bei {soc}%. Löst wieder bei {trigger}% oder darüber aus.",
            signalCopyInactive: "Inaktiv. Löst bei {trigger}% oder darüber aus.",
            signalCopyUnknown: "Die Boolesche Variable bleibt zwischen {reset}% und {trigger}% erhalten, solange kein vorheriger Zustand bekannt ist.",
            sampleOne: "1 Messwert",
            sampleMany: "{count} Messwerte",
            enabled: "Aktiviert",
            disabled: "Deaktiviert",
            historyRange: "{first} — {last}",
            pullRefresh: "Zum Aktualisieren ziehen",
            releaseRefresh: "Zum Aktualisieren loslassen",
            refreshing: "Wird aktualisiert",

            lastStateTitle: "Letzter aufgezeichneter Zustand",
            lastStateText: "Die letzte Anfrage ist fehlgeschlagen ({error}). Die Abfrage läuft automatisch weiter.",
            waitingTitle: "Warte auf die erste Messung",
            waitingText: "Das Dashboard liest status.json, sobald der Monitor seinen ersten Wechselrichter-Abruf abgeschlossen hat (bis zu zwei Minuten nach der Installation).",
            statusErrorTitle: "status.json kann nicht gelesen werden",
            statusErrorText: "Das Dashboard konnte die Telemetrie nicht laden ({error}). Prüfen Sie, ob der Monitor gelaufen ist und lighttpd das Add-on-Verzeichnis ausliefert.",
            demoTitle: "Sentinella-Vorschaudaten",
            demoText: "status.json ist nicht erreichbar. Es werden synthetische Telemetriedaten angezeigt; öffnen Sie das Dashboard über die CCU3 für Live-Messwerte.",

            savingConfig: "Konfiguration wird gespeichert…",
            configSavedApply: "Konfiguration gespeichert. Der Monitor übernimmt sie beim nächsten Lauf.",
            configSaved: "Konfiguration gespeichert.",
            loadingSettings: "Einstellungen werden geladen…",
            settingsUnavailable: "Einstellungen sind nicht verfügbar. Öffnen Sie das Dashboard über die CCU3 und stellen Sie sicher, dass CGI für das Add-on-Verzeichnis aktiviert ist.",
            settingsUnavailableShort: "Einstellungen sind nicht verfügbar. Öffnen Sie das Dashboard über die CCU3.",
            testTakingTooLong: "Der Test dauert zu lange. Prüfen Sie state/notify_test.log auf der CCU.",
            testRunnerDidNotStart: "Der Testrunner wurde nicht gestartet. Prüfen Sie state/notify_test.log auf der CCU.",
            testFailedFallback: "Testbenachrichtigung fehlgeschlagen.",
            testSavedSending: "Konfiguration gespeichert. Testnachricht wird gesendet…",
            testSavedTesting: "Konfiguration gespeichert. Signal wird getestet…",
            testSuccess: "Sentinella: Verbindung bestätigt. Bereit für Benachrichtigungen.",
            testFailedWarning: "Einstellungen gespeichert, aber die Testnachricht ist fehlgeschlagen. Prüfen Sie Telefon/UUID und API-Schlüssel.",
            savingBeforeTest: "Konfiguration wird vor dem Test gespeichert…",
            sendingTest: "Testbenachrichtigung wird gesendet…",
            languageSaveFailed: "Sprache lokal gespeichert. Das Backend konnte nicht aktualisiert werden.",
            triggerWhole: "Die Auslöseschwelle muss eine ganze Zahl zwischen 0 und 100 sein.",
            resetWhole: "Die Rücksetzschwelle muss eine ganze Zahl zwischen 0 und 100 sein.",
            resetLower: "Die Rücksetzschwelle muss unter der Auslöseschwelle liegen.",
            phoneInvalid: "Geben Sie eine E.164-Telefonnummer (zum Beispiel +491701234567) oder eine Signal-UUID ein.",
            phoneRequired: "Wenn Benachrichtigungen aktiviert sind, ist eine Telefonnummer oder Signal-UUID erforderlich.",
            apikeyRequired: "Wenn Benachrichtigungen aktiviert sind, ist ein CallMeBot-API-Schlüssel erforderlich.",
            ipInvalid: "Geben Sie eine gültige IPv4-Adresse oder einen Hostnamen des Wechselrichters ein.",
            portInvalid: "Der Wechselrichter-Port muss eine ganze Zahl zwischen 1 und 65535 sein."
        }
    };

    var pullStart = null;
    var pullDistance = 0;
    var pullActive = false;

    var el = {
        main: $("main"),
        mainContent: $("main-content"),
        pull: $("pull"),
        pullLabel: $("pull-label"),

        notice: $("notice"),
        noticeTitle: $("notice-title"),
        noticeText: $("notice-text"),
        retry: $("retry"),

        heroBlock: $("hero-block"),
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
        railResetTag: $("rail-reset-tag"),
        railTriggerTag: $("rail-trigger-tag"),

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
       Language
       ---------------------------------------------------------------------- */

    function normalizeLanguage(value) {
        return value === "de" ? "de" : "en";
    }

    function t(key, vars) {
        var table = I18N[currentLanguage] || I18N.en;
        var text = table[key];
        if (text === undefined) {
            text = I18N.en[key] !== undefined ? I18N.en[key] : key;
        }
        Object.keys(vars || {}).forEach(function (name) {
            text = text.replace(new RegExp("\\{" + name + "\\}", "g"), String(vars[name]));
        });
        return text;
    }

    function readStoredLanguage() {
        try {
            return normalizeLanguage(window.localStorage.getItem(LANG_KEY));
        } catch (error) {
            return "en";
        }
    }

    function storeLanguage(language) {
        try {
            window.localStorage.setItem(LANG_KEY, language);
        } catch (error) {
            /* localStorage can be disabled; the backend still persists it. */
        }
    }

    function applyStaticLanguage() {
        document.documentElement.lang = currentLanguage;
        document.title = t("pageTitle");

        document.querySelectorAll("[data-i18n]").forEach(function (node) {
            node.textContent = t(node.getAttribute("data-i18n"));
        });
        document.querySelectorAll("[data-i18n-aria]").forEach(function (node) {
            node.setAttribute("aria-label", t(node.getAttribute("data-i18n-aria")));
        });
        document.querySelectorAll("[data-i18n-placeholder]").forEach(function (node) {
            node.setAttribute("placeholder", t(node.getAttribute("data-i18n-placeholder")));
        });
        document.querySelectorAll("[data-i18n-title]").forEach(function (node) {
            node.setAttribute("title", t(node.getAttribute("data-i18n-title")));
        });
        document.querySelectorAll(".lang-option").forEach(function (button) {
            var active = button.getAttribute("data-lang") === currentLanguage;
            button.setAttribute("aria-pressed", active ? "true" : "false");
        });
    }

    function setLanguage(language, options) {
        var opts = options || {};
        currentLanguage = normalizeLanguage(language);
        if (opts.persist !== false) {
            storeLanguage(currentLanguage);
        }
        if (opts.pin) {
            languagePinned = true;
        }
        applyStaticLanguage();
        if (current) {
            render(current);
        } else if (el.pullLabel && !pullActive) {
            el.pullLabel.textContent = t("pullRefresh");
        }
        if (noticeRenderer && !el.notice.hidden) {
            noticeRenderer();
        }
    }

    function adoptBackendLanguage(value) {
        if (languagePinned || languageSynced) {
            return false;
        }
        if (value !== "en" && value !== "de") {
            return false;
        }
        languageSynced = true;
        setLanguage(value);
        return true;
    }

    function syncLanguageFromConfig() {
        if (languagePinned || languageSynced || configLanguageTried || demoRequested) {
            return;
        }
        configLanguageTried = true;
        requestJSON(CONFIG_URL).then(function (data) {
            adoptBackendLanguage(data && data.ui_language);
        }).catch(function () {
            /* Keep the localStorage choice until status.json provides one. */
        });
    }

    function syncLanguageFromStatus(data) {
        if (adoptBackendLanguage(data && data.ui_language)) {
            return;
        }
        syncLanguageFromConfig();
    }

    function saveLanguagePreference(language) {
        if (demoRequested) {
            return;
        }
        requestJSON(CONFIG_URL, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ui_language: language })
        }).then(function (data) {
            if (data && data.ui_language && data.ui_language !== language &&
                    currentLanguage === language) {
                setLanguage(data.ui_language);
            }
        }).catch(function () {
            showToast("error", t("languageSaveFailed"));
        });
    }

    /* ----------------------------------------------------------------------
       Theme
       ---------------------------------------------------------------------- */

    function normalizeTheme(value) {
        return value === "dark" ? "dark" : "light";
    }

    function readStoredTheme() {
        try {
            var value = window.localStorage.getItem(THEME_KEY);
            return value === "light" || value === "dark" ? value : null;
        } catch (error) {
            return null;
        }
    }

    function storeTheme(theme) {
        try {
            window.localStorage.setItem(THEME_KEY, theme);
        } catch (error) {
            /* Private mode or disabled storage: keep the theme for this visit. */
        }
    }

    function systemTheme() {
        return themeMedia && themeMedia.matches ? "dark" : "light";
    }

    function applyTheme(theme) {
        currentTheme = normalizeTheme(theme);
        document.documentElement.setAttribute("data-theme", currentTheme);

        var meta = document.querySelector('meta[name="theme-color"]');
        if (meta) {
            meta.setAttribute("content", currentTheme === "dark" ? "#09090b" : "#f8f8f9");
        }

        document.querySelectorAll(".theme-option").forEach(function (button) {
            var active = button.getAttribute("data-theme-value") === currentTheme;
            button.setAttribute("aria-pressed", active ? "true" : "false");
        });
    }

    function setTheme(theme) {
        applyTheme(theme);
        storeTheme(currentTheme);
    }

    function syncSystemTheme(event) {
        if (readStoredTheme()) {
            return;
        }
        applyTheme(event.matches ? "dark" : "light");
    }

    /* ----------------------------------------------------------------------
       Helpers
       ---------------------------------------------------------------------- */

    function clamp(value, min, max) {
        return Math.min(max, Math.max(min, value));
    }

    function toNumber(value) {
        return typeof value === "number" && Number.isFinite(value) ? value : null;
    }

    function formatTime(iso) {
        var date = new Date(iso);
        if (Number.isNaN(date.getTime())) {
            return "—";
        }
        return date.toLocaleString(currentLanguage, {
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
        return new Date(epochSeconds * 1000).toLocaleTimeString(currentLanguage, options);
    }

    function chargeLabel(value) {
        switch (value) {
            case "charging": return t("charging");
            case "discharging": return t("discharging");
            case "idle": return t("idle");
            default: return t("unknown");
        }
    }

    function chargeTag(value) {
        return chargeLabel(value).toUpperCase();
    }

    function signalLabel(data) {
        if (data.signal === true) {
            return t("active");
        }
        if (data.signal === false) {
            return t("inactive");
        }
        return t("unknown");
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

    function showNotice(tone, title, text, offerRetry) {
        el.notice.hidden = false;
        el.notice.dataset.tone = tone;
        el.noticeTitle.textContent = title;
        el.noticeText.textContent = text;
        el.retry.hidden = !offerRetry;
    }

    function showNoticeFor(tone, titleKey, textKey, vars, offerRetry) {
        noticeRenderer = function () {
            showNotice(tone, t(titleKey), t(textKey, vars), offerRetry);
        };
        noticeRenderer();
    }

    function hideNotice() {
        el.notice.hidden = true;
        noticeRenderer = null;
    }

    function powerText(data) {
        var charge = toNumber(data.charge_power_w);
        var discharge = toNumber(data.discharge_power_w);

        if (charge !== null && charge > 0 && discharge !== null && discharge > 0) {
            return t("powerBoth", {
                charge: Math.round(charge),
                discharge: Math.round(discharge)
            });
        }
        if (charge !== null && charge > 0) {
            return t("powerCharging", { power: Math.round(charge) });
        }
        if (discharge !== null && discharge > 0) {
            return t("powerDischarging", { power: Math.round(discharge) });
        }
        if (charge === 0 || discharge === 0) {
            return t("powerZero");
        }
        return t("powerNotReported");
    }

    function setCcuState(status) {
        if (status === "ok") {
            el.ccuState.textContent = t("ccuWritten");
            el.ccuState.dataset.tone = "ok";
        } else if (status === "error") {
            el.ccuState.textContent = t("ccuWriteFailed");
            el.ccuState.dataset.tone = "error";
        } else {
            el.ccuState.textContent = "—";
            delete el.ccuState.dataset.tone;
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
        var tone = signalTone(data);

        current = data;
        document.body.dataset.state = "ready";

        if (el.heroBlock) {
            el.heroBlock.dataset.charge = charge;
        }

        el.socValue.textContent = String(soc);
        el.socState.textContent = chargeTag(charge);
        el.socState.dataset.charge = charge;
        el.gauge.setAttribute(
            "aria-label",
            t("ariaSoc", { soc: soc, state: chargeLabel(charge).toLowerCase() })
        );
        el.socCaption.textContent = captionFor(data, thresholds);

        el.signalChip.textContent = signalLabel(data);
        el.signalChip.dataset.tone = tone;
        if (el.signalChip.parentElement && el.signalChip.parentElement.parentElement) {
            el.signalChip.parentElement.parentElement.dataset.tone = tone;
        }
        el.signalCopy.textContent = signalCopyFor(data, thresholds);

        el.rail.style.setProperty("--soc", String(soc));
        el.rail.style.setProperty("--reset", String(thresholds.reset));
        el.rail.style.setProperty("--trigger", String(thresholds.trigger));
        el.rail.dataset.active = data.signal === true ? "true" : "false";
        el.rail.setAttribute(
            "aria-label",
            t("ariaRail", {
                soc: soc,
                reset: thresholds.reset,
                trigger: thresholds.trigger
            })
        );
        el.railMarkerLabel.textContent = soc + "%";
        el.railResetValue.textContent = String(thresholds.reset);
        el.railTriggerValue.textContent = String(thresholds.trigger);
        if (el.railResetTag) {
            el.railResetTag.textContent = thresholds.reset + "%";
        }
        if (el.railTriggerTag) {
            el.railTriggerTag.textContent = thresholds.trigger + "%";
        }

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
        el.metaNotify.textContent = data.signal_notifications ? t("enabled") : t("disabled");
        el.metaLast.textContent = last;

        el.historyCount.textContent = history.length
            ? (history.length === 1 ? t("sampleOne") : t("sampleMany", { count: history.length }))
            : t("noSamples");

        renderChart(history, thresholds);
    }

    function captionFor(data, thresholds) {
        if (data.signal === true) {
            return t("captionActive", { soc: data.soc, reset: thresholds.reset });
        }
        if (data.signal === false) {
            return t("captionInactive", { soc: data.soc, trigger: thresholds.trigger });
        }
        return t("captionUnknown", { reset: thresholds.reset, trigger: thresholds.trigger });
    }

    function signalCopyFor(data, thresholds) {
        if (data.signal === true) {
            if (data.signal_state === "triggered") {
                return t("signalCopyTriggered", { soc: data.soc, reset: thresholds.reset });
            }
            if (data.signal_state === "held") {
                return t("signalCopyHeld", { reset: thresholds.reset });
            }
            return t("signalCopyActive", { reset: thresholds.reset });
        }
        if (data.signal === false) {
            if (data.signal_state === "reset") {
                return t("signalCopyReset", { soc: data.soc, trigger: thresholds.trigger });
            }
            return t("signalCopyInactive", { trigger: thresholds.trigger });
        }
        return t("signalCopyUnknown", { reset: thresholds.reset, trigger: thresholds.trigger });
    }

    /* ----------------------------------------------------------------------
       History chart (Stark Two-Tone Sparkline)
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
        var pad = { top: 14, right: 12, bottom: 28, left: 32 };
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
            el.chartRange.textContent = t("noData");
            el.chart.setAttribute("aria-label", t("ariaNoHistory"));
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
                y: lineY + 3.5,
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

        el.chartLayer.appendChild(svgNode("path", { d: linePath, "class": "chart-line" }));
        el.chartLayer.appendChild(svgNode("rect", {
            x: (x(last.t) - 4.5).toFixed(1),
            y: (y(last.soc) - 4.5).toFixed(1),
            width: "9",
            height: "9",
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

        el.chartRange.textContent = t("historyRange", {
            first: formatTimeShort(first.t, spanSeconds),
            last: formatTimeShort(last.t, spanSeconds)
        });
        el.chart.setAttribute(
            "aria-label",
            t("ariaHistory", {
                count: points.length,
                soc: last.soc,
                time: formatTimeShort(last.t, spanSeconds)
            })
        );
    }

    /* ----------------------------------------------------------------------
       Loading and polling
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
                syncLanguageFromStatus(data);
                render(data);
                hideNotice();
                schedule(POLL_MS);
            })
            .catch(function (error) {
                if (demoRequested) {
                    renderDemo();
                    return;
                }
                syncLanguageFromConfig();
                if (current) {
                    showNoticeFor(
                        "warning",
                        "lastStateTitle",
                        "lastStateText",
                        { error: error.message },
                        true
                    );
                } else if (error.status === 404) {
                    showNoticeFor(
                        "empty",
                        "waitingTitle",
                        "waitingText",
                        {},
                        true
                    );
                } else {
                    showNoticeFor(
                        "error",
                        "statusErrorTitle",
                        "statusErrorText",
                        { error: error.message },
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
        showNoticeFor(
            "warning",
            "demoTitle",
            "demoText",
            {},
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
        el.signalApikey.placeholder = data.signal_apikey_set ? t("unchanged") : t("required");
        el.apikeyHint.hidden = !data.signal_apikey_set;

        el.smaIp.value = data.sma_ip || "";
        el.smaPort.value = String(data.sma_port || 443);
        el.smaPassword.value = "";
        el.smaPassword.placeholder = data.sma_password_set ? t("unchanged") : t("required");
        el.smaPasswordHint.hidden = !data.sma_password_set;
    }

    function adoptConfig(data) {
        liveConfig = data;
        if (data && data.ui_language) {
            if (!languagePinned && !languageSynced) {
                languageSynced = true;
                setLanguage(data.ui_language);
            }
        }
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
                text: t("triggerWhole")
            };
        }
        if (!resetRaw || !Number.isInteger(reset) || reset < 0 || reset > 100) {
            return {
                field: "reset-soc",
                text: t("resetWhole")
            };
        }
        if (reset >= trigger) {
            return {
                field: "reset-soc",
                text: t("resetLower")
            };
        }

        var phone = el.signalPhone.value.trim();
        if (phone && !/^(\+[1-9]\d{6,14}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/i.test(phone)) {
            return {
                field: "signal-phone",
                text: t("phoneInvalid")
            };
        }

        var apikey = el.signalApikey.value.trim();
        if (el.signalEnabled.checked) {
            if (!phone) {
                return {
                    field: "signal-phone",
                    text: t("phoneRequired")
                };
            }
            if (!apikey && !(liveConfig && liveConfig.signal_apikey_set)) {
                return {
                    field: "signal-apikey",
                    text: t("apikeyRequired")
                };
            }
        }

        var ip = el.smaIp.value.trim();
        if (!ip || !/^[A-Za-z0-9._-]+$/.test(ip) || ip.indexOf("..") >= 0) {
            return {
                field: "sma-ip",
                text: t("ipInvalid")
            };
        }

        var portRaw = el.smaPort.value.trim();
        var port = Number(portRaw);
        if (!portRaw || !Number.isInteger(port) || port < 1 || port > 65535) {
            return {
                field: "sma-port",
                text: t("portInvalid")
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
            return Promise.reject(new Error(t("settingsUnavailableShort")));
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
            showSettingsStatus("pending", t("savingConfig"));
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
                    t("configSavedApply")
                );
            }
            return data;
        });
    }

    function pollTestResult(attempt) {
        return new Promise(function (resolve, reject) {
            if (attempt > TEST_POLL_LIMIT) {
                reject(new Error(t("testTakingTooLong")));
                return;
            }
            requestJSON(TEST_URL).then(function (data) {
                if (data.status === "sent") {
                    resolve(data);
                    return;
                }
                if (data.status === "failed") {
                    reject(new Error(data.error || t("testFailedFallback")));
                    return;
                }
                if (data.status === "idle" && attempt > 3) {
                    reject(new Error(t("testRunnerDidNotStart")));
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
        showSettingsStatus("pending", t("testSavedSending"));
        showToast("pending", t("testSavedTesting"));

        sendTestNotification().then(function () {
            var success = t("testSuccess");
            showSettingsStatus("success", success);
            showToast("success", success);
        }).catch(function (error) {
            var warning = t("testFailedWarning");
            showSettingsStatus("error", warning + " " + error.message);
            showToast("error", warning);
        });
    }

    function runTest() {
        if (settingsBusy) {
            return;
        }
        setSettingsBusy(true);
        showSettingsStatus("pending", t("savingBeforeTest"));

        saveSettings(true).then(function () {
            showSettingsStatus("pending", t("sendingTest"));
            return sendTestNotification();
        }).then(function () {
            var message = t("testSuccess");
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
        showSettingsStatus("pending", t("loadingSettings"));

        requestJSON(CONFIG_URL).then(function (data) {
            liveConfig = data;
            fillSettingsForm(data);
            adoptConfig(data);
            showSettingsStatus("", "");
        }).catch(function () {
            settingsAvailable = false;
            el.settingsSave.disabled = true;
            el.settingsTest.disabled = true;
            showSettingsStatus(
                "error",
                t("settingsUnavailable")
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
        el.pullLabel.textContent = distance >= 52 ? t("releaseRefresh") : t("pullRefresh");
    }

    function resetPull() {
        pullDistance = 0;
        pullActive = false;
        el.pull.dataset.active = "false";
        el.pull.dataset.ready = "false";
        el.mainContent.style.transition = "transform 200ms cubic-bezier(0.16, 1, 0.3, 1)";
        el.mainContent.style.transform = "";
        setTimeout(function () {
            el.mainContent.style.transition = "";
        }, 220);
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
            el.pullLabel.textContent = t("refreshing");
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

    document.querySelectorAll(".lang-option").forEach(function (button) {
        button.addEventListener("click", function () {
            var language = normalizeLanguage(button.getAttribute("data-lang"));
            if (language === currentLanguage) {
                return;
            }
            setLanguage(language, { persist: true, pin: true });
            saveLanguagePreference(language);
        });
    });

    document.querySelectorAll(".theme-option").forEach(function (button) {
        button.addEventListener("click", function () {
            var theme = normalizeTheme(button.getAttribute("data-theme-value"));
            if (theme === currentTheme) {
                return;
            }
            setTheme(theme);
        });
    });

    if (themeMedia) {
        if (themeMedia.addEventListener) {
            themeMedia.addEventListener("change", syncSystemTheme);
        } else if (themeMedia.addListener) {
            themeMedia.addListener(syncSystemTheme);
        }
    }

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
                verifySignalAfterSave();
            } else {
                showToast("success", t("configSaved"));
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

    applyTheme(readStoredTheme() ||
        document.documentElement.getAttribute("data-theme") ||
        systemTheme());

    setLanguage(readStoredLanguage(), { persist: false });

    if (demoRequested) {
        renderDemo();
    } else {
        load();
    }
})();
