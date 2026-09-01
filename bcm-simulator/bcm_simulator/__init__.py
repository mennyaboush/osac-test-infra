"""BCM (NVIDIA Base Command Manager) JSON API simulator for OSAC E2E tests.

A small, dependency-free HTTP/TLS server that fakes the minimal BCM API surface
the bare-metal-fulfillment-operator's BCM inventory backend talks to, so the
full BareMetalInstance lifecycle can be validated in CI without a real BCM
instance (OSAC-3773).
"""

from __future__ import annotations

from .handler import Dispatcher, Response
from .scenarios import Scenario, build_default_scenario, load_scenario
from .server import make_server
from .store import DeviceStore

__all__ = [
    "DeviceStore",
    "Dispatcher",
    "Response",
    "Scenario",
    "build_default_scenario",
    "load_scenario",
    "make_server",
]
