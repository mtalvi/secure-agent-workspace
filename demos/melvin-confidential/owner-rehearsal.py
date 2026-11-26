#!/usr/bin/env python3
"""Real owner API rehearsal; no Kubernetes exec and no secret output.

Run with the pinned grpcio/grpcio-tools/protobuf versions in README. The local
API tunnel still verifies the gateway's CA and its exact service DNS identity.
This proves application behavior; it does not replace browser recording QA.
"""
import argparse
import base64
import contextlib
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import ssl
import subprocess
import sys
import time
import urllib.parse
import urllib.request

import demo as d


def protocol():
    import grpc_tools
    from grpc_tools import protoc
    lock = json.loads((d.HERE / "owner-protocol-lock.json").read_text())
    source = d.STATE / "owner-proto"
    out = d.STATE / "owner-sdk"
    source.mkdir(exist_ok=True)
    out.mkdir(exist_ok=True)
    revision = json.loads((d.HERE / "pins.json").read_text())["gatewaySource"]
    for name, expected in lock.items():
        path = source / name
        if not path.exists():
            with urllib.request.urlopen("https://raw.githubusercontent.com/opendatahub-io/openshell/"
                    + revision + "/proto/" + name, timeout=30) as response:
                path.write_bytes(response.read())
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise RuntimeError("Owner protocol checksum mismatch")
    args = ["protoc", "-I" + str(source), "-I" + str(Path(grpc_tools.__file__).parent / "_proto"),
            "--python_out=" + str(out), "--grpc_python_out=" + str(out),
            *[str(source / name) for name in lock]]
    if protoc.main(args):
        raise RuntimeError("Owner protocol generation failed")
    sys.path.insert(0, str(out))


def owner_token(owner):
    account = d.users.saved_accounts(d)[owner]
    cert = d.get("secret", "router-certs-default", "openshift-ingress")["data"]["tls.crt"]
    context = ssl.create_default_context(cadata=base64.b64decode(cert).decode())
    issuer = "https://openshell-keycloak-ingress-saw-keycloak.apps." + d.DOMAIN + "/realms/openshell"
    form = urllib.parse.urlencode({"client_id": "openshell-cli", "grant_type": "password",
        "username": owner, "password": account["password"], "scope": "openid"}).encode()
    req = urllib.request.Request(issuer + "/protocol/openid-connect/token", data=form)
    with urllib.request.urlopen(req, context=context, timeout=30) as response:
        token = json.load(response)["access_token"]
    claims = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))
    if claims["sub"] != account["subject"] or claims["iss"] != issuer:
        raise RuntimeError("Owner identity mismatch")
    return token, account["subject"]


@contextlib.contextmanager
def client(owner):
    import grpc
    import openshell_pb2 as pb
    import openshell_pb2_grpc as rpc
    ns = "saw-" + owner
    ca = base64.b64decode(d.get("secret", owner + "-server", ns)["data"]["ca.crt"])
    token, subject = owner_token(owner)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    log = d.STATE / (owner + "-port-forward.log")
    with log.open("w") as output:
        proc = subprocess.Popen(["oc", "-n", ns, "port-forward", "service/" + owner + "-gateway",
                                 str(port) + ":17670", "--address=127.0.0.1"], stdout=output, stderr=output)
        authority = owner + "-gateway." + ns + ".svc"
        channel = grpc.secure_channel("127.0.0.1:" + str(port), grpc.ssl_channel_credentials(ca),
            options=(("grpc.ssl_target_name_override", authority), ("grpc.default_authority", authority)))
        try:
            grpc.channel_ready_future(channel).result(timeout=25)
            stub = rpc.OpenShellStub(channel)
            metadata = (("authorization", "Bearer " + token),)
            identity = stub.GetCurrentUser(pb.GetCurrentUserRequest(), metadata=metadata, timeout=15)
            if identity.subject != subject:
                raise RuntimeError("Gateway did not authenticate the expected owner")
            yield stub, metadata
        finally:
            channel.close()
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


def rehearse(owner):
    import datamodel_pb2 as dm
    import openshell_pb2 as pb
    from google.protobuf.duration_pb2 import Duration
    workload = d.verify.pod(d, owner, "workload")
    if not d.verify.ready(workload):
        raise RuntimeError("Owner workload is not Ready")
    with client(owner) as (stub, metadata):
        items = stub.ListSandboxes(pb.ListSandboxesRequest(
            workspace_scope=dm.WorkspaceSelector(all_workspaces=dm.AllWorkspaces())), metadata=metadata, timeout=15).sandboxes
        if len(items) != 1:
            raise RuntimeError("Expected one demo sandbox for this owner")
        sandbox = items[0]
        scope = dm.WorkspaceSelector(workspace=sandbox.metadata.workspace)

        def execute(command):
            request = pb.ExecSandboxRequest(sandbox=sandbox.metadata.name, workspace_scope=scope,
                command=command, no_login_shell=True, execution_timeout=Duration(seconds=150))
            stdout, stderr, code = bytearray(), bytearray(), None
            for event in stub.ExecSandbox(request, metadata=metadata, timeout=165):
                kind = event.WhichOneof("payload")
                if kind == "stdout": stdout.extend(event.stdout.data)
                if kind == "stderr": stderr.extend(event.stderr.data)
                if kind == "exit": code = event.exit.exit_code
            # Output is not printed/stored until known inference keys are ruled out.
            for key in d.load("inference-keys.json").values():
                if key.encode() in stdout or key.encode() in stderr:
                    raise RuntimeError("Credential detected in owner process output; output suppressed")
            if code != 0:
                d.save(owner + "-owner-command-failure.json", {"exit": code,
                    "stdout": stdout.decode(errors="replace")[-4000:], "stderr": stderr.decode(errors="replace")[-4000:]})
                raise RuntimeError("Owner command failed; inspect private diagnostic file")
            return stdout.decode(errors="replace")

        probe = execute(["node", "-e", "const v=process.env.CUSTOM_INFERENCE_API_KEY||'';"
            "const ok=v.startsWith('openshell:resolve:env:')&&v.endsWith('CUSTOM_INFERENCE_API_KEY');"
            "console.log(JSON.stringify({placeholderVerified:ok}));process.exit(ok?0:1)"])
        placeholder = json.loads(probe)["placeholderVerified"]
        nonce = "SAW_DEMO_" + secrets.token_hex(5)
        script = """set -eu
export HOME=/sandbox/workspace OPENCLAW_HOME=/sandbox/workspace
export OPENCLAW_CONFIG_PATH=/sandbox/workspace/.openclaw/openclaw.json
export TMPDIR=/sandbox/workspace/.openclaw/state SQLITE_TMPDIR=/sandbox/workspace/.openclaw/state
export OPENCLAW_GATEWAY_TOKEN="$(node -e 'process.stdout.write(require(process.env.OPENCLAW_CONFIG_PATH).gateway.auth.token)')"
openclaw agent --agent main --message "$1"
"""
        started = time.monotonic()
        output = execute(["sh", "-c", script, "demo-chat",
            "In one sentence, explain why protecting API keys matters. End with the exact marker " + nonce])
        elapsed = round(time.monotonic() - started, 3)
        if nonce not in output:
            raise RuntimeError("Fresh OpenClaw response did not include the nonce")
        if d.verify.pod(d, owner, "workload")["metadata"]["uid"] != workload["metadata"]["uid"]:
            raise RuntimeError("Workload changed during rehearsal")
        evidence = {"ownerAuthentication": True, "transport": "verified gateway TLS through local API tunnel",
            "workloadUID": workload["metadata"]["uid"], "sandbox": sandbox.metadata.name,
            "workspace": sandbox.metadata.workspace, "freshChat": True, "placeholderVerified": placeholder,
            "seconds": elapsed, "withinRecordingWindow": elapsed < 40, "response": output[-3000:]}
        d.save(owner + "-owner-rehearsal.json", evidence)
        print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("owner", choices=d.users.OWNERS)
    args = parser.parse_args()
    d.guard()
    protocol()
    rehearse(args.owner)
