#!/usr/bin/env python3
"""TV Guide channel: write --seconds of the Prevue-style guide to stdout as MPEG-TS.

Started by the tv-guide sidecar (server.py) once per 60-second item while someone watches channel 1, and killed when
ErsatzTV stops reading. Never fails before ffmpeg starts: no listings, no Jellyfin, no music file or a failed still
still give a full-length stream.
"""
import argparse, json, os, shutil, subprocess, sys, time, urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import listings, media, pipeline, screen   # noqa: E402

XMLTV_URL = 'http://127.0.0.1:8409/iptv/xmltv.xml'
SECRETS = '/config/jellyfin-secrets.json'          # ErsatzTV's own Jellyfin connection (read-only)
CACHE = Path(os.environ.get('TVG_CACHE_DIR', '/transcode/tv-guide'))
FONTS = os.environ.get('TVG_FONTS_DIR', '/tv-guide/fonts')
LOOKUP_BUDGET_S = 3        # clip/poster lookups per item; after that, remaining promos are posters/blank
ITEM_S = 60                # ErsatzTV schedules the guide as 60-s items on minute boundaries
SNAP_S = 10


def anchor(now):
    """The time this render stands for. A request within SNAP_S of a minute boundary is the next 60-s item (ErsatzTV
    asks a little early or late): time it from the boundary so scroll, promos, clock and music join up seamlessly.
    Anything else is someone tuning in mid-item: real time."""
    b = datetime.fromtimestamp(round(now.timestamp() / ITEM_S) * ITEM_S, now.tzinfo)
    return b if abs((now - b).total_seconds()) <= SNAP_S else now


class Guarded:
    """Library wrapper so lookups can never hold up or kill the stream: any error means "none", the first clip-lookup
    error stops further clip lookups (Jellyfin is down or hanging), and nothing is looked up once `budget_s` is spent."""
    def __init__(self, lib, budget_s=LOOKUP_BUDGET_S, clock=time.monotonic):
        self.lib, self.clock = lib, clock
        self.deadline = clock() + budget_s
        self.jellyfin_down = False

    def _call(self, fn, p):
        if self.clock() > self.deadline:
            return None
        try:
            return fn(p)
        except Exception:
            return None

    def path_for(self, p):
        if self.jellyfin_down or self.clock() > self.deadline:
            return None
        try:
            return self.lib.path_for(p)
        except Exception:
            self.jellyfin_down = True
            return None

    def poster_for(self, p):
        return self._call(self.lib.poster_for, p)


def plan(now, xmltv, library, cache_dir, seconds=60):
    """({file name: ASS document}, [still render argv], build() -> main ffmpeg argv) for `seconds` from `now`."""
    error, progs = None, []
    try:
        progs = listings.parse_xmltv(xmltv) if xmltv else None
    except Exception:
        progs = None
    if progs is None:
        error, progs = 'LISTINGS UNAVAILABLE', []
    promos = listings.promos(progs, now)
    strip, height, cycle = screen.strip_ass(listings.rows(progs, now), error)
    assets = {'frame.ass': screen.frame_ass(listings.window(now)), 'strip.ass': strip,
              'overlay.ass': screen.overlay_ass(promos, now, seconds)}
    d = str(cache_dir).rstrip('/')
    stills = [pipeline.still_cmd(f'{d}/frame.ass', f'{d}/frame.png', screen.H, FONTS),
              pipeline.still_cmd(f'{d}/strip.ass', f'{d}/strip.png', height, FONTS)]
    segments = pipeline.segments_for(promos, Guarded(library), now, seconds)

    def build():   # after the stills ran: ffmpeg_cmd falls back for any still that didn't render
        return pipeline.ffmpeg_cmd(segments, pipeline.music_for(now), f'{d}/frame.png', f'{d}/strip.png',
                                   f'{d}/overlay.ass', FONTS, seconds, (screen.scroll_offset(cycle, now, 0), cycle))
    return assets, stills, build


def _fetch_xmltv():
    try:
        with urllib.request.urlopen(XMLTV_URL, timeout=5) as r:
            return r.read().decode()
    except Exception:
        return None


def _library():
    try:
        s = json.loads(Path(SECRETS).read_text())
        return media.Library(s['Address'], s['ApiKey'], CACHE)
    except Exception:
        return media.Library('http://127.0.0.1:1', '', CACHE)   # posters still work; clip lookups fail fast


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--seconds', type=int, default=60)
    args = p.parse_args(argv)
    # one work dir per run: two viewers (or an overlap between items) must not share files
    CACHE.mkdir(parents=True, exist_ok=True)
    for old in CACHE.glob('run-*'):
        try:
            if old.stat().st_mtime < time.time() - 600:
                shutil.rmtree(old)
        except OSError:
            pass
    work = CACHE / f'run-{os.getpid()}'
    work.mkdir(exist_ok=True)
    assets, stills, build = plan(anchor(datetime.now(timezone.utc)), _fetch_xmltv(), _library(), work, args.seconds)
    for name, text in assets.items():
        (work / name).write_text(text)
    for still in stills:
        try:
            subprocess.run(still, check=False, timeout=20, stdout=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            pass
    cmd = build()
    os.execvp(cmd[0], cmd)


if __name__ == '__main__':
    main()
