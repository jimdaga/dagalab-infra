#!/usr/bin/env python3
"""Power-cycle the dagalab cluster safely (see ../SKILL.md for when and why).

  lab_power.py precheck                     read-only: nodes, Longhorn, broken pods, Argo apps
  lab_power.py stop-workloads [--dry-run]   etcd snapshot, pause Argo + prometheus-operator, scale volume workloads
                                            to 0, wait for every Longhorn volume to detach and every NFS mount to go
  lab_power.py poweroff [--dry-run]         cordon all nodes, power them off one by one, wait until none answers ping
  lab_power.py start                        after power-on: wait for nodes + Longhorn CSI, uncordon, un-pause,
                                            wait for the recorded workloads, clear Jobs that failed during startup
  lab_power.py verify                       read-only health check after power-up

State (the workloads that were scaled down) is kept in ~/.local/state/dagalab/lab-power.json so `start` works from a
new session. Needs kubectl (context dagalab) and passwordless ssh+sudo to jim@192.168.3.11-13.
"""
import json, os, pathlib, subprocess, sys, time

NODES = {'dagakube01': '192.168.3.11', 'dagakube02': '192.168.3.12', 'dagakube03': '192.168.3.13'}
POWEROFF_ORDER = ['dagakube03', 'dagakube02', 'dagakube01']   # 01 last: it holds the fresh etcd snapshot
SNAPSHOT_NODE = 'dagakube01'
SKIP_NS = {'kube-system', 'longhorn-system'}
PAUSE = [('argocd', 'statefulset', 'argocd-application-controller'),          # else selfHeal scales everything back
         ('monitoring', 'deployment', 'kube-prometheus-stack-operator')]      # else it recreates prometheus/alertmanager
NAS_NFS = ('192.168.3.21', 2049)
STATE = pathlib.Path.home() / '.local/state/dagalab/lab-power.json'
ENDPOINTS = ['https://popcorn.dagaflix.com/health', 'https://requests.dagaflix.com/api/v1/status',
             'https://books.dagaflix.com/healthcheck', 'https://jellyfin.lab.internal/health',
             'https://plex.lab.internal/identity', 'https://home.lab.internal/', 'https://homepage.lab.internal/',
             'https://argocd.lab.internal/', 'http://192.168.3.202:32400/identity']

DRY = '--dry-run' in sys.argv


def log(msg): print(msg, flush=True)


def sh(args, check=True, capture=True):
    r = subprocess.run(args, capture_output=capture, text=True)
    if check and r.returncode:
        raise SystemExit(f'FAILED: {" ".join(args)}\n{r.stderr.strip()}')
    return r.stdout if capture else ''


def kj(*args):
    return json.loads(sh(['kubectl', *args, '-o', 'json']))


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
    if DRY:
        log(f'  [dry-run] ssh {SNAPSHOT_NODE} sudo rke2 etcd-snapshot save --name {name}')
    else:
        out = ssh(SNAPSHOT_NODE, f'sudo rke2 etcd-snapshot save --name {name} 2>&1 | tail -1')
        log(f'  {out.strip()}')
    log('2. pause Argo CD + prometheus-operator')
    for ns, kind, n in PAUSE:
        act(f'scale {ns}/{n} to 0', ['kubectl', '-n', ns, 'scale', kind, n, '--replicas=0'])
    log('3. record + scale workloads with volumes to 0')
    wl = volume_workloads()
    if not DRY:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({'saved': time.strftime('%Y-%m-%dT%H:%M:%S'), 'workloads': wl}, indent=1))
        log(f'  recorded {len(wl)} workloads in {STATE}')
    for w in wl:
        act(f'scale {w["ns"]}/{w["name"]} ({w["replicas"]} -> 0)',
            ['kubectl', '-n', w['ns'], 'scale', w['kind'], w['name'], '--replicas=0'])
    if DRY:
        log(f'  [dry-run] would wait for all {len(volumes())} Longhorn volumes to detach and NFS mounts to reach 0')
        return
    log('4. wait for Longhorn + NFS to let go')
    wait('every Longhorn volume detached',
         lambda: (all(v[1] == 'detached' for v in volumes()), [v[0] for v in volumes() if v[1] != 'detached']), 900)
    wait('no NFS mounts on any node', lambda: (all(c == 0 for c in nfs_mounts().values()), nfs_mounts()), 300)
    log('STOPPED: safe to run `poweroff` (the NAS is already free)')


def poweroff():
    if not DRY:
        busy = [v[0] for v in volumes() if v[1] != 'detached']
        if busy:
            raise SystemExit(f'refusing: Longhorn volumes still attached {busy}; run stop-workloads first')
    act('cordon all nodes', ['kubectl', 'cordon', *NODES])
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
    wait('NAS NFS port open', lambda: (subprocess.run(['nc', '-z', '-G', '3', *map(str, NAS_NFS)],
                                                      capture_output=True).returncode == 0, 'closed'), 900)
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
    act('uncordon all nodes', ['kubectl', 'uncordon', *NODES])
    for ns, kind, n in reversed(PAUSE):
        act(f'scale {ns}/{n} to 1', ['kubectl', '-n', ns, 'scale', kind, n, '--replicas=1'])
    if DRY:
        return
    log('4. Argo restores every workload to its git replica count')
    wl = json.loads(STATE.read_text())['workloads'] if STATE.exists() else []

    def restored():
        waiting = []
        for w in wl:
            r = subprocess.run(['kubectl', '-n', w['ns'], 'get', w['kind'], w['name'], '-o', 'json'],
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
                ['kubectl', '-n', j['metadata']['namespace'], 'delete', 'job', j['metadata']['name']])
    if STATE.exists():
        STATE.unlink()
    log('STARTED: run `verify`')


def verify():
    bad = precheck()
    for i in range(3):
        r = subprocess.run(['kubectl', '-n', 'vault', 'exec', f'vault-{i}', '--', 'vault', 'status', '-format=json'],
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
    am = sh(['kubectl', '-n', 'monitoring', 'get', 'pods', '-o', 'name'], check=False).split()
    am = next((p for p in am if 'alertmanager-kube' in p), None)
    if am:
        raw = sh(['kubectl', '-n', 'monitoring', 'exec', am, '-c', 'alertmanager', '--', 'wget', '-qO-',
                  'http://localhost:9093/api/v2/alerts?active=true'], check=False)
        firing = [a['labels'].get('alertname') for a in json.loads(raw or '[]')
                  if a['labels'].get('alertname') not in ('Watchdog', 'InfoInhibitor')]
        log(f'firing alerts: {firing or "none"}')
    log('VERIFY ' + ('OK' if not bad else f'FOUND {bad} PROBLEM(S)'))


if __name__ == '__main__':
    cmds = {'precheck': precheck, 'stop-workloads': stop_workloads, 'poweroff': poweroff, 'start': start, 'verify': verify}
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if len(args) != 1 or args[0] not in cmds:
        raise SystemExit(__doc__)
    cmds[args[0]]()
