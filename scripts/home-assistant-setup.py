#!/usr/bin/env python3
"""Idempotent Home Assistant setup over its API (port-forwards: HA on 127.0.0.1:18123, Jellyfin on 127.0.0.1:18096).

Env: HA_USER/HA_PASSWORD (admin to create/log in as), HA_TOKEN (long-lived token from Vault; may be empty or stale),
JF_ADMIN_KEY (Jellyfin API key "arr"), JF_HA_PASSWORD (password of the Jellyfin user `homeassistant`),
UNIFI_PASSWORD/SYNOLOGY_PASSWORD (the `homepage` device accounts), SONARR_KEY, RADARR_KEY, CA_FILE (lab CA, to check
https://home.lab.internal through the LAN gateway).
Prints HA_TOKEN=<token> on stdout only when it creates a new long-lived token (the caller stores it in Vault, even
when this exits non-zero); everything else goes to stderr and never includes secrets. Exits 1 if an integration failed.
"""
import base64, ipaddress, json, os, socket, ssl, struct, sys, time, urllib.error, urllib.parse, urllib.request

HA, JF = 'http://127.0.0.1:18123', 'http://127.0.0.1:18096'
CLIENT = HA + '/'           # OAuth client id for our own logins (any URL on the same host)
TOKEN_NAME = 'dagalab'      # the long-lived token's name in HA (Profile -> Security)
JF_USER = 'homeassistant'   # Jellyfin user the HA Jellyfin integration signs in as
SVC = 'media.svc.cluster.local'
LAN_URL = 'https://home.lab.internal'
# the LAN gateway (Envoy) connects from the pod network; flannel may SNAT pod -> hostNetwork traffic to the node IP
TRUSTED_PROXIES = ['10.42.0.0/16', '192.168.3.11/32', '192.168.3.12/32', '192.168.3.13/32']
USER, PW, JF_PW = os.environ['HA_USER'], os.environ['HA_PASSWORD'], os.environ['JF_HA_PASSWORD']

def log(*a): print(*a, file=sys.stderr, flush=True)

def call(method, url, body=None, token=None, form=None, headers=None, ctx=None):
    h, data = dict(headers or {}), None
    if form is not None:
        data, h['Content-Type'] = urllib.parse.urlencode(form).encode(), 'application/x-www-form-urlencoded'
    elif body is not None:
        data, h['Content-Type'] = json.dumps(body).encode(), 'application/json'
    if token:
        h['Authorization'] = f'Bearer {token}'
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method=method, headers=h, data=data), timeout=120, context=ctx) as r:
            raw = r.read(); return r.status, (json.loads(raw) if raw[:1] in (b'{', b'[') else raw.decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:   # HA restarting, port-forward reconnecting
        return 0, str(e)

class WS:
    """Minimal websocket client for HA's /api/websocket (stdlib only; small text frames, client frames masked)."""
    def __init__(self, token):
        self.s, self.buf, self.n = socket.create_connection(('127.0.0.1', 18123), timeout=60), b'', 0
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall(('GET /api/websocket HTTP/1.1\r\nHost: 127.0.0.1:18123\r\nUpgrade: websocket\r\n'
                        f'Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n').encode())
        while b'\r\n\r\n' not in self.buf:
            self.buf += self.s.recv(4096)
        head, self.buf = self.buf.split(b'\r\n\r\n', 1)
        if b' 101 ' not in head.split(b'\r\n', 1)[0]:
            sys.exit(f'websocket upgrade failed: {head.splitlines()[0]!r}')
        self.recv()                                    # auth_required
        self.send({'type': 'auth', 'access_token': token})
        if self.recv().get('type') != 'auth_ok':
            sys.exit('websocket auth failed')
    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.s.recv(65536)
            if not chunk: sys.exit('websocket closed by HA')
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out
    def recv(self):
        data = b''
        while True:
            b0, b1 = self._read(2)
            n = b1 & 0x7f
            if n == 126: n = struct.unpack('>H', self._read(2))[0]
            elif n == 127: n = struct.unpack('>Q', self._read(8))[0]
            payload, op = self._read(n), b0 & 0x0f
            if op == 8: sys.exit('websocket closed by HA')
            if op in (9, 10): continue                 # ping/pong: ignore
            data += payload
            if b0 & 0x80: return json.loads(data)
    def send(self, obj):
        p, mask = json.dumps(obj).encode(), os.urandom(4)
        n = len(p)
        hdr = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack('>H', n) if n < 65536
                               else bytes([0x80 | 127]) + struct.pack('>Q', n))
        self.s.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(p)))
    def cmd(self, msg):
        self.n += 1
        self.send({'id': self.n, **msg})
        while True:
            r = self.recv()
            if r.get('id') == self.n and r.get('type') == 'result':
                if not r.get('success'): sys.exit(f'{msg["type"]} failed: {r.get("error")}')
                return r.get('result')

def exchange(code):
    st, t = call('POST', HA + '/auth/token', form={'grant_type': 'authorization_code', 'code': code, 'client_id': CLIENT})
    if st != 200: sys.exit(f'token exchange failed: {st} {t}')
    return t['access_token']

# 1) onboarding (each step only while not done), else a normal login
st, steps = call('GET', HA + '/api/onboarding')
if st == 404:   # onboarding finished: HA stops serving the endpoint after the next restart
    steps = [{'step': s, 'done': True} for s in ('user', 'core_config', 'analytics', 'integration')]
elif st != 200:
    sys.exit(f'onboarding status failed: {st} {steps}')
done = {s['step']: s['done'] for s in steps}
if not done.get('user'):
    st, r = call('POST', HA + '/api/onboarding/users',
                 {'client_id': CLIENT, 'name': USER, 'username': USER, 'password': PW, 'language': 'en'})
    if st != 200: sys.exit(f'onboarding user failed: {st} {r}')
    T = exchange(r['auth_code'])
    log(f'onboarding: admin user {USER} created')
else:
    st, f = call('POST', HA + '/auth/login_flow', {'client_id': CLIENT, 'handler': ['homeassistant', None], 'redirect_uri': CLIENT})
    st, r = call('POST', HA + f'/auth/login_flow/{f["flow_id"]}', {'username': USER, 'password': PW, 'client_id': CLIENT})
    if not isinstance(r, dict) or r.get('type') != 'create_entry':
        sys.exit(f'login as {USER} failed: {st} {r.get("errors") if isinstance(r, dict) else r}')
    T = exchange(r['result'])
    log(f'login as {USER}: ok')
# core config is in configuration.yaml; analytics stay off (the step only records that it was shown)
for step, body in (('core_config', {}), ('analytics', {}), ('integration', {'client_id': CLIENT, 'redirect_uri': CLIENT})):
    if not done.get(step):
        st, r = call('POST', HA + f'/api/onboarding/{step}', body, token=T)
        log(f'onboarding: {step} {st} {r if st >= 300 else ""}')

# 2) long-lived token: keep the stored one while it works, else revoke ours and issue a new one
cur = os.environ.get('HA_TOKEN', '')
st = call('GET', HA + '/api/', token=cur)[0] if cur else 401
if st == 200:
    log('api token: valid, kept')
elif st not in (401, 403):   # HA starting, port-forward hiccup: never rotate a token that may still be fine
    sys.exit(f'api token: check returned {st}; not rotating, re-run when Home Assistant answers')
else:
    ws = WS(T)
    for t in ws.cmd({'type': 'auth/refresh_tokens'}):
        if t.get('client_name') == TOKEN_NAME:
            ws.cmd({'type': 'auth/delete_refresh_token', 'refresh_token_id': t['id']})
            log(f'api token: revoked the stale "{TOKEN_NAME}" token')
    new = ws.cmd({'type': 'auth/long_lived_access_token', 'client_name': TOKEN_NAME, 'lifespan': 3650})
    print(f'HA_TOKEN={new}', flush=True)
    log(f'api token: created "{TOKEN_NAME}" (10 years)')

# 2b) proxy trust for the LAN gateway. Since 2026.10 HA keeps http settings in .storage (YAML `http:` is imported
# once, then ignored): a change is staged as "pending", HA restarts into it, and it must be promoted within 5 minutes
# or HA reverts to the stable one. Promote only after a request through the gateway works.
def wait_running():
    for _ in range(120):
        st, c = call('GET', HA + '/api/config', token=T)
        if st == 200 and isinstance(c, dict) and c.get('state') == 'RUNNING':
            return
        time.sleep(2)
    sys.exit('Home Assistant did not come back after the restart')

def norm(nets): return sorted(str(ipaddress.ip_network(n, strict=False)) for n in nets or [])

http = WS(T).cmd({'type': 'http/config'})
stable = http['stable'] or http['default']
if stable.get('use_x_forwarded_for') and norm(stable.get('trusted_proxies')) == norm(TRUSTED_PROXIES):
    log('http: proxy trust already set')
else:
    keep = {k: v for k, v in stable.items() if k not in ('created_at', 'error', 'error_message')}
    r = WS(T).cmd({'type': 'http/config/configure',
                   'config': dict(keep, use_x_forwarded_for=True, trusted_proxies=TRUSTED_PROXIES)})
    log(f'http: proxy trust staged (restart: {r.get("restart")}), waiting for Home Assistant')
    time.sleep(10)
    wait_running()
    gw = ssl.create_default_context(cafile=os.environ['CA_FILE'])
    st, r = call('GET', LAN_URL + '/api/', token=T, ctx=gw)
    if st != 200:
        sys.exit(f'http: {LAN_URL} answers {st} with the staged config; not promoting (HA reverts it in 5 minutes)')
    WS(T).cmd({'type': 'http/config/promote'})
    log(f'http: proxy trust promoted ({LAN_URL} works through the gateway)')

# 3) Jellyfin user for the HA integration: non-admin, hidden from the login screen, LAN only. Password from Vault
JH = {'Authorization': f'MediaBrowser Token="{os.environ["JF_ADMIN_KEY"]}"'}
st, users = call('GET', JF + '/Users', headers=JH)
if st != 200: sys.exit(f'jellyfin user list failed: {st} {users}')
u = next((x for x in users if x['Name'] == JF_USER), None)
jf_reset = False
if u is None:
    st, u = call('POST', JF + '/Users/New', {'Name': JF_USER, 'Password': JF_PW}, headers=JH)
    if st != 200: sys.exit(f'jellyfin user create failed: {st} {u}')
    log(f'jellyfin user {JF_USER}: created')
else:
    login = {'Authorization': 'MediaBrowser Client="dagalab-setup", Device="cli", DeviceId="dagalab-setup-ha", Version="1.0"'}
    st, _ = call('POST', JF + '/Users/AuthenticateByName', {'Username': JF_USER, 'Pw': JF_PW}, headers=login)
    if st == 200:
        log(f'jellyfin user {JF_USER}: password matches Vault')
    else:   # a reset signs the user out everywhere, so only when it doesn't match; then HA's integration re-logs in
        st, r = call('POST', JF + f'/Users/{u["Id"]}/Password', {'NewPw': JF_PW}, headers=JH)
        log(f'jellyfin user {JF_USER}: password reset to the Vault one ({st})')
        jf_reset = True
pol = dict(u['Policy'], IsAdministrator=False, IsHidden=True, EnableRemoteAccess=False,
           EnableContentDeletion=False, EnableContentDownloading=False)
if pol != u['Policy']:
    st, r = call('POST', JF + f'/Users/{u["Id"]}/Policy', pol, headers=JH)
    log(f'jellyfin user {JF_USER}: policy (non-admin, hidden, LAN only) {st} {r if st >= 300 else ""}')

# 4) lab integrations via config flows; a domain that already has an entry is left alone. Fields are filled by name
# from each step's form, so an HA release that renames a required field aborts that flow with a clear message.
FLOWS = {
    'unifi': {'host': '192.168.3.1', 'port': 443, 'verify_ssl': False,   # UCG cert is self-signed
              'username': 'homepage', 'password': os.environ['UNIFI_PASSWORD']},
    'synology_dsm': {'host': '192.168.3.20', 'port': 5000, 'ssl': False, 'verify_ssl': False,   # DSM's CA isn't served
                     'username': 'homepage', 'password': os.environ['SYNOLOGY_PASSWORD'],
                     # 2nd step: where HA's Synology backup agent would write. Nothing is written unless that agent is
                     # picked in Settings -> System -> Backups. Not the media share (video) or anyone's home.
                     'backup_share': '/dagadata', 'backup_path': 'home-assistant'},
    'jellyfin': {'url': f'http://jellyfin.{SVC}:8096', 'username': JF_USER, 'password': JF_PW},
    'sonarr': {'url': f'http://sonarr.{SVC}:8989', 'api_key': os.environ['SONARR_KEY'], 'verify_ssl': False},
    'radarr': {'url': f'http://radarr.{SVC}:7878', 'api_key': os.environ['RADARR_KEY'], 'verify_ssl': False},
    # UPS sensors (charge, load, runtime, status) from daga-nas's NUT server; read-only, no login (HA's hostNetwork
    # pod connects from a node IP, which is on the NAS's NUT allow list). Phone notifications: later (README TODO).
    'nut': {'host': '192.168.3.20', 'port': 3493},
}
def fill(schema, values):
    """Form input from `values` by field name: strings where the form wants one (e.g. DSM's port), and nested dicts
    for collapsible sections (type 'expandable', e.g. Sonarr's more_options -> verify_ssl)."""
    out = {}
    for f in schema:
        if f.get('type') == 'expandable':
            out[f['name']] = fill(f.get('schema') or [], values)
        elif f['name'] in values:
            v = values[f['name']]
            out[f['name']] = str(v) if f.get('type') == 'string' and not isinstance(v, str) else v
    return out

def missing(schema, values):
    return [n for f in schema for n in (missing(f.get('schema') or [], values) if f.get('type') == 'expandable' else
            [f['name']] if f.get('required') and f['name'] not in values and 'default' not in f else [])]

st, entries = call('GET', HA + '/api/config/config_entries/entry', token=T)
if st != 200: sys.exit(f'config entries failed: {st} {entries}')
have, failed = {e['domain'] for e in entries}, []
for e in entries:   # HA's Jellyfin session dies with a password reset (and with the earlier always-reset runs)
    if e['domain'] == 'jellyfin' and (jf_reset or e.get('state') != 'loaded'):
        st, _ = call('POST', HA + f'/api/config/config_entries/entry/{e["entry_id"]}/reload', token=T)
        log(f'integration jellyfin: reloaded ({st})')
for domain, values in FLOWS.items():
    if domain in have:
        log(f'integration {domain}: present'); continue
    st, r = call('POST', HA + '/api/config/config_entries/flow', {'handler': domain, 'show_advanced_options': False}, token=T)
    for _ in range(5):   # multi-step flows (e.g. a site picker); a step with errors stops here
        if not isinstance(r, dict) or r.get('type') != 'form' or r.get('errors'):
            break
        schema = r.get('data_schema') or []
        if need := missing(schema, values):
            r = dict(r, errors={'missing_fields': need}); break
        st, r = call('POST', HA + f'/api/config/config_entries/flow/{r["flow_id"]}', fill(schema, values), token=T)
    if isinstance(r, dict) and r.get('type') == 'create_entry':
        log(f'integration {domain}: added ({r.get("title")})'); continue
    if isinstance(r, dict) and r.get('type') == 'form' and r.get('flow_id'):
        call('DELETE', HA + f'/api/config/config_entries/flow/{r["flow_id"]}', token=T)   # don't leave it half-done
    why = (r.get('errors') or r.get('reason') or r.get('type')) if isinstance(r, dict) else f'{st} {r}'
    log(f'integration {domain}: FAILED {why}'); failed.append(domain)
if failed:
    sys.exit(f'integrations failed: {", ".join(failed)}')
