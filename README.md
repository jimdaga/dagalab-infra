# dagalab-infra

GitOps config for the dagalab homelab: a 3-node RKE2 cluster on Ubuntu 26.04 managed by Argo CD (app-of-apps, mostly upstream Helm charts). UIs live on `<app>.lab.internal` (LAN only); Jellyfin is public at `dagaflix.com`; `jimdaga.dev` is reserved for lab experiments.

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
| 192.168.3.200 | Public shared gateway (`helm/charts/shared-gateway`): the UCG forwards WAN TCP 443 here. Only Jellyfin at `popcorn.dagaflix.com` and Seerr at `requests.dagaflix.com` and Audiobookshelf at `books.dagaflix.com`; every other hostname (bare domain, made-up subdomains, raw IP) gets a blank placeholder page, `www` redirects to the bare domain. Platform UIs never go on it |
| 192.168.3.201 | LAN gateway: `https://<app>.lab.internal` (`helm/charts/lan-gateway`) |
| 192.168.3.202 | Plex (`argocd/apps/plex`): LoadBalancer TCP 32400, `externalTrafficPolicy: Local`. The UCG forwards WAN TCP 32400 here (direct remote access for friends; not via Envoy) |

`dagaflix.com` is a DNS-only Cloudflare A record kept at the WAN IP by `cloudflare-ddns` (Jellyfin streams direct; Cloudflare's proxy terms don't allow video). The certificate comes from Let's Encrypt via Cloudflare DNS-01, so no port 80 is forwarded.

### Switching

Lab devices hang off an unmanaged switch on the Cloud Gateway Ultra's **Port 3**: Native VLAN = **Lab (3)**, Tagged VLAN Management = **Block All**. Everything behind it lands on VLAN 3 untagged, so devices need no VLAN config.

If a device on Port 3 can't reach 192.168.3.1, run `sudo tcpdump -eni eth0 -c 10 arp` on it. Frames showing `802.1Q … vlan 3` mean the port is sending tagged traffic despite the UI. Flip the native VLAN to Default, apply, then set it back to Lab (3) to re-provision the port.

### DNS

Two zones, two owners:

| Names | Answered by | How records get there |
|-------|-------------|-----------------------|
| `<host>.lab.internal` (machines) | UniFi gateway (192.168.3.1) | Static hosts (nodes, bastion) need a manual UniFi DNS record; DHCP clients register automatically |
| `dagaflix.com` (public Jellyfin) | Cloudflare (registered at IONOS) | `cloudflare-ddns` |
| `<app>.jimdaga.dev` (later) | IONOS for now | external-dns once the zone moves to Cloudflare |

- VLAN 3 network settings in UniFi: set **Domain Name** to `lab.internal`, so DHCP clients get it as their search domain and it's appended to their registered names.
- Local DNS records in UniFi: `k8s`, `dagakube01`–`03`, and `bastion` under `lab.internal` (see the Network table).
- `.internal` is reserved by ICANN for private networks, so these names never leak to or collide with public DNS.
- `jimdaga.dev` is still on IONOS nameservers (`ui-dns.*`). Before external-dns can manage it: move its nameservers to Cloudflare, add the zone to the API token in Vault `secret/cloudflare`, and add it to `domainFilters`.


## Stack

| Wave | App | What |
|-----:|-----|------|
| -30 | argocd | Argo CD, self-managed |
| -20 | metallb | LoadBalancer IPs on bare metal |
| -20 | longhorn | Replicated block storage (default StorageClass) |
| -20 | cert-manager | Certificates |
| -20 | envoy-gateway | Gateway API implementation (ingress-nginx replacement) + Gateway API CRDs |
| -15 | intel-gpu-operator | Intel device plugins operator (GPU only; webhook certs from cert-manager) |
| -15 | metallb-config | IP pool / L2 advertisement |
| -10 | intel-gpu-plugin | Exposes each node's UHD 630 as `gpu.intel.com/i915` (shared ×4) for Quick Sync |
| -10 | vault | HA Vault, raft on Longhorn, AWS KMS auto-unseal |
| -10 | external-secrets | Syncs Vault secrets into k8s Secrets |
| -5 | secret-stores | Vault ClusterSecretStore + ExternalSecrets (Cloudflare token, Grafana admin, PIA, *arr API keys) |
| 0 | cluster-issuers | Let's Encrypt staging + prod via Cloudflare DNS-01 |
| 0 | cloudflare-ddns | Keeps `dagaflix.com` at the WAN IP (DNS-only) |
| 0 | external-dns | DNS records from HTTPRoutes/Services → Cloudflare (parked) |
| 0 | kube-prometheus-stack | Prometheus, Alertmanager, Grafana |
| -1 | media-storage | Static NFS PV/PVC `media/media-data` → daga-nas `192.168.3.21`, shared by all media apps; plus `jellyfin-trickplay` (`/volume2/trickplay`) |
| 0 | prowlarr | Indexer manager; API key from Vault (`secret/prowlarr`), synced to Radarr/Sonarr |
| 0 | jellyfin | Media server: library read-only from `media-data`, Quick Sync via the GPU plugin; `https://` and plain `http://jellyfin.lab.internal` |
| 0 | seerr | Request UI (Jellyfin sign-in) → Radarr/Sonarr; public `https://requests.dagaflix.com`, LAN `https://seerr.lab.internal` |
| 0 | audiobookshelf | Audiobooks + podcasts server; public `https://books.dagaflix.com`, LAN `https://books.lab.internal` |
| 0 | bookshelf | Audiobook manager (Readarr fork) → `audiobooks` share `library/`; `https://bookshelf.lab.internal`; hourly `seed-cleanup` CronJob |
| 0 | libation | Nightly CronJob: Audible library → `audiobooks` share `audible/` |
| 0 | radarr | Movies → `/data/movies`; config on Longhorn, API key from Vault |
| 0 | sonarr | TV → `/data/tv`; config on Longhorn, API key from Vault |
| 0 | qbittorrent | qBittorrent behind a Gluetun sidecar (PIA OpenVPN, port forwarding); downloads under `/data` on `media-data` |
| -15 | lab-ca | Homelab root CA + `lab-internal-ca` ClusterIssuer (cert-manager) for `*.lab.internal` |
| 0 | homepage | LAN dashboard (gethomepage.dev), *arr widgets via Vault API keys |
| 0 | homarr | Trial alternative dashboard (homarr.dev), compared against Homepage; config lives in its DB |
| 0 | uptime-kuma | Uptime monitors + notifications; config lives in its SQLite DB on Longhorn |
| 5 | lan-gateway | LAN-only Envoy Gateway on 192.168.3.201: HTTPS `*.lab.internal` routes to the admin/media UIs; owns the cluster GatewayClass |
| 5 | shared-gateway | Public Gateway (WAN 443), `dagaflix.com` wildcard cert, Jellyfin + Seerr routes |

metrics-server is bundled by RKE2, so it isn't listed here. RKE2's bundled ingress-nginx is disabled.

Sync waves between child apps work because `argocd/apps/argocd/values.yaml` restores the Application health check. Vault stays unhealthy until it's initialized, which **intentionally holds every later wave** until you run the Vault steps below.

### Why Envoy Gateway

ingress-nginx was retired in March 2026, and Gateway API is where things are headed. Envoy Gateway is a CNCF, Gateway-API-native implementation that doesn't depend on which CNI you run.

## LAN access (`*.lab.internal`)

`https://<app>.lab.internal` for every UI, LAN only, via the Envoy gateway on **192.168.3.201** with a wildcard certificate from the homelab CA (`helm/charts/lab-ca`). HTTP redirects to HTTPS. LAN is trusted: the *arr apps run with `AUTH__METHOD=External` (no built-in login; Servarr treats every proxied request as non-local, so "disabled for local addresses" can't work behind a gateway), and qBittorrent bypasses auth for the pod network the gateway connects from. **Before exposing any of these through the public shared-gateway, give them real auth.**

| URL | App |
|---|---|
| `jellyfin.lab.internal` | Jellyfin (also plain `http://` for TV apps) |
| `seerr.lab.internal` | Seerr (requests) |
| `books.lab.internal` | Audiobookshelf (audiobooks + podcasts) |
| `bookshelf.lab.internal` | Bookshelf (audiobook requests) |
| `homepage.lab.internal` | Homepage: dashboard linking everything below, with cluster/node CPU+memory and pod status (read-only ClusterRole), Longhorn usage, Sonarr/Radarr release calendar, VPN status (Gluetun control server, API key from Vault `secret/gluetun`). Config in `argocd/apps/homepage/values.yaml` |
| `homarr.lab.internal` | Homarr (trial): same services as Homepage as a drag-and-drop board with live integrations (Jellyfin now playing, Seerr requests, calendar incl. Bookshelf, downloads, VPN, Prowlarr, Bazarr, missing media; Audiobookshelf as a link tile only, since Homarr's audio stats widget needs an Audiobookshelf admin key; NAS health/volumes/resource charts and UniFi summary + Wi-Fi/wired clients and a Home Assistant integration on Platform, via Homepage's `homepage` device accounts: UCG over HTTPS with its self-signed cert trusted + pinned to the IP, DSM over HTTP :5000 on the lab VLAN since its cert's CA isn't served). Config is in its SQLite DB on Longhorn, **not** in git; seeded by `scripts/homarr-setup.sh` (admin `jim`, password + API key in Vault `secret/homarr`); `scripts/homarr-board.py` lays out four boards shown as header tabs (Home, Library, Downloads, Platform; re-runnable, resets UI edits) |
| `home.lab.internal` | Home Assistant: `hostNetwork` pod (discovery from the node's VLAN 3 interface; port 8123 open on that node). `configuration.yaml` (location, units, Prometheus) is in `argocd/apps/home-assistant/values.yaml`; everything made in the UI lives on its Longhorn volume, **not** in git. First run, proxy trust for the gateway (HA 2026.10+ keeps `http:` settings in `.storage` and only applies a change once it's confirmed, so YAML can't set them) and lab integrations: `scripts/home-assistant-setup.sh` (admin `jim`, password + API token in Vault `secret/home-assistant`) |
| `torrent.lab.internal` | qBittorrent |
| `sonarr.lab.internal` / `radarr.lab.internal` / `prowlarr.lab.internal` | *arr apps |
| `argocd.lab.internal` | Argo CD |
| `grafana.lab.internal` | Grafana |
| `uptime.lab.internal` | Uptime Kuma: up/down monitors for every gateway route (LAN apps, public dagaflix.com hosts with cert expiry) plus Plex's WAN port and family-pickem.com/healthz/ (Cloudflare challenges the home IP, not the PIA exit), nodes, bastion, NAS (DSM, NFS), UCG, Kubernetes API, Plex's LoadBalancer and the internet; alerts to Discord #alerts. The public checks leave through `vpn-proxy` (Gluetun HTTP proxy on its own PIA tunnel, US Massachusetts; NetworkPolicy: only Uptime Kuma may use it, Homepage may read its control server) and come back in over the WAN like a visitor; the `VPN proxy (PIA)` monitor says when a public failure is the tunnel's. Status pages `/status/lab` (all), `/status/public`, `/status/infra` feed the Homepage tiles. Kept by `scripts/uptime-kuma-setup.sh` (re-run after adding a route or an `EXTRA`/`PUBLIC_EXTRA` entry in `scripts/uptime-kuma-setup.py`; `--dry-run` shows the changes). It runs in the cluster, so it can't report a whole-lab outage |
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
6. The shared gateway uses `letsencrypt-prod` (staging verified 2026-10-13). After a rebuild, staging first is safer if DNS-01 is in doubt.

## Media stack plan

Deploy order, so Jellyfin scans an already clean, ID-tagged library once:

1. **qBittorrent**: done. Gluetun/PIA sidecar, saves to `/data/downloads/{movies,tv}` (categories set by the *arr apps).
2. **Prowlarr**: deployed. Indexers, synced to Radarr/Sonarr. API keys for all *arr apps are generated into Vault by `scripts/vault-bootstrap.sh`.
3. **Radarr** (`/data/movies`) and **Sonarr** (`/data/tv`): deployed; config on Longhorn, `media-data` mounted at `/data`. Hardlinks verified across `downloads/` ↔ library.
   - App settings that live in their databases (root folders, naming, hardlinks, qBittorrent client + categories, Prowlarr app sync) are applied idempotently by `scripts/arr-run.sh scripts/arr-wire.py`; rerun after a rebuild. It also sets: a Jellyfin connection (refresh the library right after each import/upgrade/rename/delete; Jellyfin can't watch NFS, its own scan is 12-hourly; key `arr` from `jellyfin-setup.sh`), and subtitle files imported alongside releases (`srt,ass,ssa,vtt,sub,idx`). Quality profiles and custom formats belong to Recyclarr (item 6).
   - Seeding (private trackers, avoid hit-and-runs), also set by `arr-wire.py`: qBittorrent seeds every finished torrent (only downloads are queued, 3 at a time) and has no global stop. Per-tracker goals live on the Prowlarr indexers, flow to Radarr/Sonarr and are set on each torrent at grab. **TorrentLeech** (site rule 1:1 or 10 days), **IPTorrents** (1:1 or 14 days; added 2026-10-13, cookie + browser User-Agent login) and **wigornot** (ratio 10 or 1 year) are all set to **seed forever** (since 2026-10-12): the library copy is a hardlink, so seeding costs no extra space, and TorrentLeech's "torrents limit reached" counts downloads, not seeds. With a numeric goal, qBittorrent stops a torrent at whichever limit comes first and Radarr/Sonarr remove it. New tracker: add it to `TRACKERS` in `arr-wire.py` (seed goals + Prowlarr priority).
   - Indexers themselves (credentials) are added by hand in Prowlarr. wigornot uses the custom definition in `helm/charts/prowlarr-definitions` (its Torznab API rejects text queries on `t=tvsearch`).
   - qBittorrent: auth bypass for the pod network (10.42.0.0/16), automatic torrent management, categories `movies`/`tv` → `/data/downloads/{movies,tv}`.
   - Set folder naming **before** the first import: Radarr `{Movie CleanTitle} ({Release Year}) [tmdbid-{TmdbId}]`, Sonarr `{Series TitleYear} [tvdbid-{TvdbId}]`.
   - Existing library imported 2026-10-12 with `scripts/arr-import.py` (plan → add → rescan → rename): 196 movies, 23 shows, all **unmonitored** (importing never downloads; monitor titles individually). Folders/files renamed to the ID format.
4. **Jellyfin**: deployed. First-run, Quick Sync, libraries and API keys are applied by `scripts/jellyfin-setup.sh` (admin + keys in Vault `secret/jellyfin`). Jellyfin 12 only accepts `Authorization: MediaBrowser Token=...` (no `X-Emby-Token`/`api_key`).
   - Config/metadata on Longhorn (SQLite: never NFS). `media-data` mounted **read-only**. Trickplay (scrub previews) on its own NAS share `/volume2/trickplay` (PVC `jellyfin-trickplay`), mounted at `/config/data/trickplay`; NFO saving is off. Transcode dir on node-local `emptyDir`.
   - **Hardware transcoding**: every node is an i7-9700 with UHD 630 (Quick Sync, `i915`, `/dev/dri/renderD128`, `render` GID 991): H.264 + HEVC 8/10-bit, no AV1. Deploy Intel's GPU device plugin (`gpu.intel.com/i915`, shared) so Jellyfin can schedule on any node.
   - **Local only for now**: `https://jellyfin.lab.internal` through the LAN gateway (192.168.3.201; add a route in `helm/charts/lan-gateway/values.yaml`). TVs on the main LAN won't auto-discover across VLANs; enter the server URL once per client. TV apps can't install the homelab CA, so Jellyfin will likely also need a plain-HTTP route (decide when deploying it).
   - Libraries: Movies (`/data/movies`), Shows (`/data/tv`), Home Videos (`/data/theme-parks`), Sports (`/data/sports`, games from `scripts/sports-dl.py`; metadata only from their `.nfo` files, no online lookups).
   - **Public**: `https://popcorn.dagaflix.com` through the shared gateway (192.168.3.200, UCG port forward WAN 443). The subdomain is unadvertised: DNS is a wildcard (`*.dagaflix.com`, kept current by `cloudflare-ddns`) and the cert is a wildcard, so the name shows up in neither DNS enumeration nor CT logs, and `dagaflix.com`, any other name and the raw IP serve a blank page that doesn't identify the server. (It was a `/4orr` BaseUrl first; some TV apps, e.g. Trovu, ignore a server path, so Jellyfin serves from `/` again.) In-cluster consumers (Homepage, Homarr, Seerr, Sonarr/Radarr via `arr-wire.py`) use `http://jellyfin.media.svc.cluster.local:8096`. Same accounts as on the LAN; set Jellyfin's Networking → Known proxies to the pod network so it sees real client IPs.

5. **Seerr** (public `https://requests.dagaflix.com` via the shared gateway, LAN `https://seerr.lab.internal`): deployed. `scripts/seerr-setup.sh` signs in with the Jellyfin admin (Seerr admin = Jellyfin `jim`), enables Movies + Shows, adds Radarr/Sonarr (Recyclarr profiles: `HD Bluray + WEB` / `WEB-1080p`) and stores Seerr's API key in Vault `secret/seerr`. It also sets the application URL to the public name, points "watch in Jellyfin" links at `popcorn.dagaflix.com` and turns Seerr's local-account login off (public site: Jellyfin accounts only). Household members sign in with their Jellyfin accounts; approved requests are monitored + searched by Radarr/Sonarr.
6. **Supporting apps** (all in `media`, all deployed 2026-10-12):
   - **Recyclarr** (daily CronJob, config in `argocd/apps/recyclarr/values.yaml`): TRaSH Guides profiles + custom formats. Sonarr `WEB-1080p` (WEB preferred, Blu-ray 1080p allowed), Radarr `HD Bluray + WEB`; Golden Rule HD + Unwanted Formats (AV1, x265 at HD, LQ, upscaled, BR-DISK: all -10000). Every series/movie uses these profiles; the old built-in ones are unused. Run now: `kubectl -n media create job --from=cronjob/recyclarr recyclarr-manual`.
   - **Unpackerr**: extracts RAR'd releases (common on TorrentLeech) so Sonarr/Radarr can import; archives keep seeding, extracted copies are deleted after import.
   - **Bazarr** (`https://bazarr.lab.internal`): English subtitles (`.en.srt` next to the video) for everything Sonarr/Radarr manage, including back-fill. `scripts/bazarr-setup.sh` sets Sonarr/Radarr, the English profile (default for all), account-free providers (podnapisi, gestdown, tvsubtitles, yifysubtitles) and ignores image-based embedded subs (PGS/VobSub make Jellyfin burn them in); API key in Vault `secret/bazarr`. Add OpenSubtitles.com later for better coverage.
   - **Decluttarr**: every 10 min removes stalled (2h, no connections), failed, metadata-stuck and orphaned downloads so the *arr apps grab an alternative, and searches 3 missing items per run (each at most weekly). Private-tracker torrents are removed outright (a stalled one would hold a download slot forever). Tag a torrent `Keep` in qBittorrent to protect it. Ran in `test_run` mode first; live since 2026-10-14.
   - **Monitoring**: Exportarr sidecars (Sonarr/Radarr/Prowlarr/Bazarr) + Unpackerr metrics → Prometheus; Grafana dashboards "Media: pipeline overview" and "Media: Sonarr / Radarr / Prowlarr (Exportarr)"; alert rules in `helm/charts/media-monitoring/files/rules.yaml`. Alertmanager has no receiver yet (Discord TODO), so alerts only show in Grafana/Alertmanager.
   - **Notifications** (Discord server `dagalab`; webhook URLs in Vault `secret/discord` keys `webhook-alerts`, `webhook-media`, `webhook-media-noise`, synced as Secret `discord` to `monitoring` and `media`). Each event goes to one channel. **#alerts**: Alertmanager (all alerts, resolved too; Watchdog/InfoInhibitor dropped) + Sonarr/Radarr "manual import needed". **#media**: Sonarr release imported, Radarr movie imported, Seerr requests (pending, auto-approved, failed) and issues, Jellyfin watch activity (someone started or finished a movie/episode; Webhook plugin, home videos excluded). **#media-noise**: grabs, upgrades, deletions, Bazarr subtitle downloads. App side applied by `scripts/notify-setup.sh [--test]`; *arr health, indexer and Unpackerr failures come only from Alertmanager.
   - Setup order after a rebuild: `vault-bootstrap.sh` → `arr-run.sh arr-wire.py` → `jellyfin-setup.sh` → `seerr-setup.sh` → `bazarr-setup.sh` → `audiobookshelf-setup.sh` (all idempotent; Recyclarr/Unpackerr/Decluttarr need nothing).

7. **Plex** (`https://plex.lab.internal`, direct `192.168.3.202:32400`): deployed 2026-10-18 for sharing Movies + TV with friends (they have Plex Pass; this server doesn't, so CPU transcoding only). Library read-only, DB on Longhorn. One-time setup (repeat only after losing the `/config` volume):
   1. Claim: `kubectl -n media port-forward svc/plex 32400:32400`, open `http://localhost:32400/web`, sign in; Plex sees localhost and offers to claim the server. No claim token in git or Vault.
   2. Name the server (`dagalab`).
   3. Libraries: Movies → `/data/movies`, TV Shows → `/data/tv`. Nothing else (`personal/`, `iMovie/`, `youtube/` are never libraries; `theme-parks/` is Jellyfin-only).
   4. Settings → Library: **off** "Empty trash automatically after every scan" (an NFS blip mid-scan must not drop the library); "Scan my library automatically" on.
   5. Settings → Network → "LAN Networks" = `192.168.0.0/24,192.168.3.0/24,10.42.0.0/16` (main LAN, VLAN 3, pod network for requests via the LAN gateway; the DMZ VLAN 4 is deliberately remote). Leave "List of IP addresses and networks that are allowed without auth" **empty**: the server is internet-facing.
   6. UniFi: port forward WAN TCP 32400 → 192.168.3.202:32400. Plex Settings → Remote Access → "Manually specify public port" 32400 → "Fully accessible outside your network".
   7. Share Movies + TV with friends (Plex web → Settings → Manage Library Access).
8. **Audiobooks** (deployed 2026-10-20; spec `docs/superpowers/specs/2026-10-20-audiobooks-design.md`). Two DSM shares (NFS rule like `trickplay`: 192.168.3.11-13 RW, root squashed to guest, user `media` RW): `audiobooks` (`/volume2/audiobooks`: `library/` = Bookshelf root, `audible/` = Libation) and `podcasts` (`/volume2/podcasts`). Bookshelf downloads land in `media-data` `downloads/audiobooks` (qBittorrent category `audiobooks`) and are copied into `library/`; the hourly `bookshelf-seed-cleanup` CronJob deletes a torrent and its downloaded files once it has ratio ≥ 3 **and** 14 days seeded **and** is imported. One-time steps (repeat only after losing the volumes):
   1. Folders, as the `media` user: `kubectl -n media exec deploy/bookshelf -- s6-setuidgid abc mkdir -p /audiobooks/library /audiobooks/audible`.
   2. `VAULT_TOKEN=... scripts/audiobookshelf-setup.sh` **before** the `books` route is on the shared gateway: root `jim` (password in Vault `secret/audiobookshelf`), libraries Audiobooks (`/audiobooks/library` + `/audiobooks/audible`, hourly scan) and Podcasts (`/podcasts`), read-only `homepage` user + key.
   3. `scripts/arr-run.sh scripts/arr-wire.py`: Bookshelf's Audiobook profile (M4B > MP3 > Unknown Audio > Unknown Text), a release profile requiring an audio term in the raw title (`audiobook`, `audio book`, `m4b`, `mp3`, `unabridged`; keeps ebooks out), root folder, qBittorrent category + save path, Prowlarr app (categories 3030 + 3000 + 7020: TorrentLeech files audiobooks under Books, titled `[AUDIOBOOK]`). Prowlarr only syncs indexers whose recent audio results pass Bookshelf's test (wigornot didn't, 2026-10-20; it's retried every sync).
   4. Audible: a temporary pod on Libation's PVC (`libation`, image `rmcrackan/libation`, `sleep`, uid 1029), copy the git `Settings.json` into `/config` (the CLI needs one), then in a terminal `kubectl -n media exec -it libation-login -- /libation/LibationCli login-external --libationFiles /config -a <audible email> -l us`; open the URL, sign in, paste the final URL back; `list-accounts` should say authenticated; delete the pod. Libation keeps its token key in `/config/libation-master.key` (on the PVC). First run: `kubectl -n media create job --from=cronjob/libation libation-first`.
   5. Family accounts in Audiobookshelf (Settings → Users): long random passwords, no upload/delete. Phone apps use `https://books.dagaflix.com` everywhere.
   6. UniFi DNS: `books.lab.internal`, `bookshelf.lab.internal` → 192.168.3.201.
   Requesting a book: search it in Bookshelf and add it (only that book is monitored); it appears in Audiobookshelf within the hour.

## UPS / power

CyberPower CP1500PFCRM2U (1000 W) on USB to daga-nas; DSM is the NUT server ("Network UPS server", 192.168.3.20:3493,
UPS `ups`, allow list: the 3 nodes, the webserver, the bastion; the bastion must be added there) and goes to standby at low battery.

| When | What happens |
|---|---|
| On battery 1 min | Alert `UPSOnBattery` (Discord #alerts) |
| On battery 8 min | Alert `UPSClusterShutdownSoon` |
| On battery 10 min | Nodes (upssched timer): dagakube01 runs `scripts/lab-power.py stop-workloads --local` (pause Argo + operator, stop storage workloads, wait for Longhorn/NFS), writes `/var/lib/dagalab/ups-shutdown.json`, powers off; dagakube02/03 wait until every Longhorn volume is detached (max 8 min), power off |
| Low battery / NAS FSD | Every NUT client (nodes, webserver, bastion) shuts down; NAS standby |
| Power back < 10 min | Timers cancelled, nothing happens |
| Power back after the shutdown | NAS `ups-wake.py watch` (every minute): after 2 min on mains, Wake-on-LAN to any node that's down; dagakube01's `dagalab-ups-autostart` sees the marker and runs `lab-power.py start --local` (waits for NAS NFS, Longhorn CSI, uncordons, un-pauses Argo) |
| Battery ran out | NAS boots when power returns; `ups-wake.py boot` waits for NFS, then wakes the nodes (BIOS "stay off" on AC restore = the NAS starts them, after it's fully up) |

Setup: `VAULT_TOKEN=... scripts/nut-setup.sh` (all hosts + the NAS script; login in Vault `secret/nut`); files in
`hosts/nut/`, `nas/`. DSM Task Scheduler (user `jim`): Triggered task at **Boot-up**
`/usr/bin/python3 /var/services/homes/jim/dagalab/ups-wake.py boot >> /var/services/homes/jim/dagalab/ups-wake.log 2>&1`;
Scheduled task daily 00:00-23:59 **every 1 minute** `… ups-wake.py watch >> …/ups-wake.log 2>&1`.
BIOS: nodes Wake-on-LAN (wake from S5) + restore on AC loss **stay off**; webserver restore on AC loss **power on**.
Metrics: `nut-exporter` (monitoring) → Grafana "UPS (daga-nas)" (`ups:power_watts` = load% × 1000 W), alert rules in
`helm/charts/ups-monitoring`, Homepage UPS tile (Network group; Homepage needs a restart after config changes), Home Assistant NUT sensors.
Test without an outage: `DRY_RUN=1 /usr/local/lib/dagalab/ups-shutdown.sh` on a node; `ups-wake.py watch --dry-run --status "OB"`.

## TODO

- [ ] kube-vip for an HA API endpoint (192.168.3.10)
- [ ] Backups: Longhorn → S3/NAS, Vault raft snapshots, RKE2 etcd snapshots off-node. Includes the *arr apps: their built-in weekly backups land in `/config/Backups` on the same Longhorn volume, so nothing is off-cluster yet
- [ ] Remote Terraform state
- [ ] Move `jimdaga.dev` DNS from IONOS to Cloudflare **after the NFL season** (lab experiments); then unpark external-dns
- [ ] Radarr: retry `scripts/arr-run.sh scripts/arr-import.py add` for Captain America: The Winter Soldier, Halloween (1978), Killer Klowns from Outer Space (api.radarr.video returned HTTP 500 for these ids on 2026-10-12), then `rename`
- [ ] Radarr shows 4 unmapped folders under `/data/movies`: the 3 movies above + Forgetting Sarah Marshall (below)
- [ ] Right-size media resources from Prometheus once there's a week of history (first look 2026-10-12, max over ~4–14h): qBittorrent has **no requests/limits** (RSS ~130 MiB; its 13 GiB "working set" is reclaimable NFS page cache, so limit on RSS, not working set); Gluetun uses ~0.45 core at ~35 MB/s (OpenVPN crypto; WireGuard would be cheaper if PIA+Gluetun allows it); Jellyfin peaks ~1 GiB / 0.8 core during transcodes (limit 6Gi); Sonarr/Radarr/Prowlarr/Seerr all well under their limits. Query: `max_over_time(container_memory_rss{namespace="media"}[7d])`, `quantile_over_time(0.95, rate(container_cpu_usage_seconds_total{namespace="media"}[5m])[7d:5m])`
- [x] Decluttarr: review its test-run log (`kubectl -n media logs deploy/decluttarr`), then set `test_run: false` in `argocd/apps/decluttarr/values.yaml`
- [ ] Bazarr: add an OpenSubtitles.com account as a provider (best English coverage; free tier ~20/day)
- [ ] UniFi DNS: `bazarr.lab.internal` → 192.168.3.201
- [ ] Dashboard trial: keep Homepage or Homarr, then remove the other (Argo app, lan-gateway route, UniFi record, Vault `secret/homarr` if Homarr goes; Homarr reuses Homepage's Jellyfin key)
- [ ] UniFi DNS: `homarr.lab.internal` → 192.168.3.201
- [ ] UniFi DNS: `uptime.lab.internal` → 192.168.3.201
- [x] UniFi DNS: `books.lab.internal`, `bookshelf.lab.internal` → 192.168.3.201
- [x] UniFi DNS: `home.lab.internal` → 192.168.3.201
- [ ] Home Assistant: review Settings → Devices & services → Discovered (main-LAN devices) and add the ones wanted
- [ ] Audiobooks follow-ups (after Audiobookshelf/Bookshelf/Libation are running; spec `docs/superpowers/specs/2026-10-20-audiobooks-design.md`): Bookshelf Discord notifications (`scripts/notify-setup.py`); Decluttarr + Unpackerr for Bookshelf; Exportarr `readarr` sidecar, Grafana panels and alert rules for Bookshelf, plus an alert on a failed Libation Job
- [ ] Exportarr: when v3 is released, bump the sidecars and restore per-state queue panels/alerts (import stuck, stalled) in `helm/charts/media-monitoring`; v2.3.0 mislabels `*_queue_total` (one series, last item's state, value = total)
- [ ] Optional: replace Forgetting Sarah Marshall's 2-part CD1/CD2 rip (Radarr can't track multi-part files); Amazon What They Know About Us has no TMDB/TVDB entry and stays unmanaged
- [ ] Jellyfin Enhanced: paste a TMDB API key and an MDBList key in Dashboard → Plugins → Jellyfin Enhanced (ratings, "Elsewhere", genre discovery), then enable MDBList ratings
- [ ] Jellyfin: plugins (Playback Reporting, TMDb Box Sets, Session Cleaner, Chapter Segments Provider, Webhook, File Transformation, Plugin Pages, Home Screen Sections, Media Bar, Jellyfin Enhanced; repos IAmParadox27 + n00bcodr) and Jellyfin Enhanced settings live only in the Longhorn config volume: add them to `scripts/jellyfin-setup.py` so a rebuild restores them
- [ ] NAS monitoring via SNMP instead of a DSM admin account: enable DSM SNMPv3 (read-only user `snmp-ro`, SHA + AES; passwords in Vault `secret/synology-snmp`), deploy Prometheus snmp-exporter with the Synology module, Grafana NAS dashboard, alerts to Discord #alerts (RAID degraded, disk failing, volume filling, temperature), and a Homepage NAS tile from Prometheus. Then drop the `diskstation` widget, the `homepage-synology` ExternalSecret and the DSM `homepage` admin user (it's an administrator only because DSM's stats API requires one; Homarr's `daga-nas` integration uses it too, so switch that to SNMP-fed data or drop it first)
- [ ] Optional: stop `media` renaming the DSM-denied folders (remove "Delete subfolders and files" at the `video` root, re-grant per media folder)
- [ ] BIOS: nodes Wake-on-LAN + restore on AC loss "stay off"; webserver restore on AC loss "power on" (README "UPS / power")
- [x] UniFi: rule `ups-nut` Allow TCP, source zone Dmz 192.168.4.10 → destination zone Internal 192.168.3.20:3493 (Lab VLAN 3 is in zone Internal, DMZ VLAN 4 in zone Dmz)
- [ ] Home Assistant: phone notifications on power loss / restore (NUT sensors are in place)
- [ ] Optional: a real outage test (unplug for > 10 min) once the BIOS settings are done
- [x] DSM: add the bastion (192.168.3.100) to the NUT server allow list; then `scripts/nut-setup.sh 192.168.3.100`
- [x] DSM Task Scheduler: create the two `ups-wake.py` tasks (README "UPS / power")
