#!/usr/bin/env python3
"""Bounded operations for the Melvin recording environment. Never print secrets."""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import urllib.request
import inference
import users
import verify
import lifecycle

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
STATE = HERE / ".state"
API = "https://api.coco-azure.azure.openshiftpartnerlabs.com:6443"
DOMAIN = "coco-azure.azure.openshiftpartnerlabs.com"
HOST = "qwen-saw-demo-inference.apps." + DOMAIN
OSC = "openshift-sandboxed-containers-operator"
LABEL = "saw.redhat.com/demo"
DEMO = "melvin-confidential"
NAMESPACES = {"saw-demo-inference", "saw-demo-install", "saw-demo-platform"}
DIRECT_TARGETS = {
    "saw-demo-inference": {"Secret": {"inference-tls", "inference-auth"}, "ConfigMap": {"acme"},
        "Deployment": {"acme"}, "Service": {"acme"}, "Route": {"qwen"}},
    "saw-demo-platform": {"ConfigMap": {"governance-interceptor-extra-profiles"}},
}


def run(*args, data=None, check=True, timeout=180, env=None, cwd=None):
    result = subprocess.run(args, input=data, capture_output=True, text=True, timeout=timeout, env=env, cwd=cwd)
    if check and result.returncode:
        # Command output can contain API bodies or credentials. Keep logs bounded.
        raise RuntimeError(f"{args[0]} {args[1] if len(args)>1 else ''} failed (exit {result.returncode})")
    return result


def oc(*args, data=None, check=True, timeout=180):
    return run("oc", *args, data=data, check=check, timeout=timeout)


def get(kind, name=None, namespace=None):
    args = ["get", kind]
    if name:
        args += [name, "--ignore-not-found"]
    if namespace:
        args += ["-n", namespace]
    args += ["-o", "json", "--request-timeout=30s"]
    raw = oc(*args).stdout
    return json.loads(raw) if raw.strip() else None


def guard():
    if oc("whoami", "--show-server").stdout.strip().rstrip("/") != API:
        raise RuntimeError("Refusing operation: the active API is not Melvin's cluster")
    STATE.mkdir(mode=0o700, exist_ok=True)
    STATE.chmod(0o700)


def save(name, value):
    path = STATE / name
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(value, indent=2) + "\n")
    path.chmod(0o600)
    return path


def load(name):
    return json.loads((STATE / name).read_text())


def clean_meta(obj):
    meta = obj["metadata"]
    return {key: meta[key] for key in ("name", "namespace", "uid", "labels", "ownerReferences", "creationTimestamp") if key in meta}


def inspect():
    inventory = {"observedAt": dt.datetime.now(dt.timezone.utc).isoformat(), "api": API,
                 "identity": oc("whoami").stdout.strip()}
    for resource in ("clusterversion", "nodes", "runtimeclasses", "kataconfigs", "storageclasses"):
        inventory[resource] = get(resource)
    inventory["operatorgroups"] = get("operatorgroups", namespace=OSC)
    inventory["oscCSV"] = get("csv", "sandboxed-containers-operator.v1.13.1", OSC)
    cm = get("configmap", "peer-pods-cm", OSC)
    allowed = {"AZURE_IMAGE_ID", "AZURE_INSTANCE_SIZE", "AZURE_INSTANCE_SIZES", "AZURE_REGION",
               "CLOUD_PROVIDER", "DISABLECVM", "PEERPODS_LIMIT_PER_NODE", "ROOT_VOLUME_SIZE"}
    inventory["peerPods"] = {k: v for k, v in cm["data"].items() if k in allowed}
    inventory["applications"] = [{"metadata": clean_meta(a), "sources": [
        {k: s.get(k) for k in ("repoURL", "targetRevision", "path", "chart")}
        for s in (a["spec"].get("sources") or [a["spec"].get("source", {})])],
        "sync": a.get("status", {}).get("sync", {}).get("status"),
        "health": a.get("status", {}).get("health", {}).get("status")}
        for a in get("applications.argoproj.io", namespace="vp-gitops")["items"]]
    inventory["retainedStorage"] = []
    for ns in ("saw-hacohen", "saw-history-8a81a68c5f13c6e3"):
        inventory["retainedStorage"] += [{"metadata": clean_meta(p), "spec": p["spec"], "status": p.get("status")}
                                        for p in get("pvc", namespace=ns)["items"]]
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    save("inventory-" + stamp + ".json", inventory)
    print(json.dumps({"api": API, "osc": inventory["oscCSV"]["status"]["phase"],
                      "peerPods": inventory["peerPods"], "retainedPVCs": len(inventory["retainedStorage"]),
                      "inventory": "inventory-" + stamp + ".json"}, indent=2))


def repair_osc():
    older = get("operatorgroup", "sandboxed-containers-operator", OSC)
    managed = get("operatorgroup", "openshift-sandboxed-containers-operator-operator-group", OSC)
    if not managed or managed["metadata"].get("annotations", {}).get("argocd.argoproj.io/tracking-id", "").split(":")[0] != "secure-agent-workspace-prod":
        raise RuntimeError("Expected GitOps-owned OperatorGroup is absent")
    if older:
        if older["spec"] != managed["spec"] or older["metadata"].get("ownerReferences") or "argocd.argoproj.io/tracking-id" in older["metadata"].get("annotations", {}):
            raise RuntimeError("OperatorGroup ownership/scope changed; refusing deletion")
        save("operatorgroup-before-repair.json", older)
        oc("delete", "operatorgroup", older["metadata"]["name"], "-n", OSC)
    oc("wait", "csv/sandboxed-containers-operator.v1.13.1", "-n", OSC,
       "--for=jsonpath={.status.phase}=Succeeded", "--timeout=120s", timeout=135)
    config = get("kataconfig", "saw-confidential-kata")
    nodes = config["status"]["kataNodes"]
    if nodes["nodeCount"] != 3 or nodes["readyNodeCount"] != 3:
        raise RuntimeError("OSC recovered but the expected three Kata workers are not ready")
    save("osc-repair-result.json", {"phase": "Succeeded", "kataNodes": nodes})
    print("OSC Succeeded; existing KataConfig still has 3/3 ready workers")


def clean_stale():
    ns = "saw-trustee-probe-20261124"
    if get("namespace", ns):
        for kind in ("pods", "pvc", "deployments", "statefulsets", "jobs", "services"):
            if get(kind, namespace=ns)["items"]:
                raise RuntimeError(f"{ns} is no longer empty; refusing deletion")
        save("empty-probe-namespace.json", get("namespace", ns))
        oc("delete", "namespace", ns, "--wait=false")
    ns = "saw-confidential-build"
    pods = get("pods", namespace=ns)["items"]
    exact = {"gateway-image-check-20261124", "runtime-0cb424caa115-1-build",
             "saw-confidential-gateway-1-build", "saw-confidential-gateway-2-build", "saw-confidential-gateway-3-build"}
    removed = []
    for pod in pods:
        name = pod["metadata"]["name"]
        if name in exact and pod["status"].get("phase") in {"Succeeded", "Failed"}:
            save("old-pods/" + name + ".json", {"metadata": clean_meta(pod),
                 "images": [c["image"] for c in pod["spec"]["containers"]], "phase": pod["status"]["phase"]})
            oc("delete", "pod", name, "-n", ns, "--wait=false")
            removed.append(name)
    for name in ("runtime-0cb424caa115-1", "saw-confidential-gateway-1", "saw-confidential-gateway-2", "saw-confidential-gateway-3"):
        build = get("build", name, ns)
        if build and build.get("status", {}).get("phase") in {"Complete", "Failed", "Cancelled", "Error"}:
            save("old-builds/" + name + ".json", {"metadata": clean_meta(build), "status": build["status"],
                 "git": build["spec"].get("source", {}).get("git"), "output": build["spec"].get("output")})
            oc("delete", "build", name, "-n", ns, "--wait=false")
    save("stale-cleanup.json", {"removedPods": removed, "retainedData": "All hacohen workspace and history PVCs preserved"})
    print("Removed enumerated terminal test resources; retained images and workspace/history data")


def apply(obj):
    namespace = obj.get("metadata", {}).get("namespace")
    kind, name = obj["kind"], obj["metadata"]["name"]
    allowed = (name in DIRECT_TARGETS.get(namespace, {}).get(kind, set()) if namespace else
               (kind == "Namespace" and name in NAMESPACES or kind == "RuntimeClass" and name == "kata-cc"))
    if not allowed:
        raise RuntimeError("Direct apply may only target enumerated demo resources")
    obj["metadata"].setdefault("labels", {})[LABEL] = DEMO
    existing = get(obj["kind"], obj["metadata"]["name"], namespace)
    if existing and existing["metadata"].get("labels", {}).get(LABEL) != DEMO:
        raise RuntimeError("Refusing to adopt a resource not owned by this demo")
    oc("apply", "-f", "-", data=json.dumps(obj))


def namespace(name):
    if name not in NAMESPACES:
        raise RuntimeError("Namespace is outside the demo allowlist")
    apply({"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": name, "labels": {
        "argocd.argoproj.io/managed-by": "vp-gitops", "pod-security.kubernetes.io/enforce": "restricted"}}})


def runtimeclass():
    source = get("runtimeclass", "kata-remote")
    if not source or source.get("handler") != "kata-remote":
        raise RuntimeError("Existing peer-pod RuntimeClass is unavailable")
    apply({"apiVersion": "node.k8s.io/v1", "kind": "RuntimeClass", "metadata": {
        "name": "kata-cc", "labels": {"openshell.ai/sandbox-attachable": "true"}},
        **{k: source[k] for k in ("handler", "overhead", "scheduling")}})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("inspect", "repair-osc", "clean-stale", "deploy-inference", "deploy-users", "verify", "recording-links", "cleanup", "rotate-keys", "finish-rotation", "acme-auth", "acme-cleanup"))
    args = parser.parse_args()
    guard()
    this = sys.modules[__name__]
    commands = {"inspect": inspect, "repair-osc": repair_osc, "clean-stale": clean_stale,
                "deploy-inference": lambda: inference.deploy(this),
                "deploy-users": lambda: users.deploy(this),
                "verify": lambda: verify.verify(this),
                "recording-links": lambda: verify.links(this),
                "cleanup": lambda: lifecycle.cleanup(this),
                "rotate-keys": lambda: lifecycle.rotate(this),
                "finish-rotation": lambda: lifecycle.finish_rotation(this),
                "acme-auth": lambda: inference.acme_auth(this),
                "acme-cleanup": lambda: inference.acme_cleanup(this)}
    if args.command not in commands:
        raise RuntimeError("This command is not implemented yet; no deployment action was taken")
    commands[args.command]()


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
