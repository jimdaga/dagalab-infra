# Audiobooks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Audiobookshelf serving audiobooks + podcasts (public `books.dagaflix.com`, LAN `books.lab.internal`), Bookshelf requesting/downloading audiobooks through Prowlarr + qBittorrent, and a nightly Libation CronJob backing up the user's Audible library.

**Architecture:** Three Argo CD apps (bjw-s app-template 5.2.1) in namespace `media`, each running as DSM user `media` (1029:100) with SQLite/config on Longhorn. New folders on the existing `media-data` NFS claim (`audiobooks/`, `audible/`, `podcasts/`, `downloads/audiobooks/`); Audiobookshelf and Libation see only their folders (subPath mounts), Bookshelf the whole share (hardlinks). Bookshelf is wired like Sonarr by `scripts/arr-wire.py`; Audiobookshelf by a new `scripts/audiobookshelf-setup.{sh,py}`.

**Tech Stack:** Argo CD (app-of-apps, multi-source), app-template 5.2.1 (Deployment + CronJob controllers), Envoy Gateway (lan-gateway + shared-gateway charts), External Secrets + Vault, Prowlarr, qBittorrent, Homepage, Renovate.

**Spec:** `docs/superpowers/specs/2026-10-20-audiobooks-design.md`

## Global Constraints

- Commits go straight to `main` and are pushed (Argo CD syncs `main`). Trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Never commit or print secrets: API keys, passwords, Audible tokens, Vault tokens. Read `~/.vault-token` in-process only.
- Images (exact tags): `ghcr.io/advplyr/audiobookshelf:2.37.1`, `ghcr.io/pennydreadful/bookshelf:hardcover-v0.4.21.182`, `rmcrackan/libation:14.2.2`.
- All three apps: namespace `media`, sync wave `0`, app-template `5.2.1`, run as 1029:100, config/DB on Longhorn (never NFS).
- Folders inside `/data`: `audiobooks/` (Bookshelf root), `audible/` (Libation), `podcasts/` (Audiobookshelf), `downloads/audiobooks/` (qBittorrent category `audiobooks`). Owned by 1029:100.
- Never mount, move or modify `personal/`, `iMovie/`, `youtube/`.
- Audiobookshelf mounts: `/audiobooks` (ro), `/audible` (ro), `/podcasts` (rw), `/config` Longhorn 2Gi, `/metadata` Longhorn 10Gi; `PORT=13378`; Service port 80 → 13378.
- Bookshelf: `PUID=1029`, `PGID=100`, port 8787, `READARR__AUTH__METHOD: External`, API key Vault `secret/bookshelf` `api-key` → ExternalSecret `bookshelf` → `READARR__AUTH__APIKEY`. LAN only, never on the shared gateway.
- Libation CronJob: `30 3 * * *`, timeZone `America/New_York`, `concurrencyPolicy: Forbid`, `backoffLimit: 0`; `/config` Longhorn 1Gi; `/data` ← `audible/`.
- Hostnames: public `books.dagaflix.com` (Audiobookshelf); LAN `books.lab.internal` (Audiobookshelf), `bookshelf.lab.internal` (Bookshelf).
- Audiobookshelf must have its root user **before** the public route exists (its unauthenticated `POST /init` creates the root user on an uninitialised server).
- Prowlarr sync categories for Bookshelf: 3030, 3000. Quality profile "Audiobook": MP3 (10) and M4B (12) allowed, M4B preferred, cutoff M4B.
- kubectl context `dagalab`. No `python3 -m py_compile`. In zsh never name a variable `path`.
- `SP=/private/tmp/claude-501/-Users-jim-git-jimdaga-dagalab-infra/d4596aa3-a16a-4a5b-90c2-8f10bd5d68e9/scratchpad`; app-template 5.2.1 is unpacked at `$SP/chart/app-template`. Render tests live in `$SP` (not committed), like the Plex plan's.

## Spec corrections (rulings made while planning, from reading upstream sources)

1. Bookshelf has no audiobook "metadata profile": the format comes from the quality profile alone (README: one type per instance; audio-only qualities ⇒ audiobooks). The default metadata profile is used.
2. Libation's image copies `/config/Settings.json` into `/config-internal` and never writes it back, so settings come from a git ConfigMap (read-only subPath), not a seed init container. Account tokens in `/config/AccountsSettings.json` are likewise only read (each run refreshes the short-lived access token from the stored refresh token).
3. Homepage's Audiobookshelf key belongs to a dedicated non-admin user `homepage` (read-only permissions), not to root.
4. Audiobookshelf's login rate limiter keys on the leftmost `X-Forwarded-For`, which a client can forge; brute force is answered by long random passwords (spec "Security"), not by the limiter.

## Review Focus

1. The public route reaching an uninitialised Audiobookshelf (anyone could create the root user). Task 3 initialises before Task 4 adds the route; Task 4 Step 1's test asserts `/status` reports `isInit: true` through the public name.
2. Bookshelf mass-downloading an author's whole catalogue when one book is requested (ratio on private trackers). Task 2 sets the root folder's monitor options to `none`; Task 2 Step 1's check asserts them.
3. Audiobookshelf writing into `audiobooks/`/`audible/` (users deleting or "embedding metadata" into Bookshelf/Libation files). Task 3 Step 6 asserts writes fail there and succeed in `/podcasts`.
4. A failed `homepage` ExternalSecret sync breaking every Homepage widget (one missing Vault property fails the whole Secret). Task 4 adds the new keys only after Task 3 stored `homepage-key`, and Step 6 checks the ExternalSecret is `SecretSynced`.
5. Libation running as 1029 but unable to write its internal config dir, failing every night silently. Task 5 mounts an emptyDir at `/config-internal`, and Step 8 runs a real Job and checks its log for `liberating books` and exit 0.

---

### Task 1: Folders, Bookshelf app, Vault key, LAN route

**Files:**
- Create: `argocd/apps/bookshelf/application.yaml`, `argocd/apps/bookshelf/values.yaml`
- Modify: `helm/charts/secret-stores/values.yaml` (ExternalSecret `bookshelf`), `scripts/vault-bootstrap.sh` (generate `secret/bookshelf`), `helm/charts/lan-gateway/values.yaml` (route), `renovate.json` (tag versioning)
- Test: `$SP/bookshelf_render_test.py` (not committed)

**Interfaces:**
- Consumes: claim `media-data`; Vault kv `secret/`; ClusterSecretStore from `helm/charts/secret-stores`.
- Produces: Service `bookshelf` in `media`, port 8787 (`http://bookshelf.media.svc.cluster.local:8787`); Secret `bookshelf` with key `READARR__AUTH__APIKEY`; Vault `secret/bookshelf` `api-key`; folders `/data/{audiobooks,audible,podcasts,downloads/audiobooks}`.

- [ ] **Step 1: Write the render test**

`$SP/bookshelf_render_test.py`:

```python
import json, re, subprocess, sys, yaml
SP = sys.argv[1]
out = subprocess.run(["helm", "template", "bookshelf", f"{SP}/chart/app-template", "-n", "media",
                      "-f", "argocd/apps/bookshelf/values.yaml"], capture_output=True, text=True, check=True).stdout
docs = {(d["kind"], d["metadata"]["name"]): d for d in yaml.safe_load_all(out) if d}
dep = docs[("Deployment", "bookshelf")]
assert dep["spec"]["strategy"]["type"] == "Recreate"
c = dep["spec"]["template"]["spec"]["containers"][0]
assert c["image"] == "ghcr.io/pennydreadful/bookshelf:hardcover-v0.4.21.182", c["image"]
env = {e["name"]: e.get("value") for e in c["env"]}
assert env["PUID"] == "1029" and env["PGID"] == "100" and env["READARR__AUTH__METHOD"] == "External", env
assert c["envFrom"] == [{"secretRef": {"name": "bookshelf"}}], c.get("envFrom")
assert c["livenessProbe"]["httpGet"] == {"path": "/ping", "port": 8787}
mounts = {m["mountPath"]: m for m in c["volumeMounts"]}
assert "subPath" not in mounts["/data"] and not mounts["/data"].get("readOnly"), mounts["/data"]
vols = dep["spec"]["template"]["spec"]["volumes"]
assert any(v.get("persistentVolumeClaim", {}).get("claimName") == "media-data" for v in vols)
assert [p["port"] for p in docs[("Service", "bookshelf")]["spec"]["ports"]] == [8787]
# ExternalSecret, LAN route, Renovate rule
ss = yaml.safe_load(open("helm/charts/secret-stores/values.yaml"))
es = next(e for e in ss["externalSecrets"] if e["name"] == "bookshelf")
assert es["namespace"] == "media" and es["data"] == [{"secretKey": "READARR__AUTH__APIKEY", "key": "bookshelf", "property": "api-key"}], es
lan = yaml.safe_load(open("helm/charts/lan-gateway/values.yaml"))["routes"]
assert {"host": "bookshelf", "namespace": "media", "service": "bookshelf", "port": 8787} in lan
sh = yaml.safe_load(open("helm/charts/shared-gateway/values.yaml"))["routes"]
assert all(r["service"] != "bookshelf" for r in sh), "Bookshelf must never be public"
rule = next(r for r in json.load(open("renovate.json"))["packageRules"] if "ghcr.io/pennydreadful/bookshelf" in r.get("matchPackageNames", []))
rx = rule["versioning"].removeprefix("regex:").replace("(?<", "(?P<")
for tag in ("hardcover-v0.4.21.182", "hardcover-v0.4.21"):
    assert re.match(rx, tag), tag
assert not re.match(rx, "softcover-v0.4.21.182")
assert "bookshelf" in open("scripts/vault-bootstrap.sh").read().split("for app in", 1)[1].split(";", 1)[0]
print("bookshelf render: OK")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /Users/jim/git/jimdaga/dagalab-infra && python3 $SP/bookshelf_render_test.py $SP`
Expected: FAIL (`helm template` error: `argocd/apps/bookshelf/values.yaml` not found).

- [ ] **Step 3: Create the folders as the `media` user (root is squashed on NFS)**

```bash
kubectl -n media exec deploy/sonarr -c app -- s6-setuidgid abc sh -c \
  'mkdir -p /data/audiobooks /data/audible /data/podcasts /data/downloads/audiobooks && ls -ldn /data/audiobooks /data/audible /data/podcasts /data/downloads/audiobooks'
```
Expected: four directories owned by `1029 100`.

- [ ] **Step 4: Seed the API key in Vault (before the ExternalSecret exists, so it syncs on first try)**

```bash
T=$(cat ~/.vault-token)
kubectl -n vault exec -i vault-0 -- env VAULT_TOKEN="$T" vault kv metadata get secret/bookshelf >/dev/null 2>&1 \
  || kubectl -n vault exec -i vault-0 -- env VAULT_TOKEN="$T" vault kv put secret/bookshelf api-key="$(openssl rand -hex 16)" >/dev/null
kubectl -n vault exec -i vault-0 -- env VAULT_TOKEN="$T" vault kv get -format=json secret/bookshelf | python3 -c "import json,sys;print(sorted(json.load(sys.stdin)['data']['data']))"
```
Expected: `['api-key']` (value never printed).

In `scripts/vault-bootstrap.sh` change the loop line to:

```bash
for app in prowlarr radarr sonarr bookshelf gluetun; do   # gluetun: API key for its HTTP control server (VPN status)
```

- [ ] **Step 5: Write `argocd/apps/bookshelf/application.yaml`**

```yaml
---
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: bookshelf
  namespace: argocd
  finalizers:
    - resources-finalizer.argocd.argoproj.io
  annotations:
    # Wave 0: needs media-data (media-storage, wave -1) and its ExternalSecret (secret-stores, wave -5)
    argocd.argoproj.io/sync-wave: "0"
spec:
  project: default
  sources:
    - repoURL: ghcr.io/bjw-s-labs/helm
      chart: app-template
      targetRevision: "5.2.1"
      helm:
        releaseName: bookshelf
        valueFiles:
          - $values/argocd/apps/bookshelf/values.yaml
    - repoURL: https://github.com/jimdaga/dagalab-infra.git
      targetRevision: main
      ref: values
  destination:
    server: https://kubernetes.default.svc
    namespace: media
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

- [ ] **Step 6: Write `argocd/apps/bookshelf/values.yaml`**

```yaml
# Bookshelf: audiobook manager (Readarr fork, Hardcover metadata), the *arr for audiobooks. Root folder
# /data/audiobooks; qBittorrent category `audiobooks` lands in /data/downloads/audiobooks (same NFS share, so imports
# hardlink). Audiobooks only: the "Audiobook" quality profile allows just M4B/MP3 (scripts/arr-wire.py owns it;
# Recyclarr doesn't support Readarr-family apps). LAN only: https://bookshelf.lab.internal. Runs as DSM user media.
controllers:
  bookshelf:
    strategy: Recreate
    containers:
      app:
        image:
          repository: ghcr.io/pennydreadful/bookshelf
          tag: hardcover-v0.4.21.182
        env:
          TZ: America/New_York
          PUID: "1029"
          PGID: "100"
          # Same as Sonarr: LAN is trusted, auth handled outside the app (gateway-proxied requests are never "local").
          # MUST switch to Forms before this is ever routed on the public shared-gateway.
          READARR__AUTH__METHOD: External
          READARR__LOG__LEVEL: info
          READARR__LOG__ANALYTICSENABLED: "False"
        envFrom:
          - secretRef:
              name: bookshelf   # READARR__AUTH__APIKEY (Vault secret/bookshelf)
        probes:
          liveness: &probe
            enabled: true
            custom: true
            spec:
              httpGet:
                path: /ping
                port: 8787
              periodSeconds: 30
              failureThreshold: 5
          readiness: *probe
        resources:
          requests:
            cpu: 50m
            memory: 256Mi
          limits:
            memory: 1Gi

service:
  app:
    controller: bookshelf
    ports:
      http:
        port: 8787

persistence:
  # SQLite config DB: Longhorn, never NFS
  config:
    type: persistentVolumeClaim
    accessMode: ReadWriteOnce
    size: 5Gi
    globalMounts:
      - path: /config
  # Shared media share: library + downloads (helm/charts/media-storage)
  data:
    type: persistentVolumeClaim
    existingClaim: media-data
    globalMounts:
      - path: /data
```

- [ ] **Step 7: ExternalSecret, LAN route, Renovate rule**

In `helm/charts/secret-stores/values.yaml`, after the `sonarr` entry:

```yaml
  - name: bookshelf
    namespace: media
    data:
      - secretKey: READARR__AUTH__APIKEY   # Servarr env override for config.xml <ApiKey> (Bookshelf keeps Readarr's prefix)
        key: bookshelf
        property: api-key
```

In `helm/charts/lan-gateway/values.yaml`, after the `radarr` route:

```yaml
  - { host: bookshelf, namespace: media,          service: bookshelf,                     port: 8787 }
```

In `renovate.json` `packageRules`, after the Plex rule:

```json
    {
      "description": "Bookshelf tags are hardcover-v<version>[.<build>]: keep the hardcover- flavour and order by version+build.",
      "matchPackageNames": [
        "ghcr.io/pennydreadful/bookshelf"
      ],
      "versioning": "regex:^hardcover-v(?<major>\\d+)\\.(?<minor>\\d+)\\.(?<patch>\\d+)(\\.(?<build>\\d+))?$"
    }
```

- [ ] **Step 8: Run the render test**

Run: `python3 $SP/bookshelf_render_test.py $SP && make lint >/dev/null && make template >/dev/null && echo lint+template OK`
Expected: `bookshelf render: OK` and `lint+template OK`.

- [ ] **Step 9: Commit, push, sync, verify live**

```bash
git add argocd/apps/bookshelf helm/charts/secret-stores/values.yaml helm/charts/lan-gateway/values.yaml renovate.json scripts/vault-bootstrap.sh
git commit -m "Bookshelf: audiobook manager (Readarr fork), LAN at bookshelf.lab.internal

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push
kubectl -n argocd annotate app root argocd.argoproj.io/refresh=hard --overwrite
kubectl -n argocd annotate app secret-stores argocd.argoproj.io/refresh=hard --overwrite
kubectl -n media rollout status deploy/bookshelf --timeout=300s
kubectl -n media exec deploy/bookshelf -- sh -c 'ps -o user,comm | grep -i readarr; curl -s -o /dev/null -w "%{http_code}\n" -H "X-Api-Key: $READARR__AUTH__APIKEY" localhost:8787/api/v1/system/status'
curl -sk -o /dev/null -w "%{http_code}\n" https://bookshelf.lab.internal/
```
Expected: rollout succeeds; the process user is `abc` (uid 1029); API status `200` with the Vault key; the LAN URL answers `200` (the `bookshelf.lab.internal` UniFi record is the user's; if DNS isn't there yet, `curl --resolve bookshelf.lab.internal:443:192.168.3.201` instead).

---

### Task 2: Wire Bookshelf (download client, root folder, profile, Prowlarr)

**Files:**
- Modify: `scripts/arr-wire.py` (new Bookshelf section + Prowlarr application), `scripts/arr-run.sh` (BOOKSHELF_KEY + port-forward 18787)
- Test: `$SP/bookshelf_wire_test.py` (not committed)

**Interfaces:**
- Consumes: Task 1's Service `bookshelf:8787`, Secret `bookshelf` key `READARR__AUTH__APIKEY`; Prowlarr at `prowlarr.media.svc.cluster.local:9696`; qBittorrent `qbittorrent.media.svc.cluster.local:8080`.
- Produces: Bookshelf quality profile `Audiobook`, root folder `/data/audiobooks`, download client `qBittorrent` (category `audiobooks`), Prowlarr application `Readarr` named `Bookshelf`.

- [ ] **Step 1: Write the live-state check**

`$SP/bookshelf_wire_test.py` (run under `scripts/arr-run.sh`, so the keys and port-forwards exist):

```python
import json, os, urllib.request
def get(base, key, p):
    with urllib.request.urlopen(urllib.request.Request(base + p, headers={'X-Api-Key': key}), timeout=30) as r:
        return json.loads(r.read())
B, BK = 'http://127.0.0.1:18787/api/v1', os.environ['BOOKSHELF_KEY']
P, PK = 'http://127.0.0.1:19696/api/v1', os.environ['PROWLARR_KEY']
prof = next(p for p in get(B, BK, '/qualityprofile') if p['name'] == 'Audiobook')
flat = [c for i in prof['items'] for c in (i.get('items') or [i])]
allowed = [c['quality']['id'] for c in flat if c['allowed']]
assert allowed == [10, 12], allowed            # MP3 then M4B: later = preferred
assert prof['cutoff'] == 12 and prof['upgradeAllowed'], prof
root = next(r for r in get(B, BK, '/rootfolder') if r['path'].rstrip('/') == '/data/audiobooks')
assert root['defaultQualityProfileId'] == prof['id'], root
assert root['defaultMonitorOption'] == 'none' and root['defaultNewItemMonitorOption'] == 'none', root   # no catalogue grabs
dc = next(d for d in get(B, BK, '/downloadclient') if d['implementation'] == 'QBittorrent')
f = {x['name']: x.get('value') for x in dc['fields']}
assert f['musicCategory'] == 'audiobooks' and f['host'] == 'qbittorrent.media.svc.cluster.local', f
assert get(B, BK, '/config/mediamanagement')['copyUsingHardlinks'] is True
assert get(B, BK, '/config/naming')['renameBooks'] is True
app = next(a for a in get(P, PK, '/applications') if a['implementation'] == 'Readarr')
af = {x['name']: x.get('value') for x in app['fields']}
assert af['baseUrl'] == 'http://bookshelf.media.svc.cluster.local:8787' and sorted(af['syncCategories']) == [3000, 3030], af
ix = get(B, BK, '/indexer')
assert len(ix) >= 3 and all(i['enableRss'] or i['enableAutomaticSearch'] for i in ix), [i['name'] for i in ix]
print('bookshelf wiring: OK', [i['name'] for i in ix])
```

- [ ] **Step 2: Add Bookshelf to `scripts/arr-run.sh`, run the check, verify it fails**

In `scripts/arr-run.sh`: add `BOOKSHELF_KEY=$(key bookshelf READARR__AUTH__APIKEY)` to the `export` line; add `kubectl -n media port-forward svc/bookshelf 18787:8787 >/dev/null 2>&1 & p5=$!`; add `$p5` to the `trap`; add `18787` to the `/ping` wait loop. Update its header comment to name Bookshelf.

Run: `scripts/arr-run.sh $SP/bookshelf_wire_test.py`
Expected: FAIL with `StopIteration` (no `Audiobook` profile yet).

- [ ] **Step 3: Add the Bookshelf section to `scripts/arr-wire.py`**

Docstring: add `BOOKSHELF_KEY` to Env and `bookshelf 18787` to Ports. Insert after the Radarr/Sonarr loop (before the `# Prowlarr -> Radarr/Sonarr applications` block):

```python
# Bookshelf (Readarr fork, audiobooks only): /api/v1 and Readarr field names, so it gets its own section.
# Audiobooks-only comes from the quality profile: only MP3/M4B allowed, M4B preferred. Recyclarr doesn't support
# Readarr-family apps, so this script owns that profile (unlike Sonarr/Radarr's).
BOOKSHELF = dict(url='http://127.0.0.1:18787/api/v1', key=os.environ['BOOKSHELF_KEY'], root='/data/audiobooks', cat='audiobooks', port=8787)
AUDIO = [10, 12]   # MP3, M4B: profile items are ordered lowest -> highest preference
b, bk = BOOKSHELF['url'], BOOKSHELF['key']
st, status = call(b, bk, 'GET', '/system/status')
print(f'== bookshelf {status.get("version") if st == 200 else st}')

st, profs = call(b, bk, 'GET', '/qualityprofile')
prof = next((x for x in profs if x['name'] == 'Audiobook'), None)
flat = lambda items: [c for i in items for c in (i.get('items') or [i])]
if prof and [c['quality']['id'] for c in flat(prof['items']) if c['allowed']] == AUDIO and prof['cutoff'] == 12:
    print('   quality profile Audiobook: present')
else:
    st, schema = call(b, bk, 'GET', '/qualityprofile/schema')
    items = flat(schema['items'])
    for c in items:
        c['allowed'] = c['quality']['id'] in AUDIO
    items = [c for c in items if c['quality']['id'] not in AUDIO] + [next(c for c in items if c['quality']['id'] == q) for q in AUDIO]
    body = dict(schema, name='Audiobook', upgradeAllowed=True, cutoff=12, items=items)
    if prof:
        body['id'] = prof['id']
    st, r = call(b, bk, 'PUT' if prof else 'POST', f'/qualityprofile/{prof["id"]}' if prof else '/qualityprofile', body)
    print(f'   quality profile Audiobook: {"updated" if prof else "created"} (HTTP {st})' if st in (200, 201, 202) else f'   quality profile FAILED {st}: {r}')
    ok &= st in (200, 201, 202)
    st, profs = call(b, bk, 'GET', '/qualityprofile')
    prof = next(x for x in profs if x['name'] == 'Audiobook')

# root folder: monitor nothing by default, so requesting one book never grabs the author's whole catalogue
st, roots = call(b, bk, 'GET', '/rootfolder')
if any(r['path'].rstrip('/') == BOOKSHELF['root'] for r in roots):
    print(f'   root folder {BOOKSHELF["root"]}: present')
else:
    st, mprofs = call(b, bk, 'GET', '/metadataprofile')
    body = dict(name='Audiobooks', path=BOOKSHELF['root'], defaultQualityProfileId=prof['id'],
                defaultMetadataProfileId=mprofs[0]['id'], defaultMonitorOption='none', defaultNewItemMonitorOption='none',
                defaultTags=[], isCalibreLibrary=False)
    st, r = call(b, bk, 'POST', '/rootfolder', body)
    print(f'   root folder {BOOKSHELF["root"]}: created (HTTP {st})' if st in (200, 201) else f'   root folder FAILED {st}: {r}'); ok &= st in (200, 201)

for cfg, want in (('/config/naming', dict(renameBooks=True, replaceIllegalCharacters=True)),
                  ('/config/mediamanagement', dict(copyUsingHardlinks=True, deleteEmptyFolders=True))):
    st, cur = call(b, bk, 'GET', cfg)
    missing = [f for f in want if f not in cur]
    assert not missing, f'{cfg}: no field(s) {missing}'
    if any(cur.get(f) != v for f, v in want.items()):
        cur.update(want)
        st, r = call(b, bk, 'PUT', f'{cfg}/{cur["id"]}', cur)
        print(f'   {cfg}: updated (HTTP {st})' if st in (200, 202) else f'   {cfg} FAILED {st}: {r}'); ok &= st in (200, 202)
    else:
        print(f'   {cfg}: already set')

st, dcs = call(b, bk, 'GET', '/downloadclient')
if any(d['implementation'] == 'QBittorrent' for d in dcs):
    print('   download client qBittorrent: present')
else:
    st, schema = call(b, bk, 'GET', '/downloadclient/schema')
    dc = next(s for s in schema if s['implementation'] == 'QBittorrent')
    dc.update(name='qBittorrent', enable=True, priority=1)
    setf(dc['fields'], 'host', f'qbittorrent.{SVC}')
    setf(dc['fields'], 'port', 8080)
    setf(dc['fields'], 'useSsl', False)
    setf(dc['fields'], 'musicCategory', BOOKSHELF['cat'])   # Readarr's name for the category field
    st, r = call(b, bk, 'POST', '/downloadclient/test', dc)
    if st not in (200, 202):
        print(f'   download client test FAILED {st}: {r}'); ok = False
    else:
        st, r = call(b, bk, 'POST', '/downloadclient', dc)
        print(f'   download client qBittorrent ({BOOKSHELF["cat"]}): created (HTTP {st})' if st in (200, 201) else f'   create FAILED {st}: {r}'); ok &= st in (200, 201)
```

In the Prowlarr applications block, after the `for name, a in APPS.items():` loop, add:

```python
# Bookshelf registers as a Readarr app; audiobook (3030) + general audio (3000: TorrentLeech has no audiobook category)
if any(e['implementation'] == 'Readarr' for e in existing):
    print('   application Bookshelf (Readarr): present')
else:
    app = next(s for s in schema if s['implementation'] == 'Readarr')
    app.update(name='Bookshelf', syncLevel='fullSync')
    setf(app['fields'], 'prowlarrUrl', f'http://prowlarr.{SVC}:9696')
    setf(app['fields'], 'baseUrl', f'http://bookshelf.{SVC}:{BOOKSHELF["port"]}')
    setf(app['fields'], 'apiKey', BOOKSHELF['key'])
    setf(app['fields'], 'syncCategories', [3030, 3000])
    st, r = call(p['url'], p['key'], 'POST', '/applications/test', app)
    if st not in (200, 202):
        print(f'   application Bookshelf test FAILED {st}: {r}'); ok = False
    else:
        st, r = call(p['url'], p['key'], 'POST', '/applications', app)
        print(f'   application Bookshelf: created (HTTP {st})' if st in (200, 201) else f'   create FAILED {st}: {r}'); ok &= st in (200, 201)
```

In the final health loop add Bookshelf: change the dict to `{**{n: (a['url'], a['key']) for n, a in APPS.items()}, 'bookshelf': (b, bk), 'prowlarr': (p['url'], p['key'])}`.

- [ ] **Step 4: Run the wiring, then trigger an indexer sync**

```bash
scripts/arr-run.sh scripts/arr-wire.py; echo exit=$?
```
Expected: Radarr/Sonarr lines all `present`/`already set` (unchanged); Bookshelf lines `created`; `application Bookshelf: created`; `exit=0`. Then push indexers now instead of waiting for Prowlarr's schedule: run `$SP/prowlarr_sync.py` under `arr-run.sh`:

```python
import json, os, urllib.request
req = urllib.request.Request('http://127.0.0.1:19696/api/v1/command', method='POST',
      headers={'X-Api-Key': os.environ['PROWLARR_KEY'], 'Content-Type': 'application/json'},
      data=json.dumps({'name': 'ApplicationIndexerSync'}).encode())
print(urllib.request.urlopen(req, timeout=30).status)
```
Expected: `201`.

- [ ] **Step 5: Re-run the check**

Run: `scripts/arr-run.sh $SP/bookshelf_wire_test.py`
Expected: `bookshelf wiring: OK ['IPTorrents (Prowlarr)', 'TorrentLeech (Prowlarr)', 'wigornot (Prowlarr)']` (names may differ in suffix; three indexers). Re-run `scripts/arr-run.sh scripts/arr-wire.py` once more: every Bookshelf line now `present`/`already set` (idempotent).

- [ ] **Step 6: Commit and push**

```bash
git add scripts/arr-wire.py scripts/arr-run.sh
git commit -m "arr-wire: Bookshelf (Audiobook profile, /data/audiobooks, qBittorrent audiobooks, Prowlarr)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push
```

---

### Task 3: Audiobookshelf app, LAN route, initialisation

**Files:**
- Create: `argocd/apps/audiobookshelf/application.yaml`, `argocd/apps/audiobookshelf/values.yaml`, `scripts/audiobookshelf-setup.sh`, `scripts/audiobookshelf-setup.py`
- Modify: `helm/charts/lan-gateway/values.yaml`
- Test: `$SP/abs_render_test.py`, `$SP/abs_live_test.sh` (not committed)

**Interfaces:**
- Consumes: Task 1's folders `audiobooks/`, `audible/`, `podcasts/`.
- Produces: Service `audiobookshelf` in `media`, port 80 → 13378 (`http://audiobookshelf.media.svc.cluster.local`); Vault `secret/audiobookshelf` keys `root-password`, `homepage-key`; libraries `Audiobooks` (`/audiobooks` + `/audible`, hourly scan) and `Podcasts` (`/podcasts`); ABS users `jim` (root) and `homepage` (read-only).

- [ ] **Step 1: Write the render test**

`$SP/abs_render_test.py`:

```python
import subprocess, sys, yaml
SP = sys.argv[1]
out = subprocess.run(["helm", "template", "audiobookshelf", f"{SP}/chart/app-template", "-n", "media",
                      "-f", "argocd/apps/audiobookshelf/values.yaml"], capture_output=True, text=True, check=True).stdout
docs = [d for d in yaml.safe_load_all(out) if d]
dep = next(d for d in docs if d["kind"] == "Deployment")
assert dep["spec"]["strategy"]["type"] == "Recreate"
pod = dep["spec"]["template"]["spec"]
sc = pod["securityContext"]
assert (sc["runAsUser"], sc["runAsGroup"], sc["fsGroup"]) == (1029, 100, 100), sc
c = pod["containers"][0]
assert c["image"] == "ghcr.io/advplyr/audiobookshelf:2.37.1", c["image"]
env = {e["name"]: e.get("value") for e in c["env"]}
assert env["PORT"] == "13378", env
assert c["livenessProbe"]["httpGet"] == {"path": "/healthcheck", "port": 13378}
m = {x["mountPath"]: x for x in c["volumeMounts"]}
assert m["/audiobooks"]["subPath"] == "audiobooks" and m["/audiobooks"]["readOnly"] is True
assert m["/audible"]["subPath"] == "audible" and m["/audible"]["readOnly"] is True
assert m["/podcasts"]["subPath"] == "podcasts" and not m["/podcasts"].get("readOnly")
assert {"/config", "/metadata"} <= set(m) and "/data" not in m
assert sorted(p["spec"]["resources"]["requests"]["storage"] for p in docs if p["kind"] == "PersistentVolumeClaim") == ["10Gi", "2Gi"]
svc = next(d for d in docs if d["kind"] == "Service")
assert [(p["port"], p["targetPort"]) for p in svc["spec"]["ports"]] == [(80, 13378)], svc["spec"]["ports"]
lan = yaml.safe_load(open("helm/charts/lan-gateway/values.yaml"))["routes"]
assert {"host": "books", "namespace": "media", "service": "audiobookshelf", "port": 80} in lan
sh = yaml.safe_load(open("helm/charts/shared-gateway/values.yaml"))["routes"]
assert all(r["service"] != "audiobookshelf" for r in sh), "public route belongs to Task 4 (after init)"
print("audiobookshelf render: OK")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 $SP/abs_render_test.py $SP`
Expected: FAIL (`argocd/apps/audiobookshelf/values.yaml` not found).

- [ ] **Step 3: Write `argocd/apps/audiobookshelf/application.yaml`**

Same as Task 1 Step 5's Application with `name: audiobookshelf`, `releaseName: audiobookshelf`, values path `$values/argocd/apps/audiobookshelf/values.yaml`, and the wave comment `# Wave 0: needs media-data (media-storage, wave -1)`. Full file:

```yaml
---
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: audiobookshelf
  namespace: argocd
  finalizers:
    - resources-finalizer.argocd.argoproj.io
  annotations:
    # Wave 0: needs media-data (media-storage, wave -1)
    argocd.argoproj.io/sync-wave: "0"
spec:
  project: default
  sources:
    - repoURL: ghcr.io/bjw-s-labs/helm
      chart: app-template
      targetRevision: "5.2.1"
      helm:
        releaseName: audiobookshelf
        valueFiles:
          - $values/argocd/apps/audiobookshelf/values.yaml
    - repoURL: https://github.com/jimdaga/dagalab-infra.git
      targetRevision: main
      ref: values
  destination:
    server: https://kubernetes.default.svc
    namespace: media
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

- [ ] **Step 4: Write `argocd/apps/audiobookshelf/values.yaml` and the LAN route**

```yaml
# Audiobookshelf: audiobook + podcast server with phone apps and per-user progress. Public https://books.dagaflix.com
# (shared-gateway), LAN https://books.lab.internal. Its own accounts (no Jellyfin sign-in): root `jim` + one per
# person, long random passwords. Libraries: Audiobooks = /audiobooks (Bookshelf) + /audible (Libation), both
# READ-ONLY here; Podcasts = /podcasts (written by Audiobookshelf). Only those folders of media-data are mounted.
# First run + libraries + Homepage key: scripts/audiobookshelf-setup.sh (must run before the public route exists).
controllers:
  audiobookshelf:
    strategy: Recreate   # never two instances on one SQLite DB
    pod:
      securityContext:   # DSM user media; non-root, hence PORT 13378 (can't bind 80)
        runAsUser: 1029
        runAsGroup: 100
        fsGroup: 100
        fsGroupChangePolicy: OnRootMismatch
    containers:
      app:
        image:
          repository: ghcr.io/advplyr/audiobookshelf
          tag: 2.37.1
        env:
          TZ: America/New_York
          PORT: "13378"
        probes:
          liveness: &probe
            enabled: true
            custom: true
            spec:
              httpGet:
                path: /healthcheck
                port: 13378
              periodSeconds: 30
              failureThreshold: 5
          readiness: *probe
        resources:
          requests:
            cpu: 100m
            memory: 256Mi
          limits:
            memory: 1Gi

service:
  app:
    controller: audiobookshelf
    ports:
      http:
        port: 80
        targetPort: 13378

persistence:
  # SQLite DB: Longhorn, never NFS
  config:
    type: persistentVolumeClaim
    accessMode: ReadWriteOnce
    size: 2Gi
    globalMounts:
      - path: /config
  # covers, cache, backups
  metadata:
    type: persistentVolumeClaim
    accessMode: ReadWriteOnce
    size: 10Gi
    globalMounts:
      - path: /metadata
  # only the audio folders of the media share (helm/charts/media-storage); books arrive via Bookshelf/Libation
  media:
    type: persistentVolumeClaim
    existingClaim: media-data
    globalMounts:
      - { path: /audiobooks, subPath: audiobooks, readOnly: true }
      - { path: /audible,    subPath: audible,    readOnly: true }
      - { path: /podcasts,   subPath: podcasts }
```

In `helm/charts/lan-gateway/values.yaml`, after the `seerr` route:

```yaml
  - { host: books,    namespace: media,           service: audiobookshelf,                port: 80 }
```

- [ ] **Step 5: Run the render test, commit, push, sync**

```bash
python3 $SP/abs_render_test.py $SP && make lint >/dev/null && make template >/dev/null && echo OK
git add argocd/apps/audiobookshelf helm/charts/lan-gateway/values.yaml
git commit -m "Audiobookshelf: audiobooks + podcasts server, LAN at books.lab.internal

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push
kubectl -n argocd annotate app root argocd.argoproj.io/refresh=hard --overwrite
kubectl -n media rollout status deploy/audiobookshelf --timeout=300s
```
Expected: `audiobookshelf render: OK`, `OK`, rollout succeeds.

- [ ] **Step 6: Write the live permission test, watch it pass for the mounts**

`$SP/abs_live_test.sh`:

```bash
set -u
x() { kubectl -n media exec deploy/audiobookshelf -- sh -c "$1"; }
[ "$(x 'id -u'):$(x 'id -g')" = "1029:100" ] || { echo "FAIL uid"; exit 1; }
for d in /audiobooks /audible; do x "touch $d/.w 2>/dev/null" && { echo "FAIL $d writable"; exit 1; }; done
x 'touch /podcasts/.w && rm /podcasts/.w' || { echo "FAIL /podcasts not writable"; exit 1; }
x 'ls /data 2>/dev/null' && { echo "FAIL /data visible"; exit 1; }
echo "abs mounts: OK"
```
Run: `bash $SP/abs_live_test.sh`
Expected: `abs mounts: OK`.

- [ ] **Step 7: Write the setup script's check (fails: server not initialised)**

`$SP/abs_setup_test.py` (run with `ABS_URL=http://127.0.0.1:13378` and a port-forward `kubectl -n media port-forward svc/audiobookshelf 13378:80`):

```python
import json, os, subprocess, urllib.request
U = os.environ['ABS_URL']
def j(p, tok=None, body=None, hdr=None):
    h = {'Content-Type': 'application/json', **({'Authorization': f'Bearer {tok}'} if tok else {}), **(hdr or {})}
    req = urllib.request.Request(U + p, method='POST' if body is not None else 'GET', headers=h,
                                 data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req, timeout=30) as r: return json.loads(r.read())
assert j('/status')['isInit'] is True, 'not initialised'
v = lambda f: subprocess.run(['kubectl', '-n', 'vault', 'exec', '-i', 'vault-0', '--', 'env', f'VAULT_TOKEN={open(os.path.expanduser("~/.vault-token")).read().strip()}',
                              'vault', 'kv', 'get', f'-field={f}', 'secret/audiobookshelf'], capture_output=True, text=True, check=True).stdout
tok = j('/login', body={'username': 'jim', 'password': v('root-password')}, hdr={'x-return-tokens': 'true'})['user']['accessToken']
libs = {l['name']: l for l in j('/api/libraries', tok)['libraries']}
a, p = libs['Audiobooks'], libs['Podcasts']
assert sorted(f['fullPath'] for f in a['folders']) == ['/audible', '/audiobooks'] and a['mediaType'] == 'book', a['folders']
assert a['settings']['autoScanCronExpression'] == '0 * * * *' and a['settings']['disableWatcher'] is True, a['settings']
assert [f['fullPath'] for f in p['folders']] == ['/podcasts'] and p['mediaType'] == 'podcast'
users = {u['username']: u for u in j('/api/users', tok)['users']}
h = users['homepage']
assert h['type'] == 'user' and not any(h['permissions'][k] for k in ('update', 'delete', 'upload', 'download')), h['permissions']
hk = v('homepage-key')
assert j('/api/libraries', hk)['libraries'], 'homepage key cannot read libraries'
print('audiobookshelf setup: OK')
```
Run: `kubectl -n media port-forward svc/audiobookshelf 13378:80 >/dev/null 2>&1 & sleep 2; ABS_URL=http://127.0.0.1:13378 python3 $SP/abs_setup_test.py; kill %1`
Expected: FAIL `AssertionError: not initialised`.

- [ ] **Step 8: Write `scripts/audiobookshelf-setup.sh`**

```bash
#!/usr/bin/env bash
# Configure Audiobookshelf: root user `jim` (password generated once into Vault secret/audiobookshelf root-password,
# BEFORE /init, so it's never lost), libraries Audiobooks (/audiobooks + /audible, hourly scan: no inotify on NFS) and
# Podcasts (/podcasts), a read-only `homepage` user + API key (secret/audiobookshelf homepage-key).
#   VAULT_TOKEN=<root or admin token> ./scripts/audiobookshelf-setup.sh
# Idempotent. Run it before the public route (shared-gateway) exists: an uninitialised server lets anyone create root.
set -euo pipefail
: "${VAULT_TOKEN:?}"
v() { kubectl -n vault exec -i vault-0 -- env VAULT_TOKEN="$VAULT_TOKEN" vault "$@"; }
field() { v kv get -field="$1" secret/audiobookshelf 2>/dev/null || true; }

pw=$(field root-password)
if [[ -z "$pw" ]]; then
  pw=$(openssl rand -base64 30 | tr -d '/+=' | cut -c1-32)
  v kv put secret/audiobookshelf root-password="$pw" >/dev/null
  echo "generated secret/audiobookshelf root-password" >&2
fi
export ABS_ROOT_PASSWORD="$pw" ABS_HAVE_HOMEPAGE_KEY=$([[ -n "$(field homepage-key)" ]] && echo 1 || echo 0)

kubectl -n media port-forward svc/audiobookshelf 13378:80 >/dev/null 2>&1 & pf=$!
trap 'kill $pf 2>/dev/null' EXIT
for _ in $(seq 1 30); do curl -s -o /dev/null http://127.0.0.1:13378/healthcheck && break; sleep 0.5; done

out=$(python3 "$(dirname "$0")/audiobookshelf-setup.py")
key=$(sed -n 's/^HOMEPAGE_KEY=//p' <<<"$out")
if [[ -n "$key" ]]; then
  v kv patch secret/audiobookshelf homepage-key="$key" >/dev/null
  echo "stored Audiobookshelf homepage API key in secret/audiobookshelf" >&2
fi
```

- [ ] **Step 9: Write `scripts/audiobookshelf-setup.py`**

```python
#!/usr/bin/env python3
"""Idempotent Audiobookshelf setup over its API (port-forward on 127.0.0.1:13378). Called by audiobookshelf-setup.sh.
Env: ABS_ROOT_PASSWORD, ABS_HAVE_HOMEPAGE_KEY (1 = already in Vault). Prints HOMEPAGE_KEY=... when it creates one."""
import json, os, secrets, sys, urllib.error, urllib.request

U = 'http://127.0.0.1:13378'
log = lambda m: print(m, file=sys.stderr)

def call(method, path, body=None, tok=None, hdr=None):
    h = {'Content-Type': 'application/json', **({'Authorization': f'Bearer {tok}'} if tok else {}), **(hdr or {})}
    req = urllib.request.Request(U + path, method=method, headers=h, data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw[:1] in (b'{', b'[') else raw.decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:400]

# 1) root user (only possible while the server has none)
st, s = call('GET', '/status')
if not s['isInit']:
    st, r = call('POST', '/init', {'newRoot': {'username': 'jim', 'password': os.environ['ABS_ROOT_PASSWORD']}})
    if st != 200:
        sys.exit(f'init failed: {st} {r}')
    log('initialised: root user jim')
else:
    log('already initialised')
st, r = call('POST', '/login', {'username': 'jim', 'password': os.environ['ABS_ROOT_PASSWORD']}, hdr={'x-return-tokens': 'true'})
if st != 200:
    sys.exit(f'login as jim failed: {st} {r}')
tok = r['user']['accessToken']

# 2) libraries. The watcher can't see NFS changes: scan hourly instead (Audiobooks); Podcasts are downloaded by ABS itself.
LIBS = [
    dict(name='Audiobooks', mediaType='book', icon='audiobookshelf', folders=[{'fullPath': '/audiobooks'}, {'fullPath': '/audible'}],
         settings={'autoScanCronExpression': '0 * * * *', 'disableWatcher': True}),
    dict(name='Podcasts', mediaType='podcast', icon='podcast', folders=[{'fullPath': '/podcasts'}],
         settings={'disableWatcher': True}),
]
st, r = call('GET', '/api/libraries', tok=tok)
have = {l['name']: l for l in r['libraries']}
for lib in LIBS:
    cur = have.get(lib['name'])
    if cur is None:
        st, r = call('POST', '/api/libraries', lib, tok=tok)
        log(f'library {lib["name"]}: ' + ('created' if st == 200 else f'FAILED {st} {r}'))
    elif sorted(f['fullPath'] for f in cur['folders']) != sorted(f['fullPath'] for f in lib['folders']) \
            or any(cur['settings'].get(k) != v for k, v in lib['settings'].items()):
        st, r = call('PATCH', f'/api/libraries/{cur["id"]}', {'folders': lib['folders'], 'settings': lib['settings']}, tok=tok)
        log(f'library {lib["name"]}: ' + ('updated' if st == 200 else f'FAILED {st} {r}'))
    else:
        log(f'library {lib["name"]}: present')

# 3) read-only `homepage` user + API key for the Homepage widget
st, r = call('GET', '/api/users', tok=tok)
user = next((u for u in r['users'] if u['username'] == 'homepage'), None)
if user is None:
    perms = dict(download=False, update=False, delete=False, upload=False, accessAllLibraries=True, accessAllTags=True,
                 accessExplicitContent=True)
    st, r = call('POST', '/api/users', {'username': 'homepage', 'password': secrets.token_urlsafe(32), 'type': 'user',
                                        'isActive': True, 'permissions': perms}, tok=tok)
    if st != 200:
        sys.exit(f'create homepage user failed: {st} {r}')
    user = r['user']
    log('user homepage: created (read-only)')
else:
    log('user homepage: present')
if os.environ.get('ABS_HAVE_HOMEPAGE_KEY') != '1':
    st, r = call('POST', '/api/api-keys', {'name': 'homepage', 'userId': user['id'], 'isActive': True}, tok=tok)
    if st != 200:
        sys.exit(f'create API key failed: {st} {r}')
    print(f"HOMEPAGE_KEY={r['apiKey']['apiKey']}")
    log('api key homepage: created')
else:
    log('api key homepage: already in Vault')
```

- [ ] **Step 10: Run the setup, then the check**

```bash
chmod +x scripts/audiobookshelf-setup.sh scripts/audiobookshelf-setup.py
VAULT_TOKEN=$(cat ~/.vault-token) scripts/audiobookshelf-setup.sh
kubectl -n media port-forward svc/audiobookshelf 13378:80 >/dev/null 2>&1 & sleep 2; ABS_URL=http://127.0.0.1:13378 python3 $SP/abs_setup_test.py; kill %1
VAULT_TOKEN=$(cat ~/.vault-token) scripts/audiobookshelf-setup.sh   # second run: idempotent
```
Expected: first run logs `initialised`, `library ... created` ×2, `user homepage: created`, `api key homepage: created`, `stored ... homepage API key`; check prints `audiobookshelf setup: OK`; second run logs `already initialised`, `present` everywhere, `already in Vault`.

- [ ] **Step 11: Commit and push**

```bash
git add scripts/audiobookshelf-setup.sh scripts/audiobookshelf-setup.py
git commit -m "Audiobookshelf setup script: root user, libraries (hourly scan), read-only homepage key

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push
```

---

### Task 4: Public route and Homepage tiles

**Files:**
- Modify: `helm/charts/shared-gateway/values.yaml`, `helm/charts/secret-stores/values.yaml` (homepage ExternalSecret), `argocd/apps/homepage/values.yaml`
- Test: `$SP/abs_public_test.sh` (not committed)

**Interfaces:**
- Consumes: Task 3's Service `audiobookshelf:80`, Vault `secret/audiobookshelf` `homepage-key`; Task 1's Vault `secret/bookshelf` `api-key`.
- Produces: `https://books.dagaflix.com`; Homepage env `HOMEPAGE_VAR_AUDIOBOOKSHELF_KEY`, `HOMEPAGE_VAR_BOOKSHELF_KEY`.

- [ ] **Step 1: Write the public check**

`$SP/abs_public_test.sh`:

```bash
set -u
st=$(curl -s https://books.dagaflix.com/status) || { echo "FAIL unreachable"; exit 1; }
python3 -c "import json,sys; d=json.loads(sys.argv[1]); assert d['isInit'] is True and d['app']=='audiobookshelf', d" "$st" || { echo "FAIL status"; exit 1; }
[ "$(curl -s -o /dev/null -w '%{http_code}' https://books.dagaflix.com/api/libraries)" = 401 ] || { echo "FAIL unauthenticated API not 401"; exit 1; }
[ "$(curl -s -o /dev/null -w '%{http_code}' https://books.lab.internal/healthcheck)" = 200 ] || { echo "FAIL LAN"; exit 1; }
curl -s https://made-up-name.dagaflix.com/ | grep -q audiobookshelf && { echo "FAIL placeholder leaks"; exit 1; }
echo "abs public: OK"
```
Run: `bash $SP/abs_public_test.sh`
Expected: `FAIL status` (the public name still gets the placeholder page).

- [ ] **Step 2: Add the public route**

In `helm/charts/shared-gateway/values.yaml`, change the comment line about Seerr and the routes to:

```yaml
# Seerr (requests) signs in with the same Jellyfin accounts; its local-account login is off (scripts/seerr-setup.py).
# Audiobookshelf (books) uses its own accounts; it was initialised (root user) before this route existed.
routes:
  - { name: jellyfin,       host: popcorn,  namespace: media, service: jellyfin,       port: 8096 }
  - { name: seerr,          host: requests, namespace: media, service: seerr,          port: 5055 }
  - { name: audiobookshelf, host: books,    namespace: media, service: audiobookshelf, port: 80 }
```
And the header comment `(Jellyfin, Seerr)` → `(Jellyfin, Seerr, Audiobookshelf)`.

- [ ] **Step 3: Homepage keys and tiles**

In `helm/charts/secret-stores/values.yaml`, `homepage` ExternalSecret, after `HOMEPAGE_VAR_SEERR_KEY`:

```yaml
      - secretKey: HOMEPAGE_VAR_AUDIOBOOKSHELF_KEY   # read-only `homepage` user's API key (scripts/audiobookshelf-setup.sh)
        key: audiobookshelf
        property: homepage-key
      - secretKey: HOMEPAGE_VAR_BOOKSHELF_KEY   # read-only use by Homepage widgets
        key: bookshelf
        property: api-key
```

In `argocd/apps/homepage/values.yaml`, Watch group, after the Plex tile:

```yaml
            - Audiobookshelf:
                href: https://books.lab.internal
                icon: audiobookshelf.png
                description: Audiobooks + podcasts
                namespace: media
                app: audiobookshelf
                siteMonitor: http://audiobookshelf.media.svc.cluster.local/healthcheck
                widget:
                  type: audiobookshelf
                  url: http://audiobookshelf.media.svc.cluster.local
                  key: "{{ "{{HOMEPAGE_VAR_AUDIOBOOKSHELF_KEY}}" }}"
```

Library group, after the Radarr tile:

```yaml
            - Bookshelf:
                href: https://bookshelf.lab.internal
                icon: readarr.png
                description: Audiobooks
                namespace: media
                app: bookshelf
                siteMonitor: http://bookshelf.media.svc.cluster.local:8787/ping
                widget:
                  type: readarr
                  url: http://bookshelf.media.svc.cluster.local:8787
                  key: "{{ "{{HOMEPAGE_VAR_BOOKSHELF_KEY}}" }}"
```

- [ ] **Step 4: Render, commit, push, sync**

```bash
make lint >/dev/null && make template >/dev/null && echo OK
git add helm/charts/shared-gateway/values.yaml helm/charts/secret-stores/values.yaml argocd/apps/homepage/values.yaml
git commit -m "Audiobookshelf public at books.dagaflix.com; Homepage tiles for Audiobookshelf + Bookshelf

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push
for a in secret-stores shared-gateway homepage; do kubectl -n argocd annotate app $a argocd.argoproj.io/refresh=hard --overwrite; done
```

- [ ] **Step 5: Run the public check**

Run (after the route shows `Accepted`): `kubectl -n media get httproute audiobookshelf -o jsonpath='{.status.parents[0].conditions[*].status}'; bash $SP/abs_public_test.sh`
Expected: `True True` and `abs public: OK`.

- [ ] **Step 6: Homepage secret + visual check**

```bash
kubectl -n homepage get externalsecret homepage -o jsonpath='{.status.conditions[0].reason}{"\n"}'
kubectl -n homepage rollout restart deploy/homepage && kubectl -n homepage rollout status deploy/homepage --timeout=180s
```
Expected: `SecretSynced`. Then Playwright: open `https://homepage.lab.internal`, screenshot to `.playwright-mcp/homepage-audiobooks.png`, read it: Audiobookshelf tile (Watch) and Bookshelf tile (Library) present, both widgets show numbers (not "API Error"), site monitors green.

---

### Task 5: Libation CronJob and Audible sign-in

**Files:**
- Create: `argocd/apps/libation/application.yaml`, `argocd/apps/libation/values.yaml`
- Test: `$SP/libation_render_test.py` (not committed)

**Interfaces:**
- Consumes: Task 1's folder `audible/`.
- Produces: CronJob `libation` in `media`; Longhorn PVC holding `/config` (Audible account); books under `/data/audible/<first author>/<title short>/`.

- [ ] **Step 1: Write the render test**

`$SP/libation_render_test.py`:

```python
import json, subprocess, sys, yaml
SP = sys.argv[1]
out = subprocess.run(["helm", "template", "libation", f"{SP}/chart/app-template", "-n", "media",
                      "-f", "argocd/apps/libation/values.yaml"], capture_output=True, text=True, check=True).stdout
docs = [d for d in yaml.safe_load_all(out) if d]
cj = next(d for d in docs if d["kind"] == "CronJob")
s = cj["spec"]
assert (s["schedule"], s["timeZone"], s["concurrencyPolicy"]) == ("30 3 * * *", "America/New_York", "Forbid"), s
job = s["jobTemplate"]["spec"]
assert job["backoffLimit"] == 0, job
pod = job["template"]["spec"]
sc = pod["securityContext"]
assert (sc["runAsUser"], sc["runAsGroup"]) == (1029, 100), sc
c = pod["containers"][0]
assert c["image"] == "rmcrackan/libation:14.2.2", c["image"]
env = {e["name"]: e.get("value") for e in c.get("env", [])}
assert "SLEEP_TIME" not in env, "must run once and exit"
m = {x["mountPath"]: x for x in c["volumeMounts"]}
assert m["/data"]["subPath"] == "audible", m["/data"]
assert m["/config/Settings.json"]["subPath"] == "Settings.json" and m["/config/Settings.json"]["readOnly"] is True
assert "/config" in m and "/config-internal" in m
vols = {v["name"]: v for v in pod["volumes"]}
assert vols[m["/config-internal"]["name"]].get("emptyDir") is not None
cm = next(d for d in docs if d["kind"] == "ConfigMap")
st = json.loads(cm["data"]["Settings.json"])
assert st["FolderTemplate"] == "<first author>/<title short>" and st["SplitFilesByChapter"] is False and st["DecryptToLossy"] is False, st
assert next(d for d in docs if d["kind"] == "PersistentVolumeClaim")["spec"]["resources"]["requests"]["storage"] == "1Gi"
print("libation render: OK")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 $SP/libation_render_test.py $SP`
Expected: FAIL (`argocd/apps/libation/values.yaml` not found).

- [ ] **Step 3: Write `argocd/apps/libation/application.yaml`**

```yaml
---
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: libation
  namespace: argocd
  finalizers:
    - resources-finalizer.argocd.argoproj.io
  annotations:
    # Wave 0: needs media-data (media-storage, wave -1)
    argocd.argoproj.io/sync-wave: "0"
spec:
  project: default
  sources:
    - repoURL: ghcr.io/bjw-s-labs/helm
      chart: app-template
      targetRevision: "5.2.1"
      helm:
        releaseName: libation
        valueFiles:
          - $values/argocd/apps/libation/values.yaml
    - repoURL: https://github.com/jimdaga/dagalab-infra.git
      targetRevision: main
      ref: values
  destination:
    server: https://kubernetes.default.svc
    namespace: media
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

- [ ] **Step 4: Write `argocd/apps/libation/values.yaml`**

```yaml
# Libation: nightly backup of the user's Audible library as DRM-free .m4b into /data/audible (Audiobookshelf reads it).
# The image runs once and exits (SLEEP_TIME unset): scan the account, download + decrypt anything new.
# /config (Longhorn) holds AccountsSettings.json (Audible tokens) and Libation's DB. The account is signed in once by
# hand with LibationCli login-external (README "Media stack plan" 8); tokens never go to git or Vault.
# Settings.json comes from git: the image copies it into /config-internal and never writes it back.
controllers:
  libation:
    type: cronjob
    cronjob:
      schedule: "30 3 * * *"
      timeZone: America/New_York
      concurrencyPolicy: Forbid
      backoffLimit: 0                 # a failed night just shows as a failed Job; the next night retries
      successfulJobsHistory: 3
      failedJobsHistory: 3
      activeDeadlineSeconds: 21600    # 6h: the first run downloads the whole library; later nights resume
    pod:
      restartPolicy: Never
      securityContext:   # DSM user media (files on the NAS owned by 1029:100)
        runAsUser: 1029
        runAsGroup: 100
        fsGroup: 100
        fsGroupChangePolicy: OnRootMismatch
    containers:
      app:
        image:
          repository: rmcrackan/libation
          tag: 14.2.2
        env:
          TZ: America/New_York
        resources:
          requests:
            cpu: 250m
            memory: 512Mi
          limits:
            memory: 2Gi

configMaps:
  settings:
    data:
      # Audiobookshelf expects Author/Title folders; one decrypted .m4b per book (no chapter split, no MP3 re-encode)
      Settings.json: |
        {
          "FolderTemplate": "<first author>/<title short>",
          "FileTemplate": "<title short>",
          "SplitFilesByChapter": false,
          "DecryptToLossy": false,
          "DownloadCoverArt": true
        }

persistence:
  # Audible account tokens + Libation DB: Longhorn
  config:
    type: persistentVolumeClaim
    accessMode: ReadWriteOnce
    size: 1Gi
    globalMounts:
      - path: /config
  settings:
    type: configMap
    identifier: settings
    globalMounts:
      - { path: /config/Settings.json, subPath: Settings.json, readOnly: true }
  # the entrypoint copies config here; writable for uid 1029 whatever the image's owner is
  internal:
    type: emptyDir
    globalMounts:
      - path: /config-internal
  # only the audible/ folder of the media share
  audible:
    type: persistentVolumeClaim
    existingClaim: media-data
    globalMounts:
      - { path: /data, subPath: audible }
```

- [ ] **Step 5: Run the render test, commit, push, sync**

```bash
python3 $SP/libation_render_test.py $SP && make lint >/dev/null && make template >/dev/null && echo OK
git add argocd/apps/libation
git commit -m "Libation: nightly Audible backup to /data/audible (CronJob)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push
kubectl -n argocd annotate app root argocd.argoproj.io/refresh=hard --overwrite
kubectl -n media get cronjob libation; kubectl -n media get pvc | grep libation
```
Expected: `libation render: OK`, `OK`; the CronJob exists; one `libation…` PVC (note its name for Step 6).

- [ ] **Step 6: Start a temporary sign-in pod on the PVC**

```bash
PVC=$(kubectl -n media get pvc -o name | grep libation | cut -d/ -f2)
kubectl -n media apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata: {name: libation-login, namespace: media}
spec:
  restartPolicy: Never
  securityContext: {runAsUser: 1029, runAsGroup: 100, fsGroup: 100}
  containers:
    - name: cli
      image: rmcrackan/libation:14.2.2
      command: [sleep, "3600"]
      volumeMounts: [{name: config, mountPath: /config}]
  volumes: [{name: config, persistentVolumeClaim: {claimName: $PVC}}]
EOF
kubectl -n media wait --for=condition=Ready pod/libation-login --timeout=180s
```
Expected: `pod/libation-login condition met`.

- [ ] **Step 7: The user signs in to Audible (interactive; their terminal)**

Ask the user to run in their own terminal (needs a TTY; `!` in this session has none), with their Audible email:

```
kubectl -n media exec -it libation-login -- /libation/LibationCli login-external --libationFiles /config -a <audible email> -l us
```
They open the printed URL, sign in (incl. 2FA), and paste the final address-bar URL back. Then verify (no tokens printed):

```bash
kubectl -n media exec libation-login -- /libation/LibationCli list-accounts --libationFiles /config
kubectl -n media exec libation-login -- ls -ln /config
kubectl -n media delete pod libation-login
```
Expected: the account listed as valid; `AccountsSettings.json` owned by 1029; pod deleted.

- [ ] **Step 8: First run (manual Job) and check**

```bash
kubectl -n media create job --from=cronjob/libation libation-first
kubectl -n media logs -f job/libation-first | grep -vE "token|Token" | tail -40
kubectl -n media get job libation-first -o jsonpath='{.status.succeeded}{"\n"}'
kubectl -n media exec deploy/audiobookshelf -- sh -c 'find /audible -name "*.m4b" | head -5; find /audible -name "*.m4b" | wc -l'
```
Expected: log shows `running once`, `scanning accounts`, `liberating books`, `exiting`; `succeeded` = `1` (a big library may hit the 6h deadline: then the Job fails, the files so far stay, and tonight's run continues; record that as a ruling, not a failure); at least one `/audible/<Author>/<Title>/<Title>.m4b`. Then trigger a library scan in Audiobookshelf (UI: Audiobooks → Scan) and see the book listed. Delete the Job: `kubectl -n media delete job libation-first`.

---

### Task 6: Docs and end-to-end with the user

**Files:**
- Modify: `CLAUDE.md`, `README.md`, `docs/superpowers/specs/2026-10-20-audiobooks-design.md` (spec corrections 1–3)

**Interfaces:**
- Consumes: everything above.
- Produces: documentation; verified success criteria.

- [ ] **Step 1: Docs check (fails first)**

```bash
for p in "books.dagaflix.com" "bookshelf.lab.internal" "audiobookshelf-setup.sh" "login-external" "audible/"; do
  grep -q "$p" README.md && grep -q -- "${p%%.*}" CLAUDE.md || echo "MISSING $p"; done
```
Expected: several `MISSING` lines.

- [ ] **Step 2: CLAUDE.md**

- Network line: in the `.200` description, `it carries only Jellyfin on popcorn.dagaflix.com and Seerr on requests.dagaflix.com (...)` → `it carries only Jellyfin on popcorn.dagaflix.com, Seerr on requests.dagaflix.com (Jellyfin sign-in only, local Seerr login off) and Audiobookshelf on books.dagaflix.com (own accounts)`.
- Storage line: after `downloads/{movies,tv}` add `; audio: audiobooks/ = Bookshelf root, audible/ = Libation's Audible backups, podcasts/ = Audiobookshelf, downloads/audiobooks = qBittorrent category`.
- New bullet after the Plex bullet:

```markdown
- Audiobooks: Audiobookshelf (`argocd/apps/audiobookshelf`, public `https://books.dagaflix.com`, LAN `https://books.lab.internal`; own accounts, root `jim` in Vault `secret/audiobookshelf`; mounts only `audiobooks/` + `audible/` read-only and `podcasts/` read-write; libraries scan hourly since NFS has no inotify; `scripts/audiobookshelf-setup.sh`, which must run before the public route on a fresh install: an uninitialised server lets anyone create root). Bookshelf (`argocd/apps/bookshelf`, Readarr fork, `https://bookshelf.lab.internal`, LAN only, `External` auth like Sonarr): wired by `arr-wire.py`, which also owns its "Audiobook" quality profile (Recyclarr doesn't support it); root folder monitors nothing by default so one request never grabs a whole catalogue. Libation (`argocd/apps/libation`): nightly CronJob, Audible account signed in once with `LibationCli login-external` on its PVC (README "Media stack plan" 8), settings from git.
```

- [ ] **Step 3: README**

- Stack table: add rows after `seerr`: `| 0 | audiobookshelf | Audiobooks + podcasts server; public https://books.dagaflix.com, LAN https://books.lab.internal |`, `| 0 | bookshelf | Audiobook manager (Readarr fork) → /data/audiobooks; https://bookshelf.lab.internal |`, `| 0 | libation | Nightly CronJob: Audible library → /data/audible |`.
- Network table `.200` row: `Only Jellyfin at popcorn.dagaflix.com and Seerr at requests.dagaflix.com` → `Only Jellyfin (popcorn), Seerr (requests) and Audiobookshelf (books) on dagaflix.com`.
- LAN access table: `| books.lab.internal | Audiobookshelf |` and `| bookshelf.lab.internal | Bookshelf (audiobook requests) |`.
- "Media stack plan": new item after 7:

```markdown
8. **Audiobooks** (deployed 2026-10-20; spec `docs/superpowers/specs/2026-10-20-audiobooks-design.md`). Folders in `/data`: `audiobooks/` (Bookshelf), `audible/` (Libation), `podcasts/` (Audiobookshelf), `downloads/audiobooks/` (qBittorrent). One-time steps (repeat only after losing the volumes):
   1. Folders, as the `media` user: `kubectl -n media exec deploy/sonarr -c app -- s6-setuidgid abc mkdir -p /data/audiobooks /data/audible /data/podcasts /data/downloads/audiobooks`.
   2. `VAULT_TOKEN=... scripts/audiobookshelf-setup.sh` **before** the `books` route is on the shared gateway: root `jim` (password in Vault `secret/audiobookshelf`), libraries Audiobooks (`/audiobooks` + `/audible`, hourly scan) and Podcasts, read-only `homepage` user + key.
   3. `scripts/arr-run.sh scripts/arr-wire.py`: Bookshelf's Audiobook profile, root folder, qBittorrent category, Prowlarr app (categories 3030 + 3000).
   4. Audible: temporary pod on Libation's PVC (`sleep`), then in a terminal `kubectl -n media exec -it libation-login -- /libation/LibationCli login-external --libationFiles /config -a <email> -l us`; open the URL, sign in, paste the final URL back; delete the pod. Run once now: `kubectl -n media create job --from=cronjob/libation libation-first`.
   5. Family accounts in Audiobookshelf (Settings → Users): long random passwords, no upload/delete. Phone apps use `https://books.dagaflix.com` everywhere.
   6. UniFi DNS: `books.lab.internal`, `bookshelf.lab.internal` → 192.168.3.201.
   Requesting a book: search it in Bookshelf and add it (only that book is monitored); it appears in Audiobookshelf within the hour.
```
- Setup order line: append `→ audiobookshelf-setup.sh` after `bazarr-setup.sh`.
- TODO: `- [ ] UniFi DNS: books.lab.internal, bookshelf.lab.internal → 192.168.3.201` (the user ticks it).

- [ ] **Step 4: Spec corrections**

In the spec: Bookshelf wiring bullet `Metadata profile: audiobook format.` → `(Audiobooks-only comes from this profile alone; the default metadata profile is used.)`; Libation settings bullet → `Settings: Settings.json from a git ConfigMap (read-only subPath; the image copies it in and never writes it back): ...`; Audiobookshelf setup bullet `an API key for Homepage` → `a read-only user homepage with an API key for Homepage`.

- [ ] **Step 5: Re-run the docs check, commit, push**

Run the Step 1 loop. Expected: no output.

```bash
git add CLAUDE.md README.md docs/superpowers/specs/2026-10-20-audiobooks-design.md
git commit -m "Docs: audiobooks (Audiobookshelf, Bookshelf, Libation)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push
```

- [ ] **Step 6: End-to-end with the user**

1. Argo: `kubectl -n argocd get app audiobookshelf bookshelf libation` → all `Synced Healthy`.
2. Request a test audiobook in Bookshelf (the user picks a title); watch `/data/downloads/audiobooks` fill, then import: `stat -c '%h %n'` on the imported file in `/data/audiobooks/...` shows link count `2` (hardlink).
3. Scan the Audiobooks library (or wait ≤1h); both the Bookshelf book and a Libation book are listed.
4. The user, on the phone app over cellular (`https://books.dagaflix.com`): plays a book, seeks to the middle (range requests through Envoy), plays a podcast episode after subscribing to one; on a second device (web on the Mac) the same book resumes at the same position (socket.io sync).
5. Ledger `Task 6: complete`; remind the user of the README follow-ups (notifications, Decluttarr, Unpackerr, monitoring + Libation alert).
