#!/usr/bin/env bash
# Configure Gmail OAuth refresh for the gmail-write provider on the integrations VM.
#
# This script uploads the OAuth client JSON and refresh token to the
# integrations VM, verifies the gmail-write provider profile has refresh
# support, configures the refresh material, rotates the token, and
# restarts the gmail-write sandbox.
#
# Prerequisites:
#   - Integrations VM deployed with gmail-write provider already created
#   - A Desktop OAuth client JSON from your GCP project
#   - A gog token export with a valid refresh_token (gmail.compose scope)
#     Generate with: gog auth add <email> --services gmail \
#       --extra-scopes="https://www.googleapis.com/auth/gmail.compose" \
#       --manual --force-consent
#     Export with: gog auth tokens export <email> --out <path>
#
# Usage (non-interactive):
#   ./scripts/configure-gmail-write-refresh.sh \
#     --client-json /path/to/client_secret.json \
#     --token-export /path/to/gog-token-export.json
#
# Usage (interactive):
#   ./scripts/configure-gmail-write-refresh.sh
#
# Environment overrides:
#   NS                 — OpenShift namespace (default: current project)
#   INTEGRATIONS_VM    — VM name (default: openshell-saw-integ)
#   SSH_KEY_PATH       — SSH private key (default: ~/.generated-ssh-keys/sandbox-ssh)
set -euo pipefail

PROVIDER_NAME="gmail-write"
CREDENTIAL_KEY="access_token"
CREDENTIAL_KEY_ALT="GMAIL_WRITE_TOKEN"
SANDBOX_NAME="gmail-write"
SANDBOX_BINARY="/sandbox/gmail-write-proxy"
SANDBOX_PORT="18081"
BEARER_HEADER="x-forge-mail-bearer"
BEARER_SECRET="gmail-write-frontdoor"
SYSTEMD_SERVICE="openshell-sandbox-gmail-write"

NS="${NS:-$(oc project -q 2>/dev/null || echo openshell-agents)}"
INTEGRATIONS_VM="${INTEGRATIONS_VM:-openshell-saw-integ}"
SSH_KEY_PATH="${SSH_KEY_PATH:-${HOME}/.generated-ssh-keys/sandbox-ssh}"
SSH_USER="${SSH_USER:-cloud-user}"
CLIENT_JSON="${CLIENT_JSON:-}"
TOKEN_EXPORT="${TOKEN_EXPORT:-}"
GMAIL_ACCOUNT="${GMAIL_ACCOUNT:-}"
PF_PID=""

cleanup_on_exit() {
  [[ -n "${PF_PID}" ]] && kill "${PF_PID}" 2>/dev/null || true
}
trap cleanup_on_exit EXIT

usage() {
  echo "Usage: $0 [OPTIONS]"
  echo ""
  echo "Options:"
  echo "  --client-json PATH    OAuth Desktop client JSON"
  echo "  --token-export PATH   gog token export JSON (must have refresh_token)"
  echo "  --gmail-account EMAIL Gmail account (for prompts)"
  echo "  --namespace NS        OpenShift namespace (default: current project)"
  echo "  --vm NAME             Integrations VM name (default: openshell-saw-integ)"
  echo "  --ssh-key PATH        SSH private key path"
  echo "  -h, --help            Show this help"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --client-json)  CLIENT_JSON="$2"; shift 2 ;;
    --token-export) TOKEN_EXPORT="$2"; shift 2 ;;
    --gmail-account) GMAIL_ACCOUNT="$2"; shift 2 ;;
    --namespace)    NS="$2"; shift 2 ;;
    --vm)           INTEGRATIONS_VM="$2"; shift 2 ;;
    --ssh-key)      SSH_KEY_PATH="$2"; shift 2 ;;
    -h|--help)      usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 1 ;;
  esac
done

ssh_cmd() {
  ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    -i "${SSH_KEY_PATH}" -p 2222 "${SSH_USER}@127.0.0.1" "$@" 2>&1
}

scp_cmd() {
  scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    -i "${SSH_KEY_PATH}" -P 2222 "$@" 2>&1
}

# --- Prompt for missing files ---
if [[ -z "${CLIENT_JSON}" ]]; then
  default_client="${HOME}/gog/client_secret.json"
  if [[ -f "${default_client}" ]]; then
    CLIENT_JSON="${default_client}"
    echo "Using client JSON: ${CLIENT_JSON}"
  else
    read -rp "Path to OAuth Desktop client JSON: " CLIENT_JSON
  fi
fi

if [[ -z "${TOKEN_EXPORT}" ]]; then
  default_export="${HOME}/gog/gog-token-export-compose.json"
  if [[ -f "${default_export}" ]]; then
    TOKEN_EXPORT="${default_export}"
    echo "Using token export: ${TOKEN_EXPORT}"
  else
    read -rp "Path to gog token export JSON (gmail.compose scope): " TOKEN_EXPORT
  fi
fi

# --- Validate files ---
[[ -f "${CLIENT_JSON}" ]] || { echo "ERROR: Client JSON not found: ${CLIENT_JSON}" >&2; exit 1; }
[[ -f "${TOKEN_EXPORT}" ]] || { echo "ERROR: Token export not found: ${TOKEN_EXPORT}" >&2; exit 1; }
jq -er '.installed.client_id' "${CLIENT_JSON}" >/dev/null || { echo "ERROR: Invalid client JSON (missing .installed.client_id)" >&2; exit 1; }
jq -er '.refresh_token' "${TOKEN_EXPORT}" >/dev/null || { echo "ERROR: Token export missing refresh_token" >&2; exit 1; }

echo "============================================================"
echo "Configuring Gmail write refresh (${PROVIDER_NAME})"
echo "  Namespace:  ${NS}"
echo "  VM:         ${INTEGRATIONS_VM}"
echo "  Provider:   ${PROVIDER_NAME}"
echo "  Sandbox:    ${SANDBOX_NAME}"
echo "  Port:       ${SANDBOX_PORT}"
echo "============================================================"

# --- Start port-forward ---
echo "Starting port-forward to ${INTEGRATIONS_VM}..."
pkill -f "virtctl.*port-forward.*${INTEGRATIONS_VM}.*2222" 2>/dev/null || true
sleep 1
virtctl port-forward -n "${NS}" "vm/${INTEGRATIONS_VM}" 2222:22 &
PF_PID=$!
sleep 5
if ! kill -0 "${PF_PID}" 2>/dev/null; then
  echo "ERROR: Port-forward failed to start." >&2
  exit 1
fi

echo "Verifying SSH connectivity..."
ssh_cmd "echo 'Connected to \$(hostname)'"

# --- Step 1: Copy credential files to the VM ---
echo "Copying credential files to VM..."
scp_cmd "${CLIENT_JSON}" "${SSH_USER}@127.0.0.1:/tmp/gog-client-secret.json"
scp_cmd "${TOKEN_EXPORT}" "${SSH_USER}@127.0.0.1:/tmp/gog-token-export.json"
ssh_cmd "chmod 600 /tmp/gog-client-secret.json /tmp/gog-token-export.json"
echo "  Files copied."

# --- Step 2: Verify provider profile has refresh support ---
echo "Checking ${PROVIDER_NAME} provider profile for refresh support..."
ssh_cmd "export PATH=\"\$HOME/.local/bin:\$PATH\"
openshell gateway select openshell-local >/dev/null 2>&1
if openshell provider profile export ${PROVIDER_NAME} 2>/dev/null | grep -q oauth2_refresh_token; then
  echo '  Profile already has refresh support.'
else
  echo '  ERROR: ${PROVIDER_NAME} profile missing oauth2_refresh_token refresh block.'
  echo '  Ensure DEPLOY_GOV_PROFILES=true was used during deploy-config.'
  exit 1
fi"

# --- Step 3: Configure refresh material ---
echo "Configuring refresh material..."
ssh_cmd "set -e
export PATH=\"\$HOME/.local/bin:\$PATH\"
openshell gateway select openshell-local >/dev/null 2>&1
GOG_CLIENT_ID=\"\$(jq -er '.installed.client_id' /tmp/gog-client-secret.json)\"
export GOG_CLIENT_SECRET=\"\$(jq -er '.installed.client_secret' /tmp/gog-client-secret.json)\"
export GOG_REFRESH_TOKEN=\"\$(jq -er '.refresh_token' /tmp/gog-token-export.json)\"
openshell provider refresh configure ${PROVIDER_NAME} \\
  --credential-key ${CREDENTIAL_KEY} \\
  --strategy oauth2-refresh-token \\
  --material \"client_id=\${GOG_CLIENT_ID}\" \\
  --secret-material-env client_secret=GOG_CLIENT_SECRET \\
  --secret-material-env refresh_token=GOG_REFRESH_TOKEN
if openshell provider get ${PROVIDER_NAME} -v 2>/dev/null | grep -q 'Credential keys:.*${CREDENTIAL_KEY_ALT}'; then
  openshell provider refresh configure ${PROVIDER_NAME} \\
    --credential-key ${CREDENTIAL_KEY_ALT} \\
    --strategy oauth2-refresh-token \\
    --material \"client_id=\${GOG_CLIENT_ID}\" \\
    --secret-material-env client_secret=GOG_CLIENT_SECRET \\
    --secret-material-env refresh_token=GOG_REFRESH_TOKEN
fi
echo '  Refresh configured.'"

# --- Step 4: Rotate and verify ---
echo "Rotating token..."
ssh_cmd "export PATH=\"\$HOME/.local/bin:\$PATH\"
openshell gateway select openshell-local >/dev/null 2>&1
openshell provider refresh rotate ${PROVIDER_NAME} --credential-key ${CREDENTIAL_KEY} || true"
ssh_cmd "export PATH=\"\$HOME/.local/bin:\$PATH\"
openshell gateway select openshell-local >/dev/null 2>&1
if openshell provider get ${PROVIDER_NAME} -v 2>/dev/null | grep -q 'Credential keys:.*${CREDENTIAL_KEY_ALT}'; then
  openshell provider refresh rotate ${PROVIDER_NAME} --credential-key ${CREDENTIAL_KEY_ALT}
fi"

echo "Checking refresh status..."
ssh_cmd "export PATH=\"\$HOME/.local/bin:\$PATH\"
openshell gateway select openshell-local >/dev/null 2>&1
openshell provider refresh status ${PROVIDER_NAME}"

# --- Step 5: Restart gmail-write sandbox ---
BEARER="$(kubectl get secret "${BEARER_SECRET}" -n "${NS}" -o jsonpath='{.data.bearer}' 2>/dev/null | base64 -d || true)"
BEARER_SHA256="$(echo -n "${BEARER}" | sha256sum | cut -d ' ' -f1)"

echo "Restarting ${SANDBOX_NAME} sandbox..."
ssh_cmd "bash -s" << EOF
set +e
export PATH="\$HOME/.local/bin:\$PATH"
openshell gateway select openshell-local >/dev/null 2>&1 || true

timeout -k 2 20 systemctl --user stop ${SYSTEMD_SERVICE}.service >/dev/null 2>&1 || true
timeout -k 2 10 systemctl --user reset-failed ${SYSTEMD_SERVICE}.service >/dev/null 2>&1 || true
sleep 1
timeout -k 2 25 systemctl --user start ${SYSTEMD_SERVICE}.service || true

echo "  Waiting for proxy HTTP 200 on port ${SANDBOX_PORT}..."
ok=1
for i in \$(seq 1 30); do
  code=\$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 \
    "http://127.0.0.1:${SANDBOX_PORT}/healthz" 2>/dev/null || true)
  if [[ "\$code" == "200" ]]; then
    echo "  proxy healthz: 200"
    ok=0
    break
  fi
  sleep 1
done
if [[ \$ok -ne 0 ]]; then
  echo "  ERROR: ${SANDBOX_NAME} proxy did not return HTTP 200 on :${SANDBOX_PORT}"
  systemctl --user status ${SYSTEMD_SERVICE}.service --no-pager || true
  journalctl --user -u ${SYSTEMD_SERVICE}.service -n 20 --no-pager || true
fi
exit \$ok
EOF
restart_rc=$?

# --- Step 6: Clean up credential files on VM ---
echo "Cleaning up credential files on VM..."
ssh_cmd "rm -f /tmp/gog-client-secret.json /tmp/gog-token-export.json" || true

if [[ "${restart_rc}" -ne 0 ]]; then
  echo "ERROR: Gmail write refresh configured, but proxy did not return HTTP 200."
  exit 1
fi

echo ""
echo "============================================================"
echo "Gmail write OAuth refresh configured successfully."
echo "The gateway will auto-refresh the access token before expiry."
echo "${SANDBOX_NAME} sandbox was restarted (new credential session)."
echo "============================================================"
