#!/usr/bin/env bash
# RHEL 9 node prep for RKE2 + Longhorn. Run as root on each node before installing RKE2.
set -euo pipefail

# Longhorn: iSCSI initiator, NFS client (RWX volumes), encryption + device-mapper tooling
dnf install -y iscsi-initiator-utils nfs-utils cryptsetup device-mapper
echo "InitiatorName=$(/sbin/iscsi-iname)" > /etc/iscsi/initiatorname.iscsi
systemctl enable --now iscsid

# Longhorn: keep multipathd away from Longhorn's sd* devices
if systemctl is-enabled multipathd &>/dev/null; then
  cat > /etc/multipath.conf <<'CONF'
blacklist {
    devnode "^sd[a-z0-9]+"
}
CONF
  systemctl restart multipathd
fi

# RKE2 + Canal: firewalld conflicts with Canal's iptables rules. The UniFi gateway is the perimeter.
systemctl disable --now firewalld || true

# RKE2 + Canal: stop NetworkManager from managing CNI interfaces
cat > /etc/NetworkManager/conf.d/rke2-canal.conf <<'CONF'
[keyfile]
unmanaged-devices=interface-name:flannel*;interface-name:cali*;interface-name:tunl*;interface-name:vxlan.calico;interface-name:vxlan-v6.calico;interface-name:wireguard.cali;interface-name:wg-v6.cali
CONF
systemctl reload NetworkManager

# Present on some RHEL images; it rewrites routing and breaks pod networking
systemctl disable --now nm-cloud-setup.service nm-cloud-setup.timer 2>/dev/null || true

# Kubernetes wants swap off
swapoff -a
sed -i '/\sswap\s/s/^/#/' /etc/fstab
