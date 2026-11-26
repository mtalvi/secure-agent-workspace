"""Two isolated, reviewed workspace registrations using the existing platform."""
import base64
import hashlib
import json
import os
import re
import secrets
import shlex
import ssl
import time
import urllib.parse
import urllib.request
import uuid

import yaml
import inference

OWNERS = ("baseline", "confidential")
GOV = "saw-demo-platform"
INSTALL = "saw-demo-install"


def saved_accounts(d):
    saved = d.load("accounts.json") if (d.STATE / "accounts.json").exists() else {}
    if set(saved) - set(OWNERS):
        raise RuntimeError("Saved account names are outside the demo allowlist")
    for account in saved.values():
        if not re.fullmatch("[a-f0-9]{16}", account.get("registration", "")):
            raise RuntimeError("Invalid saved demo registration")
        for key in ("subject", "installationID"):
            if key in account and str(uuid.UUID(account[key])) != account[key]:
                raise RuntimeError("Invalid saved demo identity")
    return saved


def keycloak(d):
    secret = d.get("secret", "openshell-keycloak-initial-admin", "saw-keycloak")["data"]
    cert = d.get("secret", "router-certs-default", "openshift-ingress")["data"]["tls.crt"]
    context = ssl.create_default_context(cadata=base64.b64decode(cert).decode())
    base = "https://openshell-keycloak-ingress-saw-keycloak.apps." + d.DOMAIN
    form = urllib.parse.urlencode({"client_id": "admin-cli", "grant_type": "password",
        "username": base64.b64decode(secret["username"]).decode(),
        "password": base64.b64decode(secret["password"]).decode()}).encode()
    with urllib.request.urlopen(urllib.request.Request(base + "/realms/master/protocol/openid-connect/token", data=form),
                                context=context, timeout=30) as response:
        token = json.load(response)["access_token"]

    def request(method, path, data=None):
        req = urllib.request.Request(base + "/admin/realms/openshell" + path,
                data=json.dumps(data).encode() if data is not None else None, method=method,
                headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, context=context, timeout=30) as response:
            raw = response.read()
            return json.loads(raw) if raw else None
    return request


def accounts(d):
    request = keycloak(d)
    saved = saved_accounts(d)
    role = request("GET", "/roles/openshell-user")
    for owner in OWNERS:
        found = request("GET", "/users?exact=true&username=" + owner)
        # This realm strips unmanaged user-profile attributes. The immutable
        # subject recorded immediately after our create is the ownership proof;
        # never adopt an existing username without that exact saved subject.
        if found and (len(found) != 1 or found[0]["id"] != saved.get(owner, {}).get("subject")
                      or found[0]["username"] != owner):
            raise RuntimeError("Existing Keycloak account is not demo-owned: " + owner)
        if owner not in saved:
            saved[owner] = {"password": "Aa9!" + secrets.token_urlsafe(24), "registration": secrets.token_hex(8)}
            d.save("accounts.json", saved)
        if not found:
            request("POST", "/users", {"username": owner, "enabled": True, "emailVerified": True,
                    "email": owner + "@openshell.local", "firstName": owner.title(), "lastName": "Demo",
                    "requiredActions": [],
                    "credentials": [{"type": "password", "temporary": False, "value": saved[owner]["password"]}]})
            found = request("GET", "/users?exact=true&username=" + owner)
        subject = found[0]["id"]
        if saved[owner].get("subject", subject) != subject:
            raise RuntimeError("Demo account identity changed")
        saved[owner]["subject"] = subject
        request("POST", "/users/" + subject + "/role-mappings/realm", [role])
        reg = saved[owner]["registration"]
        saved[owner]["installationID"] = str(uuid.uuid5(uuid.NAMESPACE_URL, "urn:saw:portal:" + owner + ":" + reg))
        d.save("accounts.json", saved)
    return saved


def vault_write(d, owner, registration, name, data):
    if owner not in OWNERS or name not in {"inference", "web-search"} or not re.fullmatch("[a-f0-9]{16}", registration):
        raise RuntimeError("Vault target outside the demo allowlist")
    keys = d.get("secret", "vaultkeys", "imperative")["data"]["vault_data_json"]
    token = json.loads(base64.b64decode(keys))["root_token"]
    path = "hub/saw-" + owner + "/" + registration + "/" + name
    script = 'read -r VAULT_TOKEN; export VAULT_TOKEN; vault kv put -mount=secret ' + shlex.quote(path) + ' - >/dev/null'
    d.oc("exec", "-i", "-n", "vault", "vault-0", "--", "sh", "-c", script,
         data=token + "\n" + json.dumps(data))


def governance(d, pins):
    d.namespace(GOV)
    d.run("helm", "upgrade", "--install", "melvin-governance-policy", str(d.ROOT / "charts/governance-policy"), "-n", GOV)
    profile = yaml.safe_load((d.ROOT / "overrides/governance-profiles/custom-inference.yaml").read_text())
    profile["endpoints"] = [{"host": d.HOST, "port": 443, "protocol": "rest", "enforcement": "enforce", "access": "read-write"}]
    d.apply({"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "governance-interceptor-extra-profiles", "namespace": GOV},
             "data": {"custom-inference.yaml": yaml.safe_dump(profile)}})
    values = d.save("governance-values.json", {"image": pins["governanceImage"], "gatewayEndpoint": "",
        "kubernetesGateways": [{"namespace": "saw-" + owner, "gatewayId": owner} for owner in OWNERS]})
    d.run("helm", "upgrade", "--install", "melvin-governance", str(d.ROOT / "charts/governance-interceptor"), "-n", GOV, "-f", str(values))
    d.oc("rollout", "status", "deployment/governance-interceptor", "-n", GOV, "--timeout=180s", timeout=195)


def values(d, owner, account, pins, revision):
    base = yaml.safe_load((d.ROOT / "overrides/saw-container-defaults.yaml").read_text())
    defaults = base["containerDefaults"]
    defaults["governanceNamespace"] = GOV
    defaults["auditReceipts"] = True
    # The standalone installer checks the site before saw-users derives these
    # same bindings from the profile catalog. Web search is disabled below.
    defaults["credentialBindings"] = [{"secret": "inference", "key": "api_key", "type": "custom-inference"}]
    defaults["profileImageOverrides"] = {"custom-inference": {"notebook": pins["workloadImage"]}}
    defaults["automaticEnrollment"].update(image=pins["runtimeImage"], revision=revision, buildNamespace=INSTALL)
    defaults["stack"] = {
        "identity": {"revision": revision},
        "gateway": {"revision": revision, "values": {
            "oidc": {"caConfigMap": owner + "-oidc-ca", "caBundle": base64.b64decode(
                d.get("secret", "router-certs-default", "openshift-ingress")["data"]["tls.crt"]).decode()},
            "kubernetes": {
            "gateway": {"image": pins["gatewayImage"]}, "supervisor": {"image": pins["supervisorImage"]},
            "workload": {"runtimeImage": pins["sandboxRuntimeImage"]},
            "storage": {"className": "azurefile-csi", "gatewayClassName": "managed-csi",
                        "gatewayClaimName": owner + "-gateway-sqlite"}}}},
        "policy": {"revision": revision, "values": {"network": {"publicHTTPS": True}}},
        "audit": {"revision": revision, "values": {"storageClass": "azurefile-csi", "exchangeStorageClass": "azurefile-csi"}}}
    site = {"subject": account["subject"]}
    if owner == "confidential":
        rc = d.get("runtimeclass", "kata-cc")
        payload = {k: rc.get(k) for k in ("handler", "overhead", "scheduling")}
        sha = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        site["kataRuntimeClass"] = {"name": "kata-cc", "handler": "kata-remote", "uid": rc["metadata"]["uid"], "sha256": sha}
        site["kataPodAnnotations"] = {role: {
            "io.katacontainers.config.hypervisor.cc_init_data": inference.initdata(d),
            "io.katacontainers.config.hypervisor.machine_type": size}
            for role, size in (("workload", "Standard_DC4as_v5"), ("supervisor", "Standard_DC2as_v5"))}
        d.save("guest-annotations.json", site["kataPodAnnotations"])
    reg = account["registration"]
    base["global"] = {"repoURL": "https://github.com/mtalvi/secure-agent-workspace.git", "targetRevision": revision,
                      "vpArgoNamespace": "vp-gitops", "clusterDomain": d.DOMAIN}
    base["namespaceLabels"] = {d.LABEL: d.DEMO}
    base["users"] = [{"name": owner, "ownerSubject": account["subject"], "registration": reg,
        "installationID": account["installationID"], "vaultPrefix": "secret/data/hub/saw-" + owner + "/" + reg,
        "profiles": ["custom-inference"], "pruneOnRemove": True, "intentGeneration": 1, "lifecycleVersion": 1,
        "desiredState": "Running", "restartGeneration": 1, "webSearch": {"enabled": False},
        "initialModelInputs": {"inference.model": pins["model"]}, "modelInputs": {"inference.model": pins["model"]},
        "customInferenceURL": "https://" + d.HOST + "/v1", "values": {"deployment": {"mode": "kata" if owner == "confidential" else "container"}}}]
    base["containerSites"] = {owner: site}
    return base


def install(d, owner, path, action="apply"):
    result = d.run("uv", "run", "--project", str(d.ROOT), "--locked", "python", str(d.ROOT / "scripts/container-workspace.py"),
          action, "--values", str(path), "--name", owner, "--namespace", INSTALL,
          env={**os.environ, "DEPLOYMENT_MODE": "kata" if owner == "confidential" else "container"},
          cwd=str(d.ROOT), timeout=600, check=False)
    if result.returncode:
        d.save(owner + "-install-result.json", {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr})
        raise RuntimeError(owner + " workspace " + action + " failed; inspect private .state/" + owner + "-install-result.json")
    print(owner + ": " + result.stdout.strip(), flush=True)


def review(d, owner, account):
    # ApplicationSet may reconcile before its bootstrap Secret exists. Request
    # a fresh reconciliation only for this release after identity provisioning.
    projector_owner = hashlib.sha256((INSTALL + "/workspace-" + owner).encode()).hexdigest()[:20]
    projector = "saw-projector-" + projector_owner
    for _ in range(30):
        identity = d.get("secret", projector, "vp-gitops")
        if identity:
            appset = d.get("applicationset", projector, "vp-gitops")
            if any(obj["metadata"].get("labels", {}).get("saw.redhat.com/projector-owner") != projector_owner
                   for obj in (identity, appset or {"metadata": {}})):
                raise RuntimeError("Projector identity does not belong to the demo release")
            d.oc("annotate", "applicationset", projector, "-n", "vp-gitops",
                 "argocd.argoproj.io/application-set-refresh=true", "--overwrite")
            break
        time.sleep(2)
    else:
        raise RuntimeError("Demo projector identity initialization did not complete")
    args = ["uv", "run", "--project", str(d.ROOT), "--locked", "python", str(d.ROOT / "scripts/portal-container/inspect-boundary.py"),
            "--owner", owner, "--registration", account["registration"], "--installation-id", account["installationID"],
            "--governance-namespace", GOV, "--api-server", d.API]
    if owner == "confidential":
        args += ["--kata-runtime-class", "kata-cc", "--kata-annotations", str(d.STATE / "guest-annotations.json")]
    # Wait for the actual observed boundary and rollout checks; do not invent hashes.
    for _ in range(90):
        result = d.run(*args, check=False, timeout=90)
        if not result.returncode:
            reference = json.loads(result.stdout)
            break
        d.save(owner + "-review-wait.json", {"stderr": result.stderr})
        time.sleep(10)
    else:
        raise RuntimeError(owner + " foundation did not become reviewable; no enrollment was authorized")
    objects = json.loads(d.run(*args, "--show-objects", timeout=90).stdout)
    d.save(owner + "-boundary-objects.json", objects)
    # The reference includes complete normalized admission specs. Assert the
    # demo's concrete runtime/image/host choices before authorizing it.
    gateway = objects["gatewayConfig"]["data"]["gateway.toml"]
    pins = json.loads((d.HERE / "pins.json").read_text())
    for key, image in (("gateway", pins["gatewayImage"]), ("governance", pins["governanceImage"])):
        if objects[key]["spec"]["template"]["spec"]["containers"][0]["image"] != image:
            raise RuntimeError(key + " image differs from the reviewed pin")
    if GOV not in gateway:
        raise RuntimeError("Gateway is not bound to demo governance")
    if owner == "confidential":
        import tomllib
        driver = tomllib.loads(gateway)["openshell"]["drivers"]["kubernetes"]
        expected = d.load("guest-annotations.json")
        for role in expected:
            if driver.get(role + "_pod_annotations") != expected[role]:
                raise RuntimeError("Guest annotation review mismatch")
    digest = d.run(*args, "--profile-digest", timeout=90).stdout.strip()
    if not re.fullmatch("[a-f0-9]{64}", digest):
        raise RuntimeError("Invalid observed BOM digest")
    d.save(owner + "-review.json", {"reference": reference, "profileDigest": digest})
    return reference, digest


def deploy(d):
    pins = json.loads((d.HERE / "pins.json").read_text())
    for name in ("gatewayImage", "runtimeImage", "governanceImage"):
        if not re.fullmatch(r"[\w./-]+@sha256:[a-f0-9]{64}", pins.get(name, "")):
            raise RuntimeError("Publish and pin " + name + " before deploying users")
    revision = d.run("git", "-C", str(d.ROOT), "rev-parse", "HEAD").stdout.strip()
    if d.run("git", "-C", str(d.ROOT), "status", "--porcelain", "--untracked-files=no").stdout:
        raise RuntimeError("Commit and push reviewed code before deploying users")
    remote = d.run("git", "-C", str(d.ROOT), "ls-remote", "origin", "refs/heads/codex/melvin-confidential-demo").stdout
    if not remote.startswith(revision + "\t"):
        raise RuntimeError("Demo commit is not the published demo branch tip")
    d.namespace(INSTALL)
    d.runtimeclass()
    governance(d, pins)
    owners = accounts(d)
    for owner, account in owners.items():
        path = d.STATE / (owner + "-values.json")
        if path.exists():
            config = d.load(path.name)
            if config.get("global", {}).get("targetRevision") != revision:
                raise RuntimeError(owner + " saved values reference a different commit; archive them and repeat foundation review")
        else:
            config = values(d, owner, account, pins, revision)
            d.save(path.name, config)
        vault_write(d, owner, account["registration"], "inference",
                    {"api_key": d.load("inference-keys.json")[owner], "url": "https://" + d.HOST + "/v1"})
        vault_write(d, owner, account["registration"], "web-search", {"api_key": "DUMMY-WEB-SEARCH-DISABLED"})
        install(d, owner, path)
        automatic = config["containerSites"][owner].setdefault("automaticEnrollment", {})
        if automatic.get("enabled") is not True:
            print(owner + ": waiting for foundation, then reviewing the observed boundary", flush=True)
            reference, digest = review(d, owner, account)
            automatic.update(enabled=True, autoApprove=False, reference=reference, approvedProfiles={"custom-inference": digest})
            d.save(path.name, config)
            install(d, owner, path)
    d.save("deployment-revision.json", {"revision": revision, "pins": pins})
    print("Both owner releases submitted with reviewed boundary and BOM digests. Run verify before recording.")
