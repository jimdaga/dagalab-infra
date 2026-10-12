#!/usr/bin/env python3
"""Idempotent Seerr setup over its API (via a port-forward on 127.0.0.1:15055).

Env: JF_USER/JF_PASSWORD (Jellyfin admin; becomes Seerr's admin), JF_KEY (Jellyfin API key "seerr",
from scripts/jellyfin-setup.sh), RADARR_KEY, SONARR_KEY. Note: Seerr v3.5 stores the Jellyfin server as
ip/port/useSsl/urlBase + apiKey, not the `hostname` its published spec shows; name/libraries/serverId are read-only. With --emit-key prints SEERR_KEY=<api key> on stdout (caller stores it in Vault);
everything else goes to stderr and never includes secrets.
"""
import http.cookiejar, json, os, sys, urllib.error, urllib.request

B = 'http://127.0.0.1:15055/api/v1'
SVC = 'media.svc.cluster.local'
QUALITY = {'radarr': 'HD Bluray + WEB', 'sonarr': 'WEB-1080p'}   # Recyclarr-managed (TRaSH) profiles
jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

def log(*a): print(*a, file=sys.stderr, flush=True)

def call(method, path, body=None):
    req = urllib.request.Request(B + path, method=method, headers={'Content-Type': 'application/json'},
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with opener.open(req, timeout=120) as r:
            raw = r.read(); return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]

st, pub = call('GET', '/settings/public')
initialized = pub.get('initialized')
log(f'seerr initialized: {initialized}')

# 1) sign in with the Jellyfin admin (first time this also creates Seerr's admin + Jellyfin settings)
login = {'username': os.environ['JF_USER'], 'password': os.environ['JF_PASSWORD']}
if not initialized:
    login.update(hostname=f'jellyfin.{SVC}', port=8096, useSsl=False, urlBase='', email='jim@lab.internal', serverType=2)
st, r = call('POST', '/auth/jellyfin', login)
if st == 500 and 'already configured' in str(r):
    # a previous partial run saved the Jellyfin server; a plain sign-in is enough now
    st, r = call('POST', '/auth/jellyfin', {k: login[k] for k in ('username', 'password')})
if st not in (200, 204):
    sys.exit(f'jellyfin sign-in failed: {st} {r}')
log(f'signed in as {login["username"]} (Jellyfin)')

# 2) Jellyfin settings: external URL for "watch in Jellyfin" links (writable fields only)
st, jf = call('GET', '/settings/jellyfin')
body = dict(ip=f'jellyfin.{SVC}', port=8096, useSsl=False, urlBase='',
            externalHostname='https://jellyfin.lab.internal', jellyfinForgotPasswordUrl=jf.get('jellyfinForgotPasswordUrl', ''),
            apiKey=os.environ['JF_KEY'])
if any(jf.get(k) != v for k, v in body.items()):
    st, r = call('POST', '/settings/jellyfin', body)
    log(f'jellyfin settings (server, external URL, API key): {st} {r if st >= 300 else ""}')
else:
    log('jellyfin settings: already set')

# 3) libraries: sync the list from Jellyfin, enable Movies + Shows (not Home Videos), start a full sync
st, r = call('POST', '/settings/jellyfin/library/sync')
st, libs = call('GET', '/settings/jellyfin/library')
if st != 200 or not isinstance(libs, list):
    sys.exit(f'library list failed: {st} {libs}')
for l in libs:
    if l['name'] in ('Movies', 'Shows') and not l.get('enabled'):
        st, r = call('PUT', f"/settings/jellyfin/library/{l['id']}", {'enabled': True})
        log(f"library {l['name']}: enable {st} {r if st >= 300 else ''}")
st, libs = call('GET', '/settings/jellyfin/library')
log('libraries enabled: ' + ', '.join(l['name'] for l in libs if l.get('enabled')))
st, r = call('POST', '/settings/jellyfin/sync')
log(f'full library sync started: {st}')

# 4) Radarr + Sonarr
def arr(kind, port, key, root, extra):
    st, existing = call('GET', f'/settings/{kind}')
    base = dict(hostname=f'{kind}.{SVC}', port=port, apiKey=key, useSsl=False, baseUrl='')
    st, t = call('POST', f'/settings/{kind}/test', base)
    if st != 200:
        log(f'{kind} test FAILED: {st} {t}'); return
    prof = next(p for p in t['profiles'] if p['name'] == QUALITY[kind])
    if existing:
        cur = existing[0]
        if cur.get('activeProfileId') == prof['id']:
            log(f'{kind}: present (profile {prof["name"]})'); return
        upd = {k: v for k, v in cur.items() if k != 'id'}   # id is read-only in the update body
        upd.update(activeProfileId=prof['id'], activeProfileName=prof['name'], apiKey=key)
        if kind == 'sonarr':
            upd.update(activeAnimeProfileId=prof['id'], activeAnimeProfileName=prof['name'])
        st, r = call('PUT', f'/settings/{kind}/{cur["id"]}', upd)
        log(f'{kind}: profile -> {prof["name"]} ({st} {r if st >= 300 else ""})'); return
    rf = next(f for f in t['rootFolders'] if f['path'].rstrip('/') == root)
    body = dict(base, name=kind.capitalize(), activeProfileId=prof['id'], activeProfileName=prof['name'],
                activeDirectory=rf['path'], is4k=False, isDefault=True, externalUrl=f'https://{kind}.lab.internal',
                syncEnabled=False, preventSearch=False, **extra(prof, rf))
    st, r = call('POST', f'/settings/{kind}', body)
    log(f'{kind}: {"added" if st in (200, 201) else f"FAILED {st} {r}"} (profile {prof["name"]}, {rf["path"]})')

arr('radarr', 7878, os.environ['RADARR_KEY'], '/data/movies', lambda p, f: dict(minimumAvailability='released'))
arr('sonarr', 8989, os.environ['SONARR_KEY'], '/data/tv',
    lambda p, f: dict(enableSeasonFolders=True, activeAnimeProfileId=p['id'], activeAnimeProfileName=p['name'],
                      activeAnimeDirectory=f['path']))

# 5) application URL + finish the setup wizard
MAIN_RW = ('appLanguage', 'applicationTitle', 'applicationUrl', 'hideAvailable', 'hideRequested', 'partialRequestsEnabled',
           'localLogin', 'mediaServerType', 'newPlexLogin', 'defaultPermissions', 'enableSpecialEpisodes', 'versionCheck')
st, main = call('GET', '/settings/main')
if main.get('applicationUrl') != 'https://seerr.lab.internal' or main.get('applicationTitle') != 'dagalab':
    body = {k: main[k] for k in MAIN_RW if k in main}
    body.update(applicationUrl='https://seerr.lab.internal', applicationTitle='dagalab')
    st, r = call('POST', '/settings/main', body)
    log(f'main settings: {st} {r if st >= 300 else ""}')
# Seerr sits behind the LAN gateway (Envoy sets X-Forwarded-For): trust it so Seerr sees real client IPs.
# Takes effect after a restart (kubectl -n media rollout restart deploy/seerr).
st, net = call('GET', '/settings/network')
if not net.get('trustProxy'):
    st, r = call('POST', '/settings/network', dict(net, trustProxy=True))
    log(f'network settings (trust proxy): {st} {r if st >= 300 else ""} (restart Seerr to apply)')
else:
    log('network settings (trust proxy): already set')
if not initialized:
    st, r = call('POST', '/settings/initialize')
    log(f'initialize: {st} {r if st >= 300 else ""}')

st, main = call('GET', '/settings/main')
log('api key: ' + ('present' if main.get('apiKey') else 'MISSING'))
if '--emit-key' in sys.argv and main.get('apiKey'):
    print(f"SEERR_KEY={main['apiKey']}")
