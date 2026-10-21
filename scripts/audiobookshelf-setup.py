#!/usr/bin/env python3
"""Idempotent Audiobookshelf setup over its API (port-forward on 127.0.0.1:13378). Called by audiobookshelf-setup.sh.
Env: ABS_ROOT_PASSWORD, ABS_HOMEPAGE_KEY (the key in Vault, may be empty). Prints HOMEPAGE_KEY=... when it creates one
(no key yet, or the stored one no longer works, e.g. after a rebuilt config volume)."""
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
    dict(name='Audiobooks', mediaType='book', icon='audiobookshelf',
         folders=[{'fullPath': '/audiobooks/library'}, {'fullPath': '/audiobooks/audible'}],
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
stored = os.environ.get('ABS_HOMEPAGE_KEY', '')
if not stored or call('GET', '/api/libraries', tok=stored)[0] != 200:
    st, r = call('POST', '/api/api-keys', {'name': 'homepage', 'userId': user['id'], 'isActive': True}, tok=tok)
    if st != 200:
        sys.exit(f'create API key failed: {st} {r}')
    print(f"HOMEPAGE_KEY={r['apiKey']['apiKey']}")
    log('api key homepage: created' + (' (stored key was rejected)' if stored else ''))
else:
    log('api key homepage: in Vault and valid')
