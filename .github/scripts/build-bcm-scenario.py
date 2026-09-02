#!/usr/bin/env python3
"""Build a BCM simulator scenario from the virtual-BMH VM inventory.

setup-virtual-bmh.sh (run with CREATE_BMH=false) writes a JSON array of the
libvirt VMs it created, each with a sushy-backed Redfish BMC address::

    [{"name": "...", "uuid": "...", "mac": "52:54:00:bb:cc:01",
      "bmc_address": "redfish-virtualmedia+http://<gw>:8000/redfish/v1/Systems/<uuid>"}]

This turns each VM into a free BCM LiteNode so the operator's BCM inventory
backend, when it assigns one, creates a BareMetalHost pointing at that same
sushy VM — which real Ironic then provisions. The resulting scenario is the
input to `python -m bcm_simulator --scenario ...`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def build_scenario(vms: list[dict[str, Any]], resource_class: str, bmc_user: str, bmc_password: str) -> dict[str, Any]:
    lite_nodes: list[dict[str, Any]] = []
    for vm in vms:
        for key in ("name", "mac", "bmc_address"):
            if not vm.get(key):
                raise ValueError(f"VM inventory entry missing '{key}': {vm!r}")
        lite_nodes.append(
            {
                "hostname": vm["name"],
                "resource_class": resource_class,
                "mac": vm["mac"],
                "bmc_address": vm["bmc_address"],
                # Match the sushy BMC credentials setup-virtual-bmh.sh uses; the
                # operator sources these from bmcSettings (OSAC-3768) to build the
                # BMH credential Secret.
                "bmc_settings": {"userName": bmc_user, "password": bmc_password},
            }
        )
    return {"cm_version": "11.0", "lite_nodes": lite_nodes}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a BCM simulator scenario from the virtual-BMH VM inventory")
    parser.add_argument("--vm-inventory", required=True, help="Path to the VM inventory JSON from setup-virtual-bmh.sh")
    parser.add_argument("--output", required=True, help="Path to write the scenario JSON")
    parser.add_argument("--resource-class", default="default", help="resource_class for each LiteNode")
    parser.add_argument("--bmc-user", default="admin", help="bmcSettings userName (default: admin)")
    parser.add_argument("--bmc-password", default="password", help="bmcSettings password (default: password)")
    args = parser.parse_args(argv)

    vms = json.loads(Path(args.vm_inventory).read_text(encoding="utf-8"))
    if not isinstance(vms, list) or not vms:
        print(f"error: VM inventory must be a non-empty JSON array, got {type(vms).__name__}", file=sys.stderr)
        return 1

    scenario = build_scenario(vms, args.resource_class, args.bmc_user, args.bmc_password)
    Path(args.output).write_text(json.dumps(scenario, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote scenario with {len(scenario['lite_nodes'])} LiteNode(s) to {args.output}")
    print(json.dumps(scenario, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
