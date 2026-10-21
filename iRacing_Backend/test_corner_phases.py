"""Reference-bound phase accounting and restricted diagnosis overrides."""
import atexit
from copy import deepcopy
import os
from pathlib import Path
import tempfile
import unittest

import numpy as np

_runtime = tempfile.TemporaryDirectory(prefix='corner-phases-test-')
atexit.register(_runtime.cleanup)
os.environ.setdefault('IRACING_TUNER_DB_PATH', str(Path(_runtime.name) / 'memory.sqlite3'))

from analysis_corners import CornerAnalysis
from closed_loop_coach import detailed_corner_diagnosis, first_deviation


def comparison():
    pct = np.linspace(0, 1, 1001)
    reference = {'distance_pct': pct, 'time_s': pct * 100,
                 'speed_kph': np.full(len(pct), 100.)}
    # Loss occurs mainly before pickup, with a separate larger straight loss.
    loss = np.interp(pct, [.0, .1, .2, .3, .4, .6, 1.], [0, 0, .12, .20, .23, .8, .8])
    target = {**reference, 'time_s': reference['time_s'] + loss}
    metrics = {'brake_start_pct': .1, 'brake_release_pct': .2,
               'min_speed_pct': .25, 'throttle_first_pct': .3,
               'full_throttle_pct': .35, 'distance_resolution_m': .5,
               'min_speed_kph': 80., 'exit_speed_kph': 100.}
    corner = {'id': 'C1', 'entry_pct': .1, 'exit_pct': .4, 'delta_s': .23,
              'target': {**metrics, 'brake_release_pct': .209, 'throttle_first_pct': .307,
                         'full_throttle_pct': .358}, 'reference': metrics}
    corner['phases'] = CornerAnalysis()._corner_phases(target, reference, corner,
                                                     {'entry_pct': .6})
    return target, reference, corner


class CornerPhaseTests(unittest.TestCase):
    def test_release_is_first_and_losses_precede_pickup(self):
        _, _, corner = comparison()
        first = first_deviation(corner, 1000.)
        self.assertEqual(first['event'], 'brake_release')
        self.assertAlmostEqual(first['delta_m'], 9.)
        self.assertIn('throttle_first', [item['event'] for item in first['then']])
        self.assertEqual(first['phase'], 'braking')
        phases = corner['phases']
        self.assertGreater(phases['braking']['delta_s'] + phases['rotation']['delta_s'],
                           phases['drive']['delta_s'])

    def test_only_documented_cause_override_preserves_original_rule(self):
        _, _, corner = comparison()
        diagnosis = detailed_corner_diagnosis(corner, 1000., 'LOCAL')
        self.assertEqual(diagnosis['root_cause'], 'BRAKE_RELEASE_LATE')
        self.assertEqual(diagnosis['rule_cause'], 'THROTTLE_RETURN_LATE')
        self.assertTrue(diagnosis['cause_from_first_deviation'])
        self.assertIn('브레이크 놓음 9 m 늦음', diagnosis['evidence'][0])
        corner['phases']['drive']['delta_s'] = .5
        unchanged = detailed_corner_diagnosis(corner, 1000., 'LOCAL')
        self.assertEqual(unchanged['root_cause'], 'THROTTLE_RETURN_LATE')
        self.assertNotIn('rule_cause', unchanged)

    def test_full_throttle_alone_retains_cause(self):
        _, _, corner = comparison()
        corner['target'] = {**corner['reference'], 'full_throttle_pct': .358}
        result = detailed_corner_diagnosis(corner, 1000., 'LOCAL')
        self.assertEqual(result['root_cause'], 'THROTTLE_RETURN_LATE')
        self.assertEqual(result['first_deviation']['event'], 'full_throttle')
        self.assertNotIn('rule_cause', result)

    def test_accounting_excludes_straight_and_exposes_extended_entry(self):
        target, reference, corner = comparison()
        pct = target['distance_pct']
        target['time_s'] += np.interp(pct, [0., .08, .1, 1.], [0., 0., .04, .04])
        for start in (.1, .08):
            corner['reference']['brake_start_pct'] = start
            phases = CornerAnalysis()._corner_phases(target, reference, corner, {'entry_pct': .6})
            total = sum(phases[key]['delta_s'] for key in ('braking', 'rotation', 'drive'))
            self.assertAlmostEqual(total + phases['unexplained_s'], corner['delta_s'], delta=.001)
            self.assertAlmostEqual(phases['unexplained_s'], 0.)
            self.assertEqual(phases['braking']['start_pct'], corner['entry_pct'])
            if start == .08:
                self.assertAlmostEqual(phases['approach']['delta_s'], .04)
            else:
                self.assertIsNone(phases['approach'])
            self.assertAlmostEqual(phases['straight_after']['delta_s'], .57)
            self.assertEqual(phases['straight_after']['exit_speed_delta_kph'], 0.)

    def test_nan_boundary_only_invalidates_touching_phases(self):
        target, reference, corner = comparison()
        target['time_s'][200] = np.nan
        phases = CornerAnalysis()._corner_phases(target, reference, corner, {'entry_pct': .6})
        self.assertIsNone(phases['braking'])
        self.assertIsNone(phases['rotation'])
        self.assertIsNotNone(phases['drive'])
        self.assertIsNotNone(phases['straight_after'])
        self.assertAlmostEqual(phases['unexplained_s'], .2)
        self.assertEqual(phases['unexplained_reason'], 'missing_boundary')

    def test_uncertainty_threshold_and_missing_uncertainty(self):
        _, _, corner = comparison()
        corner['target'] = deepcopy(corner['reference'])
        corner['target']['brake_release_pct'] += .002
        for metrics in (corner['target'], corner['reference']):
            metrics['event_uncertainty_m'] = {'brake_release': 2.5}
        self.assertIsNone(first_deviation(corner, 1000.))
        corner['target']['brake_release_pct'] += .006
        self.assertIsNone(first_deviation(corner, 1000.))
        corner['target']['brake_release_pct'] += .004
        self.assertEqual(first_deviation(corner, 1000.)['threshold_m'], 10.)
        for metrics in (corner['target'], corner['reference']):
            metrics.pop('event_uncertainty_m')
            metrics.pop('distance_resolution_m')
        self.assertIsNone(first_deviation(corner, 1000.))

    def test_no_braking_and_missing_aggregate_events(self):
        target, reference, corner = comparison()
        corner['reference']['brake_start_pct'] = None
        corner['reference']['brake_release_pct'] = None
        phases = CornerAnalysis()._corner_phases(target, reference, corner, None)
        self.assertEqual(phases['braking']['delta_s'], 0.)
        self.assertEqual(phases['braking']['start_pct'], phases['braking']['end_pct'])
        self.assertEqual(phases['rotation']['start_pct'], corner['entry_pct'])
        self.assertEqual(phases['straight_after']['end_pct'], 1.)
        corner['reference']['throttle_first_pct'] = None
        self.assertIsNone(CornerAnalysis()._corner_phases(target, reference, corner, None))

    def test_early_and_late_start_overrides_are_restricted(self):
        _, _, corner = comparison()
        corner['target']['brake_start_pct'] = .09
        result = detailed_corner_diagnosis(corner, 1000., 'LOCAL')
        self.assertEqual(result['root_cause'], 'BRAKE_POINT_EARLY')
        corner['target']['brake_start_pct'] = .11
        result = detailed_corner_diagnosis(corner, 1000., 'LOCAL')
        self.assertEqual(result['root_cause'], 'THROTTLE_RETURN_LATE')
        self.assertTrue(any('10 m 늦어 진입이 빨랐습니다' in line for line in result['evidence']))

    def test_fifty_metre_approach_is_outside_corner_sum(self):
        target, reference, corner = comparison()
        corner['reference']['brake_start_pct'] = .05
        target['time_s'] += np.interp(target['distance_pct'], [0., .05, .1, 1.], [0., 0., .2, .2])
        phases = CornerAnalysis()._corner_phases(target, reference, corner, None)
        self.assertAlmostEqual((phases['approach']['end_pct'] - phases['approach']['start_pct']) * 1000, 50.)
        self.assertAlmostEqual(phases['approach']['delta_s'], .2)
        self.assertAlmostEqual(sum(phases[k]['delta_s'] for k in ('braking', 'rotation', 'drive')), .23)
        self.assertLessEqual(abs(phases['unexplained_s']), .001)

    def test_release_before_entry_and_reversed_events_are_clamped(self):
        target, reference, corner = comparison()
        for release, pickup, expected_release, expected_pickup in (
                (.08, .3, .1, .3), (.2, .15, .2, .2), (.5, .3, .4, .4),
                (.2, None, .2, .4), (.2, .5, .2, .4)):
            with self.subTest(release=release, pickup=pickup):
                corner['reference'].update(brake_release_pct=release, throttle_first_pct=pickup)
                phases = CornerAnalysis()._corner_phases(target, reference, corner, None)
                self.assertEqual(phases['braking']['end_pct'], expected_release)
                self.assertEqual(phases['rotation']['start_pct'], expected_release)
                self.assertEqual(phases['rotation']['end_pct'], expected_pickup)
                self.assertEqual(phases['drive']['start_pct'], expected_pickup)
                self.assertLessEqual(abs(phases['unexplained_s']), .001)

    def test_adjacent_corners_straight_joins_next_approach(self):
        target, reference, corner = comparison()
        following = {'entry_pct': .6, 'exit_pct': .8, 'reference': {
            'brake_start_pct': .55, 'brake_release_pct': .65, 'throttle_first_pct': .7}, 'delta_s': 0.}
        first = CornerAnalysis()._corner_phases(target, reference, corner, following)
        second = CornerAnalysis()._corner_phases(target, reference, following, None)
        self.assertEqual(first['straight_after']['end_pct'], second['approach']['start_pct'])
        following['reference']['brake_start_pct'] = .61
        first = CornerAnalysis()._corner_phases(target, reference, corner, following)
        second = CornerAnalysis()._corner_phases(target, reference, following, None)
        self.assertEqual(first['straight_after']['end_pct'], following['entry_pct'])
        self.assertIsNone(second['approach'])

    def test_first_event_before_entry_uses_approach_and_combined_loss(self):
        _, _, corner = comparison()
        corner['reference']['brake_start_pct'] = .05
        corner['target']['brake_start_pct'] = .04
        corner['phases']['approach'] = {'delta_s': .2, 'start_pct': .05, 'end_pct': .1}
        corner['phases']['braking']['delta_s'] = -.1
        first = first_deviation(corner, 1000.)
        self.assertEqual(first['phase'], 'approach')
        self.assertEqual(first['phase_delta_s'], .2)
        self.assertIn('drive', first['phase_deltas_s'])
        result = detailed_corner_diagnosis(corner, 1000., 'LOCAL')
        self.assertEqual(result['root_cause'], 'BRAKE_POINT_EARLY')
        corner['phases']['approach'] = None
        self.assertEqual(detailed_corner_diagnosis(corner, 1000., 'LOCAL')['root_cause'], 'THROTTLE_RETURN_LATE')

    def test_unobserved_approach_does_not_override_cause(self):
        _, _, corner = comparison()
        corner['phases']['approach'] = {'delta_s': None, 'start_pct': .05, 'end_pct': .1}
        self.assertEqual(detailed_corner_diagnosis(corner, 1000., 'LOCAL')['root_cause'], 'THROTTLE_RETURN_LATE')


if __name__ == '__main__':
    unittest.main()
