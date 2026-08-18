# GPU Passthrough — OpenShift Virtualization to the Sandbox Container

## Overview

This document covers GPU passthrough from OpenShift Virtualization (KubeVirt) into the OpenShell
gateway VM, and from there into the sandbox container (Docker or Podman) running the
NemoClaw/OpenClaw agent. It's off by default (`vm.gpu.enabled: false`) and layered on top of the
existing [container runtime](container-runtime.md) and [governance interceptor](governance-interceptor.md)
support.

The path a GPU has to travel:

```
Physical GPU → OpenShift Node → KubeVirt VM → Podman/Docker Container → Sandbox Process
```

Each hop is a separate mechanism, owned by a different layer:

| Hop | Mechanism | Owned by |
|---|---|---|
| Node → VM | KubeVirt `hostDevices` (VFIO PCI passthrough) | `charts/openshift-cnv` (HyperConverged `permittedHostDevices`) + `charts/openshell-saw` (VM spec) |
| VM → container | NVIDIA Container Toolkit + CDI (Podman) or `nvidia-ctk runtime configure` (Docker) | `image-builder-charts/helm/openshell-gateway-image` (golden image) |
| container → sandbox | `openshell sandbox create --gpu` | `charts/openshell-saw` (setup scripts) — native OpenShell CLI flag, confirmed live (see below) |

## Hardware requirements — read this before enabling `vm.gpu.enabled`

**KubeVirt `hostDevices` passthrough requires bare-metal nodes, or a hypervisor that exposes
nested virtualization and an IOMMU to the guest OS.** It does **not** work on standard cloud VM
instance families (e.g. non-`.metal` AWS EC2, most Azure/GCP VM sizes), because those hypervisors
don't expose a virtual IOMMU or VFIO-capable PCI topology to the guest.

This was verified live, not assumed. On the pattern's own AWS-based test cluster (3× `g5.2xlarge`
nodes, each with one NVIDIA A10G):

```
$ oc debug node/<g5.2xlarge-node> -- chroot /host lscpu | grep -iE "virtualization|hypervisor"
Hypervisor vendor:                       KVM
# no svm/vmx flag present in the CPU flags list at all

$ oc debug node/<g5.2xlarge-node> -- chroot /host ls /sys/kernel/iommu_groups/
ls: cannot access '/sys/kernel/iommu_groups/': No such file or directory

$ oc debug node/<g5.2xlarge-node> -- chroot /host ls /dev/kvm
ls: cannot access '/dev/kvm': No such file or directory
```

Consequences, confirmed by actually trying it on that cluster:

- A plain (non-GPU) KubeVirt `VirtualMachineInstance` **fails to schedule at all**: `0/7 nodes are
  available: 3 Insufficient devices.kubevirt.io/kvm, 4 node(s) had untolerated taint(s)` — every
  node in the cluster advertises zero `devices.kubevirt.io/kvm` capacity, because `virt-handler`
  only advertises that resource where `/dev/kvm` exists.
- Enabling KubeVirt's software-emulation fallback (`spec.configuration.developerConfiguration.useEmulation: true`
  on the `KubeVirt` CR, applied via the `kubevirt.kubevirt.io/jsonpatch` annotation on
  `HyperConverged` — **not** shipped in this pattern's chart defaults, since real target hardware
  has KVM) lets VMs boot, proving the base VM-per-user architecture is viable there, just without
  hardware acceleration.
- GPU `hostDevices` passthrough specifically remains impossible regardless of emulation mode,
  because it needs a real IOMMU, which this hypervisor doesn't expose to the guest at all — there
  is no config flag that fixes this; it needs different underlying hardware.

**If you're deploying this pattern for real GPU passthrough, you need bare-metal OpenShift nodes**
(the reference design's own explicit target) or a virtualization platform that exposes nested
virtualization + IOMMU to the guest. Cloud "GPU instance" VM sizes (AWS `g4`/`g5`/`p3`, etc.) give
the *node* direct access to a GPU, but do not let you re-passthrough that GPU into a *nested* VM.

## Phase 1 — KubeVirt `hostDevices` (node → VM)

### Cluster-side: `charts/openshift-cnv`

```yaml
# charts/openshift-cnv/values.yaml
gpu:
  enabled: false
  pciHostDevices:
    - pciDeviceSelector: "10DE:2237"          # VENDOR:DEVICE hex, e.g. `lspci -nnk | grep -i nvidia`
      resourceName: "nvidia.com/GA102GL_A10G" # must match vm.gpu.deviceName below
```

When `gpu.enabled: true`, this renders a `permittedHostDevices.pciHostDevices` entry onto the
`HyperConverged` CR, which tells KubeVirt to advertise that PCI device as a schedulable
`hostDevices` resource. The PCI ID above (`10DE:2237`) is the real ID for an NVIDIA A10G, taken
from a live `lspci -nnk` on this pattern's own test cluster — adjust it for your actual GPU model.

This schema was validated with a server-side dry-run against a real `kubevirt-hyperconverged`
v4.21.16 install:

```
$ oc apply --dry-run=server -f <(helm template charts/openshift-cnv --set gpu.enabled=true)
hyperconverged.hco.kubevirt.io/kubevirt-hyperconverged configured (server dry run)
```

### Prerequisite: get the GPU bound to `vfio-pci` on at least one node

`permittedHostDevices` only makes KubeVirt *aware* the resource could exist — the PCI device
still needs to actually be bound to the `vfio-pci` kernel driver on a specific node (instead of
the `nvidia` driver) before KubeVirt's device plugin will advertise nonzero capacity for it. Two
ways to do this in a real deployment:

1. **NVIDIA GPU Operator's `vfioManager`** (already enabled by default in a `ClusterPolicy`,
   including the one already running on this pattern's shared test cluster for a different,
   unrelated workload — `vfioManager.enabled: true`, `vgpuDeviceManager.enabled: true`). Label the
   target node `nvidia.com/gpu.workload.config=vm-passthrough`; the operator then unbinds the
   `nvidia` driver and binds `vfio-pci` on that node's GPU(s).
2. Manual `driverctl`/kernel-arg-based binding, if you're not running the GPU Operator at all.

**This is mutually exclusive per-node with container-mode GPU use.** A node relabeled for
`vm-passthrough` stops being usable for regular GPU pods (device-plugin/container mode) — plan
node pools accordingly on a shared cluster. This pattern's chart does **not** perform this
relabeling automatically; it's a deliberate operator decision, documented here rather than
silently automated, since it takes GPU capacity away from other tenants on a shared cluster.

### Sandbox-side: `charts/openshell-saw`

```yaml
# charts/openshell-saw/values.yaml
vm:
  gpu:
    enabled: false
    count: 1
    deviceName: "nvidia.com/GA102GL_A10G"   # must match the HCO resourceName above
```

This renders a conditional `hostDevices` block on the `VirtualMachine`'s `domain.devices`, plus a
matching `resources.limits.memory` (KubeVirt wants an explicit memory limit on VMs requesting
`hostDevices`). Validated with a server-side dry-run against the real KubeVirt API:

```
$ oc apply --dry-run=server -f <(helm template charts/openshell-saw --set sandboxName=test --set vm.gpu.enabled=true)
virtualmachine.kubevirt.io/test-saw created (server dry run)
```

## Phase 2 — NVIDIA Container Toolkit (VM → container)

### Golden image

`image-builder-charts/helm/openshell-gateway-image` gained a `gpu.enabled` value (default
`false`). When set, the build:

1. Installs `kernel-devel`, `dkms`, `akmod-nvidia`, and `nvidia-container-toolkit` from NVIDIA's
   Fedora repos at bake time.
2. Bakes in a **first-boot** systemd service, `nvidia-driver-setup.service`, that:
   - Waits for the `nvidia` kernel module to load (the DKMS/akmod build has to happen against the
     VM's *actual running kernel*, so it can't be done at image-bake time — the builder's kernel
     and the eventual VM's kernel are different contexts).
   - Confirms `nvidia-smi` works.
   - Generates the CDI spec: `nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml`.
   - Docker only: `nvidia-ctk runtime configure --runtime=docker && systemctl restart docker`.

Podman has no `--gpus` flag equivalent — it relies entirely on the CDI spec, which is why CDI
generation happens unconditionally (both runtimes), while the Docker runtime-configure step is
Docker-only.

**Caveat, stated plainly:** the driver RPM repo URLs (`gpu.driverRepoUrl`,
`gpu.containerToolkitRepoUrl`) are parameterized in `values.yaml` because NVIDIA's CUDA/driver
repos track specific Fedora releases and can lag a brand-new one — verify they resolve to a real
repo for whatever Fedora version `build.fedoraCloud` points at before enabling this. This part of
the golden image could not be built-and-booted end-to-end in this session (no real
passthrough-capable VM was available to test it in, see the hardware section above) — treat it as
implemented-but-unverified-in-a-real-VM, and validate on real bare-metal hardware before relying
on it.

### Live-validated proof: Podman + CDI + NVIDIA, decoupled from the VM layer

Since a real passthrough VM wasn't available, the Podman+CDI+NVIDIA mechanism itself (the exact
recipe baked into the golden image above) was validated directly against the physical GPU on one
of the test cluster's `g5.2xlarge` nodes — proving the mechanism works, independent of the
node→VM passthrough question:

```
$ oc debug node/<g5.2xlarge-node> -- chroot /host nvidia-smi
Tue Aug 18 11:23:53 2026
+-----------------------------------------------------------------------------------------+
| NVIDIA-SMI 580.126.20             Driver Version: 580.126.20     CUDA Version: 13.0     |
|   0  NVIDIA A10G                    On  |   00000000:00:1E.0 Off |                    0 |
+-----------------------------------------------------------------------------------------+

# Generate a CDI spec from the already-installed toolkit (the GPU Operator installs it in a
# containerized driver root at /run/nvidia/driver on OpenShift nodes; a traditional dnf/akmod
# install — like this pattern's own golden image — puts everything on the normal system path
# instead, so real VMs won't need the --driver-root flag below):
$ nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml --driver-root=/run/nvidia/driver \
    --device-name-strategy=type-index
INFO: Generated CDI spec with version 0.5.0
# kind: nvidia.com/gpu, devices: "all" and "gpu0", both mapping to /dev/nvidia0

# Run a *plain, unmodified* Fedora 44 container — no NVIDIA libraries baked in — and get GPU
# access purely via CDI injection:
$ podman run --rm --device nvidia.com/gpu=all --security-opt label=disable \
    registry.fedoraproject.org/fedora:44 bash -c "nvidia-smi"
Tue Aug 18 11:26:10 2026
+-----------------------------------------------------------------------------------------+
| NVIDIA-SMI 580.126.20             Driver Version: 580.126.20     CUDA Version: 13.0     |
|   0  NVIDIA A10G                    On  |   00000000:00:1E.0 Off |                    0 |
+-----------------------------------------------------------------------------------------+
podman exit code: 0
```

This is real output from this pattern's own test cluster — not a simulation. It confirms the
CDI-based GPU injection mechanism the golden image bakes in genuinely works with Podman, using the
same `nvidia-ctk` version (1.19.1) and the real A10G hardware.

Note the two separate `nvidia.com/...` naming schemes at play, which are easy to conflate:

- **KubeVirt hostDevices resource name** (`nvidia.com/GA102GL_A10G` in this doc's examples) — an
  arbitrary string *you* choose, used purely for Kubernetes-level scheduling of the VM onto a node
  with that PCI device attached.
- **CDI device name** (`nvidia.com/gpu=all` / `nvidia.com/gpu=0`) — generated by `nvidia-ctk`,
  used at the container-runtime level (`podman run --device ...`) to select which GPU already
  visible to the VM's OS to expose into a container.

They operate at different layers and are not required to match.

## Phase 3 — sandbox creation (`openshell sandbox create --gpu`)

**`openshell sandbox create` has a native `--gpu [<COUNT>]` flag.** This was not obvious from this
repo's existing scripts (which only ever passed `--name`/`--from`/`--provider`/`--no-tty`, since
GPU support didn't exist in this pattern before now) — it was confirmed live by actually installing
the CLI and reading its `--help`:

```
$ pip install --index-url <pattern's pipIndexUrl> "openshell==0.0.103+rhaiv.0"
$ openshell sandbox create --help
      --gpu [<COUNT>]
          Request GPU resources for the sandbox.
          Omit COUNT for the driver's default GPU selection, or pass COUNT to request a
          specific number of GPUs.
      --driver-config-json <JSON>
          Experimental driver-keyed JSON object for driver-specific sandbox settings.
```

(`0.0.103+rhaiv.0` was the closest available version to this pattern's pinned `0.0.99+rhaiv.0` on
its private package index at the time of writing — the flag is part of the same `rhaiv` build
lineage.)

This means Phase 3 doesn't need a workaround — `charts/openshell-saw/templates/configmap-scripts.yaml`
now passes `--gpu "${GPU_COUNT}"` directly to `openshell sandbox create` whenever `vm.gpu.enabled`
is set (wired through a new `GPU_ENABLED`/`GPU_COUNT` pair in `secret-setup-env.yaml`). This is
still just a *request* — it only has any effect if the underlying `containerRuntime` is actually
GPU-capable per Phase 2 above; otherwise it's a no-op/best-effort request from OpenShell's own
driver layer, not a hard failure of this pattern's own scripts.

A GPU health check was also added to the setup Job (`run-setup.sh`): after cloud-init finishes, if
`vm.gpu.enabled`, it polls `nvidia-smi` inside the VM for up to 5 minutes and prints the GPU
name/driver/memory on success. It **warns rather than fails** the whole Job on timeout, since a
GPU-enabled VM on hardware that doesn't actually support passthrough (see the hardware section
above) is a real, documented, expected scenario on some clusters — not necessarily a bug.

`NEMOCLAW_GPU_ENABLED`/`NEMOCLAW_GPU_COUNT` env vars are also passed into `nemoclaw onboard` on a
best-effort basis, in case NemoClaw has its own GPU-aware tool selection. **This could not be
verified** — NemoClaw, like OpenShell's own gateway/CLI, is an upstream, closed-source binary this
repo doesn't control; there's no way to introspect what (if anything) it does with those env vars
without access to its source.

## Governance interceptor: not an extension point for this

The governance interceptor (see [governance-interceptor.md](governance-interceptor.md)) was
considered as a place to inject GPU device requests into `CreateSandbox`, since this repo *does*
own its policy data. It doesn't work as an extension point here: the interceptor's *application
code* is built from `NVIDIA/OpenShell`'s own `examples/governance-interceptor` at image-build time
(`image-builder-charts/governance-interceptor/Dockerfile` — `git clone` + `cargo build`), not from
source in this repo, and its policy schema (`policy.yaml`, `profiles/*.yaml`) only expresses
filesystem/landlock/process rules and provider credential/endpoint allowlists — no device or GPU
fields exist in either the interceptor's binary or its policy schema today. Using the native
`--gpu` CLI flag (Phase 3 above) avoids needing this entirely.

## Unimplemented / future work

Matching the original ticket's own "if applicable" framing, none of the following are implemented,
and none were required to make the rest of this work meaningful:

- **MIG / vGPU / GPU sharing across multiple VMs.** `vm.gpu.count` only requests whole GPUs. NVIDIA
  MIG partitioning or vGPU time-slicing would need a different `HyperConverged.mediatedDevices`
  configuration path (not `pciHostDevices`) plus a licensed vGPU manager on the host — out of
  scope until there's real bare-metal hardware to validate it against.
- **Multi-GPU node scheduling nuances** (e.g. NUMA-aware placement) — not addressed; `vm.gpu.count`
  just requests N identical `hostDevices` entries.
- **Ollama/vLLM end-to-end latency/throughput benchmarking** (the ticket's Phase 4) — not run this
  session; there was no real passthrough-capable VM available to run them in. The Phase 2 Podman
  proof above is the closest available evidence that the mechanism works; the actual demo needs
  real bare-metal GPU nodes.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| VMI stuck in `Scheduling`, event says `Insufficient devices.kubevirt.io/kvm` | No node has `/dev/kvm` — this cluster/hypervisor doesn't support nested virtualization at all (see hardware section) |
| VMI stuck in `Scheduling`, event mentions the GPU's `resourceName` | No node has the GPU bound to `vfio-pci` yet — see the node-labeling prerequisite under Phase 1 |
| `nvidia-smi` fails inside the VM after GPU passthrough succeeded | Golden image driver/kernel version mismatch — the DKMS/akmod build only happens on first boot; check `journalctl -u nvidia-driver-setup.service` and `journalctl -u akmods` on the VM |
| `openshell sandbox create --gpu` succeeds but the sandbox has no GPU | Check `containerRuntime`'s CDI spec (`/etc/cdi/nvidia.yaml` for Podman) actually exists on the VM — the `nvidia-driver-setup.service` first-boot unit generates it; it won't exist if the driver never loaded |
