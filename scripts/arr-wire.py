#!/usr/bin/env python3
"""Idempotently wire Radarr/Sonarr/Bookshelf/Prowlarr over their APIs (via local port-forwards).
Env: RADARR_KEY, SONARR_KEY, PROWLARR_KEY, BOOKSHELF_KEY, JELLYFIN_KEY (Jellyfin API key "arr"). Ports: radarr 17878, sonarr 18989, prowlarr 19696,
bookshelf 18787, qbittorrent 18080 on localhost."""
import json, os, sys, urllib.parse, urllib.request, urllib.error

SVC = 'media.svc.cluster.local'
APPS = {
    'radarr':   dict(url='http://127.0.0.1:17878/api/v3', key=os.environ['RADARR_KEY'],  root='/data/movies', cat_field='movieCategory', cat='movies', port=7878),
    'sonarr':   dict(url='http://127.0.0.1:18989/api/v3', key=os.environ['SONARR_KEY'],  root='/data/tv',     cat_field='tvCategory',    cat='tv',     port=8989),
}
PROWLARR = dict(url='http://127.0.0.1:19696/api/v1', key=os.environ['PROWLARR_KEY'])
QBT = 'http://127.0.0.1:18080/api/v2'   # port-forwarded: arrives from localhost, so no login needed...
QBT_HOST = '127.0.0.1:8080'             # ...as long as the Host header names qBittorrent's own port (else 401)

# Per private tracker: seeding goals and Prowlarr priority (lower wins ties between equally good releases; TorrentLeech
# has the better releases and swarms; IPTorrents, added 2026-10-13, sits between it and wigornot). ratio/minutes None =
# seed forever (the choice for all of them, 2026-10-12: well past the trackers' minimums, TorrentLeech 1:1 or 10 days,
# IPTorrents 1:1 or 14 days, wigornot ratio 10 or 1 year; costs no extra disk since imports
# are hardlinks, and TorrentLeech's "torrents limit reached" counts downloads, not seeds). With numbers, qBittorrent stops
# a torrent when EITHER limit is hit and Sonarr/Radarr then remove it. Keyed by Prowlarr definition name; trackers =
# announce hostnames (IPTorrents announces on several rotating domains).
TRACKERS = {
    'torrentleech': dict(ratio=None, minutes=None, priority=10, trackers=('torrentleech.org', 'tleechreload.org')),
    'iptorrents':   dict(ratio=None, minutes=None, priority=20, trackers=('empirehost.me', 'stackoverflow.tech', 'bgp.technology')),
    'wigornot':     dict(ratio=None, minutes=None, priority=25, trackers=('wigornot.com',)),
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
    jwant = {'host': f'jellyfin.{SVC}', 'port': 8096, 'urlBase': '', 'useSsl': False, 'apiKey': os.environ['JELLYFIN_KEY'], 'notify': False, 'updateLibrary': True}
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

    # Quality profiles and custom formats (incl. no-AV1) are owned by Recyclarr (argocd/apps/recyclarr), not this script.

# Bookshelf (Readarr fork, audiobooks only): /api/v1 and Readarr field names, so it gets its own section.
# Audiobooks-only comes from the quality profile: only audio allowed, M4B > MP3 > Unknown Audio (TorrentLeech's
# audiobook titles often say just "[AUDIOBOOK]", which parses as Unknown Audio). Recyclarr doesn't support
# Readarr-family apps, so this script owns that profile (unlike Sonarr/Radarr's).
BOOKSHELF = dict(url='http://127.0.0.1:18787/api/v1', key=os.environ['BOOKSHELF_KEY'], root='/audiobooks/library', cat='audiobooks', port=8787)
AUDIO = [0, 13, 10, 12]   # Unknown Text, Unknown Audio, MP3, M4B: ordered lowest -> highest preference. Unknown Text is
# needed for TorrentLeech (its "[AUDIOBOOK]" releases sit in a books category); the release profile below guards it
b, bk = BOOKSHELF['url'], BOOKSHELF['key']
st, status = call(b, bk, 'GET', '/system/status')
print(f'== bookshelf {status.get("version") if st == 200 else st}')

# Release profile: the RAW release title must say it's audio, so an untagged ebook in a books category (parsed as
# Unknown Text, which the profile has to allow for TorrentLeech) is never grabbed. (A custom format can't do this: it
# matches Bookshelf's parsed title, and TorrentLeech's "Title - Author [AUDIOBOOK]" doesn't parse; size limits are
# disabled in Bookshelf's code.) Case-insensitive substring match.
AUDIO_TERMS = ['audiobook', 'audio book', 'm4b', 'mp3', 'unabridged']
st, cfs = call(b, bk, 'GET', '/customformat')
for c in [c for c in cfs if c['name'] == 'Audiobook']:   # earlier attempt (custom format guard): remove
    st, r = call(b, bk, 'DELETE', f'/customformat/{c["id"]}')
    print(f'   custom format Audiobook: removed (HTTP {st})')
    cfs = [x for x in cfs if x['id'] != c['id']]
st, rps = call(b, bk, 'GET', '/releaseprofile')
rp = next((r for r in rps if r.get('required')), None)
want_rp = dict(enabled=True, required=AUDIO_TERMS, ignored=[], indexerId=0, tags=[])
if rp and all(rp.get(k) == v for k, v in want_rp.items()):
    print('   release profile (audio terms required): present')
else:
    body = dict(rp or {}, **want_rp)
    st, r = call(b, bk, 'PUT' if rp else 'POST', f'/releaseprofile/{rp["id"]}' if rp else '/releaseprofile', body)
    print(f'   release profile (audio terms required): {"updated" if rp else "created"} (HTTP {st})' if st in (200, 201, 202) else f'   release profile FAILED {st}: {r}')
    ok &= st in (200, 201, 202)

st, profs = call(b, bk, 'GET', '/qualityprofile')
prof = next((x for x in profs if x['name'] == 'Audiobook'), None)
flat = lambda items: [c for i in items for c in (i.get('items') or [i])]
cf_ok = lambda pr: pr['minFormatScore'] == 0 and not any(fi['score'] for fi in pr['formatItems'])
if prof and [c['quality']['id'] for c in flat(prof['items']) if c['allowed']] == AUDIO and prof['cutoff'] == 12 and cf_ok(prof):
    print('   quality profile Audiobook: present')
else:
    st, schema = call(b, bk, 'GET', '/qualityprofile/schema')
    items = flat(schema['items'])
    for c in items:
        c['allowed'] = c['quality']['id'] in AUDIO
    items = [c for c in items if c['quality']['id'] not in AUDIO] + [next(c for c in items if c['quality']['id'] == q) for q in AUDIO]
    fitems = [dict(format=x['id'], name=x['name'], score=0) for x in cfs]
    body = dict(schema, name='Audiobook', upgradeAllowed=True, cutoff=12, items=items, formatItems=fitems,
                minFormatScore=0, cutoffFormatScore=0)
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

# Prowlarr -> Radarr/Sonarr/Bookshelf applications (full sync of indexers)
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
# Bookshelf registers as a Readarr app. Categories: audiobook (3030), audio (3000), and books (7020): TorrentLeech has no
# audiobook category and files audiobooks under Books (ebooks there are rejected by the audio-only quality profile).
BOOK_CATS = [3030, 3000, 7020]
cur_app = next((e for e in existing if e['implementation'] == 'Readarr'), None)
if cur_app and sorted(next(f['value'] for f in cur_app['fields'] if f['name'] == 'syncCategories')) == sorted(BOOK_CATS):
    print('   application Bookshelf (Readarr): present')
elif cur_app:
    setf(cur_app['fields'], 'syncCategories', BOOK_CATS)
    st, r = call(p['url'], p['key'], 'PUT', f'/applications/{cur_app["id"]}', cur_app)
    print(f'   application Bookshelf: categories {BOOK_CATS} (HTTP {st})' if st in (200, 202) else f'   update FAILED {st}: {r}'); ok &= st in (200, 202)
else:
    app = next(s for s in schema if s['implementation'] == 'Readarr')
    app.update(name='Bookshelf', syncLevel='fullSync')
    setf(app['fields'], 'prowlarrUrl', f'http://prowlarr.{SVC}:9696')
    setf(app['fields'], 'baseUrl', f'http://bookshelf.{SVC}:{BOOKSHELF["port"]}')
    setf(app['fields'], 'apiKey', BOOKSHELF['key'])
    setf(app['fields'], 'syncCategories', BOOK_CATS)
    st, r = call(p['url'], p['key'], 'POST', '/applications/test', app)
    if st not in (200, 202):
        print(f'   application Bookshelf test FAILED {st}: {r}'); ok = False
    else:
        st, r = call(p['url'], p['key'], 'POST', '/applications', app)
        print(f'   application Bookshelf: created (HTTP {st})' if st in (200, 201) else f'   create FAILED {st}: {r}'); ok &= st in (200, 201)

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
    seed = 'forever' if goal['ratio'] is None else f'ratio {goal["ratio"]} / {goal["minutes"] // 1440} days'
    print(f'   seed goal + priority {ix["name"]}: {seed}, priority {goal["priority"]} (HTTP {st})' if st in (200, 202) else f'   seed goal {ix["name"]} FAILED {st}: {r}')
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
# audiobooks category (created by Bookshelf's download client): explicit save path like movies/tv; the hourly
# seed-cleanup CronJob (argocd/apps/bookshelf) only deletes torrents under exactly this path
cats = qbt('/torrents/categories')
if cats.get('audiobooks', {}).get('savePath') != '/data/downloads/audiobooks':
    qbt('/torrents/createCategory' if 'audiobooks' not in cats else '/torrents/editCategory',
        {'category': 'audiobooks', 'savePath': '/data/downloads/audiobooks'})
    print('   category audiobooks: save path /data/downloads/audiobooks')
else:
    print('   category audiobooks: present')
ok_vals = lambda v: (-1, -2) if v == -1 else (v,)   # -2 = "use global", and the global limits are off: also unlimited
for name, goal in TRACKERS.items():
    ratio, minutes = (-1 if goal['ratio'] is None else goal['ratio']), (-1 if goal['minutes'] is None else goal['minutes'])   # -1 = no limit
    stale = [t['hash'] for t in qbt('/torrents/info')
             if any(h in (t.get('tracker') or '') for h in goal['trackers'])
             and not (t['ratio_limit'] in ok_vals(ratio) and t['seeding_time_limit'] in ok_vals(minutes))]
    if stale:
        qbt('/torrents/setShareLimits', {'hashes': '|'.join(stale), 'ratioLimit': ratio,
                                         'seedingTimeLimit': minutes, 'inactiveSeedingTimeLimit': -2,
                                         'shareLimitAction': 'Default'})   # Default = global max_ratio_act (Stop)
        qbt('/torrents/start', {'hashes': '|'.join(stale)})   # resume any that already stopped at the old goal
    print(f'   {name} torrents: {len(stale)} share limits updated')

# health
for name, (u, k) in {**{n: (a['url'], a['key']) for n, a in APPS.items()}, 'bookshelf': (b, bk), 'prowlarr': (p['url'], p['key'])}.items():
    st, h = call(u, k, 'GET', '/health')
    msgs = [x['message'] for x in h] if st == 200 else [f'HTTP {st}']
    print(f'== {name} health: ' + ('OK' if not msgs else ' | '.join(msgs)))
sys.exit(0 if ok else 1)
