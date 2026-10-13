#!/usr/bin/env bash
# Discord notifications for Sonarr, Radarr, Seerr and Bazarr (see scripts/notify-setup.py for which event goes where).
#   ./scripts/notify-setup.sh [--test]     --test also posts a test message to each channel
# Webhook URLs come from the `discord` Secret in media (ExternalSecret <- Vault secret/discord); keys from the app Secrets.
set -euo pipefail
key() { kubectl -n "$1" get secret "$2" -o jsonpath="{.data.$3}" | base64 -d; }
export DISCORD_ALERTS=$(key media discord webhook-alerts) DISCORD_MEDIA=$(key media discord webhook-media) \
  DISCORD_NOISE=$(key media discord webhook-media-noise) \
  SONARR_KEY=$(key media sonarr SONARR__AUTH__APIKEY) RADARR_KEY=$(key media radarr RADARR__AUTH__APIKEY) \
  BAZARR_KEY=$(key media bazarr BAZARR_API_KEY) SEERR_KEY=$(key homepage homepage HOMEPAGE_VAR_SEERR_KEY)

pids=()
for fwd in sonarr:18989:8989 radarr:17878:7878 seerr:15055:5055 bazarr:16767:6767; do
  IFS=: read -r svc lp rp <<<"$fwd"
  kubectl -n media port-forward "svc/$svc" "$lp:$rp" >/dev/null 2>&1 & pids+=($!)
done
trap 'kill "${pids[@]}" 2>/dev/null' EXIT
for p in 18989 17878 15055 16767; do
  for _ in $(seq 1 30); do curl -s -o /dev/null "http://127.0.0.1:$p/" && break; sleep 0.5; done
done
python3 "$(dirname "$0")/notify-setup.py" "$@"
