from __future__ import annotations

import contextlib
import logging
from typing import Any

from tests.bmaas.conftest import log_bmh_inventory
from tests.core.grpc_client import GRPCClient
from tests.core.helpers import wait_for_bmi_cr, wait_for_bmi_deletion, wait_for_bmi_grpc_removal, wait_for_bmi_running
from tests.core.k8s_client import K8sClient
from tests.core.osac_cli import OsacCLI
from tests.core.runner import poll_until, run_unchecked

logger = logging.getLogger(__name__)

_RESTART_IN_PROGRESS: str = "BARE_METAL_INSTANCE_CONDITION_TYPE_RESTART_IN_PROGRESS"
_RESTART_FAILED: str = "BARE_METAL_INSTANCE_CONDITION_TYPE_RESTART_FAILED"


def _get_restart_condition_status(grpc: GRPCClient, bmi_id: str, condition_type: str) -> str:
    response: dict[str, Any] = grpc.get_baremetal_instance(bmi_id=bmi_id)
    conditions: list[dict[str, Any]] = response.get("object", {}).get("status", {}).get("conditions", [])
    for condition in conditions:
        if condition.get("type") == condition_type:
            return condition.get("status", "")
    return ""


def _get_status_restart_trigger(grpc: GRPCClient, bmi_id: str) -> int:
    response: dict[str, Any] = grpc.get_baremetal_instance(bmi_id=bmi_id)
    return int(response.get("object", {}).get("status", {}).get("restartTrigger", "0"))


def _log_bmi_state(grpc: GRPCClient, k8s: K8sClient, bmi_id: str, bmh_name: str, bmh_ns: str) -> None:
    try:
        bmi_resp: dict[str, Any] = grpc.get_baremetal_instance(bmi_id=bmi_id)
        bmi_status: dict[str, Any] = bmi_resp.get("object", {}).get("status", {})
        logger.info(
            "BMI gRPC state=%s, conditions=%s, restartTrigger(spec=%s, status=%s)",
            bmi_status.get("state", "?"),
            [
                (c.get("type", "?").removeprefix("BARE_METAL_INSTANCE_CONDITION_TYPE_"), c.get("status", "?"))
                for c in bmi_status.get("conditions", [])
            ],
            bmi_resp.get("object", {}).get("spec", {}).get("restartTrigger", "?"),
            bmi_status.get("restartTrigger", "?"),
        )
    except Exception:
        logger.exception("Failed to get BMI gRPC state for %s", bmi_id)

    bmh_state, _ = run_unchecked(
        "kubectl",
        "--as",
        "system:admin",
        "get",
        "baremetalhost",
        bmh_name,
        "-n",
        bmh_ns,
        "-o",
        "jsonpath={.status.provisioning.state}|{.spec.online}|{.status.poweredOn}"
        "|{.status.errorMessage}|{.status.errorType}",
    )
    logger.info("BMH %s: %s", bmh_name, bmh_state)

    bmi_cr_name: str = k8s.get_baremetal_instance_name(uuid=bmi_id, checked=False)
    if bmi_cr_name:
        cr_state, _ = run_unchecked(
            "kubectl",
            "--as",
            "system:admin",
            "get",
            "baremetalinstance",
            bmi_cr_name,
            "-n",
            k8s.namespace,
            "-o",
            "jsonpath={.status.phase}|{.status.conditions[*].type}|{.status.conditions[*].status}",
        )
        logger.info("BMI CR %s: %s", bmi_cr_name, cr_state)


def test_baremetal_instance_restart(
    cli: OsacCLI,
    grpc: GRPCClient,
    k8s_hub_client: K8sClient,
    catalog_item: str,
    bmh_namespace: str,
    test_run_id: str,
    ssh_public_key: str,
) -> None:
    name: str = f"e2e-bmi-restart-{test_run_id}"

    log_bmh_inventory(bmh_namespace)

    bmi_id: str = cli.create_baremetal_instance(name=name, catalog_item=catalog_item, ssh_key=ssh_public_key)
    logger.info("Created BMI %s (id=%s), waiting for CR and Running state", name, bmi_id)

    try:
        assert bmi_id in grpc.list_baremetal_instance_ids()

        bmi_cr_name: str = wait_for_bmi_cr(k8s=k8s_hub_client, uuid=bmi_id)
        logger.info("BMI CR appeared: %s", bmi_cr_name)

        wait_for_bmi_running(grpc=grpc, bmi_id=bmi_id)

        external_host_id: str = k8s_hub_client.get_baremetal_instance_external_host_id(name=bmi_cr_name)
        logger.info("BMI %s assigned to BMH: %s", bmi_id, external_host_id)
        log_bmh_inventory(bmh_namespace)

        assert "/" in external_host_id, f"Expected namespace/name format, got: {external_host_id}"
        bmh_ns, bmh_name = external_host_id.split("/", 1)
        assert bmh_ns == bmh_namespace, f"BMH landed in {bmh_ns}, expected {bmh_namespace}"

        initial_trigger: int = _get_status_restart_trigger(grpc, bmi_id)
        new_trigger: int = initial_trigger + 1
        logger.info("Incrementing restart_trigger from %d to %d", initial_trigger, new_trigger)

        grpc.update_baremetal_instance_restart_trigger(bmi_id=bmi_id, restart_trigger=new_trigger)

        poll_until(
            fn=lambda: _get_restart_condition_status(grpc, bmi_id, _RESTART_IN_PROGRESS),
            until=lambda v: v != "",
            retries=60,
            delay=2,
            description=f"{bmi_id} RESTART_IN_PROGRESS condition appears",
        )

        poll_until(
            fn=lambda: _get_status_restart_trigger(grpc, bmi_id),
            until=lambda v: v == new_trigger,
            retries=120,
            delay=10,
            description=f"{bmi_id} status.restart_trigger echoes {new_trigger}",
        )

        poll_until(
            fn=lambda: k8s_hub_client.get_bmh_powered_on(name=bmh_name, bmh_namespace=bmh_ns),
            until=lambda v: v == "true",
            retries=60,
            delay=5,
            description=f"{bmh_name} powered on after restart",
        )

        logger.info("Restart completed — checking post-restart state")
        _log_bmi_state(grpc, k8s_hub_client, bmi_id, bmh_name, bmh_ns)

        wait_for_bmi_running(grpc=grpc, bmi_id=bmi_id)

        final_trigger: int = _get_status_restart_trigger(grpc, bmi_id)
        assert final_trigger == new_trigger, (
            f"status.restart_trigger ({final_trigger}) does not match spec ({new_trigger})"
        )

        restart_in_progress: str = _get_restart_condition_status(grpc, bmi_id, _RESTART_IN_PROGRESS)
        assert restart_in_progress in ("", "CONDITION_STATUS_FALSE"), (
            f"RESTART_IN_PROGRESS should have cleared after restart, got: {restart_in_progress}"
        )

        restart_failed: str = _get_restart_condition_status(grpc, bmi_id, _RESTART_FAILED)
        assert restart_failed in ("", "CONDITION_STATUS_FALSE"), (
            f"Unexpected RESTART_FAILED condition: {restart_failed}"
        )

        cli.delete_baremetal_instance(uuid=bmi_id)
        wait_for_bmi_deletion(k8s=k8s_hub_client, name=bmi_cr_name)
        wait_for_bmi_grpc_removal(grpc=grpc, uuid=bmi_id)
    except BaseException:
        logger.error("Test failed — capturing diagnostic state")
        with contextlib.suppress(Exception):
            _log_bmi_state(grpc, k8s_hub_client, bmi_id, bmh_name, bmh_ns)
        bmi_cr: str = k8s_hub_client.get_baremetal_instance_name(uuid=bmi_id, checked=False)
        if bmi_cr:
            try:
                cli.delete_baremetal_instance(uuid=bmi_id)
                wait_for_bmi_deletion(k8s=k8s_hub_client, name=bmi_cr)
                wait_for_bmi_grpc_removal(grpc=grpc, uuid=bmi_id)
            except Exception:
                logger.exception("Failed to delete BMI %s during cleanup", bmi_id)
        raise
