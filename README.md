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
| 192.168.3.20 | daga-nas.lab.internal: Synology DS420+ port 1 (DSM, SMB) |
| 192.168.3.21 | daga-nas port 2: **NFS for Kubernetes** (`helm/charts/media-storage`) |
| 192.168.3.100 | bastion.lab.internal: Raspberry Pi, Ubuntu (static, `scripts/host-config.sh`; tooling in `bastion/`) |
| 192.168.3.101–199 | UniFi DHCP pool. UniFi defaults a new network to .6–.254, so shrink it to this range: fixed-IP reservations protect the four hosts, but the API VIP (.10) and the MetalLB pool have no MAC to reserve against. |
| 192.168.3.200–229 | MetalLB pool. **Must be outside the DHCP pool.** |
| 192.168.3.200 | Shared gateway (`*.jimdaga.dev`, parked until the Cloudflare move) |
| 192.168.3.201 | LAN gateway: `https://<app>.lab.internal` (`helm/charts/lan-gateway`) |

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
| -5 | secret-stores | Vault ClusterSecretStore + ExternalSecrets (Cloudflare token, Grafana admin, PIA, *arr API keys) |
| 0 | cluster-issuers | Let's Encrypt staging + prod via Cloudflare DNS-01 |
| 0 | external-dns | DNS records from HTTPRoutes/Services → Cloudflare |
| 0 | kube-prometheus-stack | Prometheus, Alertmanager, Grafana |
| -1 | media-storage | Static NFS PV/PVC `media/media-data` → daga-nas `192.168.3.21`, shared by all media apps |
| 0 | prowlarr | Indexer manager; API key from Vault (`secret/prowlarr`), synced to Radarr/Sonarr |
| 0 | radarr | Movies → `/data/movies`; config on Longhorn, API key from Vault |
| 0 | sonarr | TV → `/data/tv`; config on Longhorn, API key from Vault |
| 0 | qbittorrent | qBittorrent behind a Gluetun sidecar (PIA OpenVPN, port forwarding); downloads under `/data` on `media-data` |
| -15 | lab-ca | Homelab root CA + `lab-internal-ca` ClusterIssuer (cert-manager) for `*.lab.internal` |
| 0 | homepage | LAN dashboard (gethomepage.dev), *arr widgets via Vault API keys |
| 5 | lan-gateway | LAN-only Envoy Gateway on 192.168.3.201: HTTPS `*.lab.internal` routes to the admin/media UIs; owns the cluster GatewayClass |
| 5 | shared-gateway | GatewayClass, shared Gateway, wildcard cert, HTTPRoutes for UIs |

metrics-server is bundled by RKE2, so it isn't listed here. RKE2's bundled ingress-nginx is disabled.

Sync waves between child apps work because `argocd/apps/argocd/values.yaml` restores the Application health check. Vault stays unhealthy until it's initialized, which **intentionally holds every later wave** until you run the Vault steps below.

### Why Envoy Gateway

ingress-nginx was retired in March 2026, and Gateway API is where things are headed. Envoy Gateway is a CNCF, Gateway-API-native implementation that doesn't depend on which CNI you run.

## LAN access (`*.lab.internal`)

`https://<app>.lab.internal` for every UI, LAN only, via the Envoy gateway on **192.168.3.201** with a wildcard certificate from the homelab CA (`helm/charts/lab-ca`). HTTP redirects to HTTPS. LAN is trusted: the *arr apps run with `AUTH__METHOD=External` (no built-in login; Servarr treats every proxied request as non-local, so "disabled for local addresses" can't work behind a gateway), and qBittorrent bypasses auth for the pod network the gateway connects from. **Before exposing any of these through the public shared-gateway, give them real auth.**

| URL | App |
|---|---|
| `homepage.lab.internal` | Homepage: dashboard linking everything below (config in `argocd/apps/homepage/values.yaml`) |
| `torrent.lab.internal` | qBittorrent |
| `sonarr.lab.internal` / `radarr.lab.internal` / `prowlarr.lab.internal` | *arr apps |
| `argocd.lab.internal` | Argo CD |
| `grafana.lab.internal` | Grafana |
| `longhorn.lab.internal` | Longhorn UI (no auth of its own) |
| `vault.lab.internal` | Vault UI |

- **DNS:** one UniFi A record per name → `192.168.3.201`. Add a route in `helm/charts/lan-gateway/values.yaml` and a DNS record for each new app.
- **Trust the CA** once per device. Export it: `kubectl -n cert-manager get secret lab-internal-ca -o jsonpath='{.data.ca\.crt}' | base64 -d > dagalab-lab-ca.crt`
  - macOS: `sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain dagalab-lab-ca.crt`
  - iOS: AirDrop/email the file, install the profile, then Settings → General → About → Certificate Trust Settings → enable it.
  - Firefox uses its own store unless `security.enterprise_roots.enabled` is on.
- The CA key lives only in the cluster. A rebuilt cluster mints a new CA (re-trust devices) unless the `cert-manager/lab-internal-ca` secret is restored.

## Storage

| Storage | Backend | Access | Use for |
|---|---|---|---|
| `longhorn` StorageClass (default) | Node disks, 3 replicas | RWO | App config/databases, Vault, Prometheus |
| `media/media-data` PVC (`helm/charts/media-storage`) | Static NFS PV → daga-nas `192.168.3.21` | RWX | One shared media share: torrent downloads + library (Jellyfin, *arr apps) |

- No Synology CSI driver: the media apps need one existing share (so imports can hardlink), not a new share per PVC.
- Every media app mounts the same `media-data` claim; the NAS IP and export path live only in `helm/charts/media-storage/values.yaml`.
- Keep SQLite-backed app config (Jellyfin, *arr) on Longhorn, never on NFS.
- DSM: enable NFS (v4.1) and restrict each export to the nodes (192.168.3.11–13), not `*`. The share owner must match the containers' `PUID`/`PGID`.

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
   VAULT_TOKEN=... VAULT_ADMIN_USER=jim VAULT_ADMIN_PASSWORD=... CF_API_TOKEN=... GRAFANA_ADMIN_PASSWORD=... \
   ./scripts/vault-bootstrap.sh
   # Every secret is optional and re-runnable; Grafana's password is generated if unset.
   ```
   vault-1/2 join raft and auto-unseal on their own. The remaining waves then sync.
6. Once the staging cert issues, flip `certificate.issuer` in `helm/charts/shared-gateway/values.yaml` to `letsencrypt-prod`.

## Media stack plan

Deploy order, so Jellyfin scans an already clean, ID-tagged library once:

1. **qBittorrent**: done. Gluetun/PIA sidecar, saves to `/data/downloads/{movies,tv}` (categories set by the *arr apps).
2. **Prowlarr**: deployed. Indexers, synced to Radarr/Sonarr. API keys for all *arr apps are generated into Vault by `scripts/vault-bootstrap.sh`.
3. **Radarr** (`/data/movies`) and **Sonarr** (`/data/tv`): deployed; config on Longhorn, `media-data` mounted at `/data`. Hardlinks verified across `downloads/` ↔ library.
   - App settings that live in their databases (root folders, naming, hardlinks, qBittorrent client + categories, Prowlarr app sync) are applied idempotently by `scripts/arr-run.sh scripts/arr-wire.py`; rerun after a rebuild.
   - qBittorrent: auth bypass for the pod network (10.42.0.0/16), automatic torrent management, categories `movies`/`tv` → `/data/downloads/{movies,tv}`.
   - Set folder naming **before** the first import: Radarr `{Movie CleanTitle} ({Release Year}) [tmdbid-{TmdbId}]`, Sonarr `{Series TitleYear} [tvdbid-{TvdbId}]`.
   - Existing library imported 2026-10-12 with `scripts/arr-import.py` (plan → add → rescan → rename): 196 movies, 23 shows, all **unmonitored** (importing never downloads; monitor titles individually). Folders/files renamed to the ID format.
   - Settings → Connect → Jellyfin (library refresh on import).
4. **Jellyfin**:
   - Config/metadata on Longhorn (SQLite: never NFS). `media-data` mounted **read-only**. Transcode dir on node-local `emptyDir`.
   - **Hardware transcoding**: every node is an i7-9700 with UHD 630 (Quick Sync, `i915`, `/dev/dri/renderD128`, `render` GID 991): H.264 + HEVC 8/10-bit, no AV1. Deploy Intel's GPU device plugin (`gpu.intel.com/i915`, shared) so Jellyfin can schedule on any node.
   - **Local only for now**: `https://jellyfin.lab.internal` through the LAN gateway (192.168.3.201; add a route in `helm/charts/lan-gateway/values.yaml`). TVs on the main LAN won't auto-discover across VLANs; enter the server URL once per client. TV apps can't install the homelab CA, so Jellyfin will likely also need a plain-HTTP route (decide when deploying it).
   - Libraries: Movies (`/data/movies`), Shows (`/data/tv`), Home Videos (`/data/theme-parks`).
   - Later: `jellyfin.jimdaga.dev` via the shared gateway once the Cloudflare move happens (see TODO).

## TODO

- [ ] kube-vip for an HA API endpoint (192.168.3.10)
- [ ] Backups: Longhorn → S3/NAS, Vault raft snapshots, RKE2 etcd snapshots off-node
- [ ] Remote Terraform state
- [ ] Move `jimdaga.dev` DNS from IONOS to Cloudflare **after the NFL season** (the pick'em web app lives on that domain); then unpark cluster-issuers, external-dns, shared-gateway and expose `jellyfin.jimdaga.dev`
- [ ] Jellyseerr (request UI for Jellyfin), optional
- [ ] Radarr: retry `scripts/arr-run.sh scripts/arr-import.py add` for Captain America: The Winter Soldier, Halloween (1978), Killer Klowns from Outer Space (api.radarr.video returned HTTP 500 for these ids on 2026-10-12), then `rename`
- [ ] Optional: replace Forgetting Sarah Marshall's 2-part CD1/CD2 rip (Radarr can't track multi-part files); Amazon What They Know About Us has no TMDB/TVDB entry and stays unmanaged
- [ ] Optional: stop `media` renaming the DSM-denied folders (remove "Delete subfolders and files" at the `video` root, re-grant per media folder)
