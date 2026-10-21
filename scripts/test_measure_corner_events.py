"""Synthetic checks for the read-only T0 measurement tool; no app imports."""
import atexit
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

_runtime = tempfile.TemporaryDirectory(prefix='corner-event-tool-test-')
atexit.register(_runtime.cleanup)
os.environ.setdefault('IRACING_TUNER_DB_PATH', str(Path(_runtime.name) / 'test.sqlite3'))

import numpy as np

from measure_corner_events import abs_time_share, isolate, sample_event, summarize, phase_summary, phase_gate


class CornerEventMeasurementTests(unittest.TestCase):
    def test_phase_gate_excludes_missing_boundaries_and_never_pools_files(self):
        def row(value, complete=True):
            return {'phases': {'braking': {'delta_s': 0.} if complete else None,
                               'rotation': {'delta_s': 0.}, 'drive': {'delta_s': 0.},
                               'unexplained_s': value}}
        good = phase_summary([row(.0004)] * 1000 + [row(1., False)])
        bad = phase_summary([row(-.006)] * 20)
        self.assertEqual(good['absolute']['n'], 1000)
        self.assertEqual(good['missing_boundary'], 1)
        self.assertAlmostEqual(bad['absolute']['p95'], .006)
        gate = phase_gate([{'file': 'good.ibt', 'sample_basis': 'NATIVE', 'phase_measurement': good},
                           {'file': 'bad.ibt', 'sample_basis': 'NATIVE', 'phase_measurement': bad}])
        self.assertTrue(gate['stop'])
        self.assertEqual(gate['failed_files'], ['bad.ibt'])

    def test_isolation_overrides_inherited_store_locations(self):
        with patch.dict(os.environ, {'IRACING_DATA_DIR': 'existing-data',
                                     'IRACING_TUNER_DB_PATH': 'existing.sqlite3',
                                     'IRACING_AI_ENABLED': '1'}):
            isolate(Path(_runtime.name))
            self.assertTrue(Path(os.environ['IRACING_TUNER_DB_PATH']).is_relative_to(_runtime.name))
            self.assertEqual(Path(os.environ['IRACING_DATA_DIR']).resolve(), Path(_runtime.name).resolve())
            self.assertEqual(os.environ['IRACING_AI_ENABLED'], '0')
            self.assertEqual(os.environ['GARAGE61_AUTO_COLLECT'], '0')

    def test_crossing_interpolates_in_time_and_distance(self):
        event = sample_event(np.array([.1, .102, .104]), np.array([1., 1.1, 1.2]),
                             np.array([0., .4, .8]), .2, .1, .104,
                             True, True, .1, 1000.)
        self.assertAlmostEqual(event['pct'], .101)
        self.assertAlmostEqual(event['time_s'], 1.05)

    def test_release_chooses_last_falling_crossing(self):
        event = sample_event(np.array([.1, .101, .102, .103, .104]),
                             np.arange(5) * .1, np.array([.2, 0., .2, .2, 0.]),
                             .05, .1, .104, False, False, .1, 1000.)
        self.assertAlmostEqual(event['pct'], .10375)

    def test_gap_does_not_create_crossing(self):
        event = sample_event(np.array([.1, .101, .102]), np.array([0., .3, .4]),
                             np.array([0., .4, .8]), .2, .1, .102,
                             True, True, .1, 1000.)
        self.assertIsNone(event)

    def test_boundary_hold_is_clipped_and_not_evidence_for_gate(self):
        event = sample_event(np.array([.1, .101]), np.array([0., .1]),
                             np.array([.8, .8]), .2, .1, .101,
                             True, True, .1, 1000.)
        self.assertEqual(event['source'], 'CLIPPED')
        stats = summarize([{'throttle_first': {'source': 'CLIPPED', 'delta': 10.}}])
        self.assertEqual(stats['throttle_first']['clipped'], 1)
        self.assertEqual(stats['throttle_first']['absolute']['n'], 0)

    def test_abs_weights_time_and_excludes_missing_state(self):
        pct = np.array([0., .001, .0015, .002])
        times = np.array([0., .1, .2, .3])
        brake = np.ones(4)
        share = abs_time_share(pct, times, brake, np.array([1., 0., np.nan, 0.]),
                               0., .002, .1, 1000.)
        self.assertAlmostEqual(share['pct'], 50.)
        self.assertAlmostEqual(share['observed_share'], 2 / 3)
        self.assertNotEqual(share['pct'], 100 * .001 / .0015)

    def test_abs_threshold_duration_and_window_are_clipped(self):
        share = abs_time_share(np.array([0., .001, .002]), np.array([0., .1, .2]),
                               np.array([0., .16, .16]), np.array([1., 0., 0.]),
                               .0005, .0015, .1, 1000.)
        self.assertAlmostEqual(share['pct'], 50.)
        self.assertAlmostEqual(share['observed_s'], .1)

    def test_missing_channels_and_empty_statistics_stay_unknown(self):
        self.assertIsNone(abs_time_share([], [], None, None, 0., 1., .1, 1000.))
        stats = summarize([{'brake_start': {'delta': None}}])
        self.assertIsNone(stats['brake_start']['absolute']['p95'])
        self.assertEqual(stats['brake_start']['missing'], 1)


if __name__ == '__main__':
    unittest.main()
