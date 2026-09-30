# GPU Passthrough — OpenShift Virtualization to the Sandbox Container

GPU passthrough from an OpenShift node into the OpenShell gateway VM, and from
there into a sandbox container, is off everywhere by default. Nothing in a
normal install requests a GPU.

```
Physical GPU → OpenShift Node → KubeVirt VM → Podman container → Sandbox process
```

| Hop | Mechanism | Where |
|---|---|---|
| Node → VM | KubeVirt `hostDevices` (VFIO) | `charts/openshift-cnv` (`permittedHostDevices`) and `charts/openshell-saw` (`vm.gpu`) |
| VM → container | NVIDIA driver + Container Toolkit, CDI spec for Podman | `image-builder-charts/helm/openshell-gateway-image` (`gpu.enabled`) |
| Container → sandbox | `openshell sandbox create --gpu` | SAW-BOM sandbox `gpu:` field, read by `charts/openshell-saw/files/installer/apply_bom.py` |

The in-guest installer supports Podman only. The golden-image GPU path generates
a CDI spec (`nvidia-ctk cdi generate`). It does not configure a Docker nvidia
runtime.

## Hardware

`hostDevices` needs bare-metal nodes, or a hypervisor that exposes an IOMMU to
the guest. It does not work on ordinary cloud GPU VMs.

That was checked live on AWS `g5.2xlarge` nodes (one NVIDIA A10G each):

```
$ oc debug node/<g5.2xlarge-node> -- chroot /host lscpu | grep -iE "virtualization|hypervisor"
Hypervisor vendor:                       KVM
# no svm/vmx flag

$ oc debug node/<g5.2xlarge-node> -- chroot /host ls /sys/kernel/iommu_groups/
ls: cannot access '/sys/kernel/iommu_groups/': No such file or directory

$ oc debug node/<g5.2xlarge-node> -- chroot /host ls /dev/kvm
ls: cannot access '/dev/kvm': No such file or directory
```

On that cluster a KubeVirt VM could not schedule (`Insufficient devices.kubevirt.io/kvm`).
Software emulation can boot a VM there; it is not a chart default, and it still
cannot pass a PCI device through. A node-level GPU (the NVIDIA device plugin)
is not the same thing as a GPU inside the gateway VM.

LaunchPad bare-metal has not been tested with this wiring yet. Before enabling
it there, set `pciDeviceSelector` and `deviceName` from that machine
(`lspci -nnk | grep -i nvidia`). The checked-in example is the A10G from the
AWS cluster above (`10DE:2237`, `nvidia.com/GA102GL_A10G`), not a LaunchPad id.

## Turn it on

1. Bind the GPU to `vfio-pci` on the node that should host the VM. With the
   NVIDIA GPU Operator, label that node
   `nvidia.com/gpu.workload.config=vm-passthrough`. That node then cannot also
   serve ordinary GPU pods. This chart does not apply that label.
2. Deploy `charts/openshift-cnv` with `gpu.enabled=true` and the PCI id for
   this GPU. `resourceName` must match `vm.gpu.deviceName`.
3. Rebuild the golden image with `gpu.enabled=true` on
   `image-builder-charts/helm/openshell-gateway-image`. The first-boot unit
   `nvidia-driver-setup.service` builds the kernel module against the VM
   kernel, runs `nvidia-smi`, and writes `/etc/cdi/nvidia.yaml`. If the module
   does not load it warns and exits 0, so a GPU-enabled image can still boot
   on a VM that did not receive a device.
4. Install the sandbox chart with `vm.gpu.enabled=true` (and `vm.gpu.count` if
   you need more than one device). Quickstart:
   `make openshell-saw-create GPU_ENABLED=true ...`.
   `GPU_ENABLED=true` only warns in `make check-prereqs` when the GPU Operator
   or `permittedHostDevices` is missing. It does not install them.
5. On the sandbox that should see the GPU, set the BOM field and redeploy
   `charts/saw-bom`:

```yaml
gpu:
  enabled: true
  count: 1
```

`charts/saw-bom/profiles/data-science/cuda-dev/sandbox.yaml` ships this block
with `enabled: false`. `cuda-sandbox` is part of the default data-science
profile. Leaving it `true` would pass `--gpu` on every default deploy and fail
verification wherever the VM has no GPU.

The installer appends `--gpu <count>` only when that field is true. The flag
is a real `openshell sandbox create` option; it was confirmed locally on
`openshell 0.0.103+rhaiv.0` (`--gpu [<COUNT>]`). The pattern's installer BOM
pins OpenShell `0.0.116-rhaiv.0`. Re-check `openshell sandbox create --help`
on that build during the LaunchPad run.

For a `type: nemoclaw` sandbox the installer also sets
`NEMOCLAW_GPU_ENABLED` and `NEMOCLAW_GPU_COUNT` on `nemoclaw onboard`.
NemoClaw is closed-source; this repo does not know whether those variables
change its behavior.

## Not in this change

- MIG, vGPU, and time-slicing.
- A live `nvidia-smi` or inference run inside a sandbox. That needs the
  LaunchPad (or other bare-metal) test.
- Relabeling GPU Operator nodes. That takes a GPU away from other workloads
  and stays an operator decision.
