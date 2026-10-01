---
name: LaunchPad GPU validation
overview: Validate GPU passthrough on the LaunchPad cluster you already logged into (https://api.launchpad.nvidia.com:6443), using the Validated Pattern from gpu-task. Hardware is checked before any GPU node is switched to VFIO. Chart defaults stay off; this cluster’s enablement is test-branch values only.
todos:
  - id: probe-launchpad
    content: SSH to the bastion, confirm LaunchPad API, and record kvm, IOMMU, PCI id, and whether a GPU node is free for vfio
    status: completed
  - id: enable-test-values
    content: Set LaunchPad-only gpu flags (CNV, Alice VM, cuda-sandbox) from the live PCI id and push gpu-task
    status: completed
  - id: secrets-and-image
    content: Write NVIDIA key from .env and a dummy Brave key; build the Podman golden image with gpu.enabled=true
    status: in_progress
  - id: pattern-install
    content: Install the validated pattern from gpu-task and finish README login plus sandbox list
    status: pending
  - id: acceptance-checks
    content: Run nvidia-smi in the sandbox, then local Ollama or vLLM, a CUDA command, and one agent prompt
    status: pending
isProject: false
---

# LaunchPad bare-metal GPU validation

Use only `https://api.launchpad.nvidia.com:6443` (user `nvadmin` on the bastion). Do not use `api.cluster-lpj8r.dyn.redhatworkshops.io`. The `oc` login from the SSH session lives on the bastion, not in the local kubeconfig, so cluster commands run over that SSH host. The repo and keys stay on this machine until the bastion has a checkout.

`gpu-task` is already at `77c9a4d` (GPU re-port plus the NemoClaw env-var removal) and matches `origin/gpu-task`. GPU flags in the charts are still **false**. Turning them on in chart defaults would break every other cluster, so LaunchPad enablement goes in test-only values on this branch, not into the merge-ready defaults.

## 1. Hardware gate (stop if this fails)

On the bastion, confirm `oc whoami --show-server` is the LaunchPad API, then on each GPU node:

- `/dev/kvm` exists and CPU flags include `vmx` or `svm`
- `/sys/kernel/iommu_groups` is non-empty
- `lspci -nnk` NVIDIA vendor/device id (replaces the A10G example `10DE:2237` only after this is read)
- GPU Operator / `vfio-pci` binding, and whether any pod already uses that GPU

`hostDevices` needs the GPU bound to `vfio-pci` (`nvidia.com/gpu.workload.config=vm-passthrough`). That removes the GPU from normal pods. This cluster reported 86 projects, so a node that already runs GPU workloads is not relabeled. If every GPU is in use, stop and report that instead of taking one.

## 2. Test-only GitOps values, then pattern install

After the PCI id is known, on `gpu-task` only:

- [charts/openshift-cnv/values.yaml](charts/openshift-cnv/values.yaml): `gpu.enabled: true` and that node’s `pciDeviceSelector` / `resourceName`
- [overrides/saw-users.yaml](overrides/saw-users.yaml): Alice’s `values.vm.gpu.enabled: true`, `count: 1`, and the same `deviceName` (the saw-users chart merges this onto the VM; see [charts/saw-users/templates/_helpers.tpl](charts/saw-users/templates/_helpers.tpl))
- [charts/saw-bom/profiles/data-science/cuda-dev/sandbox.yaml](charts/saw-bom/profiles/data-science/cuda-dev/sandbox.yaml): `gpu.enabled: true` so the installer passes `openshell sandbox create --gpu 1`

These three stays **off** in the commits meant for main. They exist only so Argo CD on this branch can render them. Push `gpu-task` (no force if it is still even with origin).

Secrets, local only, never committed:

- NVIDIA key from [secure-agent-workspace/.env](secure-agent-workspace/.env) `API_KEY` written to `~/.nvidia-api-key` (mode 600), which [values-secret.yaml.template](values-secret.yaml.template) already references
- Brave key set to a dummy string in `~/.brave-api-key` so the default profile’s `brave` provider can be created
- `cp values-secret.yaml.template ~/values-secret-secure-agent-workspace.yaml` (the pattern’s secret file; README step 4)

Golden image: `make copy-images` pulls the pre-built quay disk, which has **no** NVIDIA driver or CDI. Checkbox 2 needs a Podman image built with `gpu.enabled=true`. `make build-gateway-podman` does not pass that flag and then tries a quay push. Build with helm `--set containerRuntime=podman --set gpu.enabled=true`, `oc start-build`, and keep the in-cluster DataSource. A quay push failure is ignored. This build is long (virt-customize plus the NVIDIA packages).

Then, from a tree that can see the LaunchPad `oc` context:

```bash
export TARGET_BRANCH=gpu-task TARGET_ORIGIN=origin
./pattern.sh make install
```

Follow [README Option A](README.md) through `make login`, `OPENSHELL_SAW_NAME=alice`, `make openshell-saw-configure-gateway`, and `openshell gateway login`.

## 3. README validation, then the five checks

README “Validating the deployment”: `openshell sandbox list`, `openshell sandbox list --workspace cuda-dev`, installer `make openshell-saw-status` until install and apply are Done.

| Check | How |
|---|---|
| 1. Bare metal | Record kvm, IOMMU, and `lspci` from step 1 |
| 2. `nvidia-smi` in the sandbox | `openshell sandbox exec` in `cuda-sandbox` / `cuda-dev`; CDI must show the GPU |
| 3. Local inference | Inside that sandbox, run Ollama (smaller model first) or vLLM on the GPU, not the cloud NVIDIA route |
| 4. CUDA tool | From the NemoClaw sandbox, run a CUDA command (`nvidia-smi` at minimum; `nvcc` or a tiny kernel only if the image has a toolkit) |
| 5. End-to-end | Point that workspace’s inference route at the local server and send one prompt through the TUI or `openclaw` |

Checks 3 and 5 are not what the default `data-science` profile does (it uses build.nvidia.com). They are a follow-up on the already-created GPU sandbox. If the sandbox image cannot install Ollama, say so and stop at the last step that actually ran.
