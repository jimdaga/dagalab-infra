#!/usr/bin/env python3
"""Idempotent Bazarr setup over its API (via a port-forward on 127.0.0.1:16767).

Env: BAZARR_CUR_KEY (key Bazarr accepts now, read from its config.yaml), BAZARR_KEY (key to end up with: from Vault, or
freshly generated on first run), SONARR_KEY, RADARR_KEY. Prints nothing secret; the caller stores BAZARR_KEY in Vault.

Applies: Sonarr/Radarr connections (same /data paths, no mapping), one "English" language profile as the default for new
and existing series/movies, free providers that need no account, and ignore image-based embedded subtitles
(PGS/VobSub make Jellyfin burn subtitles in while transcoding; a text .srt avoids that). Then syncs the library and
starts the missing-subtitle searches.
"""
import json, os, sys, time, urllib.error, urllib.parse, urllib.request

B = 'http://127.0.0.1:16767/api'
SVC = 'media.svc.cluster.local'
# no account needed; OpenSubtitles.com (best coverage) needs a login and can be added later
PROVIDERS = ['podnapisi', 'gestdown', 'yifysubtitles']   # tvsubtitles dropped 2026-10: its search page 404s
PROFILE = {'profileId': 1, 'name': 'English', 'cutoff': None, 'mustContain': [], 'mustNotContain': [],
           'originalFormat': False, 'tag': None,
           'items': [{'id': 1, 'language': 'en', 'hi': 'False', 'forced': 'False',
                      'audio_exclude': 'False', 'audio_only_include': 'False'}]}
key = os.environ['BAZARR_CUR_KEY']

def log(*a): print(*a, file=sys.stderr, flush=True)

def call(method, path, form=None):
    data = urllib.parse.urlencode(form, doseq=True).encode() if form is not None else None
    req = urllib.request.Request(B + path, method=method, data=data, headers={'X-API-KEY': key})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read(); return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]

st, status = call('GET', '/system/status')
if st != 200:
    sys.exit(f'bazarr API not reachable with the current key: {st}')
log(f'bazarr {status["data"]["bazarr_version"]}')

settings = {
    'settings-general-use_sonarr': 'true', 'settings-general-use_radarr': 'true',
    'settings-sonarr-ip': f'sonarr.{SVC}', 'settings-sonarr-port': '8989', 'settings-sonarr-base_url': '/',
    'settings-sonarr-ssl': 'false', 'settings-sonarr-apikey': os.environ['SONARR_KEY'],
    'settings-radarr-ip': f'radarr.{SVC}', 'settings-radarr-port': '7878', 'settings-radarr-base_url': '/',
    'settings-radarr-ssl': 'false', 'settings-radarr-apikey': os.environ['RADARR_KEY'],
    'settings-general-enabled_providers': PROVIDERS,
    'settings-general-serie_default_enabled': 'true', 'settings-general-serie_default_profile': '1',
    'settings-general-movie_default_enabled': 'true', 'settings-general-movie_default_profile': '1',
    'settings-general-ignore_pgs_subs': 'true', 'settings-general-ignore_vobsub_subs': 'true',
    'settings-analytics-enabled': 'false',
    'languages-enabled': ['en'],
    'languages-profiles': json.dumps([PROFILE]),
}
want_key = os.environ['BAZARR_KEY']
if want_key != key:
    settings['settings-auth-apikey'] = want_key
st, r = call('POST', '/system/settings', settings)
if st not in (200, 204):
    sys.exit(f'settings FAILED {st}: {r}')
key = want_key
log(f'settings: Sonarr/Radarr, English profile (default), providers {", ".join(PROVIDERS)}, ignore PGS/VobSub, analytics off (HTTP {st})')

st, profiles = call('GET', '/system/languages/profiles')
log('language profiles: ' + ', '.join(p['name'] for p in profiles))

# pull series/movies from Sonarr/Radarr, then give every existing one the English profile
for task in ('update_series', 'update_movies'):
    call('POST', '/system/tasks', {'taskid': task})
# the sync runs in the background (a few minutes for a full library): wait until the counts stop changing
last, steady = None, 0
for _ in range(120):
    time.sleep(10)
    st, series = call('GET', '/series?length=-1'); st2, movies = call('GET', '/movies?length=-1')
    counts = (series.get('total'), movies.get('total')) if st == st2 == 200 else None
    steady = steady + 1 if counts and counts == last and all(counts) else 0
    last = counts
    if steady >= 3:
        break
series, movies = series.get('data', []), movies.get('data', [])
log(f'library synced: {len(series)} series, {len(movies)} movies')
todo = [s['sonarrSeriesId'] for s in series if s.get('profileId') != 1]
if todo:
    st, r = call('POST', '/series', {'seriesid': todo, 'profileid': ['1'] * len(todo)})
    log(f'English profile -> {len(todo)} series ({st})')
todo = [m['radarrId'] for m in movies if m.get('profileId') != 1]
if todo:
    st, r = call('POST', '/movies', {'radarrid': todo, 'profileid': ['1'] * len(todo)})
    log(f'English profile -> {len(todo)} movies ({st})')
for task in ('wanted_search_missing_subtitles_series', 'wanted_search_missing_subtitles_movies'):
    st, _ = call('POST', '/system/tasks', {'taskid': task})
    log(f'started {task} ({st})')
