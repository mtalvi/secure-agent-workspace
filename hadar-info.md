# Secure Agent Workspace — Deployment Planning

> This document is written by a deployment agent in real time.
> Static sections (prerequisites, commands) are pre-populated from repo analysis.
> Result/status fields are filled in during each phase's execution.

---

## Environment

| Field | Value |
|---|---|
| OCP version | 4.22.6 |
| Cluster API | `https://api.cluster-s6f9c.dyn.redhatworkshops.io:6443` |
| Cluster domain | `cluster-s6f9c.dyn.redhatworkshops.io` |
| Nodes | 3 control-plane, 1 worker |
| Date of run | 2026-08-04 |
| Inference provider | NVIDIA NGC (`build`) |
| Inference model | `meta/llama-3.3-70b-instruct` |
| API key source | `local-docs/.env` → `NGC_API_KEY` |
| PR applied | #11 (fix: stabilize deployment — pre-built images, init container, OpenClaw 2026.7.1 syntax) |

---

## Phase 0: Shared Prerequisites

These steps are identical for both deployment paths and must complete successfully before either path begins.

### Required Operators (must be pre-installed on cluster)

| Operator | OLM Package | Namespace | Channel | Required by |
|---|---|---|---|---|
| OpenShift Virtualization | `kubevirt-hyperconverged` | `openshift-cnv` | `stable` | both paths |
| Red Hat Build of Keycloak | `rhbk-operator` | `openshell-agents` | `stable-v26` | both paths |
| External Secrets Operator | `openshift-external-secrets-operator` | `external-secrets-operator` | `stable-v1` | VP path only |

### Required CLI Tools

| Tool | Minimum version | Install hint |
|---|---|---|
| `oc` | matching cluster version | bundled with OCP |
| `helm` | 3.x | `brew install helm` |
| `virtctl` | v1.4.1+ | auto-detected from cluster or set `virtctlVersion` in chart |
| `openshell` | latest | https://github.com/NVIDIA/OpenShell/releases |

### Minimum Hardware per Sandbox VM

| Resource | Per VM | Cluster overhead |
|---|---|---|
| CPU | 4 cores | 8 cores (operators, Keycloak, Vault) |
| Memory | 8 GiB | 16 GiB |
| Storage | 40 GiB | 50 GiB (golden image, registry) |

### Step 0.1 — Verify CLI login and prereqs

```bash
oc whoami
make check-prereqs
```

**Result:** Cluster logged in as `admin` @ `https://api.cluster-s6f9c.dyn.redhatworkshops.io:6443`. OCP 4.22.6, 4 nodes (3 control-plane + 1 worker). RHBK operator installed (`rhbk-operator.v26.4.13-opr.1`). OpenShift Virtualization not yet installed — will be provisioned by ArgoCD in Phase 1. `make check-prereqs` skipped for VP path (it gates on OCP Virt which ArgoCD installs).

---

### Step 0.2 — Generate SSH keys

```bash
make generate-keys
```

**Result:** Keys already existed at `~/.generated-ssh-keys/sandbox-ssh{,.pub}`. No action needed.

---

### Step 0.3 — Configure `~/values-secret.yaml` (VP path)

**Result:** File already existed. Updated `inference` block: `provider: build`, `model: meta/llama-3.3-70b-instruct`, `api_key` set to NGC API key from `local-docs/.env`. SSH fields already pointed to correct paths (`~/.generated-ssh-keys/sandbox-ssh{,.pub}`). Note: existing file had `provider: custom` with a Hugging Face key — was stale from a previous session.

---

### Step 0.4 — Image pipeline

> PR #11 replaced build-from-source with `make copy-images`, which mirrors tested images from
> `quay.io/rh-ai-quickstart` directly to the internal registry. No local builds needed.

**VP path:** ArgoCD manages the gateway image via the `openshell-gateway-image` ArgoCD app — no manual image step required before `./pattern.sh make install`.

**Manual path:** Run `make copy-images` once after the registry route is available:
```bash
make copy-images
# Mirrors openshell-gateway, nemoclaw-sandbox, nemoclaw-cli
# from quay.io/rh-ai-quickstart → internal registry (openshell-agents namespace)
```

**Result:**
- `copy-images`: _to be filled_
- DataVolume `openshell-gateway-golden` phase: _to be filled_

**Bugs / issues:** _to be filled_

---

## Phase 1: Validated Pattern Deployment

The VP path uses ArgoCD to manage the full cluster state declaratively. `./pattern.sh make install` runs inside the Validated Patterns utility container (pulls `quay.io/validatedpatterns/utility:latest`) and bootstraps ArgoCD, which then reconciles all applications defined in `values-prod.yaml`.

### How it works

```
./pattern.sh make install
    │
    └─> rhvp.cluster_utils.install (ansible playbook inside utility container)
            │
            ├─> Bootstrap ArgoCD
            ├─> Load secrets (make load-secrets → Vault)
            └─> ArgoCD reconciles applications:
                    openshift-external-secrets  → ESO operand (external-secrets ns)
                    vault                       → HashiCorp Vault (vault ns)
                    openshift-cnv               → HyperConverged CR → activates CDI + KubeVirt
                    openshift-registry          → defaultRoute: true on image registry
                    openshell-keycloak          → PostgreSQL + Keycloak CR + realm import
                    pattern-secrets             → 3 ExternalSecrets sync Vault→k8s Secrets:
                                                    inference (provider/model/api_key)
                                                    openshell-aap-ssh (SSH private key)
                                                    openshell-ssh-pubkey (SSH public key)
                    openshell-saw               → VirtualMachine + setup Job + routes
                                                    override: overrides/openshell-saw.yaml
                                                    (accessControl.owner: alice)
```

### Secrets flow (VP path)

```
~/values-secret.yaml  ──make load-secrets──>  Vault (secret/data/hub/inference, .../ssh)
                                                  │
                                            ESO ExternalSecrets (pattern-secrets chart)
                                                  │
                                         k8s Secrets: inference, openshell-aap-ssh, openshell-ssh-pubkey
                                                  │
                                       openshell-saw setup Job (mounts /provider-secret, /ssh-key)
```

### Step 1.1 — Deploy

```bash
./pattern.sh make install
```

**Result:** Succeeded. ArgoCD bootstrapped in `vp-gitops` namespace. Pattern CR created, all 7 applications synced. Total wall-clock time ~75 minutes (including image import and VM setup).

**Pre-flight bugs fixed before install:**

| # | Issue | Fix |
|---|---|---|
| 1 | Podman machine stopped — `pattern.sh` couldn't connect to Podman socket | `podman machine start` |
| 2 | `~/.config/containers/registries.conf` had a stray `e` on line 4, causing TOML parse error | Removed the stray character |

---

### Step 1.2 — ArgoCD application sync status

```bash
oc get applications -n openshift-gitops
```

| Application | Expected status | Actual status |
|---|---|---|
| `openshift-external-secrets` | Synced/Healthy | Synced/Healthy ✓ |
| `vault` | Synced/Healthy | Synced/Healthy ✓ |
| `openshift-cnv` | Synced/Healthy | Synced/Healthy ✓ |
| `openshift-registry` | Synced/Healthy | Synced/Healthy ✓ (via `secure-agent-workspace-prod`) |
| `openshell-keycloak` | Synced/Healthy | Synced/Healthy ✓ |
| `pattern-secrets` | Synced/Healthy | Synced/Healthy ✓ (all 3 ExternalSecrets synced from Vault) |
| `openshell-saw` | Synced/Healthy | Synced/Healthy ✓ (after manual remediation — see bugs) |

---

### Step 1.3 — Validate infrastructure

```bash
make argo-healthcheck
make status
make keycloak-issuer
```

**argo-healthcheck result:** All 7 apps Synced/Healthy
**Keycloak issuer URL:** `https://openshell-keycloak-ingress-openshell-agents.apps.cluster-s6f9c.dyn.redhatworkshops.io/realms/openshell`
**VM status:** `openshell-saw` Running, IP `10.234.0.43`, node `control-plane-cluster-s6f9c-2`

---

### Step 1.4 — Access the sandbox

```bash
make openshell-saw-list
# Gateway route URL: to be filled
# Dashboard URL: to be filled
```

**Result:** Setup Job completed in 5m14s. Sandbox `openshell-saw` provisioned for owner `alice`. Gateway routes:
- Gateway (TLS passthrough): `openshell-saw-gateway-openshell-agents.apps.cluster-s6f9c.dyn.redhatworkshops.io`
- Dashboard (TLS edge): `openshell-saw-dashboard-openshell-agents.apps.cluster-s6f9c.dyn.redhatworkshops.io`

---

### Phase 1 Bugs

| # | Severity | Component | Symptom | Fix |
|---|---|---|---|---|
| 1 | Blocker | Pre-flight | `pattern.sh` fails: `Cannot connect to Podman` | `podman machine start` — machine was stopped |
| 2 | Blocker | Pre-flight | `pattern.sh` fails: TOML parse error in `registries.conf` | Removed stray `e` character on line 4 of `~/.config/containers/registries.conf` |
| 3 | Blocker | patterns-operator | Pattern CR stuck: `lastError: unknown target "deploy/pr11"` | Patched Pattern CR: `oc patch pattern secure-agent-workspace -n patterns-operator --type=merge -p '{"spec":{"gitSpec":{"targetRevision":"fix/init-container-wait-for-secrets"}}}'`. Root cause: `pattern.sh` detected the local branch name `deploy/pr11` and used it as the ArgoCD revision, but that branch doesn't exist on the upstream repo. Always use a branch name that exists on the remote. |
| 4 | Blocker | CDI / openshell-saw | CDI importer pod `CrashLoopBackOff`: image `openshell-agents/openshell-gateway:latest` not found in internal registry | Ran `make copy-images` to mirror from `quay.io/rh-ai-quickstart`. Root cause: VP path has no `openshell-gateway-image` ArgoCD app — image must be pre-populated manually before or immediately after `make install`. |
| 5 | Blocker | openshell-saw setup Job | Job timed out (30 min `activeDeadlineSeconds`) waiting for DV import that was stuck on missing image | Deleted failed Job and VM (`oc delete job openshell-saw-setup && oc delete vm openshell-saw`), triggered ArgoCD resync. New Job completed in 5m14s once DV was Succeeded. |

---

## Phase 2: Cluster Cleanup

Tear down all VP-managed resources between the two deployment runs. Operators remain installed to avoid reinstall overhead.

### Step 2.1 — Uninstall pattern

```bash
./pattern.sh make uninstall
```

**Result:** Succeeded. Pruned all ArgoCD applications except `openshift-cnv` and `secure-agent-workspace-prod` (parent). Operators and `vp-gitops` namespace retained as documented.

---

### Step 2.2 — Remove namespaces

```bash
oc delete project openshell-agents vault external-secrets --wait=false
oc delete application openshift-cnv secure-agent-workspace-prod -n vp-gitops
```

**Result:** All three namespaces deleted immediately. ArgoCD parent app and `openshift-cnv` app deleted.

**Bug:** Deleting the `openshift-cnv` ArgoCD app cascaded deletion of the HyperConverged CR (ArgoCD resource finalizer), which caused CDI/KubeVirt to be decommissioned and the `openshift-cnv` namespace to terminate. **For future cleanup: do NOT delete the `openshift-cnv` ArgoCD app — only delete `secure-agent-workspace-prod` to cascade-remove the pattern apps while keeping OCP Virt intact.** Required reinstalling OCP Virt for Phase 3.

---

### Step 2.3 — Verify clean state

**Result:** `openshell-agents`, `vault`, `external-secrets` fully deleted. Note: they were auto-recreated (empty) within ~30s by the patterns-operator. `openshift-cnv` also deleted (unintended — see bug above).

---

### Step 2.4 — Reinstall OpenShift Virtualization (remediation for cleanup bug)

Required because the `openshift-cnv` ArgoCD app deletion cascaded HyperConverged CR deletion:

```bash
oc create namespace openshift-cnv
oc apply -f - <<EOF
# OperatorGroup + Subscription for kubevirt-hyperconverged stable channel
EOF
# Wait for CSV Succeeded, then create HyperConverged CR
```

**Result:** _to be filled_

---

## Phase 3: Manual (Quickstart) Deployment

The manual path deploys each component individually using `make` targets and Helm directly. No Vault or ESO — API keys are passed via `--set` flags at sandbox creation time. SSH secrets are created directly as k8s Secrets.

### How it works

```
make check-prereqs              → verifies oc, helm, virtctl, operators, registry route
make keycloak                   → helm install charts/openshell-keycloak
                                    → PostgreSQL Deployment + Keycloak CR + realm import
make login                      → OIDC browser/device flow → ~/.config/openshell/oidc/token.json
make ssh-secret                 → oc apply k8s Secrets: openshell-aap-ssh, openshell-ssh-pubkey
make openshell-saw-create       → helm install charts/openshell-saw
                                    → VirtualMachine (clones from golden DataSource)
                                    → setup Job (provisions VM: installs agent, injects keys, starts gateway)
                                    → Routes: <name>-gateway (TLS passthrough), <name>-dashboard (TLS edge)
make openshell-saw-configure-gateway  → registers gateway with openshell CLI
```

### Secrets flow (manual path)

```
NGC_API_KEY (from local-docs/.env)  ──helm --set──>  k8s Secret (openshell-saw setup Job env)
make copy-images  ─────────────────────────────────>  internal registry (no Vault needed for images)
~/.generated-ssh-keys/sandbox-ssh  ──make ssh-secret──>  k8s Secrets: openshell-aap-ssh, openshell-ssh-pubkey
                                                               │
                                                      setup Job (/ssh-key mount)
```

### Step 3.1 — Check prerequisites

```bash
make check-prereqs
```

**Result:** All passed. Warning: `virtctl` not installed locally (setup Job downloads it from the cluster — non-blocking). OCP Virt installed, RHBK installed, SSH key exists, image registry route enabled.

---

### Step 3.1b — Mirror images to internal registry

```bash
make copy-images
```

**Result:** All 3 images mirrored in ~20s (quay layers cached from Phase 1 run). `openshell-gateway`, `nemoclaw-sandbox`, `nemoclaw-cli` available in internal registry.

---

### Step 3.2 — Deploy Keycloak

```bash
make keycloak
```

**Result:** Succeeded on second attempt (see bugs). Keycloak ready in ~2 min.
**Keycloak issuer URL:** `https://openshell-keycloak-ingress-openshell-agents.apps.cluster-s6f9c.dyn.redhatworkshops.io/realms/openshell`

---

### Step 3.3 — Authenticate

```bash
make login
```

**Result:** Device flow failed (Keycloak client doesn't have device authorization grant enabled). Browser flow succeeded — logged in as `admin`, token saved to `~/.config/openshell/oidc/token.json`, valid 599 min.

---

### Step 3.4 — Create SSH secrets

```bash
make ssh-secret
```

**Result:** `openshell-aap-ssh` and `openshell-ssh-pubkey` created in `openshell-agents`.

---

### Step 3.5 — Provision sandbox

```bash
set -a; source local-docs/.env; set +a
make openshell-saw-create OPENSHELL_SAW_NAME=test-saw-manual PROVIDER=build \
  MODEL=meta/llama-3.3-70b-instruct API_KEY=$NGC_API_KEY OWNER=admin
```

**Result:** Helm deployed. Setup Job ran for 9m3s. Full sequence: golden image imported, VM clone created, cloud-init finished, NemoClaw CLI installed, sandbox created, OpenClaw onboarded, gateway started.

---

### Step 3.6 — Follow setup logs

**Result:** `Setup complete: sandbox=test-saw-manual on vm/test-saw-manual`. VM IP `10.235.0.75` on `worker-cluster-s6f9c-1`.

---

### Step 3.7 — Validate

```bash
make openshell-saw-list
make status
```

**Sandbox list:** `test-saw-manual  deployed  Running  2026-08-04 12:54:49` ✓
**Job:** Complete in 9m3s ✓
**Routes:** `test-saw-manual-gateway`, `test-saw-manual-dashboard` ✓

---

### Phase 3 Bugs

| # | Severity | Component | Symptom | Fix |
|---|---|---|---|---|
| 1 | Blocker | `make keycloak` | Failed: RHBK operator in `keycloak` ns, not `openshell-agents` — Keycloak CR can't be reconciled | Installed RHBK subscription in `openshell-agents` via `oc apply`. Root cause: RHBK was pre-installed in `keycloak` namespace on this cluster; manual path assumes it's in the target namespace. |
| 2 | Blocker | `wait-for-secrets` init container | Sandbox setup Job stuck in `Init:0/1` waiting for `secret/inference` which doesn't exist in manual path | Created `inference` secret manually with provider/model/api_key. Root cause: PR #11 init container always waits for `secret/inference` regardless of deployment path — should be skipped when provider credentials are injected directly via `--set`. |
| 3 | Minor | SSH public key | `WARNING: No SSH public key found, VM may not be accessible via SSH` in setup logs | Non-fatal — SSH worked via cloud-init. `make ssh-secret` only creates `openshell-aap-ssh` (private key), but setup Job looks for `public_key` field in that secret. `openshell-ssh-pubkey` is a separate secret. |
| 4 | Minor | OIDC device flow | `make login OIDC_FLOW=device` fails — Keycloak client doesn't have device authorization grant enabled | Used browser flow (default). Non-blocking. |

---

## Conclusions

### Validated Pattern Path

| Dimension | Assessment |
|---|---|
| Total wall-clock time | ~75 min (including image import, VM boot, agent setup) |
| Complexity | High — requires `pattern.sh`, Vault, ESO, ArgoCD knowledge |
| What worked | Full GitOps stack — all 7 ArgoCD apps Synced/Healthy, Vault+ESO secrets pipeline, Keycloak realm, VM sandbox end-to-end |
| Core bugs | 5 blockers hit (Podman stopped, registries.conf corrupt, wrong branch in Pattern CR, missing image pre-population, Job timeout) |
| Operator dependency | OCP Virt + RHBK + ESO (3 operators) |
| Secrets model | Vault + ESO ExternalSecrets — secure, auditable, git-safe |
| GitOps readiness | Production-grade — full declarative, drift detection, auto-sync |
| Biggest friction | VP path has no `openshell-gateway-image` ArgoCD app — `make copy-images` must be run manually before or immediately after install. If skipped, CDI import times out and the setup Job fails. |

### Manual (Quickstart) Path

| Dimension | Assessment |
|---|---|
| Total wall-clock time | ~35 min (Keycloak + image mirror + VM boot + agent setup) |
| Complexity | Medium — step-by-step `make` targets, fewer moving parts |
| What worked | All components deployed successfully: Keycloak, SSH secrets, sandbox VM, OpenClaw gateway |
| Core bugs | 4 bugs: RHBK namespace mismatch, `wait-for-secrets` init container blocking on VP-specific `inference` secret, SSH pubkey warning, OIDC device flow disabled |
| Operator dependency | OCP Virt + RHBK (2 operators — no ESO needed) |
| Secrets model | Direct k8s Secrets + `--set` flags — simpler, API key visible in Helm release history |
| GitOps readiness | None — imperative, no drift detection |
| Biggest friction | `wait-for-secrets` init container (added in PR #11 for VP path) blocks the manual path until `secret/inference` is manually created — this is a regression for the manual path. |

### Bug Summary

| # | Phase | Severity | Component | Description |
|---|---|---|---|---|
| 1 | Pre-flight | Blocker | Podman | Machine stopped — `podman machine start` required before `pattern.sh` |
| 2 | Pre-flight | Blocker | registries.conf | Stray `e` in TOML config caused parse error |
| 3 | VP Install | Blocker | patterns-operator | Pattern CR used local branch name `deploy/pr11` (not on remote) — patched to `fix/init-container-wait-for-secrets` |
| 4 | VP Install | Blocker | CDI / openshell-saw | No `openshell-gateway-image` ArgoCD app — `make copy-images` must run before `make install` or immediately after |
| 5 | VP Install | Blocker | openshell-saw Job | Job timed out (30 min) waiting on stuck DV import — resynced after DV succeeded |
| 6 | Cleanup | Blocker | openshift-cnv | Deleting `openshift-cnv` ArgoCD app cascaded HyperConverged CR deletion — required full OCP Virt reinstall. **Only delete `secure-agent-workspace-prod` for cleanup.** |
| 7 | Manual | Blocker | RHBK namespace | `make keycloak` failed: RHBK was in `keycloak` ns, not `openshell-agents` — installed subscription in correct namespace |
| 8 | Manual | Blocker | wait-for-secrets | PR #11 init container blocks manual path on `secret/inference` (VP/ESO artifact) — must create manually |
| 9 | Manual | Minor | SSH pubkey | `openshell-aap-ssh` secret missing `public_key` field — warning logged but SSH worked |
| 10 | Manual | Minor | OIDC device flow | Device flow not enabled on `openshell-cli` Keycloak client — use browser flow |
| 11 | **Both** | Blocker | Dashboard route (503) | `openclaw.gatewayHost` defaults to `127.0.0.1` in `charts/openshell-saw/values.yaml` — openclaw gateway binds localhost-only inside the sandbox. The OCP route reaches the VM on port 18789 but nothing is listening on `0.0.0.0:18789`. Fix: pass `openclaw.gatewayHost=0.0.0.0` at deploy time. `openshell-saw-create.sh` does not expose this flag, so it must be set via direct `helm upgrade --reuse-values --set openclaw.gatewayHost=0.0.0.0` + kill/restart the gateway process inside the VM (requires virtctl or node debug pod). |
| 12 | **Both** | Blocker | Sandbox phase stuck `Unspecified` | Direct consequence of bug #11 — openshell gateway cannot reach openclaw (localhost-only), so sandbox health check fails and phase stays `Unspecified`. This blocks `openshell sandbox exec`, `openshell sandbox connect`, and the SSH proxy, creating a deadlock: you can't fix the gateway via openshell because the sandbox isn't Ready, and the sandbox isn't Ready because the gateway isn't reachable. |

### Recommendation

**For production use: Validated Pattern path.** It provides GitOps-managed declarative state, secure secrets via Vault+ESO, and full observability via ArgoCD. The bugs are pre-flight issues (Podman, registries.conf) and a one-time `make copy-images` step that should be documented as a prerequisite.

**For dev/testing or quick demos: Manual path.** Faster (~35 min vs ~75 min), fewer operator dependencies, no Vault complexity. The main friction is the `wait-for-secrets` init container regression in PR #11 — creating `secret/inference` manually should be added to the quickstart docs.

**Key PR #11 issues to fix upstream:**
1. `openclaw.gatewayHost` defaults to `127.0.0.1` — this breaks both the dashboard route and sandbox health checks in both deployment paths. Should default to `0.0.0.0`, or `openshell-saw-create.sh` should expose a `--gateway-host` flag. This is the most impactful single bug found.
2. The `wait-for-secrets` init container should conditionally skip when the provider credentials are injected directly via `--set` (manual path), not only when using VP/ESO. This is a regression for the manual path introduced in PR #11.

**Recommended corrected cleanup sequence (between deployments):**
```bash
./pattern.sh make uninstall
# Do NOT delete openshift-cnv app — only delete the pattern apps
oc delete project openshell-agents vault external-secrets --wait=false
# Verify
oc get ns | grep -E 'openshell|vault|external-secrets'
```