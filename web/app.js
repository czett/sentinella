/* ==========================================================================
   Sentinella — SMA / CCU3 battery monitor dashboard logic
   Vanilla ES6, no dependencies. Reads ./status.json.
   ========================================================================== */

(function () {
    "use strict";

    var $ = function (id) { return document.getElementById(id); };

    var STATUS_URL = new URL("./status.json", window.location.href);
    var CONFIG_URL = new URL("./api/config.cgi", window.location.href);
    var SETTINGS_URL = new URL("./api/settings.cgi", window.location.href);
    var TEST_URL = new URL("./api/test_signal.cgi", window.location.href);
    var TEST_RENAULT_URL = new URL("./api/test_renault.cgi", window.location.href);
    var ACTION_ZOE_URL = new URL("./api/action_zoe.cgi", window.location.href);
    var PLUGINS_URL = new URL("./api/plugins.cgi", window.location.href);

    var POLL_MS = 30000;
    var TEST_POLL_MS = 1000;
    var TEST_POLL_LIMIT = 30;

    var demoRequested =
        new URLSearchParams(window.location.search).has("demo") ||
        window.location.protocol === "file:";

    var current = null;
    var currentPlugins = null;
    /* Epoch ms of the last explicit local HVAC stop. Until a plugin poll
       carries a newer timestamp, its hvac_active value is forced to false. */
    var hvacStopOverrideAt = 0;
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

    var ANIMATIONS_KEY = "sentinella_animations";
    var animationsEnabled = true;
    try {
        if (window.localStorage.getItem(ANIMATIONS_KEY) === "off") {
            animationsEnabled = false;
        }
    } catch (error) {
        animationsEnabled = true;
    }
    if (!animationsEnabled) {
        document.body.classList.add("disable-animations");
    } else {
        document.body.classList.remove("disable-animations");
    }

    var I18N = {
        en: {
            pageTitle: "Sentinella",
            skipToContent: "Skip to content",
            retryNow: "Retry now",
            brandSub: "SMA Battery Monitor for CCU3",
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
            hysteresisSignal: "Excess Signal",
            signalCopyDefault: "Turns on at the trigger threshold and off below the reset threshold.",
            thresholdCorridor: "Thresholds",
            reset: "Reset",
            trigger: "Trigger",
            chargingState: "Charging state",
            batteryPower: "Battery power",
            ccuWrite: "CCU write",
            lastSample: "Last sample",
            historyTitle: "State of charge history",
            historyDescription: "One sample per monitor run. Dashed lines mark the thresholds.",
            noSamples: "No samples",
            noHistory: "No history recorded yet.",
            legendSoc: "SoC",
            legendBounds: "Thresholds",
            noData: "No data",
            systemRegister: "System register",
            systemRegisterDescription: "Values from the latest run.",
            pluginsTitle: "EXTENSIONS",
            inverterHost: "Inverter host",
            socVariable: "SoC variable",
            signalVariable: "Signal variable",
            signalGateway: "Signal gateway",
            timestamp: "Timestamp",
            systemConfiguration: "System configuration",
            batteryThresholds: "Thresholds",
            thresholdsNote: "The signal turns on at the trigger level and off below the reset level. Reset must stay below trigger.",
            triggerLevel: "Trigger level",
            triggerSlider: "Trigger level slider",
            resetLevel: "Reset level",
            resetSlider: "Reset level slider",
            signalAlerts: "Signal alerts",
            sendNotifications: "Signal Notifications",
            sendNotificationsNote: "Calls the CallMeBot Signal gateway when the signal changes.",
            phoneLabel: "Signal Account UUID / Phone",
            phoneAria: "Signal Account UUID or phone number",
            apikeyLabel: "CallMeBot API key",
            quickSetupLabel: "CallMeBot Onboarding Message",
            quickSetupHint: "Paste activation message from CallMeBot to automatically extract UUID and API key.",
            quickSetupPlaceholder: "Paste the activation message here (e.g. 'The APIKey created for you is: 653590 ... phone=98526357-0cec...')",
            quickSetupButton: "Extract & Apply",
            quickSetupAutoSaved: "Saved and active",
            gatewayActive: "CallMeBot (active)",
            quickSetupSuccessBoth: "Extracted UUID & API Key successfully",
            quickSetupSuccessPhone: "Extracted Signal Account UUID successfully",
            quickSetupSuccessApikey: "Extracted API Key successfully",
            quickSetupNotFound: "Could not find a Signal Account UUID or API key in the pasted text.",
            required: "Required",
            storedKeepHint: "An API key is stored. Leave the field empty to keep it.",
            inverter: "Inverter",
            smaIp: "SMA IP address",
            smaIpAria: "SMA inverter IP address or hostname",
            httpsPort: "HTTPS port",
            inverterPassword: "Inverter password",
            unchanged: "Unchanged",
            storedPasswordHint: "A password is stored. Leave the field empty to keep it.",
            renaultSection: "RENAULT ZOE",
            renaultNote: "Optional MY Renault cloud credentials for live Zoe battery and charging telemetry.",
            renaultEmail: "Renault E-Mail",
            renaultPassword: "Renault Passwort",
            renaultVin: "Renault VIN",
            testRenaultConnection: "Verbindung testen",
            testingRenault: "Testing Renault connection…",
            renaultTestSuccess: "Renault Zoe: Connection verified.",
            renaultTestFailed: "Renault connection failed. Check email, password and VIN.",
            renaultEmailInvalid: "Enter a valid Renault email address.",
            renaultVinInvalid: "Enter a valid Renault VIN (e.g. VF1...).",
            renaultCredentialsRequired: "Enter Renault email, password and VIN before testing.",
            testNotification: "Test notification",
            saveSettings: "Save settings",
            footnotePrefix: "Values are written to",
            footnoteSuffix: "on the CCU. The next monitor run applies them within two minutes.",
            displaySection: "Display",
            animations: "ANIMATIONS",
            animationsLabel: "ANIMATIONS",
            animationsDesc: "(On / Off)",
            animationsOn: "On",
            animationsOff: "Off",

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
            captionActive: "Excess signal active at {soc}%. Turns off below {reset}%.",
            captionInactive: "Excess signal inactive at {soc}%. Turns on at {trigger}%.",
            captionUnknown: "No signal state yet. Previous state is kept between {reset}% and {trigger}%.",
            signalCopyTriggered: "Active. Turns off below {reset}%.",
            signalCopyHeld: "Active. Turns off below {reset}%.",
            signalCopyActive: "Active. Turns off below {reset}%.",
            signalCopyReset: "Inactive. Turns on at {trigger}%.",
            signalCopyInactive: "Inactive. Turns on at {trigger}%.",
            signalCopyUnknown: "Kept unchanged between {reset}% and {trigger}% until a threshold is crossed.",
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
            waitingText: "The dashboard reads status.json after the first inverter poll (up to two minutes after installation).",
            statusErrorTitle: "Cannot read status.json",
            statusErrorText: "Could not load status.json ({error}). Check that the cron job has run and lighttpd serves the add-on directory.",
            demoTitle: "Sentinella Preview Data",
            demoText: "status.json is not reachable. Showing demo data; open the dashboard on the CCU3 for live inverter readings.",

            savingConfig: "Saving configuration…",
            configSavedApply: "Configuration saved. Applied on the next run.",
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
            testFailedWarning: "Settings saved, but test message failed. Check Signal Account UUID and API key.",
            savingBeforeTest: "Saving configuration before the test…",
            sendingTest: "Sending test notification…",
            languageSaveFailed: "Language saved locally. The backend could not be updated.",
            triggerWhole: "Trigger level must be a whole number between 0 and 100.",
            resetWhole: "Reset level must be a whole number between 0 and 100.",
            resetLower: "Reset level must be lower than the trigger level.",
            phoneInvalid: "Enter a valid Signal Account UUID (for example 98526357-0cec-4e7f-9d60-d854432414d3).",
            phoneRequired: "A Signal Account UUID is required when alerts are enabled.",
            apikeyRequired: "A CallMeBot API key is required when alerts are enabled.",
            ipInvalid: "Enter a valid inverter IPv4 address or hostname.",
            portInvalid: "Inverter port must be a whole number between 1 and 65535."
        },
        de: {
            pageTitle: "Sentinella",
            skipToContent: "Zum Inhalt springen",
            retryNow: "Jetzt erneut versuchen",
            brandSub: "SMA Batteriestatus für CCU3",
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
            hysteresisSignal: "Überschuss-Signal",
            signalCopyDefault: "Schaltet ab der Einschaltschwelle ein und unter der Ausschaltschwelle wieder ab.",
            thresholdCorridor: "Schaltschwellen",
            reset: "Aus",
            trigger: "Ein",
            chargingState: "Ladeverhalten",
            batteryPower: "Batterieleistung",
            ccuWrite: "CCU-Schreibzugriff",
            lastSample: "Letzte Messung",
            historyTitle: "Verlauf des Ladezustands",
            historyDescription: "Eine Messung pro Lauf. Gestrichelte Linien zeigen die Schaltschwellen.",
            noSamples: "Keine Messwerte",
            noHistory: "Noch kein Verlauf aufgezeichnet.",
            legendSoc: "SoC",
            legendBounds: "Schaltschwellen",
            noData: "Keine Daten",
            systemRegister: "Systemregister",
            systemRegisterDescription: "Werte aus dem letzten abgeschlossenen Lauf.",
            pluginsTitle: "ERWEITERUNGEN",
            inverterHost: "Wechselrichter",
            socVariable: "SoC-Variable",
            signalVariable: "Signalvariable",
            signalGateway: "Signal-Gateway",
            timestamp: "Zeitstempel",
            systemConfiguration: "Systemkonfiguration",
            batteryThresholds: "Schaltschwellen",
            thresholdsNote: "Das Signal schaltet ab der Einschaltschwelle ein und unter der Ausschaltschwelle wieder ab. Die Ausschaltschwelle muss unter der Einschaltschwelle liegen.",
            triggerLevel: "Einschaltschwelle",
            triggerSlider: "Schieberegler Einschaltschwelle",
            resetLevel: "Ausschaltschwelle",
            resetSlider: "Schieberegler Ausschaltschwelle",
            signalAlerts: "Signalbenachrichtigungen",
            sendNotifications: "Signal-Benachrichtigungen",
            sendNotificationsNote: "Sendet eine Signal-Nachricht über CallMeBot, wenn das Signal umschaltet.",
            phoneLabel: "Signal Account UUID / Phone",
            phoneAria: "Signal Account UUID oder Telefonnummer",
            apikeyLabel: "CallMeBot-API-Schlüssel",
            quickSetupLabel: "CallMeBot-Aktivierungsnachricht",
            quickSetupHint: "Nachricht vom CallMeBot hier einfügen, um UUID und API-Key automatisch zu übernehmen.",
            quickSetupPlaceholder: "Aktivierungsnachricht hier einfügen (z. B. 'The APIKey created for you is: 653590 ... phone=98526357-0cec...')",
            quickSetupButton: "Extrahieren & Übernehmen",
            quickSetupAutoSaved: "Gespeichert und aktiv",
            gatewayActive: "CallMeBot (aktiv)",
            quickSetupSuccessBoth: "UUID & API-Schlüssel erfolgreich extrahiert",
            quickSetupSuccessPhone: "Signal Account UUID erfolgreich extrahiert",
            quickSetupSuccessApikey: "API-Schlüssel erfolgreich extrahiert",
            quickSetupNotFound: "Keine Signal Account UUID oder API-Schlüssel im eingefügten Text gefunden.",
            required: "Erforderlich",
            storedKeepHint: "Ein API-Schlüssel ist gespeichert. Feld leer lassen, um ihn zu behalten.",
            inverter: "Wechselrichter",
            smaIp: "SMA-IP-Adresse",
            smaIpAria: "IP-Adresse oder Hostname des SMA-Wechselrichters",
            httpsPort: "HTTPS-Port",
            inverterPassword: "Wechselrichter-Passwort",
            unchanged: "Unverändert",
            storedPasswordHint: "Ein Passwort ist gespeichert. Feld leer lassen, um es zu behalten.",
            renaultSection: "RENAULT ZOE",
            renaultNote: "Optionale MY-Renault-Zugangsdaten für Live-Ladezustand und Reichweite der Zoe.",
            renaultEmail: "Renault E-Mail",
            renaultPassword: "Renault Passwort",
            renaultVin: "Renault VIN",
            testRenaultConnection: "Verbindung testen",
            testingRenault: "Renault-Verbindung wird getestet…",
            renaultTestSuccess: "Renault Zoe: Verbindung erfolgreich hergestellt.",
            renaultTestFailed: "Renault-Verbindung fehlgeschlagen. Prüfen Sie E-Mail, Passwort und VIN.",
            renaultEmailInvalid: "Geben Sie eine gültige Renault-E-Mail-Adresse ein.",
            renaultVinInvalid: "Geben Sie eine gültige Fahrzeug-Identifizierungsnummer (VIN, z. B. VF1...) ein.",
            renaultCredentialsRequired: "Geben Sie zuerst Renault E-Mail, Passwort und VIN ein.",
            testNotification: "Testbenachrichtigung",
            saveSettings: "Einstellungen speichern",
            footnotePrefix: "Werte werden in",
            footnoteSuffix: "auf der CCU gespeichert und beim nächsten Lauf (innerhalb von zwei Minuten) übernommen.",
            displaySection: "Darstellung",
            animations: "ANIMATIONEN",
            animationsLabel: "ANIMATIONEN",
            animationsDesc: "(Ein / Aus)",
            animationsOn: "Ein",
            animationsOff: "Aus",

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
            ariaRail: "Ladezustand {soc} Prozent. Ausschalten unter {reset} Prozent, Einschalten ab {trigger} Prozent.",
            ariaNoHistory: "Noch kein Ladezustandsverlauf",
            ariaHistory: "Ladezustandsverlauf, {count} Messwerte, zuletzt {soc} Prozent um {time}.",
            captionActive: "Überschuss-Signal aktiv bei {soc}%. Schaltet ab unter {reset}%.",
            captionInactive: "Überschuss-Signal inaktiv bei {soc}%. Schaltet ein ab {trigger}%.",
            captionUnknown: "Noch kein Signalzustand bekannt. Zwischen {reset}% und {trigger}% bleibt der letzte Zustand erhalten.",
            signalCopyTriggered: "Aktiv. Schaltet ab unter {reset}%.",
            signalCopyHeld: "Aktiv. Schaltet ab unter {reset}%.",
            signalCopyActive: "Aktiv. Schaltet ab unter {reset}%.",
            signalCopyReset: "Inaktiv. Schaltet ein ab {trigger}%.",
            signalCopyInactive: "Inaktiv. Schaltet ein ab {trigger}%.",
            signalCopyUnknown: "Zwischen {reset}% und {trigger}% bleibt der Wert unverändert, bis eine Schwelle erreicht wird.",
            sampleOne: "1 Messwert",
            sampleMany: "{count} Messwerte",
            enabled: "Aktiviert",
            disabled: "Deaktiviert",
            historyRange: "{first} — {last}",
            pullRefresh: "Zum Aktualisieren ziehen",
            releaseRefresh: "Zum Aktualisieren loslassen",
            refreshing: "Wird aktualisiert",

            lastStateTitle: "Letzter bekannter Stand",
            lastStateText: "Abruf fehlgeschlagen ({error}). Nächster Versuch folgt automatisch.",
            waitingTitle: "Warte auf die erste Messung",
            waitingText: "Das Dashboard liest status.json nach dem ersten Wechselrichter-Abruf (maximal zwei Minuten nach der Installation).",
            statusErrorTitle: "status.json nicht lesbar",
            statusErrorText: "status.json konnte nicht geladen werden ({error}). Prüfen Sie, ob der Cron-Job gelaufen ist und lighttpd das Verzeichnis ausliefert.",
            demoTitle: "Sentinella-Vorschaudaten",
            demoText: "status.json ist nicht erreichbar. Es werden Beispieldaten gezeigt; öffnen Sie das Dashboard auf der CCU3 für Live-Messwerte.",

            savingConfig: "Konfiguration wird gespeichert…",
            configSavedApply: "Konfiguration gespeichert. Wird beim nächsten Lauf übernommen.",
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
            testFailedWarning: "Einstellungen gespeichert, aber die Testnachricht ist fehlgeschlagen. Prüfen Sie Signal Account UUID und API-Schlüssel.",
            savingBeforeTest: "Konfiguration wird vor dem Test gespeichert…",
            sendingTest: "Testbenachrichtigung wird gesendet…",
            languageSaveFailed: "Sprache lokal gespeichert. Das Backend konnte nicht aktualisiert werden.",
            triggerWhole: "Die Einschaltschwelle muss eine ganze Zahl zwischen 0 und 100 sein.",
            resetWhole: "Die Ausschaltschwelle muss eine ganze Zahl zwischen 0 und 100 sein.",
            resetLower: "Die Ausschaltschwelle muss unter der Einschaltschwelle liegen.",
            phoneInvalid: "Geben Sie eine gültige Signal Account UUID ein (zum Beispiel 98526357-0cec-4e7f-9d60-d854432414d3).",
            phoneRequired: "Wenn Benachrichtigungen aktiviert sind, ist eine Signal Account UUID erforderlich.",
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

        heroCard: $("hero-card") || $("hero-block"),
        heroBlock: $("hero-card") || $("hero-block"),
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

        pluginsSection: $("plugins-section"),
        pluginsGrid: $("plugins-grid"),

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
        signalEnabled: $("signal_enabled"),
        signalGroup: $("signal_credentials_group"),
        signalPhone: $("signal-phone"),
        signalApikey: $("signal-apikey"),
        apikeyHint: $("apikey-hint"),
        callmebotQuickSetup: $("callmebot-quick-setup"),
        callmebotPaste: $("callmebot-paste"),
        callmebotExtract: $("callmebot-extract"),
        callmebotStatus: $("callmebot-status"),
        smaIp: $("sma-ip"),
        smaPort: $("sma-port"),
        smaPassword: $("sma-password"),
        smaPasswordHint: $("sma-password-hint"),

        renaultUser: $("renault-user"),
        renaultPass: $("renault-pass"),
        renaultPassHint: $("renault-pass-hint"),
        renaultVin: $("renault-vin"),
        renaultTest: $("renault-test"),
        renaultTestStatus: $("renault-test-status"),
        animationsToggle: $("animations-toggle"),

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
        if (el.pluginsGrid && el.pluginsGrid.children && el.pluginsGrid.children.length) {
            translatePluginCardsInDom(currentLanguage);
        } else if (currentPlugins && currentPlugins.length) {
            renderPluginsList(currentPlugins);
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
            fetchAndRenderPlugins();
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
            return window.localStorage.getItem(THEME_KEY) === "dark" ? "dark" : "light";
        } catch (error) {
            return "light";
        }
    }

    function storeTheme(theme) {
        try {
            window.localStorage.setItem(THEME_KEY, theme);
        } catch (error) {
            /* Private mode or disabled storage: keep the theme for this visit. */
        }
    }

    function applyTheme(theme) {
        currentTheme = normalizeTheme(theme);
        document.documentElement.setAttribute("data-theme", currentTheme);

        var meta = document.querySelector('meta[name="theme-color"]');
        if (meta) {
            meta.setAttribute("content", currentTheme === "dark" ? "#09090b" : "#f8f8f9");
        }
        var scheme = document.querySelector('meta[name="color-scheme"]');
        if (scheme) {
            scheme.setAttribute("content", currentTheme);
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

    function isTelemetryCharging(data) {
        if (!data || typeof data !== "object") {
            return false;
        }
        if (data.charging === true || data.charging === "charging") {
            return true;
        }
        var chargeStatus = String(data.charge_status || "").toUpperCase();
        if (chargeStatus === "LADEN" || chargeStatus === "CHARGING") {
            return true;
        }
        var power = Number(data.battery_power != null ? data.battery_power : data.charge_power_w);
        if (!isNaN(power) && power > 50) {
            return true;
        }
        if (Number(data.chargingStatus) === 1) {
            return true;
        }
        var badge = String(data.status_badge || "").toUpperCase();
        return badge === "LÄDT" || badge === "CHARGING";
    }

    function updateUI(data) {
        return render(data);
    }

    function render(data) {
        var soc = data.soc;
        var thresholds = normalizeThresholds(data);
        var history = Array.isArray(data.history) ? data.history : [];
        var isCharging = isTelemetryCharging(data);
        var charge = isCharging ? "charging" : (data.charging || "unknown");
        var last = formatTime(data.generated_at);
        var tone = signalTone(data);

        current = data;
        document.body.dataset.state = "ready";

        if (el.heroBlock) {
            el.heroBlock.dataset.charge = charge;
            el.heroBlock.classList.toggle("is-charging", isCharging);
        }
        if (el.heroCard && el.heroCard !== el.heroBlock) {
            el.heroCard.dataset.charge = charge;
            el.heroCard.classList.toggle("is-charging", isCharging);
        }
        if (el.gauge) {
            el.gauge.classList.toggle("is-charging", isCharging);
        }
        if (el.socValue) {
            el.socValue.classList.toggle("is-charging", isCharging);
        }

        /* Inverter state classes: .state-charging, .state-discharging, .state-idle */
        var power = NaN;
        if (data.battery_power != null && !isNaN(Number(data.battery_power))) {
            power = Number(data.battery_power);
        } else if (data.charge_power_w != null || data.discharge_power_w != null) {
            var cp = Number(data.charge_power_w) || 0;
            var dp = Number(data.discharge_power_w) || 0;
            power = cp - dp;
        }

        var statusUpper = String(data.status || data.status_badge || data.charging || "").toUpperCase();

        var isStateCharging = (!isNaN(power) && power > 50) ||
            statusUpper === "LADEN" || statusUpper === "CHARGING" ||
            (data.charging === "charging" && (isNaN(power) || power > 0));

        var isStateDischarging = !isStateCharging && (
            (!isNaN(power) && power < -50) ||
            statusUpper === "ENTLADEN" || statusUpper === "DISCHARGING" ||
            (data.charging === "discharging" && (isNaN(power) || power < 0))
        );

        var heroTargets = [el.heroCard, el.heroBlock].filter(Boolean);
        heroTargets.forEach(function (card) {
            card.classList.remove("state-charging", "state-discharging", "state-idle");
            if (isStateCharging) {
                card.classList.add("state-charging");
            } else if (isStateDischarging) {
                card.classList.add("state-discharging");
            } else {
                card.classList.add("state-idle");
            }
        });

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
        var gateway = data.signal_gateway || "";
        if (gateway === "CallMeBot (aktiv)") {
            gateway = t("gatewayActive");
        } else if (gateway === "Deaktiviert") {
            gateway = t("disabled");
        }
        el.metaNotify.textContent = gateway || (data.signal_notifications ? t("enabled") : t("disabled"));
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
                fetchAndRenderPlugins();
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
            signal_gateway: "Deaktiviert",
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
        fetchAndRenderPlugins();
    }

    /* ----------------------------------------------------------------------
       Plugins Ingest & Rendering
       ---------------------------------------------------------------------- */

    var PLUGIN_I18N = {
        en: {
            "RENAULT ZOE": "RENAULT ZOE",
            "Renault Zoe": "RENAULT ZOE",
            "BATTERIE SOC": "BATTERY SOC",
            "Batterie SoC": "BATTERY SOC",
            "BATTERY SOC": "BATTERY SOC",
            "REICHWEITE": "RANGE",
            "Reichweite": "RANGE",
            "RANGE": "RANGE",
            "STECKER": "CONNECTOR",
            "CONNECTOR": "CONNECTOR",
            "LÄDT": "CHARGING",
            "CHARGING": "CHARGING",
            "VERBUNDEN": "CONNECTED",
            "CONNECTED": "CONNECTED",
            "BEREIT": "READY",
            "READY": "READY",
            "Gesteckt": "Connected",
            "Connected": "Connected",
            "Getrennt": "Disconnected",
            "Disconnected": "Disconnected",
            "PV-PROGNOSE": "SOLAR FORECAST",
            "Solarprognose": "SOLAR FORECAST",
            "SOLAR FORECAST": "SOLAR FORECAST",
            "PROGNOSE HEUTE": "TODAY FORECAST",
            "TAGESERTRAG": "TODAY FORECAST",
            "Heute": "TODAY FORECAST",
            "TODAY FORECAST": "TODAY FORECAST",
            "von gestern": "vs yesterday",
            "vs. Gestern": "vs yesterday",
            "vs. morgen": "vs yesterday",
            "vs yesterday": "vs yesterday",
            "SONNIG": "SUNNY",
            "OPTIMAL": "SUNNY",
            "SUNNY": "SUNNY",
            "MODERAT": "MODERATE",
            "GUT": "MODERATE",
            "MODERATE": "MODERATE",
            "BEWÖLKT": "CLOUDY",
            "WENIG": "CLOUDY",
            "KEINE SONNE": "CLOUDY",
            "CLOUDY": "CLOUDY",
            "MORGEN": "TOMORROW",
            "Morgen": "TOMORROW",
            "TOMORROW": "TOMORROW",
            "SPITZE": "PEAK",
            "Spezifisch": "PEAK",
            "PEAK": "PEAK",
            "LADELEISTUNG": "CHARGING POWER",
            "Ladeleistung": "Charging power",
            "Charging power": "Charging power",
            "CHARGING POWER": "CHARGING POWER",
            "PHASEN": "PHASES",
            "Phasen": "Phases",
            "Phases": "Phases",
            "STROM": "CURRENT",
            "Strom": "Current",
            "Current": "Current",
            "Verbunden": "Connected",
            "Ja": "Yes",
            "Yes": "Yes",
            "Nein": "No",
            "No": "No",
            "KLIMATISIERUNG STARTEN": "START PRE-CONDITIONING",
            "START PRE-CONDITIONING": "START PRE-CONDITIONING",
            "WIRD GESTARTET...": "STARTING...",
            "STARTING...": "STARTING...",
            "KLIMATISIERUNG AKTIV": "PRE-CONDITIONING ACTIVE",
            "KLIMA LÄUFT (STOPPEN)": "HVAC RUNNING (STOP)",
            "HVAC RUNNING (STOP)": "HVAC RUNNING (STOP)",
            "WIRD BEENDET...": "STOPPING...",
            "STOPPING...": "STOPPING...",
            "KILOMETERSTAND": "ODOMETER",
            "ODOMETER": "ODOMETER",
            "PRE-CONDITIONING ACTIVE": "PRE-CONDITIONING ACTIVE",
            "Keine Verbindung zu forecast.solar": "No connection to forecast.solar",
            "No connection to forecast.solar": "No connection to forecast.solar"
        },
        de: {
            "RENAULT ZOE": "RENAULT ZOE",
            "Renault Zoe": "RENAULT ZOE",
            "BATTERY SOC": "BATTERIE SOC",
            "BATTERIE SOC": "BATTERIE SOC",
            "Batterie SoC": "BATTERIE SOC",
            "RANGE": "REICHWEITE",
            "REICHWEITE": "REICHWEITE",
            "Reichweite": "REICHWEITE",
            "CONNECTOR": "STECKER",
            "STECKER": "STECKER",
            "CHARGING": "LÄDT",
            "LÄDT": "LÄDT",
            "CONNECTED": "VERBUNDEN",
            "VERBUNDEN": "VERBUNDEN",
            "READY": "BEREIT",
            "BEREIT": "BEREIT",
            "Connected": "Gesteckt",
            "Gesteckt": "Gesteckt",
            "Disconnected": "Getrennt",
            "Getrennt": "Getrennt",
            "SOLAR FORECAST": "PV-PROGNOSE",
            "PV-PROGNOSE": "PV-PROGNOSE",
            "Solarprognose": "PV-PROGNOSE",
            "TODAY FORECAST": "PROGNOSE HEUTE",
            "PROGNOSE HEUTE": "PROGNOSE HEUTE",
            "TAGESERTRAG": "PROGNOSE HEUTE",
            "Heute": "PROGNOSE HEUTE",
            "vs yesterday": "von gestern",
            "von gestern": "von gestern",
            "vs. Gestern": "von gestern",
            "vs. morgen": "von gestern",
            "SUNNY": "SONNIG",
            "SONNIG": "SONNIG",
            "OPTIMAL": "SONNIG",
            "MODERATE": "MODERAT",
            "MODERAT": "MODERAT",
            "GUT": "MODERAT",
            "CLOUDY": "BEWÖLKT",
            "BEWÖLKT": "BEWÖLKT",
            "WENIG": "BEWÖLKT",
            "KEINE SONNE": "BEWÖLKT",
            "TOMORROW": "MORGEN",
            "MORGEN": "MORGEN",
            "Morgen": "MORGEN",
            "PEAK": "SPITZE",
            "SPITZE": "SPITZE",
            "Spezifisch": "SPITZE",
            "CHARGING POWER": "LADELEISTUNG",
            "Charging power": "Ladeleistung",
            "Ladeleistung": "Ladeleistung",
            "LADELEISTUNG": "LADELEISTUNG",
            "PHASES": "PHASEN",
            "Phases": "Phasen",
            "Phasen": "Phasen",
            "CURRENT": "STROM",
            "Current": "Strom",
            "Strom": "Strom",
            "Verbunden": "Verbunden",
            "Yes": "Ja",
            "Ja": "Ja",
            "No": "Nein",
            "Nein": "Nein",
            "START PRE-CONDITIONING": "KLIMATISIERUNG STARTEN",
            "KLIMATISIERUNG STARTEN": "KLIMATISIERUNG STARTEN",
            "STARTING...": "WIRD GESTARTET...",
            "WIRD GESTARTET...": "WIRD GESTARTET...",
            "PRE-CONDITIONING ACTIVE": "KLIMATISIERUNG AKTIV",
            "KLIMATISIERUNG AKTIV": "KLIMATISIERUNG AKTIV",
            "KLIMA LÄUFT (STOPPEN)": "KLIMA LÄUFT (STOPPEN)",
            "HVAC RUNNING (STOP)": "KLIMA LÄUFT (STOPPEN)",
            "WIRD BEENDET...": "WIRD BEENDET...",
            "STOPPING...": "WIRD BEENDET...",
            "KILOMETERSTAND": "KILOMETERSTAND",
            "ODOMETER": "KILOMETERSTAND",
            "No connection to forecast.solar": "Keine Verbindung zu forecast.solar",
            "Keine Verbindung zu forecast.solar": "Keine Verbindung zu forecast.solar"
        }
    };

    function translatePluginText(value, targetLang) {
        if (value === null || value === undefined) {
            return "";
        }
        var raw = String(value);
        var requested = targetLang || currentLanguage;
        var lang = (typeof requested === "string" && PLUGIN_I18N[requested])
            ? requested : "de";
        var dict = PLUGIN_I18N[lang];
        if (dict && Object.prototype.hasOwnProperty.call(dict, raw)) {
            return dict[raw];
        }
        if (typeof I18N === "object" && I18N && I18N[lang] &&
                Object.prototype.hasOwnProperty.call(I18N[lang], raw)) {
            return I18N[lang][raw];
        }
        return raw;
    }

    function escapeHtml(str) {
        if (str === null || str === undefined) {
            return "";
        }
        return String(str)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function renderSecondaryMetrics(metrics) {
        if (!Array.isArray(metrics) || !metrics.length) {
            return "";
        }
        var rows = metrics.map(function (m) {
            var rawLabel = (m && m.label) ? String(m.label) : "";
            var rawValue = (m && m.value !== undefined) ? String(m.value) : "";
            var rawUnit = (m && m.unit) ? String(m.unit) : "";
            var label = escapeHtml(translatePluginText(rawLabel, currentLanguage));
            var value = escapeHtml(translatePluginText(rawValue, currentLanguage));
            var unit = rawUnit ? (" " + escapeHtml(rawUnit)) : "";
            var unitAttr = rawUnit ? (' data-orig-unit="' + escapeHtml(rawUnit) + '"') : "";
            return (
                '<div class="plugin-list-row">' +
                    '<span class="plugin-list-label" data-orig-text="' + escapeHtml(rawLabel) + '">' + label + '</span>' +
                    '<span class="plugin-list-value" data-orig-text="' + escapeHtml(rawValue) + '"' + unitAttr + '>' + value + unit + '</span>' +
                '</div>'
            );
        }).join("");
        return '<div class="plugin-secondary-metrics">' + rows + '</div>';
    }

    function renderPluginCard(plugin) {
        var card = document.createElement("div");
        card.className = "plugin-card";
        if (plugin && plugin.id) {
            card.setAttribute("data-plugin-id", String(plugin.id));
        }

        var rawLayout = (plugin && plugin.layout) || "stat";
        var layout = (rawLayout === "stat" || rawLayout === "delta" || rawLayout === "list")
            ? rawLayout : "stat";
        var rawTitle = (plugin && plugin.title) ? String(plugin.title) : "";
        var title = escapeHtml(translatePluginText(rawTitle, currentLanguage));
        var rawBadge = (plugin && plugin.status_badge) ? String(plugin.status_badge) : "";
        var badgeText = rawBadge ? escapeHtml(translatePluginText(rawBadge, currentLanguage)) : "";
        var rawColor = String((plugin && plugin.status_color) || "neutral");
        var badgeColor = (rawColor === "green" || rawColor === "blue" ||
            rawColor === "red" || rawColor === "neutral") ? rawColor : "neutral";
        var pluginCharging = (plugin && (plugin.charging === true ||
            Number(plugin.chargingStatus) === 1 ||
            rawBadge.toUpperCase() === "LÄDT" ||
            rawBadge.toUpperCase() === "CHARGING"));

        var badgeHtml = badgeText ? (
            '<span class="plugin-badge" data-color="' + badgeColor + '" data-orig-text="' + escapeHtml(rawBadge) + '">' + badgeText + '</span>'
        ) : "";

        var headerHtml = (
            '<div class="plugin-header">' +
                '<h3 class="plugin-title" data-orig-text="' + escapeHtml(rawTitle) + '">' + title + '</h3>' +
                badgeHtml +
            '</div>'
        );

        var bodyHtml = "";

        if (layout === "delta") {
            var delta = (plugin && plugin.delta) || {};
            var rawDir = String(delta.direction || "neutral");
            rawDir = (rawDir === "up" || rawDir === "down" || rawDir === "neutral")
                ? rawDir : "neutral";
            var deltaDir = escapeHtml(rawDir);
            var deltaVal = escapeHtml((delta.value !== undefined) ? String(delta.value) : "");
            var rawDeltaComp = delta.comparison ? String(delta.comparison) : "";
            var deltaComp = escapeHtml(translatePluginText(rawDeltaComp, currentLanguage));

            var primary = (plugin && plugin.primary) || {};
            var priVal = escapeHtml((primary.value !== undefined) ? String(primary.value) : "");
            var priUnit = primary.unit ? escapeHtml(primary.unit) : "";

            var arrow = "";
            if (deltaDir === "up") {
                arrow = "↑ ";
            } else if (deltaDir === "down") {
                arrow = "↓ ";
            }

            bodyHtml = (
                '<div class="plugin-delta-hero">' +
                    '<span class="plugin-stat-value">' + priVal + '</span>' +
                    (priUnit ? '<span class="plugin-stat-unit">' + priUnit + '</span>' : '') +
                    (deltaVal ? (
                        '<span class="plugin-delta-badge" data-direction="' + deltaDir + '">' +
                            arrow + deltaVal +
                        '</span>'
                    ) : '') +
                '</div>' +
                (deltaComp ? '<div class="plugin-delta-comparison" data-orig-text="' + escapeHtml(rawDeltaComp) + '">' + deltaComp + '</div>' : '') +
                renderSecondaryMetrics(plugin && plugin.secondary_metrics)
            );
        } else if (layout === "list") {
            var metrics = (plugin && plugin.secondary_metrics) || [];
            var listRows = "";
            if (Array.isArray(metrics) && metrics.length) {
                listRows = metrics.map(function (m) {
                    var rawL = (m && m.label) ? String(m.label) : "";
                    var rawV = (m && m.value !== undefined) ? String(m.value) : "";
                    var rawU = (m && m.unit) ? String(m.unit) : "";
                    var l = escapeHtml(translatePluginText(rawL, currentLanguage));
                    var v = escapeHtml(translatePluginText(rawV, currentLanguage));
                    var u = rawU ? (" " + escapeHtml(rawU)) : "";
                    var uAttr = rawU ? (' data-orig-unit="' + escapeHtml(rawU) + '"') : "";
                    return (
                        '<li class="plugin-list-row">' +
                            '<span class="plugin-list-label" data-orig-text="' + escapeHtml(rawL) + '">' + l + '</span>' +
                            '<span class="plugin-list-value" data-orig-text="' + escapeHtml(rawV) + '"' + uAttr + '>' + v + u + '</span>' +
                        '</li>'
                    );
                }).join("");
            }
            bodyHtml = '<ul class="plugin-list-metrics">' + listRows + '</ul>';
        } else {
            /* Fallback to 'stat' layout */
            var primaryStat = (plugin && plugin.primary) || {};
            var statVal = escapeHtml((primaryStat.value !== undefined) ? String(primaryStat.value) : "");
            var statUnit = primaryStat.unit ? escapeHtml(primaryStat.unit) : "";
            var rawStatLabel = primaryStat.label ? String(primaryStat.label) : "";
            var statLabel = rawStatLabel ? escapeHtml(translatePluginText(rawStatLabel, currentLanguage)) : "";
            var heroClass = pluginCharging ? "plugin-stat-hero is-charging" : "plugin-stat-hero";
            /* Active HVAC controls are temporarily shelved. The Zoe card is a
               read-only telemetry card; the button markup below is retained
               (commented) for a future re-enablement.
            var isRenaultZoe = Boolean(
                (plugin && plugin.id === "renault_zoe") ||
                rawTitle.toUpperCase() === "RENAULT ZOE"
            );
            var hvacIsActive = Boolean(plugin && plugin.hvac_active === true);
            var hvacBtnKey = hvacIsActive ? "KLIMA LÄUFT (STOPPEN)" : "KLIMATISIERUNG STARTEN";
            var hvacBtnLabel = escapeHtml(translatePluginText(hvacBtnKey, currentLanguage));
            var hvacActionHtml = isRenaultZoe ? (
                '<div class="plugin-action-bar">' +
                    '<button type="button" class="plugin-action-btn' + (hvacIsActive ? ' is-active' : '') +
                        '" data-plugin-action="' + (hvacIsActive ? 'stop-hvac' : 'start-hvac') +
                        '" data-hvac-state="' + (hvacIsActive ? 'active' : 'idle') +
                        '" data-orig-text="' + hvacBtnKey + '">' +
                        hvacBtnLabel +
                    '</button>' +
                '</div>'
            ) : "";
            */

            bodyHtml = (
                '<div class="' + heroClass + '">' +
                    '<span class="plugin-stat-value">' + statVal + '</span>' +
                    (statUnit ? '<span class="plugin-stat-unit">' + statUnit + '</span>' : '') +
                '</div>' +
                (statLabel ? '<div class="plugin-stat-label" data-orig-text="' + escapeHtml(rawStatLabel) + '">' + statLabel + '</div>' : '') +
                renderSecondaryMetrics(plugin && plugin.secondary_metrics)
            );
        }

        card.innerHTML = headerHtml + bodyHtml;
        return card;
    }

    function isRenaultZoeCard(plugin) {
        return Boolean(
            plugin &&
            (plugin.id === "renault_zoe" ||
                (typeof plugin.title === "string" && plugin.title.toUpperCase() === "RENAULT ZOE"))
        );
    }

    function pluginTimestampMs(plugin) {
        var parsed = Date.parse((plugin && plugin.timestamp) || "");
        return isNaN(parsed) ? 0 : parsed;
    }

    function applyHvacStopOverride(plugins) {
        if (!hvacStopOverrideAt || !Array.isArray(plugins)) {
            return;
        }
        plugins.forEach(function (plugin) {
            if (!isRenaultZoeCard(plugin)) {
                return;
            }
            if (pluginTimestampMs(plugin) > hvacStopOverrideAt) {
                /* This poll was produced after the local stop request. */
                hvacStopOverrideAt = 0;
            } else {
                plugin.hvac_active = false;
            }
        });
    }

    function setPluginHvacActive(active) {
        if (!Array.isArray(currentPlugins)) {
            return;
        }
        currentPlugins.forEach(function (plugin) {
            if (isRenaultZoeCard(plugin)) {
                plugin.hvac_active = active === true;
            }
        });
    }

    function setHvacButtonState(button, state) {
        var map = {
            idle: ["KLIMATISIERUNG STARTEN", "start-hvac", false],
            starting: ["WIRD GESTARTET...", "start-hvac", true],
            active: ["KLIMA LÄUFT (STOPPEN)", "stop-hvac", false],
            stopping: ["WIRD BEENDET...", "stop-hvac", true]
        };
        var cfg = map[state] || map.idle;
        button.disabled = cfg[2];
        button.classList.toggle("is-starting", state === "starting");
        button.classList.toggle("is-stopping", state === "stopping");
        button.classList.toggle("is-active", state === "active" || state === "stopping");
        button.setAttribute("data-hvac-state", state);
        button.setAttribute("data-plugin-action", cfg[1]);
        button.setAttribute("data-orig-text", cfg[0]);
        button.textContent = translatePluginText(cfg[0], currentLanguage);
    }

    var hvacAutoResetTimer = null;

    function triggerZoeHvac(button, mode) {
        if (!button || button.disabled) {
            return;
        }
        var stopping = mode === "stop";
        if (stopping) {
            /* Reflect the stop immediately: idle button, card inactive. A
               background poll may only override this once it is newer. */
            hvacStopOverrideAt = Date.now();
            setPluginHvacActive(false);
            setHvacButtonState(button, "idle");
        } else {
            setHvacButtonState(button, "starting");
        }
        if (hvacAutoResetTimer) {
            window.clearTimeout(hvacAutoResetTimer);
            hvacAutoResetTimer = null;
        }

        function done(res) {
            if (stopping) {
                setHvacButtonState(button, "idle");
                setPluginHvacActive(false);
            } else {
                setHvacButtonState(button, "active");
            }
            var okText = stopping ? "KLIMATISIERUNG STARTEN" : "KLIMA LÄUFT (STOPPEN)";
            showToast("success", (res && res.message) || translatePluginText(okText, currentLanguage));
            if (!stopping) {
                /* Pre-conditioning runs for 10 minutes; the next poll corrects earlier. */
                hvacAutoResetTimer = window.setTimeout(function () {
                    if (button.getAttribute("data-hvac-state") === "active") {
                        setHvacButtonState(button, "idle");
                    }
                }, 600000);
            }
        }

        if (demoRequested) {
            window.setTimeout(function () { done(null); }, 600);
            return;
        }

        requestJSON(ACTION_ZOE_URL, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action: stopping ? "stop" : "start" })
        }).then(done).catch(function (error) {
            if (stopping) {
                hvacStopOverrideAt = 0;
                setPluginHvacActive(true);
                setHvacButtonState(button, "active");
            } else {
                setHvacButtonState(button, "idle");
            }
            showToast("error", (error && error.message) || "HVAC request failed.");
        });
    }

    function translatePluginCardsInDom(targetLang) {
        if (!el.pluginsGrid) {
            return;
        }
        var lang = targetLang || currentLanguage;
        var nodes = el.pluginsGrid.querySelectorAll("[data-orig-text]");
        for (var i = 0; i < nodes.length; i += 1) {
            var node = nodes[i];
            var orig = node.getAttribute("data-orig-text") || "";
            var unit = node.getAttribute("data-orig-unit") || "";
            var translated = translatePluginText(orig, lang);
            node.textContent = unit ? (translated + " " + unit) : translated;
        }
    }

    function renderPluginsList(plugins) {
        if (!el.pluginsSection || !el.pluginsGrid) {
            return;
        }
        if (!Array.isArray(plugins) || !plugins.length) {
            el.pluginsSection.style.display = "none";
            el.pluginsGrid.innerHTML = "";
            return;
        }
        var fragment = document.createDocumentFragment();
        plugins.forEach(function (plugin) {
            if (plugin && typeof plugin === "object") {
                fragment.appendChild(renderPluginCard(plugin));
            }
        });
        el.pluginsGrid.innerHTML = "";
        el.pluginsGrid.appendChild(fragment);
        el.pluginsSection.style.display = "";
    }

    function fetchAndRenderPlugins() {
        if (!el.pluginsSection || !el.pluginsGrid) {
            return;
        }

        var url = new URL(PLUGINS_URL.href);
        url.searchParams.set("t", String(Date.now()));

        fetch(url.toString(), { cache: "no-store" })
            .then(function (res) {
                if (!res.ok) {
                    throw new Error("HTTP " + res.status);
                }
                return res.json();
            })
            .then(function (plugins) {
                currentPlugins = Array.isArray(plugins) ? plugins : [];
                applyHvacStopOverride(currentPlugins);
                renderPluginsList(currentPlugins);
            })
            .catch(function () {
                if (!currentPlugins || !currentPlugins.length) {
                    currentPlugins = [];
                    el.pluginsSection.style.display = "none";
                    el.pluginsGrid.innerHTML = "";
                }
            });
    }

    /* ----------------------------------------------------------------------
       Settings drawer
       ---------------------------------------------------------------------- */

    function requestJSON(url, options) {
        var opts = Object.assign(
            { cache: "no-store", credentials: "same-origin" },
            options || {}
        );
        // Always carry a custom header. HTML forms cannot set it, so a
        // cross-site form POST to the CGI endpoints is rejected even when the
        // browser omits Origin.
        opts.headers = Object.assign(
            { "X-Requested-With": "XMLHttpRequest" },
            (options && options.headers) || {}
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
        el.settingsTest.disabled = busy || (el.signalEnabled && !el.signalEnabled.checked);
        if (el.renaultTest) {
            el.renaultTest.disabled = busy;
        }
        el.settingsForm.setAttribute("aria-busy", busy ? "true" : "false");
    }

    function showRenaultTestFeedback(tone, message) {
        if (!el.renaultTestStatus) {
            return;
        }
        if (!message) {
            el.renaultTestStatus.hidden = true;
            el.renaultTestStatus.textContent = "";
            delete el.renaultTestStatus.dataset.tone;
            return;
        }
        el.renaultTestStatus.hidden = false;
        el.renaultTestStatus.dataset.tone = tone || "success";
        el.renaultTestStatus.textContent = message;
    }

    function isSignalUuid(value) {
        return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(String(value || "").trim());
    }

    function isSignalRecipient(value) {
        var token = String(value || "").trim();
        return isSignalUuid(token) || /^\+?[0-9]{7,15}$/.test(token);
    }

    function isSignalPhoneList(value) {
        var tokens = String(value || "").trim().split(/[\s,;]+/).filter(Boolean);
        if (!tokens.length) {
            return false;
        }
        return tokens.every(function (token) {
            var parts = token.split(":");
            if (parts.length === 1) {
                return isSignalRecipient(parts[0]);
            }
            if (parts.length === 2) {
                return isSignalRecipient(parts[0]) && /^[A-Za-z0-9._-]+$/.test(parts[1]);
            }
            return false;
        });
    }

    function parseCallMeBotMessage(raw) {
        var text = String(raw || "");
        var apikey = "";
        var phone = "";
        var uuidPattern = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";
        var uuidRe = new RegExp(uuidPattern);

        function clean(value) {
            return String(value == null ? "" : value)
                .replace(/^[\s"'`<>()\[\]{}]+/, "")
                .replace(/[\s"'`<>()\[\]{},.;:!?]+$/, "");
        }

        function normalizePhone(value) {
            var cleaned = clean(value);
            if (!cleaned) {
                return "";
            }
            if (uuidRe.test(cleaned)) {
                var uuidMatch = cleaned.match(uuidRe);
                return uuidMatch ? uuidMatch[0] : "";
            }
            var digits = cleaned.replace(/[\s().\-\/]/g, "");
            if (/^\+[0-9]{7,15}$/.test(digits) || /^[0-9]{7,15}$/.test(digits)) {
                return digits;
            }
            return "";
        }

        var pairMatch = text.match(new RegExp("\\b(" + uuidPattern + "):([A-Za-z0-9._-]+)"));
        if (pairMatch) {
            return {
                phone: pairMatch[1],
                apikey: clean(pairMatch[2])
            };
        }

        var phoneParamMatch = text.match(/\bphone["'`]?\s*[:=]\s*["'`]?\s*([^"'`&\n\r,;]+)/i);
        if (phoneParamMatch) {
            phone = normalizePhone(phoneParamMatch[1]);
        }
        if (!phone) {
            var anyUuidMatch = text.match(uuidRe);
            if (anyUuidMatch) {
                phone = anyUuidMatch[0];
            }
        }

        var keyPatterns = [
            /[?&]apikey\s*=\s*["'`]?([A-Za-z0-9._-]+)/i,
            /API\s*Key\s+created\s+for\s+you\s+is\s*[:=]?\s*["'`]?([A-Za-z0-9._-]+)/i,
            /\bapi[_-\s]*key["'`]?\s*[:=]\s*["'`]?([A-Za-z0-9._-]+)/i
        ];
        for (var i = 0; i < keyPatterns.length && !apikey; i += 1) {
            var keyMatch = text.match(keyPatterns[i]);
            if (keyMatch) {
                apikey = clean(keyMatch[1]);
            }
        }

        if (!apikey && phone) {
            var withoutPhone = text.split(phone).join(" ").replace(new RegExp(uuidPattern, "g"), " ");
            var pureTokenMatch = withoutPhone.match(/(?:^|[\s,;])([0-9]{5,12}|[A-Za-z0-9]{6,32})(?=$|[\s,;])/);
            if (pureTokenMatch) {
                apikey = clean(pureTokenMatch[1]);
            }
        }

        return {
            phone: phone,
            apikey: apikey
        };
    }

    var extractHighlightTimer = null;

    function showQuickSetupFeedback(tone, message) {
        if (!el.callmebotStatus) {
            return;
        }
        if (!message) {
            el.callmebotStatus.hidden = true;
            el.callmebotStatus.textContent = "";
            delete el.callmebotStatus.dataset.tone;
            return;
        }
        el.callmebotStatus.hidden = false;
        el.callmebotStatus.dataset.tone = tone || "success";
        el.callmebotStatus.textContent = message;
    }

    function flashExtractedFields(fields) {
        clearTimeout(extractHighlightTimer);
        fields.forEach(function (field) {
            if (field) {
                field.classList.add("input-extracted");
            }
        });
        extractHighlightTimer = setTimeout(function () {
            fields.forEach(function (field) {
                if (field) {
                    field.classList.remove("input-extracted");
                }
            });
        }, 2400);
    }

    function isMaskedApiKey(value) {
        var text = String(value || "").trim();
        return text !== "" && /^[\u2022\u00b7*.]+$/.test(text);
    }

    function setSignalLocked(locked) {
        var group = el.signalGroup;
        if (el.signalEnabled) {
            el.signalEnabled.disabled = false;
        }
        if (group) {
            group.classList.toggle("is-locked", locked);
            var controls = group.querySelectorAll("input, textarea, button, select");
            for (var i = 0; i < controls.length; i += 1) {
                controls[i].disabled = locked;
            }
        }
        if (el.settingsTest) {
            el.settingsTest.disabled = locked;
        }
    }

    function autoSaveParsedCredentials(phone, apikey) {
        if (demoRequested || !settingsAvailable) {
            var localMsg = t("quickSetupSuccessBoth");
            showQuickSetupFeedback("success", localMsg);
            showSettingsStatus("success", localMsg);
            showToast("success", localMsg);
            return Promise.resolve(null);
        }

        if (el.signalEnabled) {
            el.signalEnabled.checked = true;
        }
        setSignalLocked(false);

        var payload = {
            signal_phone: phone,
            signal_apikey: apikey,
            signal_enabled: true
        };

        setSettingsBusy(true);
        return requestJSON(CONFIG_URL, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        }).then(function (data) {
            fillSettingsForm(data);
            adoptConfig(data);
            var savedMsg = t("quickSetupAutoSaved");
            showQuickSetupFeedback("success", savedMsg);
            showSettingsStatus("success", savedMsg);
            showToast("success", savedMsg);
            load();
            return data;
        }).catch(function (error) {
            showQuickSetupFeedback("error", error.message);
            showSettingsStatus("error", error.message);
            showToast("error", error.message);
            return null;
        }).finally(function () {
            setSettingsBusy(false);
        });
    }

    function applyCallMeBotMessage(raw, silentIfEmpty) {
        var parsed = parseCallMeBotMessage(raw);
        if (!parsed.phone && !parsed.apikey) {
            if (!silentIfEmpty) {
                showQuickSetupFeedback("error", t("quickSetupNotFound"));
                showToast("error", t("quickSetupNotFound"));
            }
            return false;
        }

        var updatedFields = [];
        if (parsed.phone) {
            el.signalPhone.value = parsed.phone;
            updatedFields.push(el.signalPhone);
        }
        if (parsed.apikey) {
            el.signalApikey.value = parsed.apikey;
            updatedFields.push(el.signalApikey);
        }
        if (el.callmebotPaste) {
            el.callmebotPaste.value = "";
        }

        flashExtractedFields(updatedFields);

        if (parsed.phone && parsed.apikey) {
            if (el.signalEnabled) {
                el.signalEnabled.checked = true;
            }
            setSignalLocked(false);
            autoSaveParsedCredentials(parsed.phone, parsed.apikey);
            return true;
        }

        var messageKey = parsed.phone ? "quickSetupSuccessPhone" : "quickSetupSuccessApikey";
        var message = t(messageKey);
        showQuickSetupFeedback("success", message);
        showSettingsStatus("success", message);
        showToast("success", message);
        return true;
    }

    function fillSettingsForm(data) {
        el.triggerSoc.value = String(data.trigger_soc);
        el.resetSoc.value = String(data.reset_soc);
        el.triggerRange.value = String(data.trigger_soc);
        el.resetRange.value = String(data.reset_soc);

        el.signalEnabled.checked = data.signal_enabled === true;
        el.signalPhone.value = String(data.signal_phone || "").trim();
        el.signalApikey.value = "";
        el.signalApikey.placeholder = data.signal_apikey_set ? t("unchanged") : t("required");
        el.apikeyHint.hidden = !data.signal_apikey_set;
        setSignalLocked(!el.signalEnabled.checked);

        el.smaIp.value = data.sma_ip || "";
        el.smaPort.value = String(data.sma_port || 443);
        el.smaPassword.value = "";
        el.smaPassword.placeholder = data.sma_password_set ? t("unchanged") : t("required");
        el.smaPasswordHint.hidden = !data.sma_password_set;

        if (el.renaultUser) {
            el.renaultUser.value = String(data.renault_user || "").trim();
        }
        if (el.renaultVin) {
            el.renaultVin.value = String(data.renault_vin || "").trim().toUpperCase();
        }
        if (el.renaultPass) {
            var renaultPassSet = Boolean(data.renault_pass_set || data.renault_password_set);
            el.renaultPass.value = "";
            el.renaultPass.placeholder = renaultPassSet ? "••••••••" : t("unchanged");
            if (el.renaultPassHint) {
                el.renaultPassHint.hidden = !renaultPassSet;
            }
        }
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
            if (data.signal_phone && (data.signal_apikey_set === true || String(data.signal_phone).indexOf(":") !== -1)) {
                current.signal_gateway = "CallMeBot (aktiv)";
            } else if (data.signal_phone === "" || data.signal_apikey_set === false) {
                current.signal_gateway = "Deaktiviert";
            }
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
        if (phone && !isSignalPhoneList(phone)) {
            return {
                field: "signal-phone",
                text: t("phoneInvalid")
            };
        }
        el.signalPhone.value = phone;

        var apikey = el.signalApikey.value.trim();
        if (el.signalEnabled.checked) {
            if (!phone) {
                return {
                    field: "signal-phone",
                    text: t("phoneRequired")
                };
            }
            var hasInlineKey = phone.indexOf(":") >= 0;
            if (!hasInlineKey && !apikey && !(liveConfig && liveConfig.signal_apikey_set)) {
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

        if (el.renaultUser) {
            var rUser = el.renaultUser.value.trim();
            el.renaultUser.value = rUser;
            if (rUser && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(rUser)) {
                return {
                    field: "renault-user",
                    text: t("renaultEmailInvalid")
                };
            }
        }

        if (el.renaultVin) {
            var rVin = el.renaultVin.value.trim().toUpperCase();
            el.renaultVin.value = rVin;
            if (rVin && !/^[A-Z0-9]{6,24}$/.test(rVin)) {
                return {
                    field: "renault-vin",
                    text: t("renaultVinInvalid")
                };
            }
        }

        return null;
    }

    function buildPayload() {
        var phone = el.signalPhone.value.trim();
        el.signalPhone.value = phone;
        var payload = {
            trigger_soc: Number(el.triggerSoc.value.trim()),
            reset_soc: Number(el.resetSoc.value.trim()),
            signal_enabled: el.signalEnabled.checked,
            signal_phone: phone,
            sma_ip: el.smaIp.value.trim(),
            sma_port: Number(el.smaPort.value.trim())
        };

        var apikey = el.signalApikey.value.trim();
        if (apikey && !isMaskedApiKey(apikey)) {
            payload.signal_apikey = apikey;
        }
        var password = el.smaPassword.value;
        if (password) {
            payload.sma_password = password;
        }
        if (el.renaultUser) {
            payload.renault_user = el.renaultUser.value.trim();
        }
        if (el.renaultVin) {
            var rVin = el.renaultVin.value.trim().toUpperCase();
            el.renaultVin.value = rVin;
            payload.renault_vin = rVin;
        }
        if (el.renaultPass) {
            var rPass = el.renaultPass.value;
            if (rPass && !isMaskedApiKey(rPass)) {
                payload.renault_pass = rPass;
            }
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

        return requestJSON(SETTINGS_URL, {
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

    function runRenaultTest() {
        if (settingsBusy) {
            return;
        }
        var rUser = el.renaultUser ? el.renaultUser.value.trim() : "";
        var rVin = el.renaultVin ? el.renaultVin.value.trim().toUpperCase() : "";
        var rPass = el.renaultPass ? el.renaultPass.value : "";
        if (el.renaultUser) {
            el.renaultUser.value = rUser;
        }
        if (el.renaultVin) {
            el.renaultVin.value = rVin;
        }

        if (rUser && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(rUser)) {
            showRenaultTestFeedback("error", t("renaultEmailInvalid"));
            showToast("error", t("renaultEmailInvalid"));
            if (el.renaultUser) {
                el.renaultUser.focus();
            }
            return;
        }
        if (rVin && !/^[A-Z0-9]{6,24}$/.test(rVin)) {
            showRenaultTestFeedback("error", t("renaultVinInvalid"));
            showToast("error", t("renaultVinInvalid"));
            if (el.renaultVin) {
                el.renaultVin.focus();
            }
            return;
        }
        var hasStoredPass = Boolean(liveConfig && (liveConfig.renault_pass_set || liveConfig.renault_password_set));
        var hasNewPass = Boolean(rPass && !isMaskedApiKey(rPass));
        if (!rUser || !rVin || (!hasNewPass && !hasStoredPass)) {
            showRenaultTestFeedback("error", t("renaultCredentialsRequired"));
            showToast("error", t("renaultCredentialsRequired"));
            return;
        }

        var testPayload = {
            renault_user: rUser,
            renault_vin: rVin
        };
        if (hasNewPass) {
            testPayload.renault_pass = rPass;
        }

        setSettingsBusy(true);
        showRenaultTestFeedback("success", t("testingRenault"));
        showSettingsStatus("pending", t("testingRenault"));

        requestJSON(TEST_RENAULT_URL, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(testPayload)
        }).then(function (res) {
            var msgText = (res && res.message) || t("renaultTestSuccess");
            if (liveConfig) {
                liveConfig.renault_user = rUser;
                liveConfig.renault_vin = rVin;
                liveConfig.renault_pass_set = true;
                liveConfig.renault_password_set = true;
            }
            if (el.renaultPass) {
                el.renaultPass.value = "";
                el.renaultPass.placeholder = "••••••••";
            }
            if (el.renaultPassHint) {
                el.renaultPassHint.hidden = false;
            }
            showRenaultTestFeedback("success", msgText);
            showSettingsStatus("success", msgText);
            showToast("success", msgText);
            fetchAndRenderPlugins();
        }).catch(function (error) {
            var errText = (error && error.message) || t("renaultTestFailed");
            showRenaultTestFeedback("error", errText);
            showSettingsStatus("error", errText);
            showToast("error", errText);
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
        showQuickSetupFeedback("", "");
        showRenaultTestFeedback("", "");
        if (el.callmebotPaste) {
            el.callmebotPaste.value = "";
        }
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

    el.settingsOpen.addEventListener("click", openSettings);
    if (el.pluginsGrid) {
        el.pluginsGrid.addEventListener("click", function (event) {
            var target = event.target;
            if (!target || typeof target.closest !== "function") {
                return;
            }
            /* HVAC action handling is temporarily disabled together with the
               action button in the plugin card. Keep for future re-enablement:
            var btn = target.closest("[data-plugin-action='start-hvac'], [data-plugin-action='stop-hvac']");
            if (btn) {
                event.preventDefault();
                triggerZoeHvac(btn, btn.getAttribute("data-plugin-action") === "stop-hvac" ? "stop" : "start");
            }
            */
        });
    }
    el.settingsClose.addEventListener("click", closeSettings);
    el.settingsBackdrop.addEventListener("click", closeSettings);
    el.settingsTest.addEventListener("click", runTest);
    if (el.renaultTest) {
        el.renaultTest.addEventListener("click", runRenaultTest);
    }
    if (el.renaultVin) {
        el.renaultVin.addEventListener("input", function () {
            var pos = el.renaultVin.selectionStart;
            var upper = el.renaultVin.value.toUpperCase();
            if (el.renaultVin.value !== upper) {
                el.renaultVin.value = upper;
                if (typeof pos === "number") {
                    try {
                        el.renaultVin.setSelectionRange(pos, pos);
                    } catch (err) {
                        /* Ignore selection errors on unsupported input types */
                    }
                }
            }
        });
    }

    if (el.signalEnabled) {
        el.signalEnabled.addEventListener("change", function () {
            setSignalLocked(!el.signalEnabled.checked);
        });
        setSignalLocked(!el.signalEnabled.checked);
    }

    if (el.animationsToggle) {
        el.animationsToggle.checked = animationsEnabled;
        el.animationsToggle.addEventListener("change", function () {
            var enabled = el.animationsToggle.checked;
            try {
                window.localStorage.setItem(ANIMATIONS_KEY, enabled ? "on" : "off");
            } catch (error) {}
            if (enabled) {
                document.body.classList.remove("disable-animations");
            } else {
                document.body.classList.add("disable-animations");
            }
        });
    }

    if (el.callmebotExtract && el.callmebotPaste) {
        el.callmebotExtract.addEventListener("click", function () {
            applyCallMeBotMessage(el.callmebotPaste.value, false);
        });
        el.callmebotPaste.addEventListener("input", function () {
            var val = el.callmebotPaste.value;
            if (!val) {
                return;
            }
            var parsed = parseCallMeBotMessage(val);
            if (parsed.phone && parsed.apikey) {
                applyCallMeBotMessage(val, true);
            }
        });
        el.callmebotPaste.addEventListener("paste", function (event) {
            var pasted = "";
            if (event.clipboardData && typeof event.clipboardData.getData === "function") {
                pasted = event.clipboardData.getData("text");
            }
            if (pasted) {
                var parsed = parseCallMeBotMessage(pasted);
                if (parsed.phone || parsed.apikey) {
                    event.preventDefault();
                    applyCallMeBotMessage(pasted, true);
                    return;
                }
            }
            window.setTimeout(function () {
                if (el.callmebotPaste.value) {
                    applyCallMeBotMessage(el.callmebotPaste.value, true);
                }
            }, 0);
        });
    }

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

    applyTheme(readStoredTheme());

    setLanguage(readStoredLanguage(), { persist: false });

    if (demoRequested) {
        renderDemo();
    } else {
        load();
    }
})();
