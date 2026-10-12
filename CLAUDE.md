# CLAUDE.md

GitOps repo for a 3-node homelab RKE2 cluster on Ubuntu 26.04 bare metal (dagakube01–03.lab.internal = 192.168.3.11–13; apps on jimdaga.dev; lab VLAN 192.168.3.0/24). Argo CD app-of-apps; see README.md for layout, network plan and bootstrap.

## Conventions

- One folder per app under `argocd/apps/{app}/` containing `application.yaml` (+ `values.yaml` for upstream charts). The root app (`argocd/bootstrap/root-app.yaml`) picks up every `*/application.yaml`. Nothing else to register.
- Upstream charts: multi-source Application, values via `$values/argocd/apps/{app}/values.yaml`. Pin `targetRevision` to an exact version (Renovate bumps it).
- Glue config (CRs that depend on another app's CRDs) goes in a local chart under `helm/charts/{app}` with its own Application.
- Order apps with `argocd.argoproj.io/sync-wave`: CRD/operator apps first, their config after, routes last.
- Keep all Applications on `project: default`, `automated` with `prune` + `selfHeal`, `CreateNamespace=true`, the standard retry block. Add `ServerSideApply=true` for charts with large CRDs.
- No rendering step. Files are applied as written.
- Park an app by renaming its `application.yaml` to `application.yaml.disabled` (root only includes `*/application.yaml`). ExternalSecrets in `helm/charts/secret-stores` take `enabled: false`. Currently parked until the Cloudflare token exists: cluster-issuers, external-dns, shared-gateway.
- Secrets live in Vault and reach workloads via ExternalSecrets in `helm/charts/secret-stores`. The only hand-made secret is `vault/vault-aws-kms`. Anything consuming a Vault-sourced secret must sync at a later wave than `secret-stores`.
- Never commit secrets, Vault init output, RKE2 tokens, or kubeconfigs.
- Hosts: bastion (Raspberry Pi) `jim@192.168.3.100`, tooling via `bastion/setup.sh`, sudo needs a password. Nodes `jim@192.168.3.11–13`, passwordless sudo. Identity/static IPs via `scripts/host-config.sh`; node prereqs via `cluster/rke2/node-prep.sh`.
- Network (VLAN 3, 192.168.3.0/24): .10 API VIP (reserved), .11–.13 nodes, .20 daga-nas port 1 (DSM/SMB), .21 daga-nas port 2 (**NFS for Kubernetes only**), .100 bastion, .101–.199 DHCP, .200–.229 MetalLB.
- Storage: Longhorn (default, 3 replicas) for app data; media apps share one static NFS claim `media/media-data` from `helm/charts/media-storage` (NAS IP/path set only there). No Synology CSI. Keep SQLite app config on Longhorn, not NFS. Media containers run as DSM user `media` (PUID 1029, PGID 100); DSM share permissions are enforced by numeric UID/GID. Media share layout (inside `/data` = `/volume2/video`): `FullVideo/Title (Year)/` = Radarr root, `TvShows/Show (Year)/Season NN/` = Sonarr root, `downloads/{movies,tv}` = qBittorrent categories (same share, so imports hardlink). `ThemeParks/` (fan videos), `_review/`, `_reorg/` (undo log), `_to-delete/` stay outside the *arr roots. `personal/`, `iMovie/`, `youtube/` are backed up by Hyper Backup: never move or modify them.
- RKE2 v1.36.5 is running on all three nodes. kubectl from the bastion: `~/.kube/config`, context `dagalab`, API `k8s.lab.internal:6443`. Argo CD is bootstrapped (app-of-apps, all active apps Synced/Healthy); Vault is initialized (init output lives outside the repo). Don't change the cluster or AWS unless asked.

## Validation

- `make lint` / `make template` for local charts.
- Placeholders are marked `TODO`; `grep -rn TODO argocd helm`.
