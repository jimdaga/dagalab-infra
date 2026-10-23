#!/usr/bin/env bash
# Uptime Kuma monitors, groups, Discord alert and status page, from the repo (idempotent; re-run after adding a route or
# an EXTRA entry). What gets monitored: scripts/uptime-kuma-setup.py. How it's applied: scripts/uptime-kuma-apply.js.
#   ./scripts/uptime-kuma-setup.sh [--dry-run]
# Login from the `uptime-kuma` Secret (ExternalSecret <- Vault secret/uptimekuma: user, password); webhook from the
# `discord` Secret in media (Vault secret/discord). UK_POD overrides the target pod (testing).
set -euo pipefail
key() { kubectl -n "$1" get secret "$2" -o jsonpath="{.data.$3}" | base64 -d; }
export UK_USER=$(key uptime-kuma uptime-kuma user) UK_PASSWORD=$(key uptime-kuma uptime-kuma password) \
  DISCORD_ALERTS=$(key media discord webhook-alerts)

pod=${UK_POD:-$(kubectl -n uptime-kuma get pod -l app.kubernetes.io/name=uptime-kuma -o jsonpath='{.items[0].metadata.name}')}
tmp=$(mktemp -d)
# the desired state holds the password and webhook: remove both copies however the run ends
trap 'rm -rf "$tmp"; kubectl -n uptime-kuma exec "$pod" -- rm -f /tmp/uk-apply.js /tmp/uk-desired.json' EXIT
(umask 077; python3 "$(dirname "$0")/uptime-kuma-setup.py" >"$tmp/desired.json")
kubectl -n uptime-kuma cp "$(dirname "$0")/uptime-kuma-apply.js" "$pod:/tmp/uk-apply.js"
kubectl -n uptime-kuma cp "$tmp/desired.json" "$pod:/tmp/uk-desired.json"
kubectl -n uptime-kuma exec "$pod" -- node /tmp/uk-apply.js /tmp/uk-desired.json "$@"
