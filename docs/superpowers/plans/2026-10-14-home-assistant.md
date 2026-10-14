# Home Assistant Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run Home Assistant on the RKE2 cluster at `https://home.lab.internal`, bootstrapped by a re-runnable script, wired into Homepage, Homarr and Prometheus.

**Architecture:**
- **Deployment:** one bjw-s `app-template` pod with `hostNetwork`, so its discovery traffic leaves from the node's VLAN 3 interface and reaches the main LAN by routing.
- **Storage:** `/config` on Longhorn. A git-managed `configuration.yaml` is mounted from a ConfigMap.
- **Setup script:** `scripts/home-assistant-setup.{sh,py}` runs HA's first-time setup, then stores the admin password and a long-lived token in Vault. It also creates a Jellyfin user for HA and adds the lab integrations through HA's config-flow REST API.
- **Consumers:** Homepage, Homarr and Prometheus read the token from Vault via ExternalSecrets, or via `homarr-setup.sh`.

**Tech Stack:** Argo CD, bjw-s app-template 5.2.1, Home Assistant `2026.10.1`, Envoy Gateway (HTTPRoute via `helm/charts/lan-gateway`), External Secrets + Vault, kube-prometheus-stack ServiceMonitor, Python 3 stdlib only (no `websockets`/`requests`).

**Spec:** `docs/superpowers/specs/2026-10-14-home-assistant-design.md`

## Global Constraints

- **Hostname:** `home.lab.internal`, on the LAN gateway 192.168.3.201 only. Never on the public shared-gateway (.200).
- **Namespace:** `home-assistant`. Argo app `home-assistant`, sync wave `"0"`, `project: default`, automated prune + selfHeal, `CreateNamespace=true`, the standard retry block (copy from `argocd/apps/jellyfin/application.yaml`).
- **Image:** `ghcr.io/home-assistant/home-assistant`, tag `2026.10.1` (an exact tag; Renovate bumps it).
- **Pod settings:**
  - 1 replica, `strategy: Recreate`.
  - `hostNetwork: true`, `dnsPolicy: ClusterFirstWithHostNet`.
  - No node pinning, no USB.
- **Config storage:** `/config` on a Longhorn PVC, 5Gi, RWO. No NFS.
- **Vault `secret/home-assistant`:** fields `admin-user` (`jim`), `admin-password`, `jellyfin-password`, `api-token`.
- **Secrets handling:**
  - Never print secrets.
  - Never commit secrets.
  - Scripts pass secrets via env.
- **Python checks:**
  - Syntax-check with `python3 -c "import ast,sys; ast.parse(open(sys.argv[1]).read())" <file>`.
  - **Never** `python3 -m py_compile`: it rewrites tracked `scripts/__pycache__`.
- **Commits:**
  - Commits go to `main` and are pushed. Argo deploys from `main`.
  - Each message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Location, units and time zone:** set in `configuration.yaml` (git), not through the API:
  - latitude 42.5274, longitude -71.7563
  - `unit_system: us_customary`
  - `time_zone: America/New_York`
  - currency USD, country US
  - This supersedes the spec's "onboarding sets core config" line. Task 1 updates the spec to match.
- **Off-limits:**
  - Don't touch Sonarr/Radarr quality profiles.
  - Don't re-enable the paused indexers.
  - Don't touch the NAS `personal/`, `iMovie/` or `youtube/` folders.
- **Mac shell:** there is no `timeout`. Use bash scripts for anything that reads secrets, since zsh doesn't word-split.

## Review Focus

1. **Gateway requests rejected by HA with 400 "untrusted proxy".** Flannel may SNAT pod→hostNetwork traffic to the node's IP, so `trusted_proxies` must cover both the pod CIDR and the node IPs. Pinned by: Task 1, Step 5, which checks the curl result and the logs.
2. **The setup script fails partway after creating a token.** The token must still reach Vault: the wrapper captures stdout even when the `.py` exits non-zero. Otherwise a token is orphaned and the next run can't log in with it. Pinned by: Task 2, Step 5, the deliberately bad Sonarr key run.
3. **The Vault `api-token` is stale or revoked.** A re-run must notice it (via `GET /api/`), revoke the old `dagalab` token and store a new one, not fail or duplicate. Pinned by: Task 2, Step 7.
4. **The pod restarts or moves to another node.** UI-made automations, scripts and scenes must survive. The init container only creates missing files and never truncates. Pinned by: Task 1, Step 6.
5. **An integration's form changes in a future HA release** (renamed or new required field). The script must abort that flow cleanly, name the missing field, carry on with the others and exit non-zero. Pinned by: Task 2, Step 5, which covers the error path with a bad key, plus the `missing` branch in the code.

---

## File Structure

| File | Responsibility |
|---|---|
| `argocd/apps/home-assistant/application.yaml` (create) | Argo app: app-template + values from this repo |
| `argocd/apps/home-assistant/values.yaml` (create) | Pod, hostNetwork, PVC, ConfigMap `configuration.yaml`, init container, Service, ServiceMonitor (Task 3) |
| `helm/charts/lan-gateway/values.yaml` (modify) | `home` route |
| `scripts/home-assistant-setup.sh` (create) | Vault I/O, port-forwards, calls the `.py`, stores the token |
| `scripts/home-assistant-setup.py` (create) | Onboarding/login, token (via a minimal stdlib websocket), Jellyfin user, integrations |
| `helm/charts/secret-stores/values.yaml` (modify) | `HOMEPAGE_VAR_HA_TOKEN` in `homepage`; new `home-assistant-prometheus` ExternalSecret |
| `argocd/apps/homepage/values.yaml` (modify) | Home Assistant tile in Platform |
| `scripts/homarr-board.py`, `scripts/homarr-setup.sh` (modify) | Home Assistant integration + launcher |
| `README.md`, `CLAUDE.md`, the spec (modify) | Docs |

---

### Task 1: Deploy Home Assistant behind `home.lab.internal`

**Files:**
- Create: `argocd/apps/home-assistant/application.yaml`
- Create: `argocd/apps/home-assistant/values.yaml`
- Modify: `helm/charts/lan-gateway/values.yaml` (routes list, after `homarr`)
- Modify: `README.md` (LAN table, Network table note, TODO list)
- Modify: `docs/superpowers/specs/2026-10-14-home-assistant-design.md` (core config moves to YAML)

**Interfaces:**
- Produces:
  - Service `home-assistant/home-assistant`, port named `http` = 8123. Tasks 2 and 3 port-forward to it, scrape it and point widgets at it.
  - In-cluster URL `http://home-assistant.home-assistant.svc.cluster.local:8123`.
  - Pod label `app.kubernetes.io/name=home-assistant`.

- [ ] **Step 1: Write the failing check.** The route doesn't exist yet, so the gateway returns 404.

Run:
```bash
curl -s -o /dev/null -w '%{http_code}\n' -L --cacert ~/kube-configs/dagalab-lab-ca.crt \
  --resolve home.lab.internal:443:192.168.3.201 https://home.lab.internal/
```
Expected: `404`.

- [ ] **Step 2: Write `argocd/apps/home-assistant/application.yaml`**

```yaml
---
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: home-assistant
  namespace: argocd
  finalizers:
    - resources-finalizer.argocd.argoproj.io
  annotations:
    # Wave 0: its ExternalSecret (Prometheus token) comes from secret-stores (wave -5)
    argocd.argoproj.io/sync-wave: "0"
spec:
  project: default
  sources:
    - repoURL: ghcr.io/bjw-s-labs/helm
      chart: app-template
      targetRevision: "5.2.1"
      helm:
        releaseName: home-assistant
        valueFiles:
          - $values/argocd/apps/home-assistant/values.yaml
    - repoURL: https://github.com/jimdaga/dagalab-infra.git
      targetRevision: main
      ref: values
  destination:
    server: https://kubernetes.default.svc
    namespace: home-assistant
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
    retry:
      limit: 5
      backoff:
        duration: 5s
        factor: 2
        maxDuration: 3m
```

- [ ] **Step 3: Write `argocd/apps/home-assistant/values.yaml`**

```yaml
# Home Assistant: home automation. LAN only: https://home.lab.internal (helm/charts/lan-gateway).
# hostNetwork: mDNS/SSDP discovery runs from the node's VLAN 3 interface and reaches the main LAN by routing
# (not isolated from VLAN 3). Port 8123 is open on whichever node runs the pod.
# configuration.yaml is below (git); everything made in the UI (integrations, devices, dashboards, automations)
# lives in /config on Longhorn. First-run setup and lab integrations: scripts/home-assistant-setup.sh.
controllers:
  home-assistant:
    strategy: Recreate   # never two HA instances on one config
    pod:
      hostNetwork: true
      dnsPolicy: ClusterFirstWithHostNet   # still resolve *.svc.cluster.local (Jellyfin, Sonarr, Radarr)
    initContainers:
      # the UI editors write these; configuration.yaml includes them. Create only when missing, never truncate.
      seed:
        image:
          repository: ghcr.io/home-assistant/home-assistant
          tag: 2026.10.1
        command:
          - sh
          - -c
          - |
            for f in automations:[] scripts:{} scenes:[]; do
              p=/config/${f%%:*}.yaml
              [ -e "$p" ] || echo "${f#*:}" > "$p"
            done
    containers:
      app:
        image:
          repository: ghcr.io/home-assistant/home-assistant
          tag: 2026.10.1
        env:
          TZ: America/New_York
        probes:
          liveness: &probe
            enabled: true
            custom: true
            spec:
              tcpSocket:
                port: 8123
              periodSeconds: 30
              failureThreshold: 5
          readiness: *probe
          startup:
            enabled: true
            custom: true
            spec:
              tcpSocket:
                port: 8123
              periodSeconds: 10
              failureThreshold: 30   # first start installs integration requirements
        resources:
          requests:
            cpu: 100m
            memory: 512Mi
          limits:
            memory: 2Gi

service:
  app:
    controller: home-assistant
    ports:
      http:
        port: 8123

configMaps:
  config:
    data:
      configuration.yaml: |
        default_config:

        # core config lives here, not in the UI (the UI shows it read-only)
        homeassistant:
          name: Home
          latitude: 42.5274
          longitude: -71.7563
          unit_system: us_customary
          time_zone: America/New_York
          currency: USD
          country: US
          internal_url: https://home.lab.internal

        # The LAN gateway (Envoy) proxies from the pod network; flannel may SNAT pod -> hostNetwork traffic to the
        # node IP, so trust both. Without this HA answers proxied requests with 400.
        http:
          use_x_forwarded_for: true
          trusted_proxies:
            - 10.42.0.0/16
            - 192.168.3.11
            - 192.168.3.12
            - 192.168.3.13

        # /api/prometheus, scraped with the long-lived token (ServiceMonitor below, token via ExternalSecret)
        prometheus:

        automation: !include automations.yaml
        script: !include scripts.yaml
        scene: !include scenes.yaml

persistence:
  # recorder SQLite DB, .storage, UI-made YAML: Longhorn, never NFS
  config:
    type: persistentVolumeClaim
    accessMode: ReadWriteOnce
    size: 5Gi
    globalMounts:
      - path: /config
  configuration:
    type: configMap
    identifier: config
    advancedMounts:
      home-assistant:
        app:
          - { path: /config/configuration.yaml, subPath: configuration.yaml, readOnly: true }
```

Note: `prometheus:` in `configuration.yaml` is enabled from the start. It is harmless until Task 3 adds the ServiceMonitor, and it saves a second rollout.

- [ ] **Step 4: Render it locally, then add the route.**

Run:
```bash
helm template home-assistant oci://ghcr.io/bjw-s-labs/helm/app-template --version 5.2.1 \
  -f argocd/apps/home-assistant/values.yaml | grep -nE 'hostNetwork|dnsPolicy|subPath|claimName|kind:'
```
Expected:
- `hostNetwork: true` and `dnsPolicy: ClusterFirstWithHostNet` are present.
- `subPath: configuration.yaml` appears on the `app` container only, not on `seed`.
- Kinds are Deployment, Service, ConfigMap and PersistentVolumeClaim.

Add to `helm/charts/lan-gateway/values.yaml` after the `homarr` line:
```yaml
  - { host: home,     namespace: home-assistant,  service: home-assistant,                port: 8123 }
```
Then run `make lint && make template >/dev/null`. Expected: no errors.

- [ ] **Step 5: Docs, spec, then commit and verify live.**

README:
- LAN table, after the `homarr` row:
```markdown
| `home.lab.internal` | Home Assistant: `hostNetwork` pod (discovery from the node's VLAN 3 interface; port 8123 open on that node). `configuration.yaml` (location, units, proxy trust, Prometheus) is in `argocd/apps/home-assistant/values.yaml`; everything made in the UI lives on its Longhorn volume, **not** in git. First run + lab integrations: `scripts/home-assistant-setup.sh` (admin `jim`, password + API token in Vault `secret/home-assistant`) |
```
- TODO list:
```markdown
- [ ] UniFi DNS: `home.lab.internal` → 192.168.3.201
- [ ] Home Assistant: review Settings → Devices & services → Discovered (main-LAN devices) and add the ones wanted
```

Spec:
- Replace the onboarding item's core config text with: `core config (location, units, time zone, currency) is set in configuration.yaml instead (git)`.

Commit and push:
```bash
git add argocd/apps/home-assistant helm/charts/lan-gateway/values.yaml README.md docs/superpowers/specs/2026-10-14-home-assistant-design.md
git commit -m "Home Assistant: hostNetwork pod on Longhorn at home.lab.internal

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push
```

Wait for Argo to sync. Poll `kubectl -n argocd get app home-assistant lan-gateway`; expected `Synced Healthy` for both. Then `kubectl -n home-assistant get pods -o wide`; expected 1/1 Running, IP = a node IP (192.168.3.11–13).

Re-run the Step 1 curl. Expected: `200`.

Run:
```bash
kubectl -n home-assistant logs deploy/home-assistant | grep -iE 'untrusted proxy|x-forwarded|invalid config' || echo clean
```
Expected: `clean`. If it shows an untrusted-proxy line, add the reported source IP's range to `trusted_proxies` and redo this step.

- [ ] **Step 6: Check that UI-made files survive a restart** (Review Focus 4).

```bash
kubectl -n home-assistant exec deploy/home-assistant -c app -- sh -c 'cat /config/automations.yaml; echo "# keep-me" >> /config/scenes.yaml'
kubectl -n home-assistant rollout restart deploy/home-assistant && kubectl -n home-assistant rollout status deploy/home-assistant --timeout=300s
kubectl -n home-assistant exec deploy/home-assistant -c app -- grep -c keep-me /config/scenes.yaml
```
Expected:
- The first command prints `[]`.
- The final grep prints `1`.

Then remove the marker:
```bash
kubectl -n home-assistant exec deploy/home-assistant -c app -- sed -i '/keep-me/d' /config/scenes.yaml
```

---

### Task 2: Setup script: onboarding, token, Jellyfin user, integrations

**Files:**
- Create: `scripts/home-assistant-setup.sh`
- Create: `scripts/home-assistant-setup.py`
- Modify: `README.md` (the Task 1 table row already names the script; add a "Media stack plan"/notes line if helpful, nothing else)
- Modify: `CLAUDE.md` (one line in Conventions)

**Interfaces:**
- Consumes: Service `home-assistant/home-assistant:8123` and Service `media/jellyfin:8096` (via port-forwards to 127.0.0.1:18123 and :18096).
- Consumes these k8s secrets:
  - `media/jellyfin-arr` key `JELLYFIN_KEY`
  - `media/sonarr` key `SONARR__AUTH__APIKEY`
  - `media/radarr` key `RADARR__AUTH__APIKEY`
- Consumes Vault `secret/homepage-devices`: `unifi-password`, `synology-password`.
- Produces in Vault `secret/home-assistant`: `admin-user`, `admin-password`, `jellyfin-password`, `api-token`. Task 3 reads `api-token`.
- Produces in HA: a long-lived token named `dagalab`, and config entries for the domains `unifi`, `synology_dsm`, `jellyfin`, `sonarr`, `radarr`.
- Produces in Jellyfin: user `homeassistant` (non-admin, hidden).

- [ ] **Step 1: Write the failing check.** `secret/home-assistant` doesn't exist yet.

```bash
bash -c 'VAULT_TOKEN=$(cat ~/.vault-token); kubectl -n vault exec -i vault-0 -- env VAULT_TOKEN="$VAULT_TOKEN" vault kv get -field=admin-user secret/home-assistant'
```
Expected: an error, `No value found at secret/data/home-assistant`.

- [ ] **Step 2: Write `scripts/home-assistant-setup.py`**

```python
#!/usr/bin/env python3
"""Idempotent Home Assistant setup over its API (port-forwards: HA on 127.0.0.1:18123, Jellyfin on 127.0.0.1:18096).

Env: HA_USER/HA_PASSWORD (admin to create/log in as), HA_TOKEN (long-lived token from Vault; may be empty or stale),
JF_ADMIN_KEY (Jellyfin API key "arr"), JF_HA_PASSWORD (password of the Jellyfin user `homeassistant`),
UNIFI_PASSWORD/SYNOLOGY_PASSWORD (the `homepage` device accounts), SONARR_KEY, RADARR_KEY.
Prints HA_TOKEN=<token> on stdout only when it creates a new long-lived token (the caller stores it in Vault, even
when this exits non-zero); everything else goes to stderr and never includes secrets. Exits 1 if an integration failed.
"""
import base64, json, os, socket, struct, sys, urllib.error, urllib.parse, urllib.request

HA, JF = 'http://127.0.0.1:18123', 'http://127.0.0.1:18096'
CLIENT = HA + '/'           # OAuth client id for our own logins (any URL on the same host)
TOKEN_NAME = 'dagalab'      # the long-lived token's name in HA (Profile -> Security)
JF_USER = 'homeassistant'   # Jellyfin user the HA Jellyfin integration signs in as
SVC = 'media.svc.cluster.local'
USER, PW, JF_PW = os.environ['HA_USER'], os.environ['HA_PASSWORD'], os.environ['JF_HA_PASSWORD']

def log(*a): print(*a, file=sys.stderr, flush=True)

def call(method, url, body=None, token=None, form=None, headers=None):
    h, data = dict(headers or {}), None
    if form is not None:
        data, h['Content-Type'] = urllib.parse.urlencode(form).encode(), 'application/x-www-form-urlencoded'
    elif body is not None:
        data, h['Content-Type'] = json.dumps(body).encode(), 'application/json'
    if token:
        h['Authorization'] = f'Bearer {token}'
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method=method, headers=h, data=data), timeout=120) as r:
            raw = r.read(); return r.status, (json.loads(raw) if raw[:1] in (b'{', b'[') else raw.decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]

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
if st != 200: sys.exit(f'onboarding status failed: {st} {steps}')
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
if cur and call('GET', HA + '/api/', token=cur)[0] == 200:
    log('api token: valid, kept')
else:
    ws = WS(T)
    for t in ws.cmd({'type': 'auth/refresh_tokens'}):
        if t.get('client_name') == TOKEN_NAME:
            ws.cmd({'type': 'auth/delete_refresh_token', 'refresh_token_id': t['id']})
            log(f'api token: revoked the stale "{TOKEN_NAME}" token')
    new = ws.cmd({'type': 'auth/long_lived_access_token', 'client_name': TOKEN_NAME, 'lifespan': 3650})
    print(f'HA_TOKEN={new}', flush=True)
    log(f'api token: created "{TOKEN_NAME}" (10 years)')

# 3) Jellyfin user for the HA integration: non-admin, hidden from the login screen, LAN only. Password from Vault
JH = {'Authorization': f'MediaBrowser Token="{os.environ["JF_ADMIN_KEY"]}"'}
st, users = call('GET', JF + '/Users', headers=JH)
if st != 200: sys.exit(f'jellyfin user list failed: {st} {users}')
u = next((x for x in users if x['Name'] == JF_USER), None)
if u is None:
    st, u = call('POST', JF + '/Users/New', {'Name': JF_USER, 'Password': JF_PW}, headers=JH)
    if st != 200: sys.exit(f'jellyfin user create failed: {st} {u}')
    log(f'jellyfin user {JF_USER}: created')
else:
    st, r = call('POST', JF + f'/Users/{u["Id"]}/Password', {'NewPw': JF_PW}, headers=JH)
    log(f'jellyfin user {JF_USER}: password synced from Vault ({st})')
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
                     'username': 'homepage', 'password': os.environ['SYNOLOGY_PASSWORD']},
    'jellyfin': {'url': f'http://jellyfin.{SVC}:8096', 'username': JF_USER, 'password': JF_PW},
    'sonarr': {'url': f'http://sonarr.{SVC}:8989', 'api_key': os.environ['SONARR_KEY'], 'verify_ssl': False},
    'radarr': {'url': f'http://radarr.{SVC}:7878', 'api_key': os.environ['RADARR_KEY'], 'verify_ssl': False},
}
st, entries = call('GET', HA + '/api/config/config_entries/entry', token=T)
if st != 200: sys.exit(f'config entries failed: {st} {entries}')
have, failed = {e['domain'] for e in entries}, []
for domain, values in FLOWS.items():
    if domain in have:
        log(f'integration {domain}: present'); continue
    st, r = call('POST', HA + '/api/config/config_entries/flow', {'handler': domain, 'show_advanced_options': False}, token=T)
    for _ in range(5):   # multi-step flows (e.g. a site picker); a step with errors stops here
        if not isinstance(r, dict) or r.get('type') != 'form' or r.get('errors'):
            break
        schema = r.get('data_schema') or []
        missing = [f['name'] for f in schema if f.get('required') and f['name'] not in values and 'default' not in f]
        if missing:
            r = dict(r, errors={'missing_fields': missing}); break
        st, r = call('POST', HA + f'/api/config/config_entries/flow/{r["flow_id"]}',
                     {f['name']: values[f['name']] for f in schema if f['name'] in values}, token=T)
    if isinstance(r, dict) and r.get('type') == 'create_entry':
        log(f'integration {domain}: added ({r.get("title")})'); continue
    if isinstance(r, dict) and r.get('type') == 'form' and r.get('flow_id'):
        call('DELETE', HA + f'/api/config/config_entries/flow/{r["flow_id"]}', token=T)   # don't leave it half-done
    why = (r.get('errors') or r.get('reason') or r.get('type')) if isinstance(r, dict) else f'{st} {r}'
    log(f'integration {domain}: FAILED {why}'); failed.append(domain)
if failed:
    sys.exit(f'integrations failed: {", ".join(failed)}')
```

Run: `python3 -c "import ast,sys; ast.parse(open(sys.argv[1]).read())" scripts/home-assistant-setup.py`. Expected: no output.

- [ ] **Step 3: Write `scripts/home-assistant-setup.sh`**, then `chmod +x scripts/home-assistant-setup.sh`.

```bash
#!/usr/bin/env bash
# Home Assistant first-run setup: admin user, long-lived API token, Jellyfin user `homeassistant`, lab integrations
# (UniFi, Synology DSM, Jellyfin, Sonarr, Radarr). Idempotent.
#   VAULT_TOKEN=<root or admin token> ./scripts/home-assistant-setup.sh
# secret/home-assistant: admin-user, admin-password, jellyfin-password (generated here), api-token (created by the .py).
set -euo pipefail
: "${VAULT_TOKEN:?}"
v() { kubectl -n vault exec -i vault-0 -- env VAULT_TOKEN="$VAULT_TOKEN" vault "$@"; }
key() { kubectl -n "$1" get secret "$2" -o jsonpath="{.data.$3}" | base64 -d; }

# passwords go to Vault before anything uses them, so a failed run never loses one
if ! v kv get -field=admin-password secret/home-assistant >/dev/null 2>&1; then
  v kv put secret/home-assistant admin-user=jim admin-password="$(openssl rand -base64 18)" \
    jellyfin-password="$(openssl rand -base64 18)" >/dev/null
  echo "generated secret/home-assistant credentials" >&2
fi
export HA_USER=$(v kv get -field=admin-user secret/home-assistant) HA_PASSWORD=$(v kv get -field=admin-password secret/home-assistant) \
  JF_HA_PASSWORD=$(v kv get -field=jellyfin-password secret/home-assistant)
export HA_TOKEN=$(v kv get -field=api-token secret/home-assistant 2>/dev/null || true)
export JF_ADMIN_KEY=$(key media jellyfin-arr JELLYFIN_KEY) SONARR_KEY=${SONARR_KEY:-$(key media sonarr SONARR__AUTH__APIKEY)} \
  RADARR_KEY=$(key media radarr RADARR__AUTH__APIKEY)
export UNIFI_PASSWORD=$(v kv get -field=unifi-password secret/homepage-devices) \
  SYNOLOGY_PASSWORD=$(v kv get -field=synology-password secret/homepage-devices)   # the `homepage` device accounts

kubectl -n home-assistant port-forward svc/home-assistant 18123:8123 >/dev/null 2>&1 & pf1=$!
kubectl -n media port-forward svc/jellyfin 18096:8096 >/dev/null 2>&1 & pf2=$!
trap 'kill $pf1 $pf2 2>/dev/null' EXIT
for _ in $(seq 1 30); do
  curl -s -o /dev/null http://127.0.0.1:18123/api/onboarding && curl -s -o /dev/null http://127.0.0.1:18096/health && break
  sleep 0.5
done

# store a new token even when the .py failed later on (an integration), or it would be orphaned
rc=0
out=$(python3 "$(dirname "$0")/home-assistant-setup.py") || rc=$?
tok=$(sed -n 's/^HA_TOKEN=//p' <<<"$out")
if [[ -n "$tok" ]]; then
  v kv patch secret/home-assistant api-token="$tok" >/dev/null
  echo "stored Home Assistant API token in secret/home-assistant" >&2
fi
exit $rc
```

`SONARR_KEY=${SONARR_KEY:-…}` lets Step 5 inject a bad key; normal runs read the real one.

Run: `bash -n scripts/home-assistant-setup.sh`. Expected: no output.

- [ ] **Step 4: First run with a deliberately wrong Sonarr key**

```bash
bash -c 'VAULT_TOKEN=$(cat ~/.vault-token) SONARR_KEY=0000deadbeef ./scripts/home-assistant-setup.sh'; echo "exit=$?"
```

- [ ] **Step 5: Verify the error path** (Review Focus 2 and 5)

Expected stderr, in order:
- `generated secret/home-assistant credentials`
- `onboarding: admin user jim created`
- `api token: created "dagalab" (10 years)`
- `jellyfin user homeassistant: created`
- `integration unifi: added …`, `integration synology_dsm: added …`, `integration jellyfin: added …`, `integration sonarr: FAILED {'base': 'invalid_auth'}` (or `cannot_connect`), `integration radarr: added …`
- `stored Home Assistant API token in secret/home-assistant`
- `exit=1`

Then check that the token reached Vault and works:
```bash
bash -c 'VAULT_TOKEN=$(cat ~/.vault-token); t=$(kubectl -n vault exec -i vault-0 -- env VAULT_TOKEN="$VAULT_TOKEN" vault kv get -field=api-token secret/home-assistant)
kubectl -n home-assistant port-forward svc/home-assistant 18123:8123 >/dev/null 2>&1 & p=$!; sleep 2
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $t" http://127.0.0.1:18123/api/; kill $p'
```
Expected: `200`.

If any integration other than sonarr FAILED, read the reason. `missing_fields` means the field names in `FLOWS` need updating for HA 2026.10. Fix `FLOWS` and re-run before going on.

- [ ] **Step 6: Second run with real keys.** This adds sonarr and confirms the script is idempotent.

```bash
bash -c 'VAULT_TOKEN=$(cat ~/.vault-token) ./scripts/home-assistant-setup.sh'; echo "exit=$?"
```
Expected:
- `login as jim: ok`
- `api token: valid, kept`
- `jellyfin user homeassistant: password synced from Vault (204)`
- `present` for unifi, synology_dsm, jellyfin and radarr; `integration sonarr: added …`
- `exit=0`

Run it once more. Expected: every integration `present`, and no "stored … token" line.

- [ ] **Step 7: Stale-token recovery** (Review Focus 3)

```bash
bash -c 'VAULT_TOKEN=$(cat ~/.vault-token); kubectl -n vault exec -i vault-0 -- env VAULT_TOKEN="$VAULT_TOKEN" vault kv patch secret/home-assistant api-token=stale >/dev/null
VAULT_TOKEN=$VAULT_TOKEN ./scripts/home-assistant-setup.sh'; echo "exit=$?"
```
Expected:
- `api token: revoked the stale "dagalab" token`
- `api token: created "dagalab" (10 years)`
- `stored Home Assistant API token …`
- `exit=0`

Then repeat the Step 5 curl. Expected: `200`, now with the new token.

- [ ] **Step 8: Docs and commit**

Add to `CLAUDE.md` Conventions, after the "Media support apps" bullet:
```markdown
- Home Assistant (`argocd/apps/home-assistant`, `https://home.lab.internal`): `hostNetwork` pod for LAN discovery; `configuration.yaml` in its values (git), UI state on Longhorn. `scripts/home-assistant-setup.sh` does onboarding, the long-lived token (Vault `secret/home-assistant` `api-token`, used by Homepage/Homarr/Prometheus), the Jellyfin user `homeassistant` and the lab integrations; re-run after a rebuild.
```

```bash
git add scripts/home-assistant-setup.sh scripts/home-assistant-setup.py CLAUDE.md
git status --short scripts/__pycache__   # expected: nothing
git commit -m "home-assistant-setup: onboarding, API token, Jellyfin user, lab integrations

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push
```

---

### Task 3: Consumers: Homepage tile, Prometheus scrape, Homarr integration

**Files:**
- Modify: `helm/charts/secret-stores/values.yaml` (the `homepage` ExternalSecret, plus a new `home-assistant-prometheus` entry)
- Modify: `argocd/apps/home-assistant/values.yaml` (add `serviceMonitor`)
- Modify: `argocd/apps/homepage/values.yaml` (Platform group, after Vault; the envFrom comment)
- Modify: `scripts/homarr-board.py` (`DEVICES`, `WANT_APPS`, Platform launchers)
- Modify: `scripts/homarr-setup.sh` (export `HA_TOKEN`)
- Modify: `README.md` (Homarr row mentions HA; Homepage row unchanged)

**Interfaces:**
- Consumes: Vault `secret/home-assistant` `api-token` (from Task 2); Service port `http` and the in-cluster URL from Task 1.
- Produces:
  - k8s Secret `homepage/homepage` key `HOMEPAGE_VAR_HA_TOKEN`.
  - k8s Secret `home-assistant/home-assistant-prometheus` key `token`.
  - Homarr integration named `Home Assistant`.

- [ ] **Step 1: Write the failing check.** Prometheus has no HA target yet.

```bash
kubectl -n monitoring exec sts/prometheus-kube-prometheus-stack-prometheus -c prometheus -- \
  wget -qO- 'http://localhost:9090/api/v1/targets?state=active' | grep -c home-assistant
```
Expected: `0`.

- [ ] **Step 2: Add the ExternalSecrets.** In `helm/charts/secret-stores/values.yaml`:

After the `HOMEPAGE_VAR_UNIFI_PASSWORD` entry of the `homepage` ExternalSecret:
```yaml
      - secretKey: HOMEPAGE_VAR_HA_TOKEN   # Home Assistant long-lived token "dagalab" (scripts/home-assistant-setup.sh)
        key: home-assistant
        property: api-token
```
Add a new entry at the end of `externalSecrets`:
```yaml
  # Prometheus scrapes Home Assistant's /api/prometheus with the same long-lived token
  - name: home-assistant-prometheus
    namespace: home-assistant
    data:
      - secretKey: token
        key: home-assistant
        property: api-token
```

- [ ] **Step 3: Add the ServiceMonitor.** Append to `argocd/apps/home-assistant/values.yaml`:

```yaml
serviceMonitor:
  app:
    service:
      identifier: app
    endpoints:
      - port: http
        path: /api/prometheus
        interval: 1m
        authorization:
          type: Bearer
          credentials:   # helm/charts/secret-stores, from Vault secret/home-assistant api-token
            name: home-assistant-prometheus
            key: token
```
Run:
```bash
helm template home-assistant oci://ghcr.io/bjw-s-labs/helm/app-template --version 5.2.1 \
  -f argocd/apps/home-assistant/values.yaml | grep -A12 'kind: ServiceMonitor'
```
Expected: the endpoint shows `authorization.credentials.name: home-assistant-prometheus`.

- [ ] **Step 4: Add the Homepage tile.** In `argocd/apps/homepage/values.yaml`, after the `- Vault:` service in `- Platform:`:

```yaml
            - Home Assistant:
                href: https://home.lab.internal
                icon: home-assistant.png
                description: Home automation
                namespace: home-assistant
                app: home-assistant
                widget:
                  type: homeassistant
                  url: http://home-assistant.home-assistant.svc.cluster.local:8123
                  key: "{{ "{{HOMEPAGE_VAR_HA_TOKEN}}" }}"   # long-lived token "dagalab"
```
Extend the `envFrom` comment on `name: homepage` to list `HA_TOKEN`:
```yaml
              name: homepage   # HOMEPAGE_VAR_{SONARR,RADARR,PROWLARR,JELLYFIN,SEERR,GLUETUN,BAZARR}_KEY, _UNIFI_PASSWORD, _HA_TOKEN
```
Run `make lint && make template >/dev/null`. Expected: no errors.

- [ ] **Step 5: Add the Homarr integration and launcher.** In `scripts/homarr-board.py`, replace the `DEVICES` list and the `for name, kind, url, pw in DEVICES:` loop header and body with:

```python
DEVICES = [  # name, kind, url, secrets (kind, value)
    ('UniFi', 'unifiController', f'https://{UCG}', [('username', 'homepage'), ('password', os.environ['UNIFI_PASSWORD'])]),
    ('daga-nas', 'synology', f'http://{DSM}:5000', [('username', 'homepage'), ('password', os.environ['SYNOLOGY_PASSWORD'])]),
    # long-lived token "dagalab" (scripts/home-assistant-setup.sh); in-cluster, so no lab CA / DNS needed
    ('Home Assistant', 'homeAssistant', 'http://home-assistant.home-assistant.svc.cluster.local:8123',
     [('apiKey', os.environ['HA_TOKEN'])]),
]
for name, kind, url, secrets in DEVICES:
    if name in have:
        continue
    r = trpc('integration.create', {'name': name, 'kind': kind, 'url': url, 'attemptSearchEngineCreation': False,
                                    'secrets': [{'kind': k, 'value': v} for k, v in secrets]},
             f'creating integration {name}')
    if isinstance(r, dict) and r.get('error'):
        raise SystemExit(f'integration {name}: connection test failed: {json.dumps(r["error"])[:400]}')
    log(f'integration {name}: created ({url})')
```
Add to `WANT_APPS`, after `'Vault'`:
```python
    'Home Assistant': ('https://home.lab.internal', f'{ICONS}/svg/home-assistant.svg', 'Home automation'),
```
In `LAYOUT['Platform']`, change the first launcher row to:
```python
        *launchers(('Kubernetes', 'Argo CD', 'Grafana', 'Longhorn', 'Vault', 'Home Assistant'), 0, 0),
```
Update the module docstring's Platform line: `UniFi, NAS and Home Assistant integrations created here`.

In `scripts/homarr-setup.sh`, after the `SYNOLOGY_PASSWORD` export:
```bash
export HA_TOKEN=$(v kv get -field=api-token secret/home-assistant)   # scripts/home-assistant-setup.sh
```
Also add the same line to the scratchpad helper `homarr-board-run.sh` (not in git).

Run: `python3 -c "import ast,sys; ast.parse(open(sys.argv[1]).read())" scripts/homarr-board.py && bash -n scripts/homarr-setup.sh`. Expected: no output.

- [ ] **Step 6: Commit, push and verify live.** Add `Home Assistant` to the README Homarr row's Platform list (`… UniFi summary + Wi-Fi/wired clients and a Home Assistant integration on Platform …`).

```bash
git add helm/charts/secret-stores/values.yaml argocd/apps/home-assistant/values.yaml argocd/apps/homepage/values.yaml \
  scripts/homarr-board.py scripts/homarr-setup.sh README.md
git commit -m "Home Assistant: Homepage tile, Prometheus scrape, Homarr integration

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push
```
Wait for `kubectl -n argocd get app secret-stores home-assistant homepage` to show Synced/Healthy. Then:

1. `kubectl -n home-assistant get externalsecret home-assistant-prometheus -o jsonpath='{.status.conditions[0].reason}'`. Expected: `SecretSynced`.
2. Re-run the Step 1 query, filtering for health:
   ```bash
   kubectl -n monitoring exec sts/prometheus-kube-prometheus-stack-prometheus -c prometheus -- \
     wget -qO- 'http://localhost:9090/api/v1/targets?state=active' | python3 -c "import json,sys; print([t['health'] for t in json.load(sys.stdin)['data']['activeTargets'] if 'home-assistant' in t['labels'].get('namespace','')])"
   ```
   Expected: `['up']`.
3. `kubectl -n homepage rollout status deploy/homepage`. Homepage picks up the new env var on its rollout. If no rollout happened, `kubectl -n homepage rollout restart deploy/homepage`.
4. Run the Homarr board: `bash <scratchpad>/homarr-board-run.sh`. Expected: `integration Home Assistant: created (…)` and `board Platform: 17 items`.

- [ ] **Step 7: Visual audit (Playwright)**

Follow the `feedback-visual-audit` recipe:
- Screenshot `https://homepage.lab.internal`. The Platform group shows the Home Assistant tile with numbers (people home / lights on / switches on, likely 0s) and no "API Error".
- Screenshot the Homarr Platform board via `<scratchpad>/shot-run.sh <scratchpad>`. The Home Assistant launcher appears in row 0 and the other tiles are not displaced.
- Screenshot `https://home.lab.internal` after logging in as `jim` with the Vault password (read in-process, never printed). Settings → Devices & services lists UniFi Network, Synology DSM, Jellyfin, Sonarr and Radarr with no "Failed to set up" badges.

Delete the screenshots afterwards. Untracked `.playwright-mcp/` stays out of git.

---

## Spec coverage (self-review)

| Spec item | Task |
|---|---|
| App, hostNetwork, Recreate, Longhorn 5Gi, ConfigMap `configuration.yaml`, trusted proxies, includes, init seed | 1 |
| `home.lab.internal` route; UniFi DNS TODO; never public | 1 |
| Onboarding, Vault fields, long-lived token | 2 |
| Integrations: UniFi, Synology DSM, Jellyfin (new `homeassistant` user), Sonarr, Radarr | 2 |
| Homepage tile + ExternalSecret | 3 |
| Homarr integration + launcher | 3 |
| Prometheus integration (1) + ServiceMonitor + token ExternalSecret (3) | 1, 3 |
| README (LAN row, TODOs), CLAUDE.md line | 1, 2, 3 |
| Testing: Argo healthy, curl no 400, idempotent re-run, screenshots, Prometheus up | 1, 2, 3 |
| The spec's IP-plan note | 1 (the README LAN row states hostNetwork and port 8123 on the node; no new IP is used, so the Network table is unchanged) |
