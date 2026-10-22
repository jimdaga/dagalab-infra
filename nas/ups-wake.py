#!/usr/bin/env python3
"""Wake the k8s nodes after a power outage. Runs on daga-nas (DSM Task Scheduler, user jim); stdlib only.

  ups-wake.py watch [--dry-run] [--status "OL CHRG"] [--state FILE]     every minute
  ups-wake.py boot  [--dry-run]                                          at DSM boot-up

watch: acts only after it has SEEN the UPS on battery (OB). Once mains (OL) has been back for 2 minutes it wakes every
node that doesn't answer (ssh/6443), for up to 30 minutes, then forgets the outage. No outage seen -> never wakes
anything, so a manual maintenance shutdown is left alone.
boot: the NAS itself lost power (the battery ran out). Wait until NFS answers, then wake the nodes. With node BIOS
"restore on AC loss: stay off" this is what starts them, after the NAS is fully up.
"""
import argparse, json, os, socket, sys, time

NODES = {'dagakube01': ('192.168.3.11', 'd8:bb:c1:2d:b1:95'),
         'dagakube02': ('192.168.3.12', '00:d8:61:1d:af:7f'),
         'dagakube03': ('192.168.3.13', '00:d8:61:47:65:37')}
BROADCAST = ('192.168.3.255', 9)
NUT = ('127.0.0.1', 3493)
NFS = ('192.168.3.21', 2049)
STATE = os.path.expanduser('~/dagalab/ups-wake-state.json')
SETTLE, WINDOW = 120, 1800


def log(msg): print(time.strftime('%Y-%m-%d %H:%M:%S'), msg, flush=True)


def ups_status():
    with socket.create_connection(NUT, timeout=5) as s:
        s.sendall(b'GET VAR ups ups.status\n')
        line = s.makefile().readline()          # VAR ups ups.status "OL CHRG"
    if not line.startswith('VAR '):
        raise RuntimeError(line.strip())
    return line.split('"')[1].split()


def step(state, flags, now, is_up):
    """Pure decision. state: {} or {'outage': ts, 'online_since': ts|None}. Returns (new_state, node names to wake)."""
    state = dict(state or {})
    if 'OB' in flags:
        state.setdefault('outage', now)
        state['online_since'] = None
        return state, []
    if 'OL' not in flags or 'outage' not in state:
        return state, []
    if not state.get('online_since'):
        state['online_since'] = now
    since = now - state['online_since']
    if since >= SETTLE + WINDOW:
        return {}, []
    if since < SETTLE:
        return state, []
    return state, [n for n, (ip, _) in NODES.items() if not is_up(ip)]


def is_up(ip):
    for port in (22, 6443):
        try:
            socket.create_connection((ip, port), timeout=1).close()
            return True
        except OSError:
            pass
    return False


def magic(mac):
    return b'\xff' * 6 + bytes.fromhex(mac.replace(':', '')) * 16


def wake(names, dry):
    for n in names:
        mac = NODES[n][1]
        if dry:
            log(f'[dry-run] wake {n} {mac}')
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.sendto(magic(mac), BROADCAST)
        log(f'woke {n} {mac}')


def watch(a):
    try:
        state = json.load(open(a.state))
    except (OSError, ValueError):
        state = {}
    flags = a.status.split() if a.status else ups_status()
    new, names = step(state, flags, time.time(), is_up)
    if new != state:
        log(f'status {" ".join(flags)}: state {new or "cleared"}')
    wake(names, a.dry_run)
    if not a.dry_run:
        os.makedirs(os.path.dirname(a.state), exist_ok=True)
        json.dump(new, open(a.state, 'w'))


def boot(a):
    end = time.time() + 1200
    while time.time() < end:
        try:
            socket.create_connection(NFS, timeout=3).close()
            break
        except OSError:
            time.sleep(10)
    else:
        log('NFS not up after 20 min: waking the nodes anyway')
    wake([n for n, (ip, _) in NODES.items() if not is_up(ip)], a.dry_run)
    if not a.dry_run and os.path.exists(a.state):
        os.remove(a.state)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('mode', choices=['watch', 'boot'])
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--status', help='simulate ups.status flags, e.g. "OB DISCHRG"')
    p.add_argument('--state', default=STATE)
    a = p.parse_args()
    {'watch': watch, 'boot': boot}[a.mode](a)
