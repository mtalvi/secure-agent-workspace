"""Revocation and cleanup, limited to recorded demo ownership."""
import base64
import json
import secrets
import shlex
import time
import urllib.error
import urllib.request

import inference
import users
import verify
import storage


def owned_namespace(d, name, installation=None):
    obj = d.get("namespace", name)
    if not obj:
        return False
    labels = obj["metadata"].get("labels", {})
    if labels.get(d.LABEL) != d.DEMO and (not installation or labels.get("saw.redhat.com/runtime-owner") != installation):
        raise RuntimeError("Refusing to remove a namespace outside this demo registration: " + name)
    return True


def rotate(d):
    """Rotate both disposable keys after a take; never emit either value."""
    if (d.STATE / "rotation-old-keys.json").exists():
        raise RuntimeError("Finish the pending rotation before beginning another one")
    old = d.load("inference-keys.json")
    fresh = {owner: "saw-demo-" + secrets.token_urlsafe(36) for owner in users.OWNERS}
    owners = users.saved_accounts(d)
    # Accept both generations during the ESO/controller refresh, then revoke
    # the previous generation explicitly using finish-rotation.
    d.save("rotation-old-keys.json", old)
    d.save("inference-keys.json", fresh)
    inference.secret(d, "inference-auth", {"keys": "\n".join([*old.values(), *fresh.values()]) + "\n"})
    d.oc("rollout", "restart", "deployment/qwen", "-n", inference.NS)
    for owner, account in owners.items():
        users.vault_write(d, owner, account["registration"], "inference", {"api_key": fresh[owner], "url": "https://" + d.HOST + "/v1"})
    print("New keys written to Vault. After ESO/controller refresh and fresh owner chats, run finish-rotation to revoke the old keys.")


def finish_rotation(d):
    owners = users.saved_accounts(d)
    keys = d.load("inference-keys.json")
    for owner, account in owners.items():
        control = "saw-ctl-" + account["registration"]
        obj = d.get("secret", verify.inference_secret_name(d, control), control)
        if base64.b64decode(obj["data"]["api_key"]).decode() != keys[owner]:
            raise RuntimeError("External Secrets has not refreshed " + owner)
    inference.secret(d, "inference-auth", {"keys": "\n".join(keys.values()) + "\n"})
    d.oc("rollout", "restart", "deployment/qwen", "-n", inference.NS)
    d.oc("rollout", "status", "deployment/qwen", "-n", inference.NS, "--timeout=15m", timeout=915)
    statuses = {}
    for owner, key in d.load("rotation-old-keys.json").items():
        req = urllib.request.Request("https://" + d.HOST + "/v1/models", headers={"Authorization": "Bearer " + key})
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                status = response.status
        except urllib.error.HTTPError as error:
            status = error.code
        if status not in (401, 403):
            raise RuntimeError("Old inference key was not proven revoked")
        statuses[owner] = status
    d.save("rotation-result.json", {"oldKeysRejected": statuses, "httpsVerified": True})
    (d.STATE / "rotation-old-keys.json").unlink()
    print("Old keys rejected over verified HTTPS. Run verify and fresh owner chats before another take.")


def cleanup(d):
    owners = users.saved_accounts(d)
    storage.capture_accounts(d)
    vms = d.load("cleanup-peer-vms.json") if (d.STATE / "cleanup-peer-vms.json").exists() else []
    namespaces = [inference.NS] + ["saw-" + owner for owner in owners]
    for ns in namespaces:
        if d.get("namespace", ns):
            if ns == inference.NS:
                owned_namespace(d, ns)
            else:
                owned_namespace(d, ns, owners[ns.removeprefix("saw-")]["installationID"])
            vms += [p["spec"]["instanceID"] for p in d.get("peerpods", namespace=ns)["items"]]
    vms = sorted(set(vms))
    d.save("cleanup-peer-vms.json", vms)
    # Stop serving the disposable keys first; the owning Helm release removes
    # the Deployment and lets the PeerPod controller delete its cloud VM.
    for release, ns in (("melvin-inference", inference.NS),):
        if owned_namespace(d, ns):
            d.run("helm", "uninstall", release, "-n", ns, "--ignore-not-found", "--wait", "--timeout", "5m", timeout=330)
    for owner, account in owners.items():
        path = d.STATE / (owner + "-values.json")
        if path.exists():
            users.install(d, owner, path, action="delete")
        for ns in ("saw-" + owner, "saw-ctl-" + account["registration"], "saw-ui-" + account["registration"], "saw-history-" + account["registration"]):
            if owned_namespace(d, ns, account["installationID"]):
                d.oc("delete", "namespace", ns, "--wait=false")
    if owners:
        request = users.keycloak(d)
        root = d.get("secret", "vaultkeys", "imperative")["data"]["vault_data_json"]
        token = json.loads(base64.b64decode(root))["root_token"]
        for owner, account in owners.items():
            current = request("GET", "/users/" + account["subject"])
            if current.get("id") != account["subject"] or current.get("username") != owner:
                raise RuntimeError("Demo account ownership changed")
            request("PUT", "/users/" + account["subject"], {"enabled": False})
            for name in ("inference", "web-search"):
                path = "hub/saw-" + owner + "/" + account["registration"] + "/" + name
                script = 'read -r VAULT_TOKEN; export VAULT_TOKEN; vault kv metadata delete -mount=secret ' + shlex.quote(path) + ' >/dev/null'
                d.oc("exec", "-i", "-n", "vault", "vault-0", "--", "sh", "-c", script, data=token + "\n")
    if owned_namespace(d, users.GOV):
        for release in ("melvin-governance", "melvin-governance-policy"):
            d.run("helm", "uninstall", release, "-n", users.GOV, "--ignore-not-found", "--wait", "--timeout", "5m", timeout=330)
    storage.cleanup(d)
    for ns in sorted(d.NAMESPACES):
        if owned_namespace(d, ns):
            d.oc("delete", "namespace", ns, "--wait=false")
    if vms:
        read = verify.azure_reader(d)
        remaining = list(vms)
        for _ in range(60):
            for instance in list(remaining):
                try:
                    read(instance)
                except urllib.error.HTTPError as error:
                    if error.code == 404:
                        remaining.remove(instance)
                    else:
                        raise
            if not remaining:
                break
            time.sleep(10)
        if remaining:
            raise RuntimeError("Peer VM cleanup is incomplete; leave controller finalizers intact and inspect cleanup-peer-vms.json")
    rc = d.get("runtimeclass", "kata-cc")
    if rc:
        if rc["metadata"].get("labels", {}).get(d.LABEL) != d.DEMO:
            raise RuntimeError("RuntimeClass ownership changed")
        d.oc("delete", "runtimeclass", "kata-cc")
    for name in ("inference-keys.json", "rotation-old-keys.json", "build-auth.json", "publish-auth.json"):
        (d.STATE / name).unlink(missing_ok=True)
    d.save("cleanup-result.json", {"cloudVMsRemoved": vms, "demoAccountsDisabled": list(owners),
        "preserved": ["repaired OSC", "shared Pattern", "existing Trustee", "saw-hacohen PVCs", "hacohen history PVC"]})
    print("Demo releases removed, keys revoked, owned peer VMs absent. Shared platform and pre-existing retained data preserved.")
