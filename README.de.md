# homematic-sma-battery-monitor

Batterieüberwachung für eine **Homematic CCU3 / RaspberryMatic** und einen **SMA Sunny Tripower / Sunny Boy Storage** mit BYD- oder vergleichbarem Speicher. Das Add-on liest den Ladezustand (State of Charge, SoC) über die lokale WebConnect-JSON-API des Wechselrichters, schreibt ihn in eine CCU3-Systemvariable und steuert daraus ein 95-%-Signal mit Hysterese für Programme und Automationen.

Das Projekt bleibt bewusst klein: POSIX `/bin/sh`, BusyBox `ash`, `curl` und `sed`. Kein Python, kein Node.js und kein Build-Schritt auf der CCU.

- **Zuverlässig** - jede SMA-Sitzung wird abgemeldet, auch bei `SIGTERM`; ein Sperrverzeichnis verhindert überlappende Cron-Läufe.
- **Sicher** - ein fehlender, ungültiger oder außerhalb des Bereichs liegender SoC überschreibt niemals den letzten guten CCU3-Wert.
- **Konfigurierbar** - alle Einstellungen liegen in einer typisierten `.env`-Datei und werden beim Start validiert.
- **Beobachtbar** - strukturierter `status.json`-Snapshot und ein eigenständiges Dark-Dashboard.
- **Dokumentiert** - deutsche und englische Anleitung, Protokollnotizen und Fehlerbehebung.

## Funktionen

| | |
|---|---|
| Wechselrichter lesen | SMA WebConnect JSON-API über HTTPS, konfigurierbarer Kanal-Schlüssel |
| CCU3 schreiben | ReGaHSS `tclrega.exe`, keine zusätzlichen CCU-Pakete nötig |
| Hysterese | Einschalten bei `THRESHOLD_TRIGGER`, Zurücksetzen bei `THRESHOLD_RESET`, Zustand persistent |
| Fehlerverhalten | `null`, nicht numerisch, außerhalb 0..100 oder nicht erreichbar: letzter CCU3-Zustand bleibt stehen |
| Sitzungssicherheit | `EXIT`-, `INT`-, `TERM`- und `HUP`-Traps rufen immer `logout.json` auf |
| Nebenläufigkeit | PID-Sperrverzeichnis mit Bereinigung verwaister Sperren |
| Benachrichtigungen | optionaler, abgekoppelter CallMeBot-Signal-Hook ohne Cron-Blockade |
| Dashboard | reines HTML/CSS/JS mit Rundanzeige, Hysterese-Leiste und Verlaufsdiagramm |
| Installation | `deploy.sh` über SSH, idempotenter Cron und Systemvariablen |

## Architektur

```mermaid
flowchart LR
    A[SMA Sunny Tripower<br/>WebConnect API] -->|HTTPS JSON<br/>login / getValues / logout| B[bin/check_soc.sh]
    B -->|ReGa tclrega.exe| C[CCU3-Systemvariablen<br/>SV_Batterie_SoC<br/>SV_Batterie_95_Signal]
    C --> D[CCU3-Programme<br/>und Automationen]
    B -->|abgekoppelter Hook| E[lib/notify_signal.sh]
    E -->|HTTPS| F[CallMeBot Signal]
    B -->|atomare status.json| G[lighttpd-Dashboard<br/>/addons/sma_battery/]
```

```
+------------------+    HTTPS / JSON     +---------------------+    ReGa HTTP     +-----------------------+
| SMA-Wechsel-     | ------------------> | bin/check_soc.sh    | ---------------> | SV_Batterie_SoC       |
| richter          | <------------------ | Sperre, Traps, Retry| <--------------- | SV_Batterie_95_Signal |
+------------------+                     +---------------------+                  +-----------------------+
                                                   |         |
                              abgekoppelte Meldung |         | atomares Schreiben
                                                   v         v
                                        lib/notify_signal.sh  web/status.json (lighttpd)
                                                   |
                                                   v
                                        CallMeBot-Signal-API
```

### Ablauf eines Laufs

1. `check_soc.sh` lädt `.env` und validiert alle Werte.
2. Unter `state/` wird ein Sperrverzeichnis angelegt. Ein laufender PID lässt den neuen Lauf still beenden; eine verwaiste Sperre wird entfernt.
3. Anmeldung (`POST /dyn/login.json`), Lesen von SoC und optionalen Leistungswerten, Abmeldung (`POST /dyn/logout.json`). Der Zyklus wird bis zu `SMA_RETRIES`-mal wiederholt.
4. Der SoC muss eine ganze Zahl von 0 bis 100 sein. Ungültige Werte beenden den Lauf, ohne die CCU3-Variablen zu verändern.
5. Die Hysterese wird mit dem gespeicherten Zustand ausgewertet. Zwischen den Schwellen bleibt der vorherige Zustand erhalten.
6. Die CCU3-Variablen werden mit einer einzigen `tclrega.exe`-Anfrage geschrieben.
7. `state/status.json` wird atomar aktualisiert und bei aktiviertem Dashboard als `status.json` in das lighttpd-Verzeichnis kopiert.
8. Bei einem Zustandswechsel wird `lib/notify_signal.sh` im Hintergrund gestartet. Cron wartet nie auf die Telefonnachricht.

## Verzeichnisstruktur

```
.
├── .env.example              Konfigurationsvorlage (nach .env kopieren)
├── .gitignore                Geheimnisse, Logs, Zustand und Build-Ausgaben
├── LICENSE                   MIT
├── CHANGELOG.md              Versionshistorie
├── SECURITY.md               Sicherheitsmeldungen und Umgang mit Geheimnissen
├── README.md                 englische Anleitung
├── README.de.md              diese Anleitung
├── CONTRIBUTING.md           Regeln für Entwicklung und Pull Requests
├── deploy.sh                 Installationshelfer auf dem Arbeitsplatz (bash)
├── setup_variables.sh        idempotente ReGa-Systemvariablen
├── bin/
│   └── check_soc.sh          Kern (POSIX sh, BusyBox ash)
├── lib/
│   └── notify_signal.sh      abgekoppelter Signal-Hook
├── web/
│   ├── index.html            Dashboard-Markup
│   ├── style.css             dunkles, responsives Design ohne externe Assets
│   ├── app.js                Rundanzeige, Hysterese-Leiste, Verlaufsdiagramm
│   └── status.example.json   Beispiel-Payload / lokale Vorschau
├── docs/
│   ├── PROTOCOL.md           Notizen zur SMA-WebConnect-API
│   └── TROUBLESHOOTING.md    ausführliche Fehlerbehebung
├── examples/
│   ├── crontab.txt           Cron-Eintrag
│   └── setup_variables.rega  ReGa-Rohskript für den Skripteditor
├── tests/
│   └── integration.py        Integrationstests mit Mock-Servern
└── tools/
    └── speedwire_sniffer.py  optionales Diagnosewerkzeug für Speedwire
```

## Voraussetzungen

**CCU3 / RaspberryMatic**

- Aktuelle CCU3- oder RaspberryMatic-Firmware mit ReGaHSS auf Port `8181`.
- `curl`, `sed`, `tr`, `cut`, `head`, `tail`, `date`, `logger` (im Standardimage enthalten).
- Root-SSH-Zugang für die Installation.
- Etwa 100 KB dauerhafter Speicher unter `/usr/local/`.

**SMA-Wechselrichter**

- Vom CCU3 aus über HTTPS erreichbar.
- WebUI-Benutzer mit Lesezugriff. Standard ist die Installateur-Rolle `usr`; `user` wird ebenfalls akzeptiert.
- Batterie in der WebUI sichtbar. Der Standardschlüssel für den SoC ist `6100_00295A00`; je nach Firmware können andere Schlüssel nötig sein (siehe [docs/PROTOCOL.md](docs/PROTOCOL.md)).

**Arbeitsplatz (nur für `deploy.sh` und Tests)**

- `bash`, `ssh`, `tar`, `curl`.
- Python 3.8+ und `openssl` für die Integrationstests.

## Installation

### 1. Quellcode holen

```sh
git clone https://github.com/<dein-konto>/homematic-sma-battery-monitor.git
cd homematic-sma-battery-monitor
```

### 2. Konfigurieren

```sh
cp .env.example .env
chmod 600 .env
${EDITOR:-vi} .env
```

Mindestens setzen:

| Schlüssel | Bedeutung |
|---|---|
| `SMA_IP` | Adresse des Wechselrichters, z. B. `192.168.178.123` |
| `SMA_PASS` | Passwort der Wechselrichter-WebUI |
| `CCU_IP` | `127.0.0.1`, wenn das Add-on auf der CCU3 läuft |
| `DEPLOY_HOST` | CCU3-Adresse für `deploy.sh` |

`.env` muss Modus `600` behalten. Die Datei ist bereits in `.gitignore` eingetragen.

### 3. Mit `deploy.sh` installieren (empfohlen)

```sh
./deploy.sh -H 192.168.178.10
```

`deploy.sh` führt diese Schritte idempotent aus:

1. SSH-Verbindung prüfen.
2. `bin/`, `lib/`, `web/`, `setup_variables.sh` und `.env` per `tar` über SSH nach `/usr/local/addons/sma_battery` übertragen (kein `rsync` auf der CCU nötig).
3. Rechte setzen: `700` für Skripte und Verzeichnisse, `600` für `.env`, `644` für Dashboard-Dateien.
4. Dashboard nach `/usr/local/etc/config/addons/www/sma_battery/` kopieren.
5. `*/2 * * * * /usr/local/addons/sma_battery/bin/check_soc.sh` in die Root-Crontab eintragen und `S71crond` neu starten.
6. `setup_variables.sh` ausführen und die beiden Systemvariablen anlegen.
7. Einen ersten Lauf ausführen und die Dashboard-URL ausgeben.

Nützliche Optionen:

```sh
./deploy.sh --dry-run                 # nur planen, nichts ändern
./deploy.sh -H ccu3.local --skip-vars # vorhandene Variablen behalten
./deploy.sh -H ccu3.local --skip-cron # eigene Zeitsteuerung
```

### 4. Manuelle Installation

Auf der CCU3:

```sh
mkdir -p /usr/local/addons/sma_battery/state
mkdir -p /usr/local/etc/config/addons/www/sma_battery
```

Vom Arbeitsplatz:

```sh
tar -cf - -C . bin lib web setup_variables.sh .env \
  | ssh root@ccu3 "tar -xf - -C /usr/local/addons/sma_battery"
```

Auf der CCU3:

```sh
cd /usr/local/addons/sma_battery
chmod 700 bin/check_soc.sh lib/notify_signal.sh setup_variables.sh
chmod 600 .env
cp web/index.html web/style.css web/app.js \
   /usr/local/etc/config/addons/www/sma_battery/
```

### 5. Systemvariablen anlegen

```sh
cd /usr/local/addons/sma_battery
sh setup_variables.sh
```

Das Skript ist idempotent und legt an:

- `SV_Batterie_SoC` - Zahl, `0..100`, Einheit `%`
- `SV_Batterie_95_Signal` - Boolean

Beide werden unter `ID_SYSTEM_VARIABLES` registriert und erscheinen unter **Status und Bedienung → Systemvariablen**. Eine Rohfassung für den CCU3-Skripteditor liegt in [`examples/setup_variables.rega`](examples/setup_variables.rega).

### 6. Cron einrichten

Auf der CCU3:

```sh
crontab -l 2>/dev/null | grep -v '/usr/local/addons/sma_battery/bin/check_soc.sh' > /tmp/sma.cron
echo '*/2 * * * * /usr/local/addons/sma_battery/bin/check_soc.sh >/dev/null 2>&1' >> /tmp/sma.cron
crontab /tmp/sma.cron
/etc/init.d/S71crond restart
```

Prüfen:

```sh
crontab -l
ps | grep crond
```

Eine vollständige Vorlage liegt in [`examples/crontab.txt`](examples/crontab.txt). Ein `rc.local`-Eintrag ist nicht nötig: Die CCU3 startet `crond` beim Booten, und die Root-Crontab liegt persistent unter `/etc/crontabs/root`. Wird sie durch ein Firmware-Update zurückgesetzt, `./deploy.sh` erneut ausführen.

### 7. Dashboard öffnen

```
http://<ccu-adresse>/addons/sma_battery/
```

Der erste Wert erscheint nach dem nächsten Cron-Lauf (maximal zwei Minuten). Die Seite aktualisiert sich alle 30 Sekunden und zeigt den Zustand „Warte auf erste Messung“, solange `status.json` fehlt. Mit `?demo=1` lässt sich das Layout mit Beispieldaten ansehen.

## Konfiguration

Alle Schlüssel stehen in `.env`. Die Datei wird von `/bin/sh` eingelesen; Werte quoten und für Geheimnisse einfache Anführungszeichen verwenden.

### Wechselrichter

| Schlüssel | Typ | Standard | Beschreibung |
|---|---|---|---|
| `SMA_IP` | Host | - | IPv4-Adresse oder DNS-Name, ohne Schema |
| `SMA_PORT` | int | `443` | HTTPS-Port der WebUI |
| `SMA_ROLE` | `usr`/`user` | `usr` | WebUI-Rolle |
| `SMA_PASS` | Geheimnis | - | WebUI-Passwort |
| `BATTERY_SOC_KEY` | String | `6100_00295A00` | SoC-Kanalschlüssel |
| `BATTERY_CHARGE_POWER_KEY` | String | `6100_00417800` | optionaler Ladeleistungsschlüssel; leer deaktiviert |
| `BATTERY_DISCHARGE_POWER_KEY` | String | `6100_00417900` | optionaler Entladeleistungsschlüssel; leer deaktiviert |
| `SMA_TLS_VERIFY` | bool | `false` | Zertifikat prüfen (`false` = `curl -k`) |
| `SMA_CONNECT_TIMEOUT` | int | `5` | Verbindungs-Timeout in Sekunden |
| `SMA_MAX_TIME` | int | `15` | Gesamt-Timeout pro Anfrage |
| `SMA_RETRIES` | int | `2` | vollständige Login-/Lese-/Logout-Zyklen |
| `SMA_RETRY_DELAY` | int | `5` | Pause zwischen Versuchen |

### CCU3

| Schlüssel | Typ | Standard | Beschreibung |
|---|---|---|---|
| `CCU_IP` | Host | `127.0.0.1` | CCU-Adresse; lokal, wenn auf der CCU ausgeführt |
| `CCU_REGA_PORT` | int | `8181` | ReGaHSS-HTTP-Port |
| `SV_SOC_NAME` | Identifier | `SV_Batterie_SoC` | numerische Systemvariable |
| `SV_SIGNAL_NAME` | Identifier | `SV_Batterie_95_Signal` | boolesche Systemvariable |
| `THRESHOLD_TRIGGER` | int 0..100 | `95` | Signal ab diesem SoC auf `true` |
| `THRESHOLD_RESET` | int 0..100 | `90` | Signal ab diesem SoC auf `false` |

`THRESHOLD_TRIGGER` muss größer als `THRESHOLD_RESET` sein. Variablennamen dürfen nur Buchstaben, Ziffern und Unterstriche enthalten.

### Benachrichtigungen

| Schlüssel | Typ | Standard | Beschreibung |
|---|---|---|---|
| `SIGNAL_ENABLED` | bool | `false` | CallMeBot-Signal aktivieren |
| `SIGNAL_PHONE` | String | - | E.164-Nummer mit `+` |
| `SIGNAL_APIKEY` | Geheimnis | - | CallMeBot-API-Schlüssel |
| `SIGNAL_API_URL` | URL | CallMeBot-Signal | kompatibles Gateway |
| `SIGNAL_MESSAGE_OVERFLOW` | String | siehe `.env.example` | Überschuss-Meldung; Platzhalter `{SOC}`, `{TRIGGER_SOC}`, `{RESET_SOC}`, `{HOST}` |
| `SIGNAL_MESSAGE_RESET` | String | siehe `.env.example` | Reset-Meldung; gleiche Platzhalter |
| `SIGNAL_MESSAGE_TEST` | String | siehe `.env.example` | Text für `notify_signal.sh --test` |
| `SIGNAL_NOTIFY_ON_START` | bool | `false` | beim ersten Lauf melden, wenn das Signal bereits aktiv ist |
| `SIGNAL_TIMEOUT` | int | `15` | Timeout der Meldung |
| `SIGNAL_RETRIES` | int | `2` | Versuche der Meldung |
| `SIGNAL_COOLDOWN` | int | `3600` | gleiche Trigger-/Reset-Meldung innerhalb dieser Sekunden unterdrücken |

Meldungen werden nur bei einem Hysterese-Wechsel (`false → true` und `true → false`) und erst nach erfolgreichem CCU3-Schreiben gesendet. Der Hook läuft mit `setsid`/`nohup` abgekoppelt und verzögert den Cron-Lauf nicht. Eine Sperrzeit pro Ereignis verhindert Doppelmeldungen bei einem Zwei-Minuten-Cron oder einem verlorenen Zustand. Der Text wird von `curl --data-urlencode` kodiert, damit Leerzeichen, Umlaute und `ß` ohne python/jq auf BusyBox funktionieren. Die alten Variablen `SIGNAL_TEXT_TRIGGER`/`SIGNAL_TEXT_RESET` und `%SOC%`-Platzhalter bleiben gültig.

### Dashboard und Logging

| Schlüssel | Typ | Standard | Beschreibung |
|---|---|---|---|
| `WEB_ENABLED` | bool | `true` | `status.json` veröffentlichen |
| `WEB_STATUS_FILE` | Pfad | `/usr/local/etc/config/addons/www/sma_battery/status.json` | lighttpd-Ziel |
| `WEB_HISTORY_POINTS` | int | `288` | Punkte im Diagramm (2 min × 288 ≈ 9,6 h) |
| `LOG_FILE` | Pfad | `<addon>/state/check_soc.log` | lokales Log; leer deaktiviert |
| `LOG_MAX_BYTES` | int | `262144` | Rotation oberhalb dieser Größe |

### Installation

| Schlüssel | Typ | Standard | Beschreibung |
|---|---|---|---|
| `DEPLOY_HOST` | Host | - | CCU3-SSH-Adresse für `deploy.sh` |
| `DEPLOY_USER` | String | `root` | SSH-Benutzer |
| `DEPLOY_PORT` | int | `22` | SSH-Port |
| `DEPLOY_ADDON_DIR` | Pfad | `/usr/local/addons/sma_battery` | entferntes Add-on-Verzeichnis |
| `DEPLOY_WEB_DIR` | Pfad | `/usr/local/etc/config/addons/www/sma_battery` | entferntes Dashboard-Verzeichnis |

## So funktioniert das Signal

Der Boolean ist ein klassischer Schmitt-Trigger:

```
SoC
100 ┤
 95 ┤──────────── Trigger ────────────  → true
 92 ┤            halten                  (vorheriger Zustand bleibt)
 90 ┤──────────── Reset ───────────────  → false
  0 ┤
```

- Ab `THRESHOLD_TRIGGER` wird das Signal `true`.
- Ab `THRESHOLD_RESET` wird es `false`.
- Dazwischen wird der Wert aus `state/signal` verwendet.
- Fehlt die Zustandsdatei und liegt der SoC im Totband (zum Beispiel 92 %), wird der Boolean absichtlich **nicht geschrieben**. Das verhindert einen vorzeitigen Reset nach einer Neuinstallation oder einem verlorenen Zustand. Das Dashboard zeigt dann `signal: null` und `signal_state: "unknown"`.

Fehlerverhalten:

| Zustand | CCU3-SoC | CCU3-Signal | Exit |
|---|---|---|---|
| gültig 0..100 | geschrieben | geschrieben oder gehalten | 0 |
| `null` / fehlend | unverändert | unverändert | 1 |
| nicht ganzzahlig / `101` / `-1` | unverändert | unverändert | 1 |
| Login- oder `getValues`-Fehler | unverändert | unverändert | 1 |
| ReGa-Anfrage fehlgeschlagen | unverändert | unverändert | 1 |
| anderer Lauf aktiv | unverändert | unverändert | 0 |

## Dashboard

Das Dashboard ist eine statische Single-Page-App. Es liest `status.json` alle 30 Sekunden per `fetch()` und zeigt:

- eine runde SoC-Anzeige,
- eine Hysterese-Leiste mit aktuellem SoC und beiden Schwellen,
- Ladezustand und optionale Batterieleistung,
- Signalzustand und Ergebnis des CCU3-Schreibens,
- ein Verlaufsdiagramm aus den gespeicherten Messpunkten,
- Verbindungsdaten sowie einen klaren Demo-/Live-/Stale-Hinweis.

`status.json` wird atomar geschrieben und enthält keine Geheimnisse. Das Schema ist in [README.md](README.md#dashboard) und in `web/status.example.json` dokumentiert.

Für eine lokale Vorschau ohne CCU3 `web/status.example.json` nach `web/status.json` kopieren und `web/` mit einem beliebigen statischen Server ausliefern, oder die Seite mit `?demo=1` öffnen. Das **Demo**-Abzeichen macht deutlich, dass die Werte nicht live sind.

Wird kein Dashboard benötigt: `WEB_ENABLED="false"`. Der Monitor schreibt weiterhin einen privaten Snapshot nach `<addon>/state/status.json`.

## SMA-WebConnect-Notizen

Die vollständigen Notizen inklusive Schlüsselsuche und Sitzungslimits stehen in [docs/PROTOCOL.md](docs/PROTOCOL.md). Kurzfassung:

```sh
# 1. Login -> sid
curl -k -sS -X POST -H 'Content-Type: application/json' \
  --data '{"right":"usr","pass":"secret"}' \
  https://192.168.178.123/dyn/login.json

# 2. Batterie-SoC lesen
curl -k -sS -X POST -H 'Content-Type: application/json' \
  --data '{"destDev":[],"keys":["6100_00295A00"],"sid":"<sid>"}' \
  'https://192.168.178.123/dyn/getValues.json?sid=<sid>'

# 3. immer abmelden
curl -k -sS -X POST -H 'Content-Type: application/json' \
  --data '{"destDev":[],"sid":"<sid>"}' \
  'https://192.168.178.123/dyn/logout.json?sid=<sid>'
```

`6100_00295A00` ist der Standardschlüssel für den Batterie-SoC bei Sunny Tripower / Sunny Boy Storage. Je nach Firmware kann er abweichen; siehe Protokollnotizen. Die optionalen Leistungsschlüssel `6100_00417800` und `6100_00417900` verbessern die Anzeige von Laden/Entladen, sind aber nicht erforderlich - das Dashboard fällt auf den SoC-Verlauf zurück.

## Signal-Benachrichtigungen

1. CallMeBot-Signal-Bot zum Telefon hinzufügen und den API-Schlüssel nach Anleitung aktivieren.
2. Nummer und Schlüssel in `.env` eintragen:

```sh
SIGNAL_ENABLED="true"
SIGNAL_PHONE="+491701234567"
SIGNAL_APIKEY="123456"
```

3. `./deploy.sh` erneut ausführen oder die aktualisierte `.env` kopieren und `chmod 600` setzen. Ein erneutes Deployment behält die vorhandene `.env` auf der CCU, überschreibt also keine Zugangsdaten.
4. Zugangsdaten und Zustellung testen, ohne einen falschen Batteriezustand zu erzeugen:

```sh
/usr/local/addons/sma_battery/lib/notify_signal.sh --test; echo "exit=$?"
```

`--test` sendet eine klar gekennzeichnete Testnachricht und liest oder schreibt `state/signal` nicht. `deploy.sh` startet denselben Test automatisch, wenn `SIGNAL_ENABLED="true"` gesetzt ist und `--skip-test` fehlt.

Der Notifier schreibt in dasselbe Log und in syslog mit Tag `sma_battery_notify`.

## Fehlerbehebung

Die ausführliche Anleitung steht in [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md). Schnellprüfungen:

```sh
# einmal im Vordergrund ausführen
/usr/local/addons/sma_battery/bin/check_soc.sh; echo "exit=$?"

# letzte Logzeilen
tail -n 50 /usr/local/addons/sma_battery/state/check_soc.log
logread | grep sma_battery

# läuft Cron?
ps | grep crond
crontab -l

# ist ReGaHSS erreichbar?
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8181/tclrega.exe
```

Häufige Ursachen:

| Symptom | Wahrscheinliche Ursache |
|---|---|
| `login failed` / `err 401` | falsches Passwort, falsche Rolle oder WebUI-Benutzer gesperrt |
| wiederholter `getValues`-Fehler | alte Sitzung noch offen; einige Minuten warten und doppelte Cron-Jobs prüfen |
| `no numeric value` | Batterie nicht verbunden, falscher `BATTERY_SOC_KEY` oder Firmware bietet den Wert nicht an |
| `CCU3 ReGa request failed` | ReGaHSS läuft nicht, falscher `CCU_REGA_PORT` oder CCU3 überlastet |
| Variablen fehlen | `setup_variables.sh` ausführen; Namen sind groß-/kleinschreibungssensitiv |
| Dashboard zeigt „No data“ | erster Cron-Lauf noch nicht fertig oder `WEB_STATUS_FILE` liegt außerhalb des lighttpd-Verzeichnisses |
| Dashboard zeigt „Stale“ | letzter Lauf älter als 10 Minuten; Cron und Log prüfen |

### BusyBox- und CCU3-Besonderheiten

- `#!/bin/sh` verwenden, nicht bash. Keine `local`, Arrays, `[[ ]]`, Prozess-Substitution, `set -u`-Annahmen oder `date -d`.
- Das `curl` der CCU3 kann älter sein; die Skripte nutzen nur `-sS`, `-k`, `-m`, `--connect-timeout`, `--data-binary` und `--data-urlencode`.
- `/` ist auf der CCU3 schreibgeschützt. Dauerhafte Daten gehören unter `/usr/local/`.
- Cron startet mit minimaler Umgebung. `check_soc.sh` setzt seinen `PATH` selbst.
- Manuell geöffnete SMA-WebUI-Sitzungen abmelden. Der Wechselrichter erlaubt nur wenige parallele Sitzungen und kann neue Logins vorübergehend ablehnen.
- Eine verlorene `state/signal`-Datei führt im Totband nicht zu einem Reset; der Monitor hält den Boolean und protokolliert `signal=unchanged`.

## Sicherheit

- `.env` enthält das Wechselrichter-Passwort und den CallMeBot-API-Schlüssel. Die Datei ist in Git ignoriert, wird mit Modus `600` installiert und bei falschem Modus verwarnt.
- Das SMA-Zertifikat ist standardmäßig selbstsigniert; `SMA_TLS_VERIFY="false"` ist daher die funktionierende Voreinstellung. `"true"` erst nach Installation eines vertrauenswürdigen Zertifikats am Wechselrichter.
- ReGaHSS wird lokal über HTTP angesprochen. Port `8181` nicht ins Internet freigeben.
- `status.json` enthält nur SoC, Schwellen, Variablennamen und Hostadresse. Kein Passwort, keine SID und kein API-Schlüssel.
- Sperr- und Zustandsdateien haben Modus `700`/`600`.
- Das Skript meldet sich pro Lauf an, liest und meldet sich wieder ab. Zwischen Cron-Läufen bleibt keine Sitzung offen.

## Entwicklung und Tests

```sh
# POSIX-Syntax
sh -n bin/check_soc.sh
sh -n lib/notify_signal.sh
sh -n setup_variables.sh

# ShellCheck (POSIX-Profil)
shellcheck -s sh -S style bin/check_soc.sh lib/notify_signal.sh setup_variables.sh

# Bash-Installationshelfer
shellcheck -s bash -S style deploy.sh

# JavaScript-Syntax
node --check web/app.js

# Ende-zu-Ende-Tests mit Mock-SMA und Mock-ReGa
python3 tests/integration.py
```

Der Integrationstest startet einen selbstsignierten HTTPS-SMA-Mock und einen HTTP-ReGa-Mock und prüft erfolgreiche Läufe, Hysterese-Wechsel, das Halten bei unbekanntem Zustand, `null`/außerhalb des Bereichs, Login-Fehler, die Sperre, Logout bei `SIGTERM`, abgekoppelte Benachrichtigungen und das ReGa-Setup.

Siehe [CONTRIBUTING.md](CONTRIBUTING.md) für die Coderegeln.

## Migration vom alten `sma_soc`-Add-on

Das neue Layout ersetzt `config.env` durch `.env`. Existiert keine `.env`, liest der Kern übergangsweise weiterhin `config.env`.

| Alter Schlüssel | Neuer Schlüssel |
|---|---|
| `SMA_HOST` | `SMA_IP` |
| `SMA_SOC_KEY` | `BATTERY_SOC_KEY` |
| `SMA_INSECURE="1"` | `SMA_TLS_VERIFY="false"` |
| `SMA_INSECURE="0"` | `SMA_TLS_VERIFY="true"` |
| `SV_SIGNAL_ON` | `THRESHOLD_TRIGGER` |
| `SV_SIGNAL_OFF` | `THRESHOLD_RESET` |
| `CCU_REGA_URL` | `CCU_IP` + `CCU_REGA_PORT` |
| `SV_SOC_NAME` | unverändert |
| `SV_SIGNAL_NAME` | unverändert |

Das Standardverzeichnis ist jetzt `/usr/local/addons/sma_battery`; mit `deploy.sh --dir` lässt sich der alte Pfad beibehalten.

## Optionales Diagnosewerkzeug

`tools/speedwire_sniffer.py` hört auf SMA-Speedwire-Multicast-Telegramme auf `239.12.255.254:9524`. Es wird für den Monitor nicht benötigt, hilft aber zu prüfen, ob der Wechselrichter im Netz ist und ob herstellerspezifische Batteriekanäle vorhanden sind. Es benötigt Python auf dem Arbeitsplatz, nicht auf der CCU3.

```sh
python3 tools/speedwire_sniffer.py --interface 192.168.178.10 --count 5
```

## Lizenz

MIT - siehe [LICENSE](LICENSE).
