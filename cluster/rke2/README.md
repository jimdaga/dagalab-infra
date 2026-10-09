# RKE2 on Ubuntu

Three identical bare-metal nodes (8 cores, 30 GB RAM, 512 GB SSD, Ubuntu 26.04 LTS), all servers: HA etcd, with workloads scheduled on all three. AppArmor stays enabled.

| Node | IP | NIC |
|------|----|-----|
| dagakube01.lab.net | 192.168.3.11 | eno1 |
| dagakube02.lab.net | 192.168.3.12 | eno1 |
| dagakube03.lab.net | 192.168.3.13 | eno1 |

## Node prep (each node)

1. Install Ubuntu Server. Check that the root LV uses the whole disk; the installer defaults to 100 GB. If it doesn't:
   `sudo lvextend -r -l +100%FREE /dev/ubuntu-vg/ubuntu-lv`
2. Set the hostname, domain, and static IP: `sudo ./scripts/host-config.sh dagakube0N 192.168.3.1N` (from the repo root).
3. Run `sudo ./cluster/rke2/node-prep.sh`. It:
   - installs Longhorn/synology-csi prerequisites (open-iscsi, nfs-common, cryptsetup), enables iscsid, and loads `iscsi_tcp` / `dm_crypt`
   - disables multipathd (no SAN; it interferes with Longhorn devices)
   - disables ufw, which conflicts with Canal
   - turns swap off and removes `/swap.img`
   - raises inotify limits

## Install

On Ubuntu, `get.rke2.io` installs the tarball to `/usr/local` and sets up the `rke2-server` systemd unit. The version is pinned in `RKE2_VERSION` below. Bump it deliberately, and keep `.tool-versions` kubectl within one minor version.

```bash
RKE2_VERSION=v1.37.1+rke2r1

# dagakube01
sudo mkdir -p /etc/rancher/rke2 && sudo cp server-init.yaml /etc/rancher/rke2/config.yaml   # fill in token
curl -sfL https://get.rke2.io | sudo INSTALL_RKE2_VERSION=$RKE2_VERSION sh -
sudo systemctl enable --now rke2-server

# dagakube02, dagakube03 (after dagakube01 is Ready)
sudo mkdir -p /etc/rancher/rke2 && sudo cp server-join.yaml /etc/rancher/rke2/config.yaml   # fill in token + node-ip
curl -sfL https://get.rke2.io | sudo INSTALL_RKE2_VERSION=$RKE2_VERSION sh -
sudo systemctl enable --now rke2-server
```

The kubeconfig is `/etc/rancher/rke2/rke2.yaml` on dagakube01. Copy it to the bastion and change `server:` to `https://192.168.3.11:6443`.
