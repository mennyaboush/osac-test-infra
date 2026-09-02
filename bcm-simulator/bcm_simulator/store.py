"""In-memory device store for the BCM simulator.

Mirrors the stateful Go integration mock ``mockBCM`` in
``osac/bare-metal-fulfillment-operator/internal/controller/baremetalinstance_bcm_integration_test.go``.
Devices are stored as decoded JSON objects keyed by hostname so the operator's
GET-modify-PUT assignment round-trips (``osac_instance_id``) persist across
reconciles. ``update_device`` is a whole-object replacement (no merge, no
version check), exactly like real BCM's ``cmdevice.updateDevice``.
"""

from __future__ import annotations

import copy
import threading
from typing import Any

# Default device-level BMC credentials seeded onto a LiteNode. Post OSAC-3768 the
# operator sources BMC credentials from bmcSettings (device -> category ->
# partition inheritance), NOT from extra_values.osac_bmc_credentials_secret.
# Fields mirror the real BCM device object (see osac-1339/reference/deep-dive.md):
# userName/password/userID only — real bmcSettings carries no nested baseType, and
# the operator's client ignores unknown fields anyway (bcmclient/types.go BMCSettings).
_DEFAULT_BMC_SETTINGS: dict[str, Any] = {"userName": "root", "password": "calvin", "userID": 2}


class DeviceStore:
    """Thread-safe in-memory store of BCM devices, categories and partitions.

    Also holds the two fault-injection knobs the error-path E2E scenarios need:
    ``down`` (simulate an unreachable BCM) and ``fail_call`` (answer a specific
    ``service.call`` with an in-band error response).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._devices: dict[str, dict[str, Any]] = {}
        self._categories: list[dict[str, Any]] = []
        self._partitions: list[dict[str, Any]] = []
        self.down: bool = False
        self.fail_call: str | None = None

    # -- seeding -----------------------------------------------------------

    def seed(
        self,
        devices: list[dict[str, Any]],
        categories: list[dict[str, Any]] | None = None,
        partitions: list[dict[str, Any]] | None = None,
    ) -> None:
        """Replace the entire store contents (scenario-based startup seeding)."""
        with self._lock:
            self._devices = {}
            for dev in devices:
                hostname = dev.get("hostname")
                if not isinstance(hostname, str) or not hostname:
                    raise ValueError(f"seeded device is missing a string 'hostname': {dev!r}")
                self._devices[hostname] = copy.deepcopy(dev)
            self._categories = copy.deepcopy(categories) if categories else []
            self._partitions = copy.deepcopy(partitions) if partitions else []

    def add_lite_node(
        self,
        hostname: str,
        resource_class: str,
        *,
        mac: str = "aa:bb:cc:dd:ee:01",
        bmc_address: str = "ipmi://10.0.0.1",
        bmc_settings: dict[str, Any] | None = None,
    ) -> None:
        """Register a free LiteNode with device-level BMC creds and a Priority-1
        BMC address, so credential/address resolution needs no category/partition
        lookup or Redfish discovery. Mirrors the Go mock's addLiteNode.
        """
        with self._lock:
            self._devices[hostname] = {
                "baseType": "Device",
                "childType": "LiteNode",
                "uuid": f"uuid-{hostname}",
                "hostname": hostname,
                "mac": mac,
                "bmcSettings": copy.deepcopy(bmc_settings) if bmc_settings else copy.deepcopy(_DEFAULT_BMC_SETTINGS),
                "extra_values": {"resource_class": resource_class, "osac_bmc_address": bmc_address},
            }

    # -- reads -------------------------------------------------------------

    def get_devices(self) -> list[dict[str, Any]]:
        """All stored device objects (``cmdevice.getDevices``)."""
        with self._lock:
            return [copy.deepcopy(dev) for dev in self._devices.values()]

    def get_device(self, hostname: str) -> dict[str, Any] | None:
        """A single device object, or ``None`` if absent (``cmdevice.getDevice``).

        A ``None`` here becomes the literal JSON ``null`` on the wire — never a
        404 and never an error object — matching the client's contract.
        """
        with self._lock:
            dev = self._devices.get(hostname)
            return copy.deepcopy(dev) if dev is not None else None

    def get_categories(self) -> list[dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(self._categories)

    def get_partitions(self) -> list[dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(self._partitions)

    # -- writes ------------------------------------------------------------

    def update_device(self, device: dict[str, Any]) -> None:
        """Whole-object replacement keyed by ``hostname`` (``cmdevice.updateDevice``).

        BCM's updateDevice is a full PUT, not a patch: the operator always sends
        the entire object back after mutating one ``extra_values`` key. We replace
        the stored object wholesale so subsequent reads reflect exactly what was
        written (the operator relies on a verify-after-write re-read). Last write
        wins; there is no conflict detection in real BCM, so we do none either.
        """
        hostname = device.get("hostname")
        if not isinstance(hostname, str) or not hostname:
            return
        with self._lock:
            self._devices[hostname] = copy.deepcopy(device)

    def remove_device(self, hostname: str) -> None:
        """Delete a device so ``get_device`` returns ``None`` (orphan/cleanup cases)."""
        with self._lock:
            self._devices.pop(hostname, None)

    # -- fault injection ---------------------------------------------------

    def set_down(self, down: bool) -> None:
        with self._lock:
            self.down = down

    def set_fail_call(self, key: str | None) -> None:
        with self._lock:
            self.fail_call = key or None

    def is_down(self) -> bool:
        with self._lock:
            return self.down

    def failing_call(self) -> str | None:
        with self._lock:
            return self.fail_call
