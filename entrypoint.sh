#!/usr/bin/env bash
set -euo pipefail

: "${GH_RUNNER_URL:?need GH_RUNNER_URL}"
: "${GH_APP_ID:?need GH_APP_ID}"
: "${GH_APP_INSTALLATION_ID:?need GH_APP_INSTALLATION_ID}"
: "${GH_APP_PRIVATE_KEY_FILE:?need GH_APP_PRIVATE_KEY_FILE}"
: "${GH_RUNNER_LABELS:=self-hosted}"
: "${GH_RUNNER_NAME:=ephemeral-$(hostname)-$RANDOM}"


create_jwt() {
  local app_id="$1"
  local key_file="$2"
  local now iat exp header payload unsigned sig

  # issued-at and expiration in seconds since epoch
  now=$(date +%s)
  iat=$((now - 60))
  exp=$((now + 540))  # 9 minutes

  header='{"alg":"RS256","typ":"JWT"}'
  payload=$(printf '{"iat":%d,"exp":%d,"iss":%d}' "$iat" "$exp" "$app_id")

  b64url() {
    openssl base64 -A | tr '+/' '-_' | tr -d '='
  }

  unsigned="$(printf '%s' "$header" | b64url).$(printf '%s' "$payload" | b64url)"

  sig=$(printf '%s' "$unsigned" \
    | openssl dgst -sha256 -sign "$key_file" \
    | b64url)

  printf '%s.%s\n' "$unsigned" "$sig"
}

get_installation_token() {
  local jwt="$1"
  local installation_id="$2"

  curl -fsSL -X POST \
    -H "Authorization: Bearer ${jwt}" \
    -H "Accept: application/vnd.github+json" \
    "https://api.github.com/app/installations/${installation_id}/access_tokens" \
  | jq -er '.token'
}

# kind is registration-token or remove-token
get_runner_admin_token() {
  local api_token="$1"
  local runner_url="$2"
  local kind="$3"

  local url_no_proto org repo token_url
  url_no_proto=${runner_url#https://github.com/}
  org=$(printf '%s\n' "$url_no_proto" | cut -d'/' -f1)
  repo=$(printf '%s\n' "$url_no_proto" | cut -d'/' -f2 || true)

  if [ -n "$repo" ] && [ "$repo" != "$org" ]; then
    token_url="https://api.github.com/repos/${org}/${repo}/actions/runners/${kind}"
  else
    token_url="https://api.github.com/orgs/${org}/actions/runners/${kind}"
  fi

  curl -fsSL -X POST \
    -H "Authorization: Bearer ${api_token}" \
    -H "Accept: application/vnd.github+json" \
    "$token_url" \
  | jq -er '.token'
}

# the startup tokens have expired by the time an idle runner is stopped
remove_runner() {
  local jwt inst_token remove_token
  jwt=$(create_jwt "$GH_APP_ID" "$GH_APP_PRIVATE_KEY_FILE") || return 1
  inst_token=$(get_installation_token "$jwt" "$GH_APP_INSTALLATION_ID") || return 1
  remove_token=$(get_runner_admin_token "$inst_token" "$GH_RUNNER_URL" remove-token) || return 1
  ./config.sh remove --token "$remove_token"
}

echo "Generating GitHub App JWT..."
jwt=$(create_jwt "$GH_APP_ID" "$GH_APP_PRIVATE_KEY_FILE")

echo "Requesting installation access token..."
inst_token=$(get_installation_token "$jwt" "$GH_APP_INSTALLATION_ID") \
  || { echo "Failed to obtain installation access token"; exit 1; }

echo "Requesting runner registration token..."
reg_token=$(get_runner_admin_token "$inst_token" "$GH_RUNNER_URL" registration-token) \
  || { echo "Failed to obtain runner registration token"; exit 1; }

echo "Configuring runner '${GH_RUNNER_NAME}'..."

./config.sh \
  --url "${GH_RUNNER_URL}" \
  --token "${reg_token}" \
  --name "${GH_RUNNER_NAME}" \
  --labels "${GH_RUNNER_LABELS}" \
  --ephemeral \
  --unattended \
  --disableupdate \
  --replace

# run.sh only relays SIGTERM/SIGINT to the listener when this is set; otherwise
# a container stop kills the shell and orphans a running job.
export RUNNER_MANUALLY_TRAP_SIG=1
./run.sh &
runner_pid=$!
trap 'kill -TERM "$runner_pid" 2>/dev/null' INT TERM

# a trapped signal interrupts wait before run.sh has exited
rc=0
while :; do
  rc=0
  wait "$runner_pid" || rc=$?
  kill -0 "$runner_pid" 2>/dev/null || break
done
trap - INT TERM

# an ephemeral runner deletes .runner once it has run a job, so its presence means
# the registration would otherwise linger offline until GitHub prunes it
if [ -f .runner ]; then
  echo "Removing idle runner '${GH_RUNNER_NAME}' from GitHub..."
  remove_runner || echo "Failed to remove runner registration"
fi

exit "$rc"
