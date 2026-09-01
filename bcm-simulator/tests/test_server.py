"""Transport-level tests against a live simulator server (plain HTTP)."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.client import HTTPConnection, RemoteDisconnected

import pytest
from bcm_simulator.scenarios import build_default_scenario
from bcm_simulator.server import make_server


@pytest.fixture
def server() -> Iterator[tuple[str, int]]:
    scenario = build_default_scenario()
    httpd = make_server("127.0.0.1", 0, scenario.store, cm_version=scenario.cm_version)
    host, port = httpd.server_address[0], httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield host, port
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _post(host: str, port: int, path: str, payload: object) -> tuple[int, bytes]:
    conn = HTTPConnection(host, port, timeout=5)
    try:
        conn.request("POST", path, body=json.dumps(payload), headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


def _get(host: str, port: int, path: str) -> tuple[int, bytes]:
    conn = HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


def _json_call(host: str, port: int, service: str, call: str, args: list[object] | None = None) -> object:
    status, body = _post(host, port, "/json", {"service": service, "call": call, "args": args or []})
    assert status == 200
    return json.loads(body)


def test_version_endpoint(server: tuple[str, int]) -> None:
    host, port = server
    status, body = _get(host, port, "/rest/v1/version")
    assert status == 200
    assert json.loads(body)["cm_version"] == "11.0"


def test_default_scenario_has_free_hosts(server: tuple[str, int]) -> None:
    host, port = server
    devices = _json_call(host, port, "cmdevice", "getDevices")
    hostnames = {d["hostname"] for d in devices}
    assert "gpu-node-001" in hostnames
    assert "default-node-001" in hostnames


def test_full_assign_read_unassign_over_http(server: tuple[str, int]) -> None:
    host, port = server
    dev = _json_call(host, port, "cmdevice", "getDevice", ["gpu-node-001"])
    dev["extra_values"]["osac_instance_id"] = "bmi-42"
    result = _json_call(host, port, "cmdevice", "updateDevice", [dev])
    assert result["success"] is True

    reread = _json_call(host, port, "cmdevice", "getDevice", ["gpu-node-001"])
    assert reread["extra_values"]["osac_instance_id"] == "bmi-42"


def test_get_device_missing_is_literal_null(server: tuple[str, int]) -> None:
    host, port = server
    status, body = _post(host, port, "/json", {"service": "cmdevice", "call": "getDevice", "args": ["missing"]})
    assert status == 200
    assert body == b"null"


def test_admin_seed_and_devices_snapshot(server: tuple[str, int]) -> None:
    host, port = server
    status, _ = _post(host, port, "/_admin/seed", {"devices": [{"hostname": "only-1", "childType": "LiteNode"}]})
    assert status == 200
    status, body = _get(host, port, "/_admin/devices")
    assert status == 200
    devices = json.loads(body)
    assert {d["hostname"] for d in devices} == {"only-1"}


def test_admin_add_and_remove_device(server: tuple[str, int]) -> None:
    host, port = server
    _post(host, port, "/_admin/add-lite-node", {"hostname": "extra-1", "resource_class": "gpu-node"})
    assert _json_call(host, port, "cmdevice", "getDevice", ["extra-1"]) is not None
    _post(host, port, "/_admin/remove-device", {"hostname": "extra-1"})
    _status, body = _post(host, port, "/json", {"service": "cmdevice", "call": "getDevice", "args": ["extra-1"]})
    assert body == b"null"


def test_admin_fail_call_injection(server: tuple[str, int]) -> None:
    host, port = server
    _post(host, port, "/_admin/set-fail-call", {"call": "cmdevice.getDevices"})
    body = _json_call(host, port, "cmdevice", "getDevices")
    assert "errormessage" in body
    _post(host, port, "/_admin/set-fail-call", {"call": None})
    assert isinstance(_json_call(host, port, "cmdevice", "getDevices"), list)


def test_down_drops_data_plane_but_admin_recovers(server: tuple[str, int]) -> None:
    host, port = server
    _post(host, port, "/_admin/set-down", {"down": True})

    # Data plane connection is dropped -> transport error, not an HTTP status.
    with pytest.raises((RemoteDisconnected, ConnectionResetError, ConnectionError)):
        _post(host, port, "/json", {"service": "cmdevice", "call": "getDevices", "args": []})

    # Control plane stays up so a test can recover.
    status, _ = _post(host, port, "/_admin/set-down", {"down": False})
    assert status == 200
    assert isinstance(_json_call(host, port, "cmdevice", "getDevices"), list)


def test_admin_bad_request(server: tuple[str, int]) -> None:
    host, port = server
    status, _ = _post(host, port, "/_admin/add-lite-node", {"hostname": "x"})  # missing resource_class
    assert status == 400
