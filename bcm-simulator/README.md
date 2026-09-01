# BCM Simulator

A small, dependency-free HTTP/TLS server that fakes the minimal
[NVIDIA Base Command Manager (BCM)](https://docs.nvidia.com/base-command-manager/)
JSON API surface the `bare-metal-fulfillment-operator`'s BCM inventory backend
talks to. It lets the full `BareMetalInstance` lifecycle be validated in CI
**without a real BCM instance** (OSAC-3773, epic OSAC-3761, feature OSAC-1339).

It is behavior-compatible with the Go integration mock (`mockBCM`) in
`osac/bare-metal-fulfillment-operator/internal/controller/baremetalinstance_bcm_integration_test.go`,
so the E2E path exercises the same contract the operator's unit/integration
tests do. The precise contract is documented in
`osac-1339/osac-3761/research-bcm-api-contract.md` (in the osac-workspace repo).

> This is the first of four PRs for OSAC-3773: **(1) simulator server** →
> (2) containerization → (3) E2E infra wiring → (4) lifecycle smoke test.
> Standard library only (Python 3.11+); no third-party runtime dependencies.

## What it implements

**Data plane** (what the operator's BCM client calls):

| Endpoint | Purpose |
|----------|---------|
| `GET /rest/v1/version` | Version gate — returns `cm_version` (default `11.0`, ≥ 10.25.3). |
| `POST /json` `cmdevice.getDevices` | List all device objects. |
| `POST /json` `cmdevice.getDevice` | One device, or the literal JSON `null` if absent (never a 404). |
| `POST /json` `cmdevice.updateDevice` | **Whole-object replacement** keyed by `hostname` — no merge, no version check. |
| `POST /json` `cmdevice.getCategories` / `cmpart.getPartitions` | BMC-credential inheritance fallback (default `[]`). |

TLS is served with a self-signed cert and **no client-cert requirement** (the
"test-mode bypass" — the operator connects with `insecureSkipVerify: true`).
Real BCM requires mTLS on `/json`; we intentionally don't, matching `mockBCM`.

**Control plane** (`/_admin/*`) — lets E2E tests drive scenarios and assert on
state. Available even when the data plane is faulted "down", so a test can recover:

| Endpoint | Body | Purpose |
|----------|------|---------|
| `GET /_admin/healthz` | — | Readiness probe. |
| `GET /_admin/devices` | — | Snapshot the device map (assert `extra_values` after assign/unassign). |
| `POST /_admin/seed` | `{devices, categories, partitions}` | Replace inventory. |
| `POST /_admin/add-lite-node` | `{hostname, resource_class, mac?, bmc_address?, bmc_settings?}` | Add a free host. |
| `POST /_admin/remove-device` | `{hostname}` | Delete a device (orphan/cleanup cases). |
| `POST /_admin/set-down` | `{down: bool}` | Simulate an unreachable BCM (drops data-plane connections). |
| `POST /_admin/set-fail-call` | `{call: "service.call" \| null}` | Return an in-band error for one call. |

## Run it

```bash
# Plain HTTP on :8443 with two default free LiteNodes (gpu-node, default):
python -m bcm_simulator

# From a scenario file, over HTTPS:
python -m bcm_simulator --scenario scenarios/default.json \
    --tls-cert server.pem --tls-key server.key --port 8443
```

Generate a throwaway self-signed cert for local/dev use:

```bash
openssl req -x509 -newkey rsa:2048 -nodes -keyout server.key -out server.pem \
    -days 365 -subj "/CN=bcm-simulator"
```

## Scenario files

A scenario is JSON. Use `lite_nodes` for the common free-host case; use
`devices` for full control over the raw object (e.g. `bmcSettings` inheritance
via category/partition, or a `NetworkBmcInterface` for Priority-2 discovery).

```json
{
  "cm_version": "11.0",
  "lite_nodes": [
    {"hostname": "gpu-node-001", "resource_class": "gpu-node", "bmc_address": "ipmi://10.0.0.1"}
  ]
}
```

BMC credentials come from `bmcSettings` (device → category → partition
inheritance), matching the operator's post-OSAC-3768 behavior — **not** from
`extra_values.osac_bmc_credentials_secret`.

## Tests

```bash
make test-bcm-simulator      # from the repo root
# or:  cd bcm-simulator && pytest
make lint                    # ruff check + format (covers bcm-simulator/)
```
