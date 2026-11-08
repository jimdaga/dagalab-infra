#!/usr/bin/env python3
"""TV Guide channel: write --seconds of the Prevue-style guide to stdout as MPEG-TS.

Run by ErsatzTV as the `script:` of a remote stream (helm/charts/tv-guide/streams/tv-guide.yml) inside its own pod,
once per 60-second item while someone watches channel 1; ErsatzTV kills it when they stop. Never fails before ffmpeg
starts: no listings or no Jellyfin still give a full-length stream.
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


class Guarded:
    """Library wrapper: any error looking up a clip or poster means "none", never a dead stream."""
    def __init__(self, lib):
        self.lib = lib

    def path_for(self, p):
        try:
            return self.lib.path_for(p)
        except Exception:
            return None

    def poster_for(self, p):
        try:
            return self.lib.poster_for(p)
        except Exception:
            return None


def plan(now, xmltv, library, cache_dir, seconds=60):
    """({file name: ASS document}, [still render argv], main ffmpeg argv) for `seconds` starting at `now`."""
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
    argv = pipeline.ffmpeg_cmd(segments, pipeline.music_for(now), f'{d}/frame.png', f'{d}/strip.png',
                               f'{d}/overlay.ass', FONTS, seconds, (screen.scroll_offset(cycle, now, 0), cycle))
    return assets, stills, argv


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
    assets, stills, cmd = plan(datetime.now(timezone.utc), _fetch_xmltv(), _library(), work, args.seconds)
    for name, text in assets.items():
        (work / name).write_text(text)
    for still in stills:
        subprocess.run(still, check=False, timeout=20, stdout=subprocess.DEVNULL)
    os.execvp(cmd[0], cmd)


if __name__ == '__main__':
    main()
