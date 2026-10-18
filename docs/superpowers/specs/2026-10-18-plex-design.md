# Plex on dagalab: design

Date: 2026-10-18

## Intent

What the user asked for:

- A Plex Media Server on the cluster, serving the existing media share (Movies + TV).
- At home, the Plex apps show the user's library alongside friends' shared libraries.
- Share Movies and TV with a few friends who have Plex Pass. The user does not buy Plex Pass.
- Friends reach the server directly (WAN port forward), not through Plex Relay.

Assumptions confirmed with the user:

- Jellyfin is unchanged and stays the family/public server (`popcorn.dagaflix.com`); Plex is an addition.
- Plex reads the library; Sonarr/Radarr remain the only writers.

Consequences of no Plex Pass (Plex's 2025 rules):

- Friends with Plex Pass may stream the user's server remotely; the user may stream their (Plex Pass) servers.
- No hardware transcoding, LAN included: it's tied to the server owner's account. Plex is CPU-only and requests no
  GPU (Jellyfin keeps the node's Quick Sync share). Most LAN clients direct play; heavy transcodes belong on Jellyfin.

Success: a phone on cellular plays a title from the user's library over a direct (non-relay) connection, the user
plays a friend's title at home, and `https://plex.lab.internal` works on the LAN.

## Deployment

`argocd/apps/plex/` (`application.yaml` + `values.yaml`), bjw-s `app-template` 5.2.1 like Jellyfin, namespace
`media`, sync wave 0 (after `media-storage`, wave -1).

- Image `plexinc/pms-docker`, pinned to an exact tag (Renovate's helm-values manager bumps it).
- Runs as DSM user `media` via the image's `PLEX_UID=1029` / `PLEX_GID=100` (its init starts as root, then drops to
  that user; root is squashed on the NFS share, so only uid 1029 can read the library).
- `TZ=America/New_York`; `ADVERTISE_IP=http://192.168.3.202:32400/`. No `ALLOWED_NETWORKS`: the image maps it to
  Plex's "allowed without auth" list, which on an internet-facing server would hand every listed network admin access.
  Local vs remote is Plex's "LAN Networks" pref (`LanNetworksBandwidth`) = `192.168.0.0/24,192.168.3.0/24,10.42.0.0/16`
  (main LAN, VLAN 3, pod network for requests via the LAN gateway); VLAN 4 (DMZ) is deliberately not local.
- Strategy `Recreate`: never two Plex instances on one database.
- CPU only; requests `500m` / `1Gi`, memory limit `4Gi`.
- Probes: HTTP `GET /identity` on 32400 for liveness/readiness; startup probe allows ~5 minutes (first start
  initialises the database).

Storage:

| Mount | Source | Notes |
|---|---|---|
| `/config` | Longhorn PVC, `30Gi`, RWO | Plex DB + metadata. Never NFS (SQLite locking). |
| `/transcode` | `emptyDir`, `sizeLimit: 20Gi` | node-local scratch |
| `/data` | existing claim `media-data`, **read-only** | libraries: Movies `/data/movies`, TV `/data/tv` |

`personal/`, `iMovie/`, `youtube/` are never added as libraries (DSM also denies them to `media`).
`theme-parks/` is not added (user chose Movies + TV only).

## Networking

- Service `type: LoadBalancer`, `loadBalancerIP: 192.168.3.202` (MetalLB pool .200–.229; .202 verified free),
  TCP 32400, `externalTrafficPolicy: Local` so Plex sees real client IPs (local vs remote). In L2 mode the node
  running the pod announces .202, so the IP follows the pod.
- Not behind Envoy and not on `.200`: the public shared-gateway keeps carrying only Jellyfin.
- UCG port forward, set by the user in UniFi: WAN TCP `32400` → `192.168.3.202:32400`. Plex "Remote Access" →
  "Manually specify public port" = `32400`. Plex reports the WAN IP to plex.tv itself; no DDNS record.
- LAN UI: route `{ host: plex, namespace: media, service: plex, port: 32400 }` in
  `helm/charts/lan-gateway/values.yaml` → `https://plex.lab.internal`, a UniFi DNS A record → `.201`, and a Homepage
  tile (link + `siteMonitor` on `/identity`; no widget, which would need a Plex token).

Security: 32400 becomes internet-facing. Plex auth protects it; the pinned image is kept current through Renovate
(Plex had serious CVEs in 2025, so update PRs are merged promptly).

## Bootstrap (one-time, manual; documented in README)

1. Claim: `kubectl -n media port-forward svc/plex 32400:32400`, open `http://localhost:32400/web`. Plex sees
   localhost and offers to claim the server to the user's account. No claim token in git or Vault; the account
   link lives in `/config` on Longhorn.
2. Name the server (default `dagalab`).
3. Libraries: Movies → `/data/movies`, TV Shows → `/data/tv`.
4. Settings → Library: **turn off** "Empty trash automatically after every scan" (an NFS blip mid-scan must not
   drop the library); leave "Scan automatically" on.
5. Settings → Remote Access: manual public port 32400; confirm "Fully accessible outside your network".
6. Share Movies + TV with friends (Plex web → Manage Library Access).

Re-run 1–6 only after losing the `/config` volume.

## Failure modes

- Pod reschedules: Recreate → one instance; .202 moves with the pod; clients reconnect; Plex re-publishes its
  addresses to plex.tv.
- NAS/NFS outage: library is read-only so nothing is damaged; auto-empty-trash off so metadata survives.
- WAN IP change: Plex rediscovers and republishes it.

## Docs

- `CLAUDE.md`: network line adds `.202 Plex (WAN 32400 forwarded; second internet-facing service, not via the
  gateway)`; a Plex line (CPU-only, no Plex Pass, read-only library, claim via port-forward).
- `README.md`: Plex bootstrap steps (above) next to the media stack plan.

## Out of scope

Plex Prometheus metrics/alerts, a separate kids library, scripted library/friend setup, any Jellyfin change,
hardware transcoding.

## Testing / done when

1. Argo CD: `plex` Synced/Healthy; pod process runs as uid 1029; writing to `/data` fails (read-only).
2. `https://plex.lab.internal` loads with the lab CA; `http://192.168.3.202:32400/identity` answers from the LAN.
3. Server claimed via port-forward and listed in the user's Plex account.
4. Remote Access shows "Fully accessible outside your network" after the UCG forward.
5. Library scan counts roughly match Radarr's movies and Sonarr's series with files.
6. Phone on cellular: plays a title from this server (direct, not relay) and a title from a friend's server.
7. Homepage tile renders and its site monitor is green (Playwright screenshot check).
