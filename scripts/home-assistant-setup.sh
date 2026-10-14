#!/usr/bin/env bash
# Home Assistant first-run setup: admin user, long-lived API token, Jellyfin user `homeassistant`, lab integrations
# (UniFi, Synology DSM, Jellyfin, Sonarr, Radarr). Idempotent.
#   VAULT_TOKEN=<root or admin token> ./scripts/home-assistant-setup.sh
# secret/home-assistant: admin-user, admin-password, jellyfin-password (generated here), api-token (created by the .py).
set -euo pipefail
: "${VAULT_TOKEN:?}"
v() { kubectl -n vault exec -i vault-0 -- env VAULT_TOKEN="$VAULT_TOKEN" vault "$@"; }
key() { kubectl -n "$1" get secret "$2" -o jsonpath="{.data.$3}" | base64 -d; }

# passwords go to Vault before anything uses them, so a failed run never loses one
if ! v kv get -field=admin-password secret/home-assistant >/dev/null 2>&1; then
  v kv put secret/home-assistant admin-user=jim admin-password="$(openssl rand -base64 18)" \
    jellyfin-password="$(openssl rand -base64 18)" >/dev/null
  echo "generated secret/home-assistant credentials" >&2
fi
export HA_USER=$(v kv get -field=admin-user secret/home-assistant) HA_PASSWORD=$(v kv get -field=admin-password secret/home-assistant) \
  JF_HA_PASSWORD=$(v kv get -field=jellyfin-password secret/home-assistant)
export HA_TOKEN=$(v kv get -field=api-token secret/home-assistant 2>/dev/null || true)
export JF_ADMIN_KEY=$(key media jellyfin-arr JELLYFIN_KEY) SONARR_KEY=${SONARR_KEY:-$(key media sonarr SONARR__AUTH__APIKEY)} \
  RADARR_KEY=$(key media radarr RADARR__AUTH__APIKEY)
export UNIFI_PASSWORD=$(v kv get -field=unifi-password secret/homepage-devices) \
  SYNOLOGY_PASSWORD=$(v kv get -field=synology-password secret/homepage-devices)   # the `homepage` device accounts

kubectl -n home-assistant port-forward svc/home-assistant 18123:8123 >/dev/null 2>&1 & pf1=$!
kubectl -n media port-forward svc/jellyfin 18096:8096 >/dev/null 2>&1 & pf2=$!
trap 'kill $pf1 $pf2 2>/dev/null' EXIT
for _ in $(seq 1 30); do
  curl -s -o /dev/null http://127.0.0.1:18123/api/onboarding && curl -s -o /dev/null http://127.0.0.1:18096/health && break
  sleep 0.5
done

# store a new token even when the .py failed later on (an integration), or it would be orphaned
rc=0
out=$(python3 "$(dirname "$0")/home-assistant-setup.py") || rc=$?
tok=$(sed -n 's/^HA_TOKEN=//p' <<<"$out")
if [[ -n "$tok" ]]; then
  v kv patch secret/home-assistant api-token="$tok" >/dev/null
  echo "stored Home Assistant API token in secret/home-assistant" >&2
fi
exit $rc
