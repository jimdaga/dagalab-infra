# Home Assistant on dagalab: design

Date: 2026-10-14. Status: approved in chat, pending spec review.

## Intent

Run Home Assistant on the RKE2 cluster, managed like the other apps (Argo CD, Longhorn, Vault), at
`https://home.lab.internal`. It talks to:

- **Wi-Fi/LAN devices on the main LAN** (plugs, bridges, Cast/Sonos, TVs, cloud integrations), and
- **lab infrastructure** (UniFi, the NAS, Jellyfin, Sonarr/Radarr).

Out of scope: Zigbee/Z-Wave/Thread radios (no USB dongle), the HA OS add-on store, access from
outside the home, Grafana dashboards/alerts for HA.

Facts from the user: the main LAN is **not isolated** from VLAN 3 (routing works both ways today),
and the hostname is `home.lab.internal`.

## Deployment and networking

- `argocd/apps/home-assistant/application.yaml` + `values.yaml`: bjw-s `app-template` (same chart
  and version as Jellyfin), image `ghcr.io/home-assistant/home-assistant` pinned to an exact tag
  (Renovate bumps it). Namespace `home-assistant`, sync wave 0 (after `secret-stores`), the
  standard sync policy and retry block.
- `hostNetwork: true`, `dnsPolicy: ClusterFirstWithHostNet`: HA's mDNS/SSDP/broadcast discovery runs
  from the node's VLAN 3 interface and reaches the main LAN by routing. Port 8123 is exposed on the
  node running the pod (acceptable: the LAN is trusted and not segmented).
- 1 replica, `Recreate` strategy (HA must never run twice against the same config). No node
  pinning.
- `/config`: Longhorn PVC, 5Gi (recorder SQLite DB and `.storage`). No NFS.
- `configuration.yaml` comes from a ConfigMap mounted read-only via `subPath`:
  - `default_config:`
  - `http:` with `use_x_forwarded_for: true` and `trusted_proxies: [10.42.0.0/16]` (the Envoy LAN
    gateway forwards from the pod network; without this HA answers 400).
  - `automation: !include automations.yaml`, `script: !include scripts.yaml`,
    `scene: !include scenes.yaml`, so the UI editors keep working.
  - `prometheus:` (see Metrics).
- An init container creates empty `automations.yaml` (`[]`), `scripts.yaml` and `scenes.yaml` in `/config` when
  they are missing.
- UI-made state (devices, dashboards, automations, integrations) lives only on the Longhorn volume,
  not in git, the same as Homarr's database.

## Access

- `helm/charts/lan-gateway/values.yaml`:
  `{ host: home, namespace: home-assistant, service: home-assistant, port: 8123 }` gives
  `https://home.lab.internal` (lab CA cert, websockets through Envoy).
- UniFi DNS record `home.lab.internal` → 192.168.3.201 is a manual step (README TODO).
- Never on the public shared-gateway.

## Bootstrap: `scripts/home-assistant-setup.sh` + `scripts/home-assistant-setup.py`

Same style as `seerr-setup` / `homarr-setup`: a port-forward, safe to re-run, secrets read from
Vault in-process and never printed.

1. Onboarding over the API (only while not done): admin user `jim` with a generated password, core
   config (home location = the Homepage weather widget's 42.5274, -71.7563; US customary units, °F;
   time zone `America/New_York`; currency USD), analytics off.
2. Vault `secret/home-assistant`: `admin-password`, plus `api-token`, a long-lived access token
   (created once; the script reuses an existing one).
3. Integrations through the config-flow API, each skipped when a config entry for it exists:

| Integration | Connection | Credentials |
|---|---|---|
| UniFi Network | `https://192.168.3.1`, verify SSL off | Homepage's view-only `homepage` account (Vault `secret/homepage-devices` `unifi-password`) |
| Synology DSM | `192.168.3.20:5000`, no SSL | `homepage` account (`synology-password`) |
| Jellyfin | `http://jellyfin.media.svc.cluster.local:8096` | new non-admin Jellyfin user `homeassistant`; the password is generated and stored in `secret/home-assistant` (`jellyfin-password`). The user is created via the Jellyfin API with an admin API key |
| Sonarr | `http://sonarr.media.svc.cluster.local:8989` | existing Sonarr API key |
| Radarr | `http://radarr.media.svc.cluster.local:7878` | existing Radarr API key |

Discovered main-LAN devices are confirmed by the user in the UI (each needs input).

## Dashboards

- Homepage (`argocd/apps/homepage/values.yaml`): Home Assistant tile, `homeassistant` widget, the
  token via a new ExternalSecret in `helm/charts/secret-stores`.
- Homarr (`scripts/homarr-board.py`): Home Assistant integration (URL + token) and an app tile on
  the Platform board.

## Metrics

- The `prometheus:` integration exposes `/api/prometheus`.
- A ServiceMonitor scrapes it with the token from an ExternalSecret (`bearerTokenSecret`).
- No Grafana dashboard or alerts yet.

## Docs

README:
- `home.lab.internal` row in the LAN access table.
- An IP-plan note that HA uses the node's own network.
- TODOs: the UniFi DNS record; review the discovered devices.

CLAUDE.md: one line on Home Assistant (where config lives, setup script).

## Testing / done when

- Argo: `home-assistant` Synced/Healthy, and `secret-stores`, `lan-gateway` and `homepage` still Healthy.
- `curl` via the gateway returns HA's login page (no 400 from the proxy check).
- Re-running the setup script reports every step as already set.
- Playwright screenshots: HA overview showing the integrations, Homepage's HA tile, Homarr's Platform board.
- The Prometheus target for HA is up.
