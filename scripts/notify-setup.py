#!/usr/bin/env python3
"""Discord notifications for the media apps (run by scripts/notify-setup.sh; idempotent).

Env: DISCORD_ALERTS / DISCORD_MEDIA / DISCORD_NOISE (webhook URLs, from Vault secret/discord via the `discord` Secret),
SONARR_KEY, RADARR_KEY, SEERR_KEY, BAZARR_KEY. Never prints a webhook URL or key.

Each event goes to exactly one channel (Alertmanager already covers *arr health, indexer failures and Unpackerr
failures in #alerts, so the apps don't repeat them):
  #alerts      Sonarr/Radarr: import needs manual interaction
  #media       Sonarr: release imported (one message per release, not per episode); Radarr: movie imported;
               Seerr: request pending/auto-approved/failed, issue created/resolved
  #media-noise Sonarr/Radarr: grabs, upgrades, deletions; Bazarr: subtitle downloaded
"""
import json, os, re, sys, urllib.error, urllib.parse, urllib.request

HOOK = {'alerts': os.environ['DISCORD_ALERTS'], 'media': os.environ['DISCORD_MEDIA'], 'noise': os.environ['DISCORD_NOISE']}
ok = True

def log(*a): print(*a, file=sys.stderr, flush=True)

def call(base, headers, method, path, body=None, form=None):
    h = dict(headers)
    data = None
    if body is not None: data = json.dumps(body).encode(); h['Content-Type'] = 'application/json'
    elif form is not None: data = urllib.parse.urlencode(form).encode(); h['Content-Type'] = 'application/x-www-form-urlencoded'
    try:
        with urllib.request.urlopen(urllib.request.Request(base + path, method=method, headers=h, data=data), timeout=60) as r:
            raw = r.read(); return r.status, (json.loads(raw) if raw and raw[:1] in b'[{' else None)
    except urllib.error.HTTPError as e:
        return e.code, re.sub(r'https://discord(app)?\.com/api/webhooks/\S+', '<webhook>', e.read().decode()[:300])

# Sonarr / Radarr: one Discord connection per channel, events toggled per connection
ARR_EVENTS = {
    'sonarr': {'media': ['onImportComplete'],
               'noise': ['onGrab', 'onUpgrade', 'onSeriesDelete', 'onEpisodeFileDelete'],
               'alerts': ['onManualInteractionRequired']},
    'radarr': {'media': ['onDownload'],
               'noise': ['onGrab', 'onUpgrade', 'onMovieDelete', 'onMovieFileDelete'],
               'alerts': ['onManualInteractionRequired']},
}
ARR = {'sonarr': ('http://127.0.0.1:18989/api/v3', os.environ['SONARR_KEY']),
       'radarr': ('http://127.0.0.1:17878/api/v3', os.environ['RADARR_KEY'])}
for app, channels in ARR_EVENTS.items():
    base, key = ARR[app]; H = {'X-Api-Key': key}
    st, schema = call(base, H, 'GET', '/notification/schema')
    tpl = next(s for s in schema if s['implementation'] == 'Discord')
    toggles = [k for k, v in tpl.items() if k.startswith('on') and isinstance(v, bool)]
    existing = {n['name']: n for n in call(base, H, 'GET', '/notification')[1]}
    for chan, events in channels.items():
        name = f'Discord #{"media-noise" if chan == "noise" else chan}'
        n = existing.get(name) or {k: v for k, v in tpl.items() if k != 'id'}
        want = {t: t in events for t in toggles}
        want['includeHealthWarnings'] = False
        fields = {f['name']: f for f in n['fields']}
        cur_url = fields['webHookUrl'].get('value')
        if name in existing and cur_url == HOOK[chan] and all(n.get(k) == v for k, v in want.items()):
            log(f'{app} {name}: already set'); continue
        n.update(want, name=name, enable=True)
        fields['webHookUrl']['value'] = HOOK[chan]
        fields['username']['value'] = app.capitalize()
        path = f'/notification/{n["id"]}' if name in existing else '/notification'
        st, r = call(base, H, 'PUT' if name in existing else 'POST', path + '?forceSave=true', n)
        log(f'{app} {name}: {", ".join(events)} -> HTTP {st}' + ('' if st < 300 else f' {r}'))
        ok &= st < 300

# Seerr: one Discord agent; notification types are a bitmask
SEERR_TYPES = {'MEDIA_PENDING': 2, 'MEDIA_FAILED': 16, 'MEDIA_AUTO_APPROVED': 128, 'ISSUE_CREATED': 256, 'ISSUE_RESOLVED': 1024}
base, H = 'http://127.0.0.1:15055/api/v1', {'X-Api-Key': os.environ['SEERR_KEY']}
st, cur = call(base, H, 'GET', '/settings/notifications/discord')
want = dict(cur, enabled=True, embedPoster=True, types=sum(SEERR_TYPES.values()),
            options=dict(cur['options'], webhookUrl=HOOK['media'], enableMentions=False))
if cur == want:
    log('seerr discord: already set')
else:
    st, r = call(base, H, 'POST', '/settings/notifications/discord', want)
    log(f'seerr discord #media: {", ".join(SEERR_TYPES)} -> HTTP {st}' + ('' if st < 300 else f' {r}'))
    ok &= st < 300

# Bazarr: Apprise "Discord" provider (discord://<id>/<token>); Bazarr notifies on subtitle downloads
base, H = 'http://127.0.0.1:16767/api', {'X-API-KEY': os.environ['BAZARR_KEY']}
m = re.match(r'https://discord(?:app)?\.com/api/webhooks/(\d+)/([\w-]+)', HOOK['noise'])
apprise = f'discord://{m.group(1)}/{m.group(2)}'
st, s = call(base, H, 'GET', '/system/settings')
cur = next(p for p in s['notifications']['providers'] if p['name'] == 'Discord')
if cur.get('enabled') and cur.get('url') == apprise:
    log('bazarr discord: already set')
else:
    st, r = call(base, H, 'POST', '/system/settings',
                 form={'notifications-providers': json.dumps({'name': 'Discord', 'enabled': True, 'url': apprise})})   # one object per field
    log(f'bazarr discord #media-noise: subtitle downloads -> HTTP {st}' + ('' if st < 300 else f' {r}'))
    ok &= st < 300

# test message to each channel
if '--test' in sys.argv:
    for chan, url in HOOK.items():
        body = {'username': 'dagalab', 'content': f'✅ Test from `scripts/notify-setup.sh`: this is **#{"media-noise" if chan == "noise" else chan}**.'}
        req = urllib.request.Request(url + '?wait=true', data=json.dumps(body).encode(), method='POST',
                                     headers={'Content-Type': 'application/json', 'User-Agent': 'dagalab-notify-setup'})
        try:
            with urllib.request.urlopen(req, timeout=30) as r: log(f'test #{chan}: HTTP {r.status}')
        except urllib.error.HTTPError as e:
            log(f'test #{chan}: FAILED HTTP {e.code}'); ok = False
sys.exit(0 if ok else 1)
