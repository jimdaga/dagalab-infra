# UPS (NUT) on dagalab: design

Date: 2026-10-21

## Intent

What the user asked for:

- The new UPS (CyberPower CP1500PFCRM2U, 1000 W, USB to daga-nas) protects the lab. The NAS is already the NUT
  server ("Network UPS server" on, allow list: the 3 nodes + the webserver 192.168.4.10) and goes to standby at low
  battery.
- NUT clients on the 3 k8s nodes and the webserver, so they shut down cleanly.
- Power / UPS metrics in Grafana (nut-exporter), Alertmanager alerts, a Homepage tile.
- Home Assistant: use its NUT integration (phone notifications on power loss/restore are a later project).
- The NAS must be fully up before the cluster starts again. The bastion must not become critical.
- BIOS changes come later; everything must work once they are made (and be harmless before).

Decisions taken with the user:

- Cluster: graceful shutdown after **10 minutes on battery** (the lab-power-cycle stop), plain NUT low-battery
  shutdown as the backstop. Power back within 10 minutes: nothing happens.
- Webserver: plain shutdown at **low battery only**.
- Power-up: automatic. The **NAS** sends Wake-on-LAN to the nodes (not the bastion); node BIOS "restore on AC loss"
  = stay off, so the NAS decides when the nodes start.
- Bastion (on the UPS): plain NUT client, shuts down at low battery, no other role.

UPS facts (NUT on 192.168.3.20:3493, UPS name `ups`, vars readable without login): `ups.load` 12 %,
`ups.realpower.nominal` 1000 (no direct watts: power = load/100 × 1000 W), `battery.runtime` ~3800 s at that load,
`battery.charge.low` 10, `battery.runtime.low` 300. Synology's NUT login for clients: user `monuser` (fixed by DSM).

Success: unplugging the UPS from the wall for 2 minutes raises the on-battery alert, shows on battery in Grafana and
Homepage, starts the 10-minute timers on the nodes, and plugging back in cancels them with nothing shut down. The
shutdown and wake paths pass their dry runs and event simulations.

## Components

### 1. NUT clients (`scripts/nut-setup.sh`, config in `hosts/nut/`)

Installs `nut-client` on dagakube01-03, the webserver (192.168.4.10) and the bastion (192.168.3.100), all Ubuntu,
and pushes:

- `/etc/nut/nut.conf`: `MODE=netclient`.
- `/etc/nut/upsmon.conf` (root:nut 640): `MONITOR ups@192.168.3.20 1 <user> <password> secondary`,
  `SHUTDOWNCMD "/sbin/shutdown -h +0"`, `NOTIFYCMD /usr/sbin/upssched`, NOTIFYFLAG ONBATT/ONLINE/LOWBATT/FSD with
  SYSLOG+EXEC. Credentials from Vault `secret/nut` (`username`, `password`), never in git.
- Nodes only: `/etc/nut/upssched.conf`: `AT ONBATT * START-TIMER cluster-shutdown 600`,
  `AT ONLINE * CANCEL-TIMER cluster-shutdown`, `CMDSCRIPT /usr/local/sbin/dagalab-upssched`.
- Nodes only: `/usr/local/sbin/dagalab-upssched` (handler) and `/usr/local/lib/dagalab/lab-power.py`.

Low battery / FSD (the NAS shutting down) makes every client run SHUTDOWNCMD: the backstop.

### 2. Graceful cluster stop on battery (`scripts/lab-power.py`)

The lab-power-cycle script moves to `scripts/lab-power.py` (the skill calls it there) and gains a node-local mode
(`--local`: `/var/lib/rancher/rke2/bin/kubectl`, `KUBECONFIG=/etc/rancher/rke2/rke2.yaml`, etcd snapshot run
locally, no ssh; NFS release checked by waiting for the recorded workloads' pods to be gone, since every NFS user is
among them; state file path via `--state`).

Handler on the `cluster-shutdown` timer:

- dagakube01: `lab-power.py stop-workloads --local --state /var/lib/dagalab/ups-shutdown.json` (writes the state file,
  which doubles as the "automatic shutdown happened" marker), then `poweroff`. If the stop fails or times out (8 min),
  power off anyway.
- dagakube02/03: `lab-power.py wait-stopped --local --timeout 480` (waits until every Longhorn volume is detached, i.e.
  dagakube01 finished), then `poweroff`. Any kubectl error or the timeout: power off anyway. No ssh between nodes.
- Power returning after the timer fired does not abort the shutdown (the wake path brings the nodes back).

### 3. Automatic start (dagakube01, `dagalab-ups-autostart.service`)

Oneshot systemd unit, `After=network-online.target rke2-server.service`. Does nothing unless
`/var/lib/dagalab/ups-shutdown.json` exists (a manual skill power-down leaves no marker). If it exists:
`lab-power.py start --local --state …` (wait for NAS NFS, all nodes Ready, Longhorn CSI on every node, uncordon,
un-pause prometheus-operator + Argo CD, wait for the recorded workloads, clear failed Jobs), then remove the marker.

### 4. Wake-on-LAN

- Nodes: `wakeonlan: true` on `eno1` in the netplan file written by `scripts/host-config.sh`
  (`90-host-config.yaml`), applied with `netplan apply`. Today the NICs report `Wake-on: d`; after: `g`.
- BIOS (user, later): enable Wake-on-LAN / wake from S5 and set restore on AC loss to "stay off". If a board can't
  wake from S5 after an AC loss, fall back to "power on" (safe: workloads still wait for the NAS via the auto-start).
- Webserver: already `Wake-on: g`; it is on VLAN 4 (broadcast from the NAS won't reach it) and only shuts down at low
  battery, so its BIOS "restore on AC loss: power on" brings it back after a full outage.

### 5. NAS wake scripts (`nas/ups-wake.py`, copied to `/var/services/homes/jim/dagalab/` by `nut-setup.sh`)

Python 3 (DSM ships it), stdlib only; reads `ups.status` from `127.0.0.1:3493`; sends magic packets for the three
node MACs (`d8:bb:c1:2d:b1:95`, `00:d8:61:1d:af:7f`, `00:d8:61:47:65:37`) to 192.168.3.255:9. Two DSM Task Scheduler
tasks (created by the user in the DSM UI, run as `jim`):

- `ups-wake.py watch`, every minute: keeps a small state file. Records when it sees `OB`. After an `OB` has been seen
  and the status has been `OL` for 2 minutes, wakes any node that doesn't answer ping, for up to 30 minutes, then
  clears the state. With no outage seen, it never wakes anything.
- `ups-wake.py boot`, triggered at boot-up: waits until DSM's NFS (192.168.3.21:2049) answers, then wakes the nodes.
  Covers the full-outage case (everything lost power; with BIOS "stay off" the nodes wait for this).
- `--dry-run` (prints instead of sending) and `--status <OL|OB>` (simulates the UPS status) for testing.

### 6. Metrics, dashboard, alerts

- `argocd/apps/nut-exporter` (app-template 5.2.1, namespace `monitoring`): `ghcr.io/druggeri/nut_exporter` pinned,
  `NUT_EXPORTER_SERVER=192.168.3.20`, variables incl. `ups.load`, `ups.realpower.nominal`, `battery.charge`,
  `battery.runtime`, `input.voltage`, `ups.status`; ServiceMonitor on `/ups_metrics`. Pod traffic leaves via the node
  IPs, which are on the NAS allow list.
- `helm/charts/ups-monitoring` (like `media-monitoring`): the upstream nut_exporter Grafana dashboard plus a watts
  panel; PrometheusRule:
  - recording rule `ups:power_watts` = load × nominal / 100
  - `UPSOnBattery` (OB, 1 m, warning), `UPSClusterShutdownSoon` (OB for 8 m, critical: the cluster stops at 10),
    `UPSLowBattery` (LB flag or charge < 30 %, critical), `UPSRuntimeLow` (< 10 min, critical),
    `UPSHighLoad` (> 80 % for 10 m, warning), `UPSReplaceBattery` (RB flag, warning),
    `UPSMetricsMissing` (exporter down or no UPS data for 5 m, warning).
  Alerts go to Discord #alerts through the existing Alertmanager routing.
- Renovate: the exporter image tag (semver; versioning rule only if its tags need one).

### 7. Homepage

UPS tile (Infrastructure group, `prometheusmetric` widget on the in-cluster Prometheus): Power (W), Charge (%),
Runtime (min), On battery (0/1).

### 8. Home Assistant

`scripts/home-assistant-setup.py` adds the core NUT integration (host 192.168.3.20, port 3493; credentials only if HA
needs them). Sensors only; phone notifications are a later project (README TODO).

## Security

- NUT credentials (Synology's fixed `monuser` password) live in Vault `secret/nut`, rendered onto hosts by the setup
  script (mode 640), never committed.
- The only firewall opening is the UniFi rule 192.168.4.10 → 192.168.3.20 TCP 3493 (user-managed). Must sit above
  the DMZ → Internal block; as of 2026-10-21 all DMZ → NAS traffic still times out.
- The NAS scripts run as `jim`, need no root (magic packets are plain UDP broadcast).

## Docs

- README: "UPS / power" section (topology, thresholds, timings, the DSM tasks, BIOS settings, testing); TODO entries:
  BIOS settings, HA phone notifications, optional full outage test.
- CLAUDE.md: one line on the UPS / NUT setup and where the pieces live.
- lab-power-cycle skill: script path is `scripts/lab-power.py`; mention the automatic UPS path and its marker.

## Out of scope

Phone notifications (later), BIOS changes (user, later), PeaNUT, changing the NAS's own low-battery behaviour, UPS
control commands (Synology's NUT user is read-only).

## Testing / done when

1. `nut-setup.sh` is idempotent; `upsc ups@192.168.3.20` works on all 5 hosts (webserver once the UniFi rule
   matches); `upsmon` running as a secondary on each.
2. Node handler dry runs: `dagalab-upssched cluster-shutdown` with `DRY_RUN=1` prints the stop/wait and poweroff steps
   on each node; `lab-power.py stop-workloads --local --dry-run` on dagakube01 lists the same workloads as from the Mac.
3. Auto-start: with no marker the unit exits immediately; `start --local --dry-run` on dagakube01 passes its waits.
4. WoL: NICs report `Wake-on: g`; `ups-wake.py watch --dry-run --status OB` then `--status OL` (2 min later) prints
   magic packets only for nodes that don't answer ping (none today); `boot --dry-run` waits for NFS then prints.
5. Metrics in Prometheus; Grafana dashboard renders with watts; `ups:power_watts` ~120 W today.
6. Homepage UPS tile shows values (Playwright screenshot).
7. HA has NUT sensors (`sensor.ups_*`) with plausible values.
8. Unplug test (user, ~2 min): `UPSOnBattery` fires to Discord, Grafana/Homepage show on battery, node syslogs show
   the timer starting then cancelled on replug, the NAS watcher records OB→OL and wakes nothing (nodes up).
