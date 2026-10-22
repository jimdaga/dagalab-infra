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
  case " $NODES " in
    *" $h "*) c+=$(put "$h" /etc/nut/upssched.conf 640 root:nut < hosts/nut/upssched-node.conf) ;;
    *)        c+=$(put "$h" /etc/nut/upssched.conf 640 root:nut < hosts/nut/upssched-plain.conf) ;;
  esac
  if [[ " $NODES " == *" $h "* ]]; then
    ssh -o BatchMode=yes "jim@$h" 'sudo mkdir -p /usr/local/lib/dagalab /var/lib/dagalab'
    c+=$(put "$h" /usr/local/lib/dagalab/lab-power.py 755 root:root < scripts/lab-power.py)
    c+=$(put "$h" /usr/local/lib/dagalab/ups-shutdown.sh 755 root:root < hosts/nut/ups-shutdown.sh)
    put "$h" /etc/systemd/system/dagalab-ups-shutdown.service 644 root:root < hosts/nut/dagalab-ups-shutdown.service >/dev/null
    put "$h" /tmp/dagalab-sudoers 440 root:root < hosts/nut/sudoers-dagalab-ups >/dev/null
    ssh -o BatchMode=yes "jim@$h" 'sudo visudo -cqf /tmp/dagalab-sudoers && sudo install -m 440 /tmp/dagalab-sudoers /etc/sudoers.d/dagalab-ups; sudo rm -f /tmp/dagalab-sudoers'
    if [ "$(put "$h" /etc/netplan/91-wakeonlan.yaml 600 root:root < hosts/nut/91-wakeonlan.yaml)" = changed ]; then
      ssh -o BatchMode=yes "jim@$h" 'sudo netplan generate && sudo netplan apply && sleep 5; sudo ethtool -s eno1 wol g'
      echo "  wake-on-lan enabled"
    fi
    if [ "$h" = 192.168.3.11 ]; then
      put "$h" /etc/systemd/system/dagalab-ups-autostart.service 644 root:root < hosts/nut/dagalab-ups-autostart.service >/dev/null
      ssh -o BatchMode=yes "jim@$h" 'sudo systemctl daemon-reload && sudo systemctl enable -q dagalab-ups-autostart.service'
    else
      ssh -o BatchMode=yes "jim@$h" 'sudo systemctl daemon-reload'
    fi
  fi
  # reset-failed: the package starts nut-monitor with its stock config (no UPS), which fails into systemd's start limit
  ssh -o BatchMode=yes "jim@$h" "sudo systemctl enable -q nut-monitor; sudo systemctl reset-failed nut-monitor; $( [ -n "$c" ] && echo 'sudo systemctl restart nut-monitor' || echo 'sudo systemctl start nut-monitor' ); sleep 2; echo \"  nut-monitor \$(systemctl is-active nut-monitor), ups \$(timeout 8 upsc ups@192.168.3.20 ups.status 2>/dev/null || echo unreachable)\""
done
if [ -z "${*:-}" ] || [[ " $* " == *" 192.168.3.20 "* ]]; then
  ssh -o BatchMode=yes jim@192.168.3.20 'mkdir -p dagalab && cat > dagalab/ups-wake.py && chmod 755 dagalab/ups-wake.py' < nas/ups-wake.py \
    && echo "== NAS: dagalab/ups-wake.py updated"
fi
