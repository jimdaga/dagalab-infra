"""Promo segments (clip / poster / blank) and the one ffmpeg command that composes the guide and encodes it."""
import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from media import clip_offset

TZ = ZoneInfo('America/New_York')
SEGMENT_S = 10
FPS = 30
MUSIC_DIR = '/data/theme-park-audio/Walt Disney World/EPCOT Main Entrance BGM/'
YEAR_ROUND = [   # (file, length in seconds), played back to back
    (MUSIC_DIR + 'Epcot - Main Entrance - Area Music (1982) - Complete Loop Version 1 - HQ (28.30).mp3', 1710),
    (MUSIC_DIR + 'Epcot - Main Entrance - Area Music (2001) - Complete Loop Version 1 (19.03).mp3', 1143),
]
CHRISTMAS = [(MUSIC_DIR + 'Epcot - Main Entrance - Area Music - Christmas Loop Version 1 (52.12).mp3', 3132)]


@dataclass
class Segment:
    kind: str            # 'clip' | 'image' | 'blank'
    path: str | None
    offset: float


def music_for(now):
    """(file, offset): position in the season's playlist from wall-clock time, so 60-s items join up seamlessly."""
    d = now.astimezone(TZ)
    playlist = CHRISTMAS if (d.month, d.day) >= (11, 25) and (d.month, d.day) <= (12, 25) else YEAR_ROUND
    pos = now.timestamp() % sum(n for _, n in playlist)
    for path, length in playlist:
        if pos < length:
            return path, round(pos, 1)
        pos -= length
    return playlist[0][0], 0.0


def segments_for(promos, library, now, seconds):
    first = int(now.timestamp()) // SEGMENT_S
    out = []
    for slot in range(-(-seconds // SEGMENT_S)):
        if not promos:
            out.append(Segment('blank', None, 0.0))
            continue
        p = promos[(first + slot) % len(promos)].programme
        hit = library.path_for(p)
        if hit and os.access(hit[0], os.R_OK):
            out.append(Segment('clip', hit[0], clip_offset(hit[1], first + slot)))
            continue
        poster = library.poster_for(p)
        out.append(Segment('image', poster, 0.0) if poster else Segment('blank', None, 0.0))
    return out


def ffmpeg_cmd(segments, music, ass_path, fonts_dir, seconds):
    cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin',
           '-init_hw_device', 'qsv=hw', '-filter_hw_device', 'hw',
           '-f', 'lavfi', '-i', f'color=c=0x06106B:s=1920x1080:r={FPS}']
    for s in segments:
        if s.kind == 'clip':
            cmd += ['-ss', f'{s.offset:.1f}', '-t', str(SEGMENT_S), '-i', s.path]
        elif s.kind == 'image':
            cmd += ['-loop', '1', '-framerate', str(FPS), '-t', str(SEGMENT_S), '-i', s.path]
        else:
            cmd += ['-f', 'lavfi', '-t', str(SEGMENT_S), '-i', f'color=c=black:s=1280x540:r={FPS}']
    music_idx = len(segments) + 1
    cmd += ['-stream_loop', '-1', '-ss', f'{music[1]:.1f}', '-i', music[0]]
    chains = [f'[{i + 1}:v:0]scale=1280:540:force_original_aspect_ratio=decrease,pad=1280:540:-1:-1:color=black,'
              f'setsar=1,fps={FPS},format=yuv420p,tpad=stop_mode=clone:stop_duration={SEGMENT_S},'
              f'trim=duration={SEGMENT_S},setpts=PTS-STARTPTS[s{i}]' for i in range(len(segments))]
    joined = ''.join(f'[s{i}]' for i in range(len(segments)))
    graph = ';'.join(chains + [
        f'{joined}concat=n={len(segments)}:v=1:a=0[promo]',
        '[0:v][promo]overlay=0:0:eof_action=pass,format=yuv420p[bg]',
        f'[bg]ass=filename={ass_path}:fontsdir={fonts_dir},format=nv12,hwupload=extra_hw_frames=64[v]'])
    cmd += ['-filter_complex', graph, '-map', '[v]', '-map', f'{music_idx}:a:0', '-t', str(seconds),
            '-c:v', 'h264_qsv', '-b:v', '8M', '-maxrate', '8M', '-bufsize', '16M', '-g', '60',
            '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2', '-f', 'mpegts', 'pipe:1']
    return cmd
