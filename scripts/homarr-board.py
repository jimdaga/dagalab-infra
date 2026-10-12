#!/usr/bin/env python3
"""Lay out Homarr's "dagalab" board (run by scripts/homarr-setup.sh after onboarding; safe to re-run).

Env: HOMARR_USER/HOMARR_PASSWORD (to read the board), HOMARR_API_KEY (REST), CA_FILE.
Keeps the widgets onboarding created (their integrations and ids), drops broken/unconfigured items, adds launcher
tiles (incl. Homepage's bookmarks), sets icons/weather/clock, and rewrites the layout in rows by purpose:
  launch bar (clock, weather, media apps) / watching / library / downloads / platform + bookmark launchers.
"""
import http.client, json, os, random, socket, ssl, string, sys, urllib.parse

HOST = 'homarr.lab.internal'
_g = socket.getaddrinfo
socket.getaddrinfo = lambda h, *a, **k: _g('192.168.3.201' if h == HOST else h, *a, **k)
CTX = ssl.create_default_context(cafile=os.environ['CA_FILE'])
ICONS = 'https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons'
cookies = {}

def log(*a): print(*a, file=sys.stderr, flush=True)

def request(method, path, body=None, form=None, api=False):
    c = http.client.HTTPSConnection(HOST, context=CTX, timeout=120)
    h = {'Origin': f'https://{HOST}', 'Sec-Fetch-Site': 'same-origin'}
    if api: h['ApiKey'] = os.environ['HOMARR_API_KEY']
    if cookies: h['Cookie'] = '; '.join(f'{k}={v}' for k, v in cookies.items())
    data = None
    if body is not None: data = json.dumps(body); h['Content-Type'] = 'application/json'
    elif form is not None: data = urllib.parse.urlencode(form); h['Content-Type'] = 'application/x-www-form-urlencoded'
    c.request(method, path, body=data, headers=h); r = c.getresponse(); raw = r.read()
    for k, v in r.getheaders():
        if k.lower() == 'set-cookie':
            n, _, rest = v.partition('='); cookies[n] = rest.split(';', 1)[0]
    try: return r.status, (json.loads(raw) if raw else None)
    except ValueError: return r.status, raw.decode(errors='replace')[:400]

def new_id(): return ''.join(random.choices(string.ascii_lowercase + string.digits, k=24))

# sign in, read the board
st, csrf = request('GET', '/api/auth/csrf')
request('POST', '/api/auth/callback/credentials', form={'csrfToken': csrf['csrfToken'], 'name': os.environ['HOMARR_USER'],
                                                       'password': os.environ['HOMARR_PASSWORD'], 'callbackUrl': f'https://{HOST}/'})
st, r = request('GET', '/api/trpc/board.getBoardByName?input=' + urllib.parse.quote(json.dumps({'json': {'name': 'dagalab'}})))
if st != 200: raise SystemExit(f'reading board failed: HTTP {st} {r}')
board = r['result']['data']['json']
layouts = {l['name']: l for l in board['layouts']}
base, mobile = layouts['Base'], layouts['Mobile']
section = next(s for s in board['sections'] if s['kind'] == 'empty')

# apps: icons for the link tiles, plus Homepage's bookmarks
st, apps = request('GET', '/api/apps', api=True)
by_name = {a['name']: a for a in apps}
WANT_APPS = {  # name: (href, iconUrl, description); existing apps only get their icon/href corrected
    'Argo CD': (None, f'{ICONS}/svg/argo-cd.svg', None),
    'Grafana': (None, f'{ICONS}/svg/grafana.svg', None),
    'Longhorn': (None, f'{ICONS}/svg/longhorn.svg', None),
    'Vault': (None, f'{ICONS}/svg/hashicorp-vault.svg', None),
    'Homepage': (None, f'{ICONS}/png/homepage.png', None),
    'dagalab-infra': ('https://github.com/jimdaga/dagalab-infra', f'{ICONS}/svg/github.svg', 'GitOps repo'),
    'UniFi': ('https://192.168.3.1', f'{ICONS}/svg/ubiquiti-unifi.svg', 'Cloud Gateway'),
    'daga-nas (DSM)': ('https://192.168.3.20:5001', f'{ICONS}/svg/synology-dsm.svg', 'NAS'),
    'Proton Mail': ('https://mail.proton.me/', f'{ICONS}/svg/proton-mail.svg', None),
}
for name, (href, icon, desc) in WANT_APPS.items():
    a = by_name.get(name)
    if a is None:
        st, a = request('POST', '/api/apps', body={'name': name, 'href': href, 'iconUrl': icon, 'description': desc, 'pingUrl': None}, api=True)
        if st not in (200, 201): raise SystemExit(f'creating app {name} failed: HTTP {st} {a}')
        log(f'app {name}: created')
        by_name[name] = a if isinstance(a, dict) and a.get('id') else None
    elif a.get('iconUrl') != icon:
        body = {k: a.get(k) for k in ('name', 'description', 'href', 'pingUrl')}; body['iconUrl'] = icon
        st, r = request('PATCH', f'/api/apps/{a["id"]}', body=body, api=True)
        log(f'app {name}: icon {"set" if st in (200, 204) else f"FAILED {st} {r}"}')
st, apps = request('GET', '/api/apps', api=True)
by_name = {a['name']: a for a in apps}

# widgets onboarding created, keyed by kind (keep id + integrations + options)
widgets = {it['kind']: it for it in board['items'] if it['kind'] != 'app'}
app_items = {it['options'].get('appId'): it for it in board['items'] if it['kind'] == 'app'}

def widget(kind, options=None):
    it = dict(widgets[kind]); it['options'] = {**(it.get('options') or {}), **(options or {})}; return it

def launcher(app_name):
    app = by_name[app_name]
    it = app_items.get(app['id']) or {'kind': 'app', 'id': new_id(), 'integrationIds': [], 'advancedOptions': {}}
    return {**it, 'options': {**(it.get('options') or {}), 'appId': app['id'], 'showTitle': True, 'pingEnabled': True}}

LEOMINSTER = {'name': 'Leominster', 'latitude': 42.5274, 'longitude': -71.7563}
# (item, base x, y, w, h) on the 10-column base layout; mobile stacks them in this order on 3 columns
ROWS = [
    # launch bar
    (widget('clock', {'customTimeFormat': 'h:mm A', 'showDate': True}), 0, 0, 2, 1),
    (widget('weather', {'isFormatFahrenheit': True, 'location': LEOMINSTER, 'showCity': True, 'hasForecast': False}), 2, 0, 2, 1),
    *[(launcher(n), 4 + i, 0, 1, 1) for i, n in enumerate(('Jellyfin', 'Seerr', 'Sonarr', 'Radarr', 'Bazarr', 'Prowlarr'))],
    # watching
    (widget('mediaServer'), 0, 1, 4, 2),
    (widget('mediaReleases'), 4, 1, 3, 2),
    (widget('mediaRequests-requestList'), 7, 1, 3, 2),
    # library
    (widget('calendar'), 0, 3, 3, 3),
    (widget('mediaMissing'), 3, 3, 3, 3),
    (widget('mediaRequests-requestStats'), 6, 3, 2, 3),
    (widget('bazarr'), 8, 3, 2, 3),
    # downloads
    (widget('downloads'), 0, 6, 6, 3),
    (widget('vpn'), 6, 6, 4, 1),
    (widget('indexerManager'), 6, 7, 4, 2),
    # platform + bookmarks as one launcher row (Homepage's bookmarks are launcher tiles here)
    *[(launcher(n), i, 9, 1, 1) for i, n in enumerate(('qBittorrent', 'Argo CD', 'Grafana', 'Longhorn', 'Vault', 'Homepage',
                                                        'UniFi', 'daga-nas (DSM)', 'dagalab-infra', 'Proton Mail'))],
]

items, my, mx = [], 0, 0
for it, x, y, w, h in ROWS:
    mw = min(w, 3)
    if mx + mw > 3: my += 1; mx = 0   # mobile: wrap at 3 columns (1x1 tiles pack three to a row)
    lay = [{'layoutId': base['id'], 'sectionId': section['id'], 'xOffset': x, 'yOffset': y, 'width': w, 'height': h},
           {'layoutId': mobile['id'], 'sectionId': section['id'], 'xOffset': mx, 'yOffset': my, 'width': mw, 'height': h}]
    if mw == 3: my += h; mx = 0
    else:
        mx += mw
        if mx >= 3: my += h; mx = 0
    items.append({'kind': it['kind'], 'id': it['id'], 'options': it.get('options') or {},
                  'integrationIds': it.get('integrationIds') or [], 'advancedOptions': it.get('advancedOptions') or {},
                  'layouts': lay})
dropped = sorted({it['kind'] for it in board['items']} - {i['kind'] for i in items})
sections = [{k: s[k] for k in ('id', 'kind', 'xOffset', 'yOffset')} for s in board['sections'] if s['kind'] == 'empty']
st, r = request('PUT', f'/api/boards/{board["id"]}/content', body={'sections': sections, 'items': items}, api=True)
if st not in (200, 204): raise SystemExit(f'saving board failed: HTTP {st} {json.dumps(r)[:800]}')
log(f'board saved: {len(items)} items; removed: {", ".join(dropped) or "nothing"} + broken app tiles')
