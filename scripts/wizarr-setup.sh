#!/usr/bin/env bash
# Configure Wizarr: admin `jim` (password generated once into Vault secret/wizarr admin-password, BEFORE /setup, so it's
# never lost), media servers Jellyfin (secret/jellyfin wizarr-key) and Audiobookshelf (secret/audiobookshelf wizarr-key),
# all libraries enabled. Run jellyfin-setup.sh and audiobookshelf-setup.sh first (they issue those keys).
#   VAULT_TOKEN=<root or admin token> ./scripts/wizarr-setup.sh
# Idempotent. Run it before the public route (shared-gateway) exists: an uninitialised server lets anyone create the admin.
set -euo pipefail
: "${VAULT_TOKEN:?}"
v() { kubectl -n vault exec -i vault-0 -- env VAULT_TOKEN="$VAULT_TOKEN" vault "$@"; }

pw=$(v kv get -field=admin-password secret/wizarr 2>/dev/null || true)
if [[ -z "$pw" ]]; then
  pw="$(openssl rand -base64 30 | tr -d '/+=' | cut -c1-28)Aa1"   # Wizarr wants upper + lower + digit
  v kv put secret/wizarr admin-user=jim admin-password="$pw" >/dev/null
  echo "generated secret/wizarr admin credentials" >&2
fi
export WIZARR_PASSWORD="$pw"
export JELLYFIN_KEY=$(v kv get -field=wizarr-key secret/jellyfin)
export ABS_KEY=$(v kv get -field=wizarr-key secret/audiobookshelf)

kubectl -n media port-forward svc/wizarr 15690:5690 >/dev/null 2>&1 & pf=$!
trap 'kill $pf 2>/dev/null' EXIT
for _ in $(seq 1 30); do curl -s -o /dev/null http://127.0.0.1:15690/health && break; sleep 0.5; done

python3 "$(dirname "$0")/wizarr-setup.py"
