import importlib.util, pathlib, sys, unittest
from datetime import datetime, timedelta, timezone

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'files'))
spec = importlib.util.spec_from_file_location('render', HERE.parent / 'files' / 'render.py')
rd = importlib.util.module_from_spec(spec); spec.loader.exec_module(rd)

EST = timezone(timedelta(hours=-5))
NOW = datetime(2026, 11, 8, 15, 40, tzinfo=EST)


class Down:
    def path_for(self, p):
        raise OSError('jellyfin down')

    def poster_for(self, p):
        raise OSError('down')


class RenderTest(unittest.TestCase):
    def test_plan_without_listings(self):
        assets, stills, build = rd.plan(NOW, None, Down(), '/c', 60)
        argv = build()
        self.assertIn('LISTINGS UNAVAILABLE', assets['strip.ass'])
        self.assertEqual(sorted(assets), ['frame.ass', 'overlay.ass', 'strip.ass'])
        self.assertEqual([c[-1] for c in stills], ['/c/frame.png', '/c/strip.png'])
        self.assertEqual(argv.count('color=c=black:s=1280x540:r=30'), 6)
        self.assertEqual(argv[argv.index('-t', argv.index('-filter_complex')) + 1], '60')

    def test_plan_bad_xml(self):
        assets, _, _ = rd.plan(NOW, '<tv><programme', Down(), '/c', 60)
        self.assertIn('LISTINGS UNAVAILABLE', assets['strip.ass'])

    def test_plan_with_listings_library_down(self):
        assets, _, build = rd.plan(NOW, (HERE / 'xmltv.xml').read_text(), Down(), '/c', 60)
        argv = build()
        self.assertIn('NOW ON', assets['overlay.ass'])
        self.assertNotIn('LISTINGS UNAVAILABLE', assets['strip.ass'])
        self.assertEqual(argv.count('color=c=black:s=1280x540:r=30'), 6)   # library errors -> blank promos


if __name__ == '__main__':
    unittest.main()


class AnchorTest(unittest.TestCase):
    """Review Important 1: items are 60 s on minute boundaries; time them from the boundary, not the request."""
    def test_late_and_early_requests_snap_to_the_boundary(self):
        b = datetime(2026, 11, 8, 16, 12, 0, tzinfo=EST)
        self.assertEqual(rd.anchor(b + timedelta(seconds=2.9)), b)
        self.assertEqual(rd.anchor(b - timedelta(seconds=3)), b)

    def test_mid_item_join_keeps_real_time(self):
        t = datetime(2026, 11, 8, 16, 11, 37, tzinfo=EST)
        self.assertEqual(rd.anchor(t), t)


class BudgetTest(unittest.TestCase):
    """Review Important 2: a hanging Jellyfin must not hold up the stream."""
    def test_first_failure_stops_further_lookups(self):
        calls = []
        class Fails:
            def path_for(self, p): calls.append('path'); raise OSError('timeout')
            def poster_for(self, p): calls.append('poster'); return '/c/p.jpg'
        g = rd.Guarded(Fails(), budget_s=3)
        self.assertIsNone(g.path_for(None))
        self.assertIsNone(g.path_for(None))
        self.assertEqual(g.poster_for(None), '/c/p.jpg')     # posters come from ErsatzTV, not Jellyfin
        self.assertEqual(calls, ['path', 'poster'])

    def test_budget_spent_skips_lookups(self):
        clock = [0.0]
        class Slow:
            def path_for(self, p): clock[0] += 4; return ('/x.mkv', 10.0)
            def poster_for(self, p): clock[0] += 4; return '/p.jpg'
        g = rd.Guarded(Slow(), budget_s=3, clock=lambda: clock[0])
        self.assertEqual(g.path_for(None), ('/x.mkv', 10.0))
        self.assertIsNone(g.path_for(None))
        self.assertIsNone(g.poster_for(None))
