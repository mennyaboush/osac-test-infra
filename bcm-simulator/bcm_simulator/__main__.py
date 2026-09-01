"""CLI entry point for the BCM simulator.

Examples::

    python -m bcm_simulator --port 8443 --tls-cert server.pem --tls-key server.key
    python -m bcm_simulator --scenario scenarios/default.json
    python -m bcm_simulator            # plain HTTP on :8443, default 2 free hosts
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from .scenarios import Scenario, build_default_scenario, load_scenario
from .server import make_server


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    # Every flag falls back to a BCM_SIM_* env var, so the container image can be
    # configured via a Deployment's env without overriding its command/args.
    parser = argparse.ArgumentParser(prog="bcm_simulator", description="Fake NVIDIA BCM JSON API for OSAC E2E tests")
    parser.add_argument("--host", default=os.getenv("BCM_SIM_HOST", "0.0.0.0"), help="Bind address")
    parser.add_argument("--port", type=int, default=int(os.getenv("BCM_SIM_PORT", "8443")), help="Bind port")
    parser.add_argument(
        "--scenario", default=os.getenv("BCM_SIM_SCENARIO"), help="Scenario JSON file (default: two free LiteNodes)"
    )
    # cert+key both unset -> plain HTTP (test mode); both set -> HTTPS.
    parser.add_argument("--tls-cert", default=os.getenv("BCM_SIM_TLS_CERT"), help="Server TLS cert (PEM)")
    parser.add_argument("--tls-key", default=os.getenv("BCM_SIM_TLS_KEY"), help="Server TLS key (PEM)")
    parser.add_argument("--log-level", default=os.getenv("BCM_SIM_LOG_LEVEL", "INFO"), help="Logging level")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)-8s %(name)s %(message)s")

    if bool(args.tls_cert) != bool(args.tls_key):
        print("error: --tls-cert and --tls-key must be given together", file=sys.stderr)
        return 2

    scenario: Scenario = load_scenario(args.scenario) if args.scenario else build_default_scenario()
    logging.getLogger("bcm_simulator").info(
        "seeded %d device(s); cm_version=%s", len(scenario.store.get_devices()), scenario.cm_version
    )

    httpd = make_server(
        args.host,
        args.port,
        scenario.store,
        cm_version=scenario.cm_version,
        cert_file=args.tls_cert,
        key_file=args.tls_key,
    )
    scheme = "https" if args.tls_cert else "http"
    logging.getLogger("bcm_simulator").info("BCM simulator listening on %s://%s:%d", scheme, args.host, args.port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logging.getLogger("bcm_simulator").info("shutting down")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
