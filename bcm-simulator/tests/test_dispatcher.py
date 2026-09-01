"""Unit tests for the JSON-RPC dispatcher — the wire contract the operator's
BCM client depends on. Mirrors the Go integration mock's ``handle`` switch.
"""

from __future__ import annotations

import json
from typing import Any

from bcm_simulator.handler import LITERAL_NULL, Dispatcher
from bcm_simulator.store import DeviceStore


def _envelope(service: str, call: str, args: list[Any] | None = None) -> bytes:
    return json.dumps({"service": service, "call": call, "args": args or []}).encode()


def _make() -> tuple[Dispatcher, DeviceStore]:
    store = DeviceStore()
    return Dispatcher(store), store


def test_version_meets_minimum() -> None:
    disp, _ = _make()
    body = json.loads(disp.version().encode())
    assert body["cm_version"] == "11.0"


def test_version_is_configurable() -> None:
    store = DeviceStore()
    disp = Dispatcher(store, cm_version="10.25.03")
    assert json.loads(disp.version().encode())["cm_version"] == "10.25.03"


def test_get_devices_returns_array() -> None:
    disp, store = _make()
    store.add_lite_node("gpu-node-001", "gpu-node")
    resp = disp.json_call(_envelope("cmdevice", "getDevices"))
    devices = json.loads(resp.encode())
    assert isinstance(devices, list)
    assert devices[0]["hostname"] == "gpu-node-001"


def test_get_device_found() -> None:
    disp, store = _make()
    store.add_lite_node("gpu-node-001", "gpu-node")
    resp = disp.json_call(_envelope("cmdevice", "getDevice", ["gpu-node-001"]))
    assert json.loads(resp.encode())["hostname"] == "gpu-node-001"


def test_get_device_absent_is_literal_null_not_error() -> None:
    disp, _ = _make()
    resp = disp.json_call(_envelope("cmdevice", "getDevice", ["missing"]))
    # Must be the literal bytes ``null`` — never a 404, never an error object.
    assert resp.encode() == LITERAL_NULL
    assert resp.status == 200


def test_update_device_persists_and_returns_success() -> None:
    disp, store = _make()
    store.add_lite_node("gpu-node-001", "gpu-node")
    dev = store.get_device("gpu-node-001")
    assert dev is not None
    dev["extra_values"]["osac_instance_id"] = "bmi-1"

    resp = disp.json_call(_envelope("cmdevice", "updateDevice", [dev]))
    body = json.loads(resp.encode())
    assert body["success"] is True
    assert body["task_uuid"] == "0"
    assert body["validation"] == []

    # A subsequent read reflects the write (supports verify-after-write).
    after = json.loads(disp.json_call(_envelope("cmdevice", "getDevice", ["gpu-node-001"])).encode())
    assert after["extra_values"]["osac_instance_id"] == "bmi-1"


def test_assign_then_unassign_round_trip() -> None:
    disp, store = _make()
    store.add_lite_node("gpu-node-001", "gpu-node")

    dev = store.get_device("gpu-node-001")
    assert dev is not None
    dev["extra_values"]["osac_instance_id"] = "bmi-1"
    disp.json_call(_envelope("cmdevice", "updateDevice", [dev]))

    # Unassign: remove the key and write the whole object back.
    dev = store.get_device("gpu-node-001")
    assert dev is not None
    del dev["extra_values"]["osac_instance_id"]
    disp.json_call(_envelope("cmdevice", "updateDevice", [dev]))

    after = store.get_device("gpu-node-001")
    assert after is not None
    assert "osac_instance_id" not in after["extra_values"]


def test_categories_and_partitions_default_empty() -> None:
    disp, _ = _make()
    assert json.loads(disp.json_call(_envelope("cmdevice", "getCategories")).encode()) == []
    assert json.loads(disp.json_call(_envelope("cmpart", "getPartitions")).encode()) == []


def test_unknown_call_returns_errormessage() -> None:
    disp, _ = _make()
    body = json.loads(disp.json_call(_envelope("cmdevice", "bogus")).encode())
    assert body["errormessage"] == "no such call: cmdevice.bogus"


def test_fail_call_injects_in_band_error() -> None:
    disp, store = _make()
    store.add_lite_node("gpu-node-001", "gpu-node")
    store.set_fail_call("cmdevice.updateDevice")
    body = json.loads(disp.json_call(_envelope("cmdevice", "updateDevice", [{"hostname": "x"}])).encode())
    assert "errormessage" in body
    assert "cmdevice.updateDevice" in body["errormessage"]
    # A non-failing call still works.
    assert isinstance(json.loads(disp.json_call(_envelope("cmdevice", "getDevices")).encode()), list)


def test_args_tolerates_object_and_missing() -> None:
    disp, store = _make()
    store.add_lite_node("gpu-node-001", "gpu-node")
    # args as {} (not a list) and args entirely absent must not crash.
    assert isinstance(
        json.loads(disp.json_call(b'{"service":"cmdevice","call":"getDevices","args":{}}').encode()), list
    )
    assert isinstance(json.loads(disp.json_call(b'{"service":"cmdevice","call":"getDevices"}').encode()), list)


def test_malformed_body_does_not_crash() -> None:
    disp, _ = _make()
    body = json.loads(disp.json_call(b"not json").encode())
    assert body["errormessage"] == "no such call: ."
