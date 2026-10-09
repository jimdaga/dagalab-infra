#!/usr/bin/env bash
# Ubuntu 26.04 node prep for RKE2 + Longhorn + synology-csi. Run with sudo on each node before installing RKE2.
# Idempotent; safe to re-run.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }

# Longhorn + synology-csi: iSCSI initiator, NFS client (RWX volumes), encryption + device-mapper tooling
apt-get update -qq
apt-get install -y -qq open-iscsi nfs-common cryptsetup dmsetup
systemctl enable --now iscsid
cat > /etc/modules-load.d/longhorn.conf <<'CONF'
iscsi_tcp
dm_crypt
CONF
modprobe iscsi_tcp
modprobe dm_crypt

# Longhorn: no SAN here, so multipathd only gets in the way of Longhorn's block devices
systemctl disable --now multipathd.socket multipathd.service 2>/dev/null || true

# RKE2 + Canal: ufw's rules conflict with Canal. The UniFi gateway is the perimeter.
ufw disable || true
systemctl disable --now ufw 2>/dev/null || true

# Kubernetes wants swap off (the Ubuntu installer creates /swap.img)
swapoff -a
sed -i '/\sswap\s/s/^[^#]/#&/' /etc/fstab
rm -f /swap.img

# Headroom for many pods tailing/watching files (log shippers, Grafana, kubetail)
cat > /etc/sysctl.d/90-k8s.conf <<'CONF'
fs.inotify.max_user_instances = 8192
fs.inotify.max_user_watches = 524288
CONF
sysctl --system >/dev/null

echo "node-prep done on $(hostname -f)"
