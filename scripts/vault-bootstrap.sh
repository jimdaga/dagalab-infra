#!/usr/bin/env bash
# One-time Vault configuration after `vault operator init`:
#   - kv-v2 at secret/
#   - kubernetes auth + read-only role for external-secrets
#   - seed the secrets platform apps need
#
# Usage: VAULT_TOKEN=<root token> CF_API_TOKEN=<token> GRAFANA_ADMIN_PASSWORD=<pw> \
#        SYNOLOGY_HOST=<dsm hostname> SYNOLOGY_USERNAME=<csi user> SYNOLOGY_PASSWORD=<pw> \
#        ./scripts/vault-bootstrap.sh
set -euo pipefail

: "${VAULT_TOKEN:?root token from vault operator init}"
: "${CF_API_TOKEN:?Cloudflare API token (Zone:DNS:Edit on jimdaga.dev)}"
: "${GRAFANA_ADMIN_PASSWORD:?Grafana admin password}"
: "${SYNOLOGY_HOST:?DSM hostname matching its TLS cert, e.g. nas.jimdaga.dev}"
: "${SYNOLOGY_USERNAME:?DSM user for synology-csi (administrators group, no 2FA)}"
: "${SYNOLOGY_PASSWORD:?DSM password for that user}"

v() { kubectl -n vault exec -i vault-0 -- env VAULT_TOKEN="$VAULT_TOKEN" vault "$@"; }

v secrets enable -path=secret kv-v2 || true
v auth enable kubernetes || true
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

v kv put secret/cloudflare api-token="$CF_API_TOKEN"
v kv put secret/grafana admin-user=admin admin-password="$GRAFANA_ADMIN_PASSWORD"
v kv put secret/synology host="$SYNOLOGY_HOST" username="$SYNOLOGY_USERNAME" password="$SYNOLOGY_PASSWORD"
