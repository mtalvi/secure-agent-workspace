"""Audit database placement can differ from the shared exchange filesystem."""
import shutil
from pathlib import Path

import pytest
import yaml
from helm_cache import run_helm

ROOT = Path(__file__).resolve().parents[2]
HELM = shutil.which("helm")
pytestmark = [pytest.mark.ci_external, pytest.mark.skipif(not HELM, reason="Helm required")]


@pytest.mark.parametrize("separate_history", [False, True])
@pytest.mark.parametrize("separate_workspace", [False, True])
def test_history_claim_and_writer_mount_remain_consistent(separate_history, separate_workspace):
    values = {"enabled": "true", "trustedNamespace": "history", "runtimeNamespace": "runtime",
        "image": "example/node@sha256:" + "a" * 64, "ownerID": "owner",
        "storageClass": "shared", "exchangeStorageClass": "shared",
        **{"identity." + key: key for key in ("subject", "username", "workspace", "sandbox")}}
    if separate_history:
        values.update(historyStorageClass="block", historyClaimName="demo-history-sqlite")
    if separate_workspace:
        values.update(workspaceStorageClass="workspace-block")
    args = [HELM, "template", "demo", str(ROOT / "charts/saw-audit-writer")]
    for key, value in values.items():
        args.extend(["--set", key + "=" + value])
    result = run_helm(args, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    docs = {(d["kind"], d["metadata"]["name"]): d for d in yaml.safe_load_all(result.stdout) if d}
    name = "demo-history-sqlite" if separate_history else "demo-history"
    pvc = docs["PersistentVolumeClaim", name]
    assert pvc["metadata"]["namespace"] == "history"
    assert pvc["spec"]["storageClassName"] == ("block" if separate_history else "shared")
    assert pvc["spec"]["accessModes"] == ["ReadWriteOnce"]
    workspace = docs["PersistentVolumeClaim", "workspace-demo"]["spec"]
    assert workspace["storageClassName"] == ("workspace-block" if separate_workspace else "shared")
    assert workspace["accessModes"] == ["ReadWriteOnce"]
    for shared in ("saw-gate-demo", "saw-audit-demo"):
        assert docs["PersistentVolumeClaim", shared]["spec"]["storageClassName"] == "shared"
    volumes = docs["Deployment", "demo-writer"]["spec"]["template"]["spec"]["volumes"]
    assert next(v for v in volumes if v["name"] == "data")["persistentVolumeClaim"]["claimName"] == name
