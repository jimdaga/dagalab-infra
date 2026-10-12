#!/usr/bin/env python3
"""Idempotently wire Radarr/Sonarr/Prowlarr over their APIs (via local port-forwards).
Env: RADARR_KEY, SONARR_KEY, PROWLARR_KEY. Ports: radarr 17878, sonarr 18989, prowlarr 19696 on localhost."""
import json, os, sys, urllib.request, urllib.error

SVC = 'media.svc.cluster.local'
APPS = {
    'radarr':   dict(url='http://127.0.0.1:17878/api/v3', key=os.environ['RADARR_KEY'],  root='/data/movies', cat_field='movieCategory', cat='movies', port=7878),
    'sonarr':   dict(url='http://127.0.0.1:18989/api/v3', key=os.environ['SONARR_KEY'],  root='/data/tv',     cat_field='tvCategory',    cat='tv',     port=8989),
}
PROWLARR = dict(url='http://127.0.0.1:19696/api/v1', key=os.environ['PROWLARR_KEY'])

def call(base, key, method, path, body=None):
    req = urllib.request.Request(base + path, method=method, headers={'X-Api-Key': key, 'Content-Type': 'application/json'},
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:400]

def setf(fields, name, value):
    for f in fields:
        if f['name'] == name:
            f['value'] = value; return
    raise KeyError(name)

NAMING = {
    'radarr': dict(renameMovies=True, replaceIllegalCharacters=True, colonReplacementFormat='spaceDashSpace',
                   movieFolderFormat='{Movie CleanTitle} ({Release Year}) [tmdbid-{TmdbId}]',
                   standardMovieFormat='{Movie CleanTitle} ({Release Year}) {[Quality Full]}'),
    'sonarr': dict(renameEpisodes=True, replaceIllegalCharacters=True, colonReplacementFormat=4,
                   seriesFolderFormat='{Series TitleYear} [tvdbid-{TvdbId}]',
                   seasonFolderFormat='Season {season:00}', specialsFolderFormat='Specials',
                   standardEpisodeFormat='{Series TitleYear} - S{season:00}E{episode:00} - {Episode CleanTitle} {[Quality Full]}',
                   dailyEpisodeFormat='{Series TitleYear} - {Air-Date} - {Episode CleanTitle} {[Quality Full]}',
                   animeEpisodeFormat='{Series TitleYear} - S{season:00}E{episode:00} - {Episode CleanTitle} {[Quality Full]}'),
}

ok = True
for name, a in APPS.items():
    u, k = a['url'], a['key']
    st, status = call(u, k, 'GET', '/system/status')
    print(f'== {name} {status.get("version") if st == 200 else st}')

    # root folder
    st, roots = call(u, k, 'GET', '/rootfolder')
    if not any(r['path'].rstrip('/') == a['root'] for r in roots):
        st, r = call(u, k, 'POST', '/rootfolder', {'path': a['root']})
        print(f'   root folder {a["root"]}: created (HTTP {st})' if st in (200, 201) else f'   root folder FAILED {st}: {r}'); ok &= st in (200, 201)
    else:
        print(f'   root folder {a["root"]}: present')

    # naming
    st, n = call(u, k, 'GET', '/config/naming')
    want = NAMING[name]
    if any(n.get(f) != v for f, v in want.items()):
        n.update(want)
        st, r = call(u, k, 'PUT', f'/config/naming/{n["id"]}', n)
        print(f'   naming: updated (HTTP {st})' if st in (200, 202) else f'   naming FAILED {st}: {r}'); ok &= st in (200, 202)
    else:
        print('   naming: already set')

    # media management: hardlinks on, don't leave empty folders, no recycle bin needed
    st, mm = call(u, k, 'GET', '/config/mediamanagement')
    mwant = {'copyUsingHardlinks': True, 'deleteEmptyFolders': True, 'createEmptySeriesFolders' if name == 'sonarr' else 'createEmptyMovieFolders': False}
    if any(mm.get(f) != v for f, v in mwant.items()):
        mm.update(mwant)
        st, r = call(u, k, 'PUT', f'/config/mediamanagement/{mm["id"]}', mm)
        print(f'   media management (hardlinks): updated (HTTP {st})' if st in (200, 202) else f'   media mgmt FAILED {st}: {r}'); ok &= st in (200, 202)
    else:
        print('   media management (hardlinks): already set')

    # qBittorrent download client
    st, dcs = call(u, k, 'GET', '/downloadclient')
    if any(d['implementation'] == 'QBittorrent' for d in dcs):
        print('   download client qBittorrent: present')
    else:
        st, schema = call(u, k, 'GET', '/downloadclient/schema')
        dc = next(s for s in schema if s['implementation'] == 'QBittorrent')
        dc.update(name='qBittorrent', enable=True, priority=1)
        setf(dc['fields'], 'host', f'qbittorrent.{SVC}')
        setf(dc['fields'], 'port', 8080)
        setf(dc['fields'], 'useSsl', False)
        setf(dc['fields'], a['cat_field'], a['cat'])
        st, r = call(u, k, 'POST', '/downloadclient/test', dc)
        if st not in (200, 202):
            print(f'   download client test FAILED {st}: {r}'); ok = False
        else:
            st, r = call(u, k, 'POST', '/downloadclient', dc)
            print(f'   download client qBittorrent ({a["cat"]}): test OK, created (HTTP {st})' if st in (200, 201) else f'   create FAILED {st}: {r}'); ok &= st in (200, 201)

# Prowlarr -> Radarr/Sonarr applications (full sync of indexers)
p = PROWLARR
st, status = call(p['url'], p['key'], 'GET', '/system/status')
print(f'== prowlarr {status.get("version") if st == 200 else st}')
st, existing = call(p['url'], p['key'], 'GET', '/applications')
st, schema = call(p['url'], p['key'], 'GET', '/applications/schema')
for name, a in APPS.items():
    impl = name.capitalize()
    if any(e['implementation'] == impl for e in existing):
        print(f'   application {impl}: present'); continue
    app = next(s for s in schema if s['implementation'] == impl)
    app.update(name=impl, syncLevel='fullSync')
    setf(app['fields'], 'prowlarrUrl', f'http://prowlarr.{SVC}:9696')
    setf(app['fields'], 'baseUrl', f'http://{name}.{SVC}:{a["port"]}')
    setf(app['fields'], 'apiKey', a['key'])
    st, r = call(p['url'], p['key'], 'POST', '/applications/test', app)
    if st not in (200, 202):
        print(f'   application {impl} test FAILED {st}: {r}'); ok = False; continue
    st, r = call(p['url'], p['key'], 'POST', '/applications', app)
    print(f'   application {impl}: test OK, created (HTTP {st})' if st in (200, 201) else f'   create FAILED {st}: {r}'); ok &= st in (200, 201)

# health
for name, (u, k) in {**{n: (a['url'], a['key']) for n, a in APPS.items()}, 'prowlarr': (p['url'], p['key'])}.items():
    st, h = call(u, k, 'GET', '/health')
    msgs = [x['message'] for x in h] if st == 200 else [f'HTTP {st}']
    print(f'== {name} health: ' + ('OK' if not msgs else ' | '.join(msgs)))
sys.exit(0 if ok else 1)
