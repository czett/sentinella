#!/usr/bin/env python3
"""Integration tests for homematic-sma-battery-monitor.

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
SIGNAL_ENABLED="{'true' if signal_enabled else 'false'}"
SIGNAL_PHONE="+491701234567"
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
            body: str = "", timeout: float = 20) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update(
        {
            "REQUEST_METHOD": method,
            "SCRIPT_NAME": f"/addons/sma_battery/api/{action}.cgi",
            "PATH_INFO": "",
            "CONTENT_LENGTH": str(len(body.encode())),
            "CONTENT_TYPE": "application/json",
            "SMA_MONITOR_ADDON_DIR": str(addon),
            "SMA_MONITOR_ENV": str(addon / ".env"),
        }
    )
    return subprocess.run(
        ["sh", str(ROOT / "web" / "api" / "config.cgi")],
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
            check(query.get("phone", [""])[0] == "+491701234567", "phone passed", failures)
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
            check("Testnachricht" in text, "test message is clearly marked", failures)
            check("Überschuss" not in text, "test is not an overflow alert", failures)
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
        }
        check(required.issubset(status.keys()), "all required fields present", failures)
        check(isinstance(status["history"], list) and status["history"], "history present", failures)

        print("\n13. settings API returns the current configuration")
        result = run_cgi(addon2, "GET")
        status_code, config = parse_cgi(result)
        check(status_code == "200 OK", f"GET config returns 200 (got {status_code})", failures)
        check(config.get("trigger_soc") == 95, "trigger level returned", failures)
        check(config.get("reset_soc") == 90, "reset level returned", failures)
        check(config.get("signal_phone") == "+491701234567", "phone returned", failures)
        check(config.get("signal_apikey_set") is True, "API key presence reported", failures)
        check(config.get("sma_password_set") is True, "inverter password presence reported", failures)
        check("test-key" not in result.stdout, "API key is never returned", failures)
        check("test-secret" not in result.stdout, "inverter password is never returned", failures)

        print("\n14. invalid settings are rejected before writing")
        env_path = addon2 / ".env"
        env_before = env_path.read_text()
        invalid_cases = [
            ({"trigger_soc": 150}, "trigger_soc"),
            ({"reset_soc": -1}, "reset_soc"),
            ({"trigger_soc": 80, "reset_soc": 90}, "trigger_soc"),
            ({"signal_phone": "not-a-phone"}, "signal_phone"),
            ({"sma_ip": "192.168.1.1/24"}, "sma_ip"),
            ({"sma_port": 70000}, "sma_port"),
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

        print("\n15. valid settings are written atomically")
        env_path.write_text("# preserved comment\n" + env_path.read_text())
        env_path.chmod(0o600)
        payload = {
            "trigger_soc": 96,
            "reset_soc": 91,
            "signal_enabled": True,
            "signal_phone": "+491709876543",
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
        check("SIGNAL_PHONE='+491709876543'" in env_text, "phone written to .env", failures)
        check("# preserved comment" in env_text, "comments preserved during rewrite", failures)
        check("SMA_PASS='test-secret'" in env_text, "existing secret preserved", failures)
        check("SIGNAL_APIKEY=\"test-key\"" in env_text, "existing API key preserved", failures)
        check((env_path.stat().st_mode & 0o777) == 0o600, ".env stays mode 600", failures)
        check(not list(addon2.glob(".env.tmp*")), "no temporary files left behind", failures)
        check(not (addon2 / "state" / ".config.lock").exists(), "config lock released", failures)

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
            check("Testnachricht" in text, "test message is clearly marked", failures)
            check(query.get("phone", [""])[0] == "+491709876543", "test uses the saved phone", failures)

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
