"""Scenario loading and defaults for the BCM simulator.

A scenario is a JSON document describing the pre-configured inventory the
simulator serves at startup (AC: "constructed with pre-configured LiteNodes at
startup, scenario-based"). Shape::

    {
      "cm_version": "11.0",              # optional, defaults to 11.0
      "lite_nodes": [                    # convenience shorthand, expanded via
        {"hostname": "gpu-node-001",     #   DeviceStore.add_lite_node
         "resource_class": "gpu-node",
         "mac": "aa:bb:cc:dd:ee:01",     # optional
         "bmc_address": "ipmi://10.0.0.1"}  # optional
      ],
      "devices": [ { ...full device... } ],  # optional, seeded verbatim
      "categories": [ ... ],            # optional
      "partitions": [ ... ]             # optional
    }

Use ``lite_nodes`` for the common free-host case; use ``devices`` when a test
needs full control over the raw object (e.g. bmcSettings inheritance via
category/partition, or a NetworkBmcInterface for Priority-2 discovery).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .handler import DEFAULT_CM_VERSION
from .store import DeviceStore


@dataclass
class Scenario:
    store: DeviceStore
    cm_version: str = DEFAULT_CM_VERSION
    lite_nodes: list[dict[str, Any]] = field(default_factory=list)
    devices: list[dict[str, Any]] = field(default_factory=list)


def build_default_scenario() -> Scenario:
    """A minimal scenario with two free LiteNodes, enough for a lifecycle smoke
    test: one ``gpu-node`` and one ``default`` resource class.
    """
    store = DeviceStore()
    store.add_lite_node("gpu-node-001", "gpu-node", mac="aa:bb:cc:dd:ee:01", bmc_address="ipmi://10.0.0.1")
    store.add_lite_node("default-node-001", "default", mac="aa:bb:cc:dd:ee:02", bmc_address="ipmi://10.0.0.2")
    return Scenario(store=store, cm_version=DEFAULT_CM_VERSION)


def load_scenario(path: str | Path) -> Scenario:
    """Load and validate a scenario JSON file into a seeded ``DeviceStore``."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError(f"scenario file must be a JSON object, got {type(doc).__name__}")

    cm_version = doc.get("cm_version", DEFAULT_CM_VERSION)
    if not isinstance(cm_version, str):
        raise ValueError("scenario 'cm_version' must be a string")

    lite_nodes = doc.get("lite_nodes", []) or []
    devices = doc.get("devices", []) or []
    categories = doc.get("categories", []) or []
    partitions = doc.get("partitions", []) or []
    for name, value in (
        ("lite_nodes", lite_nodes),
        ("devices", devices),
        ("categories", categories),
        ("partitions", partitions),
    ):
        if not isinstance(value, list):
            raise ValueError(f"scenario '{name}' must be a list")

    store = DeviceStore()
    store.seed(devices=devices, categories=categories, partitions=partitions)
    for node in lite_nodes:
        if not isinstance(node, dict) or "hostname" not in node or "resource_class" not in node:
            raise ValueError(f"lite_nodes entry needs 'hostname' and 'resource_class': {node!r}")
        kwargs: dict[str, Any] = {}
        if "mac" in node:
            kwargs["mac"] = node["mac"]
        if "bmc_address" in node:
            kwargs["bmc_address"] = node["bmc_address"]
        if "bmc_settings" in node:
            kwargs["bmc_settings"] = node["bmc_settings"]
        store.add_lite_node(node["hostname"], node["resource_class"], **kwargs)

    return Scenario(store=store, cm_version=cm_version, lite_nodes=lite_nodes, devices=devices)
