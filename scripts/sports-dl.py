#!/usr/bin/env python3
"""Download a full sports game from YouTube into the NAS sports library (TV-show layout, for ErsatzTV / Jellyfin).

  sports-dl.py URL --season 2016 --title "Super Bowl LI: Patriots vs. Falcons" --aired 2017-02-05 [--league NFL]
               [--episode N] [--plot TEXT] [--show NAME] [--root DIR] [--dry-run]

Layout (root = video share `sports/`, mounted over SMB on the Mac at /Volumes/video):
  <root>/<League> Classic Games/tvshow.nfo
  <root>/<League> Classic Games/Season <season>/<show> - S<season>E<nn> - <title>.{mp4,nfo,-thumb.jpg}

Season = the year the season started (NFL games in Jan/Feb belong to the previous year; NBA/NHL 2015-16 = 2015).
Episode defaults to the next free number in that season folder; <aired> carries the real game date for ordering.
The YouTube id goes into the episode .nfo, so a video already in the library is skipped.
Downloads go to a local staging dir first (merging over SMB is slow), then copy across under a temp name and rename.
"""
import argparse, glob, os, re, shutil, subprocess, sys, tempfile
from xml.sax.saxutils import escape

FORMAT = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'   # H.264/AAC MP4: ErsatzTV can stream it without a transcode

def safe(name):
    return re.sub(r'\s+', ' ', re.sub(r'[\\/:*?"<>|]', ' -', name)).strip(' .')

def ytdlp(*args):
    return subprocess.run(['yt-dlp', *args], check=True, text=True, capture_output=True).stdout.strip()

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('url')
    p.add_argument('--season', type=int, required=True)
    p.add_argument('--title', required=True, help='episode title, e.g. "Super Bowl LI: Patriots vs. Falcons"')
    p.add_argument('--aired', required=True, help='game date, YYYY-MM-DD')
    p.add_argument('--league', default='NFL')
    p.add_argument('--show', help='show folder name (default "<league> Classic Games")')
    p.add_argument('--episode', type=int)
    p.add_argument('--plot', default='')
    p.add_argument('--root', default='/Volumes/video/sports')
    p.add_argument('--dry-run', action='store_true')
    a = p.parse_args()
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', a.aired):
        sys.exit('--aired must be YYYY-MM-DD')
    if not os.path.isdir(os.path.dirname(a.root.rstrip('/'))):
        sys.exit(f'{os.path.dirname(a.root)} not found: mount the share first (open smb://192.168.3.20/video)')
    for tool in ('yt-dlp', 'ffmpeg'):
        if not shutil.which(tool):
            sys.exit(f'{tool} not installed (brew install yt-dlp ffmpeg)')

    vid = ytdlp('--print', 'id', '--skip-download', a.url)
    show = a.show or f'{a.league} Classic Games'
    show_dir = os.path.join(a.root, show)
    season_dir = os.path.join(show_dir, f'Season {a.season}')

    for nfo in glob.glob(os.path.join(show_dir, '*', '*.nfo')):
        with open(nfo, encoding='utf-8') as f:
            if f'<uniqueid type="youtube">{vid}</uniqueid>' in f.read():
                print(f'already in library: {nfo}'); return

    ep = a.episode
    if ep is None:
        nums = [int(m.group(1)) for f in glob.glob(os.path.join(season_dir, '*.nfo'))
                if (m := re.search(rf'S{a.season}E(\d+)', os.path.basename(f)))]
        ep = max(nums, default=0) + 1
    base = f'{show} - S{a.season}E{ep:02d} - {safe(a.title)}'
    if glob.glob(os.path.join(season_dir, f'{show} - S{a.season}E{ep:02d} - *')):
        sys.exit(f'episode {ep} already exists in {season_dir}; pick another --episode')

    print(f'video   {vid}\ntarget  {season_dir}/{base}.mp4\naired   {a.aired}')
    if a.dry_run:
        return

    stage = tempfile.mkdtemp(prefix='sports-dl-')
    try:
        subprocess.run(['yt-dlp', '-P', stage, '-f', FORMAT, '--merge-output-format', 'mp4',
                        '--write-thumbnail', '--convert-thumbnails', 'jpg', '--embed-thumbnail', '--embed-metadata',
                        '-o', f'{base}.%(ext)s', a.url], check=True)
        thumb = os.path.join(stage, f'{base}.jpg')
        if os.path.exists(thumb):
            os.rename(thumb, os.path.join(stage, f'{base}-thumb.jpg'))
        with open(os.path.join(stage, f'{base}.nfo'), 'w', encoding='utf-8') as f:
            f.write(f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<episodedetails>\n'
                    f'  <title>{escape(a.title)}</title>\n  <showtitle>{escape(show)}</showtitle>\n'
                    f'  <season>{a.season}</season>\n  <episode>{ep}</episode>\n  <aired>{a.aired}</aired>\n'
                    f'  <plot>{escape(a.plot)}</plot>\n  <uniqueid type="youtube">{vid}</uniqueid>\n</episodedetails>\n')

        os.makedirs(season_dir, exist_ok=True)
        tv_nfo = os.path.join(show_dir, 'tvshow.nfo')
        if not os.path.exists(tv_nfo):
            with open(tv_nfo, 'w', encoding='utf-8') as f:
                f.write(f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<tvshow>\n'
                        f'  <title>{escape(show)}</title>\n  <plot>Full {escape(a.league)} game broadcasts.</plot>\n'
                        f'  <genre>Sports</genre>\n</tvshow>\n')
        # Video last, so a half-copied file never sits under its final name.
        files = sorted(os.listdir(stage), key=lambda n: n.endswith('.mp4'))
        for name in files:
            tmp = os.path.join(season_dir, f'.{name}.part')
            shutil.copyfile(os.path.join(stage, name), tmp)
            os.rename(tmp, os.path.join(season_dir, name))
            print(f'copied  {name}')
    finally:
        shutil.rmtree(stage, ignore_errors=True)

if __name__ == '__main__':
    main()
