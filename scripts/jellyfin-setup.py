#!/usr/bin/env python3
"""Idempotent Jellyfin first-run + config over its API (via a port-forward on 127.0.0.1:18096).
--rotate=<app> revokes and reissues that app's API key (e.g. after it leaked).

Env: JF_USER, JF_PASSWORD (admin to create/log in as). Prints API keys only as KEY_<app>=... lines on
stdout when --emit-keys is given (the caller pipes them into Vault; nothing else prints secrets).
"""
import json, os, sys, time, urllib.parse, urllib.request, urllib.error

ROOT = 'http://127.0.0.1:18096'
BASE_URL = ''   # served from /; hidden by subdomain instead (popcorn.dagaflix.com). A path broke some TV apps.
USER, PW = os.environ['JF_USER'], os.environ['JF_PASSWORD']
EMIT = '--emit-keys' in sys.argv
ROTATE = [a.split('=', 1)[1] for a in sys.argv if a.startswith('--rotate=')]   # e.g. --rotate=homepage
AUTH = 'MediaBrowser Client="dagalab-setup", Device="cli", DeviceId="dagalab-setup", Version="1.0"'
LIBS = [('Movies', 'movies', '/data/movies'), ('Shows', 'tvshows', '/data/tv'), ('Home Videos', 'homevideos', '/data/theme-parks'),
        ('Sports', 'tvshows', '/data/sports')]   # games from scripts/sports-dl.py, metadata only from its .nfo files
KEY_APPS = ['homepage', 'seerr', 'arr']   # arr: Sonarr/Radarr tell Jellyfin to refresh after imports

def log(*a): print(*a, file=sys.stderr, flush=True)

def call(method, path, body=None, token=None, raw=False):
    h = {'Content-Type': 'application/json',
         'Authorization': AUTH + (f', Token="{token}"' if token else '')}
    req = urllib.request.Request(B + path, method=method, headers=h, data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
            return r.status, (data if raw else (json.loads(data) if data and data[:1] in b'[{' else data.decode()))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]

# 0) a fresh install serves from /; a server still on the old /4orr BaseUrl only under it. Try /4orr first:
# on such a server / redirects into /4orr, so probing / would wrongly succeed.
B = ROOT
for base in ('/4orr', BASE_URL):
    try:
        urllib.request.urlopen(ROOT + base + '/System/Info/Public', timeout=10)
        B = ROOT + base
        break
    except urllib.error.URLError:
        pass
log(f'api base: {B}')

# 1) startup wizard (only while not completed)
st, info = call('GET', '/System/Info/Public')
if not info.get('StartupWizardCompleted'):
    log('startup wizard: running')
    st, _ = call('POST', '/Startup/Configuration', {'UICulture': 'en-US', 'MetadataCountryCode': 'US', 'PreferredMetadataLanguage': 'en'})
    log(f'   configuration: {st}')
    call('GET', '/Startup/User')   # creates/returns the initial user
    st, r = call('POST', '/Startup/User', {'Name': USER, 'Password': PW})
    log(f'   admin user {USER}: {st} {r if st >= 300 else ""}')
    st, _ = call('POST', '/Startup/RemoteAccess', {'EnableRemoteAccess': True, 'EnableAutomaticPortMapping': False})
    log(f'   remote access (LAN + gateway): {st}')
    st, r = call('POST', '/Startup/Complete')
    log(f'   complete: {st} {r if st >= 300 else ""}')
else:
    log('startup wizard: already completed')

# 2) log in
st, a = call('POST', '/Users/AuthenticateByName', {'Username': USER, 'Pw': PW})
if st != 200:
    sys.exit(f'login failed: {st} {a}')
T = a['AccessToken']
log(f'login as {USER}: ok')

# 3) server name
st, cfg = call('GET', '/System/Configuration', token=T)
if cfg.get('ServerName') != 'DAGAFLIX':
    cfg['ServerName'] = 'DAGAFLIX'
    st, _ = call('POST', '/System/Configuration', cfg, token=T)
    log(f'server name -> DAGAFLIX: {st}')

# 3b) base URL: takes effect after a restart; the gateways and every client URL include it
st, net = call('GET', '/System/Configuration/network', token=T)
if net.get('BaseUrl') != BASE_URL:
    net['BaseUrl'] = BASE_URL
    st, _ = call('POST', '/System/Configuration/network', net, token=T)
    log(f'base URL -> {BASE_URL}: {st}; restart Jellyfin to apply (kubectl -n media rollout restart deploy/jellyfin), then rerun')
else:
    log(f'base URL: {BASE_URL}')

# 4) hardware transcoding: Intel Quick Sync on the UHD 630 (Gen 9.5)
st, enc = call('GET', '/System/Configuration/encoding', token=T)
want = dict(HardwareAccelerationType='qsv', QsvDevice='/dev/dri/renderD128', VaapiDevice='/dev/dri/renderD128',
            EnableHardwareEncoding=True, AllowHevcEncoding=False,
            HardwareDecodingCodecs=['h264', 'hevc', 'mpeg2video', 'vc1', 'vp8', 'vp9'],
            EnableDecodingColorDepth10Hevc=True, EnableDecodingColorDepth10Vp9=False,
            EnableIntelLowPowerH264HwEncoder=False, EnableIntelLowPowerHevcHwEncoder=False,
            TranscodingTempPath='/cache/transcodes', EnableThrottling=True)
if any(enc.get(k) != v for k, v in want.items()):
    enc.update(want)
    st, r = call('POST', '/System/Configuration/encoding', enc, token=T)
    log(f'hardware transcoding (qsv): {st} {r if st >= 300 else ""}')
else:
    log('hardware transcoding (qsv): already set')

# 5) libraries
st, folders = call('GET', '/Library/VirtualFolders', token=T)
have = {f['Name'] for f in folders}
for name, ctype, path in LIBS:
    if name in have:
        log(f'library {name}: present'); continue
    q = urllib.parse.urlencode({'name': name, 'collectionType': ctype, 'refreshLibrary': 'false'})
    st, r = call('POST', f'/Library/VirtualFolders?{q}', {'LibraryOptions': {'PathInfos': [{'Path': path}], 'EnableRealtimeMonitor': False}}, token=T)
    log(f'library {name} ({ctype}) -> {path}: {st} {r if st >= 300 else ""}')
# trickplay (scrub previews) in /config/data/trickplay, which is the NAS share /volume2/trickplay (media stays read-only);
# no NFO files written into media folders
LIB_OPTS = {'EnableTrickplayImageExtraction': True, 'ExtractTrickplayImagesDuringLibraryScan': True,
            'SaveTrickplayWithMedia': False, 'MetadataSavers': []}
# Sports: no TVDB/TMDB lookups ("NFL Classic Games" would match some other show); the .nfo + -thumb.jpg are the metadata
SPORTS_OPTS = {'TypeOptions': [{'Type': t, 'MetadataFetchers': [], 'MetadataFetcherOrder': [], 'ImageFetchers': [],
                                'ImageFetcherOrder': [], 'ImageOptions': []} for t in ('Series', 'Season', 'Episode')]}
st, folders = call('GET', '/Library/VirtualFolders', token=T)
for f in folders:
    if f['Name'] not in ('Movies', 'Shows', 'Sports'): continue
    o = f['LibraryOptions']
    want = {**LIB_OPTS, **(SPORTS_OPTS if f['Name'] == 'Sports' else {})}
    if any(o.get(k) != v for k, v in want.items()):
        st, r = call('POST', '/Library/VirtualFolders/LibraryOptions', {'Id': f['ItemId'], 'LibraryOptions': {**o, **want}}, token=T)
        log(f'library {f["Name"]}: trickplay on, no NFO: {st} {r if st >= 300 else ""}')
    else:
        log(f'library {f["Name"]}: options already set')
st, _ = call('POST', '/Library/Refresh', token=T)
log(f'library scan started: {st}')

# 6) API keys for other apps
st, keys = call('GET', '/Auth/Keys', token=T)
items = {k['AppName']: k['AccessToken'] for k in keys.get('Items', [])}
MANAGED = {'homepage', 'seerr', 'arr'}   # keys this script has ever created; never touch keys other apps made (e.g. Seerr's own "Seerr")
for app in [a for a in items if a in MANAGED and a not in KEY_APPS]:   # revoke our keys no app uses any more
    st, _ = call('DELETE', f'/Auth/Keys/{items.pop(app)}', token=T)
    log(f'api key {app}: no longer used, revoked ({st})')
for app in ROTATE:
    if app in items:
        st, _ = call('DELETE', f'/Auth/Keys/{items.pop(app)}', token=T)
        log(f'api key {app}: revoked ({st}), issuing a new one')
for app in KEY_APPS:
    if app not in items:
        call('POST', f'/Auth/Keys?app={app}', token=T)
st, keys = call('GET', '/Auth/Keys', token=T)
items = {k['AppName']: k['AccessToken'] for k in keys.get('Items', [])}
log('api keys: ' + ', '.join(f'{a}={"ok" if a in items else "MISSING"}' for a in KEY_APPS))
if EMIT:
    for app in KEY_APPS:
        print(f'KEY_{app}={items[app]}')
