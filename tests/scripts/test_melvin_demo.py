"""Safety boundaries for helpers that mutate the shared demonstration cluster."""
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "demos/melvin-confidential"


@pytest.fixture
def demo(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(DEMO))
    spec = importlib.util.spec_from_file_location("melvin_demo_test", DEMO / "demo.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "STATE", tmp_path)
    return module


@pytest.mark.parametrize("command", ["repair-osc", "clean-stale", "deploy-inference", "deploy-users", "cleanup", "rotate-keys", "finish-rotation"])
def test_every_mutation_checks_exact_server_first(demo, monkeypatch, command):
    calls = []
    monkeypatch.setattr(sys, "argv", ["demo.py", command])
    monkeypatch.setattr(demo, "oc", lambda *args, **kw: calls.append(args) or SimpleNamespace(stdout="https://another-cluster:6443"))
    with pytest.raises(RuntimeError, match="active API"):
        demo.main()
    assert calls == [("whoami", "--show-server")]


@pytest.mark.parametrize("kind,name,namespace", [
    ("Namespace", "saw-hacohen", None), ("RuntimeClass", "kata-remote", None),
    ("Secret", "unrelated", "saw-demo-inference"), ("Deployment", "qwen", "saw-demo-inference"),
    ("ConfigMap", "peer-pods-cm", "openshift-sandboxed-containers-operator"),
])
def test_direct_apply_rejects_non_enumerated_resources(demo, monkeypatch, kind, name, namespace):
    def unexpected(*args, **kwargs):
        pytest.fail("Cluster access before allowlist check")
    monkeypatch.setattr(demo, "get", unexpected)
    with pytest.raises(RuntimeError, match="enumerated"):
        demo.apply({"kind": kind, "metadata": {"name": name, **({"namespace": namespace} if namespace else {})}})


def test_apply_refuses_adoption_of_existing_unowned_secret(demo, monkeypatch):
    monkeypatch.setattr(demo, "get", lambda *args: {"metadata": {"labels": {}}})
    monkeypatch.setattr(demo, "oc", lambda *args, **kw: pytest.fail("Must not mutate unowned resources"))
    with pytest.raises(RuntimeError, match="Refusing to adopt"):
        demo.apply({"kind": "Secret", "metadata": {"name": "inference-auth", "namespace": "saw-demo-inference"}})


def test_secret_value_is_sent_only_on_stdin(demo, monkeypatch):
    calls = []
    monkeypatch.setattr(demo, "get", lambda *args: None)
    monkeypatch.setattr(demo, "oc", lambda *args, **kwargs: calls.append((args, kwargs)))
    demo.inference.secret(demo, "inference-auth", {"keys": "sensitive-test-fixture"})
    args, kwargs = calls[0]
    assert args == ("apply", "-f", "-")
    assert "sensitive-test-fixture" not in repr(args)
    assert json.loads(kwargs["data"])["stringData"]["keys"] == "sensitive-test-fixture"


def test_credential_evidence_detects_leak_without_disclosing_value(demo, monkeypatch):
    import base64
    key = "private-inference-fixture"
    fixtures = {"accounts.json": {"confidential": {"registration": "a" * 16}},
                "inference-keys.json": {"confidential": key}}
    monkeypatch.setattr(demo, "load", fixtures.__getitem__)
    leaked = {"metadata": {"name": "disguised"}, "data": {"config": base64.b64encode(key.encode()).decode()}}
    monkeypatch.setattr(demo, "get", lambda *args, **kwargs: {"items": [leaked]})
    with pytest.raises(RuntimeError, match="agent namespace") as failure:
        demo.verify.credential_locations(demo, "confidential")
    assert key not in str(failure.value)


def test_cleanup_rejects_account_names_outside_the_demo(demo):
    demo.save("accounts.json", {"hacohen": {"registration": "a" * 16}})
    with pytest.raises(RuntimeError, match="allowlist"):
        demo.lifecycle.cleanup(demo)


def test_initdata_includes_guest_service_activation_without_trustee(demo):
    import base64
    import gzip
    import tomllib
    encoded = demo.inference.initdata(demo)
    assert encoded == demo.inference.initdata(demo)
    contents = tomllib.loads(gzip.decompress(base64.b64decode(encoded)).decode())["data"]
    assert set(contents) == {"aa.toml", "cdh.toml", "policy.rego"}
    assert tomllib.loads(contents["aa.toml"])["token_configs"] == {}
    cdh = tomllib.loads(contents["cdh.toml"])
    assert cdh["credentials"] == [] and cdh["kbc"]["url"] == "" and cdh["image"] == {}
    assert "default ExecProcessRequest := false" in contents["policy.rego"]
    assert "default SetPolicyRequest := false" in contents["policy.rego"]


def test_generated_site_passes_configuration_preflight(demo, monkeypatch):
    import base64
    monkeypatch.setattr(demo, "get", lambda *args: {"data": {"tls.crt": base64.b64encode(b"public-ca").decode()}})
    spec = importlib.util.spec_from_file_location("demo_preflight", ROOT / "charts/saw-portal-container/files/preflight.py")
    preflight = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(preflight)
    pins = json.loads((DEMO / "pins.json").read_text())
    config = demo.users.values(demo, "baseline", {"subject": "subject", "registration": "a" * 16,
        "installationID": "installation"}, pins, "b" * 40)
    assert all(preflight.configuration_facts(config["containerDefaults"]).values())
    assert config["containerDefaults"]["credentialBindings"] == [
        {"secret": "inference", "key": "api_key", "type": "custom-inference"}]


def test_existing_keycloak_username_without_saved_subject_is_not_adopted(demo, monkeypatch):
    def request(method, path, data=None):
        assert method == "GET"
        return {"name": "openshell-user"} if path.startswith("/roles/") else [{"id": "unrelated", "username": "baseline"}]
    monkeypatch.setattr(demo.users, "keycloak", lambda d: request)
    with pytest.raises(RuntimeError, match="not demo-owned"):
        demo.users.accounts(demo)


def test_created_account_retry_uses_saved_subject_when_realm_strips_attributes(demo, monkeypatch):
    import uuid
    saved = {owner: {"registration": str(i) * 16, "subject": str(uuid.UUID(int=i))}
             for i, owner in enumerate(demo.users.OWNERS, 1)}
    demo.save("accounts.json", saved)
    def request(method, path, data=None):
        if path == "/roles/openshell-user":
            return {"name": "openshell-user"}
        if method == "GET":
            owner = path.split("username=")[1]
            return [{"id": saved[owner]["subject"], "username": owner}]
        assert method == "POST" and path.endswith("/role-mappings/realm")
    monkeypatch.setattr(demo.users, "keycloak", lambda d: request)
    assert set(demo.users.accounts(demo)) == set(demo.users.OWNERS)
