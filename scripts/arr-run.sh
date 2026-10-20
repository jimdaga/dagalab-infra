#!/usr/bin/env bash
# Port-forward Radarr/Sonarr/Prowlarr/Bookshelf/qBittorrent, export their (and Jellyfin's "arr") API keys from the cluster secrets (never printed), run a script, clean up.
# usage: scripts/arr-run.sh scripts/arr-wire.py
set -u
key() { kubectl -n media get secret "$1" -o jsonpath="{.data.$2}" | base64 -d; }
export RADARR_KEY=$(key radarr RADARR__AUTH__APIKEY) SONARR_KEY=$(key sonarr SONARR__AUTH__APIKEY) PROWLARR_KEY=$(key prowlarr PROWLARR__AUTH__APIKEY) \
  JELLYFIN_KEY=$(key jellyfin-arr JELLYFIN_KEY) BOOKSHELF_KEY=$(key bookshelf READARR__AUTH__APIKEY)
kubectl -n media port-forward svc/radarr 17878:7878 >/dev/null 2>&1 & p1=$!
kubectl -n media port-forward svc/sonarr 18989:8989 >/dev/null 2>&1 & p2=$!
kubectl -n media port-forward svc/prowlarr 19696:9696 >/dev/null 2>&1 & p3=$!
kubectl -n media port-forward svc/qbittorrent 18080:8080 >/dev/null 2>&1 & p4=$!
kubectl -n media port-forward svc/bookshelf 18787:8787 >/dev/null 2>&1 & p5=$!
trap 'kill $p1 $p2 $p3 $p4 $p5 2>/dev/null' EXIT
for port in 17878 18989 19696 18787; do
  for _ in $(seq 1 30); do curl -s -o /dev/null "http://127.0.0.1:$port/ping" && break; sleep 0.5; done
done
for _ in $(seq 1 30); do curl -s -o /dev/null http://127.0.0.1:18080/api/v2/app/version && break; sleep 0.5; done
python3 "$@"
