# Secure Agent Workspace — Deployment Planning

> This document is written by a deployment agent in real time.
> Static sections (prerequisites, commands) are pre-populated from repo analysis.
> Result/status fields are filled in during each phase's execution.
>
> Written after reviewing [hadar-info.md](hadar-info.md) and PR #11's review thread
> (https://github.com/validatedpatterns-sandbox/secure-agent-workspace/pull/11).
> Two proactive fixes were applied before this run based on Hadar's bug list:
> (1) patched the OpenClaw gateway bind mode, later superseded by an identical upstream
> fix merged mid-session; (2) pushed the working branch to a remote before running
> `pattern.sh` to avoid the "unknown target" Pattern CR bug. A mid-session merge of 5 new
> upstream commits (PR #11 review-feedback fixes) also changed the plan significantly —
> see the "Mid-session branch update" section.

---

## Environment

| Field | Value |
|---|---|
| OCP version | 4.22.6 |
| Cluster API | `https://api.cluster-w85nv.dyn.redhatworkshops.io:6443` |
| Cluster domain | `cluster-w85nv.dyn.redhatworkshops.io` |
| Nodes | 3 control-plane, 2 worker |
| Date of run | 2026-08-04 |
| Inference provider | NVIDIA Build (`build`) |
| Inference model | `meta/llama-3.3-70b-instruct` |
| API key source | Provided directly by the user during the session |
| Branch | `deployment-test`, based on `fix/init-container-wait-for-secrets` (PR #11), pushed to `origin` (fork `mtalvi/secure-agent-workspace`) |
| Identity used | `mtalvi` (real SSO account, not a built-in `alice`/`admin` test user) |

---

## Mid-session branch update

Partway through this run, `fix/init-container-wait-for-secrets` was updated upstream with 5 new
commits responding to PR review feedback from `yuvalk` and `Hadar301`. These were fetched and
merged into `deployment-test` (one trivial conflict — flag order on an identical `--bind lan` fix).
Key changes and their effect on this run:

| Upstream change | Effect |
|---|---|
| `job.waitForSecrets` (default `false`, `true` in `overrides/openshell-saw.yaml`) makes the `wait-for-secrets` init container conditional | **Eliminated** the need to manually pre-create the `inference` secret for the manual path (Hadar's bug #8) — verified by rendering the chart both ways. |
| `openclaw gateway run --bind lan ...` (identical to a fix already applied locally) | Landed upstream too, confirming it as directionally correct — but see the deeper finding below; it does **not** fully fix the dashboard route. |
| Removed dead `OPENCLAW_GATEWAY_HOST`/`openclaw.gatewayHost` plumbing | Cleanup; no functional impact. |
| Shell-injection quoting + secret-name regex validation in the init container | Security hardening; no impact (our secret names already comply). |
| OIDC token dir/file now `chmod 700`/`600` | Minor hardening; no impact. |

---

## Phase 0: Shared Prerequisites

### Step 0.1 — Verify CLI login and prereqs

**Result:** Logged in as `mtalvi` @ `https://api.cluster-w85nv.dyn.redhatworkshops.io:6443`. OCP
4.22.6, 5 nodes. RHBK operator pre-installed, but in the `keycloak` namespace (not
`openshell-agents`) — same mismatch Hadar hit. No CNV, no `openshell-agents`/`vault`/
`external-secrets` namespaces — a clean slate.

### Step 0.2 — Generate SSH keys

**Result:** Keys already existed at `~/.generated-ssh-keys/sandbox-ssh{,.pub}`. No action needed.

### Step 0.3 — Configure `~/values-secret.yaml`

**Result:** File existed but was stale (`provider: custom`, a Red Hat MaaS key from a previous
session — same pattern Hadar noted). Updated to `provider: build`, `model:
meta/llama-3.3-70b-instruct`, `api_key: <NVIDIA key, provided interactively by the user>`.

### Step 0.4 — Proactive fix applied before any deploy

Patched the gateway startup line in
[charts/openshell-saw/templates/configmap-scripts.yaml](charts/openshell-saw/templates/configmap-scripts.yaml)
to add `--bind lan` to `openclaw gateway run`, based on Hadar's dashboard-503 bug report — this
was later found to be superseded by an identical upstream fix (see above), and was itself found
to be **necessary but not sufficient** (see Phase 1 finding below).

---

## Phase 1: Validated Pattern Deployment

### Step 1.1 — Deploy

Before running install, the branch was pushed to `origin` (`mtalvi/secure-agent-workspace`) —
proactively avoiding Hadar's "Pattern CR picks up local branch name" bug, since `pattern.sh`
derives `target_branch`/`target_origin` from the local git checkout and the ArgoCD repo-server
needs that ref to actually be fetchable.

```bash
git push -u origin deployment-test
VALUES_SECRET=~/values-secret.yaml ./pattern.sh make install
```

**Result:** Succeeded (exit code 0). Ansible logs explicitly confirmed: *"Validating that branch
'deployment-test' is reachable on remote repo ... All assertions passed"* — the proactive push
worked. `make copy-images` was run manually in parallel (Hadar's "biggest friction" bug — VP path
has no `openshell-gateway-image` ArgoCD app) while ArgoCD's health-check retry loop ran; all 7
apps reached Synced/Healthy at attempt 25/60 (one transient flap on the parent
`secure-agent-workspace-prod` app self-resolved).

### Step 1.2 — Verify

**Result:** All 7 ArgoCD apps Synced/Healthy. VM `openshell-saw` Running/Ready. Gateway and
dashboard routes created.

### Step 1.3 — Dashboard route finding (refines Hadar's bug #11/#12)

**Result:** Dashboard route returned `503`. Root-caused precisely by SSHing directly into the VM
(via a temporary debug pod using the pod-network IP and the ESO-synced `openshell-aap-ssh`
secret):

- `podman ps` on the VM host showed the NemoClaw sandbox container publishes **only**
  `0.0.0.0:<random>->2222/tcp` (SSH, used internally by `openshell sandbox exec`/`ssh-proxy`).
  Port `18789` — where `openclaw-gateway` actually listens (confirmed alive and healthy in the
  process list) — is **never published to the VM host at all**, regardless of its bind mode
  inside the container's own network namespace.
- This is why the *documented* access method, `make openshell-saw-gui`
  ([scripts/openshell-saw-gui.sh](scripts/openshell-saw-gui.sh)), works: it tunnels via SSH
  through the gateway's `ssh-proxy` straight into the sandbox's own loopback, never touching the
  VM's host network for port 18789.
- **Conclusion:** the `openshell-saw-dashboard` OpenShift Route is fundamentally incompatible
  with the current sandbox networking model. Neither Hadar's suggested fix
  (`openclaw.gatewayHost=0.0.0.0`) nor the upstream `--bind lan` fix (now merged) resolves this —
  both only affect binding *inside* the sandbox container's own namespace, not whether the port
  is published to the VM host. A real fix needs either upstream OpenShell CLI support for
  publishing additional sandbox container ports, or a custom host-side tunnel daemon. Verified
  this is still broken later, after the fix, on the manual-path deployment too (see Phase 3).

---

## Phase 2: Cluster Cleanup

### Step 2.1 — Uninstall pattern (this is where it went seriously wrong)

```bash
./pattern.sh make uninstall
```

**Result:** Got stuck indefinitely. The Pattern CR reported `deletionPhase: DeleteHubChildApps`,
`lastError: "waiting 1 hub child applications to be removed"`, referring to the `openshift-cnv`
Application.

**Following the corrected sequence from Hadar's Recommendation section (don't delete the
`openshift-cnv` app) was not sufficient here** — the finalizer removal step below revealed that
ArgoCD's own automatic uninstall logic (using a `global.deletePattern: DeleteChildApps` Helm
parameter) had **already started cascading deletion into `openshift-cnv`'s child resources**
*before* any manual intervention — CSV/Subscription, `kubemacpool-service`,
`kubevirt-operator-webhook`, `hco-webhook-service` were already gone. This is a strictly worse
version of Hadar's bug #6: his mitigation (avoid manually deleting the `openshift-cnv`
Application) doesn't help once ArgoCD's own uninstall tooling does the cascading automatically.

Removing the stuck ArgoCD finalizer (`resources-finalizer.argocd.argoproj.io/foreground`) on the
`openshift-cnv` Application unstuck the *Application object*, but the underlying
`openshift-cnv` namespace was left in `Terminating` phase indefinitely, blocked by:

1. Orphaned CRs with finalizers whose owning controller no longer existed:
   `migcontrollers.migrations.kubevirt.io`, `ssps.ssp.kubevirt.io`, then (after those cleared)
   the `HyperConverged` and `KubeVirt` CRs themselves, then a stale `CDI` CR.
2. The `HyperConverged` CRD's `spec.conversion.strategy: Webhook` pointed at the now-deleted
   `hco-webhook-service`, meaning **any** read/patch/delete of the CR failed outright — had to
   temporarily flip the CRD's conversion strategy to `None` to even touch the object.
3. Several `ValidatingWebhookConfiguration`/`MutatingWebhookConfiguration` objects
   (`virt-api-validator`, `virt-operator-validator`, `virt-template-validator`,
   `cdi-api-*`, `kubemacpool-mutator`, etc.) pointed at deleted services and blocked *all*
   further API operations on KubeVirt/CDI resources cluster-wide.
4. Stale `APIService` aggregated-API registrations (`v1.subresources.kubevirt.io`,
   `v1alpha3.subresources.kubevirt.io`, `v1beta1.upload.cdi.kubevirt.io`) pointed at deleted
   services, blocking the namespace controller's discovery step needed to confirm full deletion.

**Fix (full recovery sequence):**
```bash
# Strip finalizers on orphaned CRs blocking namespace termination
oc patch migcontrollers.migrations.kubevirt.io migcontroller-kubevirt-hyperconverged -n openshift-cnv --type=merge -p '{"metadata":{"finalizers":[]}}'
oc patch ssps.ssp.kubevirt.io ssp-kubevirt-hyperconverged -n openshift-cnv --type=merge -p '{"metadata":{"finalizers":[]}}'
oc patch crd hyperconvergeds.hco.kubevirt.io --type=json -p '[{"op":"replace","path":"/spec/conversion","value":{"strategy":"None"}}]'
oc patch hyperconverged kubevirt-hyperconverged -n openshift-cnv --type=merge -p '{"metadata":{"finalizers":[]}}'
oc patch kubevirt kubevirt-kubevirt-hyperconverged -n openshift-cnv --type=merge -p '{"metadata":{"finalizers":[]}}'
# Delete orphaned webhook configs (backing services gone)
oc delete validatingwebhookconfigurations cdi-api-dataimportcron-validate cdi-api-datavolume-validate cdi-api-populator-validate cdi-api-validate objecttransfer-api-validate virt-api-validator virt-operator-validator virt-template-validator
oc delete mutatingwebhookconfigurations cdi-api-datavolume-mutate cdi-api-pvc-mutate kubemacpool-mutator kubevirt-ipam-controller-mutating-webhook-configuration virt-api-mutator
# Delete stale APIServices
oc delete apiservices v1.subresources.kubevirt.io v1alpha3.subresources.kubevirt.io v1beta1.upload.cdi.kubevirt.io
# Namespace then fully terminates. Recreate namespace + operator:
oc apply -f - <<EOF   # Namespace, OperatorGroup, Subscription (channel: stable, source: redhat-operators)
EOF
# Wait for CSV Succeeded, then:
oc apply -f - <<EOF   # fresh HyperConverged CR, spec: {}
EOF
# HCO then got stuck again on a stale leftover CDI CR (148min old, phase=Error) and a set of
# orphaned cluster-scoped RBAC/CRDs from the *original* CNV install (cluster-scoped resources
# survive namespace deletion since they're not namespaced):
oc patch cdi cdi-kubevirt-hyperconverged --type=merge -p '{"metadata":{"finalizers":[]}}'   # operator recreates it fresh, healthy
oc delete clusterrole migrations.kubevirt.io:storagemigrate kubevirt-migration-controller migrations.kubevirt.io:admin migrations.kubevirt.io:edit migrations.kubevirt.io:storagemigrate-multins migrations.kubevirt.io:view
oc delete clusterrolebinding kubevirt-migration-sa
oc delete crd virtualmachinestoragemigrations.migrations.kubevirt.io multinamespacevirtualmachinestoragemigrationplans.migrations.kubevirt.io multinamespacevirtualmachinestoragemigrations.migrations.kubevirt.io virtualmachinestoragemigrationplans.migrations.kubevirt.io
```

**Result:** After ~40 minutes of recovery, `HyperConverged` reached
`Available=True, Progressing=False, Degraded=False`, CSV `Succeeded`, all pods `Running`. Fully
recovered with zero data loss (no VMs existed at the time of the botched uninstall).

---

## Phase 3: Manual (Quickstart) Deployment

Per guidance to focus on the manual path only for this run.

### Step 3.1 — Check prerequisites and mirror images

```bash
make check-prereqs
make copy-images
```

**Result:** All checks passed. Images already present in the internal registry from Phase 1 and
survived the namespace's ArgoCD-managed-resource pruning (they were created directly via
`oc image mirror`, not Helm/ArgoCD) — re-ran `copy-images` anyway to confirm; took ~12 min
(slower than Hadar's cached ~20s, since layer caching didn't carry over the same way in this
environment).

### Step 3.2 — Deploy Keycloak

```bash
make keycloak KEYCLOAK_NS=keycloak
```

**Result:** Succeeded first try (~65s) by pointing at the RHBK operator's actual namespace
directly — simpler than Hadar's fix of installing a duplicate RHBK subscription in
`openshell-agents`; this fallback is already built into
[scripts/deploy-keycloak.sh](scripts/deploy-keycloak.sh).

### Step 3.3 — Authenticate

```bash
OIDC_ISSUER="https://openshell-keycloak-ingress-keycloak.apps.cluster-w85nv.dyn.redhatworkshops.io/realms/openshell" make login
```

**Result:** Browser-based login required (device-code flow confirmed disabled for the
`openshell-cli` client — same as Hadar's bug #10, despite `realm-import.yaml` setting
`oauth2.device.authorization.grant.enabled: "true"`, worth investigating further). Logged in as
real SSO identity `mtalvi` (not a built-in test user) — **this choice directly caused three new
blockers documented below**, since `mtalvi` starts with none of the `openshell-user`/
`openshell-admin` realm roles that the built-in test users get for free via
`realm-import.yaml`.

### Step 3.4 — Create SSH secrets

```bash
make ssh-secret
```

**Result:** `openshell-aap-ssh` and `openshell-ssh-pubkey` created.

### Step 3.5 — Provision sandbox (3 new blockers hit in sequence)

```bash
make openshell-saw-create OPENSHELL_SAW_NAME=openshell-saw PROVIDER=build \
  MODEL=meta/llama-3.3-70b-instruct API_KEY=<key> OWNER=mtalvi
```

**Attempt 1 — kubemacpool webhook error:** Failed immediately with
`failed calling webhook "mutatevirtualmachines.kubemacpool.io" ... service "kubemacpool-service"
not found` — direct fallout from the still-broken CNV at that point in time (before Phase 2's
recovery completed). Resolved once CNV recovery finished.

**Attempt 2 — Keycloak role missing:** After CNV recovery, failed inside the setup Job with
`role 'openshell-user' required`. Granted via `kcadm.sh` (executed inside the `openshell-keycloak-0`
pod, `HOME=/tmp` needed since the pod's real `$HOME` isn't writable):
```bash
oc exec -n keycloak openshell-keycloak-0 -- env HOME=/tmp /opt/keycloak/bin/kcadm.sh \
  add-roles -r openshell --uusername mtalvi --rolename openshell-user
```
Token refresh (`oidc-login.sh refresh`) failed with this client's config; required a fresh
interactive `make login` to pick up the new role claim.

**Attempt 3 — workspace membership missing:** Failed again with
`not a member of workspace 'default'; ask a platform admin to run: openshell workspace member add
--workspace 'default' --subject '<uuid>' --role user`. Investigated directly on the VM
(`openshell workspace list` → "No workspaces found"; `openshell workspace create` →
`role 'openshell-admin' required`). This is a **separate, gateway-level authorization model on
top of Keycloak realm roles**, and bootstrapping it on a freshly created gateway needs
`openshell-admin`. Granted that role too, refreshed login again, then (after manually pushing the
refreshed access token onto the VM's local gateway config, since the Job's baked-in token was
stale) bootstrapped:
```bash
oc exec ... kcadm.sh add-roles -r openshell --uusername mtalvi --rolename openshell-admin
# on the VM:
openshell --gateway-insecure workspace create --name default   # already existed, auto-created earlier
openshell --gateway-insecure workspace member add --workspace default --subject <uuid> --role admin
```

**Result after all three fixes:** `make openshell-saw-create` re-run succeeded end-to-end.
**Note:** each retry required manually `oc delete job openshell-saw-setup` first — Kubernetes
`Job` specs are immutable, so `helm upgrade`/`--reuse-values` silently left the old failed Job in
place without recreating it.

### Step 3.6 — Configure the CLI and verify

```bash
OIDC_ISSUER="https://openshell-keycloak-ingress-keycloak.apps.cluster-w85nv.dyn.redhatworkshops.io/realms/openshell" \
  make openshell-saw-configure-gateway OPENSHELL_SAW_NAME=openshell-saw NS=openshell-agents
```

**Result:** Without an explicit `OIDC_ISSUER`, this target's auto-detection looks for a
`Keycloak` CR in `NS` (defaults to `openshell-agents`) — since ours is in `keycloak`, detection
silently failed, `OIDC_OPTS` stayed empty, and `openshell gateway add` defaulted to an
interactive "cloud" edge-authenticated flow (browser confirmation code) instead of direct OIDC —
the same root-cause pattern as Hadar's bug #7, but hitting a *different* Makefile target that
`KEYCLOAK_NS` doesn't cover. Passing `OIDC_ISSUER` explicitly fixed it (`Auth: oidc` confirmed in
`openshell gateway list`).

```bash
openshell --gateway-insecure sandbox list
# openshell-saw  2026-08-04 17:48:05  Ready
openshell --gateway-insecure sandbox exec -n openshell-saw --no-tty -- echo "hello from sandbox"
# hello from sandbox
make openshell-saw-gui OPENSHELL_SAW_NAME=openshell-saw
# OpenClaw UI: http://localhost:18789/#token=... (token fetched successfully, tunnel started)
curl -sk https://openshell-saw-dashboard-openshell-agents.apps.cluster-w85nv.dyn.redhatworkshops.io/
# HTTP 503 (expected — see Phase 1 finding, confirmed still broken post-fix)
```

**Result:** Sandbox phase `Ready`. `sandbox exec` works. The documented SSH-tunnel GUI access
method (`make openshell-saw-gui`) works correctly. The direct dashboard Route still 503s, exactly
as predicted by the Phase 1 root-cause finding.

---

## Bug Summary

| # | Phase | Severity | Component | Description |
|---|---|---|---|---|
| 1 | VP Install | Blocker (avoided) | patterns-operator | Pattern CR uses local git branch name as ArgoCD revision — avoided by pushing the branch to `origin` before running `pattern.sh` (same root cause as Hadar's bug #3, different mitigation). |
| 2 | VP Install | Blocker | CDI / openshell-saw | No `openshell-gateway-image` ArgoCD app — `make copy-images` must run manually. Same as Hadar's bug #4. |
| 3 | **Both** | Blocker (root-caused, not fully fixed) | Dashboard route (503) | Refines Hadar's bug #11/#12: the sandbox container only ever publishes port 2222 (SSH) to the VM host; port 18789 (OpenClaw) is never published, regardless of bind mode. Neither Hadar's suggested fix nor the merged upstream `--bind lan` fix resolves this. The documented `make openshell-saw-gui` (SSH-tunnel) access method works correctly instead. |
| 4 | Cleanup | **Critical** (escalates Hadar's bug #6) | openshift-cnv | `./pattern.sh make uninstall`'s own automatic `DeleteChildApps` logic cascaded deletion into `openshift-cnv`'s child resources (CSV, webhooks, pods) *before* any manual `oc delete application` was run — Hadar's mitigation ("don't manually delete the app") is insufficient. Full recovery required: stripping finalizers on 2 orphaned namespaced CRs, temporarily disabling a CRD's webhook conversion strategy, stripping finalizers on `HyperConverged`/`KubeVirt`/`CDI` CRs, deleting 13 orphaned webhook configs and 3 stale `APIService` registrations, then fully recreating the namespace + operator + CR, then cleaning up 7 more orphaned cluster-scoped RBAC/CRD objects left from the original install. ~40 min of recovery, zero data loss since no VMs existed yet. |
| 5 | Manual | Blocker | RHBK namespace | Same as Hadar's bug #7 — fixed more simply via `make keycloak KEYCLOAK_NS=keycloak` (already supported by `scripts/deploy-keycloak.sh`) rather than installing a duplicate operator. |
| 6 | Manual | Resolved upstream | wait-for-secrets | Hadar's bug #8 fixed by the mid-session upstream merge (`job.waitForSecrets`, default `false`). No manual secret pre-creation needed anymore. |
| 7 | Manual | **Blocker (new)** | Keycloak realm role | A freshly authenticated real SSO user (not a built-in `alice`/`admin` test account) has **no** `openshell-user`/`openshell-admin` realm role by default. Sandbox creation fails with `role 'openshell-user' required`. Nothing in the repo automates granting this. Fixed via `kcadm.sh add-roles` + a fresh login (token refresh doesn't pick up new role claims with this client config). |
| 8 | Manual | **Blocker (new)** | Gateway workspace bootstrap | A separate, gateway-level authorization model requires membership in a `default` workspace, independent of Keycloak roles. Bootstrapping the very first workspace on a fresh gateway itself requires `openshell-admin`. Undocumented anywhere in the repo or PR review. |
| 9 | Manual | Minor (new) | Setup Job retry | Kubernetes `Job`s are immutable — after any failure requiring a retry, `helm upgrade`/`--reuse-values` silently leaves the old failed Job in place. Must `oc delete job openshell-saw-setup` before retrying. |
| 10 | Manual | Minor (new) | `configure-gateway` OIDC auto-detection | `openshell-saw-configure-gateway`'s OIDC-issuer auto-detection looks for `Keycloak` in `NS` (default `openshell-agents`); with RHBK in a different namespace it silently falls back to an interactive "cloud" edge-auth flow instead of OIDC. `KEYCLOAK_NS` doesn't cover this target — needs explicit `OIDC_ISSUER`. Same root cause as bug #5/#7 but a different, uncovered code path. |
| 11 | Manual | Minor | SSH pubkey | Same as Hadar's bug #9 — non-fatal, SSH still worked. |
| 12 | Manual | Minor | OIDC device flow | Same as Hadar's bug #10 — device flow rejected despite realm config appearing to enable it (`oauth2.device.authorization.grant.enabled: "true"`); worth further investigation. |
| 13 | Docs | Trivial (new) | NOTES.txt / helm output | Deploy output suggests `make gui`/`make tui`, but the actual targets are `make openshell-saw-gui`/`make openshell-saw-tui`. |

---

## Conclusions

### Validated Pattern Path

| Dimension | Assessment |
|---|---|
| Deploy | Succeeded cleanly once the branch was pushed to a real remote first. |
| Cleanup | **Currently unsafe** — `./pattern.sh make uninstall` can itself cascade-delete `openshift-cnv`'s live resources before any operator intervention is possible. Needs a fix upstream (e.g., excluding `openshift-cnv` from `DeleteChildApps`, or documenting a pre-uninstall step to detach it) before it can be considered safe to run against a shared/persistent cluster. |
| Dashboard | Broken by architecture, not by config — see bug #3. |

### Manual (Quickstart) Path

| Dimension | Assessment |
|---|---|
| What worked | Every component deployed successfully once the identity/authorization gaps were closed: Keycloak, SSH secrets, sandbox VM, OpenClaw gateway, `sandbox exec`, SSH-tunnel GUI access. |
| Core new friction | **Using a real (non-test-user) identity surfaces two undocumented authorization bootstrap steps** (Keycloak realm roles, gateway workspace membership) that the built-in `alice`/`admin` test users get for free via `realm-import.yaml`. This is invisible if you only ever use the documented test accounts. |
| Biggest single improvement needed | Automate first-run bootstrap: either seed the deploying user's realm roles + workspace membership as part of `make openshell-saw-create`/`configure-gateway`, or clearly document the exact `kcadm.sh`/`openshell workspace` commands needed for a non-test-user identity. |

### Recommendation

**Use the manual path with a built-in test-user identity (`alice`/`admin`) for quick
demos** — it avoids the entire Keycloak-role/workspace-bootstrap rabbit hole documented above.
If deploying with a real corporate SSO identity, budget time for the manual `kcadm.sh` role
grants and `openshell workspace member add` bootstrap (~10 extra minutes once you know the exact
commands, which are now captured in bug #7/#8 above).

**Do not run `./pattern.sh make uninstall` against a cluster where you need to preserve
OpenShift Virtualization**, until the `DeleteChildApps` cascade-into-`openshift-cnv` behavior is
fixed upstream — recovery is possible (documented step-by-step above) but takes ~40 minutes and
requires cluster-admin-level surgery (finalizer stripping, CRD conversion-strategy patching,
webhook/APIService cleanup).

**Key items worth raising on PR #11 / as new issues:**
1. The `--bind lan` fix (already merged) does not actually fix the dashboard route — the real
   gap is one level deeper (sandbox container port publishing). Recommend either removing/fixing
   the `openshell-saw-dashboard` Route until this is properly supported, or documenting
   `make openshell-saw-gui` as the only supported dashboard access method.
2. `./pattern.sh make uninstall`'s `DeleteChildApps` behavior needs a guard for `openshift-cnv`
   (or any Application whose deletion has wide blast radius) — this is a data-loss-adjacent
   footgun for any real deployment.
3. Non-test-user identities need an automated or documented bootstrap path for Keycloak realm
   roles and gateway workspace membership.
