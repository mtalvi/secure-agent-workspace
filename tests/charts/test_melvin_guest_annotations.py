"""The demo carry must survive Helm serialization and pin both pod roles."""
import json
import subprocess
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
KEY = "io.katacontainers.config.hypervisor.cc_init_data"
SIZE = "io.katacontainers.config.hypervisor.machine_type"


def test_gateway_toml_propagates_distinct_admin_annotations(tmp_path):
    annotations = {role: {KEY: "reviewed-policy", SIZE: size} for role, size in
                   [("workload", "Standard_DC4as_v5"), ("supervisor", "Standard_DC2as_v5")]}
    override = tmp_path / "values.json"
    override.write_text(json.dumps({"deployment": {"mode": "kata"}, "kubernetes": {
        **{role: {"podAnnotations": value} for role, value in annotations.items()},
        "workload": {"podAnnotations": annotations["workload"], "runtimeClassName": "kata-cc"}}}))
    out = subprocess.check_output(["helm", "template", "saw-test", str(ROOT / "charts/openshell-saw-kubernetes"),
        "-n", "saw-test", "-f", str(ROOT / "tests/fixtures/saw-kubernetes-values.yaml"), "-f", str(override),
        "--api-versions", "agents.x-k8s.io/v1beta1/Sandbox",
        "--api-versions", "policy.networking.k8s.io/v1alpha1/AdminNetworkPolicy"], text=True)
    cm = next(x for x in yaml.safe_load_all(out) if x and x["kind"] == "ConfigMap" and "gateway.toml" in x.get("data", {}))
    driver = tomllib.loads(cm["data"]["gateway.toml"])["openshell"]["drivers"]["kubernetes"]
    for role in annotations:
        assert driver[role + "_pod_annotations"] == annotations[role]
    assert driver["default_runtime_class_name"] == driver["supervisor_runtime_class_name"] == "kata-cc"


def test_admission_pins_both_roles_and_sandbox_template(tmp_path):
    override = tmp_path / "values.json"
    annotations = {role: {KEY: "reviewed-policy", SIZE: "Standard_DC4as_v5"} for role in ("workload", "supervisor")}
    override.write_text(json.dumps({"mode": "kata", "workloadRuntimeClassName": "kata-cc", "kataPodAnnotations": annotations}))
    out = subprocess.check_output(["helm", "template", "runtime-test", str(ROOT / "charts/saw-runtime-policies"),
        "-f", str(ROOT / "charts/saw-runtime-policies/tests/container-values.yaml"), "-f", str(override),
        "--api-versions", "admissionregistration.k8s.io/v1/ValidatingAdmissionPolicy",
        "--api-versions", "policy.networking.k8s.io/v1alpha1/AdminNetworkPolicy",
        "--api-versions", "agents.x-k8s.io/v1beta1/Sandbox"], text=True)
    policies = [x for x in yaml.safe_load_all(out) if x and x["kind"] == "ValidatingAdmissionPolicy"]
    expressions = "\n".join(v["expression"] for p in policies for v in p["spec"]["validations"])
    for role in annotations:
        assert "variables.role != '" + role + "'" in expressions
    assert expressions.count(KEY) >= 6
    assert "object.spec.podTemplate.metadata.annotations" in expressions
