"""A missing LMU countdown needs continuous, same-session expiry evidence."""
from collections import deque
from copy import deepcopy
import unittest

from race_engineer import RaceEngineer


class ExpiredClockTests(unittest.TestCase):
    def setUp(self):
        self.wall = RaceEngineer()
        self.race = dict(game='lmu', session_type='Race', state=4, flags=0, total_laps=None,
                         time_remaining_s=.9, player_idx=0, session_duration_s=1200,
                         cars=[dict(idx=0, class_id=1, position=1, class_position=1,
                                    lap_completed=9, distance_pct=.45, on_pit_road=False, track_surface=3)])
        self.wall.ingest(deepcopy(self.race), 'run', 1200)
        self.wall.paces[0] = deque([100.] * 3)

    def tick(self, at, remaining=None, run='run'):
        self.race['time_remaining_s'] = remaining
        self.wall.ingest(deepcopy(self.race), run, at)
        return self.wall.view(dict(session_time=at, session_time_remaining_s=remaining,
            fuel_l=2.88, session_flags=0), 4.19, 3)

    def test_countdown_crossing_with_missing_lmu_clock_keeps_finish_distance(self):
        for at in (1201, 1202, 1250):
            # Continuous 1 Hz observations, including a long post-zero period.
            if at == 1250:
                for t in range(1203, 1250): self.tick(t)
            view = self.tick(at)
            self.assertEqual(view['remaining_laps'], .55)
            self.assertEqual(view['fuel_finish_shortfall_l'], 0)

    def test_real_shortage_survives_recovered_countdown(self):
        self.tick(1201)
        view = self.wall.view(dict(session_time=1201, fuel_l=1., session_flags=0), 4.19, 3)
        self.assertGreater(view['fuel_finish_shortfall_l'], 1)

    def test_missing_is_unknown_without_a_fresh_zero_crossing(self):
        for at, remaining, game in ((1200.5, None, 'lmu'), (1204, None, 'lmu'), (1201, None, 'iracing')):
            with self.subTest(at=at, game=game):
                self.setUp(); self.race['game'] = game
                self.assertIsNone(self.tick(at, remaining)['remaining_laps'])

    def test_new_session_clock_reversal_disconnect_or_extension_clears_expiry(self):
        for change in ('new_run', 'reverse', 'gap', 'inactive', 'extension'):
            with self.subTest(change=change):
                self.setUp(); self.tick(1201)
                if change == 'new_run': self.tick(1202, run='other')
                elif change == 'reverse': self.tick(1199)
                elif change == 'gap': self.tick(1210)
                elif change == 'inactive':
                    self.race['state'] = 3; self.tick(1202); self.race['state'] = 4
                else: self.tick(1202, 60.)
                self.assertIsNone(self.tick(1211 if change == 'gap' else 1203)['remaining_laps'])

    def test_stale_view_does_not_reuse_recovered_clock(self):
        self.tick(1201)
        self.assertIsNone(self.wall.view(dict(session_time=1204, fuel_l=2.88), 4.19, 3)['remaining_laps'])
