# Melvin confidential-workspace recording package

This is a temporary integration for Beats **1, 2, 3 and 5a**. The recording gate
is `./demo.sh verify`; a deployed manifest or a RuntimeClass name is not a pass.
Read the latest verification report before recording.

The target is exactly `https://api.coco-azure.azure.openshiftpartnerlabs.com:6443`.
Every helper checks the current API before doing work. Standalone workspace
releases live in `saw-demo-install`, governance in `saw-demo-platform`, and the
inference server in `saw-demo-inference`. The shared Pattern keeps following
Hadar's fork and its existing branch. This branch starts at PR #174's merge,
`c44caef718307454d1d92f3b5d43139d485b65ba`.

## Credential claims

The current path is **Vault → control namespace ExternalSecret/Secret → gateway
credential storage → supervisor use**. OpenClaw receives a placeholder. No
inference credential Secret is created in its runtime namespace. The gateway
is an ordinary pod. Control-plane and gateway copies remain present today.
The inference server's accepted keys and TLS private key also originate from
demo Kubernetes Secrets. This demo does not establish attestation-gated secret
delivery or protect those control-plane copies from cluster administrators.

The `kata-cc` name is a demo-owned alias of `kata-remote`. Verification must
correlate each pod UID with its PeerPod and actual Azure `ConfidentialVM`, and
show the exec denial comes from the guest policy. Trustee credential release,
tampering/attestation rejection, and email/chat/calendar remain future work.

## Preparation

Requirements: `oc`, `helm`, `uv`, `git`, cluster-admin access to the exact target,
public ingress on TCP 80 and 443, Azure quota for DC8as_v5 + DC4as_v5 + DC2as_v5,
and anonymously pullable pinned images. Local image builds additionally use
Podman, a permitted Red Hat Rust builder pull credential and GHCR publication
access. Secrets must never be added to Git or passed on command arguments.

Run from this directory:

```sh
./demo.sh inspect
./demo.sh repair-osc
./demo.sh clean-stale
./demo.sh deploy-inference
./demo.sh deploy-users
./demo.sh verify
./demo.sh recording-links
```

`inspect` saves the resource ownership/revision/runtime/storage inventory.
`repair-osc` deletes only the older, unmanaged duplicate OperatorGroup after
checking it has the same scope as the preserved GitOps group. It waits for the
CSV to succeed and checks the existing KataConfig's three workers.
`clean-stale` has an exact allowlist of obsolete terminal pods/build runs and
checks the old probe namespace is empty before removing it. It preserves image
artifacts and all previously retained hacohen workspace/history PVCs.

`deploy-inference` creates the alias, requests a dedicated Let's Encrypt
HTTP-01 certificate, and installs the `melvin-inference` Helm release. A small
temporary HTTP responder serves the challenge on port 80. After issuance, the
Route uses passthrough TLS to llama.cpp inside the confidential peer pod. No
router certificate or insecure TLS client bypass is used for inference.
The pinned model loader verifies the GGUF SHA-256 before starting the server.
The server uses a 32K context, one request slot, eight CPU threads, Jinja tool
formatting and disabled thinking. Keys are generated locally and supplied as
mounted files. Do not expose `.state/inference-keys.json` in a recording.

`deploy-users` requires a clean, committed demo branch published to the user's
fork and digest pins for both custom images. It creates only demo-owned
Keycloak accounts, writes their disposable inference keys under their unique
Vault registrations, and writes a clearly dummy web-search key. Web search is
disabled in both profiles. It installs each foundation, waits for health,
captures and checks the observed boundary, pins its reference and BOM digest,
then enables enrollment. It never uses `autoApprove`. A foundation failure
leaves enrollment disabled. The generated per-owner values are saved privately
under `.state`; use those exact registrations for retries and cleanup.
Keycloak ownership uses the immutable subject saved when the helper creates
each account. This realm discards unmanaged user attributes. An existing
username without the matching saved subject is never adopted.

Both owners use the same application, supervisor, sandbox-runtime and gateway
images. Only the confidential owner uses `mode: kata`, the explicitly approved
`kata-remote` handler, the class UID/placement digest, and role-specific VM-size
and initdata annotations. Runtime admission and enrollment pin those values.
Local Kata retains its `/dev/kvm` checks and the ordinary-container defaults.

## Pins and carry

`pins.json` is the image/model lock. `inference/` is the Helm manifest source.
The actual runtime class UID and placement hash are cluster-specific and are
captured in the reviewed owner values. `policy.rego`, `aa.toml`, and `cdh.toml` are
deterministically packed into gzipped initdata, with hashes recorded in
`.state/initdata-evidence.json`. CDH has an empty credential list, no Trustee
endpoint, and no image-policy or registry-credential URI; images are public.
The minimal AA file has no token provider. Its presence starts the VM's
AA → CDH → Kata service chain; it does not enable Trustee credential release.

`gateway-carry/guest-annotations.patch` applies to the exact downstream
OpenShell source named by `gatewaySource`. It adds administrator-only annotation
maps to workload and supervisor construction; caller metadata cannot override
them. The existing sandbox auxv carry remains pinned in `sandboxRuntimeImage`.
The policy denies `ExecProcessRequest` and `SetPolicyRequest`. It is a demo
lifecycle policy, not a complete attested workload/image allowlist.

Build the images from the repository root:

```sh
podman build -f kubernetes-runtime/Containerfile -t localhost/saw-melvin-runtime:demo .
podman build --authfile demos/melvin-confidential/.state/build-auth.json \
  -t localhost/saw-melvin-gateway:demo demos/melvin-confidential/gateway-carry
podman build --authfile demos/melvin-confidential/.state/build-auth.json \
  --target builder -t localhost/saw-melvin-gateway-builder:demo \
  demos/melvin-confidential/gateway-carry
podman build -f demos/melvin-confidential/gateway-carry/Testfile \
  demos/melvin-confidential/gateway-carry
```

The build auth file is private and contains only the authorized builder-registry
credential. Publish the runtime and gateway to the explicitly approved
`ghcr.io/mtalvi/saw-melvin-runtime` and `ghcr.io/mtalvi/saw-melvin-gateway`
packages. Authenticate through stdin, record the returned manifest digest,
make the package public, and verify anonymous retrieval before deployment.
Do not use a mutable image tag in the final owner values.

## Verification and rehearsal

`verify` checks OSC, credential locations, verified HTTPS, rejection of missing/wrong keys, real fresh
nonce responses, timing, Azure VM correlation, expected annotations, and the
two supervisor exec outcomes. It records actual error text and fails if a
denial is not specifically attributable to `ExecProcessRequest` guest policy.
Startup timeouts are failures. The final rehearsal gate also requires fresh
OpenClaw turns, placeholder checks, negative placement/initdata admission
checks, and chat after recreation, tied to current pod UIDs. Missing evidence
never counts as passed. Do not hand-edit that report to claim success.

The stock `scripts/e2e-workspace.sh` uses host-driven workload exec and therefore
cannot serve as the confidential-owner chat check under this policy. Rehearse
through the owner's browser UI. Record the real prompt, response, elapsed time,
pod identity and verification method in `.state/rehearsal.json`; no credentials
belong in that evidence. A live recorder must perform these checks before
asserting their boolean results. Keep the HTTP inference nonce test separate
from the owner OpenClaw test; they prove different paths.

Use server-side dry-runs of modified copies of the real pod manifests for
negative admission checks. Preserve the real creator's ServiceAccount,
ownerReferences and expected annotations, and change only the placement or
initdata being tested. First prove the unchanged dry-run succeeds. Require the
specific ValidatingAdmissionPolicy rejection; another error is inconclusive.

For the recreation rehearsal, restart the confidential workspace using its
documented `restartGeneration` lifecycle input, keeping the pinned plan,
registration, placement and policy unchanged. Reapply the owner release through
`scripts/container-workspace.py`. Wait for both new pod UIDs to be Ready, repeat
Azure and exec checks, then send a fresh owner chat. Do not patch an enrolled
plan or remove admission guards to force recovery.

## Recording setup

Use independent browser profiles for `baseline` and `confidential`, plus the
existing cluster-admin console window. Demo login passwords are in the private
`.state/accounts.json`; keep them out of the terminal recording and Git.
The existing console/Keycloak ingress uses the cluster's own certificate trust;
configure that trust normally before recording. Inference has its separate
publicly trusted certificate.

Record each clip independently at **1920×1080**, with OpenClaw on the left and
the relevant console/diagram tab on the right. Increase browser text size until
the Secret field, runtime class and terminal error are readable. Close unrelated
tabs and notifications. Use `recording-links` immediately before recording; it
resolves live registration IDs, credential Secret names, pod/container names
and console URLs. Never guess a namespace or reuse a stale pod link.

The four scripts include independent starting states, narration, expected
results, and resets:

- [Beat 1 — The problem](scripts/beat-1.md)
- [Beat 2 — The extra layer](scripts/beat-2.md)
- [Beat 3 — What is protected today](scripts/beat-3.md)
- [Beat 5a — Try the terminal](scripts/beat-5a.md)

Open [the staged architecture diagram](architecture.html) locally. Keys 1–4
select stages, arrows advance/back, and R hides its controls. End Beat 5a at
stage 4. The gateway stays outside the confidential boundary at every stage.

## Rotation and cleanup

After a take reveals a key, run `./demo.sh rotate-keys`. This temporarily accepts
both generations so ESO and the gateway can refresh. Wait for fresh chats with
both owners, then run `./demo.sh finish-rotation` to remove the old generation
from the server's key file and restart the inference release. The helper waits
for model readiness and requires old keys to return 401/403 before sharing the edited
clip. Blur the revealed value even after revocation. Re-run all gates after a
rotation/recreation because VM and pod identities change.

After all recordings, run:

```sh
./demo.sh cleanup
```

Cleanup stops the inference release, removes owner releases, deletes only the
recorded demo namespaces/history, revokes their Vault credentials, disables the
demo Keycloak accounts, and waits for the owned Azure VMs to be absent. It then
removes the demo alias. It leaves the repaired OSC, shared Pattern, existing
Trustee and the four pre-existing retained hacohen PVCs intact. A cloud cleanup
timeout is an outstanding failure; never strip the PeerPod finalizer or delete
a cloud VM identified only by a loose name prefix.

`.state` is mode 0700 and ignored by Git; it contains sensitive operational state
as well as evidence. Never attach or publish the entire directory. Share only
reviewed, credential-free inventory, boundary, VM and verification JSON. Delete
the ACME private-key directory and account passwords after cleanup when no
further demo operation needs them. Published demo images contain no demo keys.

Primary references: [OSC confidential-container configuration](https://docs.redhat.com/en/documentation/openshift_sandboxed_containers/1.13/html/deploying_confidential_containers_on_microsoft_azure/configure-cc-overview_azure-cc),
[official Qwen GGUF model](https://huggingface.co/Qwen/Qwen3-4B-GGUF),
[llama.cpp server](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md),
[ACME HTTP-01 requirements](https://letsencrypt.org/docs/challenge-types/).
