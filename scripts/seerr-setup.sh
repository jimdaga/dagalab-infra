#!/usr/bin/env bash
# Configure Seerr (Jellyfin sign-in/admin, libraries, Radarr/Sonarr) and store its API key in Vault (secret/seerr).
#   VAULT_TOKEN=<root or admin token> ./scripts/seerr-setup.sh
# Needs Jellyfin set up first (scripts/jellyfin-setup.sh). Idempotent.
set -euo pipefail
: "${VAULT_TOKEN:?}"
v() { kubectl -n vault exec -i vault-0 -- env VAULT_TOKEN="$VAULT_TOKEN" vault "$@"; }

export JF_USER=$(v kv get -field=admin-user secret/jellyfin)
export JF_PASSWORD=$(v kv get -field=admin-password secret/jellyfin)
export JF_KEY=$(v kv get -field=seerr-key secret/jellyfin)
export RADARR_KEY=$(v kv get -field=api-key secret/radarr)
export SONARR_KEY=$(v kv get -field=api-key secret/sonarr)

kubectl -n media port-forward svc/seerr 15055:5055 >/dev/null 2>&1 & pf=$!
trap 'kill $pf 2>/dev/null' EXIT
for _ in $(seq 1 30); do curl -s -o /dev/null http://127.0.0.1:15055/api/v1/status && break; sleep 0.5; done

out=$(python3 "$(dirname "$0")/seerr-setup.py" --emit-key)
key=$(sed -n 's/^SEERR_KEY=//p' <<<"$out")
[[ -n "$key" ]] || { echo "Seerr API key missing" >&2; exit 1; }
v kv put secret/seerr api-key="$key" >/dev/null
echo "stored Seerr API key in secret/seerr" >&2
