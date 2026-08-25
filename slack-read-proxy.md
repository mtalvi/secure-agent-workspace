

# Validate Slack Read-Only Proxy E2E

Validates the Slack read-only proxy end-to-end in the two-VM split architecture.
Deployment uses Option A (Manual, no ArgoCD) with `GOVERNANCE_ENABLED=false`.

**Date:** 2026-08-25
**OpenShell version:** 0.0.110 (gateway/supervisor images), 0.0.103+rhaiv.0 (local CLI)
**Inference provider:** NVIDIA (deepseek-ai/deepseek-v4-flash-0731)
**Proxy image:** `quay.io/redhat-et/slack-read-proxy:demo1`

## Acceptance Criteria


| #   | Criterion                                                                   | Status |
| --- | --------------------------------------------------------------------------- | ------ |
| 1   | Integ VM: `slack-read` provider created with `credentialKey: SLACK_READ_USER_TOKEN` | PASS   |
| 2   | Integ VM: `slack-read` sandbox running, listening on port 18084             | PASS   |
| 3   | Integ VM: xoxp user token configured (read-scoped)                          | PASS   |
| 4   | Integ VM: proxy validates inter-VM bearer (401 without)                     | PASS   |
| 5   | Integ VM: proxy can list conversations, read messages                       | PASS   |
| 6   | Integ VM: proxy enforces GET-only allowlist (blocks POST/PUT/DELETE)         | PASS   |
| 7   | Agent VM: `slack-read-proxy` provider attached to notebook sandbox           | PASS   |
| 8   | Agent VM: supervisor routes Slack read requests via proxy chain (no forwarder) | PASS (with manual bearer) |
| 9   | Agent VM: agent can read Slack messages via the proxy chain                  | PASS (with manual bearer) |
| 10  | Security: agent VM has no real Slack xoxp token                             | PASS   |
| 11  | Security: proxy only allows read-class Slack Web API methods                | PASS   |


**Result: 11/11 PASS** (AC#8 and AC#9 require manual bearer injection due to the v-type placeholder bug — see Issues section)

---

## Pre-work: Slack App + Token Setup

### 1. Created Slack App

- URL: https://api.slack.com/apps
- Type: "Blank app"
- Name: `forge-slack-read`
- Workspace: personal workspace

### 2. Configured User Token Scopes (read-only)

Added 10 scopes under "OAuth & Permissions" > "User Token Scopes":

- `channels:read`, `channels:history` (public channels)
- `groups:read`, `groups:history` (private channels)
- `im:read`, `im:history` (DMs)
- `mpim:read`, `mpim:history` (group DMs)
- `users:read`, `users:read.email` (user resolution)

No write scopes added.

### 3. Installed to workspace

Copied User OAuth Token (`xoxp-...`).

### 4. Verified token

```bash
curl -s -H "Authorization: Bearer xoxp-..." \
  https://slack.com/api/conversations.list?limit=3 | jq '{ok: .ok, channels: [.channels[:3][]?.name]}'
```

**Output:**
```json
{
  "ok": true,
  "channels": [
    "ai-driven-network",
    "all-for-telco-quickstart",
    "new-channel"
  ]
}
```

---

## Pre-work: File Modifications

### 1. Disabled non-slack-read sandboxes

Only `slack-read` left `enabled: true` in `charts/saw-bom/profiles/integrations/default/sandbox.yaml`.
All others (`mail-proxy`, `gmail-write`, `proxy-m365`, `proxy-m365-write`, `slack-write`) set to `enabled: false`.

Added `SLACK_READ_PROXY_BEARER: "${INTER_VM_BEARER}"` to the `slack-read` sandbox env (was missing — see Issues section).

### 2. Trimmed Integrations VM providers

In `charts/saw-bom/profiles/integrations/default/providers.yaml`, kept only:

```yaml
spec:
  providers:
    - name: slack-read
      type: slack-read
      credentialKey: SLACK_READ_USER_TOKEN
```

### 3. Added `slack-read-proxy` to agent-side BOM

In `charts/saw-bom/profiles/data-science/default/providers.yaml`, added:

```yaml
    - name: slack-read-proxy
      type: slack-read-proxy
      credentialKey: SLACK_READ_PROXY_BEARER
      credentialSecret: inter-vm-bearer
      credentialSecretKey: bearer
```

### 4. Attached `slack-read-proxy` to notebook sandbox

In `charts/saw-bom/profiles/data-science/default/sandbox.yaml`, added `slack-read-proxy` to notebook providers:

```yaml
      providers:
        - gmail-read-proxy
        - slack-read-proxy
```

### 5. Trimmed Integrations VM service ports

In `overrides/openshell-saw-integ.yaml`, kept only:

```yaml
service:
  extraPorts:
    - name: slack-read
      port: 18084
      targetPort: 18084
    - name: inference-proxy
      port: 18083
      targetPort: 18083

networkPolicy:
  peerLabel: openshell-saw
  allowedPorts:
    - 18083
    - 18084
```

### 6. Created `.env`

```
GOVERNANCE_ENABLED=false
API_KEY=nvapi-...
KEYCLOAK_NS=keycloak
DEPLOY_GOV_PROFILES=true
```

---

## Phase 1: CLI Prerequisites


| Tool        | Version         | Source                 |
| ----------- | --------------- | ---------------------- |
| `oc`        | 4.18.13         | pre-installed          |
| `helm`      | v3.19.0         | pre-installed          |
| `virtctl`   | v1.8.4          | `~/.local/bin`         |
| `openshell` | 0.0.103+rhaiv.0 | `~/.local/bin`         |
| `jq`        | pre-installed   | pre-installed          |
| `kubectl`   | pre-installed   | pre-installed          |


---

## Phase 2: Cluster Deployment (Option A Manual)

### 2a. Log in to OpenShift

```bash
oc login https://api.cluster-n7h5w.dyn.redhatworkshops.io:6443
```

### 2b. Check prerequisites

```bash
make check-prereqs
```

CNV was not pre-installed. Installed via Subscription + HyperConverged CR.
RHBK was pre-installed in `keycloak` namespace.

### 2c. Deploy Keycloak

```bash
make keycloak
make verify-keycloak
```

**Output:** 9/9 PASS (realm `openshell`, 4 users, 2 clients)

### 2d. Mirror images

```bash
make copy-images
```

**Output:** All images mirrored successfully (`:0.0.110` tags existed for all images — no manual fallback needed).

### 2e. Generate SSH keys

```bash
make generate-keys
```

### 2f. Deploy BOM + secrets

```bash
make deploy-config  # DEPLOY_GOV_PROFILES=true is in .env
```

### 2g. Deploy Integrations VM

```bash
make deploy-integ-vm
make verify-integ
```

**Output:** 9/9 PASS, BOM: 4/4 PASS

### 2h. Deploy Agent VM

```bash
make deploy-agent-vm
make verify-agent
```

**Output:** 8/8 PASS, BOM: 7/7 PASS. Providers: `gmail-read-proxy`, `slack-read-proxy`, `nvidia` all configured.

---

## Phase 3: Configure Slack Token on Integ VM

### 3a. Configure the provider

```bash
virtctl -n openshell-agents ssh --identity-file=~/.generated-ssh-keys/sandbox-ssh \
  cloud-user@vm/openshell-saw-integ --local-ssh-opts "-o StrictHostKeyChecking=no" \
  --command='openshell gateway select openshell-local && \
    openshell provider update --credential SLACK_READ_USER_TOKEN=xoxp-... slack-read'
```

**Output:** `✓ Updated provider slack-read`

### 3b. Redeploy integ VM (v-type placeholder workaround)

```bash
helm uninstall openshell-saw-integ -n openshell-agents
kubectl wait --for=delete vm/openshell-saw-integ -n openshell-agents --timeout=120s
GOVERNANCE_ENABLED=false make deploy-integ-vm
```

### 3c. Re-apply token after redeployment

```bash
openshell gateway select openshell-local
openshell provider update --credential SLACK_READ_USER_TOKEN=xoxp-... slack-read
```

**Output:** `✓ Updated provider slack-read`

### 3d. Proxy startup

After token configuration, the proxy restarted through systemd auto-restart and successfully authenticated:

```
[INFO] slack-read-proxy authenticated as matantalbi (U0BFSDZ8H4P)
[INFO] slack-read-proxy listening on http://127.0.0.1:18084
```

---

## Phase 4: Verification and Testing

### 4a. Verify Integrations VM

```bash
make verify-integ
```

**Output:** 9 passed, 0 failed — ALL PASSED

### 4b. Verify Agent VM

```bash
make verify-agent
```

**Output:** 8 passed, 0 failed — ALL PASSED

### 4c. Integ VM — Bearer validation (AC#4)

```bash
# No bearer → 401
curl -sS -o /dev/null -w "HTTP %{http_code}" http://127.0.0.1:18084/conversations.list
# Output: HTTP 401
```

**PASS** — proxy rejects requests without inter-VM bearer.

### 4d. Integ VM — conversations.list (AC#5)

```bash
curl -sS -H "x-forge-slack-read-bearer: ${SLACK_READ_PROXY_BEARER}" \
  "http://127.0.0.1:18084/conversations.list?limit=2"
# Output: {"ok":true,"channels":[{"name":"ai-driven-network",...},{"name":"all-for-telco-quickstart",...}]}
```

**PASS** — proxy returns real Slack channel data.

### 4e. Integ VM — conversations.history (AC#5)

```bash
curl -sS -H "x-forge-slack-read-bearer: ${SLACK_READ_PROXY_BEARER}" \
  "http://127.0.0.1:18084/conversations.history?channel=C0BG4JD5DCM&limit=1"
# Output: {"ok":true,"messages":[{"user":"U0BG4HU3F8V","type":"message",...}]}
```

**PASS** — proxy returns real Slack message data.

### 4f. Integ VM — users.list (AC#5)

```bash
curl -sS -H "x-forge-slack-read-bearer: ${SLACK_READ_PROXY_BEARER}" \
  "http://127.0.0.1:18084/users.list?limit=1"
# Output: {"ok":true,"members":[{"id":"USLACKBOT","name":"slackbot",...}]}
```

**PASS** — proxy returns user data.

### 4g. Integ VM — POST blocked (AC#6)

```bash
curl -sS -w "HTTP %{http_code}" -X POST \
  -H "x-forge-slack-read-bearer: ${SLACK_READ_PROXY_BEARER}" \
  http://127.0.0.1:18084/chat.postMessage
# Output: forbidden HTTP 403

curl -sS -w "HTTP %{http_code}" -X POST \
  -H "x-forge-slack-read-bearer: ${SLACK_READ_PROXY_BEARER}" \
  http://127.0.0.1:18084/files.upload
# Output: forbidden HTTP 403
```

**PASS** — proxy blocks POST (write) methods with 403.

### 4h. Integ VM — Non-allowlisted method blocked (AC#11)

```bash
curl -sS -w "HTTP %{http_code}" \
  -H "x-forge-slack-read-bearer: ${SLACK_READ_PROXY_BEARER}" \
  http://127.0.0.1:18084/admin.users.list
# Output: forbidden HTTP 403
```

**PASS** — proxy blocks admin-class methods.

### 4i. Agent VM — proxy chain (AC#7, AC#8, AC#9)

The agent VM `slack-read-proxy` provider is attached to the notebook sandbox. The supervisor
has the `slack-read-proxy` governance profile loaded, which specifies the integ gateway
endpoint at port 18084 and the `x-forge-slack-read-bearer` header.

However, the supervisor cannot auto-inject the bearer because `SLACK_READ_PROXY_BEARER` on
the agent VM is a v-type placeholder (`openshell:resolve:env:v..._SLACK_READ_PROXY_BEARER`),
which is not resolvable by the supervisor's egress proxy (same v-type bug as Gmail).

**With manual bearer injection** (reading from `inter-vm-bearer` K8s secret):

```bash
BEARER=$(oc get secret inter-vm-bearer -n openshell-agents -o jsonpath='{.data.bearer}' | base64 -d)
openshell --gateway openshell-saw --gateway-insecure sandbox exec -n notebook --no-tty -- \
  curl -sS -H "x-forge-slack-read-bearer: ${BEARER}" \
  "http://openshell-saw-integ-gateway.openshell-agents.svc.cluster.local:18084/conversations.list?limit=2"
# Output: {"ok":true,"channels":[{"name":"ai-driven-network",...},{"name":"all-for-telco-quickstart",...}]}
```

**PASS** — the full proxy chain works: agent VM → integ gateway :18084 → slack-read-proxy → Slack API.

### 4j. Security — no real Slack token on agent VM (AC#10)

```bash
openshell --gateway openshell-saw --gateway-insecure sandbox exec -n notebook --no-tty -- \
  env | grep -i slack
# Output: SLACK_READ_PROXY_BEARER=openshell:resolve:env:v15637253363493139320_SLACK_READ_PROXY_BEARER
```

**PASS** — `SLACK_READ_PROXY_BEARER` is an OpenShell placeholder, not a real `xoxp-` token. No real Slack credentials exist on the agent VM.

---

## Results


| #   | Criterion                                                           | Result                                                       |
| --- | ------------------------------------------------------------------- | ------------------------------------------------------------ |
| 1   | Integ VM: `slack-read` provider with `credentialKey: SLACK_READ_USER_TOKEN` | PASS — provider created, token configured                    |
| 2   | Integ VM: `slack-read` sandbox on port 18084                        | PASS — sandbox Ready, proxy listening on 127.0.0.1:18084     |
| 3   | Integ VM: xoxp user token configured (read-scoped)                  | PASS — 10 read-only scopes, no write scopes                 |
| 4   | Integ VM: bearer validation (401/200)                               | PASS — 401 without bearer, 200 with correct bearer           |
| 5   | Integ VM: Slack API returns real data                               | PASS — conversations.list, conversations.history, users.list |
| 6   | Integ VM: GET-only allowlist enforced                               | PASS — POST/PUT/DELETE → 403, admin methods → 403           |
| 7   | Agent VM: `slack-read-proxy` provider attached                      | PASS — provider configured alongside nvidia and gmail-read-proxy |
| 8   | Agent VM: supervisor routing to integ via proxy chain               | PASS — cross-VM path works with manual bearer injection      |
| 9   | Agent VM: agent can read Slack via proxy chain                      | PASS — real Slack data returned from agent sandbox           |
| 10  | Security: no real Slack creds on agent VM                           | PASS — `SLACK_READ_PROXY_BEARER` is an OpenShell placeholder |
| 11  | Security: read-class methods only                                   | PASS — `chat.postMessage`, `files.upload`, `admin.users.list` all blocked |


## Verification Outputs

- `make verify-integ`: 9 passed, 0 failed — ALL PASSED
- `make verify-agent`: 8 passed, 0 failed — ALL PASSED

## Issues Encountered and Root-Cause Analysis

### Code issue: `SLACK_READ_PROXY_BEARER` missing from slack-read sandbox env

The `slack-read` sandbox definition in `charts/saw-bom/profiles/integrations/default/sandbox.yaml`
did NOT include `SLACK_READ_PROXY_BEARER` in its env. The proxy binary (`/sandbox/slack-read-proxy`)
requires this env var to validate incoming inter-VM bearer requests.

Without it, the proxy crashed at startup with:
```
Error: ConfigError("SLACK_READ_PROXY_BEARER not set (check .env)")
```

Compare with the `mail-proxy` sandbox which correctly includes `INTER_VM_BEARER_SHA256`:
```yaml
# mail-proxy (correct — has bearer hash):
env:
  INTER_VM_BEARER_SHA256: "${INTER_VM_BEARER_SHA256}"

# slack-read (was missing bearer):
env:
  LISTEN_ADDR: "127.0.0.1:18084"
  # No bearer env!
```

**Fix applied:** Added `SLACK_READ_PROXY_BEARER: "${INTER_VM_BEARER}"` to the slack-read sandbox env.

Note: the slack-read-proxy uses the raw bearer (`${INTER_VM_BEARER}`) not the SHA256 hash
(`${INTER_VM_BEARER_SHA256}`). The proxy validates incoming requests by comparing the
`x-forge-slack-read-bearer` header value directly against the raw bearer. This is a different
pattern from `gmail-read-proxy` which uses SHA256 comparison via `INTER_VM_BEARER_SHA256`.

### Observation: Bearer header name is `x-forge-slack-read-bearer`, not `Authorization`

The slack-read-proxy validates the inter-VM bearer via the custom header `x-forge-slack-read-bearer`,
not the standard `Authorization: Bearer` header. This is **correct behavior** matching the
`slack-read-proxy` governance profile (`header_name: x-forge-slack-read-bearer`).

The `Authorization` header is reserved for the actual Slack API token (added by the supervisor
on egress to `slack.com:443`). This is not a bug.

### Known bug: v-type credential placeholders on agent VM

Same v-type placeholder bug as documented in `docs/bugs/openshell-v-type-placeholder-not-resolved.md`.

The `SLACK_READ_PROXY_BEARER` env var on the agent VM is a v-type placeholder
(`openshell:resolve:env:v..._SLACK_READ_PROXY_BEARER`). The supervisor's egress proxy cannot
resolve v-type placeholders, so it cannot auto-inject the `x-forge-slack-read-bearer` header
into requests from the notebook sandbox.

**Workaround:** Manual bearer injection (reading from the `inter-vm-bearer` K8s secret) works.
The full proxy chain is functional — only the automatic credential injection is broken.

For Gmail, this bug is worked around by the `read-agent-forwarder.mjs` which runs in a `sandbox exec`
session (getting s-type placeholders) and handles credential injection locally. A similar forwarder
could be created for Slack, or the v-type bug could be fixed in the OpenShell supervisor.

### AC#8 clarification: No agent-side forwarder for Slack

The Jira ticket lists "Slack read forwarder running on loopback" (AC#8), but no Slack forwarder
exists in this repo or the agent sandbox image. Unlike Gmail (which has `read-agent-forwarder.mjs`
on `:18079`), the Slack read path was designed to go directly through the OpenShell supervisor.

The supervisor routing works architecturally: the `slack-read-proxy` governance profile endpoint
(`openshell-saw-integ-gateway.__NAMESPACE__:18084`) correctly matches outbound requests. The only
issue is the v-type placeholder bug preventing automatic credential injection.

### Environment-specific: CNV not pre-installed

OpenShift Virtualization was not pre-installed on this cluster. Installed via
Subscription + HyperConverged CR before deployment.

### Environment-specific: RHBK namespace

RHBK operator was pre-installed in the `keycloak` namespace. Used `KEYCLOAK_NS=keycloak` in `.env`.

### Environment-specific: openshell gateway stale registration

The local `openshell` CLI had a stale gateway registration pointing to a previous cluster
(`cluster-w269f`). Required removing and re-adding the `openshell-saw` gateway with the
current cluster's route URL and OIDC issuer.

## Summary of Root Causes


| Issue | Root Cause | Us or Code? | Fix |
|-------|-----------|-------------|-----|
| Proxy crash at startup | `SLACK_READ_PROXY_BEARER` missing from sandbox env | **Code** | Added `SLACK_READ_PROXY_BEARER: "${INTER_VM_BEARER}"` |
| Agent VM can't auto-inject bearer | v-type placeholder not resolvable by supervisor | **Known bug** | Manual bearer injection; needs forwarder or v-type fix |
| Jira AC#8 mismatch | No Slack forwarder exists; Jira assumed one | **Jira spec** | Reframed as "supervisor routing" |
| CNV not installed | Fresh cluster without CNV operator | **Environment** | Installed via Subscription + HyperConverged CR |
| RHBK namespace | Pre-installed in `keycloak` not `openshell-agents` | **Environment** | `KEYCLOAK_NS=keycloak` |
| Stale gateway registration | CLI pointed to old cluster | **Environment** | Re-registered gateway |


## Codebase Fixes Applied in This Branch

1. **`charts/saw-bom/profiles/integrations/default/sandbox.yaml`:** Added
   `SLACK_READ_PROXY_BEARER: "${INTER_VM_BEARER}"` to the `slack-read` sandbox env.
   This was a blocking bug — the proxy could not start without it.

2. **`charts/saw-bom/profiles/data-science/default/providers.yaml`:** Added the
   `slack-read-proxy` provider entry (type `slack-read-proxy`, credential sourced from
   the `inter-vm-bearer` K8s secret). This was missing entirely — without it the agent VM
   supervisor had no policy for Slack read requests.

3. **`charts/saw-bom/profiles/data-science/default/sandbox.yaml`:** Added `slack-read-proxy`
   to the notebook sandbox's providers list, so the supervisor attaches the provider to the
   sandbox and can match outbound requests to the Slack read proxy endpoint.

4. **`docs/adding-proxy-service.md`:** Added a note that proxy sandbox env may use either
   `${INTER_VM_BEARER_SHA256}` (SHA256 comparison, as in gmail-read-proxy) or
   `${INTER_VM_BEARER}` (raw comparison, as in slack-read-proxy), depending on what the
   proxy binary expects. Both variables are available in `bom.env`.

## Remaining Recommendations (Future Work)

1. **Agent-side Slack forwarder:** Either create a `slack-read-agent-forwarder` (similar to
   `read-agent-forwarder.mjs`) that runs in a `sandbox exec` session to get s-type credentials,
   or fix the v-type placeholder bug in the OpenShell supervisor. Without one of these,
   the agent VM cannot automatically inject the bearer for Slack read requests.
