"""Unit tests for the in-memory device store."""

from __future__ import annotations

from bcm_simulator.store import DeviceStore


def test_add_lite_node_canonical_shape() -> None:
    store = DeviceStore()
    store.add_lite_node("gpu-node-001", "gpu-node")
    dev = store.get_device("gpu-node-001")
    assert dev is not None
    assert dev["baseType"] == "Device"
    assert dev["childType"] == "LiteNode"
    assert dev["hostname"] == "gpu-node-001"
    assert dev["extra_values"]["resource_class"] == "gpu-node"
    # BMC creds come from bmcSettings (post OSAC-3768), not a credentials secret.
    assert dev["bmcSettings"]["userName"] == "root"
    assert "osac_bmc_credentials_secret" not in dev["extra_values"]
    # Priority-1 BMC address is pre-set so no Redfish discovery is needed.
    assert dev["extra_values"]["osac_bmc_address"] == "ipmi://10.0.0.1"


def test_get_device_absent_returns_none() -> None:
    store = DeviceStore()
    assert store.get_device("nope") is None


def test_update_device_is_whole_object_replacement() -> None:
    store = DeviceStore()
    store.add_lite_node("gpu-node-001", "gpu-node")

    # Simulate the operator's GET-modify-PUT: read, set osac_instance_id, write back.
    dev = store.get_device("gpu-node-001")
    assert dev is not None
    dev["extra_values"]["osac_instance_id"] = "bmi-uid-123"
    store.update_device(dev)

    persisted = store.get_device("gpu-node-001")
    assert persisted is not None
    assert persisted["extra_values"]["osac_instance_id"] == "bmi-uid-123"

    # Whole-object replacement: fields absent from the written object are gone.
    replacement = {"hostname": "gpu-node-001", "childType": "LiteNode"}
    store.update_device(replacement)
    after = store.get_device("gpu-node-001")
    assert after == replacement
    assert "extra_values" not in after


def test_update_device_without_hostname_is_ignored() -> None:
    store = DeviceStore()
    store.update_device({"childType": "LiteNode"})
    assert store.get_devices() == []


def test_remove_device_makes_it_absent() -> None:
    store = DeviceStore()
    store.add_lite_node("gpu-node-001", "gpu-node")
    store.remove_device("gpu-node-001")
    assert store.get_device("gpu-node-001") is None


def test_get_devices_returns_deep_copies() -> None:
    store = DeviceStore()
    store.add_lite_node("gpu-node-001", "gpu-node")
    devices = store.get_devices()
    devices[0]["extra_values"]["resource_class"] = "mutated"
    # Mutating the returned copy must not affect stored state.
    assert store.get_device("gpu-node-001")["extra_values"]["resource_class"] == "gpu-node"


def test_seed_replaces_contents_and_validates_hostname() -> None:
    store = DeviceStore()
    store.add_lite_node("old", "default")
    store.seed(devices=[{"hostname": "new-1", "childType": "LiteNode"}])
    assert store.get_device("old") is None
    assert store.get_device("new-1") is not None


def test_seed_rejects_device_without_hostname() -> None:
    store = DeviceStore()
    try:
        store.seed(devices=[{"childType": "LiteNode"}])
    except ValueError:
        return
    raise AssertionError("expected ValueError for device without hostname")


def test_fault_knobs() -> None:
    store = DeviceStore()
    assert store.is_down() is False
    store.set_down(True)
    assert store.is_down() is True
    assert store.failing_call() is None
    store.set_fail_call("cmdevice.updateDevice")
    assert store.failing_call() == "cmdevice.updateDevice"
    store.set_fail_call(None)
    assert store.failing_call() is None
