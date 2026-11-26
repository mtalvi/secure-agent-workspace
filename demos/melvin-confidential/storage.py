"""Demo-owned POSIX storage, with a real filesystem capability check."""
import base64
import datetime
import json
import re
import time
import urllib.parse
import urllib.error
import urllib.request

CLASS = "saw-demo-nfs"
RELEASE = "melvin-storage"
NAMESPACE = "saw-demo-install"


def cloud(d):
    raw = d.get("secret", "peer-pods-secret", d.OSC)["data"]
    credentials = {k: base64.b64decode(v).decode() for k, v in raw.items()}
    form = urllib.parse.urlencode({"grant_type": "client_credentials", "scope": "https://management.azure.com/.default",
        "client_id": credentials["AZURE_CLIENT_ID"], "client_secret": credentials["AZURE_CLIENT_SECRET"]}).encode()
    with urllib.request.urlopen(urllib.request.Request("https://login.microsoftonline.com/" +
            credentials["AZURE_TENANT_ID"] + "/oauth2/v2.0/token", data=form), timeout=30) as response:
        token = json.load(response)["access_token"]

    def request(account, suffix="", method="GET"):
        if not re.fullmatch(r"[a-z0-9]{3,24}", account) or suffix not in ("", "/fileServices/default/shares"):
            raise RuntimeError("Storage account request is outside the demo inventory contract")
        base = "https://management.azure.com/subscriptions/" + credentials["AZURE_SUBSCRIPTION_ID"]
        base += "/resourceGroups/coco-azure-d5ft7-rg/providers/Microsoft.Storage/storageAccounts/" + account
        req = urllib.request.Request(base + suffix + "?api-version=2023-05-01", method=method,
            headers={"Authorization": "Bearer " + token})
        with urllib.request.urlopen(req, timeout=45) as response:
            body = response.read()
            return json.loads(body) if body else None
    return request


def owned_class(d):
    obj = d.get("storageclass", CLASS)
    if obj:
        meta = obj["metadata"]
        if (meta.get("labels", {}).get(d.LABEL) != d.DEMO or
                meta.get("annotations", {}).get("meta.helm.sh/release-name") != RELEASE or
                meta.get("annotations", {}).get("meta.helm.sh/release-namespace") != NAMESPACE):
            raise RuntimeError("Refusing to adopt a storage class outside the demo release")
    return obj


def deploy(d):
    owned_class(d)
    pins = json.loads((d.HERE / "pins.json").read_text())
    values = d.save("storage-values.json", {"image": pins["pythonImage"], "probe": True})
    d.run("helm", "upgrade", "--install", RELEASE, str(d.HERE / "storage"), "-n", NAMESPACE,
        "-f", str(values), "--wait", "--wait-for-jobs", "--timeout", "10m", timeout=630)
    job = d.get("job", "melvin-storage-probe", NAMESPACE)
    if job.get("status", {}).get("succeeded") != 1:
        raise RuntimeError("POSIX filesystem probe has not succeeded")
    result = json.loads(d.oc("logs", "job/melvin-storage-probe", "-n", NAMESPACE).stdout.strip().splitlines()[-1])
    if set(result) != {"posixPermissions", "ownerUID", "hardLinks", "rename", "locking"} or not all(v is True for v in result.values()):
        raise RuntimeError("POSIX filesystem capability check failed")
    pvc = d.get("pvc", "melvin-storage-probe", NAMESPACE)
    pv = d.get("pv", pvc["spec"]["volumeName"])
    if pv["spec"]["claimRef"]["uid"] != pvc["metadata"]["uid"] or pv["spec"]["storageClassName"] != CLASS:
        raise RuntimeError("Storage probe ownership changed")
    group, account, share, *_ = pv["spec"]["csi"]["volumeHandle"].split("#")
    if group != "coco-azure-d5ft7-rg":
        raise RuntimeError("Storage account is outside the demo resource group")
    remote = cloud(d)(account)
    if remote.get("tags", {}).get("sawDemo") != d.DEMO:
        raise RuntimeError("Storage account does not have the demo ownership tag")
    created = datetime.datetime.fromisoformat(remote["properties"]["creationTime"].replace("Z", "+00:00"))
    class_created = datetime.datetime.fromisoformat(owned_class(d)["metadata"]["creationTimestamp"].replace("Z", "+00:00"))
    if created < class_created:
        raise RuntimeError("Refusing to adopt a storage account predating this demo storage release")
    evidence = {"observedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(), "checks": result,
        "storageClassUID": owned_class(d)["metadata"]["uid"], "probePVCUID": pvc["metadata"]["uid"],
        "probePVUID": pv["metadata"]["uid"], "resourceGroup": group, "account": account, "probeShare": share,
        "accountID": remote["id"], "accountCreatedAt": remote["properties"]["creationTime"],
        "jobUID": job["metadata"]["uid"], "capacity": pv["spec"]["capacity"]}
    d.save("storage-evidence.json", evidence)
    capture_accounts(d)
    print("Demo NFS probe passed: permissions, ownership, hard links, rename and locking", flush=True)


def capture_accounts(d):
    """Track every CSI account, including one created after the initial probe."""
    if not owned_class(d):
        return
    records = d.load("storage-accounts.json") if (d.STATE / "storage-accounts.json").exists() else {}
    owners = d.load("accounts.json") if (d.STATE / "accounts.json").exists() else {}
    allowed = {NAMESPACE, "saw-baseline", "saw-confidential"} | {"saw-ctl-" + a["registration"] for a in owners.values()}
    request = cloud(d)
    for pv in d.get("pv")["items"]:
        if pv["spec"].get("storageClassName") != CLASS:
            continue
        if pv["spec"].get("claimRef", {}).get("namespace") not in allowed:
            raise RuntimeError("A non-demo namespace uses the demo storage class; inspect before cleanup")
        group, account, *_ = pv["spec"]["csi"]["volumeHandle"].split("#")
        if group != "coco-azure-d5ft7-rg":
            raise RuntimeError("Unexpected NFS storage resource group")
        remote = request(account)
        if remote.get("tags", {}).get("sawDemo") != d.DEMO:
            raise RuntimeError("NFS storage account ownership tag mismatch")
        record = {"id": remote["id"], "createdAt": remote["properties"]["creationTime"]}
        if account in records and records[account] != record:
            raise RuntimeError("Recorded NFS storage account identity changed")
        records[account] = record
    d.save("storage-accounts.json", records)


def cleanup(d):
    if not owned_class(d) and not (d.STATE / "storage-evidence.json").exists():
        return
    evidence = d.load("storage-evidence.json")
    # Namespace deletion is asynchronous. Wait for all workspace claims to be
    # removed before removing the storage release and its disposable probe.
    for _ in range(60):
        volumes = [p for p in d.get("pv")["items"] if p["spec"].get("storageClassName") == CLASS]
        others = [p for p in volumes if p["metadata"]["uid"] != evidence["probePVUID"]]
        if not others:
            break
        time.sleep(5)
    else:
        raise RuntimeError("Demo workspace storage still exists; leave the storage release intact")
    d.run("helm", "uninstall", RELEASE, "-n", NAMESPACE, "--ignore-not-found", "--wait", "--timeout", "5m", timeout=330)
    request = cloud(d)
    removed = []
    for name, record in d.load("storage-accounts.json").items():
        try:
            request(name)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                removed.append(record["id"])
                continue
            raise
        for _ in range(60):
            shares = request(name, "/fileServices/default/shares")
            if not shares.get("value"):
                break
            time.sleep(5)
        else:
            raise RuntimeError("Demo NFS shares remain; refusing storage account deletion")
        account = request(name)
        if (account["id"] != record["id"] or account.get("tags", {}).get("sawDemo") != d.DEMO or
                account["properties"]["creationTime"] != record["createdAt"]):
            raise RuntimeError("Storage account ownership changed; refusing deletion")
        request(name, method="DELETE")
        for _ in range(60):
            try:
                request(name)
            except urllib.error.HTTPError as error:
                if error.code == 404:
                    removed.append(record["id"])
                    break
                raise
            time.sleep(5)
        else:
            raise RuntimeError("NFS account deletion is still pending")
    d.save("storage-cleanup.json", {"removedAccounts": removed})
