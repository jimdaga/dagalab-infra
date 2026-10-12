#!/usr/bin/env python3
"""Idempotent Jellyfin first-run + config over its API (via a port-forward on 127.0.0.1:18096).

Env: JF_USER, JF_PASSWORD (admin to create/log in as). Prints API keys only as KEY_<app>=... lines on
stdout when --emit-keys is given (the caller pipes them into Vault; nothing else prints secrets).
"""
import json, os, sys, time, urllib.parse, urllib.request, urllib.error

B = 'http://127.0.0.1:18096'
USER, PW = os.environ['JF_USER'], os.environ['JF_PASSWORD']
EMIT = '--emit-keys' in sys.argv
AUTH = 'MediaBrowser Client="dagalab-setup", Device="cli", DeviceId="dagalab-setup", Version="1.0"'
LIBS = [('Movies', 'movies', '/data/movies'), ('Shows', 'tvshows', '/data/tv'), ('Home Videos', 'homevideos', '/data/theme-parks')]
KEY_APPS = ['homepage', 'seerr']

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
if cfg.get('ServerName') != 'dagalab':
    cfg['ServerName'] = 'dagalab'
    st, _ = call('POST', '/System/Configuration', cfg, token=T)
    log(f'server name -> dagalab: {st}')

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
st, _ = call('POST', '/Library/Refresh', token=T)
log(f'library scan started: {st}')

# 6) API keys for other apps
st, keys = call('GET', '/Auth/Keys', token=T)
items = {k['AppName']: k['AccessToken'] for k in keys.get('Items', [])}
for app in KEY_APPS:
    if app not in items:
        call('POST', f'/Auth/Keys?app={app}', token=T)
st, keys = call('GET', '/Auth/Keys', token=T)
items = {k['AppName']: k['AccessToken'] for k in keys.get('Items', [])}
log('api keys: ' + ', '.join(f'{a}={"ok" if a in items else "MISSING"}' for a in KEY_APPS))
if EMIT:
    for app in KEY_APPS:
        print(f'KEY_{app}={items[app]}')
