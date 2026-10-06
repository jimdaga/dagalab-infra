#!/usr/bin/env bash
# One-time host identity + static networking for the bastion. Run with sudo.
#   sudo ./bastion/host-config.sh
# Uses `netplan try`: if SSH drops, the old config comes back automatically after 120s.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }

HOST=bastion
DOMAIN=lab.jimdaga.dev
IP=192.168.3.100
GATEWAY=192.168.3.1
IFACE=eth0

# Stop cloud-init from rewriting hostname, /etc/hosts, and netplan on boot
cat > /etc/cloud/cloud.cfg.d/99-bastion.cfg <<CFG
preserve_hostname: true
manage_etc_hosts: false
network: {config: disabled}
CFG

hostnamectl set-hostname "$HOST"

cat > /etc/hosts <<HOSTS
127.0.0.1 localhost
${IP} ${HOST}.${DOMAIN} ${HOST}

::1 localhost ip6-localhost ip6-loopback
ff02::1 ip6-allnodes
ff02::2 ip6-allrouters
HOSTS

# Overrides eth0 from 50-cloud-init.yaml (left in place so any wlan0 config survives)
cat > /etc/netplan/90-bastion-static.yaml <<NETPLAN
network:
  version: 2
  ethernets:
    ${IFACE}:
      dhcp4: false
      addresses: [${IP}/24]
      routes:
        - to: default
          via: ${GATEWAY}
      nameservers:
        addresses: [${GATEWAY}]
        search: [${DOMAIN}]
NETPLAN
chmod 600 /etc/netplan/90-bastion-static.yaml

netplan generate
netplan try --timeout 120

echo
hostname -f
resolvectl status "$IFACE" | grep -E 'DNS Server|DNS Domain'
