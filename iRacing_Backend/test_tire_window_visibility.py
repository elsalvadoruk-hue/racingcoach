import unittest

from tire_reader.window_visibility import GameVisibility


class VisibilityTests(unittest.TestCase):
    capture=dict(left=-2560,top=0,width=2560,height=1440)

    def test_game_spanning_triple_screens_can_be_visible_without_window_focus(self):
        probe=GameVisibility(windows=lambda:[dict(left=-2560,top=0,width=7680,height=1440)],points=lambda _:True)
        self.assertTrue(probe.window_visible(self.capture))
        self.assertTrue(probe.pressure_visible(self.capture,dict(FL=[50,100,120,140])))

    def test_game_on_different_monitor_or_minimized_is_not_captured(self):
        for windows in ([],[dict(left=0,top=-1080,width=1920,height=1080)]):
            probe=GameVisibility(windows=lambda:windows,points=lambda _:True)
            self.assertFalse(probe.window_visible(self.capture))

    def test_actual_new_pressure_coordinates_are_checked_in_desktop_space(self):
        received=[]
        probe=GameVisibility(windows=lambda:[self.capture],points=lambda points:received.extend(points) or True)
        self.assertTrue(probe.pressure_visible(self.capture,dict(FL=[50,100,120,140])))
        self.assertEqual(received,[(-2475,120),(-2509,101),(-2442,138)])
        received.clear()
        self.assertTrue(probe.pressure_visible(self.capture,dict(FL=[80,110,150,150])))
        self.assertEqual(received[0],(-2445,130))

    def test_non_game_window_covering_digits_is_rejected(self):
        probe=GameVisibility(windows=lambda:[self.capture],points=lambda _:False)
        self.assertTrue(probe.window_visible(self.capture))
        self.assertFalse(probe.pressure_visible(self.capture,dict(FL=[50,100,120,140])))
