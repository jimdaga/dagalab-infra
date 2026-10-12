#!/usr/bin/env bash
# First-run setup for Homarr (trial): admin user, board, integrations, app links, then the board layout (homarr-board.py).
#   VAULT_TOKEN=<root or admin token> ./scripts/homarr-setup.sh
# secret/homarr: encryption-key (pre-created), admin-user, admin-password (generated here), api-key (REST API).
set -euo pipefail
: "${VAULT_TOKEN:?}"
v() { kubectl -n vault exec -i vault-0 -- env VAULT_TOKEN="$VAULT_TOKEN" vault "$@"; }
key() { kubectl -n "$1" get secret "$2" -o jsonpath="{.data.$3}" | base64 -d; }

if ! v kv get -field=admin-password secret/homarr >/dev/null 2>&1; then
  v kv patch secret/homarr admin-user=jim admin-password="$(openssl rand -base64 18)" >/dev/null
  echo "generated secret/homarr admin credentials" >&2
fi
export HOMARR_USER=$(v kv get -field=admin-user secret/homarr) HOMARR_PASSWORD=$(v kv get -field=admin-password secret/homarr)
export SONARR_KEY=$(key media sonarr SONARR__AUTH__APIKEY) RADARR_KEY=$(key media radarr RADARR__AUTH__APIKEY) \
  PROWLARR_KEY=$(key media prowlarr PROWLARR__AUTH__APIKEY) BAZARR_KEY=$(key media bazarr BAZARR_API_KEY) \
  JELLYFIN_KEY=$(key homepage homepage HOMEPAGE_VAR_JELLYFIN_KEY) SEERR_KEY=$(key homepage homepage HOMEPAGE_VAR_SEERR_KEY) \
  GLUETUN_KEY=$(key homepage homepage HOMEPAGE_VAR_GLUETUN_KEY)
export CA_FILE=${CA_FILE:-$HOME/kube-configs/dagalab-lab-ca.crt}

out=$(python3 "$(dirname "$0")/homarr-setup.py" --emit-key)
api=$(sed -n 's/^HOMARR_API_KEY=//p' <<<"$out")
if [[ -n "$api" ]]; then
  v kv patch secret/homarr api-key="$api" >/dev/null
  echo "stored Homarr API key in secret/homarr" >&2
fi

# board layout (rows by purpose, launchers, bookmarks, icons, weather); re-runnable on its own
export HOMARR_API_KEY=$(v kv get -field=api-key secret/homarr)
python3 "$(dirname "$0")/homarr-board.py"
