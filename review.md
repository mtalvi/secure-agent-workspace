# PR #79 live review

- PR: [Remove the per-SAW prepare Job](https://github.com/validatedpatterns-sandbox/secure-agent-workspace/pull/79)
- Branch: `sauagarwa/secure-agent-workspace` `feat/remove-prepare-job` at `524c88b`
- Cluster: `cluster-ltkqg` (`https://api.cluster-ltkqg.dyn.redhatworkshops.io:6443`), OpenShift 4.22.15, one control-plane worker, default storage Ceph RBD
- Date: 2026-10-07
- Install path: Option A (Validated Pattern). The README section "Validating the deployment" was run after install, not as a second install method.
- Command:

```bash
cd /home/mtalvi/secure-agent-workspace-pr79
export TARGET_BRANCH=feat/remove-prepare-job
export TARGET_ORIGIN=pr79
make generate-keys
make copy-images
./pattern.sh make install
```

Argo CD tracked `https://github.com/sauagarwa/secure-agent-workspace.git` at `feat/remove-prepare-job`. Keycloak for this pattern is namespace `saw-keycloak`, not `openshell-keycloak`. The platform Keycloak already in namespace `keycloak` was left in place.

NVIDIA `API_KEY` was taken from `/home/mtalvi/secure-agent-workspace/.env` and written to `~/.nvidia-api-key`. Brave used a dummy value in `~/.brave-api-key.saw-review`. Web search was not exercised.

## Checklist

| Check | Result |
| --- | --- |
| 1. Clean pattern install, Argo CD synced | Pass |
| 2. Redirect registrar in `saw-keycloak` | Pass |
| 3. No prepare Job; CDI import; VM Running; installer Done | Pass after one VM restart. First boot apply Failed |
| 4. OIDC login, dashboard, sandbox connectivity, TUI | Pass for login, dashboard, and sandbox exec. Host TUI failed |
| 5. User namespace cannot read Keycloak admin credentials | Pass |
| 6. `make test-installer` | Fail: 3 failed, 824 passed. The failing file is not in this PR |

## 1. Pattern install

`./pattern.sh make install` exited 0. On the successful health check (attempt 17/60) every application was Synced and Healthy:

`secure-agent-workspace-prod`, `openshift-cnv`, `openshift-external-secrets`, `vault`, `openshell-keycloak`, `openshell-rhdh`, `governance-policy`, `governance-interceptor`, `saw-users`, `saw-alice`, `saw-alice-bom`, `saw-alice-secrets`.

`copy-images` could not mirror `quay.io/rh-ai-quickstart/openshell-gateway:0.0.116` or `:v0.0.116` (`error: an error occurred during planning`) and fell back to `:latest`. That tag was copied into the internal registry and tagged `latest`.

The pre-existing `~/values-secret-secure-agent-workspace.yaml` did not contain the `keycloak-users` or `rhdh-oidc` sections that this branch's `values-secret.yaml.template` generates. The first secret load injected only ssh, inference, and web-search. Keycloak's ExternalSecret then failed with `could not get secret data from provider`, and because that secret is sync-wave `-1`, Argo CD did not create the Keycloak CR or the registrar until those two generated secrets were added and `make load-secrets` was run again (5 secrets injected). That is a stale local secrets file, not a defect in the branch template. After the second load, `openshell-keycloak` synced and became Healthy.

## 2. Redirect registrar

`saw-redirect-registrar` in `saw-keycloak` is Running, ServiceAccount `saw-redirect-registrar`.

Init container (`bootstrap`) log:

```text
[redirect-registrar] client saw-redirect-registrar created
[redirect-registrar] saw-redirect-registrar may manage the clients of realm openshell
[redirect-registrar] saw-redirect-registrar ready
```

Registrar container log:

```text
[redirect-registrar] route hosts must be under .apps.cluster-ltkqg.dyn.redhatworkshops.io
[redirect-registrar] openshell-dashboard: 3 SAW redirect URIs; added https://alice-cuda-dev-cuda-sandbox-ui.apps.cluster-ltkqg.dyn.redhatworkshops.io/oauth2/callback, https://alice-default-notebook-ui.apps.cluster-ltkqg.dyn.redhatworkshops.io/oauth2/callback, https://alice-webui-saw-alice.apps.cluster-ltkqg.dyn.redhatworkshops.io/oauth2/callback
```

Those three routes are the ones labelled `saw.redhat.com/oidc-redirect=true`. `alice-dashboard` is a separate route and is not labelled; the OpenShell dashboard UI is `alice-webui`.

The registrar container does not mount `openshell-keycloak-initial-admin`. That secret is mounted only by the `bootstrap` init container. The registrar's ClusterRole is list `routes`, list `namespaces`, and get `ingresses`.

## 3. Workspace and CDI

Namespace `saw-alice` has VM `alice` and no `*-prepare` Job, ServiceAccount, Role, or RoleBinding. No `*-prepare-anyuid` or `*-registry-reader` ClusterRoleBinding was created. The only new cluster binding of this kind is `saw-redirect-registrar-saw-keycloak`.

DataVolume `alice-root` source is the internal registry image `openshell-agents/openshell-gateway:latest`, `pullMethod: node`. Phase `Succeeded`, progress `100%`. VM `alice` is `Running` / Ready.

CDI times on this Ceph RBD node (40 GiB disk, image about 0.78 GiB):

| Event | Time (UTC) |
| --- | --- |
| DataVolume created; importer unschedulable (control-plane taint) | 09:08:37 |
| Import scheduled | 09:13:30 |
| Golden image pulled (4.809 s, 834872946 bytes) and import in progress | 09:13:46 |
| Import complete / succeeded | 09:14:04 / 09:14:05 |

End to end create to Succeeded: 5m 28s. About 4m 53s of that was the importer waiting on the node taint. After the pod was scheduled, the pull plus import was about 35s, of which the pull was 4.8s and the import itself about 18s. A `ClaimMisbound` warning appeared while the CDI prime PVC and `alice-root` shared a volume; the final PVC is Bound and the VM is running.

Installer status after the VM was restarted once secrets existed:

```json
{
  "apply": { "phase": "Done", "message": "", "updatedAt": "2026-10-07T09:44:57+00:00" },
  "install": { "phase": "Done", "bom": "openshell-0-1-2-rhaiv-0", "updatedAt": "2026-10-07T09:41:50+00:00" }
}
```

First boot, before that restart, apply was Failed. See Issues.

## 4. Authentication and README validation

- `make login` completed in the browser. Result: `Logged in as: alice`.
- `openshell gateway login alice` completed. Result: `Authenticated to gateway 'alice' as alice`. `openshell whoami` shows name `alice`, provider `oidc`, roles `openshell-user` and `openshell-admin`.
- OpenShell dashboard at `https://alice-webui-saw-alice.apps.cluster-ltkqg.dyn.redhatworkshops.io/` loaded signed in as `alice`, with workspaces `default` and `cuda-dev` both ACTIVE. No `redirect_uri_mismatch`.
- Notebook UI at `https://alice-default-notebook-ui.apps.cluster-ltkqg.dyn.redhatworkshops.io/chat/main` loaded as `alice` (model `nvidia/nemotron-3-super-120b-a12b`). The first paint said the control UI bundle had not started; a few seconds later the chat UI was up. No `redirect_uri_mismatch`.
- Sandbox list from the gateway's own CLI (`openshell 0.1.2-rhaiv.0` inside the VM): `notebook` Ready, `cuda-sandbox` Ready.
- `openshell sandbox exec` from that CLI printed `openclaw-exec-ok` and `nemoclaw-exec-ok`.
- Host `make openclaw-tui` failed before a session. The workstation CLI is `openshell 0.0.103+rhaiv.0`. The gateway is 0.1.2, and the old CLI cannot decode `workspace_scope` (`failed to decode Protobuf message: GetSandboxRequest.workspace_scope: unexpected end group tag`). The same error breaks `openshell sandbox list` on the workstation. `make nemoclaw-tui` uses that same CLI, so it was not run separately.

## 5. Security

- `saw-alice` secrets are `alice-cloudinit`, `alice-ssh-pubkey`, `inference`, `web-search`, `openshell-aap-ssh`, `openshell-ssh-pubkey`, plus the default dockercfg and pipeline secrets. There is no `*-initial-admin` secret.
- `oc auth can-i get secrets -n saw-keycloak --as=system:serviceaccount:saw-alice:default` is `no`.
- The registrar process does not see the master admin secret. Only the init container mounts it, and only long enough to create client `saw-redirect-registrar`.

## 6. Tests

`make test-installer` from the PR worktree, with Helm 3.19.0:

```text
3 failed, 824 passed in 826.27s
FAILED tests/installer/test_harness_mount.py::test_a_running_gateway_is_kept_when_nothing_changed
FAILED tests/installer/test_harness_mount.py::test_a_refill_replaces_the_running_gateway
FAILED tests/installer/test_harness_mount.py::test_changed_settings_replace_the_running_gateway
```

A second run of those three tests failed again. `git diff HEAD~8 -- tests/installer/test_harness_mount.py` on `524c88b` is empty, so this PR does not change that file. One captured error was `sh: /proc/<pid>/cmdline: No such file or directory`. Treat these as failures of the current tree in this environment, not as a change introduced by the eight PR commits.

## Issues

### 1. First boot apply fails until the VM is restarted (blocks merge)

The VM became Ready at 09:14:10. Secrets `inference` and `web-search` were created at 09:21:53. The guest secret disks are iso9660 and were frozen at 09:14:06, before those secrets existed. `/run/saw/secrets/inference` had the secret-volume directories and no key files. At 09:34:09, well after the secrets existed in the API, apply was still:

```text
phase: Failed
message: credential for provider 'nvidia' in workspace 'cuda-dev' not found: Secret 'inference' key 'api_key'. List the Secret in openshell-saw additionalProviderSecrets and make sure it exists.
```

`virtctl restart` at 09:41 rebuilt the disks with `api_key` present (71 bytes) and `web-search` present (16 byte dummy). The next apply was `Done` at 09:44:57.

This is the race left by removing the prepare Job's wait for secrets. A fresh workspace whose VM starts before External Secrets has written the provider secrets does not recover on its own. Every new Option A user hits a Failed apply until someone restarts the VM.

### 2. Workstation CLI cannot drive this gateway (does not block this PR)

`make openclaw-tui` and `openshell sandbox list` on the laptop fail because the installed CLI is 0.0.103 and the gateway BOM is `openshell-0-1-2-rhaiv-0`. The matching 0.1.2 CLI inside the VM lists and execs both sandboxes. README asks for a CLI from the gateway's 0.1.x series.

### 3. Three installer tests fail outside this diff (does not block this PR)

See section 6. They fail repeatedly here and are unchanged by these eight commits.

### Residual risk, not a fresh-install failure

SAW apps do not prune. An upgrade from a cluster that still has `*-prepare` Jobs, ServiceAccounts, and cluster bindings will leave those objects behind. This cluster never had them, so that path was not tested. The fresh install itself did not create them.

## Test limitation

Brave was the dummy value `dummy-brave-key`. Web search was not called. The NVIDIA key came from `.env` `API_KEY`. The OpenClaw UI shows model `nvidia/nemotron-3-super-120b-a12b`.

## Verdict

Do not merge yet.

The security change holds on this cluster: nothing in `saw-alice` can read Keycloak's admin secret, the registrar runs as its own client, CDI imported the root disk, and dashboard plus notebook UI sign-in completed without `redirect_uri_mismatch`.

The prepare Job's secret wait was not replaced. On this fresh install the VM snapshotted empty credential disks and `apply` stayed `Failed` until the VM was restarted by hand. That will hit every new workspace that boots before External Secrets has written `inference`. Fix that race, then this is safe to merge.
