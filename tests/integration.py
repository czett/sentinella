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
from datetime import date, timedelta
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
        self.forecast_requests: list[str] = []
        self.renault_login_calls = 0
        self.renault_jwt_calls = 0
        self.renault_person_calls = 0
        self.renault_battery_calls = 0
        self.renault_hvac_calls = 0
        self.renault_hvac_bodies: list[str] = []
        self.renault_cockpit_calls = 0
        self.renault_mileage: float | None = 45210
        self.renault_hvac_status: str | None = None
        self.renault_battery_level = 78
        self.renault_autonomy = 214
        self.renault_plug_status = 1
        self.renault_charging_status = 1.0
        self.renault_force_401_once = False

    def reset_requests(self) -> None:
        with self.lock:
            self.logins = 0
            self.logouts = 0
            self.get_requests = 0
            self.rega_bodies = []
            self.signal_requests = []
            self.forecast_requests = []
            self.renault_login_calls = 0
            self.renault_jwt_calls = 0
            self.renault_person_calls = 0
            self.renault_battery_calls = 0
            self.renault_hvac_calls = 0
            self.renault_hvac_bodies = []
            self.renault_cockpit_calls = 0


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


class ForecastHandler(BaseHTTPRequestHandler):
    """Minimal forecast.solar estimate endpoint using live-relative dates."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *_args) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802
        state: State = self.server.state  # type: ignore[attr-defined]
        with state.lock:
            state.forecast_requests.append(self.path)
        today = date.today()
        tomorrow = today + timedelta(days=1)
        payload = {
            "result": {
                "watt_hours_day": {
                    today.isoformat(): 18400,
                    tomorrow.isoformat(): 15200,
                }
            },
            "message": {"code": 0, "type": "success", "text": ""},
        }
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
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


class RenaultHandler(BaseHTTPRequestHandler):
    """Mock Gigya + Kamereon endpoints for Renault Zoe plugin tests."""

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
        raw = self.rfile.read(length).decode("utf-8", "replace") if length else ""

        if parsed.path == "/accounts.login":
            with state.lock:
                state.renault_login_calls += 1
            params = parse_qs(raw)
            user = params.get("loginID", [""])[0]
            pw = params.get("password", [""])[0]
            if user != "zoe@example.com" or pw != "renault-secret-42":
                self._json(200, {"errorCode": 403042, "errorDetails": "invalid loginID or password"})
                return
            self._json(200, {"sessionInfo": {"cookieName": "gmid", "cookieValue": "gigya-login-token-xyz"}})
            return

        if parsed.path == "/accounts.getJWT":
            with state.lock:
                state.renault_jwt_calls += 1
            content_type = self.headers.get("Content-Type", "")
            if (
                not content_type.startswith("multipart/form-data")
                or 'name="ApiKey"' not in raw
                or "3_VgdkgtIRH3AdHvJm-cjV2ug2EFE0lxt0IJzMC4MFqZjFpn_GYFXVdNZ19L7wZX0N" not in raw
                or "gigya-login-token-xyz" not in raw
                or "data.personId,data.gigyaDataCenter" not in raw
                or 'name="expiration"' not in raw
            ):
                self._json(400, {"errorCode": 400006})
                return
            self._json(
                200,
                {
                    "id_token": "jwt-id-token-valid-999",
                    "data.personId": "person-de-001",
                    "data.gigyaDataCenter": "eu1",
                },
            )
            return

        expected_hvac_path = (
            "/commerce/v1/accounts/acc-myrenault-777/kamereon/kca/"
            "car-adapter/v1/cars/VF1AG000012345678/actions/hvac-start"
        )
        if parsed.path == expected_hvac_path:
            id_token = self.headers.get("x-gigya-id_token", "")
            api_key = self.headers.get("apikey", "")
            ct = self.headers.get("Content-Type", "")
            with state.lock:
                state.renault_hvac_calls += 1
                state.renault_hvac_bodies.append(raw)
                if state.renault_force_401_once:
                    state.renault_force_401_once = False
                    self._json(401, {"error": "token expired"})
                    return
            if id_token != "jwt-id-token-valid-999" or not api_key or "application/vnd.api+json" not in ct:
                self._json(401, {"error": "unauthorized or bad content-type"})
                return
            try:
                hvac_doc = json.loads(raw)
            except json.JSONDecodeError:
                hvac_doc = {}
            data_obj = hvac_doc.get("data") or {}
            attrs = data_obj.get("attributes") or {}
            is_cancel = attrs.get("action") == "cancel"
            if (
                data_obj.get("type") != "HvacStart"
                or not (
                    is_cancel
                    or (
                        attrs.get("action") == "start"
                        and float(attrs.get("targetTemperature", 0)) == 21.0
                    )
                )
            ):
                self._json(400, {"error": "invalid hvac payload"})
                return
            self._json(200, {"data": {"type": "HvacStart", "id": "hvac-req-1"}})
            return

        self._json(404, {"err": 404})

    def do_GET(self) -> None:  # noqa: N802
        state: State = self.server.state  # type: ignore[attr-defined]
        parsed = urlparse(self.path)
        id_token = self.headers.get("x-gigya-id_token", "")
        api_key = self.headers.get("apikey", "")

        if parsed.path == "/commerce/v1/persons/person-de-001":
            with state.lock:
                state.renault_person_calls += 1
            if id_token != "jwt-id-token-valid-999" or not api_key:
                self._json(401, {"error": "unauthorized"})
                return
            self._json(
                200,
                {
                    "accounts": [
                        {"accountId": "acc-dacia-ignore", "accountType": "MYDACIA"},
                        {"accountId": "acc-myrenault-777", "accountType": "MYRENAULT"},
                    ]
                },
            )
            return

        kam_base = "/commerce/v1/accounts/acc-myrenault-777/kamereon/kca/car-adapter"
        if parsed.path == f"{kam_base}/v1/cars/VF1AG000012345678/cockpit":
            with state.lock:
                state.renault_cockpit_calls += 1
                mileage = state.renault_mileage
            if id_token != "jwt-id-token-valid-999" or not api_key:
                self._json(401, {"error": "unauthorized"})
                return
            attrs = {} if mileage is None else {"totalMileage": mileage}
            self._json(200, {"data": {"type": "Car", "attributes": attrs}})
            return
        if parsed.path == f"{kam_base}/v1/cars/VF1AG000012345678/hvac-status":
            with state.lock:
                hvac_status = state.renault_hvac_status
            if hvac_status is None:
                self._json(404, {"error": "not supported"})
                return
            self._json(200, {"data": {"attributes": {"hvacStatus": hvac_status}}})
            return

        expected_bat_path = (
            "/commerce/v1/accounts/acc-myrenault-777/kamereon/kca/"
            "car-adapter/v2/cars/VF1AG000012345678/battery-status"
        )
        if parsed.path == expected_bat_path:
            with state.lock:
                state.renault_battery_calls += 1
                if state.renault_force_401_once:
                    state.renault_force_401_once = False
                    self._json(401, {"error": "token expired"})
                    return
                if id_token != "jwt-id-token-valid-999" or not api_key:
                    self._json(401, {"error": "invalid token"})
                    return
                level = state.renault_battery_level
                autonomy = state.renault_autonomy
                plug = state.renault_plug_status
                charging = state.renault_charging_status
            self._json(
                200,
                {
                    "data": {
                        "type": "Car",
                        "id": "VF1AG000012345678",
                        "attributes": {
                            "timestamp": "2026-11-07T14:30:00Z",
                            "batteryLevel": level,
                            "batteryAutonomy": autonomy,
                            "plugStatus": plug,
                            "chargingStatus": charging,
                        },
                    }
                },
            )
            return

        self._json(404, {"err": 404})


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
    forecast_state = State()
    fc = start_server(ForecastHandler, forecast_state)
    renault_state = State()
    rn = start_server(RenaultHandler, renault_state)
    sma_port = sma.server_address[1]
    rega_port = rega.server_address[1]
    signal_port = sig.server_address[1]
    forecast_port = fc.server_address[1]
    renault_port = rn.server_address[1]

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

        # Subcase B: test fixtures are copied into the isolated test add-on only,
        # never into the repository's production state/plugins directory.
        fixture_dir = ROOT / "tests" / "fixtures" / "plugins"
        fixture_names = ["01_zoe.json", "02_forecast.json", "03_wallbox.json"]
        plugins_state = addon2 / "state" / "plugins"
        plugins_state.mkdir(parents=True, exist_ok=True)
        fixture_payloads = {}
        for name in fixture_names:
            source = fixture_dir / name
            check(source.is_file(), f"test fixture {name} exists", failures)
            if not source.is_file():
                continue
            shutil.copy2(source, plugins_state / name)
            fixture_payloads[name] = json.loads(source.read_text())

        plugins_res = run_cgi(addon2, "GET", "plugins")
        status_line, plugins_array = parse_cgi(plugins_res)
        check(isinstance(plugins_array, list), "plugins response is a JSON list", failures)
        check(
            len(plugins_array) == len(fixture_payloads),
            f"plugins response has {len(fixture_payloads)} entries (got {len(plugins_array)})",
            failures,
        )
        plugin_ids = {p.get("id") for p in plugins_array if isinstance(p, dict)}
        expected_ids = {p.get("id") for p in fixture_payloads.values()}
        check(plugin_ids == expected_ids, f"plugin IDs match fixtures (got {plugin_ids})", failures)
        for payload in fixture_payloads.values():
            check(
                any(entry == payload for entry in plugins_array),
                f"fixture {payload.get('id')} JSON matches original object",
                failures,
            )
        check(
            not any((ROOT / "state" / "plugins" / name).exists() for name in fixture_names),
            "plugins.cgi test does not pollute production state/plugins",
            failures,
        )

        if shutil.which("node"):
            print("\n21. web/app.js and web/index.html plugins grid validation")
            frontend_plugin_script = r"""
const fs = require("fs");
const appCode = fs.readFileSync(process.argv[1], "utf8");
const htmlCode = fs.readFileSync(process.argv[2], "utf8");
const fail = (msg) => { console.error(msg); process.exit(1); };

// 1. Verify HTML anchor
if (!htmlCode.includes('id="plugins-section"')) fail("missing plugins-section in index.html");
if (!htmlCode.includes('class="plugins-section"')) fail("missing class plugins-section in index.html");
if (!htmlCode.includes('id="plugins-grid"')) fail("missing plugins-grid in index.html");
if (!htmlCode.includes('class="plugins-grid"')) fail("missing class plugins-grid in index.html");
if (!htmlCode.includes('data-i18n="pluginsTitle"')) fail("missing data-i18n pluginsTitle in index.html");

// 2. Verify JS functions & templates
if (!appCode.includes("fetchAndRenderPlugins")) fail("fetchAndRenderPlugins missing in app.js");
if (!appCode.includes("renderPluginCard")) fail("renderPluginCard missing in app.js");
if (!appCode.includes("escapeHtml")) fail("escapeHtml missing in app.js");

// 3. Test renderPluginCard logic in a mocked DOM environment
const pluginI18nMatch = appCode.match(/var PLUGIN_I18N = \{[\s\S]*?\n    \};/);
const translatePluginMatch = appCode.match(/function translatePluginText\([\s\S]*?\n    \}/);
const escapeHtmlMatch = appCode.match(/function escapeHtml\([\s\S]*?\n    \}/);
const secondaryMetricsMatch = appCode.match(/function renderSecondaryMetrics\([\s\S]*?\n    \}/);
const renderPluginCardMatch = appCode.match(/function renderPluginCard\([\s\S]*?\n    \}/);

if (!pluginI18nMatch || !translatePluginMatch || !escapeHtmlMatch || !secondaryMetricsMatch || !renderPluginCardMatch) {
    fail("could not extract plugin functions from app.js");
}

const mockEnv = `
var currentLanguage = "de";
const document = {
    createElement: function(tag) {
        return {
            tagName: tag,
            className: "",
            innerHTML: "",
            setAttribute: function(k, v) { this[k] = v; }
        };
    }
};
` + pluginI18nMatch[0] + "\n" + translatePluginMatch[0] + "\n" + escapeHtmlMatch[0] + "\n" + secondaryMetricsMatch[0] + "\n" + renderPluginCardMatch[0] + "\n" + `

// Test Layout: stat
const statCard = renderPluginCard({
    layout: "stat",
    title: "Solar Production",
    status_badge: "AKTIV",
    status_color: "green",
    primary: { value: 4.8, unit: "kW", label: "Peak Generation" },
    secondary_metrics: [{ label: "Daily Total", value: 24.5, unit: "kWh" }]
});
if (!statCard.innerHTML.includes("Solar Production")) throw new Error("statCard title missing");
if (!statCard.innerHTML.includes("4.8")) throw new Error("statCard value missing");
if (!statCard.innerHTML.includes("kW")) throw new Error("statCard unit missing");
if (!statCard.innerHTML.includes("AKTIV")) throw new Error("statCard badge missing");
if (!statCard.innerHTML.includes("Daily Total")) throw new Error("statCard secondary metrics missing");

// Test Layout: delta
const deltaCard = renderPluginCard({
    layout: "delta",
    title: "Grid Feed-In",
    status_badge: "EXPORT",
    status_color: "blue",
    primary: { value: 3.2, unit: "kW" },
    delta: { direction: "up", value: "+1.2 kW", comparison: "vs. Yesterday: +3.2 kWh" }
});
if (!deltaCard.innerHTML.includes("Grid Feed-In")) throw new Error("deltaCard title missing");
if (!deltaCard.innerHTML.includes("+1.2 kW")) throw new Error("deltaCard delta value missing");
if (!deltaCard.innerHTML.includes("vs. Yesterday: +3.2 kWh")) throw new Error("deltaCard comparison missing");

// Test Layout: list
const listCard = renderPluginCard({
    layout: "list",
    title: "SG-Ready Heat Pump",
    status_badge: "BOOST",
    status_color: "green",
    secondary_metrics: [
        { label: "Compressor", value: "Active", unit: "" },
        { label: "Target Temp", value: 55, unit: "°C" }
    ]
});
if (!listCard.innerHTML.includes("SG-Ready Heat Pump")) throw new Error("listCard title missing");
if (!listCard.innerHTML.includes("Compressor")) throw new Error("listCard row 1 missing");
if (!listCard.innerHTML.includes("Target Temp")) throw new Error("listCard row 2 missing");

// Test XSS safety
const xssCard = renderPluginCard({
    layout: "stat",
    title: "<script>alert('xss')</script>",
    primary: { value: "\\"><img src=x onerror=1>", unit: "" }
});
if (xssCard.innerHTML.includes("<script>")) throw new Error("XSS script tag not escaped");
if (xssCard.innerHTML.includes("<img")) throw new Error("XSS img tag not escaped");
`;

eval(mockEnv);
"""
            fe_res = subprocess.run(
                ["node", "-e", frontend_plugin_script, str(ROOT / "web" / "app.js"), str(ROOT / "web" / "index.html")],
                capture_output=True,
                text=True,
                timeout=10,
            )
            check(
                fe_res.returncode == 0,
                f"web/app.js & web/index.html plugin layout and XSS checks pass ({fe_res.stderr.strip() or 'ok'})",
                failures,
            )

        print("\n22. forecast.solar plugin and test fixtures")
        plugin_path = ROOT / "plugins.d" / "30_forecast_solar.sh"
        check(plugin_path.is_file(), "plugins.d/30_forecast_solar.sh exists", failures)
        syntax_res = subprocess.run(
            ["sh", "-n", str(plugin_path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        check(
            syntax_res.returncode == 0,
            f"30_forecast_solar.sh passes sh -n ({syntax_res.stderr.strip() or 'ok'})",
            failures,
        )

        fc_addon = tmp / "forecast-addon"
        (fc_addon / "plugins.d").mkdir(parents=True)
        (fc_addon / "config" / "plugins").mkdir(parents=True)
        fc_script = fc_addon / "plugins.d" / "30_forecast_solar.sh"
        shutil.copy2(plugin_path, fc_script)
        fc_script.chmod(0o755)
        (fc_addon / "config" / "plugins" / "forecast_solar.env").write_text(
            'FORECAST_LAT="51.43"\n'
            'FORECAST_LON="7.76"\n'
            'FORECAST_DEC="35"\n'
            'FORECAST_AZ="0"\n'
            'FORECAST_KWP="10"\n'
        )

        fc_env = os.environ.copy()
        fc_env["FORECAST_API_URL"] = f"http://127.0.0.1:{forecast_port}/estimate"
        fc_env.pop("FORECAST_STATE_DIR", None)
        forecast_state.reset_requests()
        fc_result = subprocess.run(
            [str(fc_script)], env=fc_env, capture_output=True, text=True, timeout=20
        )
        check(
            fc_result.returncode == 0,
            f"forecast plugin exits 0 (got {fc_result.returncode})",
            failures,
        )
        check(bool(forecast_state.forecast_requests), "forecast API queried once", failures)
        fc_json_path = fc_addon / "state" / "plugins" / "forecast_solar.json"
        check(fc_json_path.is_file(), "forecast_solar.json written", failures)
        fc_data = json.loads(fc_json_path.read_text()) if fc_json_path.is_file() else {}
        check(fc_data.get("layout") == "delta", "forecast card uses delta layout", failures)
        check(fc_data.get("id") == "forecast_solar", "forecast card id", failures)
        check(fc_data.get("title") == "PV-PROGNOSE", "forecast card title (DE default)", failures)
        check(
            fc_data.get("status_badge") in {"SONNIG", "MODERAT", "BEWÖLKT"},
            f"forecast badge set (got {fc_data.get('status_badge')!r})",
            failures,
        )
        check(
            fc_data.get("status_color") in {"green", "blue", "neutral"},
            "forecast badge color valid",
            failures,
        )
        fc_primary = fc_data.get("primary") or {}
        fc_delta = fc_data.get("delta") or {}
        fc_metrics = fc_data.get("secondary_metrics") or []
        check(
            isinstance(fc_primary.get("value"), (int, float))
            and abs(float(fc_primary["value"]) - 18.4) < 0.06,
            f"today's yield is 18.4 kWh (got {fc_primary.get('value')!r})",
            failures,
        )
        check(fc_primary.get("unit") == "kWh", "primary unit is kWh", failures)
        check(fc_primary.get("label") == "PROGNOSE HEUTE", "primary label is PROGNOSE HEUTE", failures)
        check(fc_delta.get("direction") == "down", "delta points down vs. tomorrow", failures)
        check(fc_delta.get("value") == "-3.2 kWh", "delta value is -3.2 kWh", failures)
        check(fc_delta.get("comparison") == "von gestern", "delta comparison label (DE)", failures)
        fc_labels = {m.get("label") for m in fc_metrics if isinstance(m, dict)}
        check({"MORGEN", "SPITZE"}.issubset(fc_labels), "secondary metrics present (DE)", failures)

        # Offline must keep the last good card byte-for-byte.
        before = fc_json_path.read_text()
        fc_env_offline = os.environ.copy()
        fc_env_offline["FORECAST_API_URL"] = "http://127.0.0.1:1/estimate"
        fc_env_offline.pop("FORECAST_STATE_DIR", None)
        offline_res = subprocess.run(
            [str(fc_script)], env=fc_env_offline, capture_output=True, text=True, timeout=20
        )
        check(offline_res.returncode == 0, "offline run still exits 0", failures)
        check(fc_json_path.read_text() == before, "existing card kept when offline", failures)

        # Without a previous card the plugin publishes a neutral badge instead.
        fc_json_path.unlink()
        offline_res = subprocess.run(
            [str(fc_script)], env=fc_env_offline, capture_output=True, text=True, timeout=20
        )
        check(offline_res.returncode == 0, "offline fallback exits 0", failures)
        check(fc_json_path.is_file(), "offline fallback writes a card", failures)
        neutral = json.loads(fc_json_path.read_text()) if fc_json_path.is_file() else {}
        check(neutral.get("status_badge") == "OFFLINE", "offline badge is neutral", failures)
        check(neutral.get("layout") == "delta", "offline card keeps the delta layout", failures)
        check(
            isinstance(neutral.get("secondary_metrics"), list),
            "offline card has a metrics list",
            failures,
        )

        fixtures_dir = ROOT / "tests" / "fixtures" / "plugins"
        expected_fixtures = {
            "01_zoe.json": ("stat", "LÄDT"),
            "02_forecast.json": ("delta", "OPTIMAL"),
            "03_wallbox.json": ("list", "BEREIT"),
        }
        for name, (layout, badge) in expected_fixtures.items():
            fixture = fixtures_dir / name
            check(fixture.is_file(), f"{name} exists in tests/fixtures/plugins", failures)
            if not fixture.is_file():
                continue
            payload = json.loads(fixture.read_text())
            check(payload.get("layout") == layout, f"{name} layout is {layout}", failures)
            check(payload.get("status_badge") == badge, f"{name} badge is {badge}", failures)
            check(
                isinstance(payload.get("secondary_metrics"), list),
                f"{name} has secondary metrics",
                failures,
            )
            check(
                not (ROOT / "state" / "plugins" / name).exists(),
                f"{name} is not shipped in production state/plugins",
                failures,
            )
        check(
            (ROOT / "state" / "plugins" / ".gitkeep").is_file(),
            "state/plugins keeps only .gitkeep",
            failures,
        )
        check(
            not (ROOT / "fixtures" / "dev").exists(),
            "legacy fixtures/dev directory is gone",
            failures,
        )

        print("\n23. Renault Zoe plugin, token caching, settings API & WebUI integration")
        zoe_plugin_path = ROOT / "plugins.d" / "20_renault_zoe.sh"
        renault_example_path = ROOT / "config" / "plugins" / "renault.env.example"
        check(zoe_plugin_path.is_file(), "plugins.d/20_renault_zoe.sh exists", failures)
        check(renault_example_path.is_file(), "config/plugins/renault.env.example exists", failures)
        if renault_example_path.is_file():
            ex_text = renault_example_path.read_text()
            for expected_line in ('RENAULT_USER=""', 'RENAULT_PASS=""', 'RENAULT_VIN=""'):
                check(
                    expected_line in ex_text,
                    f"renault.env.example contains {expected_line}",
                    failures,
                )

        zoe_syntax = subprocess.run(
            ["sh", "-n", str(zoe_plugin_path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        check(
            zoe_syntax.returncode == 0,
            f"20_renault_zoe.sh passes sh -n ({zoe_syntax.stderr.strip() or 'ok'})",
            failures,
        )

        zoe_addon = make_addon(tmp, sma_port, rega_port, signal_port, False, name="zoe-addon")
        (zoe_addon / "plugins.d").mkdir(parents=True, exist_ok=True)
        (zoe_addon / "config" / "plugins").mkdir(parents=True, exist_ok=True)
        (zoe_addon / "state" / "plugins").mkdir(parents=True, exist_ok=True)
        zoe_script = zoe_addon / "plugins.d" / "20_renault_zoe.sh"
        shutil.copy2(zoe_plugin_path, zoe_script)
        zoe_script.chmod(0o755)
        check(
            (zoe_script.stat().st_mode & 0o777) == 0o755,
            "20_renault_zoe.sh has mode 755",
            failures,
        )

        zoe_card_path = zoe_addon / "state" / "plugins" / "renault_zoe.json"
        zoe_session_path = zoe_addon / "state" / "plugins" / ".renault_session"

        # 23a. Missing / empty credentials -> clean exit 0 without writing card or error
        noop_res = subprocess.run(
            [str(zoe_script)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        check(noop_res.returncode == 0, "unconfigured Renault plugin exits 0", failures)
        check(not zoe_card_path.exists(), "unconfigured Renault plugin writes no card", failures)
        check(not zoe_session_path.exists(), "unconfigured Renault plugin writes no session", failures)

        # 23b. Settings API saves Renault credentials with mode 600 and masks password on GET
        post_renault = run_cgi(
            zoe_addon,
            "POST",
            "settings",
            body=json.dumps(
                {
                    "renault_user": "zoe@example.com",
                    "renault_pass": "renault-secret-42",
                    "renault_vin": "vf1ag000012345678",
                }
            ),
        )
        r_status, r_saved = parse_cgi(post_renault)
        renault_env_file = zoe_addon / "config" / "plugins" / "renault.env"
        check(r_status == "200 OK", f"POST settings.cgi returns 200 (got {r_status})", failures)
        check(renault_env_file.is_file(), "config/plugins/renault.env created", failures)
        check(
            (renault_env_file.stat().st_mode & 0o777) == 0o600,
            "config/plugins/renault.env has mode 600",
            failures,
        )
        check(
            r_saved.get("renault_user") == "zoe@example.com",
            "renault_user returned in settings response",
            failures,
        )
        check(
            r_saved.get("renault_vin") == "VF1AG000012345678",
            "renault_vin normalized to uppercase",
            failures,
        )
        check(
            r_saved.get("renault_pass_set") is True,
            "renault_pass_set is True",
            failures,
        )
        check(
            "renault-secret-42" not in post_renault.stdout,
            "Renault password is never returned in CGI response",
            failures,
        )

        # Verify subsequent save with empty password keeps stored password
        post_keep = run_cgi(
            zoe_addon,
            "POST",
            "settings",
            body=json.dumps(
                {
                    "renault_user": "zoe@example.com",
                    "renault_pass": "",
                    "renault_vin": "VF1AG000012345678",
                }
            ),
        )
        _, keep_data = parse_cgi(post_keep)
        check(
            keep_data.get("renault_pass_set") is True
            and "renault-secret-42" in renault_env_file.read_text(),
            "empty renault_pass preserves stored password in renault.env",
            failures,
        )

        # 23c. Full 4-step auth chain + telemetry parse (chargingStatus=1.0 -> LÄDT / green when UI_LANGUAGE=de)
        (zoe_addon / "config" / "config.env").write_text('UI_LANGUAGE="de"\n')
        (zoe_addon / "config" / "config.env").chmod(0o600)
        zoe_env = os.environ.copy()
        zoe_env["RENAULT_GIGYA_URL"] = f"http://127.0.0.1:{renault_port}"
        zoe_env["RENAULT_KAMEREON_URL"] = f"http://127.0.0.1:{renault_port}"
        renault_state.reset_requests()
        renault_state.renault_battery_level = 78
        renault_state.renault_autonomy = 214
        renault_state.renault_plug_status = 1
        renault_state.renault_charging_status = 1.0

        zoe_res = subprocess.run(
            [str(zoe_script)], env=zoe_env, capture_output=True, text=True, timeout=20
        )
        check(zoe_res.returncode == 0, f"Renault plugin exits 0 (got {zoe_res.returncode})", failures)
        check(renault_state.renault_login_calls == 1, "Gigya accounts.login called once", failures)
        check(renault_state.renault_jwt_calls == 1, "Gigya accounts.getJWT called once", failures)
        check(renault_state.renault_person_calls == 1, "Kamereon persons called once", failures)
        check(renault_state.renault_battery_calls == 1, "Kamereon battery-status called once", failures)
        check(zoe_session_path.is_file(), ".renault_session cache file written", failures)
        check(
            (zoe_session_path.stat().st_mode & 0o777) == 0o600,
            ".renault_session has mode 600",
            failures,
        )
        session_text = zoe_session_path.read_text()
        check(
            "ID_TOKEN=" in session_text and "ACCOUNT_ID=" in session_text and "TOKEN_TS=" in session_text,
            ".renault_session stores ID_TOKEN, ACCOUNT_ID, TOKEN_TS",
            failures,
        )
        check(zoe_card_path.is_file(), "renault_zoe.json card written", failures)
        zoe_card = json.loads(zoe_card_path.read_text()) if zoe_card_path.is_file() else {}
        check(zoe_card.get("layout") == "stat", "Renault card layout is stat", failures)
        check(zoe_card.get("status_badge") == "LÄDT", "chargingStatus 1.0 -> badge LÄDT", failures)
        check(zoe_card.get("status_color") == "green", "chargingStatus 1.0 -> color green", failures)
        check(
            zoe_card.get("primary") == {"value": 78, "unit": "%", "label": "BATTERIE SOC"},
            f"primary stat matches expected structure (got {zoe_card.get('primary')})",
            failures,
        )
        check(
            zoe_card.get("secondary_metrics")
            == [
                {"label": "REICHWEITE", "value": "214 km"},
                {"label": "STECKER", "value": "Gesteckt"},
                {"label": "KILOMETERSTAND", "value": "45.210 km"},
            ],
            f"secondary_metrics match expected values (got {zoe_card.get('secondary_metrics')})",
            failures,
        )

        # 23d. Cache hit (< 6h): reuses ID_TOKEN and ACCOUNT_ID without re-authenticating
        renault_state.reset_requests()
        renault_state.renault_battery_level = 82
        renault_state.renault_autonomy = 225
        renault_state.renault_plug_status = 1
        renault_state.renault_charging_status = 0.0
        zoe_res2 = subprocess.run(
            [str(zoe_script)], env=zoe_env, capture_output=True, text=True, timeout=20
        )
        check(zoe_res2.returncode == 0, "cached Renault poll exits 0", failures)
        check(
            renault_state.renault_login_calls == 0
            and renault_state.renault_jwt_calls == 0
            and renault_state.renault_person_calls == 0,
            "cached poll skips Gigya and persons auth calls",
            failures,
        )
        check(renault_state.renault_battery_calls == 1, "cached poll queries battery-status", failures)
        zoe_card2 = json.loads(zoe_card_path.read_text())
        check(
            zoe_card2.get("status_badge") == "VERBUNDEN"
            and zoe_card2.get("status_color") == "blue",
            "plugStatus 1 & chargingStatus 0.0 -> VERBUNDEN / blue",
            failures,
        )

        # 23e. HTTP 401 on cached token triggers automatic re-auth and retry; unplugged -> BEREIT / neutral
        renault_state.reset_requests()
        renault_state.renault_force_401_once = True
        renault_state.renault_battery_level = 65
        renault_state.renault_autonomy = 180
        renault_state.renault_plug_status = 0
        renault_state.renault_charging_status = -1.0
        zoe_res3 = subprocess.run(
            [str(zoe_script)], env=zoe_env, capture_output=True, text=True, timeout=20
        )
        check(zoe_res3.returncode == 0, "401 recovery poll exits 0", failures)
        check(renault_state.renault_login_calls == 1, "401 triggers fresh Gigya login", failures)
        check(renault_state.renault_battery_calls == 2, "battery-status retried after 401", failures)
        zoe_card3 = json.loads(zoe_card_path.read_text())
        check(
            zoe_card3.get("status_badge") == "BEREIT"
            and zoe_card3.get("status_color") == "neutral"
            and zoe_card3.get("secondary_metrics")
            == [
                {"label": "REICHWEITE", "value": "180 km"},
                {"label": "STECKER", "value": "Getrennt"},
                {"label": "KILOMETERSTAND", "value": "45.210 km"},
            ],
            "unplugged Zoe card reports BEREIT / neutral / Getrennt",
            failures,
        )

        # 23f. test_renault.cgi connection test endpoint
        test_rn_env = os.environ.copy()
        test_rn_env.update(
            {
                "REQUEST_METHOD": "POST",
                "SCRIPT_NAME": "/addons/sentinella/api/test_renault.cgi",
                "PATH_INFO": "",
                "CONTENT_LENGTH": "2",
                "CONTENT_TYPE": "application/json",
                "SMA_MONITOR_ADDON_DIR": str(zoe_addon),
                "SMA_MONITOR_ENV": str(zoe_addon / ".env"),
                "RENAULT_GIGYA_URL": f"http://127.0.0.1:{renault_port}",
                "RENAULT_KAMEREON_URL": f"http://127.0.0.1:{renault_port}",
            }
        )
        test_rn_res = subprocess.run(
            ["sh", str(ROOT / "web" / "api" / "test_renault.cgi")],
            input="{}",
            env=test_rn_env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        rn_test_status, rn_test_body = parse_cgi(test_rn_res)
        check(
            rn_test_status == "200 OK" and rn_test_body.get("status") == "connected",
            f"test_renault.cgi verifies live connection (got {rn_test_status}: {rn_test_body})",
            failures,
        )

        # 23g. WebUI & deploy.sh checks for Renault Zoe
        deploy_text = (ROOT / "deploy.sh").read_text()
        check(
            "plugins.d/20_renault_zoe.sh" in deploy_text
            and 'install -m 755 "$ROOT/plugins.d/20_renault_zoe.sh"' in deploy_text,
            "deploy.sh stages 20_renault_zoe.sh with mode 755",
            failures,
        )
        html_text = (ROOT / "web" / "index.html").read_text()
        app_text = (ROOT / "web" / "app.js").read_text()
        check(
            'id="renault-user"' in html_text
            and 'id="renault-pass"' in html_text
            and 'id="renault-vin"' in html_text
            and 'id="renault-test"' in html_text,
            "web/index.html includes Renault Zoe settings fields and test button",
            failures,
        )
        check(
            "test_renault.cgi" in app_text
            and "settings.cgi" in app_text
            and "••••••••" in app_text,
            "web/app.js wires settings.cgi, test_renault.cgi, and masked password placeholder",
            failures,
        )
        print("\n24. security hardening (SEN-01..SEN-11)")

        sec_addon = make_addon(
            tmp, sma_port, rega_port, signal_port, False, name="sec-addon"
        )
        sec_env_path = sec_addon / ".env"

        def cgi_sec(method, action, body="", extra=None):
            env = os.environ.copy()
            for key in ("HTTP_ORIGIN", "HTTP_REFERER", "HTTP_HOST",
                        "HTTP_X_REQUESTED_WITH"):
                env.pop(key, None)
            env.update(
                {
                    "REQUEST_METHOD": method,
                    "SCRIPT_NAME": f"/addons/sentinella/api/{action}.cgi",
                    "PATH_INFO": "",
                    "CONTENT_LENGTH": str(len(body.encode())),
                    "CONTENT_TYPE": "application/json",
                    "SMA_MONITOR_ADDON_DIR": str(sec_addon),
                    "SMA_MONITOR_ENV": str(sec_env_path),
                }
            )
            env.update(extra or {})
            return subprocess.run(
                ["sh", str(ROOT / "web" / "api" / f"{action}.cgi")],
                input=body,
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
            )

        # 24a. CSRF / Origin / Content-Type enforcement
        original_env = sec_env_path.read_text()
        r_bad_origin = cgi_sec(
            "POST", "config", json.dumps({"trigger_soc": 80}),
            {"HTTP_HOST": "ccu.local", "HTTP_ORIGIN": "http://evil.example"},
        )
        st, _ = parse_cgi(r_bad_origin)
        check(st == "403 Forbidden", f"cross-origin POST rejected (got {st})", failures)
        r_bad_ref = cgi_sec(
            "POST", "config", json.dumps({"trigger_soc": 80}),
            {"HTTP_HOST": "ccu.local", "HTTP_REFERER": "http://evil.example/x"},
        )
        st, _ = parse_cgi(r_bad_ref)
        check(st == "403 Forbidden", f"cross-origin Referer rejected (got {st})", failures)
        r_bad_type = cgi_sec(
            "POST", "config", json.dumps({"trigger_soc": 80}),
            {"CONTENT_TYPE": "text/plain"},
        )
        st, _ = parse_cgi(r_bad_type)
        check(st == "400 Bad Request", f"non-JSON POST rejected (got {st})", failures)
        check(
            sec_env_path.read_text() == original_env,
            "rejected requests leave .env untouched",
            failures,
        )
        r_good_origin = cgi_sec(
            "POST", "config", json.dumps({"ui_language": "de"}),
            {"HTTP_HOST": "ccu.local", "HTTP_ORIGIN": "http://ccu.local"},
        )
        st, _ = parse_cgi(r_good_origin)
        check(st == "200 OK", f"same-origin POST accepted (got {st})", failures)
        check(
            'UI_LANGUAGE=\'de\'' in sec_env_path.read_text(),
            "same-origin POST persisted the change",
            failures,
        )
        r_custom_header = cgi_sec(
            "POST", "config", json.dumps({"ui_language": "en"}),
            {"HTTP_HOST": "ccu.local", "HTTP_X_REQUESTED_WITH": "XMLHttpRequest"},
        )
        st, _ = parse_cgi(r_custom_header)
        check(st == "200 OK", f"X-Requested-With POST accepted (got {st})", failures)

        # 24b. Flat JSON parsing: commas in values and key spoofing
        r_comma = cgi_sec(
            "POST", "config", json.dumps({"sma_password": "pa,ss"}),
            {"HTTP_HOST": "ccu.local", "HTTP_ORIGIN": "http://ccu.local"},
        )
        st, _ = parse_cgi(r_comma)
        check(st == "200 OK", f"password containing a comma accepted (got {st})", failures)
        check(
            "SMA_PASS='pa,ss'" in sec_env_path.read_text(),
            "comma inside a JSON string is preserved",
            failures,
        )
        cgi_sec(
            "POST", "config", json.dumps({"sma_password": "xxui_language"}),
            {"HTTP_HOST": "ccu.local", "HTTP_ORIGIN": "http://ccu.local"},
        )
        check(
            'UI_LANGUAGE=\'en\'' in sec_env_path.read_text(),
            "key name embedded in a value does not spoof another key",
            failures,
        )
        pwn_path = tmp / "sen24-pwned"
        cgi_sec(
            "POST", "config", json.dumps({"sma_password": f"$(touch {pwn_path})"}),
            {"HTTP_HOST": "ccu.local", "HTTP_ORIGIN": "http://ccu.local"},
        )
        sourced_password = subprocess.run(
            ["sh", "-c", '. "$1"; printf %s "$SMA_PASS"', "sh", str(sec_env_path)],
            capture_output=True, text=True, timeout=10,
        ).stdout
        check(
            not pwn_path.exists() and sourced_password == f"$(touch {pwn_path})",
            "shell metacharacters in a password stay literal after sourcing",
            failures,
        )

        # 24c. plugins.cgi method, symlink and envelope guards
        r_plugin_post = cgi_sec("POST", "plugins", "{}")
        st, _ = parse_cgi(r_plugin_post)
        check(st == "405 Method Not Allowed", f"plugins.cgi rejects POST (got {st})", failures)
        sec_plugins = sec_addon / "state" / "plugins"
        sec_plugins.mkdir(parents=True, exist_ok=True)
        os.symlink(sec_env_path, sec_plugins / "leak.json")
        (sec_plugins / "bad.json").write_text("SMA_PASS='leaked'\n")
        (sec_plugins / "ok.json").write_text('{"id":"ok"}\n')
        r_plugin_get = cgi_sec("GET", "plugins")
        check(
            "test-secret" not in r_plugin_get.stdout
            and "SMA_PASS='leaked'" not in r_plugin_get.stdout
            and '"id":"ok"' in r_plugin_get.stdout,
            "plugins.cgi skips symlinks and non-JSON files",
            failures,
        )

        # 24d. secrets never in curl argv; private log/history modes
        check_soc_text = (ROOT / "bin" / "check_soc.sh").read_text()
        notify_text = (ROOT / "lib" / "notify_signal.sh").read_text()
        renault_text = (ROOT / "plugins.d" / "20_renault_zoe.sh").read_text()
        forecast_text = (ROOT / "plugins.d" / "30_forecast_solar.sh").read_text()
        check(
            '--data-binary @-' in check_soc_text.split("sma_login()", 1)[1]
            .split("sma_logout()", 1)[0]
            and '--data-binary "$_body"' not in check_soc_text.split("sma_login()", 1)[1]
            .split("sma_logout()", 1)[0],
            "SMA_PASS is streamed to curl via stdin",
            failures,
        )
        check(
            '--config -' in notify_text
            and '--data-urlencode "apikey=' not in notify_text,
            "Signal phone/apikey do not appear in curl argv",
            failures,
        )
        check(
            '--config -' in renault_text
            and '--data-urlencode "password=' not in renault_text
            and '-H "x-gigya-id_token:' not in renault_text,
            "Renault password and token do not appear in curl argv",
            failures,
        )
        check(
            "/tmp/sentinella_" not in renault_text
            and "/tmp/sentinella_" not in forecast_text,
            "plugins no longer use predictable /tmp scratch files",
            failures,
        )
        reset_state(sec_addon)
        write_config(sec_addon, sma_port, rega_port, signal_port, False)
        run_check(sec_addon)
        sec_log = sec_addon / "state" / "test.log"
        sec_hist = sec_addon / "state" / "history.csv"
        check(
            sec_log.is_file() and (sec_log.stat().st_mode & 0o777) == 0o600,
            "check_soc.log is mode 600",
            failures,
        )
        check(
            sec_hist.is_file() and (sec_hist.stat().st_mode & 0o777) == 0o600,
            "history.csv is mode 600",
            failures,
        )

        # 24e. Renault VIN / session path traversal
        trav_addon = tmp / "trav-addon"
        (trav_addon / "plugins.d").mkdir(parents=True)
        (trav_addon / "config" / "plugins").mkdir(parents=True)
        (trav_addon / "state" / "plugins").mkdir(parents=True)
        trav_script = trav_addon / "plugins.d" / "20_renault_zoe.sh"
        shutil.copy2(ROOT / "plugins.d" / "20_renault_zoe.sh", trav_script)
        trav_script.chmod(0o755)
        trav_env_file = trav_addon / "config" / "plugins" / "renault.env"
        trav_env_file.write_text(
            "RENAULT_USER='zoe@example.com'\n"
            "RENAULT_PASS='renault-secret-42'\n"
            "RENAULT_VIN='../../etc/passwd'\n"
        )
        trav_env_file.chmod(0o600)
        trav_env = os.environ.copy()
        trav_env["RENAULT_GIGYA_URL"] = f"http://127.0.0.1:{renault_port}"
        trav_env["RENAULT_KAMEREON_URL"] = f"http://127.0.0.1:{renault_port}"
        renault_state.reset_requests()
        trav_res = subprocess.run(
            [str(trav_script)], env=trav_env, capture_output=True, text=True, timeout=20
        )
        check(
            trav_res.returncode == 0
            and not (trav_addon / "state" / "plugins" / "renault_zoe.json").exists()
            and renault_state.renault_login_calls == 0,
            "invalid RENAULT_VIN is rejected before any request",
            failures,
        )
        trav_env_file.write_text(
            "RENAULT_USER='zoe@example.com'\n"
            "RENAULT_PASS='renault-secret-42'\n"
            "RENAULT_VIN='VF1AG000012345678'\n"
        )
        trav_env_file.chmod(0o600)
        trav_session = trav_addon / "state" / "plugins" / ".renault_session"
        trav_session.write_text(
            "ID_TOKEN='jwt-id-token-valid-999'\n"
            "ACCOUNT_ID='../../pwn'\n"
            f"TOKEN_TS='{int(time.time())}'\n"
        )
        trav_session.chmod(0o600)
        renault_state.reset_requests()
        trav_res2 = subprocess.run(
            [str(trav_script)], env=trav_env, capture_output=True, text=True, timeout=20
        )
        check(
            trav_res2.returncode == 0
            and renault_state.renault_login_calls >= 1,
            "poisoned session ACCOUNT_ID is rejected and re-authentication happens",
            failures,
        )

        # 24f. frontend defence in depth
        app_sec = (ROOT / "web" / "app.js").read_text()
        html_sec = (ROOT / "web" / "index.html").read_text()
        check(
            '"X-Requested-With": "XMLHttpRequest"' in app_sec,
            "app.js sends X-Requested-With on API calls",
            failures,
        )
        check(
            'rawLayout === "delta"' in app_sec
            and 'rawColor === "green"' in app_sec
            and 'rawDir === "up"' in app_sec,
            "plugin layout/color/direction values are allowlisted",
            failures,
        )
        check(
            'http-equiv="Content-Security-Policy"' in html_sec,
            "index.html ships a Content Security Policy",
            failures,
        )

        print("\n25. Plugin i18n (DE / EN), charging pulse animation, benchmark & CONTRIBUTING.md")
        # 25a. Renault Zoe plugin i18n: UI_LANGUAGE=en vs UI_LANGUAGE=de in config/config.env
        (zoe_addon / "config" / "config.env").write_text('UI_LANGUAGE="en"\n')
        (zoe_addon / "config" / "config.env").chmod(0o600)
        renault_state.reset_requests()
        renault_state.renault_battery_level = 84
        renault_state.renault_autonomy = 230
        renault_state.renault_plug_status = 1
        renault_state.renault_charging_status = 1.0
        zoe_en_res = subprocess.run(
            [str(zoe_script)], env=zoe_env, capture_output=True, text=True, timeout=20
        )
        check(zoe_en_res.returncode == 0, "Renault Zoe EN run exits 0", failures)
        zoe_en_card = json.loads(zoe_card_path.read_text())
        check(zoe_en_card.get("title") == "RENAULT ZOE", "Renault EN title is RENAULT ZOE", failures)
        check(zoe_en_card.get("status_badge") == "CHARGING", "Renault EN charging badge is CHARGING", failures)
        check(
            zoe_en_card.get("primary") == {"value": 84, "unit": "%", "label": "BATTERY SOC"},
            f"Renault EN primary is BATTERY SOC (got {zoe_en_card.get('primary')})",
            failures,
        )
        check(
            zoe_en_card.get("secondary_metrics")
            == [
                {"label": "RANGE", "value": "230 km"},
                {"label": "CONNECTOR", "value": "Connected"},
                {"label": "ODOMETER", "value": "45,210 km"},
            ],
            f"Renault EN secondary metrics match RANGE / CONNECTOR / Connected (got {zoe_en_card.get('secondary_metrics')})",
            failures,
        )

        # Renault Zoe EN: CONNECTED & READY states
        renault_state.renault_plug_status = 1
        renault_state.renault_charging_status = 0.0
        subprocess.run([str(zoe_script)], env=zoe_env, capture_output=True, text=True, timeout=20)
        zoe_en_conn = json.loads(zoe_card_path.read_text())
        check(
            zoe_en_conn.get("status_badge") == "CONNECTED"
            and zoe_en_conn.get("secondary_metrics")[1] == {"label": "CONNECTOR", "value": "Connected"},
            "Renault EN plugged idle -> CONNECTED / Connected",
            failures,
        )

        renault_state.renault_plug_status = 0
        renault_state.renault_charging_status = 0.0
        subprocess.run([str(zoe_script)], env=zoe_env, capture_output=True, text=True, timeout=20)
        zoe_en_ready = json.loads(zoe_card_path.read_text())
        check(
            zoe_en_ready.get("status_badge") == "READY"
            and zoe_en_ready.get("secondary_metrics")[1] == {"label": "CONNECTOR", "value": "Disconnected"},
            "Renault EN unplugged -> READY / Disconnected",
            failures,
        )

        # Switch back to DE via config/config.env and verify DE outputs
        (zoe_addon / "config" / "config.env").write_text('UI_LANGUAGE="de"\n')
        renault_state.renault_plug_status = 1
        renault_state.renault_charging_status = 1.0
        subprocess.run([str(zoe_script)], env=zoe_env, capture_output=True, text=True, timeout=20)
        zoe_de_card = json.loads(zoe_card_path.read_text())
        check(
            zoe_de_card.get("title") == "RENAULT ZOE"
            and zoe_de_card.get("status_badge") == "LÄDT"
            and zoe_de_card.get("primary", {}).get("label") == "BATTERIE SOC"
            and zoe_de_card.get("secondary_metrics")
            == [
                {"label": "REICHWEITE", "value": "230 km"},
                {"label": "STECKER", "value": "Gesteckt"},
                {"label": "KILOMETERSTAND", "value": "45.210 km"},
            ],
            "Renault DE card matches BATTERIE SOC / REICHWEITE / STECKER / LÄDT / Gesteckt",
            failures,
        )

        # 25b. forecast.solar plugin i18n: UI_LANGUAGE=en vs UI_LANGUAGE=de in config/config.env
        (fc_addon / "config" / "config.env").write_text('UI_LANGUAGE="en"\n')
        (fc_addon / "config" / "config.env").chmod(0o600)
        fc_en_res = subprocess.run(
            [str(fc_script)], env=fc_env, capture_output=True, text=True, timeout=20
        )
        check(fc_en_res.returncode == 0, "forecast.solar EN run exits 0", failures)
        fc_en_card = json.loads(fc_json_path.read_text())
        check(fc_en_card.get("title") == "SOLAR FORECAST", "forecast EN title is SOLAR FORECAST", failures)
        check(
            fc_en_card.get("status_badge") in {"SUNNY", "MODERATE", "CLOUDY"},
            f"forecast EN badge is localized (got {fc_en_card.get('status_badge')!r})",
            failures,
        )
        check(
            fc_en_card.get("primary", {}).get("label") == "TODAY FORECAST",
            f"forecast EN primary label is TODAY FORECAST (got {fc_en_card.get('primary')})",
            failures,
        )
        check(
            fc_en_card.get("delta", {}).get("comparison") == "vs yesterday",
            f"forecast EN comparison is 'vs yesterday' (got {fc_en_card.get('delta')})",
            failures,
        )
        fc_en_labels = [m.get("label") for m in fc_en_card.get("secondary_metrics", [])]
        check(
            fc_en_labels == ["TOMORROW", "PEAK"],
            f"forecast EN secondary metrics are TOMORROW / PEAK (got {fc_en_labels})",
            failures,
        )

        # Switch forecast_solar to DE via config/config.env
        (fc_addon / "config" / "config.env").write_text('UI_LANGUAGE="de"\n')
        subprocess.run([str(fc_script)], env=fc_env, capture_output=True, text=True, timeout=20)
        fc_de_card = json.loads(fc_json_path.read_text())
        fc_de_labels = [m.get("label") for m in fc_de_card.get("secondary_metrics", [])]
        check(
            fc_de_card.get("title") == "PV-PROGNOSE"
            and fc_de_card.get("status_badge") in {"SONNIG", "MODERAT", "BEWÖLKT"}
            and fc_de_card.get("primary", {}).get("label") == "PROGNOSE HEUTE"
            and fc_de_card.get("delta", {}).get("comparison") == "von gestern"
            and fc_de_labels == ["MORGEN", "SPITZE"],
            "forecast DE card matches PV-PROGNOSE / PROGNOSE HEUTE / von gestern / MORGEN / SPITZE",
            failures,
        )

        # 25c. CSS pulse-charge animation & JS .is-charging + frontend plugin i18n dictionary
        style_css = (ROOT / "web" / "style.css").read_text()
        check("@keyframes pulse-charge" in style_css, "style.css defines @keyframes pulse-charge", failures)
        check("will-change: opacity" in style_css, "style.css uses will-change: opacity", failures)
        check("is-charging" in style_css, "style.css defines .is-charging rules", failures)

        if shutil.which("node"):
            pulse_i18n_js = r"""
const fs = require("fs");
const code = fs.readFileSync(process.argv[1], "utf8");
const pluginI18nMatch = code.match(/var PLUGIN_I18N = \{[\s\S]*?\n    \};/);
const translateMatch = code.match(/function translatePluginText\([\s\S]*?\n    \}/);
const chargingMatch = code.match(/function isTelemetryCharging\([\s\S]*?\n    \}/);
const escapeHtmlMatch = code.match(/function escapeHtml\([\s\S]*?\n    \}/);
const secondaryMetricsMatch = code.match(/function renderSecondaryMetrics\([\s\S]*?\n    \}/);
const renderPluginCardMatch = code.match(/function renderPluginCard\([\s\S]*?\n    \}/);
if (!pluginI18nMatch || !translateMatch || !chargingMatch || !renderPluginCardMatch) {
    console.error("Missing required functions in web/app.js");
    process.exit(1);
}
const runner = new Function(
    "var currentLanguage = 'en';\n" +
    "const document = { createElement: (t) => ({ tagName: t, className: '', innerHTML: '', attrs: {}, setAttribute: function(k, v) { this.attrs[k] = v; } }) };\n" +
    pluginI18nMatch[0] + "\n" +
    translateMatch[0] + "\n" +
    chargingMatch[0] + "\n" +
    escapeHtmlMatch[0] + "\n" +
    secondaryMetricsMatch[0] + "\n" +
    renderPluginCardMatch[0] + "\n" +
    "return { setLang: (l) => { currentLanguage = l; }, translatePluginText, isTelemetryCharging, renderPluginCard };"
)();

const pairs = [
    ["BATTERIE SOC", "BATTERY SOC"],
    ["REICHWEITE", "RANGE"],
    ["STECKER", "CONNECTOR"],
    ["LÄDT", "CHARGING"],
    ["VERBUNDEN", "CONNECTED"],
    ["BEREIT", "READY"],
    ["Gesteckt", "Connected"],
    ["Getrennt", "Disconnected"],
    ["PV-PROGNOSE", "SOLAR FORECAST"],
    ["PROGNOSE HEUTE", "TODAY FORECAST"],
    ["von gestern", "vs yesterday"],
    ["SONNIG", "SUNNY"],
    ["MODERAT", "MODERATE"],
    ["BEWÖLKT", "CLOUDY"],
    ["MORGEN", "TOMORROW"],
    ["SPITZE", "PEAK"]
];
for (const [deStr, enStr] of pairs) {
    if (runner.translatePluginText(deStr, "en") !== enStr) {
        throw new Error("DE->EN failed for " + deStr);
    }
    if (runner.translatePluginText(enStr, "de") !== deStr) {
        throw new Error("EN->DE failed for " + enStr);
    }
}

if (!runner.isTelemetryCharging({ charging: "charging" })) throw new Error("charging string not detected");
if (!runner.isTelemetryCharging({ charging: true })) throw new Error("charging boolean not detected");
if (!runner.isTelemetryCharging({ chargingStatus: 1 })) throw new Error("chargingStatus 1 not detected");
if (!runner.isTelemetryCharging({ status_badge: "LÄDT" })) throw new Error("status_badge LÄDT not detected");
if (!runner.isTelemetryCharging({ status_badge: "CHARGING" })) throw new Error("status_badge CHARGING not detected");
if (runner.isTelemetryCharging({ charging: "discharging", chargingStatus: 0, status_badge: "BEREIT" })) {
    throw new Error("discharging/idle wrongly marked as charging");
}
if (runner.isTelemetryCharging({ charging: "idle" })) {
    throw new Error("idle wrongly marked as charging");
}

// Verify dynamic card translation DE -> EN and EN -> DE
runner.setLang("en");
const cardEn = runner.renderPluginCard({
    layout: "stat",
    title: "RENAULT ZOE",
    status_badge: "LÄDT",
    status_color: "green",
    primary: { value: 78, unit: "%", label: "BATTERIE SOC" },
    secondary_metrics: [
        { label: "REICHWEITE", value: "214 km" },
        { label: "STECKER", value: "Gesteckt" }
    ]
});
if (!cardEn.innerHTML.includes("CHARGING") || !cardEn.innerHTML.includes("BATTERY SOC") ||
    !cardEn.innerHTML.includes("RANGE") || !cardEn.innerHTML.includes("CONNECTOR") ||
    !cardEn.innerHTML.includes("Connected") || !cardEn.innerHTML.includes("is-charging")) {
    throw new Error("EN translation or .is-charging failed: " + cardEn.innerHTML);
}

runner.setLang("de");
const cardDe = runner.renderPluginCard({
    layout: "delta",
    title: "SOLAR FORECAST",
    status_badge: "SUNNY",
    status_color: "green",
    primary: { value: 18.4, unit: "kWh", label: "TODAY FORECAST" },
    delta: { direction: "up", value: "+3.2 kWh", comparison: "vs yesterday" },
    secondary_metrics: [
        { label: "TOMORROW", value: "15.2", unit: "kWh" },
        { label: "PEAK", value: "1.84", unit: "kWh/kWp" }
    ]
});
if (!cardDe.innerHTML.includes("PV-PROGNOSE") || !cardDe.innerHTML.includes("SONNIG") ||
    !cardDe.innerHTML.includes("von gestern") || !cardDe.innerHTML.includes("MORGEN") ||
    !cardDe.innerHTML.includes("SPITZE")) {
    throw new Error("DE translation failed: " + cardDe.innerHTML);
}
"""
            pulse_res = subprocess.run(
                ["node", "-e", pulse_i18n_js, str(ROOT / "web" / "app.js")],
                capture_output=True,
                text=True,
                timeout=10,
            )
            check(
                pulse_res.returncode == 0,
                f"isTelemetryCharging and dynamic plugin i18n dictionary pass ({pulse_res.stderr.strip() or 'ok'})",
                failures,
            )

        # 25d. tools/benchmark_footprint.sh execution and budget guard
        bench_script = ROOT / "tools" / "benchmark_footprint.sh"
        check(bench_script.is_file(), "tools/benchmark_footprint.sh exists", failures)
        check(os.access(bench_script, os.X_OK), "tools/benchmark_footprint.sh is executable", failures)
        bench_syntax = subprocess.run(
            ["sh", "-n", str(bench_script)], capture_output=True, text=True, timeout=10
        )
        check(bench_syntax.returncode == 0, "benchmark_footprint.sh passes sh -n", failures)
        bench_run = subprocess.run(
            [str(bench_script)], capture_output=True, text=True, timeout=60
        )
        check(
            bench_run.returncode == 0,
            f"benchmark_footprint.sh passes budget guards (exit {bench_run.returncode})",
            failures,
        )
        check(
            "| Release Tarball Package Size |" in bench_run.stdout
            and "| Installed Add-on Footprint |" in bench_run.stdout
            and "bin/check_soc.sh --dry-run" in bench_run.stdout
            and "plugins.d/20_renault_zoe.sh" in bench_run.stdout,
            "benchmark_footprint.sh prints Markdown table with all required rows",
            failures,
        )

        # 25e. CONTRIBUTING.md completeness check
        contrib_path = ROOT / "CONTRIBUTING.md"
        check(contrib_path.is_file(), "CONTRIBUTING.md exists", failures)
        contrib_text = contrib_path.read_text() if contrib_path.is_file() else ""
        for marker in (
            "BusyBox",
            "/state/plugins/*.json",
            "stat",
            "delta",
            "list",
            "--config -",
            "chmod 600",
            "umask 077",
            "is_safe_id",
            "python3 tests/integration.py",
        ):
            check(
                marker in contrib_text,
                f"CONTRIBUTING.md documents {marker}",
                failures,
            )

        print("\n26. Renault Zoe Remote HVAC Pre-Conditioning (--start-hvac & action_zoe.cgi)")
        # 26a. CLI --start-hvac reuses cached session and sends Kamereon HvacStart payload
        renault_state.reset_requests()
        hvac_cli_res = subprocess.run(
            [str(zoe_script), "--start-hvac"],
            env=zoe_env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        check(hvac_cli_res.returncode == 0, "20_renault_zoe.sh --start-hvac exits 0", failures)
        try:
            hvac_cli_json = json.loads(hvac_cli_res.stdout.strip())
        except json.JSONDecodeError:
            hvac_cli_json = {}
        check(
            hvac_cli_json == {"ok": True, "action": "hvac_started"},
            f"--start-hvac returns {{'ok':true,'action':'hvac_started'}} (got {hvac_cli_res.stdout.strip()!r})",
            failures,
        )
        check(
            renault_state.renault_login_calls == 0 and renault_state.renault_hvac_calls == 1,
            "--start-hvac reuses cached session token without re-login",
            failures,
        )

        # 26b. CLI --start-hvac refreshes expired session on 401
        renault_state.reset_requests()
        renault_state.renault_force_401_once = True
        hvac_401_res = subprocess.run(
            [str(zoe_script), "--start-hvac"],
            env=zoe_env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        check(
            hvac_401_res.returncode == 0
            and renault_state.renault_login_calls == 1
            and renault_state.renault_hvac_calls == 2,
            "--start-hvac refreshes session on HTTP 401 and succeeds on retry",
            failures,
        )

        # 26c. action_zoe.cgi security (GET rejected, CSRF check, rate limiting, and execution)
        hvac_lock = zoe_addon / "state" / ".zoe_hvac.lock"
        hvac_lock.unlink(missing_ok=True)

        def call_action_zoe(method="POST", body='{"action":"start_hvac"}', extra_env=None):
            env = os.environ.copy()
            for k in ("HTTP_ORIGIN", "HTTP_REFERER", "HTTP_HOST", "HTTP_X_REQUESTED_WITH"):
                env.pop(k, None)
            env.update(
                {
                    "REQUEST_METHOD": method,
                    "SCRIPT_NAME": "/addons/sentinella/api/action_zoe.cgi",
                    "PATH_INFO": "",
                    "CONTENT_LENGTH": str(len(body.encode())),
                    "CONTENT_TYPE": "application/json",
                    "HTTP_HOST": "ccu.local",
                    "HTTP_ORIGIN": "http://ccu.local",
                    "SMA_MONITOR_ADDON_DIR": str(zoe_addon),
                    "SMA_MONITOR_ENV": str(zoe_addon / ".env"),
                    "RENAULT_GIGYA_URL": f"http://127.0.0.1:{renault_port}",
                    "RENAULT_KAMEREON_URL": f"http://127.0.0.1:{renault_port}",
                }
            )
            if extra_env:
                env.update(extra_env)
            return subprocess.run(
                ["sh", str(ROOT / "web" / "api" / "action_zoe.cgi")],
                input=body,
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
            )

        st_get, _ = parse_cgi(call_action_zoe(method="GET", body=""))
        check(st_get == "405 Method Not Allowed", f"action_zoe.cgi rejects GET with 405 (got {st_get})", failures)

        st_csrf, _ = parse_cgi(
            call_action_zoe(extra_env={"HTTP_ORIGIN": "http://attacker.example"})
        )
        check(st_csrf == "403 Forbidden", f"action_zoe.cgi rejects cross-origin POST with 403 (got {st_csrf})", failures)

        renault_state.reset_requests()
        st_ok, body_ok = parse_cgi(call_action_zoe())
        check(
            st_ok == "200 OK"
            and body_ok.get("ok") is True
            and body_ok.get("action") == "hvac_started"
            and renault_state.renault_hvac_calls == 1,
            f"action_zoe.cgi triggers HVAC and returns 200 OK (got {st_ok}: {body_ok})",
            failures,
        )
        check(
            hvac_lock.is_file() and (hvac_lock.stat().st_mode & 0o777) == 0o600,
            ".zoe_hvac.lock created with mode 600",
            failures,
        )

        # Second call within 60s must be rate-limited (429 Too Many Requests)
        st_rl, body_rl = parse_cgi(call_action_zoe())
        check(
            st_rl == "429 Too Many Requests"
            and body_rl.get("ok") is False
            and renault_state.renault_hvac_calls == 1,
            f"action_zoe.cgi rate-limits rapid repeat calls within 60s (got {st_rl})",
            failures,
        )

        # 26c2. Odometer fallback, hvac_active, --stop-hvac and CGI stop routing
        zoe_dir_state = zoe_addon / "state" / "plugins"
        hvac_marker = zoe_dir_state / ".renault_hvac_active"
        hvac_stopped_marker = zoe_dir_state / ".renault_hvac_stopped"
        renault_state.renault_mileage = None
        subprocess.run([str(zoe_script)], env=zoe_env, capture_output=True, text=True, timeout=20)
        card_nomile = json.loads(zoe_card_path.read_text())
        check(
            all(m.get("label") != "KILOMETERSTAND" for m in card_nomile.get("secondary_metrics", []))
            and len(card_nomile.get("secondary_metrics", [])) == 2,
            "missing totalMileage omits odometer metric without breaking card",
            failures,
        )
        renault_state.renault_mileage = 45210
        check(
            hvac_marker.is_file(),
            ".renault_hvac_active marker written by --start-hvac",
            failures,
        )
        subprocess.run([str(zoe_script)], env=zoe_env, capture_output=True, text=True, timeout=20)
        card_active = json.loads(zoe_card_path.read_text())
        check(card_active.get("hvac_active") is True, "card exposes hvac_active=true after start", failures)
        check(
            {"label": "KILOMETERSTAND", "value": "45.210 km"} in card_active.get("secondary_metrics", [])
            and renault_state.renault_cockpit_calls >= 1,
            "cockpit totalMileage rendered as KILOMETERSTAND secondary metric",
            failures,
        )

        renault_state.reset_requests()
        stop_cli = subprocess.run(
            [str(zoe_script), "--stop-hvac"], env=zoe_env, capture_output=True, text=True, timeout=20
        )
        check(
            stop_cli.returncode == 0
            and stop_cli.stdout.strip() == '{"ok":true,"action":"hvac_stopped"}',
            f"--stop-hvac returns hvac_stopped (got {stop_cli.stdout.strip()!r})",
            failures,
        )
        check(
            renault_state.renault_hvac_calls == 1
            and '"action":"cancel"' in renault_state.renault_hvac_bodies[-1]
            and not hvac_marker.exists()
            and hvac_stopped_marker.is_file(),
            "--stop-hvac POSTs cancel payload, clears active marker, and writes stopped marker",
            failures,
        )
        check(
            json.loads(zoe_card_path.read_text()).get("hvac_active") is False,
            "--stop-hvac immediately updates renault_zoe.json to hvac_active=false",
            failures,
        )

        # Stale cloud status lag: hvacStatus "on" is suppressed during 180s stop window
        renault_state.renault_hvac_status = "on"
        subprocess.run([str(zoe_script)], env=zoe_env, capture_output=True, text=True, timeout=20)
        check(
            json.loads(zoe_card_path.read_text()).get("hvac_active") is False,
            "--stop-hvac suppresses stale cloud 'on' response within 180s window",
            failures,
        )

        # Once stopped marker is older than 180 seconds, it expires and cloud "on" is accepted again
        old_stop_ts = int(time.time()) - 181
        hvac_stopped_marker.write_text(f"{old_stop_ts}\n")
        subprocess.run([str(zoe_script)], env=zoe_env, capture_output=True, text=True, timeout=20)
        check(
            json.loads(zoe_card_path.read_text()).get("hvac_active") is True,
            "expired stopped marker (>180s) is removed and cloud 'on' status accepted",
            failures,
        )
        check(not hvac_stopped_marker.exists(), "expired stopped marker removed", failures)
        renault_state.renault_hvac_status = None

        # --start-hvac removes stopped marker and sets hvac_active: true immediately
        hvac_stopped_marker.write_text(f"{int(time.time())}\n")
        subprocess.run([str(zoe_script), "--start-hvac"], env=zoe_env, capture_output=True, text=True, timeout=20)
        check(not hvac_stopped_marker.exists(), "--start-hvac removes stopped marker", failures)
        check(
            json.loads(zoe_card_path.read_text()).get("hvac_active") is True,
            "--start-hvac immediately updates renault_zoe.json to hvac_active=true",
            failures,
        )
        subprocess.run([str(zoe_script), "--stop-hvac"], env=zoe_env, capture_output=True, text=True, timeout=20)

        # CGI: start sets lock, stop bypasses cooldown, cancels and clears lock
        renault_state.reset_requests()
        st_start2, body_start2 = parse_cgi(call_action_zoe(body='{"action":"start"}'))
        check(
            st_start2 in ("200 OK", "429 Too Many Requests"),
            f"action_zoe.cgi accepts action=start (got {st_start2})",
            failures,
        )
        hvac_lock.write_text(str(int(time.time())) + "\n")
        renault_state.reset_requests()
        st_stop, body_stop = parse_cgi(call_action_zoe(body='{"action":"stop"}'))
        check(
            st_stop == "200 OK"
            and body_stop.get("action") == "hvac_stopped"
            and renault_state.renault_hvac_calls == 1
            and '"action":"cancel"' in renault_state.renault_hvac_bodies[-1],
            f"action_zoe.cgi action=stop dispatches --stop-hvac despite cooldown (got {st_stop}: {body_stop})",
            failures,
        )
        check(not hvac_lock.exists(), "action=stop clears .zoe_hvac.lock", failures)
        st_bad, _ = parse_cgi(call_action_zoe(body='{"action":"explode"}'))
        check(st_bad == "400 Bad Request", f"action_zoe.cgi rejects unknown action (got {st_bad})", failures)

        # WebUI check for HVAC button & i18n
        app_hvac_text = (ROOT / "web" / "app.js").read_text()
        check(
            "action_zoe.cgi" in app_hvac_text
            and "KLIMATISIERUNG STARTEN" in app_hvac_text
            and "START PRE-CONDITIONING" in app_hvac_text
            and "WIRD GESTARTET..." in app_hvac_text
            and "KLIMA LÄUFT (STOPPEN)" in app_hvac_text
            and "HVAC RUNNING (STOP)" in app_hvac_text
            and "WIRD BEENDET..." in app_hvac_text
            and "STOPPING..." in app_hvac_text
            and "KILOMETERSTAND" in app_hvac_text
            and "ODOMETER" in app_hvac_text,
            "web/app.js includes Zoe HVAC button, endpoint, and DE/EN labels",
            failures,
        )

        # 26d. Production packaging (tools/build_addon.sh) and update_check.cgi
        build_script = ROOT / "tools" / "build_addon.sh"
        check(build_script.is_file() and os.access(build_script, os.X_OK), "tools/build_addon.sh exists and is executable", failures)
        build_syntax = subprocess.run(["sh", "-n", str(build_script)], capture_output=True, text=True, timeout=10)
        check(build_syntax.returncode == 0, "tools/build_addon.sh passes sh -n", failures)

        dist_dir = tmp / "dist"
        build_env = os.environ.copy()
        build_env["SENTINELLA_DIST_DIR"] = str(dist_dir)
        build_res = subprocess.run([str(build_script)], env=build_env, capture_output=True, text=True, timeout=30)
        check(build_res.returncode == 0, f"tools/build_addon.sh builds archive cleanly (exit {build_res.returncode})", failures)

        version_str = (ROOT / "VERSION").read_text().strip()
        tarball_path = dist_dir / f"sentinella-{version_str}.tar.gz"
        check(tarball_path.is_file() and tarball_path.stat().st_size > 10000, f"dist/sentinella-{version_str}.tar.gz created", failures)

        unpack_dir = tmp / "unpacked_addon"
        unpack_dir.mkdir(parents=True, exist_ok=True)
        unpack_res = subprocess.run(
            ["tar", "-xzf", str(tarball_path), "-C", str(unpack_dir)],
            capture_output=True,
            text=True,
            timeout=15,
        )
        check(unpack_res.returncode == 0, "tarball unpacks without errors", failures)
        check(
            (unpack_dir / "update_script").is_file()
            and (unpack_dir / " update_script" if False else (unpack_dir / "update_script")).stat().st_mode & 0o777 == 0o755,
            "update_script is present in archive with mode 755",
            failures,
        )
        check(
            (unpack_dir / "rc.d" / "sentinella").is_file()
            and ((unpack_dir / "rc.d" / "sentinella").stat().st_mode & 0o777) == 0o755,
            "rc.d/sentinella is present in archive with mode 755",
            failures,
        )
        check(
            ((unpack_dir / "web" / "index.html").stat().st_mode & 0o777) == 0o644
            and ((unpack_dir / "web" / "api" / "action_zoe.cgi").stat().st_mode & 0o777) == 0o755
            and ((unpack_dir / "web" / "api" / "update_check.cgi").stat().st_mode & 0o777) == 0o755,
            "archive enforces 644 on static files and 755 on CGI endpoints",
            failures,
        )

        # Verify update_check.cgi returns installed version
        upd_env = os.environ.copy()
        upd_env.update(
            {
                "REQUEST_METHOD": "GET",
                "SCRIPT_NAME": "/addons/sentinella/api/update_check.cgi",
                "SMA_MONITOR_ADDON_DIR": str(ROOT),
                "SENTINELLA_OFFLINE": "1",
            }
        )
        upd_res = subprocess.run(
            ["sh", str(ROOT / "web" / "api" / "update_check.cgi")],
            env=upd_env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        upd_status, upd_body = parse_cgi(upd_res)
        check(
            upd_status == "200 OK"
            and upd_body.get("ok") is True
            and upd_body.get("version") == version_str,
            f"update_check.cgi returns current installed version {version_str} (got {upd_status}: {upd_body})",
            failures,
        )

    finally:
        sma.shutdown()
        rega.shutdown()
        sig.shutdown()
        fc.shutdown()
        rn.shutdown()
        sma.server_close()
        rega.server_close()
        sig.server_close()
        fc.server_close()
        rn.server_close()
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
