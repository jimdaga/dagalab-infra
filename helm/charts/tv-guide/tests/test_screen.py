import importlib.util, pathlib, re, sys, unittest
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
                  '3:30 PM', '4:00 PM'):
            self.assertIn(s, ass)
        self.assertNotIn('4:30 PM', ass)


POS = re.compile(r'\\an(\d)\\pos\((\d+),(\d+)\)\\fs(\d+)[^}]*}(.*)$')


def texts(ass):
    """(alignment, x, size, text) of every text event (boxes have no \\fs)."""
    out = []
    for line in ass.splitlines():
        m = line.startswith('Dialogue:') and POS.search(line)
        if m:
            out.append((int(m[1]), int(m[2]), int(m[4]), re.sub(r'{[^}]*}', '', m[5])))
    return out


class CenterCutTest(unittest.TestCase):
    """All text sits in the middle 4:3 (1440 px) so a CRT that zooms 16:9 to fill the screen loses only decoration."""
    CHAR_W = 0.5                         # Barlow Condensed Bold, generous average advance per em

    def assertInside(self, ass):
        lo, hi = sc.SAFE_X, sc.SAFE_X + sc.SAFE_W
        for an, x, size, text in texts(ass):
            w = len(text) * size * self.CHAR_W
            left = {7: x, 4: x, 1: x, 5: x - w / 2, 8: x - w / 2, 2: x - w / 2}.get(an, x - w)
            self.assertGreaterEqual(left, lo, text)
            self.assertLessEqual(left + w, hi, text)

    def test_safe_area_is_4_3(self):
        self.assertEqual(sc.SAFE_W * 3, sc.H * 4)
        self.assertEqual(sc.SAFE_X * 2 + sc.SAFE_W, sc.W)

    def test_frame(self):
        self.assertInside(sc.frame_ass(ls.window(NOW)))

    def test_strip(self):
        long = ls.Row(80, 'Throwback Theater', [ls.Block('A' * 80, 'B' * 80, 0.0, 0.5), ls.Block('C' * 80, None, 0.5, 1.0)])
        self.assertInside(sc.strip_ass(ls.rows(PROGS, NOW) + [long])[0])
        self.assertInside(sc.strip_ass([], error='X' * 80)[0])

    def test_overlay(self):
        p = ls.Programme(80, 'Throwback Theater', 'T' * 80, 'D' * 80, NOW, NOW + timedelta(hours=2), None, None, None)
        self.assertInside(sc.overlay_ass([ls.Promo('NEXT', p)], NOW, 10))

    def test_last_column_and_tab_bleed_to_edges(self):
        ass, _, _ = sc.strip_ass([ls.Row(25, 'Christmas', [ls.Block('Elf', None, 0.0, 1.0)])])
        boxes = re.findall(r'\\pos\((\d+),\d+\).*?\\p1}m 0 0 l (\d+) ', ass)
        self.assertIn(('0', str(sc.SAFE_X + sc.TAB_W - 3)), boxes)                  # channel tab from the left edge
        self.assertTrue(any(int(x) + int(w) == sc.W for x, w in boxes), boxes)       # last block to the right edge


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
        for s in ('CHRISTMAS', '90S REWIND', 'Bad Santa 2', '2016', 'The Sim…', 'Simpsons…', 'South Park'):   # 244-px block: ellipsized
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
