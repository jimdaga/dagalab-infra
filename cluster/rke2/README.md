# RKE2 on RHEL

Three identical RHEL 9 nodes, all servers (HA etcd, workloads scheduled on all three). SELinux stays enforcing.

| Node  | IP           |
|-------|--------------|
| lab-1 | 192.168.2.11 |
| lab-2 | 192.168.2.12 |
| lab-3 | 192.168.2.13 |

## Node prep (each node)

1. Install RHEL 9 (minimal), register it, set the hostname, and give it a UniFi DHCP reservation on VLAN 2.
2. Make sure each node has a dedicated disk or a large `/var/lib/longhorn` for Longhorn.
3. Run `sudo ./node-prep.sh`. It:
   - installs Longhorn's prerequisites (iscsid, nfs-utils, cryptsetup) and adds a multipath blacklist
   - disables firewalld, which conflicts with Canal
   - tells NetworkManager to leave the CNI interfaces alone
   - disables nm-cloud-setup and turns swap off

RHEL 10: check the RKE2 support matrix before using it. RHEL 9 is the safe choice.

## Install

On RHEL, `get.rke2.io` installs from Rancher's RPM repo, including `rke2-selinux`, so upgrades come through `dnf` as well. Version is pinned in `RKE2_VERSION` below. Bump deliberately; keep `.tool-versions` kubectl within one minor.

```bash
RKE2_VERSION=v1.37.1+rke2r1

# lab-1
sudo mkdir -p /etc/rancher/rke2 && sudo cp server-init.yaml /etc/rancher/rke2/config.yaml   # fill in token
curl -sfL https://get.rke2.io | sudo INSTALL_RKE2_VERSION=$RKE2_VERSION sh -
sudo systemctl enable --now rke2-server

# lab-2, lab-3 (after lab-1 is Ready)
sudo mkdir -p /etc/rancher/rke2 && sudo cp server-join.yaml /etc/rancher/rke2/config.yaml   # fill in token + node-ip
curl -sfL https://get.rke2.io | sudo INSTALL_RKE2_VERSION=$RKE2_VERSION sh -
sudo systemctl enable --now rke2-server
```

Kubeconfig: `/etc/rancher/rke2/rke2.yaml` on lab-1. Copy it locally and change `server:` to `https://192.168.2.11:6443`.
