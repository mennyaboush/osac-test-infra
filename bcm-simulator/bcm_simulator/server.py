"""HTTP/TLS transport for the BCM simulator.

Two planes:

* **Data plane** — what the operator's BCM client talks to:
  ``GET /rest/v1/version`` and ``POST /json``. Served over TLS with a
  self-signed cert; client certs are NOT required (the "test-mode bypass" —
  the operator connects with ``insecureSkipVerify: true``). Real BCM requires
  mTLS on ``/json``; we intentionally don't, matching the Go integration mock.

* **Control plane** — ``/_admin/*`` endpoints so E2E tests can drive scenarios
  (seed hosts, remove a device, toggle the unreachable/error faults, and read
  back the device map to assert on ``extra_values``). The control plane stays
  available even when the data plane is faulted "down", so a test can recover.
"""

from __future__ import annotations

import contextlib
import json
import logging
import ssl
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .handler import Dispatcher
from .store import DeviceStore

logger = logging.getLogger("bcm_simulator")

_ADMIN_PREFIX = "/_admin/"
_MAX_BODY_BYTES = 8 * 1024 * 1024


class _SimulatorServer(ThreadingHTTPServer):
    """ThreadingHTTPServer carrying the store + dispatcher for its handlers."""

    daemon_threads = True

    def __init__(self, address: tuple[str, int], store: DeviceStore, dispatcher: Dispatcher) -> None:
        self.store = store
        self.dispatcher = dispatcher
        super().__init__(address, _SimulatorHandler)


class _SimulatorHandler(BaseHTTPRequestHandler):
    server: _SimulatorServer  # narrowed type
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        logger.info("%s - %s", self.address_string(), fmt % args)

    # -- routing -----------------------------------------------------------

    def do_GET(self) -> None:
        if self.path.startswith(_ADMIN_PREFIX):
            self._handle_admin_get()
            return
        if self.path == "/rest/v1/version":
            self._write_response(HTTPStatus.OK, self.server.dispatcher.version().encode())
            return
        self._write_response(HTTPStatus.NOT_FOUND, b'{"errormessage": "not found"}')

    def do_POST(self) -> None:
        if self.path.startswith(_ADMIN_PREFIX):
            self._handle_admin_post()
            return

        # Data plane: honour the "down" fault by dropping the connection so the
        # client sees a transport error (ErrConnectionFailed), not an HTTP code.
        if self.server.store.is_down():
            self._drop_connection()
            return

        if self.path == "/json":
            body = self._read_body()
            self._write_response(HTTPStatus.OK, self.server.dispatcher.json_call(body).encode())
            return

        self._write_response(HTTPStatus.NOT_FOUND, b'{"errormessage": "not found"}')

    # -- admin control plane ----------------------------------------------

    def _handle_admin_get(self) -> None:
        if self.path == "/_admin/healthz":
            self._write_json(HTTPStatus.OK, {"status": "ok"})
            return
        if self.path == "/_admin/devices":
            self._write_json(HTTPStatus.OK, self.server.store.get_devices())
            return
        self._write_json(HTTPStatus.NOT_FOUND, {"error": "unknown admin endpoint"})

    def _handle_admin_post(self) -> None:
        store = self.server.store
        payload = self._read_json()
        if payload is None:
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": "invalid JSON body"})
            return

        try:
            if self.path == "/_admin/seed":
                store.seed(
                    devices=payload.get("devices", []) or [],
                    categories=payload.get("categories", []) or [],
                    partitions=payload.get("partitions", []) or [],
                )
            elif self.path == "/_admin/add-lite-node":
                kwargs = {k: payload[k] for k in ("mac", "bmc_address", "bmc_settings") if k in payload}
                store.add_lite_node(payload["hostname"], payload["resource_class"], **kwargs)
            elif self.path == "/_admin/remove-device":
                store.remove_device(payload["hostname"])
            elif self.path == "/_admin/set-down":
                store.set_down(bool(payload.get("down", False)))
            elif self.path == "/_admin/set-fail-call":
                store.set_fail_call(payload.get("call"))
            else:
                self._write_json(HTTPStatus.NOT_FOUND, {"error": "unknown admin endpoint"})
                return
        except (KeyError, ValueError) as exc:
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        self._write_json(HTTPStatus.OK, {"status": "ok"})

    # -- io helpers --------------------------------------------------------

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return b""
        if length > _MAX_BODY_BYTES:
            return b""
        return self.rfile.read(length)

    def _read_json(self) -> dict[str, object] | None:
        try:
            decoded = json.loads(self._read_body() or b"{}")
        except json.JSONDecodeError:
            return None
        return decoded if isinstance(decoded, dict) else None

    def _write_response(self, status: HTTPStatus, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _write_json(self, status: HTTPStatus, body: object) -> None:
        self._write_response(status, json.dumps(body).encode("utf-8"))

    def _drop_connection(self) -> None:
        self.close_connection = True
        with contextlib.suppress(OSError):
            self.connection.close()


def build_ssl_context(cert_file: str, key_file: str) -> ssl.SSLContext:
    """Server-side TLS with a self-signed cert, no client-cert requirement.

    This is the test-mode bypass: the operator reaches the simulator with
    ``insecureSkipVerify: true`` and offers no (or an unverified) client cert.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile=cert_file, keyfile=key_file)
    context.verify_mode = ssl.CERT_NONE
    return context


def make_server(
    host: str,
    port: int,
    store: DeviceStore,
    *,
    cm_version: str,
    cert_file: str | None = None,
    key_file: str | None = None,
) -> _SimulatorServer:
    """Construct (but do not start) the simulator server.

    If both ``cert_file`` and ``key_file`` are given, the data plane is served
    over HTTPS; otherwise plain HTTP (convenient for unit tests).
    """
    dispatcher = Dispatcher(store, cm_version=cm_version)
    httpd = _SimulatorServer((host, port), store, dispatcher)
    if cert_file and key_file:
        context = build_ssl_context(cert_file, key_file)
        httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
        logger.info("TLS enabled (cert=%s)", cert_file)
    else:
        logger.warning("TLS disabled — serving plain HTTP (test mode)")
    return httpd
