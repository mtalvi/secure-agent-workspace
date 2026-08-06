# Test: Create a Sandbox and Verify the Dashboard's Terminal Feature

## Why this test

My manager asked: *"are you able to create a new sandbox in your cluster? I tried and I thought
it worked. Can you give it a try. And then deploy the ui, select the workspace, select the
sandbox and go to terminal."*

This documents an independent, end-to-end reproduction of that exact workflow on my own sandbox
(`openshell-saw`): create a new sandbox the normal way (CLI), then use the dashboard UI to
navigate Workspace → Sandbox → Terminal and confirm the browser-based terminal actually works
(not just that the page loads).

## 1. Refresh CLI auth

The `openshell` CLI's OIDC token had expired (10h lifetime, last refreshed early in an earlier
session). A stored refresh token existed but `./scripts/oidc-login.sh refresh` failed (refresh
token itself had expired too), so a full interactive login was needed:

```bash
OIDC_ISSUER="https://openshell-keycloak-ingress-keycloak.apps.cluster-w85nv.dyn.redhatworkshops.io/realms/openshell" \
  ./scripts/oidc-login.sh login
```

This opened a browser auth URL; the host's existing browser session was already SSO-authenticated
as `mtalvi`, so login completed automatically:

```
Logged in as: mtalvi
Token expires in: 599 minutes
```

Then re-registered the gateway and authenticated the CLI against it:

```bash
make openshell-saw-configure-gateway
openshell gateway login openshell-saw --gateway-insecure
```

`openshell --gateway openshell-saw --gateway-insecure sandbox list` confirmed valid auth
afterward.

## 2. Create a new sandbox via CLI

```bash
openshell --gateway openshell-saw --gateway-insecure sandbox create \
  --name mtalvi-terminal --no-tty -- sh -c "echo ready"
```

(Note: first attempt used `mtalvi-terminal-check`, which the gateway rejected — sandbox names
have a 19-character limit: `"name exceeds maximum length (21 > 19)"`. Shortened to
`mtalvi-terminal`.)

The command printed `Created sandbox: mtalvi-terminal`, then errored during its own follow-up
attach/exec step:

```
Error: × transport error
├─▶ invalid peer certificate: UnknownIssuer
Error: × ssh exited with status exit status: 255
```

This looked like a failure at first glance, but it's the CLI's *own* follow-up attach connection
hitting the same kind of self-signed-cert issue documented elsewhere in this project (the
`--gateway-insecure` flag covers the gateway API connection, not this separate SSH-attach leg) —
**not a failure of sandbox creation itself.** Confirmed via a direct status check:

```bash
$ openshell --gateway openshell-saw --gateway-insecure sandbox list
NAME             CREATED              PHASE
openshell-saw    2026-08-04 19:30:50  Ready
mtalvi-terminal  2026-08-05 15:48:08  Ready

$ openshell --gateway openshell-saw --gateway-insecure sandbox get mtalvi-terminal
Phase: Ready
...
Conditions: Ready status = True, Reason: DependenciesReady, Message: "Supervisor session connected"
```

**Result: sandbox creation via CLI works.** The error is a cosmetic/confusing side effect of the
CLI's own post-create attach attempt, not an actual provisioning failure — worth flagging to
whoever owns the `openshell` CLI, but out of scope for this dashboard integration's own bug list
since it's unrelated to anything we built.

## 3. Dashboard UI test (Workspace → Sandbox → Terminal)

Performed via an automated browser test against
`https://openshell-saw-webui-openshell-agents.apps.cluster-w85nv.dyn.redhatworkshops.io/`.

| Step | Result |
|---|---|
| Load dashboard URL | Loaded directly — browser already had a valid session as `mtalvi` (no Keycloak login prompt needed this time) |
| Select workspace | `default` workspace found, status `ACTIVE`, selected successfully |
| Select sandbox | `mtalvi-terminal` found in the sandbox list (alongside a pre-existing `mtalvi-use` sandbox from earlier testing), opened its detail view |
| Sandbox detail view | Tabs available: **Details, Logs, Terminal, Providers, Policy, Proposals, Services, Files.** Status: `Ready`, image `ghcr.io/redhat/openshell-community/sandbox/base:latest` |
| Open Terminal tab | Terminal (xterm.js) loaded, showed "Connected", then a live bash prompt appeared within ~3 seconds: `sandbox@sandbox-mtalvi-terminal:~$` |
| Run `whoami` | Returned `sandbox` — real command execution, not a static page |
| Run `ls` | Returned empty (correct — fresh sandbox, empty home directory) |

Terminal transcript observed:
```
sandbox@sandbox-mtalvi-terminal:~$ whoami
sandbox
sandbox@sandbox-mtalvi-terminal:~$ ls
sandbox@sandbox-mtalvi-terminal:~$
```

The terminal was immediately interactive — responsive enough that the very first keystroke typed
(`w`) alone briefly ran as the `w` command before the rest of `whoami` was typed, confirming this
is a live, low-latency interactive session (a WebSocket connection through the dashboard's BFF →
gateway → sandbox), not a mocked or buffered view.

## Verdict

**Both parts of the manager's request work end-to-end, verified independently:**

- ✅ Creating a new sandbox via the CLI works (`mtalvi-terminal`, `Ready`, `DependenciesReady`)
- ✅ The dashboard UI flow works exactly as described: Workspace → Sandbox → Terminal
- ✅ The Terminal tab opens a real, responsive, interactive shell into the sandbox — commands
  actually execute and return real output, not a frozen or fake view

**No dashboard bug found** — nothing needed fixing on the dashboard/oauth2-proxy/Keycloak side
this time. The one rough edge found (the CLI's post-create attach TLS error) is a pre-existing,
unrelated `openshell` CLI quirk, not a defect introduced by the dashboard integration work.

## Side observations (not blocking, not fixed here)

- A second sandbox, `mtalvi-use`, was found already present in the `default` workspace (~22h30m
  old) — leftover from earlier testing in this project, not something created by this test.
- The dashboard exposes several other useful tabs not exercised by this specific test (`Logs`,
  `Providers`, `Policy`, `Proposals`, `Services`, `Files`) — worth a follow-up smoke test of each
  if broader dashboard coverage is wanted later.
