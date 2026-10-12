#!/usr/bin/env bash
# Configure Bazarr (Sonarr/Radarr, English profile, providers) and keep its API key in Vault (secret/bazarr api-key).
#   VAULT_TOKEN=<root or admin token> ./scripts/bazarr-setup.sh
# Run after Sonarr/Radarr are wired (arr-run.sh arr-wire.py). Idempotent. On first run Bazarr's self-generated key is
# replaced by a new random one (it never leaves this script except into Vault).
set -euo pipefail
: "${VAULT_TOKEN:?}"
v() { kubectl -n vault exec -i vault-0 -- env VAULT_TOKEN="$VAULT_TOKEN" vault "$@"; }
key() { kubectl -n media get secret "$1" -o jsonpath="{.data.$2}" | base64 -d; }

export SONARR_KEY=$(key sonarr SONARR__AUTH__APIKEY) RADARR_KEY=$(key radarr RADARR__AUTH__APIKEY)
# the key Bazarr accepts right now (auth.apikey in its config.yaml)
export BAZARR_CUR_KEY=$(kubectl -n media exec deploy/bazarr -c app -- sed -n '/^auth:/,/^[a-z]/s/^  apikey: *//p' /config/config/config.yaml | tr -d "'\" ")
export BAZARR_KEY=$(v kv get -field=api-key secret/bazarr 2>/dev/null || openssl rand -hex 16)
v kv put secret/bazarr api-key="$BAZARR_KEY" >/dev/null

kubectl -n media port-forward svc/bazarr 16767:6767 >/dev/null 2>&1 & pf=$!
trap 'kill $pf 2>/dev/null' EXIT
for _ in $(seq 1 30); do curl -s -o /dev/null http://127.0.0.1:16767/ && break; sleep 0.5; done

python3 "$(dirname "$0")/bazarr-setup.py"
echo "Bazarr API key is in Vault secret/bazarr (api-key)" >&2
