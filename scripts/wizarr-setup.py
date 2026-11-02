#!/usr/bin/env python3
"""Idempotent Wizarr setup through its admin forms (no JSON API for this; port-forward on 127.0.0.1:15690).
Called by wizarr-setup.sh. Env: WIZARR_PASSWORD, JELLYFIN_KEY, ABS_KEY."""
import html, http.cookiejar, os, re, sys, urllib.error, urllib.parse, urllib.request

U = 'http://127.0.0.1:15690'
USER, PW = 'jim', os.environ['WIZARR_PASSWORD']
# in-cluster URL Wizarr talks to; external URL is what invitees are sent to after signing up
SERVERS = [
    dict(server_name='Jellyfin', server_type='jellyfin', server_url='http://jellyfin.media.svc.cluster.local:8096',
         external_url='https://popcorn.dagaflix.com', api_key=os.environ['JELLYFIN_KEY']),
    dict(server_name='Audiobookshelf', server_type='audiobookshelf', server_url='http://audiobookshelf.media.svc.cluster.local',
         external_url='https://books.dagaflix.com', api_key=os.environ['ABS_KEY']),
]
log = lambda m: print(m, file=sys.stderr)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), NoRedirect)


def call(method, path, form=None):
    """Returns (status, body, Location header). Redirects are not followed."""
    data = urllib.parse.urlencode(form, doseq=True).encode() if form is not None else None
    req = urllib.request.Request(U + path, method=method, data=data, headers={'HX-Request': 'true'} if method != 'GET' else {})
    try:
        with opener.open(req, timeout=60) as r:
            return r.status, r.read().decode(), r.headers.get('Location', '')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), e.headers.get('Location', '')


def csrf(page):
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page) or re.search(r'value="([^"]+)"[^>]*name="csrf_token"', page)
    return m.group(1) if m else ''


# 1) admin account (only possible while there is none: the form shows up at /setup/)
st, page, loc = call('GET', '/setup/')
if st == 200 and 'name="confirm"' in page:
    st, page, loc = call('POST', '/setup/', {'csrf_token': csrf(page), 'username': USER, 'password': PW, 'confirm': PW})
    if st != 302:
        sys.exit(f'creating admin failed: {st} {page[:400]}')
    log(f'admin {USER}: created')
else:
    log('admin: already set up')

# 2) log in
st, page, _ = call('GET', '/login')
st, page, loc = call('POST', '/login', {'csrf_token': csrf(page), 'username': USER, 'password': PW, 'auth_method': 'local'})
st, page, loc = call('GET', '/settings/servers')
if st != 200:
    sys.exit(f'login as {USER} failed (GET /settings/servers: {st} -> {loc})')

# 3) media servers, matched by in-cluster URL (on each server's edit form)
have = {}
for sid in sorted(set(re.findall(r'/settings/servers/(\d+)/edit', page))):
    _, form, _ = call('GET', f'/settings/servers/{sid}/edit')
    for s in SERVERS:
        if f'value="{s["server_url"]}"' in html.unescape(form):
            have[s['server_type']] = sid
for s in SERVERS:
    if s['server_type'] in have:
        log(f'server {s["server_name"]}: present')
        continue
    _, form, _ = call('GET', '/settings/servers/create')
    st, body, loc = call('POST', '/settings/servers/create', {'csrf_token': csrf(form), **s})
    if st != 302:   # a failed connection test re-renders the modal (200) with the error
        err = re.search(r'(?s)class="[^"]*text-red[^"]*"[^>]*>(.*?)<', body)
        sys.exit(f'adding {s["server_name"]} failed: {st} {err.group(1).strip() if err else body[:400]}')
    log(f'server {s["server_name"]}: added')

# 4) library list: new libraries are inserted enabled; existing rows keep the admin's choice
st, page, _ = call('GET', '/settings/servers')
for sid in sorted(set(re.findall(r'/settings/servers/(\d+)/edit', page))):
    st, body, _ = call('POST', f'/settings/servers/{sid}/scan-libraries')
    log(f'server {sid}: library scan ' + ('ok' if st == 200 else f'FAILED {st} {body[:200]}'))
