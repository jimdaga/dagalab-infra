import importlib.util, io, json, pathlib, sys, tempfile, unittest, urllib.error
from datetime import datetime, timedelta, timezone

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'files'))


def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE.parent / 'files' / f'{name}.py')
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


ls, md = load('listings'), load('media')
EST = timezone(timedelta(hours=-5))
T = datetime(2026, 11, 8, 15, 0, tzinfo=EST)
MOVIE = ls.Programme(25, 'Christmas', 'Home Alone 3', '1997', T, T, None, 1997, None)
EPISODE = ls.Programme(90, '90s Rewind', 'The Simpsons', 'Simpsons Roasting on an Open Fire', T, T, 'S01E01', None, None)


class FakeOpener:
    """urlopen stand-in: first route whose key is in the URL answers with its JSON."""
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, req, timeout=None):
        url = req.full_url if hasattr(req, 'full_url') else req
        self.calls.append(url)
        for key, body in self.routes.items():
            if key in url:
                if isinstance(body, Exception):
                    raise body
                return io.BytesIO(body if isinstance(body, bytes) else json.dumps(body).encode())
        raise urllib.error.HTTPError(url, 404, 'nf', {}, None)


ROUTES = {
    'IncludeItemTypes=Movie': {'Items': [
        {'Name': 'Home Alone 3', 'ProductionYear': 1997, 'Path': '/data/movies/HA3.mkv', 'RunTimeTicks': 61531890000},
        {'Name': 'Home Alone 3', 'ProductionYear': 2097, 'Path': '/data/movies/wrong.mkv', 'RunTimeTicks': 1}]},
    'IncludeItemTypes=Series': {'Items': [{'Name': 'The Simpsons', 'Id': 'abc'}]},
    '/Shows/abc/Episodes': {'Items': [
        {'ParentIndexNumber': 1, 'IndexNumber': 2, 'Path': '/data/tv/s01e02.mkv', 'RunTimeTicks': 1},
        {'ParentIndexNumber': 1, 'IndexNumber': 1, 'Path': '/data/tv/s01e01.mkv', 'RunTimeTicks': 13364910000}]},
}


class MediaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())

    def lib(self, opener):
        return md.Library('http://jf', 'KEY', self.tmp, opener=opener)

    def test_movie_and_episode_lookup(self):
        op = FakeOpener(ROUTES)
        lib = self.lib(op)
        self.assertEqual(lib.path_for(MOVIE), ('/data/movies/HA3.mkv', 6153.189))
        self.assertEqual(lib.path_for(EPISODE), ('/data/tv/s01e01.mkv', 1336.491))
        n = len(op.calls)
        # cached on disk: a fresh Library (next 60-s item = new process) makes no calls
        lib2 = self.lib(op)
        self.assertEqual(lib2.path_for(MOVIE), ('/data/movies/HA3.mkv', 6153.189))
        self.assertEqual(lib2.path_for(EPISODE), ('/data/tv/s01e01.mkv', 1336.491))
        self.assertEqual(len(op.calls), n)

    def test_auth_header(self):
        seen = []
        def opener(req, timeout=None):
            seen.append(req.get_header('Authorization'))
            return io.BytesIO(json.dumps({'Items': []}).encode())
        self.lib(opener).path_for(MOVIE)
        self.assertEqual(seen, ['MediaBrowser Token="KEY"'])

    def test_lookup_miss_and_error(self):
        self.assertIsNone(self.lib(FakeOpener({'IncludeItemTypes=Movie': {'Items': []}})).path_for(MOVIE))
        self.assertIsNone(self.lib(FakeOpener({'Items': urllib.error.URLError('down')})).path_for(EPISODE))

    def test_clip_offset_range(self):
        for dur in (1320.0, 6000.0):
            for seed in range(51):
                off = md.clip_offset(dur, seed)
                self.assertGreaterEqual(off, 0.2 * dur)
                self.assertLessEqual(off, 0.6 * dur)
        self.assertEqual(md.clip_offset(1320.0, 7), md.clip_offset(1320.0, 7))

    def test_poster_download_and_reuse(self):
        p = ls.Programme(25, 'Christmas', 'Elf', '2003', T, T, None, 2003, 'http://etv/iptv/artwork/posters/jellyfin/1')
        op = FakeOpener({'artwork/posters': b'\x89PNGfake'})
        f = self.lib(op).poster_for(p)
        self.assertTrue(pathlib.Path(f).read_bytes().startswith(b'\x89PNG'))
        self.assertEqual(self.lib(op).poster_for(p), f)
        self.assertEqual(len(op.calls), 1)
        self.assertIsNone(self.lib(FakeOpener({})).poster_for(
            ls.Programme(25, 'C', 'X', None, T, T, None, None, 'http://etv/missing')))
        self.assertIsNone(self.lib(op).poster_for(ls.Programme(25, 'C', 'X', None, T, T, None, None, None)))


if __name__ == '__main__':
    unittest.main()
