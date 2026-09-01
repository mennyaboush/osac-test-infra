"""Request dispatch for the BCM simulator.

Pure logic, independent of the HTTP/TLS transport, so it can be unit-tested
directly. Mirrors the Go integration mock's ``handle`` switch. The operator
makes exactly two wire shapes:

* ``GET /rest/v1/version``            -> version object
* ``POST /json`` JSON-RPC envelope    -> dispatch on ``service.call``

See ``osac-1339/osac-3761/research-bcm-api-contract.md`` for the full contract.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .store import DeviceStore

# Minimum BCM version the operator's client accepts (>= 10.25.3). "11.0" passes.
DEFAULT_CM_VERSION = "11.0"

_VERSION_BODY: dict[str, Any] = {
    "cm_version": DEFAULT_CM_VERSION,
    "cmd_version": "3.1",
    "build_hash": "simulated",
    "build_index": 0,
    "database_version": 0,
}

# Sentinel: the wire body must be the literal bytes ``null`` (getDevice miss).
LITERAL_NULL = b"null"


@dataclass(frozen=True)
class Response:
    """A dispatch result. Either ``json_body`` (serialized) or ``raw`` bytes."""

    status: int = 200
    json_body: Any = None
    raw: bytes | None = None

    def encode(self) -> bytes:
        if self.raw is not None:
            return self.raw
        return json.dumps(self.json_body).encode("utf-8")


class Dispatcher:
    """Turns parsed requests into ``Response`` objects against a ``DeviceStore``."""

    def __init__(self, store: DeviceStore, *, cm_version: str = DEFAULT_CM_VERSION) -> None:
        self._store = store
        self._version_body = dict(_VERSION_BODY, cm_version=cm_version)

    def version(self) -> Response:
        """``GET /rest/v1/version``."""
        return Response(json_body=self._version_body)

    def json_call(self, body: bytes) -> Response:
        """``POST /json`` — dispatch the JSON-RPC envelope on ``service.call``."""
        service, call, args = _parse_envelope(body)
        key = f"{service}.{call}"

        failing = self._store.failing_call()
        if failing is not None and failing == key:
            # In-band error at HTTP 200: the client treats a non-empty
            # errormessage as an error even on 200. This is the failCall knob.
            return Response(json_body={"errormessage": f"simulated BCM error for {key}"})

        if key == "cmdevice.getDevices":
            return Response(json_body=self._store.get_devices())

        if key == "cmdevice.getDevice":
            hostname = args[0] if args and isinstance(args[0], str) else ""
            device = self._store.get_device(hostname)
            if device is None:
                return Response(raw=LITERAL_NULL)
            return Response(json_body=device)

        if key == "cmdevice.updateDevice":
            device = args[0] if args and isinstance(args[0], dict) else None
            if device is not None:
                self._store.update_device(device)
            return Response(json_body={"success": True, "task_uuid": "0", "validation": []})

        if key == "cmdevice.getCategories":
            return Response(json_body=self._store.get_categories())

        if key == "cmpart.getPartitions":
            return Response(json_body=self._store.get_partitions())

        return Response(json_body={"errormessage": f"no such call: {key}"})


def _parse_envelope(body: bytes) -> tuple[str, str, list[Any]]:
    """Extract ``service``, ``call`` and positional ``args`` from the request.

    Tolerant of ``args`` being absent, ``[]`` or ``{}`` (the Go client sends an
    array; the mock tolerates both), matching the mock's lenient decode.
    """
    try:
        envelope = json.loads(body or b"{}")
    except json.JSONDecodeError:
        envelope = {}
    if not isinstance(envelope, dict):
        envelope = {}
    service = envelope.get("service") if isinstance(envelope.get("service"), str) else ""
    call = envelope.get("call") if isinstance(envelope.get("call"), str) else ""
    raw_args = envelope.get("args")
    args = raw_args if isinstance(raw_args, list) else []
    return service, call, args
