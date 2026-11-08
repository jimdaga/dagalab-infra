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


class FrameTest(unittest.TestCase):
    def test_header_and_size(self):
        ass = sc.frame_ass(ls.window(NOW))
        for s in ('PlayResX: 1920', 'PlayResY: 1080', 'Barlow Condensed', 'DAGAFLIX', 'TV GUIDE', 'SUN NOV 8',
                  '3:30 PM', '4:00 PM', '4:30 PM'):
            self.assertIn(s, ass)


class StripTest(unittest.TestCase):
    def test_strip_covers_visible_area_plus_cycle(self):
        for n in (1, 2, 12):
            rows = [ls.Row(i + 2, f'C{i}', []) for i in range(n)]
            ass, height, cycle = sc.strip_ass(rows)
            self.assertEqual(cycle, n * sc.ROW_H)
            self.assertGreaterEqual(height, sc.GRID_VISIBLE + cycle)
            self.assertEqual(height % cycle, 0)
            self.assertIn(f'PlayResY: {height}', ass)
            self.assertEqual(len([t for t in events(ass, 'row') if t.endswith(f'}}{rows[0].channel}')]), height // cycle)

    def test_rows_text(self):
        ass, _, _ = sc.strip_ass(ls.rows(PROGS, NOW))
        for s in ('CHRISTMAS', '90S REWIND', 'Bad Santa 2', '2016', 'The Simpsons', 'Simpsons Roast…'):   # 230-px block: ellipsized
            self.assertIn(s, ass)

    def test_narrow_block_has_no_text(self):
        row = ls.Row(90, '90s Rewind', [ls.Block('South Park', 'Pilot', 0.0, 0.02), ls.Block('Freaks', None, 0.02, 1.0)])
        ass, _, _ = sc.strip_ass([row])
        self.assertNotIn('South', ass)
        self.assertIn('Freaks', ass)

    def test_title_with_braces_renders_literally(self):
        row = ls.Row(25, 'Christmas', [ls.Block('Elf {\\b1}', None, 0.0, 1.0)])
        self.assertIn('Elf (\\b1)', sc.strip_ass([row])[0])

    def test_error_strip(self):
        ass, height, cycle = sc.strip_ass([], error='LISTINGS UNAVAILABLE')
        self.assertIn('LISTINGS UNAVAILABLE', ass)
        self.assertEqual(events(ass, 'row'), [])
        self.assertGreaterEqual(height, sc.GRID_VISIBLE + cycle)


class OverlayTest(unittest.TestCase):
    def test_clock_one_event_per_second(self):
        clock = events(sc.overlay_ass([], datetime(2026, 11, 8, 15, 44, 0, tzinfo=EST), 60), 'clock')
        self.assertEqual(len(clock), 60)
        self.assertTrue(clock[0].endswith('3:44 PM'), clock[0])

    def test_promo_text_and_rotation(self):
        start = NOW
        while int(start.timestamp()) // 10 % 4 != 2:
            start += timedelta(seconds=10)
        heads = [t for t in events(sc.overlay_ass(ls.promos(PROGS, start), start, 60), 'promo') if ' ON ' in t]
        self.assertEqual(len(heads), 6)
        self.assertIn('NOW ON 90S REWIND', heads[0])
        self.assertIn('COMING UP ON 90S REWIND', heads[1])
        self.assertIn('NOW ON CHRISTMAS', heads[2])

    def test_no_promos(self):
        self.assertEqual(events(sc.overlay_ass([], NOW, 60), 'promo'), [])


class ScrollTest(unittest.TestCase):
    def test_scroll_continuous_across_items(self):
        a = datetime(2026, 11, 8, 15, 40, 7, tzinfo=EST)
        for n in (1, 12):
            self.assertAlmostEqual(sc.scroll_offset(n * sc.ROW_H, a, 60), sc.scroll_offset(n * sc.ROW_H, a + timedelta(seconds=60), 0))
            self.assertLess(sc.scroll_offset(n * sc.ROW_H, a, 0), n * sc.ROW_H)


class EscapeTest(unittest.TestCase):
    def test_escape_special_and_long(self):
        self.assertEqual(sc.escape('A {b} \\c', 80), 'A (b) \\c')
        self.assertNotIn('\\N', sc.escape('x\\Ny', 80))          # would be a line break in libass
        self.assertIn('\\', sc.escape('x\\Ny', 80))
        long = sc.escape('x' * 100, 80)
        self.assertTrue(long.endswith('…'))
        self.assertLessEqual(len(long), 80)


if __name__ == '__main__':
    unittest.main()
