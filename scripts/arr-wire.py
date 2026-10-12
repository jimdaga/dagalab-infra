#!/usr/bin/env python3
"""Idempotently wire Radarr/Sonarr/Prowlarr over their APIs (via local port-forwards).
Env: RADARR_KEY, SONARR_KEY, PROWLARR_KEY, JELLYFIN_KEY (Jellyfin API key "arr"). Ports: radarr 17878, sonarr 18989, prowlarr 19696, qbittorrent 18080 on localhost."""
import json, os, sys, urllib.parse, urllib.request, urllib.error

SVC = 'media.svc.cluster.local'
APPS = {
    'radarr':   dict(url='http://127.0.0.1:17878/api/v3', key=os.environ['RADARR_KEY'],  root='/data/movies', cat_field='movieCategory', cat='movies', port=7878),
    'sonarr':   dict(url='http://127.0.0.1:18989/api/v3', key=os.environ['SONARR_KEY'],  root='/data/tv',     cat_field='tvCategory',    cat='tv',     port=8989),
}
PROWLARR = dict(url='http://127.0.0.1:19696/api/v1', key=os.environ['PROWLARR_KEY'])
QBT = 'http://127.0.0.1:18080/api/v2'   # port-forwarded: arrives from localhost, so no login needed...
QBT_HOST = '127.0.0.1:8080'             # ...as long as the Host header names qBittorrent's own port (else 401)

# Per private tracker: seeding goals, so nothing lands on a hit-and-run list, and Prowlarr priority (lower wins
# ties between equally good releases; TorrentLeech has the better releases and swarms). qBittorrent stops a torrent when
# EITHER limit is hit, matching the trackers' "ratio OR time" rules; Sonarr/Radarr then remove it (data stays
# in the library: imports are hardlinks). Keyed by Prowlarr definition name; trackers = announce hostnames.
#   TorrentLeech: 1:1 or 10 days -> 1.5 / 14 days.  wigornot: ratio 10 or 1 year (its feed's minimumratio/seedtime).
TRACKERS = {
    'torrentleech': dict(ratio=1.5, minutes=14 * 24 * 60,  priority=10, trackers=('torrentleech.org', 'tleechreload.org')),
    'wigornot':     dict(ratio=10,  minutes=365 * 24 * 60, priority=25, trackers=('wigornot.com',)),
}
# qBittorrent: seed everything that's finished: only downloads are queued (3 at a time); no cap on active seeding,
# no global stop (torrents without a per-tracker goal seed indefinitely). Stop (not remove) at a goal so the *arr apps clean up.
# Stalled downloads must keep counting toward the 3: private trackers cap concurrent downloads per account
# (TorrentLeech answers "torrents limit reached"), so starting every queued torrent at once gets them all refused.
QBT_PREFS = dict(queueing_enabled=True, max_active_downloads=3, max_active_uploads=-1, max_active_torrents=-1,
                 dont_count_slow_torrents=False, max_ratio_enabled=False, max_seeding_time_enabled=False, max_ratio_act=0)

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

    # media management: hardlinks on, don't leave empty folders, no recycle bin needed; import subtitle files that
    # come with a release (embedded subtitles need nothing: Jellyfin reads them from the mkv)
    st, mm = call(u, k, 'GET', '/config/mediamanagement')
    mwant = {'copyUsingHardlinks': True, 'deleteEmptyFolders': True, 'createEmptySeriesFolders' if name == 'sonarr' else 'createEmptyMovieFolders': False,
             'importExtraFiles': True, 'extraFileExtensions': 'srt,ass,ssa,vtt,sub,idx'}
    if any(mm.get(f) != v for f, v in mwant.items()):
        mm.update(mwant)
        st, r = call(u, k, 'PUT', f'/config/mediamanagement/{mm["id"]}', mm)
        print(f'   media management (hardlinks, subtitles): updated (HTTP {st})' if st in (200, 202) else f'   media mgmt FAILED {st}: {r}'); ok &= st in (200, 202)
    else:
        print('   media management (hardlinks, subtitles): already set')

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

    # Jellyfin connection: refresh the library right after imports/upgrades/renames/deletes. Jellyfin can't watch the
    # NFS share for changes, so without this new files only show up at its 12-hourly scan. Same /data path in both pods.
    st, conns = call(u, k, 'GET', '/notification')
    jf = next((c for c in conns if c['implementation'] == 'MediaBrowser'), None)
    if jf is None:
        st, schema = call(u, k, 'GET', '/notification/schema')
        jf = next(c for c in schema if c['implementation'] == 'MediaBrowser')
    jwant = {'host': f'jellyfin.{SVC}', 'port': 8096, 'useSsl': False, 'apiKey': os.environ['JELLYFIN_KEY'], 'notify': False, 'updateLibrary': True}
    events = [e for e in ('onDownload', 'onUpgrade', 'onImportComplete', 'onRename', 'onSeriesAdd', 'onSeriesDelete', 'onEpisodeFileDelete',
                          'onEpisodeFileDeleteForUpgrade', 'onMovieAdded', 'onMovieDelete', 'onMovieFileDelete', 'onMovieFileDeleteForUpgrade')
              if e in jf and jf.get('supports' + e[0].upper() + e[1:], False)]
    cur = {f['name']: f.get('value') for f in jf['fields']}
    # the API returns the stored key masked, so it can't be compared: after jellyfin-setup.sh --rotate=arr, delete the
    # Jellyfin connection in Sonarr/Radarr and rerun this to re-create it with the new key
    if jf.get('id') and all(cur.get(n) == v for n, v in jwant.items() if n != 'apiKey') and all(jf.get(e) for e in events):
        print('   Jellyfin connection: present')
    else:
        jf.update(name='Jellyfin', **{e: True for e in events})
        for n, v in jwant.items():
            setf(jf['fields'], n, v)
        st, r = call(u, k, 'POST', '/notification/test', jf)
        if st not in (200, 202):
            print(f'   Jellyfin connection test FAILED {st}: {r}'); ok = False
        else:
            st, r = call(u, k, 'PUT' if jf.get('id') else 'POST', f'/notification/{jf["id"]}' if jf.get('id') else '/notification', jf)
            print(f'   Jellyfin connection: {"updated" if jf.get("id") else "created"} (HTTP {st}; {", ".join(events)})' if st in (200, 201, 202) else f'   Jellyfin connection FAILED {st}: {r}')
            ok &= st in (200, 201, 202)

    # Never grab AV1: the UHD 630 can't decode it in hardware and many TVs/Apple devices can't direct-play it.
    # A custom format scored far below every profile's minimum (0) makes such releases rejected outright.
    st, cfs = call(u, k, 'GET', '/customformat')
    av1 = next((c for c in cfs if c['name'] == 'AV1'), None)
    if av1 is None:
        st, av1 = call(u, k, 'POST', '/customformat', {'name': 'AV1', 'includeCustomFormatWhenRenaming': False, 'specifications': [
            {'name': 'AV1', 'implementation': 'ReleaseTitleSpecification', 'negate': False, 'required': True,
             'fields': [{'name': 'value', 'value': r'\bAV1\b'}]}]})
        print(f'   custom format AV1: created (HTTP {st})' if st in (200, 201) else f'   custom format AV1 FAILED {st}: {av1}'); ok &= st in (200, 201)
    else:
        print('   custom format AV1: present')
    for qp in call(u, k, 'GET', '/qualityprofile')[1]:
        item = next((f for f in qp['formatItems'] if f['format'] == av1['id']), None)
        if item and item['score'] == -10000 and qp['minFormatScore'] >= 0:
            continue
        if item: item['score'] = -10000
        else: qp['formatItems'].append({'format': av1['id'], 'name': 'AV1', 'score': -10000})
        qp['minFormatScore'] = max(qp['minFormatScore'], 0)
        st, r = call(u, k, 'PUT', f'/qualityprofile/{qp["id"]}', qp)
        print(f'   profile {qp["name"]}: AV1 scored -10000 (HTTP {st})' if st in (200, 202) else f'   profile {qp["name"]} FAILED {st}: {r}'); ok &= st in (200, 202)

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

# Prowlarr indexer seed goals + priority (pushed to Radarr/Sonarr, which set per-torrent limits in qBittorrent on grab)
st, indexers = call(p['url'], p['key'], 'GET', '/indexer')
for ix in indexers:
    goal = TRACKERS.get((ix.get('definitionName') or '').lower())
    if not goal:
        continue
    want = {'torrentBaseSettings.seedRatio': goal['ratio'], 'torrentBaseSettings.seedTime': goal['minutes'],
            'torrentBaseSettings.packSeedTime': goal['minutes']}
    cur = {f['name']: f.get('value') for f in ix['fields']}
    if all(cur.get(n) == v for n, v in want.items()) and ix['priority'] == goal['priority']:
        print(f'   seed goal + priority {ix["name"]}: already set'); continue
    for n, v in want.items():
        setf(ix['fields'], n, v)
    ix['priority'] = goal['priority']
    st, r = call(p['url'], p['key'], 'PUT', f'/indexer/{ix["id"]}?forceSave=true', ix)
    print(f'   seed goal + priority {ix["name"]}: ratio {goal["ratio"]} / {goal["minutes"] // 1440} days, priority {goal["priority"]} (HTTP {st})' if st in (200, 202) else f'   seed goal {ix["name"]} FAILED {st}: {r}')
    ok &= st in (200, 202)

# qBittorrent: preferences + bring torrents grabbed before these goals existed in line with their tracker
def qbt(path, form=None):
    req = urllib.request.Request(QBT + path, headers={'Host': QBT_HOST},
                                 data=urllib.parse.urlencode(form).encode() if form is not None else None)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read(); return json.loads(raw) if raw and raw[:1] in b'[{' else raw.decode()
print(f'== qbittorrent {qbt("/app/version")}')
prefs = qbt('/app/preferences')
if any(prefs.get(n) != v for n, v in QBT_PREFS.items()):
    qbt('/app/setPreferences', {'json': json.dumps(QBT_PREFS)})
    print('   preferences (unlimited seeding, 3 downloads): updated')
else:
    print('   preferences (unlimited seeding, 3 downloads): already set')
for name, goal in TRACKERS.items():
    stale = [t['hash'] for t in qbt('/torrents/info')
             if any(h in (t.get('tracker') or '') for h in goal['trackers'])
             and (t['ratio_limit'] != goal['ratio'] or t['seeding_time_limit'] != goal['minutes'])]
    if stale:
        qbt('/torrents/setShareLimits', {'hashes': '|'.join(stale), 'ratioLimit': goal['ratio'],
                                         'seedingTimeLimit': goal['minutes'], 'inactiveSeedingTimeLimit': -2,
                                         'shareLimitAction': 'Default'})   # Default = global max_ratio_act (Stop)
    print(f'   {name} torrents: {len(stale)} share limits updated')

# health
for name, (u, k) in {**{n: (a['url'], a['key']) for n, a in APPS.items()}, 'prowlarr': (p['url'], p['key'])}.items():
    st, h = call(u, k, 'GET', '/health')
    msgs = [x['message'] for x in h] if st == 200 else [f'HTTP {st}']
    print(f'== {name} health: ' + ('OK' if not msgs else ' | '.join(msgs)))
sys.exit(0 if ok else 1)
