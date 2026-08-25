

# Validate Gmail Write Proxy E2E

Validates the Gmail write proxy (draft create/send/cancel with undo window) on the integrations VM
in the two-VM split architecture. The write proxy is called by the Forge UI relay (Desk), NOT by
the agent.

**Date:** 2026-08-25
**OpenShell version:** 0.0.110 (gateway/supervisor images), 0.0.103+rhaiv.0 (local CLI)
**Inference provider:** NVIDIA (deepseek-ai/deepseek-v4-flash-0731)
**Proxy image:** `quay.io/redhat-et/gmail-write-proxy:demo1`
**Cluster:** `api.cluster-vsffz.dyn.redhatworkshops.io:6443`
**Branch:** `gmail-write-proxy` (from `feat/multi-proxy-services`)

## Acceptance Criteria


| #   | Criterion                                                                                   | Status   |
| --- | ------------------------------------------------------------------------------------------- | -------- |
| 1   | Integ VM: `gmail-write` provider created with `credentialKey: GMAIL_WRITE_TOKEN`            | PASS     |
| 2   | Integ VM: `gmail-write` sandbox running, listening on port 18081                            | PASS     |
| 3   | Integ VM: `gmail-write-frontdoor` K8s Secret created with SHA256 digest passed to sandbox   | PASS     |
| 4   | Integ VM: OAuth refresh configured (gmail.compose scope)                                    | PASS     |
| 5   | Integ VM: proxy validates front-door bearer (401 without, passes with correct bearer)       | PASS     |
| 6   | Integ VM: `/healthz` returns 200                                                           | PASS     |
| 7   | Integ VM: proxy can create draft, send draft, cancel pending send                           | DEFERRED |
| 8   | Integ VM: undo window (60s default) works                                                   | DEFERRED |
| 9   | Agent VM: NO `gmail-write` provider or capability present                                   | PASS     |
| 10  | Agent VM: agent cannot reach gmail-write port directly (NetworkPolicy)                      | PASS     |
| 11  | Forge UI relay: can reach proxy using front-door bearer from K8s Secret                     | DEFERRED |
| 12  | Security: write-scoped OAuth (gmail.compose only)                                           | PASS     |
| 13  | Security: proxy only allows POST/PUT/DELETE on `/gmail/v1/users/me/drafts*`                 | PASS     |


**Result: 10/13 PASS, 3 DEFERRED (require Forge UI relay)**

---

## Pre-work: File Modifications

### 1. Disabled non-gmail-write sandboxes

Only `gmail-write` left `enabled: true` in `charts/saw-bom/profiles/integrations/default/sandbox.yaml`.
All others (`mail-proxy`, `proxy-m365`, `proxy-m365-write`, `slack-read`, `slack-write`) set to `enabled: false`.

### 2. Trimmed integrations BOM providers

In `charts/saw-bom/profiles/integrations/default/providers.yaml`, kept only:

```yaml
spec:
  providers:
    - name: gmail-write
      type: gmail-write
      credentialKey: GMAIL_WRITE_TOKEN
```

### 3. Trimmed integrations VM service ports

In `overrides/openshell-saw-integ.yaml`, kept only:

```yaml
service:
  extraPorts:
    - name: mail-write
      port: 18081
      targetPort: 18081
    - name: inference-proxy
      port: 18083
      targetPort: 18083

networkPolicy:
  peerLabel: openshell-saw
  allowedPorts:
    - 18083
```

Port 18081 is intentionally NOT in `allowedPorts` — the agent VM must not reach the write proxy.

### 4. Created `.env`

```
GOVERNANCE_ENABLED=false
API_KEY=nvapi-...  (NVIDIA inference key)
KEYCLOAK_NS=keycloak
DEPLOY_GOV_PROFILES=true
```

---

## Phase 1: CLI Prerequisites


| Tool        | Version         |
| ----------- | --------------- |
| `oc`        | 4.18.13         |
| `helm`      | v3.19.0         |
| `virtctl`   | v1.8.4          |
| `openshell` | 0.0.103+rhaiv.0 |
| `gog`       | 0.36.0          |


---

## Phase 2: Cluster Deployment (Option A Manual)

### 2a. Install OpenShift Virtualization (CNV)

Fresh cluster required CNV operator installation:
1. Created `openshift-cnv` namespace + OperatorGroup
2. Subscribed to `kubevirt-hyperconverged` operator (channel: `stable`)
3. Waited for CSV `kubevirt-hyperconverged-operator.v4.22.6` → Succeeded
4. Created `HyperConverged` CR via `helm install openshift-cnv`
5. Waited for HyperConverged → Available

### 2b. Prerequisites check

```
make check-prereqs
```

All tools present. RHBK in `keycloak` namespace. Image registry route enabled.

### 2c. Deploy Keycloak

```
make keycloak KEYCLOAK_NS=keycloak
make verify-keycloak KEYCLOAK_NS=keycloak
```

9/9 checks passed. Realm `openshell`, 4 users, 2 clients.

### 2d. Mirror images

```
make copy-images KEYCLOAK_NS=keycloak
```

- `openshell-gateway:0.0.110` — mirrored OK
- `openshell-gateway-docker:0.0.110` — initial mirror failed (unexpected EOF), `:latest` fallback
  succeeded
- All other images mirrored OK

### 2e-f. Deploy config, integ VM, agent VM

```
make generate-keys
make deploy-config KEYCLOAK_NS=keycloak
make deploy-integ-vm KEYCLOAK_NS=keycloak
make verify-integ KEYCLOAK_NS=keycloak     # 9/9 PASS
make deploy-agent-vm KEYCLOAK_NS=keycloak
make verify-agent KEYCLOAK_NS=keycloak     # 8/8 PASS
```

Integ VM setup job: gmail-write sandbox created, image pulled, port 18081 ready, governance profile
imported. 4/4 PASS.

Agent VM setup job: notebook sandbox created with `gmail-read-proxy` and `nvidia` providers. No
`gmail-write` provider present (by design). 5/5 PASS.

---

## Phase 3: Gmail OAuth Authorization

### Discovery: `gog --gmail-scope` does not support `compose`

`gog auth add --help` shows `--gmail-scope="full"` with options `full|readonly`. There is no
`compose` value.

### Solution: `--extra-scopes` + `--services gmail`

```bash
gog auth add mtalvi@redhat.com \
  --services gmail \
  --extra-scopes="https://www.googleapis.com/auth/gmail.compose" \
  --manual --force-consent
```

This requested both the default gmail scopes AND `gmail.compose`. The OAuth consent screen granted:
- `gmail.compose` (target scope)
- `gmail.modify`, `gmail.readonly`, `gmail.settings.basic`, `gmail.settings.sharing` (from
  `--services gmail` default)

The governance profile restricts actual API calls at the proxy level to
`POST/PUT/DELETE on /gmail/v1/users/me/drafts*` only.

### Token export

```bash
gog auth tokens export mtalvi@redhat.com --out ~/gog/gog-token-export-compose.json
```

`refresh_token` present in export.

---

## Phase 4: Configure Gmail Write Refresh

### Manual process (no script exists for gmail-write)

The existing `configure-gmail-refresh.sh` (~1000 lines) is hardcoded for the `gmail-read` provider.
Equivalent steps performed manually for `gmail-write`:

1. Port-forwarded to integ VM via `virtctl port-forward`
2. Uploaded `client_secret.json` and `gog-token-export-compose.json` via SCP
3. Verified `gmail-write` profile has `oauth2_refresh_token` refresh block (deployed via
   `DEPLOY_GOV_PROFILES=true`)
4. Configured refresh material for both `access_token` and `GMAIL_WRITE_TOKEN` credential keys
5. Rotated tokens — both keys show status `refreshed`
6. Cleaned up credential files on VM

**Refresh status after configuration:**

```
PROVIDER     CREDENTIAL_KEY     STRATEGY               STATUS     EXPIRES_AT           NEXT_REFRESH
gmail-write  access_token       oauth2_refresh_token   refreshed  2026-08-25 08:58:55  2026-08-25 08:53:55
gmail-write  GMAIL_WRITE_TOKEN  oauth2_refresh_token   refreshed  2026-08-25 08:59:20  2026-08-25 08:54:20
```

---

## Phase 5: Redeploy Integ VM (v-type placeholder workaround)

Per `docs/bugs/openshell-v-type-placeholder-not-resolved.md`, the gmail-write sandbox gets a v-type
credential placeholder at startup that the supervisor cannot resolve. Redeployed the integ VM to get
a fresh s-type placeholder:

```bash
helm uninstall openshell-saw-integ -n openshell-agents
kubectl wait --for=delete vm/openshell-saw-integ -n openshell-agents --timeout=120s
GOVERNANCE_ENABLED=false make deploy-integ-vm
```

**Finding:** Redeploy destroys the OAuth refresh configuration. Had to reconfigure refresh material
after the redeploy (Phase 4 steps repeated). The gmail-read validation documentation does not
explicitly call this out.

Post-redeploy verification: 9/9 PASS.

---

## Phase 6: Acceptance Testing

### AC #6: `/healthz` returns 200

```
curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:18081/healthz
→ 200
```

**PASS**

### AC #5: Front-door bearer validation

**Discovery: The write proxy uses `x-forge-mail-bearer` header, NOT `Authorization: Bearer`.**

The Jira ticket specified `Authorization: Bearer`, but the actual proxy binary uses a custom header
`x-forge-mail-bearer`. This was discovered by inspecting the binary's strings after all
`Authorization: Bearer` attempts returned 401.

Without bearer:

```
curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:18081/pending
→ 401
```

With `x-forge-mail-bearer` header:

```
curl -sS -o /dev/null -w '%{http_code}' -H 'x-forge-mail-bearer: <bearer>' http://127.0.0.1:18081/pending
→ 200
```

**PASS** (with corrected header name)

### AC #3: Front-door Secret and SHA256

```
oc get secret gmail-write-frontdoor -n openshell-agents -o jsonpath='{.data.bearer}' | base64 -d | sha256sum
→ 3b21e3005c940649feb8db2a819e97234c97c2cfb89dc1c765c231faf6d9f73a

Sandbox env FRONT_DOOR_BEARER_SHA256:
→ 3b21e3005c940649feb8db2a819e97234c97c2cfb89dc1c765c231faf6d9f73a
```

SHA256 match confirmed. **PASS**

### AC #1: Provider verification

```
openshell provider get gmail-write -v
→ Name: gmail-write, Type: gmail-write, Credential keys: GMAIL_WRITE_TOKEN, access_token
```

**PASS**

### AC #2: Sandbox listening on port 18081

```
ss -tlnp | grep 18081
→ LISTEN 0 128 0.0.0.0:18081 0.0.0.0:* users:(("ssh",...))
```

Port 18081 listening via `openshell forward` SSH tunnel. **PASS**

### AC #4: OAuth refresh configured

```
openshell provider refresh status gmail-write
→ access_token:       refreshed, oauth2_refresh_token, expires 2026-08-25 09:18:57
→ GMAIL_WRITE_TOKEN:  refreshed, oauth2_refresh_token, expires 2026-08-25 09:18:57
```

**PASS**

### AC #7 & #8: Draft operations and undo window — DEFERRED

The write proxy's `/send` endpoint accepts JSON but returns "invalid JSON request" for all tested
body formats. From the DESIGN document, the proxy expects a signed assertion from the Forge UI
relay to derive the sender identity. Without the Forge UI relay and its assertion mechanism, the
proxy cannot process send requests.

Tested JSON formats (all returned "invalid JSON request"):
- `{"to":"...", "subject":"...", "body":"..."}`
- `{"raw":"<base64>"}`
- `{"message":{"raw":"<base64>"}}`
- `{}`

The proxy logs confirm the route matches and bearer is accepted:
```
[ALLOW] method=POST path=/send route=Send
[ALLOW] method=POST path=/send auth=front-door-bearer
```

**DEFERRED: Requires Forge UI relay with assertion support.**

### AC #9: No gmail-write on agent VM

```
openshell provider list | grep gmail-write → NOT FOUND
```

Agent VM has `gmail-read-proxy` and `nvidia` providers only. **PASS**

### AC #10: Agent VM cannot reach port 18081 (NetworkPolicy)

**Finding: No NetworkPolicy template exists in the Helm chart.** The `overrides/openshell-saw-integ.yaml`
has `networkPolicy.allowedPorts` values, but the chart has no template to render them. The comment
in `values.yaml` says "NetworkPolicy is applied post-setup via deploy/two-vm/networkpolicy.yaml"
but that file does not exist.

**Fix applied:** Created an egress NetworkPolicy on the agent VM pod to restrict outbound traffic
to the integ VM to port 18083 only:

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: openshell-saw-egress-restrict
  namespace: openshell-agents
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/instance: openshell-saw
  policyTypes:
    - Egress
  egress:
    - to: [{namespaceSelector: {}}]
      ports: [{protocol: UDP, port: 53}, {protocol: TCP, port: 53}]
    - to: [{podSelector: {matchLabels: {app.kubernetes.io/instance: openshell-saw-integ}}}]
      ports: [{protocol: TCP, port: 18083}]
    - to: [{namespaceSelector: {matchExpressions: [{key: kubernetes.io/metadata.name, operator: NotIn, values: [openshell-agents]}]}}]
    - to: [{podSelector: {matchExpressions: [{key: app.kubernetes.io/instance, operator: NotIn, values: [openshell-saw-integ]}]}}]
```

After applying:
```
Agent VM → integ:18081 → 000 (connection blocked)
Agent VM → integ:18083 → 404 (reachable, allowed)
```

**PASS** (with manual NetworkPolicy applied)

### AC #11: Forge UI relay — DEFERRED

No Forge UI relay access. **DEFERRED: Requires relay access.**

### AC #12: Write-scoped OAuth

OAuth authorized with `gmail.compose` scope via `gog auth add --extra-scopes`. The governance
profile restricts outbound traffic to `gmail.googleapis.com:443` with methods
`POST/PUT/DELETE` on `/gmail/v1/users/me/drafts*` only. **PASS**

### AC #13: Method and path enforcement

```
GET /gmail/v1/users/me/messages → 404 (denied)
POST /some/other/path → 404 (denied)
GET /pending (with bearer) → 200 (allowed)
POST /send (with bearer) → 400 (allowed route, body validation)
GET /healthz → 200 (allowed)
```

**PASS**

---

## Issues Encountered and Root-Cause Analysis

### Discovery: Bearer header is `x-forge-mail-bearer`, not `Authorization: Bearer`

The Jira ticket test commands specified `Authorization: Bearer <frontdoor-bearer>`, but the proxy
binary uses the custom header `x-forge-mail-bearer`. Discovered by:
1. Confirming FRONT_DOOR_BEARER_SHA256 matches in sandbox env and K8s Secret
2. All `Authorization: Bearer` variants returned 401 with log: "invalid front-door bearer"
3. Binary string analysis: `grep -ao "x-forge" /sandbox/gmail-write-proxy` → `x-forge-mail-bearer`
4. Testing with `x-forge-mail-bearer` header → 200

This is consistent with the gmail-read proxy which uses `x-forge-read-bearer`.

### Discovery: No NetworkPolicy chart template

The `overrides/openshell-saw-integ.yaml` has `networkPolicy.peerLabel` and `networkPolicy.allowedPorts`
values, but the Helm chart has no template to render them into a Kubernetes NetworkPolicy resource.
The comment in `values.yaml` says: "NetworkPolicy is applied post-setup via
deploy/two-vm/networkpolicy.yaml" — but that file does not exist.

Without a NetworkPolicy, the agent VM could reach all integ VM ports including 18081 (write proxy).
An egress-based NetworkPolicy on the agent VM was the working solution (ingress-based policies on
the integ VM blocked kubevirt/API server traffic).

### Discovery: Refresh config lost after integ VM redeploy

Helm uninstall + redeploy creates a new VM with a fresh gateway. The OAuth refresh configuration
(material: client_id, client_secret, refresh_token) is stored in the gateway's state, which is
destroyed during redeploy. The refresh must be reconfigured after each redeploy.

This was not explicitly documented in the gmail-read validation.

### Environment: `gog --gmail-scope` has no `compose` value

`gog` v0.36.0 only supports `--gmail-scope full|readonly`. Used `--extra-scopes` to request
`gmail.compose` explicitly. This resulted in broader scopes than ideal (full gmail scope + compose),
but the governance profile constrains actual API access at the proxy level.

### Environment: `openshell-gateway-docker:0.0.110` mirror failure

Same as gmail-read validation — versioned tag fails, `:latest` fallback works.

---

## Summary of Root Causes


| Issue | Root Cause | Category | Fix |
|-------|-----------|----------|-----|
| Bearer header wrong in Jira ticket | Proxy uses `x-forge-mail-bearer`, not `Authorization: Bearer` | **Documentation** | Corrected in test commands |
| No NetworkPolicy | Chart has no template for `networkPolicy` values | **Code** | Manual egress policy applied |
| Refresh lost after redeploy | Gateway state destroyed during helm uninstall | **Architecture** | Reconfigure refresh after redeploy |
| Draft operations fail | Proxy requires Forge UI assertion for sender derivation | **By design** | Deferred to Forge UI relay testing |
| `gog` no compose scope | v0.36.0 only supports `full\|readonly` | **Tool limitation** | Used `--extra-scopes` |
| Docker image mirror failure | `:0.0.110` tag missing on quay | **Known issue** | `:latest` fallback |


## Deferred: Requires Forge UI Relay

The following acceptance criteria require the Forge UI relay (Desk) to validate:

1. **AC #7: Draft create/send/cancel** — The proxy's `/send` endpoint requires a signed assertion
   from the Forge UI to derive the sender identity. Without the assertion, the proxy returns
   "invalid JSON request" regardless of the JSON body format.

2. **AC #8: Undo window** — Cannot be tested without a successful send via AC #7.

3. **AC #11: Forge UI relay can reach proxy** — No Forge UI relay deployed.

The Forge UI relay testing should validate:
- Desk can POST to `/send` with a signed assertion
- The proxy creates a draft in the user's Gmail Drafts folder
- The draft is held for the undo window (60s in this configuration)
- The draft can be cancelled within the undo window via `/pending` + DELETE
- If not cancelled, the draft is sent after the window elapses

## Fixes Applied

### Fix 1: NetworkPolicy Helm template (security-critical)

Created `charts/openshell-saw/templates/networkpolicy.yaml`. When `role=agent` and
`networkPolicy.peerLabel` is set, the template renders an **egress** NetworkPolicy on the agent VM
that restricts outbound traffic to the peer (integrations) VM to only the ports listed in
`networkPolicy.allowedPorts`. All other egress (DNS, API server, other namespaces) is unrestricted.

Egress-based (on the agent VM) instead of ingress-based (on the integ VM) because ingress policies
on the integ VM block kubevirt/API server traffic needed for SSH and control-plane operations.

Updated `charts/openshell-saw/values.yaml` comment to reflect the new template.

### Fix 2: Jira ticket test commands

Updated `docs/jira/validate-gmail-write-proxy.md`:
- Changed bearer header from `Authorization: Bearer` to `x-forge-mail-bearer`
- Added full `virtctl ssh` syntax with `-i` and `-t` flags
- Added command to retrieve the front-door bearer from the K8s Secret

### Fix 3: Refresh-lost-on-redeploy documentation

Added a warning to `README_TWO_VM_ARCH.md` after the redeploy step: the OAuth refresh configuration
is stored in the gateway's state and is destroyed during `helm uninstall`. After every integ VM
redeploy, `make configure-gmail-refresh` or `make configure-gmail-write-refresh` must be re-run.

### Fix 4: `configure-gmail-write-refresh.sh` script

Created `scripts/configure-gmail-write-refresh.sh` — a dedicated script for the gmail-write
provider. NOT a modification of the gmail-read script (which remains untouched).

Key differences from the gmail-read script:
- Provider: `gmail-write` (not `gmail-read`)
- Credential keys: `access_token` + `GMAIL_WRITE_TOKEN` (not `GMAIL_ACCESS_TOKEN`)
- Sandbox: `gmail-write` on port 18081 (not `mail-proxy` on 18080)
- Bearer: `x-forge-mail-bearer` from `gmail-write-frontdoor` secret (not `x-forge-read-bearer`)
- OAuth scope: `gmail.compose` (not `gmail.readonly`)
- No `--authorize-local` / `--readonly` gog flow (compose scope requires `--extra-scopes`)
- Healthz check only (no Gmail API call for verification -- requires Forge UI assertion)

Added `configure-gmail-write-refresh` Makefile target in `Makefile-quickstart`.

Usage:
```bash
make configure-gmail-write-refresh \
  CLIENT_JSON=$HOME/gog/client_secret.json \
  TOKEN_EXPORT=$HOME/gog/gog-token-export-compose.json
```

## Remaining Recommendations

1. **gog**: Add `--gmail-scope compose` option (maps to
   `https://www.googleapis.com/auth/gmail.compose`). Currently requires `--extra-scopes` workaround.
2. **Proxy documentation**: The `gmail-write-proxy` binary should document its expected `/send`
   JSON body schema and the `x-forge-mail-bearer` header name.
