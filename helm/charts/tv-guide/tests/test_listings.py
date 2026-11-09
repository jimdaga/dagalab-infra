import importlib.util, pathlib, sys, unittest
from datetime import datetime, timedelta, timezone

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'files'))
spec = importlib.util.spec_from_file_location('listings', HERE.parent / 'files' / 'listings.py')
ls = importlib.util.module_from_spec(spec); spec.loader.exec_module(ls)

EST = timezone(timedelta(hours=-5))
XML = (HERE / 'xmltv.xml').read_text()


def at(h, m):
    return datetime(2026, 11, 8, h, m, tzinfo=EST)


class ListingsTest(unittest.TestCase):
    def setUp(self):
        self.progs = ls.parse_xmltv(XML)

    def test_parse_channels_and_fields(self):
        self.assertEqual(sorted({p.channel for p in self.progs}), [1, 25, 90])
        ha = next(p for p in self.progs if p.title == 'Home Alone 3')
        self.assertEqual((ha.channel, ha.channel_name, ha.year, ha.detail, ha.episode), (25, 'Christmas', 1997, '1997', None))
        self.assertEqual(ha.poster_url, 'http://127.0.0.1:8409/iptv/artwork/posters/jellyfin/1718')
        self.assertEqual((ha.start, ha.stop), (at(14, 0), at(15, 33)))
        simp = next(p for p in self.progs if p.title == 'The Simpsons')
        self.assertEqual((simp.channel_name, simp.detail, simp.episode, simp.year), ('90s Rewind', 'Simpsons Roasting on an Open Fire', 'S01E01', None))

    def test_window_floors_to_half_hour(self):
        self.assertEqual(ls.window(at(15, 44)), (at(15, 30), at(16, 30)))   # 60 min: two half-hour columns
        self.assertEqual(ls.window(at(16, 0)), (at(16, 0), at(17, 0)))

    def test_rows_exclude_guide_and_sort(self):
        self.assertEqual([r.channel for r in ls.rows(self.progs, at(15, 44))], [25, 90])
        self.assertEqual([r.name for r in ls.rows(self.progs, at(15, 44))], ['Christmas', '90s Rewind'])

    def test_blocks_clipped_to_window(self):
        r25, r90 = ls.rows(self.progs, at(15, 44))
        self.assertEqual([b.title for b in r25.blocks], ['Home Alone 3', 'Bad Santa 2'])   # Elf (17:10) is outside
        self.assertEqual((r25.blocks[0].x0, round(r25.blocks[0].x1, 4)), (0.0, round(3 / 60, 4)))
        self.assertEqual(r25.blocks[1].x1, 1.0)
        self.assertEqual([b.title for b in r90.blocks], ['The Simpsons', 'South Park'])
        self.assertEqual(round(r90.blocks[0].x1, 4), round(14 / 60, 4))
        self.assertEqual(round(r90.blocks[1].x0, 4), round(20 / 60, 4))          # gap 15:44-15:50 stays empty

    def test_promos_now_then_next(self):
        got = [(p.programme.channel, p.kind, p.programme.title) for p in ls.promos(self.progs, at(15, 40))]
        self.assertEqual(got, [(25, 'NOW', 'Bad Santa 2'), (25, 'NEXT', 'Elf'),
                               (90, 'NOW', 'The Simpsons'), (90, 'NEXT', 'South Park')])

    def test_promos_skip_missing(self):
        got = [(p.programme.channel, p.kind) for p in ls.promos(self.progs, at(16, 0))]
        self.assertEqual(got, [(25, 'NOW'), (25, 'NEXT'), (90, 'NOW')])         # South Park is the feed's last on 90
        got = [(p.programme.channel, p.kind) for p in ls.promos(self.progs, at(15, 46))]
        self.assertEqual(got, [(25, 'NOW'), (25, 'NEXT'), (90, 'NEXT')])        # 90 is in a gap: no NOW


if __name__ == '__main__':
    unittest.main()
