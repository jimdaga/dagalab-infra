import importlib.util, pathlib, unittest

spec = importlib.util.spec_from_file_location('ups_wake', pathlib.Path(__file__).resolve().parent.parent / 'ups-wake.py')
uw = importlib.util.module_from_spec(spec); spec.loader.exec_module(uw)
UP = lambda ip: True
DOWN = lambda ip: False


class StepTest(unittest.TestCase):
    def test_no_outage_never_wakes(self):
        s, w = uw.step({}, ['OL', 'CHRG'], 1000, DOWN)
        self.assertEqual((s, w), ({}, []))

    def test_settle_then_wake_only_down_nodes(self):
        s, w = uw.step({}, ['OB', 'DISCHRG'], 0, UP);            self.assertEqual(w, [])
        s, w = uw.step(s, ['OL', 'CHRG'], 100, DOWN);             self.assertEqual(w, [])   # just back
        s, w = uw.step(s, ['OL'], 100 + 119, DOWN);               self.assertEqual(w, [])   # < 120 s
        s, w = uw.step(s, ['OL'], 100 + 120, lambda ip: ip != '192.168.3.12')
        self.assertEqual(w, ['dagakube02'])

    def test_short_outage_nodes_up_wakes_nothing_then_forgets(self):
        s, _ = uw.step({}, ['OB'], 0, UP)
        s, _ = uw.step(s, ['OL'], 60, UP)
        s, w = uw.step(s, ['OL'], 60 + 300, UP);                  self.assertEqual(w, [])
        s, w = uw.step(s, ['OL'], 60 + 120 + 1800, DOWN);         self.assertEqual((s, w), ({}, []))

    def test_back_on_battery_resets_settle(self):
        s, _ = uw.step({}, ['OB'], 0, UP)
        s, _ = uw.step(s, ['OL'], 100, UP)
        s, _ = uw.step(s, ['OB'], 150, UP)
        s, w = uw.step(s, ['OL'], 200, DOWN)
        s, w = uw.step(s, ['OL'], 300, DOWN);                     self.assertEqual(w, [])   # only 100 s since back
        s, w = uw.step(s, ['OL'], 320, DOWN);                     self.assertEqual(len(w), 3)

    def test_magic_packet(self):
        p = uw.magic('d8:bb:c1:2d:b1:95')
        self.assertEqual(len(p), 102)
        self.assertEqual(p[:6], b'\xff' * 6)
        self.assertEqual(p[6:12], bytes.fromhex('d8bbc12db195'))


if __name__ == '__main__':
    unittest.main()
