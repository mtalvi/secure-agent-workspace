# Beat 1 — The problem (about 45 seconds)

**Starting state:** All verification gates pass. Use the baseline owner's browser profile on the left, with a new OpenClaw conversation. On the right, use the cluster-admin console. Run `../demo.sh recording-links` and use the resolved `baseline` links. Only disposable demo keys may be revealed.

1. **0–12s:** Send: “In one sentence, explain why protecting API keys matters.” Wait for a fresh response. If it misses the timing window, reset and rehearse again; never substitute a prerecorded answer.
2. **12–25s:** In the console, choose **Workloads → Secrets**. Select the resolved **baseline control namespace** (`controlNamespace`), then its `credentialSecret`. Use `credentialURL` to preload the exact page if needed. Click the UI's reveal control for `api_key` only. Do not reveal other Secrets or browse token pages.
3. **25–42s:** Narrate: “This agent just used a real API key. OpenShell keeps that key away from the agent. But in today’s deployment, a cluster administrator can still read the credential stored here.”
4. **42–45s:** Hold on the Secret page. Blur the entire revealed value in the edited clip.

**Expected result:** The fresh response used the Qwen endpoint; the control namespace contains the actual disposable inference key. This is not an inference Secret in `saw-baseline`.

**Reset:** Hide the value, return to a fresh conversation, and clear the Secrets filter. After the take, rotate the baseline key using the runbook's rotation procedure and verify a fresh response. Re-run `recording-links` after any pod recreation. Keep raw footage private until the revealed key has been revoked.

**Do not claim:** “The key is in the agent,” “no keys in etcd,” or “the gateway cannot see credentials.”
