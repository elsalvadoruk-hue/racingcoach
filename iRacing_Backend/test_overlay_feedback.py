"""Measured footer arithmetic and clean-pass lifetime; no stores or services."""
import unittest

import numpy as np

from live_corners import LiveCornerCoach
from overlay_feedback import corner_feedback
from overlay_reference import AlignedReference, GridReference


def reference():
    distance = np.arange(1001)/1000
    return AlignedReference(GridReference({'distance_pct': distance, 'time_s': distance*100,
        'Brake': ((distance >= .1) & (distance < .2)).astype(float),
        'Throttle': (distance >= .25).astype(float)}, 100), 1000)


def view():
    return {'track_length_m': 1000, 'snapshot': {'session_time': 50., 'lap_number': 2},
            'corner_pass': {'id': 'c1', 'entry_pct': .12, 'apex_pct': .2, 'exit_pct': .3,
                            'duration_s': 18.18, 'brake_pct': .108, 'brake_observed': True,
                            'exit_at': 49., 'lap': 2, 'clean': True}}


class FooterTests(unittest.TestCase):
    def test_time_and_brake_use_same_graph_reference_interval_and_sign(self):
        self.assertEqual(corner_feedback(reference(), view()),
                         {'corner_id': 'c1', 'delta_s': .18, 'brake_delta_m': 8.})
        data = view()
        data['corner_pass'].update(duration_s=17.88, brake_pct=.097)
        result = corner_feedback(reference(), data)
        self.assertEqual((result['delta_s'], result['brake_delta_m']), (-.12, -3.))

    def test_incomplete_dirty_expired_or_old_lap_never_reuses_a_value(self):
        for changes in ({'clean': False}, {'duration_s': None}, {'exit_at': 10.},
                        {'exit_at': 51.}, {'lap': 0}, {'exit_pct': None}):
            data = view()
            data['corner_pass'].update(changes)
            self.assertIsNone(corner_feedback(reference(), data), changes)
        data = view()
        data.pop('corner_pass')
        self.assertIsNone(corner_feedback(reference(), data))

    def test_partial_reference_time_is_not_joined_across_missing_intervals(self):
        lap = reference()
        lap.times[150:160] = np.nan
        self.assertIsNone(corner_feedback(lap, view()))

    def test_brake_missing_or_not_actually_observed_keeps_time_only(self):
        for changes in ({'brake_pct': None}, {'brake_observed': False}, {'brake_pct': .4}):
            data = view()
            data['corner_pass'].update(changes)
            result = corner_feedback(reference(), data)
            self.assertEqual(result['delta_s'], .18)
            self.assertIsNone(result['brake_delta_m'])
        lap = reference()
        lap.grid['Brake'][:] = np.nan
        self.assertIsNone(corner_feedback(lap, view())['brake_delta_m'])

    def test_clean_live_pass_reaches_footer_and_dirty_pass_or_reset_clears_it(self):
        definitions = [{'id': 'c1', 'entry_pct': .12, 'apex_pct': .2, 'exit_pct': .3}]
        coach = LiveCornerCoach(lookup=lambda _: definitions)
        identity = {'car_id': '1', 'track_id': '2'}
        def feed(lap, dirty=False):
            for step in range(50, 351):
                pct = step/1000
                coach.ingest({'distance_pct': pct, 'session_time': lap*100+pct*100,
                    'speed_mps': 10., 'lap_number': lap, 'on_track': True, 'is_replay': False,
                    'on_pit_road': False, 'brake': float(.108 <= pct < .19),
                    'throttle': float(pct >= .25), 'incidents': 0,
                    'car_left_right': 2 if dirty and step == 200 else 0, 'session_flags': 0}, identity, lap*100+pct*100, 1000)
        feed(2)
        measurement = coach.last_measurement
        self.assertAlmostEqual(measurement['duration_s'], 18.)
        self.assertAlmostEqual(measurement['exit_at'], 230.)
        data = view()
        data.update(snapshot={'session_time': 235., 'lap_number': 2}, corner_pass=measurement)
        result = corner_feedback(reference(), data)
        self.assertAlmostEqual(result['delta_s'], 0.)
        self.assertEqual(result['brake_delta_m'], 8.)
        feed(3, dirty=True)
        self.assertIsNone(coach.last_measurement)
        feed(4)
        self.assertIsNotNone(coach.last_measurement)
        coach.reset()
        self.assertIsNone(coach.last_measurement)


if __name__ == '__main__':
    unittest.main()
