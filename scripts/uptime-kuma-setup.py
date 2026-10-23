#!/usr/bin/env python3
"""Desired Uptime Kuma state as JSON on stdout (run by scripts/uptime-kuma-setup.sh, applied in the pod by uptime-kuma-apply.js).

Env: UK_USER, UK_PASSWORD (Vault secret/uptimekuma), DISCORD_ALERTS (webhook, Vault secret/discord).

Monitors come from the repo, so a new route is monitored on the next run:
  LAN apps       every route in helm/charts/lan-gateway/values.yaml   -> https://<host>.lab.internal<monitorPath or />
  Public         every route in helm/charts/shared-gateway/values.yaml -> https://<host>.dagaflix.com/ (+ cert expiry),
                 plus PUBLIC_EXTRA; all through the vpn-proxy (own PIA tunnel), so they test the way in from the internet
  Infrastructure EXTRA below: anything not behind a gateway (hosts, LoadBalancer services, ports)
A route takes `monitor: false` to skip it. Every monitor alerts Discord #alerts; groups don't (they'd repeat the child).
Status pages (one per Homepage tile): lab = everything, public, infra.
"""
import json, os, pathlib, sys, yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
TAG = 'gitops'   # marks what this script owns: untagged monitors (made in the UI) are never touched

def routes(chart):
    v = yaml.safe_load((ROOT / 'helm/charts' / chart / 'values.yaml').read_text())
    return v['domain'], [r for r in v['routes'] if r.get('monitor', True)]

# 2 retries a minute apart: a monitor alerts after ~3 minutes down, not on one slow answer
BASE = dict(interval=60, retryInterval=60, maxretries=2, timeout=48, resendInterval=0)
http = lambda url, **kw: dict(BASE, type='http', url=url, method='GET', maxredirects=10,
                              accepted_statuscodes=['200-399'], **{'ignoreTls': False, 'expiryNotification': False, **kw})
ping = lambda host: dict(BASE, type='ping', hostname=host)
port = lambda host, p: dict(BASE, type='port', hostname=host, port=p)

monitors = {}
domain, lan = routes('lan-gateway')
for r in lan:   # lab CA: devices trust it, Node doesn't; cert-manager renews the wildcard cert
    monitors[f'{r["host"]}.{domain}'] = dict(http(f'https://{r["host"]}.{domain}{r.get("monitorPath", "/")}', ignoreTls=True), group='LAN apps')
# Public: out through PIA and back in over the WAN (from the LAN they'd hairpin at the UCG and never leave the house).
# The proxy resolves names itself, inside the tunnel: public DNS, not the UCG's split answers.
domain, public = routes('shared-gateway')
for r in public:
    monitors[f'{r["host"]}.{domain}'] = dict(http(f'https://{r["host"]}.{domain}{r.get("monitorPath", "/")}', expiryNotification=True),
                                             group='Public', viaProxy=True)
PUBLIC_EXTRA = {
    'Plex (WAN :32400)': http('http://dagaflix.com:32400/identity'),   # UCG forwards WAN 32400 to 192.168.3.202
}
for name, m in PUBLIC_EXTRA.items():
    monitors[name] = dict(m, group='Public', viaProxy=True)

EXTRA = {
    'dagakube01': ping('192.168.3.11'),
    'dagakube02': ping('192.168.3.12'),
    'dagakube03': ping('192.168.3.13'),
    'Kubernetes API': port('k8s.lab.internal', 6443),   # anonymous /readyz is refused, so a TCP check
    'bastion': ping('192.168.3.100'),
    'daga-nas DSM': http('https://192.168.3.20:5001/', ignoreTls=True),
    'daga-nas NFS': port('192.168.3.21', 2049),          # port 2: every media/audio PV mounts through it
    'UniFi gateway': http('https://192.168.3.1/', ignoreTls=True),
    'Plex (LoadBalancer)': http('http://192.168.3.202:32400/identity'),   # direct, not via Envoy; WAN side under Public
    'Internet': ping('1.1.1.1'),
    # the tunnel itself: when this is down, the Public monitors are failing because of the VPN, not the services
    'VPN proxy (PIA)': dict(http('https://www.cloudflare.com/cdn-cgi/trace'), viaProxy=True),
}
for name, m in EXTRA.items():
    monitors[name] = dict(m, group='Infrastructure')

json.dump(dict(
    user=os.environ['UK_USER'], password=os.environ['UK_PASSWORD'], tag=TAG,
    groups=['LAN apps', 'Public', 'Infrastructure'],
    monitors=monitors,
    # default notification: monitors added by hand in the UI get it too
    notification=dict(name='Discord #alerts', type='discord', discordWebhookUrl=os.environ['DISCORD_ALERTS'],
                      discordUsername='Uptime Kuma', isDefault=True, active=True),
    proxy=dict(protocol='http', host='vpn-proxy.uptime-kuma.svc.cluster.local', port=8888, auth=False, active=True, default=False),
    # one per Homepage uptimekuma tile (a widget shows one page's totals); LAN only, like the rest of the UI
    statusPages=[
        dict(slug='lab', title='dagalab', groups=['LAN apps', 'Public', 'Infrastructure']),
        dict(slug='public', title='dagaflix.com from the internet', groups=['Public']),
        dict(slug='infra', title='Lab infrastructure', groups=['Infrastructure']),
    ],
), sys.stdout)
