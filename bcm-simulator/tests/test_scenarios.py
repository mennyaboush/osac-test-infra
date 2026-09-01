"""Unit tests for scenario loading."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from bcm_simulator.scenarios import build_default_scenario, load_scenario


def test_default_scenario() -> None:
    scenario = build_default_scenario()
    hostnames = {d["hostname"] for d in scenario.store.get_devices()}
    assert hostnames == {"gpu-node-001", "default-node-001"}
    assert scenario.cm_version == "11.0"


def test_bundled_default_json_loads() -> None:
    path = Path(__file__).parent.parent / "scenarios" / "default.json"
    scenario = load_scenario(path)
    hostnames = {d["hostname"] for d in scenario.store.get_devices()}
    assert "gpu-node-001" in hostnames
    assert "default-node-001" in hostnames


def test_load_scenario_lite_nodes_and_devices(tmp_path: Path) -> None:
    doc = {
        "cm_version": "10.25.03",
        "lite_nodes": [{"hostname": "ln-1", "resource_class": "gpu-node", "bmc_address": "ipmi://1.2.3.4"}],
        "devices": [{"hostname": "raw-1", "childType": "LiteNode", "bmcSettings": {"userName": "u", "password": "p"}}],
    }
    path = tmp_path / "s.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    scenario = load_scenario(path)
    assert scenario.cm_version == "10.25.03"
    ln1 = scenario.store.get_device("ln-1")
    assert ln1 is not None
    assert ln1["extra_values"]["osac_bmc_address"] == "ipmi://1.2.3.4"
    assert scenario.store.get_device("raw-1") is not None


def test_load_scenario_rejects_lite_node_without_resource_class(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"lite_nodes": [{"hostname": "x"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="resource_class"):
        load_scenario(path)


def test_load_scenario_rejects_non_object(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a JSON object"):
        load_scenario(path)
