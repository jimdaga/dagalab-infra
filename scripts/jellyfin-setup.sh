#!/usr/bin/env bash
# Configure Jellyfin (first-run, Quick Sync, libraries, API keys) and keep its credentials in Vault.
#   VAULT_TOKEN=<root or admin token> ./scripts/jellyfin-setup.sh [--rotate=homepage|seerr]
# secret/jellyfin: admin-user, admin-password (generated once), homepage-key, seerr-key.
set -euo pipefail
: "${VAULT_TOKEN:?}"
v() { kubectl -n vault exec -i vault-0 -- env VAULT_TOKEN="$VAULT_TOKEN" vault "$@"; }

if ! v kv get -field=admin-password secret/jellyfin >/dev/null 2>&1; then
  v kv put secret/jellyfin admin-user=jim admin-password="$(openssl rand -base64 18)" >/dev/null
  echo "generated secret/jellyfin admin credentials" >&2
fi
export JF_USER=$(v kv get -field=admin-user secret/jellyfin)
export JF_PASSWORD=$(v kv get -field=admin-password secret/jellyfin)

kubectl -n media port-forward svc/jellyfin 18096:8096 >/dev/null 2>&1 & pf=$!
trap 'kill $pf 2>/dev/null' EXIT
for _ in $(seq 1 30); do curl -s -o /dev/null http://127.0.0.1:18096/health && break; sleep 0.5; done

keys=$(python3 "$(dirname "$0")/jellyfin-setup.py" --emit-keys "$@")   # e.g. --rotate=homepage
hk=$(sed -n 's/^KEY_homepage=//p' <<<"$keys"); sk=$(sed -n 's/^KEY_seerr=//p' <<<"$keys")
[[ -n "$hk" && -n "$sk" ]] || { echo "API keys missing" >&2; exit 1; }
v kv patch secret/jellyfin homepage-key="$hk" seerr-key="$sk" >/dev/null
echo "stored Jellyfin API keys in secret/jellyfin" >&2
