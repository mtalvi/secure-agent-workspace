# Beat 3 — What is protected today (about 40 seconds)

**Starting state:** Use the confidential owner's OpenClaw on the left. Load `confidential.agentSecretsURL` in the cluster-admin console on the right. Preload both `confidential.pods.<role>.yamlURL` tabs from `recording-links`. Their exact UIDs must match the latest verification.

1. **0–12s:** In **Workloads → Secrets**, select **saw-confidential** and filter names for **inference**. Show the empty result. Narrate: “There is no inference credential Secret in the agent’s namespace.”
2. **12–22s:** Open the workload pod's **YAML** tab and find/highlight `runtimeClassName: kata-cc`. Narrate: “The agent receives a placeholder, while its supervisor handles credential use.”
3. **22–29s:** Show the separate supervisor pod's YAML, highlighting the same runtime class. Narrate: “Both pods run in confidential VMs.”
4. **29–40s:** Hold the two YAML tabs or return to diagram stage 2. Narrate: “The control-plane credential copies are still present today; removing those depends on the Trustee integration.”

**Expected result:** No inference Secret in `saw-confidential`; both distinct pod identities use the alias. The prior Azure verification establishes that `kata-cc` actually runs confidential peer VMs. The alias alone is not that proof.

**Reset:** Clear the Secret filter, reload both YAML tabs, and return to `agentSecretsURL`. If a pod changed, rerun verification and links before another take.

**Do not claim:** No key exists in etcd or the gateway. Do not imply this proves an attestation policy or show a working Trustee provider configuration.
