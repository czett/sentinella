#!/usr/bin/env python3
"""Integration tests for Sentinella.

Runs the real POSIX scripts against a local mock of the SMA WebConnect API
(HTTPS, self-signed) and the CCU3 ReGa endpoint (HTTP). No CCU or inverter is
needed.

Requirements: Python 3.8+, curl, openssl. Run from the repository root:

    python3 tests/integration.py
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
SMA_SOC_KEY = "6100_00295A00"
SMA_CHARGE_KEY = "6100_00417800"
SMA_DISCHARGE_KEY = "6100_00417900"
PASSWORD = "test-secret"
DEFAULT_UUID = "98526357-0cec-4e7f-9d60-d854432414d3"
UPDATED_UUID = "123e4567-e89b-12d3-a456-426614174000"


class State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.soc = 96
        self.charge = 0
        self.discharge = 0
        self.fail_login = False
        self.fail_get = False
        self.get_delay = 0.0
        self.sid = ""
        self.logins = 0
        self.logouts = 0
        self.get_requests = 0
        self.rega_bodies: list[str] = []
        self.signal_requests: list[str] = []

    def reset_requests(self) -> None:
        with self.lock:
            self.logins = 0
            self.logouts = 0
            self.get_requests = 0
            self.rega_bodies = []
            self.signal_requests = []


class SmaHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args) -> None:
        pass

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        state: State = self.server.state  # type: ignore[attr-defined]
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode() or "{}")
        except json.JSONDecodeError:
            body = {}

        if parsed.path == "/dyn/login.json":
            with state.lock:
                state.logins += 1
                if state.fail_login or body.get("pass") != PASSWORD:
                    self._json(200, {"err": 401, "result": {}})
                    return
                state.sid = "test-sid-001"
                self._json(200, {"result": {"sid": state.sid}})
            return

        if parsed.path == "/dyn/getValues.json":
            with state.lock:
                state.get_requests += 1
                delay = state.get_delay
            if delay:
                time.sleep(delay)
            sid = parse_qs(parsed.query).get("sid", [""])[0]
            if state.fail_get or sid != state.sid:
                self._json(200, {"err": 503, "result": {}})
                return
            keys = body.get("keys", [])
            values = {}
            if SMA_SOC_KEY in keys:
                values[SMA_SOC_KEY] = {"1": [{"val": state.soc}]}
            if SMA_CHARGE_KEY in keys:
                values[SMA_CHARGE_KEY] = {"1": [{"val": state.charge}]}
            if SMA_DISCHARGE_KEY in keys:
                values[SMA_DISCHARGE_KEY] = {"1": [{"val": state.discharge}]}
            self._json(200, {"result": {"data": {"device": values}}})
            return

        if parsed.path == "/dyn/logout.json":
            with state.lock:
                state.logouts += 1
                state.sid = ""
            self._json(200, {"result": {}})
            return

        self._json(404, {"err": 404})


class RegaHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args) -> None:
        pass

    def do_POST(self) -> None:  # noqa: N802
        state: State = self.server.state  # type: ignore[attr-defined]
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b""
        with state.lock:
            state.rega_bodies.append(raw.decode("utf-8", "replace"))
        body = b"SMA_SETUP_OK\nSMA_SOC_OK\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class SignalHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802
        state: State = self.server.state  # type: ignore[attr-defined]
        with state.lock:
            state.signal_requests.append(self.path)
        body = b"OK\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_server(handler, state: State, tls: tuple[str, str] | None = None):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.state = state  # type: ignore[attr-defined]
    if tls:
        cert, key = tls
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=cert, keyfile=key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def make_cert(directory: Path) -> tuple[str, str]:
    cert = directory / "cert.pem"
    key = directory / "key.pem"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(key), "-out", str(cert), "-days", "1",
            "-subj", "/CN=127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )
    return str(cert), str(key)


def write_config(addon: Path, sma_port: int, rega_port: int,
                 signal_port: int | None, signal_enabled: bool) -> None:
    config = f"""\
SMA_IP="127.0.0.1"
SMA_PORT="{sma_port}"
SMA_ROLE="usr"
SMA_PASS='{PASSWORD}'
BATTERY_SOC_KEY="{SMA_SOC_KEY}"
BATTERY_CHARGE_POWER_KEY="{SMA_CHARGE_KEY}"
BATTERY_DISCHARGE_POWER_KEY="{SMA_DISCHARGE_KEY}"
SMA_TLS_VERIFY="false"
SMA_CONNECT_TIMEOUT="3"
SMA_MAX_TIME="5"
SMA_RETRIES="1"
SMA_RETRY_DELAY="0"
CCU_IP="127.0.0.1"
CCU_REGA_PORT="{rega_port}"
SV_SOC_NAME="SV_Batterie_SoC"
SV_SIGNAL_NAME="SV_Batterie_95_Signal"
THRESHOLD_TRIGGER="95"
THRESHOLD_RESET="90"
UI_LANGUAGE="en"
SIGNAL_ENABLED="{'true' if signal_enabled else 'false'}"
SIGNAL_PHONE="{DEFAULT_UUID}"
SIGNAL_APIKEY="test-key"
SIGNAL_API_URL="http://127.0.0.1:{signal_port or 1}/signal"
SIGNAL_TEXT_TRIGGER="Battery full: SoC %SOC% on %HOST%"
SIGNAL_TEXT_RESET="Battery OK: SoC %SOC% on %HOST%"
SIGNAL_NOTIFY_ON_START="false"
SIGNAL_TIMEOUT="5"
SIGNAL_RETRIES="1"
WEB_ENABLED="true"
WEB_STATUS_FILE="{addon}/www/status.json"
WEB_HISTORY_POINTS="288"
LOG_FILE="{addon}/state/test.log"
LOG_MAX_BYTES="262144"
"""
    path = addon / ".env"
    path.write_text(config)
    path.chmod(0o600)


def make_addon(tmp: Path, sma_port: int, rega_port: int,
               signal_port: int | None = None, signal_enabled: bool = False,
               name: str = "addon") -> Path:
    addon = tmp / name
    (addon / "bin").mkdir(parents=True)
    (addon / "lib").mkdir(parents=True)
    shutil.copy2(ROOT / "bin" / "check_soc.sh", addon / "bin" / "check_soc.sh")
    shutil.copy2(ROOT / "lib" / "notify_signal.sh", addon / "lib" / "notify_signal.sh")
    shutil.copy2(ROOT / "setup_variables.sh", addon / "setup_variables.sh")
    (addon / "bin" / "check_soc.sh").chmod(0o700)
    (addon / "lib" / "notify_signal.sh").chmod(0o700)
    (addon / "setup_variables.sh").chmod(0o700)
    write_config(addon, sma_port, rega_port, signal_port, signal_enabled)
    return addon


def run_check(addon: Path, timeout: float = 20) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["SMA_MONITOR_ENV"] = str(addon / ".env")
    return subprocess.run(
        [str(addon / "bin" / "check_soc.sh")],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def read_status(addon: Path) -> dict:
    return json.loads((addon / "www" / "status.json").read_text())


def wait_until(predicate, timeout: float = 8.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def run_notify(addon: Path, *args: str, timeout: float = 20) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["SMA_MONITOR_ENV"] = str(addon / ".env")
    return subprocess.run(
        [str(addon / "lib" / "notify_signal.sh"), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def run_cgi(addon: Path, method: str = "GET", action: str = "config",
            body: str = "", prefix: str = "sentinella",
            timeout: float = 20) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update(
        {
            "REQUEST_METHOD": method,
            "SCRIPT_NAME": f"/addons/{prefix}/api/{action}.cgi",
            "PATH_INFO": "",
            "CONTENT_LENGTH": str(len(body.encode())),
            "CONTENT_TYPE": "application/json",
            "SMA_MONITOR_ADDON_DIR": str(addon),
            "SMA_MONITOR_ENV": str(addon / ".env"),
        }
    )
    script_name = f"{action}.cgi"
    script_path = ROOT / "web" / "api" / script_name
    return subprocess.run(
        ["sh", str(script_path)],
        input=body,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def parse_cgi(result: subprocess.CompletedProcess) -> tuple[str, dict]:
    head, _, payload = result.stdout.partition("\n\n")
    first = head.splitlines()[0] if head else ""
    status = first.split(" ", 1)[1] if first.startswith("Status: ") else first
    return status, json.loads(payload)


def check(condition: bool, label: str, failures: list[str]) -> None:
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}")
        failures.append(label)


def reset_state(addon: Path) -> None:
    state = addon / "state"
    if state.exists():
        shutil.rmtree(state)


def main() -> int:
    failures: list[str] = []
    tmp = Path(tempfile.mkdtemp(prefix="sma-monitor-test-"))
    print(f"workdir: {tmp}")

    cert, key = make_cert(tmp)
    sma_state = State()
    sma = start_server(SmaHandler, sma_state, tls=(cert, key))
    rega_state = State()
    rega = start_server(RegaHandler, rega_state)
    signal_state = State()
    sig = start_server(SignalHandler, signal_state)
    sma_port = sma.server_address[1]
    rega_port = rega.server_address[1]
    signal_port = sig.server_address[1]

    try:
        print("\n1. trigger at 96 %")
        addon = make_addon(tmp, sma_port, rega_port)
        reset_state(addon)
        sma_state.soc = 96
        sma_state.reset_requests()
        rega_state.reset_requests()
        result = run_check(addon)
        body = "\n".join(rega_state.rega_bodies)
        check(result.returncode == 0, f"exit 0 (got {result.returncode})", failures)
        check("s.State(96)" in body, "SoC written to CCU", failures)
        check("b.State(true)" in body, "signal written true", failures)
        check(sma_state.logouts >= 1, "SMA session logged out", failures)
        check((addon / "state" / "signal").read_text().strip() == "1", "hysteresis state persisted", failures)
        status = read_status(addon)
        check(status["soc"] == 96, "status.json SoC", failures)
        check(status["signal"] is True, "status.json signal", failures)
        check(status["signal_state"] == "triggered", "status.json signal state", failures)
        check(status["ccu_status"] == "ok", "status.json CCU status", failures)
        check(
            status.get("signal_gateway") == "CallMeBot (aktiv)",
            "status.json reports an active CallMeBot gateway",
            failures,
        )

        print("\n2. hold at 92 %")
        sma_state.soc = 92
        sma_state.reset_requests()
        rega_state.reset_requests()
        result = run_check(addon)
        body = "\n".join(rega_state.rega_bodies)
        check(result.returncode == 0, "exit 0", failures)
        check("b.State(true)" in body, "held signal stays true", failures)
        status = read_status(addon)
        check(status["signal_state"] == "held", "status.json held", failures)

        print("\n3. reset at 89 %")
        sma_state.soc = 89
        sma_state.reset_requests()
        rega_state.reset_requests()
        result = run_check(addon)
        body = "\n".join(rega_state.rega_bodies)
        check(result.returncode == 0, "exit 0", failures)
        check("b.State(false)" in body, "signal written false", failures)
        check((addon / "state" / "signal").read_text().strip() == "0", "state persisted false", failures)

        print("\n4. unknown state inside the dead band")
        reset_state(addon)
        sma_state.soc = 92
        sma_state.reset_requests()
        rega_state.reset_requests()
        result = run_check(addon)
        body = "\n".join(rega_state.rega_bodies)
        check(result.returncode == 0, "exit 0", failures)
        check("s.State(92)" in body, "SoC still written", failures)
        check("b.State(" not in body, "boolean left untouched", failures)
        status = read_status(addon)
        check(status["signal"] is None, "status.json signal null", failures)

        print("\n5. null SoC keeps the last value")
        sma_state.soc = None
        sma_state.reset_requests()
        rega_state.reset_requests()
        result = run_check(addon)
        check(result.returncode == 1, f"exit 1 (got {result.returncode})", failures)
        check(not rega_state.rega_bodies, "no CCU write", failures)
        check(sma_state.logouts >= 1, "session still logged out", failures)

        print("\n6. out-of-range SoC is rejected")
        sma_state.soc = 101
        sma_state.reset_requests()
        rega_state.reset_requests()
        result = run_check(addon)
        check(result.returncode == 1, "exit 1", failures)
        check(not rega_state.rega_bodies, "no CCU write", failures)

        print("\n7. login failure is handled")
        sma_state.soc = 95
        sma_state.fail_login = True
        sma_state.reset_requests()
        rega_state.reset_requests()
        result = run_check(addon)
        check(result.returncode == 1, "exit 1", failures)
        check(not rega_state.rega_bodies, "no CCU write", failures)
        sma_state.fail_login = False

        print("\n8. live lock prevents overlap")
        reset_state(addon)
        lock = addon / "state" / ".lock"
        lock.mkdir(parents=True)
        sleeper = subprocess.Popen(["sleep", "30"])
        (lock / "pid").write_text(str(sleeper.pid))
        sma_state.reset_requests()
        rega_state.reset_requests()
        result = run_check(addon)
        check(result.returncode == 0, "exit 0", failures)
        check(sma_state.logins == 0, "no SMA login", failures)
        check(not rega_state.rega_bodies, "no CCU write", failures)
        sleeper.kill()
        sleeper.wait()
        reset_state(addon)

        print("\n9. SIGTERM logs out and removes the lock")
        sma_state.soc = 95
        sma_state.get_delay = 8.0
        sma_state.reset_requests()
        rega_state.reset_requests()
        env = os.environ.copy()
        env["SMA_MONITOR_ENV"] = str(addon / ".env")
        proc = subprocess.Popen(
            [str(addon / "bin" / "check_soc.sh")],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        deadline = time.time() + 5
        while time.time() < deadline and sma_state.get_requests == 0:
            time.sleep(0.05)
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            failures.append("SIGTERM test did not exit")
        check(proc.returncode == 143, f"exit 143 (got {proc.returncode})", failures)
        check(sma_state.logouts >= 1, "logout after SIGTERM", failures)
        check(not (addon / "state" / ".lock").exists(), "lock removed after SIGTERM", failures)
        sma_state.get_delay = 0.0

        print("\n10. Signal notification is detached and sent")
        addon2 = make_addon(tmp, sma_port, rega_port, signal_port, True, name="addon2")
        reset_state(addon2)
        (addon2 / "state").mkdir(parents=True)
        (addon2 / "state" / "signal").write_text("0\n")
        sma_state.soc = 96
        sma_state.reset_requests()
        rega_state.reset_requests()
        signal_state.reset_requests()
        result = run_check(addon2)
        check(result.returncode == 0, "main run exits 0", failures)
        deadline = time.time() + 8
        while time.time() < deadline and not signal_state.signal_requests:
            time.sleep(0.05)
        check(bool(signal_state.signal_requests), "notification request received", failures)
        if signal_state.signal_requests:
            query = parse_qs(urlparse(signal_state.signal_requests[0]).query)
            text = query.get("text", [""])[0]
            check("96" in text, "message contains the SoC", failures)
            check(query.get("phone", [""])[0] == DEFAULT_UUID, "UUID passed directly", failures)
            check(query.get("apikey", [""])[0] == "test-key", "api key passed", failures)

        print("\n10a. duplicate trigger inside the 60 minute window is suppressed")
        notify_state = addon2 / "state" / "notify_trigger"
        check(wait_until(notify_state.exists), "cooldown timestamp written", failures)
        first_timestamp = notify_state.read_text().strip() if notify_state.exists() else ""
        (addon2 / "state" / "signal").write_text("0\n")
        signal_state.reset_requests()
        result = run_check(addon2)
        time.sleep(1.5)
        check(result.returncode == 0, "main run exits 0", failures)
        check(not signal_state.signal_requests, "duplicate request suppressed", failures)
        check(
            notify_state.read_text().strip() == first_timestamp,
            "cooldown timestamp unchanged",
            failures,
        )

        print("\n10b. --test validates credentials without touching state")
        signal_before = (addon2 / "state" / "signal").read_text()
        signal_state.reset_requests()
        result = run_notify(addon2, "--test")
        check(result.returncode == 0, f"--test exits 0 (got {result.returncode})", failures)
        check(
            wait_until(lambda: bool(signal_state.signal_requests)),
            "test request received",
            failures,
        )
        if signal_state.signal_requests:
            query = parse_qs(urlparse(signal_state.signal_requests[0]).query)
            text = query.get("text", [""])[0]
            check(
                text == "Sentinella: Connection verified. Telemetry and alert channel ready.",
                "test message uses the compact English default",
                failures,
            )
            check("Überschuss" not in text, "test is not an overflow alert", failures)
            check("http" not in text, "test message contains no URL", failures)
        check(
            (addon2 / "state" / "signal").read_text() == signal_before,
            "state/signal unchanged by --test",
            failures,
        )
        check(
            notify_state.read_text().strip() == first_timestamp,
            "cooldown timestamp unchanged by --test",
            failures,
        )

        print("\n10c. German umlauts and spaces are URL-encoded")
        addon3 = make_addon(tmp, sma_port, rega_port, signal_port, True, name="addon3")
        reset_state(addon3)
        (addon3 / "state").mkdir(parents=True)
        (addon3 / "state" / "signal").write_text("0\n")
        with (addon3 / ".env").open("a") as env_file:
            env_file.write(
                'SIGNAL_MESSAGE_OVERFLOW="Achtung: Batteriespeicher hat {SOC}% '
                'erreicht! Überschuss verfügbar. Größe ß."\n'
            )
        sma_state.soc = 96
        sma_state.reset_requests()
        signal_state.reset_requests()
        result = run_check(addon3)
        check(result.returncode == 0, "main run exits 0", failures)
        check(
            wait_until(lambda: bool(signal_state.signal_requests)),
            "encoded request received",
            failures,
        )
        if signal_state.signal_requests:
            raw = signal_state.signal_requests[0]
            query = parse_qs(urlparse(raw).query)
            text = query.get("text", [""])[0]
            raw_lower = raw.lower()
            check(" " not in raw, "no literal spaces in URL", failures)
            check("%c3%9c" in raw_lower, "Ü is percent-encoded UTF-8", failures)
            check("%c3%9f" in raw_lower, "ß is percent-encoded UTF-8", failures)
            check(
                text
                == "Achtung: Batteriespeicher hat 96% erreicht! "
                "Überschuss verfügbar. Größe ß.",
                "decoded German message matches",
                failures,
            )

        print("\n10d. built-in messages follow UI_LANGUAGE=de")
        addon4 = make_addon(tmp, sma_port, rega_port, signal_port, True, name="addon4")
        reset_state(addon4)
        (addon4 / "state").mkdir(parents=True)
        (addon4 / "state" / "signal").write_text("0\n")
        with (addon4 / ".env").open("a") as env_file:
            env_file.write(
                'UI_LANGUAGE="de"\n'
                'SIGNAL_TEXT_TRIGGER=""\n'
                'SIGNAL_TEXT_RESET=""\n'
            )
        sma_state.soc = 96
        sma_state.reset_requests()
        signal_state.reset_requests()
        result = run_check(addon4)
        check(result.returncode == 0, "main run exits 0", failures)
        check(
            wait_until(lambda: bool(signal_state.signal_requests)),
            "German trigger request received",
            failures,
        )
        if signal_state.signal_requests:
            query = parse_qs(urlparse(signal_state.signal_requests[0]).query)
            text = query.get("text", [""])[0]
            check(
                text == "Sentinella: Batteriespeicher bei 96% (Schwelle: 95%). "
                        "Überschuss verfügbar.",
                "German trigger message matches",
                failures,
            )

        signal_state.reset_requests()
        result = run_notify(addon4, "--test")
        check(result.returncode == 0, "German --test exits 0", failures)
        check(
            wait_until(lambda: bool(signal_state.signal_requests)),
            "German test request received",
            failures,
        )
        if signal_state.signal_requests:
            query = parse_qs(urlparse(signal_state.signal_requests[0]).query)
            text = query.get("text", [""])[0]
            check(
                text == "Sentinella: Verbindung erfolgreich getestet. Alarmsystem bereit.",
                "German test message matches",
                failures,
            )

        print("\n11. setup_variables.sh builds the ReGa payload")
        rega_state.reset_requests()
        env = os.environ.copy()
        env["SMA_MONITOR_ENV"] = str(addon2 / ".env")
        result = subprocess.run(
            ["sh", str(addon2 / "setup_variables.sh")],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        body = "\n".join(rega_state.rega_bodies)
        check(result.returncode == 0, "setup exits 0", failures)
        check("OT_VARDP" in body, "variable type declared", failures)
        check("ivtFloat" in body, "SoC is a float", failures)
        check("ivtBinary" in body, "signal is binary", failures)
        check("ID_SYSTEM_VARIABLES" in body, "variables registered", failures)
        check("SV_Batterie_SoC" in body and "SV_Batterie_95_Signal" in body, "names in payload", failures)

        print("\n12. status.json is valid, published JSON")
        status = read_status(addon2)
        required = {
            "soc", "signal", "signal_state", "thresholds", "history",
            "ccu_status", "generated_at", "variables", "charging",
            "signal_gateway",
        }
        check(required.issubset(status.keys()), "all required fields present", failures)
        check(status.get("ui_language") == "en", "status.json reports the UI language", failures)
        check(isinstance(status["history"], list) and status["history"], "history present", failures)

        print("\n13. settings API returns the current configuration")
        result = run_cgi(addon2, "GET")
        status_code, config = parse_cgi(result)
        check(status_code == "200 OK", f"GET config returns 200 (got {status_code})", failures)
        legacy_status, legacy_config = parse_cgi(run_cgi(addon2, "GET", prefix="sma_battery"))
        check(
            legacy_status == "200 OK" and legacy_config.get("signal_phone") == DEFAULT_UUID,
            "GET config works identically under legacy /addons/sma_battery/ path",
            failures,
        )
        check(config.get("trigger_soc") == 95, "trigger level returned", failures)
        check(config.get("reset_soc") == 90, "reset level returned", failures)
        check(config.get("signal_phone") == DEFAULT_UUID, "UUID returned", failures)
        check(config.get("signal_apikey_set") is True, "API key presence reported", failures)
        check(config.get("sma_password_set") is True, "inverter password presence reported", failures)
        check(config.get("ui_language") == "en", "UI language returned", failures)
        check("test-key" not in result.stdout, "API key is never returned", failures)
        check("test-secret" not in result.stdout, "inverter password is never returned", failures)

        print("\n14. invalid settings are rejected before writing")
        env_path = addon2 / ".env"
        env_before = env_path.read_text()
        invalid_cases = [
            ({"trigger_soc": 150}, "trigger_soc"),
            ({"reset_soc": -1}, "reset_soc"),
            ({"trigger_soc": 80, "reset_soc": 90}, "trigger_soc"),
            ({"signal_phone": "not-a-uuid"}, "signal_phone"),
            ({"signal_phone": "0171 1234567"}, "signal_phone"),
            ({"sma_ip": "192.168.1.1/24"}, "sma_ip"),
            ({"sma_port": 70000}, "sma_port"),
            ({"ui_language": "fr"}, "ui_language"),
        ]
        for payload, field in invalid_cases:
            result = run_cgi(addon2, "POST", body=json.dumps(payload))
            status_code, error = parse_cgi(result)
            check(
                status_code.startswith("400") and error.get("field") == field,
                f"rejects {field}={payload.get(field)!r}",
                failures,
            )
        check(env_path.read_text() == env_before, "rejected input leaves .env unchanged", failures)

        result = run_cgi(addon2, "POST", body=json.dumps({
            "signal_phone": "+491701234567",
            "signal_api_key": "alias-key-99",
        }))
        status_code, saved = parse_cgi(result)
        env_text = env_path.read_text()
        check(
            status_code == "200 OK" and saved.get("signal_phone") == "+491701234567",
            "accepts an E.164 phone number as Signal recipient",
            failures,
        )
        check(
            "SIGNAL_APIKEY='alias-key-99'" in env_text,
            "signal_api_key alias is persisted to .env",
            failures,
        )
        # Restore the fixture key for the later atomic-write assertions.
        run_cgi(addon2, "POST", body=json.dumps({"signal_apikey": "test-key"}))

        print("\n15. valid settings are written atomically")
        env_path.write_text("# preserved comment\n" + env_path.read_text())
        env_path.chmod(0o600)
        payload = {
            "trigger_soc": 96,
            "reset_soc": 91,
            "signal_enabled": True,
            "signal_phone": UPDATED_UUID,
            "sma_ip": "10.1.2.3",
            "sma_port": 8443,
        }
        result = run_cgi(addon2, "POST", body=json.dumps(payload))
        status_code, saved = parse_cgi(result)
        env_text = env_path.read_text()
        check(status_code == "200 OK", f"POST config returns 200 (got {status_code})", failures)
        check(saved.get("trigger_soc") == 96 and saved.get("reset_soc") == 91, "new thresholds returned", failures)
        check(saved.get("signal_apikey_set") is True, "API key kept when field omitted", failures)
        check(saved.get("sma_password_set") is True, "password kept when field omitted", failures)
        check("THRESHOLD_TRIGGER='96'" in env_text, "threshold written to .env", failures)
        check(f"SIGNAL_PHONE='{UPDATED_UUID}'" in env_text, "UUID written to .env", failures)
        check("# preserved comment" in env_text, "comments preserved during rewrite", failures)
        check("SMA_PASS='test-secret'" in env_text, "existing secret preserved", failures)
        check(
            "SIGNAL_APIKEY='test-key'" in env_text or 'SIGNAL_APIKEY="test-key"' in env_text,
            "existing API key preserved",
            failures,
        )
        check((env_path.stat().st_mode & 0o777) == 0o600, ".env stays mode 600", failures)
        check(not list(addon2.glob(".env.tmp*")), "no temporary files left behind", failures)
        check(not (addon2 / "state" / ".config.lock").exists(), "config lock released", failures)

        print("\n15a. UI language is persisted through the settings API")
        result = run_cgi(addon4, "POST", body=json.dumps({"ui_language": "en"}))
        status_code, saved = parse_cgi(result)
        check(status_code == "200 OK", f"language POST returns 200 (got {status_code})", failures)
        check(saved.get("ui_language") == "en", "language returned in the response", failures)
        check('UI_LANGUAGE=\'en\'' in (addon4 / ".env").read_text(), "language written to .env", failures)
        status_code, config = parse_cgi(run_cgi(addon4, "GET"))
        check(status_code == "200 OK" and config.get("ui_language") == "en",
              "language survives a settings reload", failures)

        print("\n15b. Signal Account UUID is trimmed and passed directly to CallMeBot without phone normalization")
        result = run_cgi(addon4, "POST", body=json.dumps({"signal_phone": f"  {UPDATED_UUID}  "}))
        status_code, saved = parse_cgi(result)
        check(
            status_code == "200 OK" and saved.get("signal_phone") == UPDATED_UUID,
            "trims whitespace around Signal Account UUID on save",
            failures,
        )
        check(
            f"SIGNAL_PHONE='{UPDATED_UUID}'" in (addon4 / ".env").read_text(),
            "writes exact UUID with hyphens intact to .env",
            failures,
        )
        signal_state.reset_requests()
        result = run_notify(addon4, "--test")
        check(result.returncode == 0, "notify_signal.sh --test with UUID exits 0", failures)
        check(
            wait_until(lambda: bool(signal_state.signal_requests)),
            "UUID test request received",
            failures,
        )
        if signal_state.signal_requests:
            raw_req = signal_state.signal_requests[0]
            query = parse_qs(urlparse(raw_req).query)
            check(
                f"phone={UPDATED_UUID}" in raw_req,
                "UUID is passed directly in query string with hyphens intact",
                failures,
            )
            check(
                query.get("phone", [""])[0] == UPDATED_UUID,
                "gateway receives exact UUID",
                failures,
            )

        print("\n15c. status.json gateway state follows stored credentials")
        addon5 = make_addon(tmp, sma_port, rega_port, signal_port, True, name="addon5")
        reset_state(addon5)
        env5 = addon5 / ".env"
        lines = []
        for line in env5.read_text().splitlines():
            if line.startswith("SIGNAL_PHONE="):
                line = 'SIGNAL_PHONE=""'
            elif line.startswith("SIGNAL_APIKEY="):
                line = 'SIGNAL_APIKEY=""'
            elif line.startswith("SIGNAL_ENABLED="):
                line = 'SIGNAL_ENABLED="false"'
            lines.append(line)
        env5.write_text("\n".join(lines) + "\n")
        sma_state.soc = 90
        result = run_check(addon5)
        check(result.returncode == 0, "run without Signal credentials exits 0", failures)
        check(
            read_status(addon5).get("signal_gateway") == "Deaktiviert",
            "status.json reports the gateway as disabled without credentials",
            failures,
        )
        with env5.open("a") as env_file:
            env_file.write(f'SIGNAL_PHONE="{DEFAULT_UUID}"\nSIGNAL_APIKEY="test-key"\n')
        result = run_check(addon5)
        check(result.returncode == 0, "run with Signal credentials exits 0", failures)
        check(
            read_status(addon5).get("signal_gateway") == "CallMeBot (aktiv)",
            "status.json reports the gateway as active with credentials",
            failures,
        )

        print("\n15d. signal_enabled accepts on/off/1/0 and empty API keys never erase the stored key")
        addon6 = make_addon(tmp, sma_port, rega_port, signal_port, True, name="addon6")
        reset_state(addon6)
        env6 = addon6 / ".env"
        for raw, expected in (("on", "true"), ("1", "true"), ("off", "false"), ("0", "false")):
            result = run_cgi(addon6, "POST", body=json.dumps({"signal_enabled": raw}))
            status_code, saved = parse_cgi(result)
            env_text = env6.read_text()
            check(
                status_code == "200 OK" and saved.get("signal_enabled") == (expected == "true"),
                f"signal_enabled={raw!r} normalizes to {expected}",
                failures,
            )
            check(
                f"SIGNAL_ENABLED='{expected}'" in env_text
                or f'SIGNAL_ENABLED="{expected}"' in env_text,
                f"SIGNAL_ENABLED written as {expected} for {raw!r}",
                failures,
            )
        result = run_cgi(addon6, "POST", body=json.dumps({
            "signal_phone": DEFAULT_UUID,
            "signal_api_key": "",
            "signal_enabled": "on",
        }))
        status_code, saved = parse_cgi(result)
        env_text = env6.read_text()
        check(status_code == "200 OK", "empty signal_api_key payload is accepted", failures)
        check(saved.get("signal_apikey_set") is True, "stored API key still reported as set", failures)
        check(
            "test-key" in env_text,
            "empty signal_api_key does not erase the stored key",
            failures,
        )

        print("\n16. test notification is dispatched in the background")
        signal_before = (addon2 / "state" / "signal").read_text()
        signal_state.reset_requests()
        result = run_cgi(addon2, "POST", "test_signal", body="{}")
        status_code, started = parse_cgi(result)
        check(status_code.startswith("202"), f"test queued (got {status_code})", failures)
        check(started.get("status") == "queued", "test endpoint reports queued", failures)
        check(
            wait_until(lambda: bool(signal_state.signal_requests)),
            "test notification reached the gateway",
            failures,
        )
        if signal_state.signal_requests:
            query = parse_qs(urlparse(signal_state.signal_requests[0]).query)
            text = query.get("text", [""])[0]
            check(
                text == "Sentinella: Connection verified. Telemetry and alert channel ready.",
                "test message is clearly marked",
                failures,
            )
            check(query.get("phone", [""])[0] == UPDATED_UUID, "test uses the saved UUID", failures)

        deadline = time.time() + 8
        test_status = ""
        while time.time() < deadline:
            status_code, reported = parse_cgi(run_cgi(addon2, "GET", "test_signal"))
            test_status = reported.get("status", "")
            if test_status in {"sent", "failed"}:
                break
            time.sleep(0.1)
        check(test_status == "sent", f"test result reported as sent (got {test_status})", failures)
        check(
            (addon2 / "state" / "signal").read_text() == signal_before,
            "test does not change the battery signal state",
            failures,
        )

        if shutil.which("node"):
            print("\n17. CallMeBot onboarding message parser in web/app.js")
            node_script = """
const fs = require("fs");
const code = fs.readFileSync(process.argv[1], "utf8");
const uuidMatch = code.match(/function isSignalUuid\\([\\s\\S]*?\\n    \\}/);
const parseMatch = code.match(/function parseCallMeBotMessage\\([\\s\\S]*?\\n    \\}/);
if (!uuidMatch || !parseMatch) {
    process.exit(2);
}
const fn = new Function(uuidMatch[0] + "\\n" + parseMatch[0] + "\\nreturn parseCallMeBotMessage;")();
const samples = [
    {
        input: "APIKey created for Signal CallMeBot!!\\nThe APIKey created for you is: 653590\\nYou can now send messages using the API.\\nhttps://signal.callmebot.com/signal/send.php?phone=98526357-0cec-4a2b-8f1d-123456789abc&apikey=653590&text=This+is+a+test",
        phone: "98526357-0cec-4a2b-8f1d-123456789abc",
        apikey: "653590"
    },
    {
        input: "https://signal.callmebot.com/signal/send.php?phone=98526357-0cec-4e7f-9d60-d854432414d3&apikey=abc123XYZ&text=Hello",
        phone: "98526357-0cec-4e7f-9d60-d854432414d3",
        apikey: "abc123XYZ"
    },
    {
        input: "The APIKey created for you is:   998877  \\nYour UUID is 123e4567-e89b-12d3-a456-426614174000",
        phone: "123e4567-e89b-12d3-a456-426614174000",
        apikey: "998877"
    },
    {
        input: "apikey: 445566\\nphone=+491711234567",
        phone: "+491711234567",
        apikey: "445566"
    },
    {
        input: "ApiKey: '778899'\\nPhone: '+49 171 1234567'",
        phone: "+491711234567",
        apikey: "778899"
    },
    {
        input: "{ \\"apikey\\": \\"123456\\", \\"phone\\": \\"+491701234567\\" }",
        phone: "+491701234567",
        apikey: "123456"
    },
    {
        input: "apikey=445566&phone=98526357-0cec-4e7f-9d60-d854432414d3&text=Hello",
        phone: "98526357-0cec-4e7f-9d60-d854432414d3",
        apikey: "445566"
    },
    {
        input: "apikey: 445566.\\nphone: +491711234567!",
        phone: "+491711234567",
        apikey: "445566"
    },
    {
        input: "Send to 98526357-0cec-4e7f-9d60-d854432414d3:abc123XYZ, thanks",
        phone: "98526357-0cec-4e7f-9d60-d854432414d3",
        apikey: "abc123XYZ"
    }
];
for (const s of samples) {
    const res = fn(s.input);
    if (res.phone !== s.phone || res.apikey !== s.apikey) {
        console.error("Mismatch:", s, "got:", res);
        process.exit(1);
    }
}
"""
            node_res = subprocess.run(
                ["node", "-e", node_script, str(ROOT / "web" / "app.js")],
                capture_output=True,
                text=True,
                timeout=10,
            )
            check(
                node_res.returncode == 0,
                f"parseCallMeBotMessage extracts UUID & API key across CallMeBot formats ({node_res.stderr.strip() or 'ok'})",
                failures,
            )

            print("\n18. Streamlined EN/DE UI terminology, title, UUID-only labels, and footer in web/")
            i18n_script = """
const fs = require("fs");
const code = fs.readFileSync(process.argv[1], "utf8");
const html = fs.readFileSync(process.argv[2], "utf8");
const i18nMatch = code.match(/var I18N = (\\{[\\s\\S]*?\\n    \\});/);
if (!i18nMatch) process.exit(2);
const I18N = new Function("return " + i18nMatch[1])();
const assertEq = (actual, expected, label) => {
    if (actual !== expected) {
        console.error(label + ": expected " + JSON.stringify(expected) + ", got " + JSON.stringify(actual));
        process.exit(1);
    }
};
assertEq(I18N.en.pageTitle, "Sentinella", "EN pageTitle");
assertEq(I18N.de.pageTitle, "Sentinella", "DE pageTitle");
assertEq(I18N.en.brandSub, "SMA Battery Monitor for CCU3", "EN brandSub");
assertEq(I18N.de.brandSub, "SMA Batteriestatus für CCU3", "DE brandSub");
assertEq(I18N.en.hysteresisSignal, "Excess Signal", "EN signal box label");
assertEq(I18N.de.hysteresisSignal, "Überschuss-Signal", "DE signal box label");
assertEq(I18N.en.thresholdCorridor, "Thresholds", "EN slider title");
assertEq(I18N.de.thresholdCorridor, "Schaltschwellen", "DE slider title");
assertEq(I18N.en.legendBounds, "Thresholds", "EN chart bounds");
assertEq(I18N.de.legendBounds, "Schaltschwellen", "DE chart bounds");
assertEq(I18N.en.phoneLabel, "Signal Account UUID / Phone", "EN phoneLabel");
assertEq(I18N.de.phoneLabel, "Signal Account UUID / Phone", "DE phoneLabel");
assertEq(I18N.en.quickSetupLabel, "CallMeBot Onboarding Message", "EN quickSetupLabel");
assertEq(I18N.en.quickSetupHint, "Paste activation message from CallMeBot to automatically extract UUID and API key.", "EN quickSetupHint");
assertEq(I18N.de.quickSetupHint, "Nachricht vom CallMeBot hier einfügen, um UUID und API-Key automatisch zu übernehmen.", "DE quickSetupHint");
assertEq(I18N.de.signalCopyActive, "Aktiv. Schaltet ab unter {reset}%.", "DE active signal copy");
assertEq(I18N.de.signalCopyHeld, "Aktiv. Schaltet ab unter {reset}%.", "DE held signal copy");
assertEq(I18N.de.signalCopyInactive, "Inaktiv. Schaltet ein ab {trigger}%.", "DE inactive signal copy");
assertEq(I18N.en.signalCopyActive, "Active. Turns off below {reset}%.", "EN active signal copy");
assertEq(I18N.en.signalCopyInactive, "Inactive. Turns on at {trigger}%.", "EN inactive signal copy");
if (!html.includes("<title>Sentinella</title>")) {
    console.error("HTML title not simplified to <title>Sentinella</title>");
    process.exit(1);
}
if (!html.includes('placeholder="98526357-0cec-4e7f-9d60-d854432414d3"')) {
    console.error("UUID placeholder missing in HTML");
    process.exit(1);
}
if (!html.includes("<span>SENTINELLA · CCU3</span>") || html.includes("footerTagline")) {
    console.error("Footer not simplified properly");
    process.exit(1);
}
"""
            i18n_res = subprocess.run(
                ["node", "-e", i18n_script, str(ROOT / "web" / "app.js"), str(ROOT / "web" / "index.html")],
                capture_output=True,
                text=True,
                timeout=10,
            )
            check(
                i18n_res.returncode == 0,
                f"EN/DE UI strings and footer match streamlined copy ({i18n_res.stderr.strip() or 'ok'})",
                failures,
            )

            print("\n19. Signal notifications toggle locks credentials until enabled")
            toggle_script = """
const fs = require("fs");
const code = fs.readFileSync(process.argv[1], "utf8");
const html = fs.readFileSync(process.argv[2], "utf8");
const fail = (label) => { console.error(label); process.exit(1); };
if (!/id="signal_enabled"/.test(html)) fail("signal_enabled toggle id missing");
if (!/for="signal_enabled"/.test(html)) fail("signal_enabled label target missing");
if (!/id="signal_credentials_group"/.test(html)) fail("signal_credentials_group container missing");
const groupMatch = html.match(/id="signal_credentials_group"[\\s\\S]*?<\\/fieldset>/);
if (!groupMatch) fail("signal_credentials_group is not closed before the fieldset ends");
for (const id of ["callmebot-paste", "signal-phone", "signal-apikey"]) {
    if (!groupMatch[0].includes('id="' + id + '"')) fail(id + " is outside the locked group");
}
if (!/data-signal-control/.test(html)) fail("test notification is not marked as a Signal control");
if (!/function setSignalLocked\\(/.test(code)) fail("setSignalLocked missing");
if (!/group\\.querySelectorAll\\("input, textarea, button, select"\\)/.test(code)) fail("lock does not disable grouped controls");
if (!/signal_enabled:\\s*true/.test(code)) fail("auto-save does not force signal_enabled true");
if (!/isMaskedApiKey\\(/.test(code)) fail("masked API key guard missing");
if (/payload\\.signal_enabled = el\\.signalEnabled\\.checked/.test(code)) fail("auto-save still copies an unchecked toggle");
"""
            toggle_res = subprocess.run(
                ["node", "-e", toggle_script, str(ROOT / "web" / "app.js"), str(ROOT / "web" / "index.html")],
                capture_output=True,
                text=True,
                timeout=10,
            )
            check(
                toggle_res.returncode == 0,
                f"toggle gates credentials, auto-save forces enabled and preserves masked keys ({toggle_res.stderr.strip() or 'ok'})",
                failures,
            )

        print("\n20. plugins API returns concatenated JSON array of modular plugins")
        # Subcase A: No plugins exist -> returns []
        empty_plugins_res = run_cgi(addon2, "GET", "plugins")
        status_line, empty_plugins = parse_cgi(empty_plugins_res)
        check(status_line == "Content-Type: application/json; charset=utf-8", "plugins headers correct", failures)
        check(empty_plugins == [], f"empty plugins dir returns [] (got {empty_plugins})", failures)

        # Subcase B: 2 plugin JSON files exist -> returns valid array [ {...}, {...} ]
        plugins_state = addon2 / "state" / "plugins"
        plugins_state.mkdir(parents=True, exist_ok=True)
        plugin1_data = {
            "id": "tibber",
            "name": "Tibber Pulse",
            "timestamp": "2026-11-07T15:30:00Z",
            "data": {"current_power_w": 1450, "price_eur_kwh": 0.285}
        }
        plugin2_data = {
            "id": "heatpump",
            "name": "SG-Ready Heat Pump",
            "timestamp": "2026-11-07T15:30:05Z",
            "data": {"status": "boost", "target_temp_c": 55.0}
        }
        (plugins_state / "10_tibber.json").write_text(json.dumps(plugin1_data) + "\n")
        (plugins_state / "20_heatpump.json").write_text(json.dumps(plugin2_data) + "\n")

        plugins_res = run_cgi(addon2, "GET", "plugins")
        status_line, plugins_array = parse_cgi(plugins_res)
        check(isinstance(plugins_array, list), "plugins response is a JSON list", failures)
        check(len(plugins_array) == 2, f"plugins response has 2 entries (got {len(plugins_array)})", failures)
        plugin_ids = {p.get("id") for p in plugins_array if isinstance(p, dict)}
        check(plugin_ids == {"tibber", "heatpump"}, f"plugin IDs match expected (got {plugin_ids})", failures)
        tibber_entry = next((p for p in plugins_array if p.get("id") == "tibber"), None)
        heatpump_entry = next((p for p in plugins_array if p.get("id") == "heatpump"), None)
        check(tibber_entry == plugin1_data, "tibber plugin JSON matches original object", failures)
        check(heatpump_entry == plugin2_data, "heatpump plugin JSON matches original object", failures)
    finally:
        sma.shutdown()
        rega.shutdown()
        sig.shutdown()
        sma.server_close()
        rega.server_close()
        sig.server_close()
        if not failures:
            shutil.rmtree(tmp, ignore_errors=True)

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for failure in failures:
            print(f"  - {failure}")
        print(f"artifacts kept in {tmp}")
        return 1
    print("All integration checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
