#!/usr/bin/env python3
"""First-run setup for Homarr (trial alongside Homepage), through https://homarr.lab.internal.

Env: HOMARR_USER/HOMARR_PASSWORD (admin to create), CA_FILE (homelab CA), and service keys SONARR_KEY, RADARR_KEY,
PROWLARR_KEY, BAZARR_KEY, JELLYFIN_KEY, SEERR_KEY, GLUETUN_KEY. With --emit-key prints HOMARR_API_KEY=<key> (caller
stores it in Vault); nothing else secret is printed.

Homarr v2 keeps everything in its database, so this drives its own onboarding API: claim the wizard, create the admin,
sign in, then onboard.completeSetup creates the board, integrations (live widgets) and apps in one go. Apps created
from integrations link to the internal service URL, so their href is then pointed at https://<app>.lab.internal via
the REST API (status pings keep using the internal URL). Not idempotent past onboarding: on an existing install it only
re-applies the app links.
"""
import http.client, json, os, socket, ssl, sys, urllib.parse

HOST = 'homarr.lab.internal'
GATEWAY_IP = '192.168.3.201'   # LAN gateway; resolve here in case UniFi DNS isn't set up for homarr yet
SVC = 'media.svc.cluster.local'
_getaddrinfo = socket.getaddrinfo
socket.getaddrinfo = lambda host, *a, **k: _getaddrinfo(GATEWAY_IP if host == HOST else host, *a, **k)
CTX = ssl.create_default_context(cafile=os.environ['CA_FILE'])
cookies = {}

def log(*a): print(*a, file=sys.stderr, flush=True)

def request(method, path, body=None, form=None, headers=None):
    conn = http.client.HTTPSConnection(HOST, context=CTX, timeout=120)
    h = {'Origin': f'https://{HOST}', 'Sec-Fetch-Site': 'same-origin', **(headers or {})}
    if cookies:
        h['Cookie'] = '; '.join(f'{k}={v}' for k, v in cookies.items())
    data = None
    if body is not None:
        data = json.dumps(body); h['Content-Type'] = 'application/json'
    elif form is not None:
        data = urllib.parse.urlencode(form); h['Content-Type'] = 'application/x-www-form-urlencoded'
    conn.request(method, path, body=data, headers=h)
    r = conn.getresponse(); raw = r.read()
    for k, v in r.getheaders():
        if k.lower() == 'set-cookie':
            name, _, rest = v.partition('=')
            cookies[name] = rest.split(';', 1)[0]
    try:
        return r.status, (json.loads(raw) if raw else None)
    except ValueError:
        return r.status, raw.decode(errors='replace')[:300]

def trpc(proc, data=None, query=False):
    if query:
        q = urllib.parse.quote(json.dumps({'json': data}))
        st, r = request('GET', f'/api/trpc/{proc}?input={q}')
    else:
        st, r = request('POST', f'/api/trpc/{proc}', body={'json': data})
    if st != 200:
        msg = r.get('error', {}).get('json', {}).get('message') if isinstance(r, dict) else r
        raise SystemExit(f'{proc} failed: HTTP {st}: {msg}')
    return r['result']['data']['json'] if isinstance(r, dict) and 'result' in r else r

E = os.environ
INTEGRATIONS = [  # (sourceId, kind, name, internal url, secrets, public href)
    ('jellyfin', 'jellyfin', 'Jellyfin', f'http://jellyfin.{SVC}:8096', {'apiKey': E['JELLYFIN_KEY']}, 'https://jellyfin.lab.internal'),
    ('seerr', 'seerr', 'Seerr', f'http://seerr.{SVC}:5055', {'apiKey': E['SEERR_KEY']}, 'https://seerr.lab.internal'),
    ('sonarr', 'sonarr', 'Sonarr', f'http://sonarr.{SVC}:8989', {'apiKey': E['SONARR_KEY']}, 'https://sonarr.lab.internal'),
    ('radarr', 'radarr', 'Radarr', f'http://radarr.{SVC}:7878', {'apiKey': E['RADARR_KEY']}, 'https://radarr.lab.internal'),
    ('bazarr', 'bazarr', 'Bazarr', f'http://bazarr.{SVC}:6767', {'apiKey': E['BAZARR_KEY']}, 'https://bazarr.lab.internal'),
    ('prowlarr', 'prowlarr', 'Prowlarr', f'http://prowlarr.{SVC}:9696', {'apiKey': E['PROWLARR_KEY']}, 'https://prowlarr.lab.internal'),
    # qBittorrent trusts the pod network (no real login); Homarr still wants a credential pair
    ('qbittorrent', 'qBittorrent', 'qBittorrent', f'http://qbittorrent.{SVC}:8080', {'username': 'admin', 'password': 'unused'}, 'https://torrent.lab.internal'),
    ('gluetun', 'gluetun', 'VPN (Gluetun)', f'http://qbittorrent.{SVC}:8000', {'apiKey': E['GLUETUN_KEY']}, 'https://torrent.lab.internal'),
]
APPS = [  # plain link tiles (no Homarr integration)
    ('argocd', 'Argo CD', 'https://argocd.lab.internal', 'http://argocd-server.argocd.svc.cluster.local/healthz', 'GitOps'),
    ('grafana', 'Grafana', 'https://grafana.lab.internal/d/dagalab-media-pipeline', 'http://kube-prometheus-stack-grafana.monitoring.svc.cluster.local/api/health', 'Media pipeline dashboard'),
    ('longhorn', 'Longhorn', 'https://longhorn.lab.internal', 'http://longhorn-frontend.longhorn-system.svc.cluster.local', 'Block storage'),
    ('vault', 'Vault', 'https://vault.lab.internal', 'http://vault-active.vault.svc.cluster.local:8200/v1/sys/health', 'Secrets'),
    ('homepage', 'Homepage', 'https://homepage.lab.internal', 'http://homepage.homepage.svc.cluster.local:3000/api/healthcheck', 'The other dashboard'),
]
WIDGETS = ['mediaServer', 'mediaRequests-requestList', 'mediaRequests-requestStats', 'calendar', 'downloads', 'vpn',
           'indexerManager', 'bazarr', 'mediaMissing', 'clock', 'weather']

step = trpc('onboard.currentStep', None, query=True)
log(f'onboarding step: {step.get("current") if isinstance(step, dict) else step}')
current = step.get('current') if isinstance(step, dict) else step

if current != 'finish' and current is not None:
    st, r = request('POST', '/api/onboarding/claim')
    if st not in (200, 201):
        raise SystemExit(f'claim failed: HTTP {st} {r}')
    log('onboarding claimed')
    if current == 'start':
        trpc('onboard.nextStep'); current = 'user'
    if current == 'user':
        trpc('user.initUser', {'username': E['HOMARR_USER'], 'password': E['HOMARR_PASSWORD'],
                               'confirmPassword': E['HOMARR_PASSWORD'], 'email': ''})
        log(f'admin user {E["HOMARR_USER"]} created'); current = 'setup'

# sign in (Auth.js credentials provider)
st, csrf = request('GET', '/api/auth/csrf')
st, r = request('POST', '/api/auth/callback/credentials',
                form={'csrfToken': csrf['csrfToken'], 'name': E['HOMARR_USER'], 'password': E['HOMARR_PASSWORD'],
                      'callbackUrl': f'https://{HOST}/'})
st, session = request('GET', '/api/auth/session')
if not (isinstance(session, dict) and session.get('user')):
    raise SystemExit(f'sign-in failed (HTTP {st})')
log(f'signed in as {session["user"].get("name")}')

if current == 'setup':
    trpc('onboard.completeSetup', {
        'server': {'defaultLocale': 'en', 'defaultColorScheme': 'dark', 'analyticsEnabled': False},
        'board': {'name': 'dagalab', 'primaryColor': '#4c6ef5', 'secondaryColor': '#7950f2', 'itemRadius': 'lg',
                  'layoutPreset': 'balanced', 'leftSidebar': False, 'rightSidebar': False},
        'integrations': [{'sourceId': sid, 'kind': kind, 'name': name, 'url': url, 'pingUrl': url,
                          'secrets': [{'kind': k, 'value': v} for k, v in sec.items()]}
                         for sid, kind, name, url, sec, _ in INTEGRATIONS],
        'apps': [{'sourceId': sid, 'name': name, 'href': href, 'pingUrl': ping, 'iconUrl': None, 'description': desc}
                 for sid, name, href, ping, desc in APPS],
        'selectedWidgetKinds': WIDGETS,
    })
    log(f'setup complete: board dagalab, {len(INTEGRATIONS)} integrations, {len(APPS)} link apps, {len(WIDGETS)} widget kinds')

# REST API key (for the app links below and later scripting)
key = trpc('apiKeys.create', {}) if '--emit-key' in sys.argv else None
api_key = (key.get('apiKey') or key.get('key')) if isinstance(key, dict) else key
H = {'ApiKey': api_key} if api_key else {}
st, apps = request('GET', '/api/apps', headers=H)
if st != 200:
    raise SystemExit(f'listing apps failed: HTTP {st} {apps}')
public = {name: href for _, _, name, _, _, href in INTEGRATIONS}
fixed = 0
for app in apps:
    want = public.get(app['name'])
    if want and app.get('href') != want:
        body = {k: app.get(k) for k in ('name', 'description', 'iconUrl', 'pingUrl')}
        body['href'] = want
        st, r = request('PATCH', f'/api/apps/{app["id"]}', body=body, headers=H)
        if st not in (200, 204):
            log(f'app {app["name"]}: link update FAILED {st} {r}')
        else:
            fixed += 1
log(f'app links -> lab.internal: {fixed} updated, {len(apps)} apps total')
if api_key:
    print(f'HOMARR_API_KEY={api_key}')
