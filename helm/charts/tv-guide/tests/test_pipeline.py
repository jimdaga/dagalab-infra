import importlib.util, pathlib, sys, tempfile, unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'files'))


def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE.parent / 'files' / f'{name}.py')
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


ls, pl = load('listings'), load('pipeline')
NY = ZoneInfo('America/New_York')
EST = timezone(timedelta(hours=-5))
NOW = datetime(2026, 11, 8, 15, 40, tzinfo=EST)
PROGS = ls.parse_xmltv((HERE / 'xmltv.xml').read_text())


class StubLibrary:
    def __init__(self, path=None, poster=None):
        self.path, self.poster = path, poster

    def path_for(self, p):
        return (self.path, 1320.0) if self.path else None

    def poster_for(self, p):
        return self.poster


def args_after(cmd, flag):
    return [cmd[i + 1] for i, a in enumerate(cmd) if a == flag]


class PipelineTest(unittest.TestCase):
    def test_music_season(self):
        xmas = pl.music_for(datetime(2026, 12, 1, 20, 0, tzinfo=NY))
        self.assertIn('Christmas Loop', xmas[0])
        self.assertLess(xmas[1], 3132)
        for d in (datetime(2026, 11, 24, 12, tzinfo=NY), datetime(2026, 12, 26, 12, tzinfo=NY), datetime(2026, 7, 4, tzinfo=NY)):
            path, off = pl.music_for(d)
            self.assertIn('Complete Loop Version 1', path)
            self.assertLess(off, 1710 if '(1982)' in path else 1143)
        self.assertTrue(xmas[0].startswith('/data/theme-park-audio/Walt Disney World/EPCOT Main Entrance BGM/'))

    def test_music_continuous(self):
        a, b = pl.music_for(NOW), pl.music_for(NOW + timedelta(seconds=60))
        if a[0] == b[0]:
            self.assertAlmostEqual(b[1] - a[1], 60.0)

    def test_cmd_core(self):
        segs = [pl.Segment('clip', '/data/tv/a.mkv', 300.0), pl.Segment('image', '/c/p.jpg', 0.0), pl.Segment('blank', None, 0.0)]
        with tempfile.TemporaryDirectory() as d:
            for f in ('m.mp3', 'frame.png', 'strip.png'):
                pathlib.Path(d, f).touch()
            cmd = pl.ffmpeg_cmd(segs, (f'{d}/m.mp3', 12.5), f'{d}/frame.png', f'{d}/strip.png', '/c/overlay.ass',
                                '/tv-guide/fonts', 60, (12.0, 216))
        cmd = [a.replace(d, '/c') if a.startswith(d) else a for a in cmd]
        s = ' '.join(cmd)
        for want in ('-init_hw_device qsv=hw', '-c:v h264_qsv', '-t 60', '-f mpegts', 'pipe:1'):
            self.assertIn(want, s)
        inputs = args_after(cmd, '-i')
        self.assertEqual(inputs[:2], ['/c/frame.png', '/c/strip.png'])
        fc = cmd[cmd.index('-filter_complex') + 1]
        self.assertIn("[1:v]crop=1920:486:0:'mod(12.0+40*t,216)'", fc)        # grid scrolls by cropping the strip
        self.assertIn('overlay=0:594', fc)
        self.assertIn('scale=960:540:', fc)                        # promo box inside the 4:3 centre
        self.assertIn('[frame][promo]overlay=240:0', fc)
        self.assertIn('ass=filename=/c/overlay.ass:fontsdir=/tv-guide/fonts', fc)
        self.assertIn('concat=n=3:v=1:a=0', fc)
        self.assertIn('-ss 300.0 -t 10 -i /data/tv/a.mkv', s)
        self.assertIn('-loop 1 -framerate 30 -t 10 -i /c/p.jpg', s)
        self.assertIn('-stream_loop -1 -ss 12.5 -i /c/m.mp3', s)
        self.assertEqual(args_after(cmd, '-map'), ['[v]', f'{inputs.index("/c/m.mp3")}:a:0'])  # never the clips' audio

    def test_still_cmd(self):
        cmd = pl.still_cmd('/c/strip.ass', '/c/strip.png', 648, '/tv-guide/fonts')
        s = ' '.join(cmd)
        self.assertIn('color=c=black:s=1920x648', s)
        self.assertIn('ass=filename=/c/strip.ass:fontsdir=/tv-guide/fonts', s)
        self.assertIn('-frames:v 1', s)
        self.assertEqual(cmd[-1], '/c/strip.png')

    def test_missing_music_and_stills_fall_back(self):
        # Review Important 3: a renamed music file or a failed still must not kill the stream
        cmd = pl.ffmpeg_cmd([pl.Segment('blank', None, 0.0)], ('/nonexistent/m.mp3', 1.0), '/nonexistent/frame.png',
                            '/nonexistent/strip.png', '/c/o.ass', '/f', 60, (0.0, 108))
        s = ' '.join(cmd)
        self.assertIn('anullsrc=channel_layout=stereo:sample_rate=48000', s)
        self.assertNotIn('/nonexistent/', s)
        self.assertIn('color=c=0x06106B:s=1920x1080', s)       # frame fallback
        self.assertIn('color=c=0x000000:s=1920x594', s)         # strip fallback: crop window (486) + one cycle

    def test_segments_clip(self):
        with tempfile.NamedTemporaryFile() as f:
            segs = pl.segments_for(ls.promos(PROGS, NOW), StubLibrary(path=f.name), NOW, 60)
            self.assertEqual(len(segs), 6)
            self.assertTrue(all(s.kind == 'clip' and s.path == f.name for s in segs))
            self.assertTrue(all(0.2 * 1320 <= s.offset <= 0.6 * 1320 for s in segs))

    def test_missing_file_falls_back(self):
        promos = ls.promos(PROGS, NOW)
        segs = pl.segments_for(promos, StubLibrary(path='/nonexistent/x.mkv', poster='/tmp/p.jpg'), NOW, 60)
        self.assertEqual({s.kind for s in segs}, {'image'})
        segs = pl.segments_for(promos, StubLibrary(path='/nonexistent/x.mkv'), NOW, 60)
        self.assertEqual({s.kind for s in segs}, {'blank'})

    def test_no_promos_all_blank(self):
        segs = pl.segments_for([], StubLibrary(), NOW, 60)
        self.assertEqual([s.kind for s in segs], ['blank'] * 6)


if __name__ == '__main__':
    unittest.main()
