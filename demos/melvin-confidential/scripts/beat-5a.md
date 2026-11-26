# Beat 5a — Try the terminal (about 45 seconds)

**Starting state:** Use the same logged-in `kube:admin` console window for both attempts. Preload each resolved supervisor `terminalURL`, with its actual container selected. Both pods must be Ready. Keep OpenClaw on the left. Have `architecture.html` ready at stage 4.

1. **0–15s:** Open the baseline supervisor **Terminal**. Run `id`. Show its real `uid=…` output. Narrate: “The same cluster-admin account can open the baseline supervisor’s terminal.”
2. **15–31s:** Switch to the confidential supervisor **Terminal**. Show the actual guest-policy rejection. Narrate: “Here, the confidential guest rejects the exec request.” If the console shows only a generic failure, use the recorded CLI verification beside it, with exact pod/container names. Never relabel an RBAC denial, missing shell, timeout or startup failure as guest policy.
3. **31–41s:** Narrate: “Next, attestation-gated credential release will bind access to the approved VM configuration.”
4. **41–45s:** End on diagram stage **4**: **Next: Trustee credential release**.

**Expected result:** Baseline `id` succeeds; confidential exec fails with a recorded `ExecProcessRequest` policy denial. Chat must still work after the confidential pod recreation rehearsal.

**Reset:** Close terminal sessions, refresh the resolved pod pages, and clear the baseline terminal. Do not change RBAC, disable policy, or exec into the confidential pod by another path. Rerun `recording-links` after any recreation.

**Excluded:** Tampering, Trustee release logs, the 502 demonstration, and email/chat/calendar integrations.
