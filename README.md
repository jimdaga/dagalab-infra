# dagalab-infra

GitOps config for the dagalab homelab: a 3-node RKE2 cluster on Ubuntu 26.04 managed by Argo CD (app-of-apps, mostly upstream Helm charts). Apps live on `jimdaga.dev`; machines on `lab.internal`.

## Layout

```
dagalab-infra/
├── bastion/                       # Admin Pi setup (CLI tooling)
├── cluster/rke2/                  # RKE2 node configs + install notes
├── terraform/aws-vault-unseal/    # AWS KMS key + IAM user for Vault auto-unseal
├── argocd/
│   ├── bootstrap/root-app.yaml    # App-of-apps, applied once by hand
│   └── apps/{app}/
│       ├── application.yaml       # Argo CD Application (chart + version + sync wave)
│       └── values.yaml            # Helm values (upstream-chart apps only)
├── helm/charts/                   # Local charts for glue config (issuers, gateway, IP pools, secret stores)
├── scripts/vault-bootstrap.sh     # One-time Vault config after init
├── Makefile
└── renovate.json                  # Bumps chart versions in application.yaml
```

Upstream-chart apps use a multi-source Application: source 1 is the chart, source 2 is this repo (`ref: values`), so values come from `$values/argocd/apps/{app}/values.yaml`. Local-chart apps point straight at `helm/charts/{app}`.

## Network

Lab VLAN 3 on the UniFi Cloud Gateway Ultra: `192.168.3.0/24`.

| Range | Use |
|-------|-----|
| 192.168.3.1 | Gateway |
| 192.168.3.10 | Reserved: Kubernetes API VIP (future kube-vip), `k8s.lab.internal` |
| 192.168.3.11–13 | dagakube01–03.lab.internal (static, `scripts/host-config.sh`) |
| 192.168.3.100 | bastion.lab.internal: Raspberry Pi, Ubuntu (static, `scripts/host-config.sh`; tooling in `bastion/`) |
| 192.168.3.101–199 | UniFi DHCP pool. UniFi defaults a new network to .6–.254, so shrink it to this range: fixed-IP reservations protect the four hosts, but the API VIP (.10) and the MetalLB pool have no MAC to reserve against. |
| 192.168.3.200–229 | MetalLB pool. **Must be outside the DHCP pool.** |
| 192.168.3.207 | daga-nas (Synology, static). Inside the MetalLB range, so it's excluded from the pool; TODO: move it below .100 |
| 192.168.3.200 | Shared gateway (`*.jimdaga.dev`) |

external-dns publishes `<app>.jimdaga.dev → 192.168.3.200` to Cloudflare (DNS-only), so names resolve anywhere but only work on the LAN/VPN.

### Switching

Lab devices hang off an unmanaged switch on the Cloud Gateway Ultra's **Port 3**: Native VLAN = **Lab (3)**, Tagged VLAN Management = **Block All**. Everything behind it lands on VLAN 3 untagged, so devices need no VLAN config.

If a device on Port 3 can't reach 192.168.3.1, run `sudo tcpdump -eni eth0 -c 10 arp` on it. Frames showing `802.1Q … vlan 3` mean the port is sending tagged traffic despite the UI. Flip the native VLAN to Default, apply, then set it back to Lab (3) to re-provision the port.

### DNS

Two zones, two owners:

| Names | Answered by | How records get there |
|-------|-------------|-----------------------|
| `<host>.lab.internal` (machines) | UniFi gateway (192.168.3.1) | Static hosts (nodes, bastion) need a manual UniFi DNS record; DHCP clients register automatically |
| `<app>.jimdaga.dev` (cluster apps) | Cloudflare | external-dns |

- VLAN 3 network settings in UniFi: set **Domain Name** to `lab.internal`, so DHCP clients get it as their search domain and it's appended to their registered names.
- Local DNS records in UniFi: `k8s`, `dagakube01`–`03`, and `bastion` under `lab.internal` (see the Network table).
- `.internal` is reserved by ICANN for private networks, so these names never leak to or collide with public DNS.
- **Prerequisite:** `jimdaga.dev` is currently on IONOS nameservers (`ui-dns.*`). Move the domain's nameservers to Cloudflare before external-dns or cert-manager DNS-01 will work.


## Stack

| Wave | App | What |
|-----:|-----|------|
| -30 | argocd | Argo CD, self-managed |
| -20 | metallb | LoadBalancer IPs on bare metal |
| -20 | longhorn | Replicated block storage (default StorageClass) |
| -20 | cert-manager | Certificates |
| -20 | envoy-gateway | Gateway API implementation (ingress-nginx replacement) + Gateway API CRDs |
| -15 | metallb-config | IP pool / L2 advertisement |
| -10 | vault | HA Vault, raft on Longhorn, AWS KMS auto-unseal |
| -10 | external-secrets | Syncs Vault secrets into k8s Secrets |
| -5 | secret-stores | Vault ClusterSecretStore + ExternalSecrets (Cloudflare token, Grafana admin) |
| 0 | cluster-issuers | Let's Encrypt staging + prod via Cloudflare DNS-01 |
| 0 | external-dns | DNS records from HTTPRoutes/Services → Cloudflare |
| 0 | kube-prometheus-stack | Prometheus, Alertmanager, Grafana |
| 0 | synology-csi | Synology DSM storage classes: iSCSI (RWO) and NFS (RWX), Delete/Retain |
| 5 | shared-gateway | GatewayClass, shared Gateway, wildcard cert, HTTPRoutes for UIs |

metrics-server is bundled by RKE2, so it isn't listed here. RKE2's bundled ingress-nginx is disabled.

Sync waves between child apps work because `argocd/apps/argocd/values.yaml` restores the Application health check. Vault stays unhealthy until it's initialized, which **intentionally holds every later wave** until you run the Vault steps below.

### Why Envoy Gateway

ingress-nginx was retired in March 2026, and Gateway API is where things are headed. Envoy Gateway is a CNCF, Gateway-API-native implementation that doesn't depend on which CNI you run.

## Storage

| StorageClass | Backend | Access | Use for |
|---|---|---|---|
| `longhorn` (default) | Node disks, 3 replicas | RWO | Most apps, Vault, Prometheus |
| `synology-csi-iscsi-delete` / `-retain` | Synology iSCSI LUN | RWO | Big volumes you want on the NAS |
| `synology-csi-nfs-delete` / `-retain` | Synology NFS share | RWX | Shared/media volumes |

Synology prep (DSM):
- Create a dedicated CSI user in the `administrators` group, with 2FA off for that user.
- Enable iSCSI, and NFS v4.1 for the NFS classes.
- Give DSM a certificate valid for the hostname you store in Vault (`SYNOLOGY_HOST`).
- Ideally put the NAS on VLAN 3 (or give it a VLAN 3 interface), so storage traffic doesn't get routed through the gateway.

## Bootstrap

1. **AWS:** `cd terraform/aws-vault-unseal && terraform init && terraform apply`, then create an access key out of band so it stays out of state:
   `aws iam create-access-key --user-name dagalab-vault-unseal`
2. **Cluster:** follow `cluster/rke2/README.md`.
3. **Vault's AWS creds:** the only hand-made secret:
   ```bash
   kubectl create ns vault
   kubectl -n vault create secret generic vault-aws-kms \
     --from-literal=AWS_ACCESS_KEY_ID=... --from-literal=AWS_SECRET_ACCESS_KEY=...
   ```
4. **Argo CD:** `make bootstrap`. Waves -30 through -10 roll out, then Vault waits to be initialized.
5. **Vault:**
   ```bash
   kubectl -n vault exec -ti vault-0 -- vault operator init   # recovery keys + root token → password manager, NOT git
   VAULT_TOKEN=... CF_API_TOKEN=... GRAFANA_ADMIN_PASSWORD=... \
   SYNOLOGY_HOST=... SYNOLOGY_USERNAME=... SYNOLOGY_PASSWORD=... ./scripts/vault-bootstrap.sh
   # Every secret is optional and re-runnable; Grafana's password is generated if unset.
   ```
   vault-1/2 join raft and auto-unseal on their own. The remaining waves then sync.
6. Once the staging cert issues, flip `certificate.issuer` in `helm/charts/shared-gateway/values.yaml` to `letsencrypt-prod`.

## TODO

- [ ] kube-vip for an HA API endpoint (192.168.3.10)
- [ ] Backups: Longhorn → S3/NAS, Vault raft snapshots, RKE2 etcd snapshots off-node
- [ ] Remote Terraform state
