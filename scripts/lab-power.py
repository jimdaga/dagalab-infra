#!/usr/bin/env python3
"""Power-cycle the dagalab cluster safely (see ../SKILL.md for when and why).

  lab-power.py precheck                                  read-only checks
  lab-power.py stop-workloads [--dry-run] [--local] [--state F]   etcd snapshot, pause Argo + operator, scale storage
                                                         workloads to 0, wait for Longhorn + NFS to let go
  lab-power.py wait-stopped --local [--timeout S]        (dagakube02/03, UPS) wait until every Longhorn volume is detached
  lab-power.py poweroff [--dry-run]                      (Mac only) cordon, power the nodes off, wait for ping to stop
  lab-power.py start [--dry-run] [--local] [--state F]   NAS NFS, nodes Ready, Longhorn CSI, uncordon, un-pause, wait
  lab-power.py verify                                    read-only health check
--local: run on a node as root (RKE2's kubectl + kubeconfig, etcd snapshot locally, no ssh). Used by the UPS
automation (hosts/nut/). Default state file: ~/.local/state/dagalab/lab-power.json (Mac); the UPS path uses
/var/lib/dagalab/ups-shutdown.json, which is also the auto-start marker.

State (the workloads that were scaled down) is kept in ~/.local/state/dagalab/lab-power.json so `start` works from a
new session. Needs kubectl (context dagalab) and passwordless ssh+sudo to jim@192.168.3.11-13.
"""
import json, os, pathlib, socket, subprocess, sys, time

NODES = {'dagakube01': '192.168.3.11', 'dagakube02': '192.168.3.12', 'dagakube03': '192.168.3.13'}
POWEROFF_ORDER = ['dagakube03', 'dagakube02', 'dagakube01']   # 01 last: it holds the fresh etcd snapshot
SNAPSHOT_NODE = 'dagakube01'
SKIP_NS = {'kube-system', 'longhorn-system'}
PAUSE = [('argocd', 'statefulset', 'argocd-application-controller'),          # else selfHeal scales everything back
         ('monitoring', 'deployment', 'kube-prometheus-stack-operator')]      # else it recreates prometheus/alertmanager
NAS_NFS = ('192.168.3.21', 2049)
ENDPOINTS = ['https://popcorn.dagaflix.com/health', 'https://requests.dagaflix.com/api/v1/status',
             'https://books.dagaflix.com/healthcheck', 'https://jellyfin.lab.internal/health',
             'https://plex.lab.internal/identity', 'https://home.lab.internal/', 'https://homepage.lab.internal/',
             'https://argocd.lab.internal/', 'http://192.168.3.202:32400/identity']

DRY, LOCAL = False, False
STATE = pathlib.Path.home() / '.local/state/dagalab/lab-power.json'
TIMEOUT = 480


def kubectl_cmd(local):
    if os.environ.get('LAB_POWER_KUBECTL'):
        return os.environ['LAB_POWER_KUBECTL'].split()
    # on a node during a UPS shutdown the apiserver may accept connections and never answer: bound every call
    return (['/var/lib/rancher/rke2/bin/kubectl', '--kubeconfig', '/etc/rancher/rke2/rke2.yaml', '--request-timeout=20s']
            if local else ['kubectl'])


def log(msg): print(msg, flush=True)


def sh(args, check=True, capture=True):
    r = subprocess.run(args, capture_output=capture, text=True)
    if check and r.returncode:
        raise SystemExit(f'FAILED: {" ".join(args)}\n{r.stderr.strip()}')
    return r.stdout if capture else ''


def kj(*args):
    return json.loads(sh([*kubectl_cmd(LOCAL), *args, '-o', 'json']))


def act(desc, args):
    """Run a mutating command, or only print it with --dry-run."""
    if DRY:
        log(f'  [dry-run] {desc}: {" ".join(args)}')
        return ''
    log(f'  {desc}')
    return sh(args)


def ssh(node, cmd, check=True):
    return sh(['ssh', '-o', 'ConnectTimeout=5', '-o', 'BatchMode=yes', f'jim@{NODES[node]}', cmd], check=check)


def wait(desc, pred, timeout, every=10):
    log(f'  waiting: {desc}')
    end = time.time() + timeout
    while True:
        ok, detail = pred()
        if ok:
            log(f'  ok: {desc}')
            return
        if time.time() > end:
            raise SystemExit(f'TIMEOUT ({timeout}s): {desc} -- {detail}')
        time.sleep(every)


def volumes():
    return [(v['metadata']['name'], v.get('status', {}).get('state'), v.get('status', {}).get('robustness'))
            for v in kj('-n', 'longhorn-system', 'get', 'volumes.longhorn.io')['items']]


def nodes_ready():
    out = {}
    for n in kj('get', 'nodes')['items']:
        cond = {c['type']: c['status'] for c in n['status']['conditions']}
        out[n['metadata']['name']] = (cond.get('Ready') == 'True', n['spec'].get('unschedulable', False))
    return out


def volume_workloads():
    """Deployments/StatefulSets whose pods mount a PVC (Longhorn or NFS), outside system namespaces."""
    found = []
    for d in kj('get', 'deploy,statefulset', '-A')['items']:
        ns = d['metadata']['namespace']
        if ns in SKIP_NS:
            continue
        vols = d['spec']['template']['spec'].get('volumes') or []
        if any('persistentVolumeClaim' in v for v in vols) or d['spec'].get('volumeClaimTemplates'):
            found.append({'kind': d['kind'].lower(), 'ns': ns, 'name': d['metadata']['name'],
                          'replicas': d['spec'].get('replicas', 1)})
    return found


def nfs_mounts():
    return {n: int(ssh(n, 'mount -t nfs4 | wc -l', check=False).strip() or -1) for n in NODES}


def nfs_claim_pods():
    """Pods that mount a PVC backed by an NFS PV (media-data, audiobooks, podcasts, trickplay)."""
    nfs_pvs = {p['metadata']['name'] for p in kj('get', 'pv')['items'] if 'nfs' in p['spec']}
    claims = {(c['metadata']['namespace'], c['metadata']['name']) for c in kj('get', 'pvc', '-A')['items']
              if c['spec'].get('volumeName') in nfs_pvs}
    return [f'{p["metadata"]["namespace"]}/{p["metadata"]["name"]}' for p in kj('get', 'pods', '-A')['items']
            if any((p['metadata']['namespace'], v['persistentVolumeClaim']['claimName']) in claims
                   for v in p['spec'].get('volumes') or [] if 'persistentVolumeClaim' in v)]


def wait_stopped(timeout, every=10):
    """True once every Longhorn volume is detached; False on timeout or if the API can't be reached."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            if all(v[1] == 'detached' for v in volumes()):
                return True
        except SystemExit:
            pass
        time.sleep(every)
    return False


def pingable(ip):
    return subprocess.run(['ping', '-c1', '-t2', ip], capture_output=True).returncode == 0


# ---------------------------------------------------------------- commands

def precheck():
    bad = 0
    for n, (ready, cordoned) in nodes_ready().items():
        log(f'node {n}: {"Ready" if ready else "NOT READY"}{" (cordoned)" if cordoned else ""}')
        bad += not ready
    vols = volumes()
    attached_bad = [v for v in vols if v[1] == 'attached' and v[2] != 'healthy']
    log(f'longhorn: {len(vols)} volumes, {sum(v[1] == "attached" for v in vols)} attached, '
        f'{len(attached_bad)} attached-but-not-healthy {[v[0] for v in attached_bad]}')
    bad += len(attached_bad)
    broken = [f'{p["metadata"]["namespace"]}/{p["metadata"]["name"]}' for p in kj('get', 'pods', '-A')['items']
              if p['status'].get('phase') not in ('Running', 'Succeeded')]
    log(f'pods not Running/Succeeded: {broken or "none"}')
    apps = [f'{a["metadata"]["name"]}={a.get("status", {}).get("sync", {}).get("status")}/'
            f'{a.get("status", {}).get("health", {}).get("status")}' for a in kj('-n', 'argocd', 'get', 'app')['items']]
    not_ok = [a for a in apps if not a.endswith('=Synced/Healthy')]
    log(f'argo apps not Synced/Healthy: {not_ok or "none"}')
    log('PRECHECK ' + ('OK' if not bad else f'FOUND {bad} PROBLEM(S): fix or explain before shutting down'))
    return bad


def stop_workloads():
    log('1. etcd snapshot')
    name = f'pre-power-{time.strftime("%Y%m%d-%H%M")}'
    if LOCAL:
        cmd = ['/usr/local/bin/rke2', 'etcd-snapshot', 'save', '--name', name]
        if DRY:
            log(f'  [dry-run] {" ".join(cmd)}')
        else:
            log('  ' + ((sh(cmd, check=False).strip().splitlines() or [''])[-1]))
    elif DRY:
        log(f'  [dry-run] ssh {SNAPSHOT_NODE} sudo rke2 etcd-snapshot save --name {name}')
    else:
        out = ssh(SNAPSHOT_NODE, f'sudo rke2 etcd-snapshot save --name {name} 2>&1 | tail -1')
        log(f'  {out.strip()}')
    # record first: the state file is also the auto-start marker, so it must exist before anything is paused,
    # or a failure mid-pause would leave Argo at 0 replicas after the reboot with nothing to un-pause it
    log('2. record the workloads with volumes')
    wl = volume_workloads()
    if not DRY:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({'saved': time.strftime('%Y-%m-%dT%H:%M:%S'), 'workloads': wl}, indent=1))
        log(f'  recorded {len(wl)} workloads in {STATE}')
    log('3. pause Argo CD + prometheus-operator, scale the workloads to 0')
    for ns, kind, n in PAUSE:
        act(f'scale {ns}/{n} to 0', [*kubectl_cmd(LOCAL), '-n', ns, 'scale', kind, n, '--replicas=0'])
    for w in wl:
        act(f'scale {w["ns"]}/{w["name"]} ({w["replicas"]} -> 0)',
            [*kubectl_cmd(LOCAL), '-n', w['ns'], 'scale', w['kind'], w['name'], '--replicas=0'])
    if DRY:
        log(f'  [dry-run] would wait for all {len(volumes())} Longhorn volumes to detach and NFS mounts to reach 0')
        return
    log('4. wait for Longhorn + NFS to let go')
    wait('every Longhorn volume detached',
         lambda: (all(v[1] == 'detached' for v in volumes()), [v[0] for v in volumes() if v[1] != 'detached']), 900)
    if LOCAL:
        wait('no pods using NFS claims', lambda: (not nfs_claim_pods(), nfs_claim_pods()), 300)
    else:
        wait('no NFS mounts on any node', lambda: (all(c == 0 for c in nfs_mounts().values()), nfs_mounts()), 300)
    log('STOPPED: safe to run `poweroff` (the NAS is already free)')


def poweroff():
    if LOCAL:
        raise SystemExit('poweroff is Mac-only; on a node the UPS automation powers itself off')
    if not DRY:
        busy = [v[0] for v in volumes() if v[1] != 'detached']
        if busy:
            raise SystemExit(f'refusing: Longhorn volumes still attached {busy}; run stop-workloads first')
    act('cordon all nodes', [*kubectl_cmd(LOCAL), 'cordon', *NODES])
    for n in POWEROFF_ORDER:
        # delayed poweroff so the ssh command returns instead of hanging on the dying connection
        cmd = 'sudo systemd-run --on-active=3 /bin/systemctl poweroff'
        if DRY:
            log(f'  [dry-run] ssh {n} {cmd}')
            continue
        log(f'  powering off {n}')
        ssh(n, cmd, check=False)
        time.sleep(20)
    if DRY:
        return
    # sshd stops early (connection refused) while the node still answers ping; wait for ping to stop
    wait('all nodes off the network', lambda: (not any(pingable(ip) for ip in NODES.values()),
                                              [n for n, ip in NODES.items() if pingable(ip)]), 600)
    log('OFF: the NAS can be shut down now')


def start():
    log('0. NAS NFS reachable')
    def nfs_open():
        try:
            socket.create_connection(NAS_NFS, timeout=3).close()
            return True, ''
        except OSError as e:
            return False, str(e)
    wait('NAS NFS port open', nfs_open, 1800)
    log('1. nodes Ready (still cordoned)')

    def all_ready():
        try:
            st = nodes_ready()
        except SystemExit as e:
            return False, 'API not answering yet'
        return len(st) == len(NODES) and all(r for r, _ in st.values()), st
    wait('all nodes Ready', all_ready, 1200, every=15)
    log('2. Longhorn CSI registered on every node (pods mounting before this fail with "driver.longhorn.io not found")')

    def csi_ready():
        pods = [p for p in kj('-n', 'longhorn-system', 'get', 'pods', '-l', 'app=longhorn-csi-plugin')['items']]
        ok = [p['spec'].get('nodeName') for p in pods
              if all(c.get('ready') for c in p['status'].get('containerStatuses', []))]
        return set(ok) >= set(NODES), sorted(ok)
    wait('longhorn-csi-plugin ready on all nodes', csi_ready, 900)
    log('3. uncordon + un-pause')
    act('uncordon all nodes', [*kubectl_cmd(LOCAL), 'uncordon', *NODES])
    for ns, kind, n in reversed(PAUSE):
        act(f'scale {ns}/{n} to 1', [*kubectl_cmd(LOCAL), '-n', ns, 'scale', kind, n, '--replicas=1'])
    if DRY:
        return
    log('4. Argo restores every workload to its git replica count')
    wl = json.loads(STATE.read_text())['workloads'] if STATE.exists() else []

    def restored():
        waiting = []
        for w in wl:
            r = subprocess.run([*kubectl_cmd(LOCAL), '-n', w['ns'], 'get', w['kind'], w['name'], '-o', 'json'],
                               capture_output=True, text=True)
            if r.returncode:
                waiting.append(w['name']); continue
            s = json.loads(r.stdout)
            ready = s.get('status', {}).get('readyReplicas') or 0
            if ready < w['replicas']:
                waiting.append(f'{w["name"]}({ready}/{w["replicas"]})')
        return not waiting, waiting
    wait(f'{len(wl)} recorded workloads ready', restored, 1800, every=20)
    log('5. clear Jobs that failed while their services were still down (they make their Argo app Degraded)')
    for j in kj('get', 'jobs', '-A')['items']:
        if j.get('status', {}).get('failed'):
            act(f'delete failed job {j["metadata"]["namespace"]}/{j["metadata"]["name"]}',
                [*kubectl_cmd(LOCAL), '-n', j['metadata']['namespace'], 'delete', 'job', j['metadata']['name']])
    if STATE.exists():
        STATE.unlink()
    log('STARTED: run `verify`')


def verify():
    bad = precheck()
    for i in range(3):
        r = subprocess.run([*kubectl_cmd(LOCAL), '-n', 'vault', 'exec', f'vault-{i}', '--', 'vault', 'status', '-format=json'],
                           capture_output=True, text=True)
        sealed = json.loads(r.stdout)['sealed'] if r.stdout.strip().startswith('{') else 'unknown'
        log(f'vault-{i} sealed: {sealed}')
        bad += sealed is not False
    es = [f'{e["metadata"]["namespace"]}/{e["metadata"]["name"]}' for e in kj('get', 'externalsecret', '-A')['items']
          if (e.get('status', {}).get('conditions') or [{}])[0].get('reason') != 'SecretSynced']
    log(f'ExternalSecrets not synced: {es or "none"}')
    bad += len(es)
    log(f'NFS mounts per node: {nfs_mounts()}')
    for u in ENDPOINTS:
        code = subprocess.run(['curl', '-sk', '-m', '10', '-o', '/dev/null', '-w', '%{http_code}', u],
                              capture_output=True, text=True).stdout
        log(f'{code} {u}')
        bad += code != '200'
    am = sh([*kubectl_cmd(LOCAL), '-n', 'monitoring', 'get', 'pods', '-o', 'name'], check=False).split()
    am = next((p for p in am if 'alertmanager-kube' in p), None)
    if am:
        raw = sh([*kubectl_cmd(LOCAL), '-n', 'monitoring', 'exec', am, '-c', 'alertmanager', '--', 'wget', '-qO-',
                  'http://localhost:9093/api/v2/alerts?active=true'], check=False)
        firing = [a['labels'].get('alertname') for a in json.loads(raw or '[]')
                  if a['labels'].get('alertname') not in ('Watchdog', 'InfoInhibitor')]
        log(f'firing alerts: {firing or "none"}')
    log('VERIFY ' + ('OK' if not bad else f'FOUND {bad} PROBLEM(S)'))


def wait_stopped_cmd():
    ok = wait_stopped(TIMEOUT)
    log('STOPPED' if ok else 'TIMEOUT: volumes still attached or API unreachable')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=['precheck', 'stop-workloads', 'wait-stopped', 'poweroff', 'start', 'verify'])
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--local', action='store_true')
    p.add_argument('--state', type=pathlib.Path)
    p.add_argument('--timeout', type=int, default=480)
    a = p.parse_args()
    DRY, LOCAL, TIMEOUT = a.dry_run, a.local, a.timeout
    if a.state:
        STATE = a.state
    {'precheck': precheck, 'stop-workloads': stop_workloads, 'wait-stopped': wait_stopped_cmd, 'poweroff': poweroff,
     'start': start, 'verify': verify}[a.command]()
