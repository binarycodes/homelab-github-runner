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
  | jq -r '.token'
}

get_runner_registration_token() {
  local api_token="$1"
  local runner_url="$2"

  local url_no_proto org repo token_url
  url_no_proto=${runner_url#https://github.com/}
  org=$(printf '%s\n' "$url_no_proto" | cut -d'/' -f1)
  repo=$(printf '%s\n' "$url_no_proto" | cut -d'/' -f2 || true)

  if [ -n "$repo" ] && [ "$repo" != "$org" ]; then
    token_url="https://api.github.com/repos/${org}/${repo}/actions/runners/registration-token"
  else
    token_url="https://api.github.com/orgs/${org}/actions/runners/registration-token"
  fi

  curl -fsSL -X POST \
    -H "Authorization: Bearer ${api_token}" \
    -H "Accept: application/vnd.github+json" \
    "$token_url" \
  | jq -r '.token'
}

echo "Generating GitHub App JWT..."
jwt=$(create_jwt "$GH_APP_ID" "$GH_APP_PRIVATE_KEY_FILE")

echo "Requesting installation access token..."
inst_token=$(get_installation_token "$jwt" "$GH_APP_INSTALLATION_ID")

if [ -z "$inst_token" ] || [ "$inst_token" = "null" ]; then
  echo "Failed to obtain installation access token"
  exit 1
fi

echo "Requesting runner registration token..."
reg_token=$(get_runner_registration_token "$inst_token" "$GH_RUNNER_URL")

if [ -z "$reg_token" ] || [ "$reg_token" = "null" ]; then
  echo "Failed to obtain runner registration token"
  exit 1
fi

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
exec ./run.sh
