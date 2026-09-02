#!/usr/bin/env bash
# Provision the BCM simulator for BMaaS E2E tests, backed by the real
# sushy-tools/libvirt/Ironic fabric.
#
# Unlike the Metal3 path, the BCM inventory backend creates the BareMetalHosts
# itself from BCM LiteNodes. So this script:
#   1. runs setup-virtual-bmh.sh with CREATE_BMH=false to stand up Ironic +
#      sushy-tools + libvirt VMs (but NO BMHs) and export the VM inventory;
#   2. turns that VM inventory into a BCM scenario whose LiteNodes point their
#      osac_bmc_address/mac at those same sushy VMs;
#   3. runs the containerized BCM simulator on the runner host (podman,
#      --network=host) bound to the libvirt gateway IP, so the in-cluster
#      operator reaches it exactly the way Ironic reaches sushy-tools.
#
# The operator, configured with a `type: bcm` inventory pointing at this
# simulator, then assigns a LiteNode, creates a BMH against the matching sushy
# VM, and real Ironic provisions it — the full handoff the integration tests fake.
#
# Required env:
#   CLONE_NAME   — cluster-tool clone name
#   KUBECONFIG   — path to the cluster kubeconfig
#
# Optional env:
#   BMH_NAMESPACE     — namespace the operator will create BMHs in (default: host-inventory)
#   BMH_COUNT         — number of virtual hosts (default: 2)
#   SUSHY_PORT        — sushy-tools port (default: 8000)
#   BCM_SIM_PORT      — BCM simulator HTTPS port (default: 8443)
#   BCM_SIM_IMAGE     — simulator image tag to build/run (default: bcm-simulator:e2e-${CLONE_NAME})
#   BCM_RESOURCE_CLASS— resource_class stamped on every LiteNode (default: default)
#
# Exports BCM_SIMULATOR_URL to $GITHUB_ENV for the "Create BMF secrets" step.
# Teardown derives all paths/names from CLONE_NAME.
set -euo pipefail

: "${CLONE_NAME:?CLONE_NAME is required}"
: "${KUBECONFIG:?KUBECONFIG is required}"

BMH_NAMESPACE="${BMH_NAMESPACE:-host-inventory}"
BMH_COUNT="${BMH_COUNT:-2}"
SUSHY_PORT="${SUSHY_PORT:-8000}"
BCM_SIM_PORT="${BCM_SIM_PORT:-8443}"
BCM_SIM_IMAGE="${BCM_SIM_IMAGE:-bcm-simulator:e2e-${CLONE_NAME}}"
BCM_RESOURCE_CLASS="${BCM_RESOURCE_CLASS:-default}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
WORK_DIR="${HOME}/bcm-sim-${CLONE_NAME}"
CONTAINER_NAME="bcm-simulator-${CLONE_NAME}"
CT_NETWORK="test-infra-net-${CLONE_NAME}"
VIRSH="virsh -c qemu:///system"

VM_INVENTORY_FILE="${WORK_DIR}/vm-inventory.json"
SCENARIO_FILE="${WORK_DIR}/scenario.json"
TLS_CERT="${WORK_DIR}/tls.crt"
TLS_KEY="${WORK_DIR}/tls.key"

mkdir -p "${WORK_DIR}"

# --- Step 1: sushy/libvirt/Ironic fabric, no BMHs, export VM inventory ---
echo "==> Standing up virtual BMH fabric (no BMHs; operator creates them)..."
CREATE_BMH=false \
BMH_NAMESPACE="${BMH_NAMESPACE}" \
BMH_COUNT="${BMH_COUNT}" \
SUSHY_PORT="${SUSHY_PORT}" \
VM_INVENTORY_FILE="${VM_INVENTORY_FILE}" \
  bash "${SCRIPT_DIR}/setup-virtual-bmh.sh"

# --- Step 2: Discover the gateway IP the simulator binds to ---
GW_IP=$(${VIRSH} net-dumpxml "${CT_NETWORK}" | python3 -c "
import sys, xml.etree.ElementTree as ET
print(ET.parse(sys.stdin).getroot().find('.//ip').get('address'))
")
echo "==> Gateway IP for BCM simulator: ${GW_IP}"

# --- Step 3: Build the BCM scenario from the VM inventory ---
echo "==> Building BCM scenario from VM inventory..."
python3 "${SCRIPT_DIR}/build-bcm-scenario.py" \
  --vm-inventory "${VM_INVENTORY_FILE}" \
  --output "${SCENARIO_FILE}" \
  --resource-class "${BCM_RESOURCE_CLASS}"

# --- Step 4: Self-signed server TLS cert (operator connects insecureSkipVerify) ---
echo "==> Generating self-signed TLS cert for the simulator..."
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout "${TLS_KEY}" -out "${TLS_CERT}" \
  -days 3 -subj "/CN=bcm-simulator" 2>/dev/null

# --- Step 5: Build and run the simulator container on the runner host ---
echo "==> Building BCM simulator image ${BCM_SIM_IMAGE}..."
podman build -t "${BCM_SIM_IMAGE}" -f "${REPO_ROOT}/bcm-simulator/Containerfile" "${REPO_ROOT}/bcm-simulator"

echo "==> Starting BCM simulator container on ${GW_IP}:${BCM_SIM_PORT}..."
podman rm -f "${CONTAINER_NAME}" 2>/dev/null || true
podman run -d --name "${CONTAINER_NAME}" --network=host \
  -v "${SCENARIO_FILE}:/etc/bcm-sim/scenario.json:ro,Z" \
  -v "${TLS_CERT}:/etc/bcm-sim/tls.crt:ro,Z" \
  -v "${TLS_KEY}:/etc/bcm-sim/tls.key:ro,Z" \
  -e BCM_SIM_HOST="${GW_IP}" \
  -e BCM_SIM_PORT="${BCM_SIM_PORT}" \
  -e BCM_SIM_SCENARIO=/etc/bcm-sim/scenario.json \
  -e BCM_SIM_TLS_CERT=/etc/bcm-sim/tls.crt \
  -e BCM_SIM_TLS_KEY=/etc/bcm-sim/tls.key \
  "${BCM_SIM_IMAGE}"

# --- Step 6: Wait for the simulator to answer ---
echo "==> Waiting for BCM simulator health..."
SIM_READY=false
for attempt in $(seq 1 30); do
  if curl -ksf --connect-timeout 3 --max-time 5 "https://${GW_IP}:${BCM_SIM_PORT}/_admin/healthz" >/dev/null; then
    SIM_READY=true
    break
  fi
  if ! podman container exists "${CONTAINER_NAME}" || \
     [[ "$(podman inspect -f '{{.State.Running}}' "${CONTAINER_NAME}" 2>/dev/null)" != "true" ]]; then
    echo "ERROR: simulator container is not running. Logs:" >&2
    podman logs "${CONTAINER_NAME}" >&2 2>&1 || true
    exit 1
  fi
  echo "    attempt ${attempt}/30: not ready yet"
  sleep 2
done
if [[ "${SIM_READY}" != "true" ]]; then
  echo "ERROR: BCM simulator did not become healthy. Logs:" >&2
  podman logs "${CONTAINER_NAME}" >&2 2>&1 || true
  exit 1
fi

echo "==> BCM simulator serving $(curl -ksf "https://${GW_IP}:${BCM_SIM_PORT}/_admin/devices" | python3 -c 'import sys,json; print(len(json.load(sys.stdin)))') LiteNode(s)."

# --- Step 7: Export the URL for the inventory config secret ---
if [[ -n "${GITHUB_ENV:-}" ]]; then
  echo "BCM_SIMULATOR_URL=https://${GW_IP}:${BCM_SIM_PORT}" >> "${GITHUB_ENV}"
fi
echo "==> BCM simulator setup complete: https://${GW_IP}:${BCM_SIM_PORT}"
