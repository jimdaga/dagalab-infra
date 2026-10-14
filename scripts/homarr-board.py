#!/usr/bin/env python3
"""Lay out Homarr's boards and header tabs (run by scripts/homarr-setup.sh after onboarding; safe to re-run).

Env: HOMARR_USER/HOMARR_PASSWORD (to read boards and set the header), HOMARR_API_KEY (REST), CA_FILE,
UNIFI_PASSWORD / SYNOLOGY_PASSWORD (the `homepage` accounts on the UCG and DSM, from Vault secret/homepage-devices),
HA_TOKEN (Home Assistant long-lived token, Vault secret/home-assistant api-token).
Four boards, pinned as tabs in the header (centre zone) of HOMARR_USER, each with an icon (shown as the tab) and title:
  Home      clock, weather, media launchers, now playing, recently added, requests
  Library   calendar, missing/queued, request stats, subtitles
  Downloads qBittorrent, VPN, indexers
  Platform  launchers for the platform UIs (incl. Homarr's Kubernetes pages) and Homepage's bookmarks; UniFi and NAS
            widgets (UniFi, NAS and Home Assistant integrations created here, the devices re-using Homepage's `homepage` accounts)
Widgets keep the integrations onboarding created (taken from whichever board currently has that widget kind).
The onboarding board "dagalab" is renamed to Home, so it stays the user's home board.
"""
import http.client, json, os, random, socket, ssl, string, sys, urllib.parse

HOST = 'homarr.lab.internal'
_g = socket.getaddrinfo
socket.getaddrinfo = lambda h, *a, **k: _g('192.168.3.201' if h == HOST else h, *a, **k)
CTX = ssl.create_default_context(cafile=os.environ['CA_FILE'])
ICONS = 'https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons'
BOARDS = ['Home', 'Library', 'Downloads', 'Platform']   # tab order
cookies = {}

def log(*a): print(*a, file=sys.stderr, flush=True)

def request(method, path, body=None, form=None, api=False, raw=None):
    c = http.client.HTTPSConnection(HOST, context=CTX, timeout=120)
    h = {'Origin': f'https://{HOST}', 'Sec-Fetch-Site': 'same-origin'}
    if api: h['ApiKey'] = os.environ['HOMARR_API_KEY']
    if cookies: h['Cookie'] = '; '.join(f'{k}={v}' for k, v in cookies.items())
    data = None
    if body is not None: data = json.dumps(body); h['Content-Type'] = 'application/json'
    elif form is not None: data = urllib.parse.urlencode(form); h['Content-Type'] = 'application/x-www-form-urlencoded'
    elif raw is not None: data, h['Content-Type'] = raw
    c.request(method, path, body=data, headers=h); r = c.getresponse(); raw = r.read()
    for k, v in r.getheaders():
        if k.lower() == 'set-cookie':
            n, _, rest = v.partition('='); cookies[n] = rest.split(';', 1)[0]
    try: return r.status, (json.loads(raw) if raw else None)
    except ValueError: return r.status, raw.decode(errors='replace')[:400]

def ok(st, r, what):
    if st not in (200, 201, 204): raise SystemExit(f'{what} failed: HTTP {st} {json.dumps(r)[:800]}')
    return r

def new_id(): return ''.join(random.choices(string.ascii_lowercase + string.digits, k=24))

def get_board(name):
    st, r = request('GET', '/api/trpc/board.getBoardByName?input=' + urllib.parse.quote(json.dumps({'json': {'name': name}})))
    return r['result']['data']['json'] if st == 200 else None

# sign in
st, csrf = request('GET', '/api/auth/csrf')
request('POST', '/api/auth/callback/credentials', form={'csrfToken': csrf['csrfToken'], 'name': os.environ['HOMARR_USER'],
                                                       'password': os.environ['HOMARR_PASSWORD'], 'callbackUrl': f'https://{HOST}/'})
me = request('GET', '/api/auth/session')[1]['user']

def trpc(proc, data, what):
    return ok(*request('POST', f'/api/trpc/{proc}', body={'json': data}), what)['result']['data']['json']

# device integrations (onboarding only made the media ones). The UCG serves a self-signed cert for unifi.local:
# trust it as its own root and pin it to the IP. DSM's cert is signed by a per-device "Synology Inc. CA" it doesn't
# serve, so DSM is reached over plain HTTP on the lab VLAN instead.
UCG, DSM = '192.168.3.1', '192.168.3.20'
have = {i['name']: i for i in ok(*request('GET', '/api/trpc/integration.all'), 'listing integrations')['result']['data']['json']}
if 'UniFi' not in have:
    pem = ssl.get_server_certificate((UCG, 443))
    fname = 'unifi-ucg.crt'
    b = 'dagalab' + new_id()
    body = (f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{fname}"\r\n'
            f'Content-Type: application/x-x509-ca-cert\r\n\r\n{pem}\r\n--{b}--\r\n').encode()
    ok(*request('POST', '/api/trpc/certificates.addCertificate', raw=(body, f'multipart/form-data; boundary={b}')), 'trusting UCG cert')
    trpc('certificates.trustHostnameMismatch', {'hostname': UCG, 'certificate': pem}, 'pinning UCG cert to its IP')
    log(f'certificate: UCG self-signed cert trusted for {UCG}')
DEVICES = [  # name, kind, url, secrets (kind, value)
    ('UniFi', 'unifiController', f'https://{UCG}', [('username', 'homepage'), ('password', os.environ['UNIFI_PASSWORD'])]),
    ('daga-nas', 'synology', f'http://{DSM}:5000', [('username', 'homepage'), ('password', os.environ['SYNOLOGY_PASSWORD'])]),
    # long-lived token "dagalab" (scripts/home-assistant-setup.sh); in-cluster, so no lab CA / DNS needed
    ('Home Assistant', 'homeAssistant', 'http://home-assistant.home-assistant.svc.cluster.local:8123',
     [('apiKey', os.environ['HA_TOKEN'])]),
]
for name, kind, url, secrets in DEVICES:
    if name in have:
        continue
    r = trpc('integration.create', {'name': name, 'kind': kind, 'url': url, 'attemptSearchEngineCreation': False,
                                    'secrets': [{'kind': k, 'value': v} for k, v in secrets]},
             f'creating integration {name}')
    if isinstance(r, dict) and r.get('error'):
        raise SystemExit(f'integration {name}: connection test failed: {json.dumps(r["error"])[:400]}')
    log(f'integration {name}: created ({url})')
integrations = {i['name']: i for i in ok(*request('GET', '/api/trpc/integration.all'), 'listing integrations')['result']['data']['json']}

# boards: rename the onboarding board to Home, create the others
existing = {b['name']: b for b in ok(*request('GET', '/api/boards', api=True), 'listing boards')}
if 'Home' not in existing and 'dagalab' in existing:
    ok(*request('PATCH', f'/api/boards/{existing["dagalab"]["id"]}/name', body={'name': 'Home'}, api=True), 'renaming board')
    log('board dagalab -> Home')
for name in BOARDS:
    if name not in existing and not (name == 'Home' and 'dagalab' in existing):
        ok(*request('POST', '/api/boards', body={'name': name, 'columnCount': 10, 'isPublic': False}, api=True), f'creating board {name}')
        log(f'board {name}: created')
boards = {name: get_board(name) for name in BOARDS}

# apps: icons for the link tiles, Homepage's bookmarks, Homarr's own Kubernetes pages
apps = ok(*request('GET', '/api/apps', api=True), 'listing apps')
by_name = {a['name']: a for a in apps}
WANT_APPS = {  # name: (href, iconUrl, description); existing apps only get their icon corrected
    'Kubernetes': (f'https://{HOST}/manage/tools/kubernetes', f'{ICONS}/svg/kubernetes.svg', 'Nodes, pods, namespaces'),
    'Argo CD': (None, f'{ICONS}/svg/argo-cd.svg', None),
    'Grafana': (None, f'{ICONS}/svg/grafana.svg', None),
    'Longhorn': (None, f'{ICONS}/svg/longhorn.svg', None),
    'Vault': (None, f'{ICONS}/svg/hashicorp-vault.svg', None),
    'Home Assistant': ('https://home.lab.internal', f'{ICONS}/svg/home-assistant.svg', 'Home automation'),
    'Homepage': (None, f'{ICONS}/png/homepage.png', None),
    'dagalab-infra': ('https://github.com/jimdaga/dagalab-infra', f'{ICONS}/svg/github.svg', 'GitOps repo'),
    'UniFi': ('https://192.168.3.1', f'{ICONS}/svg/ubiquiti-unifi.svg', 'Cloud Gateway'),
    'daga-nas (DSM)': ('https://192.168.3.20:5001', f'{ICONS}/svg/synology-dsm.svg', 'NAS'),
    'Proton Mail': ('https://mail.proton.me/', f'{ICONS}/svg/proton-mail.svg', None),
}
for name, (href, icon, desc) in WANT_APPS.items():
    a = by_name.get(name)
    if a is None:
        ok(*request('POST', '/api/apps', body={'name': name, 'href': href, 'iconUrl': icon, 'description': desc, 'pingUrl': None}, api=True), f'creating app {name}')
        log(f'app {name}: created')
    elif a.get('iconUrl') != icon:
        body = {k: a.get(k) for k in ('name', 'description', 'href', 'pingUrl')}; body['iconUrl'] = icon
        ok(*request('PATCH', f'/api/apps/{a["id"]}', body=body, api=True), f'app {name} icon')
by_name = {a['name']: a for a in ok(*request('GET', '/api/apps', api=True), 'listing apps')}

# widget templates (kind -> options + integrations), from whichever board has them now
templates = {}
for b in boards.values():
    for it in b['items']:
        if it['kind'] != 'app' and it['kind'] not in templates:
            templates[it['kind']] = it
LEOMINSTER = {'name': 'Leominster', 'latitude': 42.5274, 'longitude': -71.7563}
OPTIONS = {
    'clock': {'customTimeFormat': 'h:mm A', 'showDate': True},
    'weather': {'isFormatFahrenheit': True, 'location': LEOMINSTER, 'showCity': True, 'hasForecast': False},
    'healthMonitoring': {'fahrenheit': True},
}

def widget(kind, integration=None, **opts):
    t = templates.get(kind) or {'integrationIds': [integrations[integration]['id']]}   # new widget kinds start blank
    return {'kind': kind, 'options': {**(t.get('options') or {}), **OPTIONS.get(kind, {}), **opts},
            'integrationIds': t.get('integrationIds') or [], 'advancedOptions': t.get('advancedOptions') or {}}

def launcher(name):
    return {'kind': 'app', 'options': {'appId': by_name[name]['id'], 'showTitle': True, 'pingEnabled': True},
            'integrationIds': [], 'advancedOptions': {}}

def launchers(names, x0, y): return [(launcher(n), x0 + i, y, 1, 1) for i, n in enumerate(names)]

# (item, x, y, w, h) on each board's 10-column base layout; mobile stacks them in this order on 3 columns
LAYOUT = {
    'Home': [
        (widget('clock'), 0, 0, 2, 1), (widget('weather'), 2, 0, 2, 1),
        *launchers(('Jellyfin', 'Seerr', 'Sonarr', 'Radarr', 'Bazarr', 'Prowlarr'), 4, 0),
        (widget('mediaServer'), 0, 1, 4, 3), (widget('mediaReleases'), 4, 1, 3, 3), (widget('mediaRequests-requestList'), 7, 1, 3, 3),
    ],
    'Library': [
        *launchers(('Sonarr', 'Radarr', 'Bazarr', 'Seerr', 'Jellyfin'), 0, 0),
        (widget('calendar'), 0, 1, 4, 4), (widget('mediaMissing'), 4, 1, 4, 4),
        (widget('mediaRequests-requestStats'), 8, 0, 2, 3), (widget('bazarr'), 8, 3, 2, 2),
    ],
    'Downloads': [
        (widget('downloads'), 0, 0, 7, 4),
        (widget('vpn'), 7, 0, 3, 1), (widget('indexerManager'), 7, 1, 3, 2),
        *launchers(('qBittorrent', 'Prowlarr'), 7, 3),
    ],
    'Platform': [
        *launchers(('Kubernetes', 'Argo CD', 'Grafana', 'Longhorn', 'Vault', 'Home Assistant'), 0, 0),
        *launchers(('Homepage', 'UniFi', 'daga-nas (DSM)', 'dagalab-infra', 'Proton Mail'), 0, 1),
        (widget('healthMonitoring', 'daga-nas'), 0, 2, 4, 2), (widget('systemDisks', 'daga-nas'), 4, 2, 2, 2),
        (widget('networkControllerSummary', 'UniFi'), 6, 2, 2, 2), (widget('networkControllerStatus', 'UniFi', content='wifi'), 8, 2, 2, 2),
        (widget('systemResources', 'daga-nas'), 0, 4, 8, 2), (widget('networkControllerStatus', 'UniFi', content='wired'), 8, 4, 2, 2),
    ],
}

for name, rows in LAYOUT.items():
    b = boards[name]
    lays = sorted(b['layouts'], key=lambda l: l.get('breakpoint') or 0)
    base, mobile = lays[-1], (lays[0] if len(lays) > 1 else None)
    section = next(s for s in b['sections'] if s['kind'] == 'empty')
    # keep ids of items already on this board (same kind, or same app) so widget state survives re-runs
    pool = {}
    for it in b['items']:
        pool.setdefault((it['kind'], (it.get('options') or {}).get('appId')), []).append(it['id'])
    items, my, mx = [], 0, 0
    for it, x, y, w, h in rows:
        ids = pool.get((it['kind'], it['options'].get('appId')))
        lay = [{'layoutId': base['id'], 'sectionId': section['id'], 'xOffset': x, 'yOffset': y, 'width': w, 'height': h}]
        if mobile:
            mw = min(w, mobile['columnCount'])
            if mx + mw > mobile['columnCount']: my += 1; mx = 0
            lay.append({'layoutId': mobile['id'], 'sectionId': section['id'], 'xOffset': mx, 'yOffset': my, 'width': mw, 'height': h})
            mx += mw
            if mx >= mobile['columnCount']: my += h; mx = 0
        items.append({**it, 'id': ids.pop(0) if ids else new_id(), 'layouts': lay})
    sections = [{k: s[k] for k in ('id', 'kind', 'xOffset', 'yOffset')} for s in b['sections'] if s['kind'] == 'empty']
    ok(*request('PUT', f'/api/boards/{b["id"]}/content', body={'sections': sections, 'items': items}, api=True), f'saving board {name}')
    log(f'board {name}: {len(items)} items')

# tab look: the header renders board shortcuts as the board's logo (initials without one) with the name as tooltip,
# and shows the current board's logo + page title top-left, so each board gets an icon and its name as title
LOOK = {'Home': 'jellyfin', 'Library': 'sonarr', 'Downloads': 'qbittorrent', 'Platform': 'kubernetes'}
for name, icon in LOOK.items():
    bid = boards[name]['id']
    cur = ok(*request('GET', f'/api/boards/{bid}/settings', api=True), f'reading {name} settings')
    want = {'logoImageUrl': f'{ICONS}/svg/{icon}.svg', 'pageTitle': name, 'metaTitle': f'{name} | dagalab'}
    if any(cur.get(k) != v for k, v in want.items()):
        ok(*request('PATCH', f'/api/boards/{bid}/settings', body={**{k: v for k, v in cur.items() if k != 'id'}, **want}, api=True), f'{name} settings')
        log(f'board {name}: logo {icon}, title {name}')

# header: board tabs in the centre, search as an icon
header = {'version': 3, 'visible': True, 'searchDisplay': 'icon', 'logoDisplay': 'logoAndText', 'zones': {
    'left': [{'type': 'builtin', 'id': 'logo'}],
    'center': [{'type': 'board', 'boardId': boards[n]['id']} for n in BOARDS],
    'right': [{'type': 'builtin', 'id': i} for i in ('search', 'boardEdit', 'settings', 'user')],
}}
ok(*request('PATCH', '/api/users/header-preferences', body={'id': me['id'], 'headerPreferences': header}, api=True), 'header preferences')
log(f'header: tabs {" | ".join(BOARDS)} for {me.get("name")}')
