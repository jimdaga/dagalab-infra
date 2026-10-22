#!/usr/bin/env bash
# NUT clients for the UPS on daga-nas (192.168.3.20:3493, UPS "ups"). Idempotent; re-run after editing hosts/nut/.
#   VAULT_TOKEN=<token> scripts/nut-setup.sh [host ...]        default: all five hosts
# Nodes (192.168.3.11-13): graceful cluster stop after 10 min on battery, auto-start, Wake-on-LAN (hosts/nut/).
# Webserver 192.168.4.10 + bastion 192.168.3.100: plain shutdown at low battery. Also copies nas/ups-wake.py to the NAS.
# Login from Vault secret/nut; README "UPS / power".
set -euo pipefail
: "${VAULT_TOKEN:?}"
cd "$(dirname "$0")/.."
v() { kubectl -n vault exec -i vault-0 -- env VAULT_TOKEN="$VAULT_TOKEN" vault "$@"; }
NUT_USER=$(v kv get -field=username secret/nut); NUT_PW=$(v kv get -field=password secret/nut)
NODES="192.168.3.11 192.168.3.12 192.168.3.13"
HOSTS=$(for x in ${*:-$NODES 192.168.4.10 192.168.3.100}; do [ "$x" = 192.168.3.20 ] || printf '%s ' "$x"; done)   # the NAS isn't a NUT client

put() {   # put <host> <dest> <mode> <owner> < content ; prints "changed" if the file changed
  ssh -o BatchMode=yes "jim@$1" "t=\$(mktemp); cat > \$t; if sudo cmp -s \$t $2; then rm \$t; else sudo install -m $3 -o ${4%:*} -g ${4#*:} \$t $2 && rm \$t && echo changed; fi"
}

for h in $HOSTS; do
  echo "== $h"
  ssh -o BatchMode=yes "jim@$h" 'dpkg -l nut-client 2>/dev/null | grep -q ^ii || { sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nut-client >/dev/null && echo "  installed nut-client"; }'
  c=""
  c+=$(put "$h" /etc/nut/nut.conf 640 root:nut < hosts/nut/nut.conf)
  c+=$(sed -e "s/__USER__/$NUT_USER/" -e "s/__PASSWORD__/$NUT_PW/" hosts/nut/upsmon.conf.tmpl | put "$h" /etc/nut/upsmon.conf 640 root:nut)
  c+=$(put "$h" /usr/local/sbin/dagalab-upssched 755 root:root < hosts/nut/dagalab-upssched)
  c+=$(put "$h" /etc/nut/upssched.conf 640 root:nut < hosts/nut/upssched-plain.conf)   # Task 3: nodes get upssched-node.conf
  # reset-failed: the package starts nut-monitor with its stock config (no UPS), which fails into systemd's start limit
  ssh -o BatchMode=yes "jim@$h" "sudo systemctl enable -q nut-monitor; sudo systemctl reset-failed nut-monitor; $( [ -n "$c" ] && echo 'sudo systemctl restart nut-monitor' || echo 'sudo systemctl start nut-monitor' ); sleep 2; echo \"  nut-monitor \$(systemctl is-active nut-monitor), ups \$(timeout 8 upsc ups@192.168.3.20 ups.status 2>/dev/null || echo unreachable)\""
done
