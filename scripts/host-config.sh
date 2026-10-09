#!/usr/bin/env bash
# Host identity + static networking for lab machines (Ubuntu, netplan + systemd-networkd).
#
#   sudo ./scripts/host-config.sh <hostname> <ip>        e.g. dagakube01 192.168.3.11
#
# Safety net: before applying, a rollback timer is armed that restores the previous netplan
# config after ROLLBACK_SECS unless you confirm from the NEW address with:
#   sudo systemctl stop netplan-rollback.timer
# The apply itself is detached (a few seconds later) so an SSH session on the old IP can exit cleanly.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }
[[ $# -eq 2 ]] || { echo "usage: $0 <hostname> <ip>" >&2; exit 1; }

HOST=$1
IP=$2
DOMAIN=${DOMAIN:-lab.internal}
GATEWAY=${GATEWAY:-192.168.3.1}
PREFIX=${PREFIX:-24}
ROLLBACK_SECS=${ROLLBACK_SECS:-180}
IFACE=${IFACE:-$(ip route show default | awk '{print $5; exit}')}

# Keep cloud-init from rewriting hostname, /etc/hosts, or networking on boot
cat > /etc/cloud/cloud.cfg.d/99-host-config.cfg <<CFG
preserve_hostname: true
manage_etc_hosts: false
network: {config: disabled}
CFG
rm -f /etc/cloud/cloud.cfg.d/99-bastion.cfg   # superseded

hostnamectl set-hostname "$HOST"

cat > /etc/hosts <<HOSTS
127.0.0.1 localhost
${IP} ${HOST}.${DOMAIN} ${HOST}

::1 localhost ip6-localhost ip6-loopback
ff02::1 ip6-allnodes
ff02::2 ip6-allrouters
HOSTS

# Back up current netplan, then override the interface (installer/cloud-init files stay in place)
BACKUP=/root/netplan-backup-$(date +%Y%m%d%H%M%S)
cp -a /etc/netplan "$BACKUP"
rm -f /etc/netplan/90-bastion-static.yaml   # superseded
cat > /etc/netplan/90-host-config.yaml <<NETPLAN
network:
  version: 2
  ethernets:
    ${IFACE}:
      dhcp4: false
      addresses: [${IP}/${PREFIX}]
      routes:
        - to: default
          via: ${GATEWAY}
      nameservers:
        addresses: [${GATEWAY}]
        search: [${DOMAIN}]
NETPLAN
chmod 600 /etc/netplan/*.yaml
netplan generate

systemctl stop netplan-rollback.timer 2>/dev/null || true
systemd-run --quiet --unit=netplan-rollback --on-active="${ROLLBACK_SECS}" \
  sh -c "rm -rf /etc/netplan && cp -a '$BACKUP' /etc/netplan && netplan apply"
systemd-run --quiet --unit=netplan-apply-$$ --on-active=3 netplan apply

echo "${HOST}.${DOMAIN}: applying ${IP}/${PREFIX} on ${IFACE} in 3s."
echo "Confirm within ${ROLLBACK_SECS}s from the new address: sudo systemctl stop netplan-rollback.timer"
