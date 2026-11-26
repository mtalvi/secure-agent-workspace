"""Inference lifecycle. Secret material is only passed on stdin or in files."""
import base64
import gzip
import hashlib
import json
import os
import secrets
import time
import urllib.error
import urllib.request

NS = "saw-demo-inference"


def initdata(d):
    policy = (d.HERE / "policy.rego").read_text()
    cdh = (d.HERE / "cdh.toml").read_text()
    aa = (d.HERE / "aa.toml").read_text()
    raw = ('version = "0.1.0"\nalgorithm = "sha256"\n[data]\n"aa.toml" = \'\'\'\n'
           + aa + "'''\n\"cdh.toml\" = '''\n"
           + cdh + "'''\n\"policy.rego\" = '''\n"
           + policy + "'''\n").encode()
    d.save("initdata-evidence.json", {"sha256": hashlib.sha256(raw).hexdigest(),
           "policySHA256": hashlib.sha256(policy.encode()).hexdigest(),
           "cdhSHA256": hashlib.sha256(cdh.encode()).hexdigest(),
           "aaSHA256": hashlib.sha256(aa.encode()).hexdigest()})
    return base64.b64encode(gzip.compress(raw, mtime=0)).decode()


def secret(d, name, data, kind="Opaque"):
    d.apply({"apiVersion": "v1", "kind": "Secret", "type": kind,
             "metadata": {"name": name, "namespace": NS}, "stringData": data})


def route(d, acme=False):
    spec = {"host": d.HOST, "to": {"kind": "Service", "name": "acme" if acme else "qwen"},
            "port": {"targetPort": "http" if acme else "https"}}
    if not acme:
        spec["tls"] = {"termination": "passthrough", "insecureEdgeTerminationPolicy": "None"}
    d.apply({"apiVersion": "route.openshift.io/v1", "kind": "Route",
             "metadata": {"name": "qwen", "namespace": NS}, "spec": spec})


def acme_auth(d):
    token, validation = os.environ["CERTBOT_TOKEN"], os.environ["CERTBOT_VALIDATION"]
    pins = json.loads((d.HERE / "pins.json").read_text())
    d.apply({"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "acme", "namespace": NS},
             "data": {token: validation}})
    d.apply({"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "acme", "namespace": NS},
             "spec": {"replicas": 1, "selector": {"matchLabels": {"app": "melvin-acme"}}, "template": {
                 "metadata": {"labels": {"app": "melvin-acme", d.LABEL: d.DEMO},
                              "annotations": {"challenge": hashlib.sha256(token.encode()).hexdigest()}},
                 "spec": {"automountServiceAccountToken": False,
                          "securityContext": {"runAsNonRoot": True, "seccompProfile": {"type": "RuntimeDefault"}},
                          "containers": [{"name": "http", "image": pins["pythonImage"],
                              "command": ["python3", "-m", "http.server", "8080", "--directory", "/challenge"],
                              "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}},
                              "ports": [{"name": "http", "containerPort": 8080}],
                              "volumeMounts": [{"name": "challenge", "mountPath": "/challenge/.well-known/acme-challenge", "readOnly": True}]}],
                          "volumes": [{"name": "challenge", "configMap": {"name": "acme"}}]}}}})
    d.apply({"apiVersion": "v1", "kind": "Service", "metadata": {"name": "acme", "namespace": NS},
             "spec": {"selector": {"app": "melvin-acme"}, "ports": [{"name": "http", "port": 8080, "targetPort": "http"}]}})
    route(d, acme=True)
    d.oc("rollout", "status", "deployment/acme", "-n", NS, "--timeout=180s", timeout=195)
    url = "http://" + d.HOST + "/.well-known/acme-challenge/" + token
    for _ in range(18):
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                if response.read().decode() == validation:
                    print("Public port 80 HTTP-01 challenge is reachable")
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(5)
    raise RuntimeError("HTTP-01 prerequisite failed: public port 80 must reach this cluster's ingress")


def acme_cleanup(d):
    # Certbot invokes this even after validation fails. Keep the public challenge
    # objects until issuance succeeds so its error can be diagnosed.
    pass


def deploy(d):
    d.namespace(NS)
    d.runtimeclass()
    certdir = d.STATE / "acme" / "config" / "live" / d.HOST
    if not (certdir / "fullchain.pem").exists():
        command = ["uv", "tool", "run", "--from", "certbot==5.1.0", "certbot", "certonly",
                   "--manual", "--preferred-challenges", "http", "--non-interactive", "--agree-tos",
                   "--register-unsafely-without-email", "--manual-auth-hook", str(d.HERE / "demo.sh") + " acme-auth",
                   "--manual-cleanup-hook", str(d.HERE / "demo.sh") + " acme-cleanup",
                   "--config-dir", str(d.STATE / "acme/config"), "--work-dir", str(d.STATE / "acme/work"),
                   "--logs-dir", str(d.STATE / "acme/logs"), "-d", d.HOST]
        result = d.run(*command, check=False, timeout=600)
        if result.returncode:
            # ACME logs contain account URLs and challenge tokens; keep local.
            d.save("acme-result.json", {"returncode": result.returncode,
                                      "stdout": result.stdout, "stderr": result.stderr})
            raise RuntimeError("ACME issuance failed; diagnostic details are in private .state/acme-result.json")
    secret(d, "inference-tls", {"tls.crt": (certdir / "fullchain.pem").read_text(),
                               "tls.key": (certdir / "privkey.pem").read_text()}, "kubernetes.io/tls")
    if not (d.STATE / "inference-keys.json").exists():
        d.save("inference-keys.json", {user: "saw-demo-" + secrets.token_urlsafe(36)
                                      for user in ("baseline", "confidential")})
    secret(d, "inference-auth", {"keys": "\n".join(d.load("inference-keys.json").values()) + "\n"})
    values = json.loads((d.HERE / "pins.json").read_text())
    values.update(host=d.HOST, initdata=initdata(d))
    path = d.save("inference-values.json", values)
    d.run("helm", "upgrade", "--install", "melvin-inference", str(d.HERE / "inference"),
          "-n", NS, "-f", str(path), timeout=180)
    route(d)
    for kind in ("deployment", "service", "configmap"):
        obj = d.get(kind, "acme", NS)
        if obj and obj["metadata"].get("labels", {}).get(d.LABEL) == d.DEMO:
            d.oc("delete", kind, "acme", "-n", NS, "--wait=false")
    print("Inference release submitted. Wait for the confidential peer VM and model download; run verify afterward.")
