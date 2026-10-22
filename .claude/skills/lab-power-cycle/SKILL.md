---
name: lab-power-cycle
description: Safely power down and power back up the whole dagalab homelab (the 3 RKE2 nodes dagakube01-03 and the Synology NAS daga-nas) for maintenance such as a UPS install, electrical work, moving hardware, a planned power cut, or a full cold restart. Use this whenever the user wants to shut down, power off, turn off, reboot all of, or bring back up the cluster/lab/nodes/NAS, or asks to "drain the nodes" for maintenance, even if they only mention one step (e.g. "help me shut the lab down" or "things are powered back on"). Not for rebooting a single node or upgrading RKE2.
---

# Power-cycle the dagalab homelab

A full power-down has to protect three things: Longhorn volumes (replicated block storage; a volume still attached when its
node dies can come back faulted), the NAS's NFS exports (media apps hold them open), and etcd (needs 2 of 3 nodes for quorum).
The safe order is: stop every workload that holds storage, prove the storage is released, power off the nodes, and only then
the NAS. Power-up is the reverse: NAS first, then nodes, then let Argo CD put everything back.

Draining node by node does not work for a FULL shutdown: pods just pile onto the remaining nodes, and the last drain is
blocked by Longhorn's PodDisruptionBudgets. Instead the script pauses Argo CD and scales workloads to zero, so Argo's git
state is the single source of truth for bringing them back.

`scripts/lab-power.py` does the mechanical parts. The same script runs on dagakube01 (`--local`) for the automatic UPS shutdown/start (README "UPS / power"). Every mutating step supports `--dry-run`; run it first and show the user
what will happen. Run it from the repo root with kubectl context `dagalab`.

## Power down

1. **Precheck** (read-only): `python3 scripts/lab-power.py precheck`
   Nodes Ready, no attached Longhorn volume unhealthy, no broken pods, Argo apps listed if not Synced/Healthy. Going down
   with a degraded volume or a broken app makes the power-up harder to judge, so point problems out first.
2. **Confirm with the user**, then **stop workloads**: `... stop-workloads` (after `--dry-run`)
   - Saves an etcd snapshot on dagakube01 (`/var/lib/rancher/rke2/server/db/snapshots/pre-power-<date>`).
   - Scales `argocd-application-controller` and `kube-prometheus-stack-operator` to 0. Argo's selfHeal would otherwise
     scale everything straight back up, and the prometheus-operator recreates Prometheus/Alertmanager.
   - Records every Deployment/StatefulSet with a PVC to `~/.local/state/dagalab/lab-power.json` and scales it to 0.
   - Waits until every Longhorn volume is `detached` and no node has an NFS mount. When it reports STOPPED, the NAS is
     already free.
3. **Power off the nodes**: `... poweroff`. Cordons all nodes, powers them off one by one (dagakube01 last, it holds the
   snapshot) with a delayed `systemctl poweroff` so the ssh call returns. sshd dies before the network does, so it waits
   until no node answers ping. This takes a few minutes.
4. Tell the user it's safe to **shut down the NAS** (DSM). That step is theirs.

## Power up

When the user says things are back on:

1. Make sure the **NAS is up first**: media pods need its NFS exports. `start` waits for the NFS port anyway.
2. `... start`:
   - Waits for the API and all nodes Ready (they come back cordoned, because cordon survives a reboot).
   - Waits until `longhorn-csi-plugin` is ready on every node. Pods scheduled before that sit in ContainerCreating with
     `driver name driver.longhorn.io not found in the list of registered CSI drivers` and only recover on kubelet's
     backoff (it happened on dagakube02 the first time).
   - Uncordons, un-pauses the operator and Argo; Argo restores every workload to its git replica count.
   - Waits for the recorded workloads to be ready (about 10 to 15 minutes), deletes Jobs that failed while their
     service was still down.
3. `... verify`: nodes, Longhorn, pods, Argo apps, Vault (auto-unseals via AWS KMS, should be sealed=False on all 3),
   ExternalSecrets, NFS mounts back, public + LAN endpoints, Plex direct, firing alerts.

## Known leftovers after power-up (expected, explain rather than "fix")

- An Argo app with a CronJob (e.g. `bookshelf`, its hourly `seed-cleanup`) can stay **Degraded** until the CronJob's
  next successful run: Argo judges CronJob health by last schedule vs last success, so deleting the failed Job isn't
  enough. `root` mirrors it as Degraded. Both clear on their own.
- Volumes that are `detached`/`unknown` and not used by any app are normal.
- If the lab went down by itself (UPS), dagakube01 starts it again automatically when
  `/var/lib/dagalab/ups-shutdown.json` exists; check `journalctl -u dagalab-ups-autostart` before running `start` by hand.

## If something goes wrong

- A step times out: the script prints what it was still waiting for (volume names, node names, workloads). Look at
  that object (`kubectl describe`, events) before retrying; never skip ahead to powering off with volumes still attached
  (`poweroff` refuses anyway).
- etcd won't form quorum on power-up: at least 2 of the 3 nodes must be up. A snapshot is on dagakube01.
- `root` stuck OutOfSync on an Application after a controller restart: compare the live Application with git. Argo CD
  3.5 adds `pre-delete-finalizer.argocd.argoproj.io` (+`/cleanup`) to apps whose chart has PreDelete hooks (Longhorn);
  those are declared in `argocd/apps/longhorn/application.yaml` so root doesn't fight the controller.
- Shell notes: macOS has no `timeout`; don't parse `kubectl get pods -o wide` with awk columns (RESTARTS like
  `2 (5m ago)` shift them), use jsonpath.
