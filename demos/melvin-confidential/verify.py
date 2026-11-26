"""Live evidence; unknown or untested gates never count as a pass."""
import base64
import datetime
import json
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

import inference


def azure_reader(d):
    data = d.get("secret", "peer-pods-secret", d.OSC)["data"]
    credential = {key: base64.b64decode(value).decode() for key, value in data.items()}
    body = urllib.parse.urlencode({"grant_type": "client_credentials", "scope": "https://management.azure.com/.default",
        "client_id": credential["AZURE_CLIENT_ID"], "client_secret": credential["AZURE_CLIENT_SECRET"]}).encode()
    url = "https://login.microsoftonline.com/" + credential["AZURE_TENANT_ID"] + "/oauth2/v2.0/token"
    with urllib.request.urlopen(urllib.request.Request(url, data=body), timeout=30) as response:
        token = json.load(response)["access_token"]
    prefix = "/subscriptions/" + credential["AZURE_SUBSCRIPTION_ID"] + "/resourceGroups/coco-azure-d5ft7-rg/providers/Microsoft.Compute/virtualMachines/"

    def read(instance):
        if not instance.startswith(prefix + "podvm-") or not re.fullmatch(r"[a-zA-Z0-9/_.-]+", instance):
            raise RuntimeError("Peer VM is outside the recorded demo resource group")
        req = urllib.request.Request("https://management.azure.com" + instance + "?api-version=2024-07-01",
                                     headers={"Authorization": "Bearer " + token})
        with urllib.request.urlopen(req, timeout=30) as response:
            value = json.load(response)
        props = value["properties"]
        return {"id": value["id"], "location": value["location"], "size": props["hardwareProfile"]["vmSize"],
                "securityProfile": props.get("securityProfile", {}), "provisioningState": props.get("provisioningState"),
                "imageReference": props["storageProfile"]["imageReference"]}
    return read


def pod(d, owner, role):
    ns = "saw-" + owner
    items = [p for p in d.get("pods", namespace=ns)["items"]
             if not p["metadata"].get("deletionTimestamp")
             and p["metadata"].get("labels", {}).get("openshell.ai/boundary-role") == role]
    if len(items) != 1:
        raise RuntimeError(owner + " must have exactly one live " + role + " pod")
    return items[0]


def ready(p):
    return any(c["type"] == "Ready" and c["status"] == "True" for c in p.get("status", {}).get("conditions", []))


def credential_locations(d, owner):
    """Compare credential bytes only in memory; evidence contains names, never values."""
    registration = d.load("accounts.json")[owner]["registration"]
    control = "saw-ctl-" + registration
    expected_key = d.load("inference-keys.json")[owner].encode()
    runtime_secrets = d.get("secrets", namespace="saw-" + owner)["items"]
    for secret in runtime_secrets:
        if "inference" in secret["metadata"]["name"].lower() or any(
                expected_key in base64.b64decode(value) for value in secret.get("data", {}).values()):
            raise RuntimeError("An inference credential Secret exists in the agent namespace")
    copies = []
    for secret in d.get("secrets", namespace=control)["items"]:
        if any(expected_key in base64.b64decode(value) for value in secret.get("data", {}).values()):
            copies.append(secret["metadata"]["name"])
    if not copies:
        raise RuntimeError("The expected control-namespace inference credential copy was not found")
    return {"agentNamespace": "saw-" + owner, "inferenceSecretAbsent": True,
            "controlNamespace": control, "credentialSecrets": copies,
            "remainingCopies": ["Vault", "control-namespace Secret", "gateway credential storage", "inference server Secret"]}


def inference_secret_name(d, control):
    external = d.get("externalsecrets", namespace=control)["items"]
    names = [e["spec"].get("target", {}).get("name", e["metadata"]["name"]) for e in external
             if any(x.get("remoteRef", {}).get("key", "").endswith("/inference") for x in e["spec"].get("data", []))]
    if len(names) != 1:
        raise RuntimeError("Inference credential Secret is not uniquely resolved in " + control)
    return names[0]


def links(d):
    console = "https://console-openshift-console.apps." + d.DOMAIN
    records = {}
    for owner in ("baseline", "confidential"):
        ns = d.get("namespace", "saw-" + owner)
        if not ns:
            raise RuntimeError(owner + " runtime namespace has not been created")
        reg = ns["metadata"]["labels"]["saw.redhat.com/registration"]
        control = "saw-ctl-" + reg
        credential_secret = inference_secret_name(d, control)
        routes = d.get("routes", namespace="saw-ui-" + reg)["items"]
        records[owner] = {"registration": reg, "runtimeNamespace": ns["metadata"]["name"], "controlNamespace": control,
            "agentSecretsURL": console + "/k8s/ns/" + ns["metadata"]["name"] + "/secrets",
            "credentialSecret": credential_secret,
            "credentialURL": console + "/k8s/ns/" + control + "/secrets/" + credential_secret,
            "openclawURL": "https://" + next(r["spec"]["host"] for r in routes), "pods": {}}
        for role in ("workload", "supervisor"):
            p = pod(d, owner, role)
            container = p["spec"]["containers"][0]["name"]
            url = console + "/k8s/ns/" + ns["metadata"]["name"] + "/pods/" + p["metadata"]["name"]
            records[owner]["pods"][role] = {"name": p["metadata"]["name"], "uid": p["metadata"]["uid"],
                "container": container, "yamlURL": url + "/yaml", "terminalURL": url + "/terminal?container=" + container}
    d.save("recording-links.json", records)
    print(json.dumps(records, indent=2))
    return records


def verify(d):
    result = {"observedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(), "api": d.API, "checks": []}

    def check(name, fn):
        try:
            evidence = fn()
            result["checks"].append({"name": name, "pass": True, "evidence": evidence})
            print("PASS " + name, flush=True)
        except Exception as e:
            # HTTP errors may contain an account URL; never capture response bodies.
            result["checks"].append({"name": name, "pass": False, "failure": str(e) if isinstance(e, RuntimeError) else type(e).__name__})
            print("FAIL " + name + ": " + result["checks"][-1]["failure"], flush=True)

    def osc():
        phase = d.get("csv", "sandboxed-containers-operator.v1.13.1", d.OSC)["status"]["phase"]
        nodes = d.get("kataconfig", "saw-confidential-kata")["status"]["kataNodes"]
        if phase != "Succeeded" or nodes["readyNodeCount"] != 3:
            raise RuntimeError("OSC or its three workers are not ready")
        return {"phase": phase, "nodes": nodes}
    check("OSC", osc)

    def endpoint():
        base = "https://" + d.HOST + "/v1"
        statuses = []
        for key in (None, "intentionally-invalid-demo-key"):
            req = urllib.request.Request(base + "/models", headers={"Authorization": "Bearer " + key} if key else {})
            try:
                with urllib.request.urlopen(req, timeout=20) as response:
                    status = response.status
            except urllib.error.HTTPError as error:
                status = error.code
            if status not in (401, 403):
                raise RuntimeError("Missing/incorrect API key was not rejected")
            statuses.append(status)
        timings = {}
        for owner, key in d.load("inference-keys.json").items():
            nonce = "fresh-" + secrets.token_hex(6)
            body = {"model": "Qwen3-4B", "messages": [{"role": "user", "content": "Reply with exactly " + nonce}],
                    "max_tokens": 40, "temperature": 0, "chat_template_kwargs": {"enable_thinking": False}}
            req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode(),
                    headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
            started = time.monotonic()
            with urllib.request.urlopen(req, timeout=120) as response:
                text = json.load(response)["choices"][0]["message"]["content"]
            elapsed = round(time.monotonic() - started, 3)
            if nonce not in text:
                raise RuntimeError("Inference response did not include its fresh nonce")
            timings[owner] = {"seconds": elapsed, "nonceMatched": True, "within40Seconds": elapsed < 40}
        if not all(t["within40Seconds"] for t in timings.values()):
            raise RuntimeError("Inference rehearsal exceeded the recording window")
        return {"httpsVerified": True, "rejectedStatus": statuses, "timings": timings}
    check("Inference HTTPS, authentication and fresh response", endpoint)

    def peers():
        read = azure_reader(d)
        desired = [("saw-demo-inference", p, "Standard_DC8as_v5")
                   for p in d.get("pods", namespace="saw-demo-inference")["items"] if p["metadata"].get("labels", {}).get("app") == "qwen"]
        for role, size in (("workload", "Standard_DC4as_v5"), ("supervisor", "Standard_DC2as_v5")):
            desired.append(("saw-confidential", pod(d, "confidential", role), size))
        evidence = []
        expected_initdata = inference.initdata(d)
        for ns, p, size in desired:
            if not ready(p) or p["spec"].get("runtimeClassName") != "kata-cc":
                raise RuntimeError("Confidential pod is not Ready with kata-cc: " + p["metadata"]["name"])
            if p["metadata"].get("annotations", {}).get("io.katacontainers.config.hypervisor.cc_init_data") != expected_initdata:
                raise RuntimeError("Confidential pod initdata differs from reviewed policy")
            matches = [pp for pp in d.get("peerpods", namespace=ns)["items"] if any(
                o.get("uid") == p["metadata"]["uid"] for o in pp["metadata"].get("ownerReferences", []))]
            if len(matches) != 1:
                raise RuntimeError("Pod does not have a unique owned PeerPod")
            vm = read(matches[0]["spec"]["instanceID"])
            security = vm["securityProfile"]
            if vm["size"] != size or security.get("securityType") != "ConfidentialVM":
                raise RuntimeError("Azure VM confidentiality/size does not match the demo")
            evidence.append({"pod": p["metadata"]["name"], "uid": p["metadata"]["uid"], "namespace": ns, "vm": vm})
        if len(evidence) != 3:
            raise RuntimeError("Expected exactly three confidential peer VMs")
        return evidence
    check("Confidential peer VM correlation", peers)

    for owner in ("baseline", "confidential"):
        check(owner + " credential locations", lambda owner=owner: credential_locations(d, owner))
        def exec_check(owner=owner):
            p = pod(d, owner, "supervisor")
            if not ready(p):
                raise RuntimeError("Supervisor is not Ready")
            name, container = p["metadata"]["name"], p["spec"]["containers"][0]["name"]
            cmd = d.oc("exec", "-n", "saw-" + owner, name, "-c", container, "--", "id", check=False, timeout=25)
            if owner == "baseline":
                if cmd.returncode or "uid=" not in cmd.stdout:
                    raise RuntimeError("Baseline id did not succeed")
            else:
                if not cmd.returncode or not re.search("ExecProcessRequest", cmd.stderr) or not re.search("denied|policy", cmd.stderr, re.I):
                    raise RuntimeError("Exec failed without a proven guest-policy rejection, or unexpectedly succeeded")
            return {"pod": name, "uid": p["metadata"]["uid"], "container": container,
                    "exit": cmd.returncode, "stdout": cmd.stdout[:2000], "stderr": cmd.stderr[:4000]}
        check(owner + " supervisor exec", exec_check)
    # These must be written by the live browser/recreation rehearsal. Their
    # pod UIDs make stale evidence fail after a later restart.
    def rehearsal():
        if not (d.STATE / "rehearsal.json").exists():
            raise RuntimeError("Owner chat, placeholder, admission rejection and recreation rehearsal has not been completed")
        evidence = d.load("rehearsal.json")
        for owner in ("baseline", "confidential"):
            e = evidence[owner]
            if e["workloadUID"] != pod(d, owner, "workload")["metadata"]["uid"] or not all(
                    e.get(key) is True for key in ("freshChat", "placeholderVerified", "withinRecordingWindow")):
                raise RuntimeError("Owner rehearsal is stale or incomplete")
        if not all(evidence["confidential"].get(key) is True for key in ("recreated", "chatAfterRecreation", "placementRejection", "initdataRejection")):
            raise RuntimeError("Confidential recreation/admission rehearsal incomplete")
        return evidence
    check("Owner and recreation rehearsal", rehearsal)
    d.save("verification.json", result)
    if not all(c["pass"] for c in result["checks"]):
        raise RuntimeError("Recording gates have not all passed; see .state/verification.json")
    print("All recording gates passed for the recorded pod identities.")
