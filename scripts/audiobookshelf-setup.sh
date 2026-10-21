#!/usr/bin/env bash
# Configure Audiobookshelf: root user `jim` (password generated once into Vault secret/audiobookshelf root-password,
# BEFORE /init, so it's never lost), libraries Audiobooks (/audiobooks/library + /audiobooks/audible, hourly scan: no
# inotify on NFS) and Podcasts (/podcasts), a read-only `homepage` user + API key (secret/audiobookshelf homepage-key).
#   VAULT_TOKEN=<root or admin token> ./scripts/audiobookshelf-setup.sh
# Idempotent. Run it before the public route (shared-gateway) exists: an uninitialised server lets anyone create root.
set -euo pipefail
: "${VAULT_TOKEN:?}"
v() { kubectl -n vault exec -i vault-0 -- env VAULT_TOKEN="$VAULT_TOKEN" vault "$@"; }
field() { v kv get -field="$1" secret/audiobookshelf 2>/dev/null || true; }

pw=$(field root-password)
if [[ -z "$pw" ]]; then
  pw=$(openssl rand -base64 30 | tr -d '/+=' | cut -c1-32)
  v kv put secret/audiobookshelf root-password="$pw" >/dev/null
  echo "generated secret/audiobookshelf root-password" >&2
fi
export ABS_ROOT_PASSWORD="$pw" ABS_HOMEPAGE_KEY="$(field homepage-key)"   # checked against ABS; replaced if stale

kubectl -n media port-forward svc/audiobookshelf 13378:80 >/dev/null 2>&1 & pf=$!
trap 'kill $pf 2>/dev/null' EXIT
for _ in $(seq 1 30); do curl -s -o /dev/null http://127.0.0.1:13378/healthcheck && break; sleep 0.5; done

out=$(python3 "$(dirname "$0")/audiobookshelf-setup.py")
key=$(sed -n 's/^HOMEPAGE_KEY=//p' <<<"$out")
if [[ -n "$key" ]]; then
  v kv patch secret/audiobookshelf homepage-key="$key" >/dev/null
  echo "stored Audiobookshelf homepage API key in secret/audiobookshelf" >&2
fi
