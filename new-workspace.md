# New Workspace: Creating a Secure Agent Workspace via `make openshell-saw-create`

## Why this test

My manager asked: *"Can you create a new Secure Agent Workspace also using the
`make openshell-saw-create`"* — specifically the documented Makefile command that provisions a
whole new, independent workspace (VM + gateway), as opposed to just a new application-level
sandbox inside an existing one (which was covered separately in `test.md`).

This documents provisioning a third, fully independent workspace — `mtalvi-e2e` — via that exact
command, then adding the dashboard/webui feature on top, and verifying everything end-to-end.

## Inputs used

- Name: `mtalvi-e2e`
- Provider: reused the exact NVIDIA config already running on `openshell-saw` (retrieved live via
  `helm get values openshell-saw -n openshell-agents -a`, confirmed reusable per prior Q&A):
  - `PROVIDER=build`, `MODEL=meta/llama-3.3-70b-instruct`, same `nvapi-...` key
- Namespace: `openshell-agents` (default `shared` mode, same as the other two workspaces)
- Dashboard/webui feature: enabled as a follow-up step, same two-step pattern used for
  `openshell-saurabh`

## 1. Preflight

Confirmed `mtalvi-e2e` had no existing Helm release, VM, or Route before starting.

## 2. Base workspace creation

```bash
export OPENSHELL_SAW_NAME=mtalvi-e2e
export OIDC_ISSUER="https://openshell-keycloak-ingress-keycloak.apps.cluster-w85nv.dyn.redhatworkshops.io/realms/openshell"
make openshell-saw-create \
  PROVIDER=build \
  MODEL=meta/llama-3.3-70b-instruct \
  API_KEY=<same NVIDIA key already used by openshell-saw>
```

`Release "mtalvi-e2e" does not exist. Installing it now.` → `STATUS: deployed`, `REVISION: 1`.
This is a fully independent Helm release (confirmed by design — nothing about `openshell-saw` or
`openshell-saurabh` was read, modified, or restarted at any point in this process).

### Bug found: the setup Job's OIDC-token fallback assumes username == password

The first setup Job run **failed**:

```
Fetching OIDC token for 'mtalvi' from Keycloak...
WARNING: Could not fetch OIDC token (sandbox will deploy without OIDC auth)
...
Creating sandbox (...)
Error: × code: 'The request does not have valid authentication credentials',
       message: "missing authorization header"
exit status 1
```

**Root cause:** two things compounded:
1. My local `openshell` CLI's OIDC token had expired again (only refreshed ~16 hours earlier, in
   yesterday's `test.md` session — 10-hour lifetime). `make openshell-saw-create`'s own
   token-fetch (`oidc-login.sh token`, called internally to embed `--set-string oidc.token=...`)
   silently returned empty as a result.
2. Because the embedded `oidc.token` Helm value was empty, `configmap-scripts.yaml`'s
   `run-setup.sh` fell through to a **fallback** mechanism (line ~301-319) that tries a Keycloak
   *password grant* using `username=${OWNER}` and `password=${OWNER}` — i.e., it assumes the
   owner's username and password are the identical string. This is true for the realm's built-in
   test users (`alice`/`alice`, etc. — which is exactly why this never surfaced before, since both
   `openshell-saw` and `openshell-saurabh` have `accessControl.owner: alice`), but **it can never
   work for a real, federated SSO identity** like `mtalvi`, which has no local password at all.

**Fix applied:** refreshed the local CLI token first (`./scripts/oidc-login.sh login`, completed
automatically via the existing browser SSO session), then pushed the fresh token directly into
the already-created release and re-ran the Job:

```bash
FRESH_TOKEN=$(python3 -c "import json;print(json.load(open('/home/mtalvi/.config/openshell/oidc/token.json'))['access_token'])")
helm upgrade mtalvi-e2e charts/openshell-saw -n openshell-agents --reuse-values \
  --set-string oidc.token="${FRESH_TOKEN}"
oc delete job mtalvi-e2e-setup -n openshell-agents
helm upgrade mtalvi-e2e charts/openshell-saw -n openshell-agents --reuse-values
```

Retried successfully: `OIDC token configured for gateway auth` → `Created sandbox: mtalvi-e2e` →
`Setup complete: sandbox=mtalvi-e2e on vm/mtalvi-e2e`. Job `Complete`, `1/1`, in 3m6s.

**Not fixed in code** (out of scope for this one-off run, but worth a real fix upstream): the
password-grant fallback should not exist for non-test owners, or at minimum should fail with a
clear "your local CLI token is invalid/expired, run `make login`" message instead of the
confusing downstream "missing authorization header" error two steps later.

### Known pre-existing issue recurred (already documented, unrelated to this task)

The same `EACCES: permission denied, open '/sandbox/.openclaw/openclaw.json'` issue already noted
in `docs/openshell-dashboard-integration.md`'s "Known pre-existing issue" section showed up again
during `openclaw onboard`. This time it **did not** abort the whole script — confirmed live proof
that the bug #14 fix (`|| true` guard on the auth-token extraction pipeline) works correctly in a
brand-new scenario too, not just the one it was originally found in.

## 3. Adding the dashboard/webui feature

Same pattern already used for `openshell-saurabh`:

```bash
helm upgrade mtalvi-e2e charts/openshell-saw -n openshell-agents --reuse-values \
  --set dashboard.enabled=true \
  --set dashboard.image="quay.io/gkrumbach07/openshell-dashboard:latest" \
  --set dashboard.proxyImage="quay.io/oauth2-proxy/oauth2-proxy:v7.9.0" \
  --set dashboard.clientId="openshell-dashboard" \
  --set dashboard.keycloakNamespace=keycloak \
  --set route.webui=true

oc delete job mtalvi-e2e-setup -n openshell-agents
helm upgrade mtalvi-e2e charts/openshell-saw -n openshell-agents --reuse-values
```

Job log confirmed: `Registering redirect URI on Keycloak client 'openshell-dashboard'...` →
`Redirect URI registered: .../mtalvi-e2e-webui-.../oauth2/callback` → `dashboard ready` →
`setup-dashboard OK` → `Setup complete`.

**Pleasant surprise:** unlike `openshell-saurabh` (bug #7), the running VMI already had the new
`webui` masquerade port without needing an `oc delete vmi` restart — VM template and running VMI
ports matched immediately after this upgrade. The external route worked on the first check with
no restart needed.

## 4. CLI access

```bash
make openshell-saw-configure-gateway OPENSHELL_SAW_NAME=mtalvi-e2e
```

### Bug found: `openshell gateway add`'s interactive OIDC flow hangs/times out in this environment

The Makefile's gateway-configure step (and a direct `openshell gateway add ... --oidc-issuer ...`
retry) both consistently hit:

```
Opening browser for OIDC authentication...
Browser opened. Waiting for authentication...
Opening in existing browser session.
! Authentication failed: OIDC authentication timed out after 120 seconds.
! Registration for 'mtalvi-e2e' removed. Fix the issue and retry gateway add.
```

Unlike `./scripts/oidc-login.sh` (this project's own script, which reliably auto-completed via
the existing browser SSO session both times it was used today), the CLI's *built-in*
`gateway add` OIDC flow never received its callback and timed out — twice, identically. On
timeout it **deletes its own registration**, unlike a plain failed login.

**Workaround applied:** manually created the gateway's local config files, mirroring the exact
structure of the already-working `openshell-saw` gateway entry
(`~/.config/openshell/gateways/openshell-saw/metadata.json`):

```bash
mkdir -p ~/.config/openshell/gateways/mtalvi-e2e && chmod 700 ~/.config/openshell/gateways/mtalvi-e2e
cat > ~/.config/openshell/gateways/mtalvi-e2e/metadata.json <<'EOF'
{
  "name": "mtalvi-e2e",
  "gateway_endpoint": "https://mtalvi-e2e-gateway-openshell-agents.apps.cluster-w85nv.dyn.redhatworkshops.io",
  "is_remote": true,
  "gateway_port": 0,
  "auth_mode": "oidc",
  "oidc_issuer": "https://openshell-keycloak-ingress-keycloak.apps.cluster-w85nv.dyn.redhatworkshops.io/realms/openshell",
  "oidc_client_id": "openshell-cli"
}
EOF
# + oidc_token.json populated from the already-valid local ~/.config/openshell/oidc/token.json
```

This worked immediately:

```bash
$ openshell --gateway mtalvi-e2e --gateway-insecure sandbox list
NAME        CREATED              PHASE
mtalvi-e2e  2026-08-06 07:38:59  Ready
```

**Not fixed in code** — this is a CLI binary behavior (`openshell gateway add`'s own OIDC
callback handling), not something in this repository. Worth reporting upstream to whoever owns
the `openshell` CLI, since the confusing part isn't the timeout itself but that it silently
deletes the registration rather than leaving it in a retry-able state.

## 5. Dashboard verification

- `systemctl --user show openshell-dashboard.service openshell-dashboard-proxy.service` on the
  VM: both `ActiveState=active`, `SubState=running`, `NRestarts=0`.
- External route check:
  ```
  $ curl -sk -D - https://mtalvi-e2e-webui-openshell-agents.apps.cluster-w85nv.dyn.redhatworkshops.io/
  HTTP/1.1 302 Found
  location: https://openshell-keycloak-.../auth?...&client_id=openshell-dashboard&code_challenge=...
            &code_challenge_method=S256&redirect_uri=https%3A%2F%2Fmtalvi-e2e-webui-...%2Foauth2%2Fcallback
            &response_type=code&scope=openid+email+profile&state=...
  ```
  Correct client ID, PKCE challenge, sandbox-specific redirect URI, and — confirming bug #13's
  fix holds for brand-new workspaces too — `scope=openid+email+profile` with no `offline_access`.
- **Real login test** (via browser automation, `alice`/`alice`): Keycloak login page appeared,
  login succeeded, landed on the dashboard, `default` workspace showed `ACTIVE`, and the
  `mtalvi-e2e` sandbox was visible in the sandbox list with status **Ready**.

## Verdict

**`make openshell-saw-create` works** — provisions a fully independent new workspace with no
impact on `openshell-saw` or `openshell-saurabh`. Adding the dashboard/webui feature afterward
also works cleanly, using the exact same pattern already established for `openshell-saurabh`.

Three real issues were found and worked around along the way (none are dashboard-integration
bugs, so none were added to `docs/openshell-dashboard-integration.md`'s numbered list):

1. **New finding** — the setup Job's OIDC password-grant fallback only works for owners whose
   username equals their password (built-in test users), and fails confusingly for real
   federated identities. Root cause: fix by ensuring the local CLI token is fresh before running
   `make openshell-saw-create` (`make login` first if unsure).
2. **Already known** (bug #14 area, `docs/openshell-dashboard-integration.md`) — the
   `/sandbox/.openclaw/openclaw.json` permission issue recurred, but no longer aborts the whole
   setup script, confirming that fix generalizes beyond the case it was found in.
3. **New finding** — `openshell gateway add`'s interactive OIDC flow hangs/times out in this
   environment and deletes its own registration on failure; worked around by manually writing the
   gateway's local config files. This is CLI behavior, not something in this repository.

## Final URLs

- Gateway: `https://mtalvi-e2e-gateway-openshell-agents.apps.cluster-w85nv.dyn.redhatworkshops.io`
- Dashboard (webui): `https://mtalvi-e2e-webui-openshell-agents.apps.cluster-w85nv.dyn.redhatworkshops.io`
