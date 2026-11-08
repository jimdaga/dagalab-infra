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
        assets, stills, argv = rd.plan(NOW, None, Down(), '/c', 60)
        self.assertIn('LISTINGS UNAVAILABLE', assets['strip.ass'])
        self.assertEqual(sorted(assets), ['frame.ass', 'overlay.ass', 'strip.ass'])
        self.assertEqual([c[-1] for c in stills], ['/c/frame.png', '/c/strip.png'])
        self.assertEqual(argv.count('color=c=black:s=1280x540:r=30'), 6)
        self.assertEqual(argv[argv.index('-t', argv.index('-filter_complex')) + 1], '60')

    def test_plan_bad_xml(self):
        assets, _, _ = rd.plan(NOW, '<tv><programme', Down(), '/c', 60)
        self.assertIn('LISTINGS UNAVAILABLE', assets['strip.ass'])

    def test_plan_with_listings_library_down(self):
        assets, _, argv = rd.plan(NOW, (HERE / 'xmltv.xml').read_text(), Down(), '/c', 60)
        self.assertIn('NOW ON', assets['overlay.ass'])
        self.assertNotIn('LISTINGS UNAVAILABLE', assets['strip.ass'])
        self.assertEqual(argv.count('color=c=black:s=1280x540:r=30'), 6)   # library errors -> blank promos


if __name__ == '__main__':
    unittest.main()
