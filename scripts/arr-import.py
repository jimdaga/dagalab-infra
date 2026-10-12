#!/usr/bin/env python3
"""Import an existing library into Radarr/Sonarr over their APIs (run via scripts/arr-run.sh).

  arr-import.py plan     look up every folder, write a match report (no changes)
  arr-import.py add      add confident matches (+ any ids in OVERRIDES), unmonitored, no search
  arr-import.py rescan   rescan disks, then report what has files
  arr-import.py rename   move folders to the naming format and rename files, for everything imported

Existing items are added UNMONITORED with monitor=none: importing never triggers downloads.
Confident = normalised title equal to the TMDB/TVDB title (or an alternate title) and year within 1.
"""
import json, os, re, sys, time, unicodedata, urllib.parse, urllib.request, urllib.error

MODE = sys.argv[1] if len(sys.argv) > 1 else 'plan'
REPORT = os.environ.get('IMPORT_REPORT', '/tmp/arr-import-report.json')
QUALITY = 'HD-1080p'
# Manual decisions for folders the matcher can't settle: folder name -> tmdbId (movies) / tvdbId (shows), or None to skip.
OVERRIDES = json.loads(os.environ.get('IMPORT_OVERRIDES', '{}'))

APPS = {
    'radarr': dict(port=17878, key='RADARR_KEY', root='/data/movies', kind='movie', idf='tmdbId'),
    'sonarr': dict(port=18989, key='SONARR_KEY', root='/data/tv', kind='series', idf='tvdbId'),
}

def call(app, method, path, body=None, timeout=120):
    a = APPS[app]
    req = urllib.request.Request(f"http://127.0.0.1:{a['port']}/api/v3{path}", method=method,
                                 headers={'X-Api-Key': os.environ[a['key']], 'Content-Type': 'application/json'},
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(); return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]

def norm(s):
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode().lower().replace('&', 'and')
    s = re.sub(r'\((us|uk)\)', r'\1', s)
    return re.sub(r'[^a-z0-9]', '', s)

def split(folder):
    m = re.match(r'^(.*?)\s*\((\d{4})\)$', folder)
    return (m.group(1), int(m.group(2))) if m else (folder, None)

def wait_commands(app, ids, label):
    for _ in range(360):
        pending = [i for i in ids if call(app, 'GET', f'/command/{i}')[1].get('status') not in ('completed', 'failed', 'aborted')]
        if not pending: break
        time.sleep(5)
    states = [call(app, 'GET', f'/command/{i}')[1].get('status') for i in ids]
    print(f'   {label}: ' + ', '.join(f'{s}={states.count(s)}' for s in sorted(set(states))))

def listdir(app):
    # ask the app itself what's in its root folder (unmapped folders), so paths are exactly what it sees
    st, roots = call(app, 'GET', '/rootfolder')
    root = next(r for r in roots if r['path'].rstrip('/') == APPS[app]['root'])
    return sorted(u['name'] for u in root.get('unmappedFolders', []) if not u['name'].startswith('@'))

def plan():
    report = {}
    for app, a in APPS.items():
        rows = []
        for folder in listdir(app):
            title, year = split(folder)
            if folder in OVERRIDES:
                rows.append(dict(folder=folder, status='override', id=OVERRIDES[folder])); continue
            st, res = call(app, 'GET', f"/{a['kind']}/lookup?term=" + urllib.parse.quote(f'{title} {year or ""}'.strip()))
            res = res if st == 200 and isinstance(res, list) else []
            best, why = None, 'no result'
            for r in res[:10]:
                names = {norm(r.get('title', ''))} | {norm(t.get('title', '')) for t in r.get('alternateTitles') or []}
                if r.get('originalTitle'): names.add(norm(r['originalTitle']))
                if norm(title) in names and (year is None or abs((r.get('year') or 0) - year) <= 1):
                    best, why = r, 'exact'; break
            top = res[0] if res else None
            row = dict(folder=folder, status='confident' if best else 'uncertain', why=why)
            pick = best or top
            if pick:
                row.update(id=pick.get(a['idf']), match=f"{pick.get('title')} ({pick.get('year')})")
            rows.append(row)
        report[app] = rows
        c = sum(r['status'] == 'confident' for r in rows)
        print(f"== {app}: {len(rows)} unmapped folders -> {c} confident, {len(rows) - c} need review")
        for r in rows:
            if r['status'] != 'confident':
                print(f"   ? {r['folder']!r:62} best guess: {r.get('match')} [{APPS[app]['idf']} {r.get('id')}]")
    json.dump(report, open(REPORT, 'w'), indent=1)
    print(f'report: {REPORT}')

def add():
    report = json.load(open(REPORT))
    for app, a in APPS.items():
        st, profiles = call(app, 'GET', '/qualityprofile')
        qp = next(p['id'] for p in profiles if p['name'] == QUALITY)
        st, existing = call(app, 'GET', f"/{a['kind']}")
        have = {x[a['idf']] for x in existing}
        added = failed = skipped = 0
        for r in report[app]:
            if r['status'] not in ('confident', 'override') or not r.get('id'):
                skipped += 1; continue
            if r['id'] in have:
                skipped += 1; continue
            st, res = call(app, 'GET', f"/{a['kind']}/lookup?term=" + urllib.parse.quote(f"{'tmdb' if app == 'radarr' else 'tvdb'}:{r['id']}"))
            item = res[0] if st == 200 and res else None
            if not item:
                # the metadata server sometimes 500s on id lookups; a title search returns the same entry
                title, year = split(r['folder'])
                st, res = call(app, 'GET', f"/{a['kind']}/lookup?term=" + urllib.parse.quote(f'{title} {year or ""}'.strip()))
                item = next((x for x in res if x.get(a['idf']) == r['id']), None) if st == 200 and isinstance(res, list) else None
            if not item:
                print(f"   ! lookup by id failed: {r['folder']}"); failed += 1; continue
            item.update(qualityProfileId=qp, rootFolderPath=a['root'], path=f"{a['root']}/{r['folder']}", monitored=False, tags=[])
            if app == 'radarr':
                item.update(minimumAvailability='released', addOptions={'monitor': 'none', 'searchForMovie': False})
            else:
                item.update(seasonFolder=True, monitorNewItems='none',
                            addOptions={'monitor': 'none', 'searchForMissingEpisodes': False, 'searchForCutoffUnmetEpisodes': False})
                for s in item.get('seasons', []): s['monitored'] = False
            st, res = call(app, 'POST', f"/{a['kind']}", item)
            if st in (200, 201): added += 1; have.add(r['id'])
            else: failed += 1; print(f"   ! add failed {r['folder']}: {st} {res}")
        print(f'== {app}: added {added}, failed {failed}, skipped {skipped}')

def verify_and_rename(do_rename):
    for app, a in APPS.items():
        st, items = call(app, 'GET', f"/{a['kind']}")
        ids = [x['id'] for x in items]
        if app == 'radarr':
            nofile = [x['title'] for x in items if not x.get('hasFile')]
        else:
            nofile = [x['title'] for x in items if (x.get('statistics') or {}).get('episodeFileCount', 0) == 0]
        mon = sum(1 for x in items if x.get('monitored'))
        print(f"== {app}: {len(items)} in library, {mon} monitored, {len(nofile)} with no files detected" + (f": {nofile}" if nofile else ''))
        if not do_rename or not ids: continue
        # 1) move folders to root/<naming format>  2) rename files to the file format
        ed = {'movieIds' if app == 'radarr' else 'seriesIds': ids, 'rootFolderPath': a['root'], 'moveFiles': True}
        st, res = call(app, 'PUT', f"/{a['kind']}/editor", ed)
        print(f'   folder move/rename request: HTTP {st}')
        time.sleep(20)
        # wait for the move commands Radarr/Sonarr queued
        st, cmds = call(app, 'GET', '/command')
        wait_commands(app, [c['id'] for c in cmds if c['name'] in ('MoveMovie', 'MoveSeries', 'BulkMoveMovie', 'BulkMoveSeries')], 'folder moves')
        name = 'RenameMovie' if app == 'radarr' else 'RenameSeries'
        st, c = call(app, 'POST', '/command', {'name': name, ('movieIds' if app == 'radarr' else 'seriesIds'): ids})
        wait_commands(app, [c['id']], 'file renames')

if MODE == 'plan': plan()
elif MODE == 'add': add(); time.sleep(60); verify_and_rename(False)
elif MODE == 'verify': verify_and_rename(False)
elif MODE == 'rescan':
    for app in APPS:
        st, c = call(app, 'POST', '/command', {'name': 'RescanMovie' if app == 'radarr' else 'RescanSeries'})
        wait_commands(app, [c['id']], f'{app} disk rescan')
    verify_and_rename(False)
elif MODE == 'rename': verify_and_rename(True)
else: sys.exit(f'unknown mode {MODE}')
