# Beat 2 — The extra layer (about 40 seconds)

**Starting state:** Open `../architecture.html` at stage 1. Hide its controls with **R**. Keep the confidential owner's OpenClaw on the left and the diagram on the right. The confidential workload and supervisor must already be Ready and correlated with distinct Azure ConfidentialVMs in the verification evidence.

1. **0–14s:** Show stage **1**, the separate agent and supervisor VMs. Narrate: “The agent and its OpenShell supervisor now run in separate confidential VMs.”
2. **14–26s:** Press **2** to reveal the Qwen VM and TLS connection. Narrate: “The supervisor mediates the agent’s credentials and connects over TLS to our confidential inference server.”
3. **26–40s:** Press **3** to reveal the dashed Trustee connection, labelled **Next step**. Narrate: “The next step is Trustee: release the credential to the supervisor only after attestation succeeds.”

**Expected result:** The gateway remains visibly outside the confidential VM boundary. The diagram distinguishes implemented TLS/VM isolation from future attestation-gated release. The current credential-copy limitation remains visible in the footer.

**Reset:** Press **1** before each take. Press **R** only if controls are visible. This clip needs no resource mutation or Secret reveal.

**Do not claim:** Trustee release or tampering rejection works today. Do not show fabricated release logs or a fake `kbs:///` provider.
