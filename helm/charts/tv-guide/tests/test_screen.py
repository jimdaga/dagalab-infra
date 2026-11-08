import importlib.util, pathlib, sys, unittest
from datetime import datetime, timedelta, timezone

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'files'))


def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE.parent / 'files' / f'{name}.py')
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


ls, sc = load('listings'), load('screen')
EST = timezone(timedelta(hours=-5))
PROGS = ls.parse_xmltv((HERE / 'xmltv.xml').read_text())
NOW = datetime(2026, 11, 8, 15, 40, tzinfo=EST)


def events(ass, name):
    """Dialogue lines whose Name field is `name` -> their Text field."""
    out = []
    for line in ass.splitlines():
        if line.startswith('Dialogue:'):
            f = line[len('Dialogue:'):].split(',', 9)
            if f[4] == name:
                out.append(f[9])
    return out


def build(start=NOW, seconds=60, error=None, progs=PROGS):
    return sc.build_ass(ls.rows(progs, start), ls.promos(progs, start), ls.window(start), start, seconds, error)


class ScreenTest(unittest.TestCase):
    def test_header_and_size(self):
        ass = build()
        for s in ('PlayResX: 1920', 'PlayResY: 1080', 'Barlow Condensed'):
            self.assertIn(s, ass)

    def test_clock_one_event_per_second(self):
        clock = events(build(start=datetime(2026, 11, 8, 15, 44, 0, tzinfo=EST)), 'clock')
        self.assertEqual(len(clock), 60)
        self.assertTrue(clock[0].endswith('3:44 PM'), clock[0])

    def test_promo_text_and_rotation(self):
        # 4 promos at 15:40 (25 NOW/NEXT, 90 NOW/NEXT); pick a start whose 10-s slot index % 4 == 2 -> 90 NOW
        start = NOW
        while int(start.timestamp()) // 10 % 4 != 2:
            start += timedelta(seconds=10)
        heads = [t for t in events(build(start=start), 'promo') if 'ON ' in t]
        self.assertEqual(len(heads), 6)
        self.assertIn('NOW ON 90S REWIND', heads[0])
        self.assertIn('COMING UP ON 90S REWIND', heads[1])
        self.assertIn('NOW ON CHRISTMAS', heads[2])

    def test_escape_special_and_long(self):
        self.assertEqual(sc.escape('A {b} \\c', 80), 'A (b) \\c')
        self.assertNotIn('\\N', sc.escape('x\\Ny', 80))          # would be a line break in libass
        self.assertIn('\\', sc.escape('x\\Ny', 80))
        long = sc.escape('x' * 100, 80)
        self.assertTrue(long.endswith('…'))
        self.assertLessEqual(len(long), 80)

    def test_title_with_braces_renders_literally(self):
        weird = [p if p.title != 'Elf' else ls.Programme(p.channel, p.channel_name, 'Elf {\\b1}', p.detail, p.start,
                                                          p.stop, p.episode, p.year, p.poster_url) for p in PROGS]
        ass = build(progs=weird)
        self.assertIn('Elf (\\b1)', ass)

    def test_scroll_continuous_across_items(self):
        for n in (1, 12):
            a = datetime(2026, 11, 8, 15, 40, 7, tzinfo=EST)
            self.assertEqual(sc.grid_positions(n, a, 60), sc.grid_positions(n, a + timedelta(seconds=60), 0))

    def test_grid_covers_visible_area(self):
        for n in (1, 2, 12):
            ys = sorted(y for _, y in sc.grid_positions(n, NOW, 0))
            self.assertLessEqual(ys[0], sc.GRID_TOP)
            self.assertGreaterEqual(ys[-1] + sc.ROW_H, 1080)

    def test_error_text(self):
        ass = build(error='LISTINGS UNAVAILABLE', progs=[])
        self.assertIn('LISTINGS UNAVAILABLE', ass)
        self.assertEqual(events(ass, 'row'), [])
        self.assertEqual(events(ass, 'promo'), [])
        self.assertEqual(len(events(ass, 'clock')), 60)


if __name__ == '__main__':
    unittest.main()
