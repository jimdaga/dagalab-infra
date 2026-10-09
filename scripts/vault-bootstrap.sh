#!/usr/bin/env bash
# Vault configuration after `vault operator init`. Idempotent; re-run as secrets become available.
#   - kv-v2 at secret/
#   - kubernetes auth + read-only role for external-secrets
#   - seed the secrets platform apps need; each one is optional and only written when provided
#
# Usage: VAULT_TOKEN=<root token> \
#        [CF_API_TOKEN=<token>] \
#        [GRAFANA_ADMIN_PASSWORD=<pw>]   (generated if unset and secret/grafana doesn't exist yet) \
#        [SYNOLOGY_HOST=<dsm hostname> SYNOLOGY_USERNAME=<csi user> SYNOLOGY_PASSWORD=<pw>] \
#        ./scripts/vault-bootstrap.sh
set -euo pipefail

: "${VAULT_TOKEN:?root token from vault operator init}"

v() { kubectl -n vault exec -i vault-0 -- env VAULT_TOKEN="$VAULT_TOKEN" vault "$@"; }
exists() { v kv metadata get "secret/$1" >/dev/null 2>&1; }

v secrets list -format=json | grep -q '"secret/"' || v secrets enable -path=secret kv-v2
v auth list -format=json | grep -q '"kubernetes/"' || v auth enable kubernetes
v write auth/kubernetes/config kubernetes_host=https://kubernetes.default.svc

v policy write external-secrets - <<'POLICY'
path "secret/data/*"     { capabilities = ["read"] }
path "secret/metadata/*" { capabilities = ["read", "list"] }
POLICY

v write auth/kubernetes/role/external-secrets \
  bound_service_account_names=external-secrets \
  bound_service_account_namespaces=external-secrets \
  policies=external-secrets \
  ttl=1h

if [[ -n "${CF_API_TOKEN:-}" ]]; then
  v kv put secret/cloudflare api-token="$CF_API_TOKEN"
else
  echo "skip secret/cloudflare: set CF_API_TOKEN (Zone:DNS:Edit on jimdaga.dev)" >&2
fi

if [[ -n "${GRAFANA_ADMIN_PASSWORD:-}" ]]; then
  v kv put secret/grafana admin-user=admin admin-password="$GRAFANA_ADMIN_PASSWORD"
elif ! exists grafana; then
  v kv put secret/grafana admin-user=admin admin-password="$(openssl rand -base64 24)"
  echo "generated secret/grafana; read it with: vault kv get secret/grafana" >&2
fi

if [[ -n "${SYNOLOGY_HOST:-}" && -n "${SYNOLOGY_USERNAME:-}" && -n "${SYNOLOGY_PASSWORD:-}" ]]; then
  v kv put secret/synology host="$SYNOLOGY_HOST" username="$SYNOLOGY_USERNAME" password="$SYNOLOGY_PASSWORD"
else
  echo "skip secret/synology: set SYNOLOGY_HOST, SYNOLOGY_USERNAME, SYNOLOGY_PASSWORD" >&2
fi
