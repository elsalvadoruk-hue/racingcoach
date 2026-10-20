"""Analytic native-time pedal integration, independent of rendered traces."""
import json
import unittest
from unittest.mock import patch

import numpy as np

from telemetry_core import AnalysisLap, ChannelTrace
from lmu_analysis import lap_report
from lmu_pedal_usage import CATEGORY_KEYS, pedal_usage


def trace(times, values, frequency=1, interpolation='LINEAR', mask=None, unit='ratio'):
    return ChannelTrace('pedal', unit, frequency, np.array(times), np.array(values),
                        np.ones(len(times), bool) if mask is None else np.array(mask), interpolation)


def lap(throttle, brake, start=0, end=4, stages=('UNFILTERED', 'UNFILTERED')):
    return AnalysisLap('LMU_DUCKDB', 1, start, end, end-start, None, 'VALIDITY_UNKNOWN', {},
                       {'throttle_ratio': throttle, 'brake_ratio': brake},
                       {key: {'status': 'AVAILABLE', 'input_stage': stage}
                        for key, stage in zip(('throttle_ratio', 'brake_ratio'), stages)})


class PedalUsageTests(unittest.TestCase):
    def assert_totals(self, result, expected, observed=None):
        self.assertEqual([r['key'] for r in result['categories']], list(CATEGORY_KEYS))
        for row, seconds in zip(result['categories'], expected):
            self.assertAlmostEqual(row['duration_s'], seconds, places=10)
        observed = sum(expected) if observed is None else observed
        self.assertAlmostEqual(result['observed_time_s'], observed)
        self.assertAlmostEqual(sum(r['duration_s'] for r in result['categories']), observed)
        self.assertAlmostEqual(result['observed_time_s'] + result['unobserved_time_s'], result['lap_duration_s'])
        self.assertAlmostEqual(sum(r['observed_percent'] for r in result['categories']), 100)
        json.dumps(result, allow_nan=False)

    def test_step_four_categories_and_threshold_equality(self):
        result = pedal_usage(lap(trace(range(5), [.05, 0, .05, 0, 0], interpolation='STEP'),
                                 trace(range(5), [0, .05, .05, 0, 0], interpolation='STEP')))
        self.assertEqual(result['status'], 'AVAILABLE')
        self.assert_totals(result, [1, 1, 1, 1])

    def test_linear_crossings_integrate_fractional_time(self):
        result = pedal_usage(lap(trace([0, 1, 2], [0, .1, 0]),
                                 trace([0, 1, 2], [.1, 0, .1]), end=2))
        self.assert_totals(result, [1, 1, 0, 0])

    def test_linear_plateau_at_threshold_is_active(self):
        result = pedal_usage(lap(trace([0, 1, 2, 3], [0, .05, .05, 0]),
                                 trace([0, 1, 2, 3], [0, 0, 0, 0]), end=3))
        self.assert_totals(result, [1, 0, 0, 2])

    def test_mixed_frequency_intersection_uses_time_not_sample_counts(self):
        result = pedal_usage(lap(trace([0, 1, 2], [0, .1, 0]),
                                 trace(np.arange(0, 2.1, .5), [0, 0, .1, 0, 0], frequency=2), end=2))
        self.assert_totals(result, [.5, 0, .5, 1])

    def test_invalid_holes_and_native_gaps_are_not_bridged(self):
        throttle = trace([0, 1, 2, 3, 6, 7], [1]*6, mask=[1, 1, 0, 1, 1, 1])
        brake = trace(range(8), [0]*8)
        result = pedal_usage(lap(throttle, brake, end=7))
        self.assertEqual(result['status'], 'PARTIAL')
        self.assert_totals(result, [2, 0, 0, 0])
        self.assertEqual(result['unobserved_time_s'], 5)
        self.assertEqual(result['categories'][0]['observed_percent'], 100)

    def test_lap_boundaries_are_clipped_without_extrapolation(self):
        result = pedal_usage(lap(trace([.25, 1.25, 2.25, 3.25], [1]*4),
                                 trace([.5, 1.5, 2.5, 3.5], [0]*4)))
        self.assert_totals(result, [2.75, 0, 0, 0])
        self.assertEqual(result['unobserved_time_s'], 1.25)

    def test_signed_and_offset_clocks_preserve_integration(self):
        for start in (-100, 1_000_000):
            with self.subTest(start=start):
                times = np.array([0, 1, 2])+start
                result = pedal_usage(lap(trace(times, [0, .1, 0]), trace(times, [0, 0, 0]), start, start+2))
                self.assert_totals(result, [1, 0, 0, 1])

    def test_crossing_outside_clipped_lap_does_not_change_state(self):
        result = pedal_usage(lap(trace([0, 1, 2], [0, .1, 0]), trace([0, 1, 2], [0]*3), .6, 1.4))
        self.assert_totals(result, [.8, 0, 0, 0])

    def test_out_of_range_or_nan_sample_is_missing_not_zero(self):
        for value in (-.1, 1.1, float('nan')):
            with self.subTest(value=value):
                result = pedal_usage(lap(trace(range(5), [1, 1, value, 1, 1]), trace(range(5), [0]*5)))
                self.assert_totals(result, [2, 0, 0, 0])

    def test_input_stage_missing_mismatch_and_units_are_unavailable(self):
        for stages in ((None, None), ('UNFILTERED', 'GAME_PROCESSED'), ('UNKNOWN', 'UNKNOWN')):
            result = pedal_usage(lap(trace(range(5), [1]*5), trace(range(5), [0]*5), stages=stages))
            self.assertEqual(result['status'], 'UNAVAILABLE')
            self.assertIsNone(result['categories'][0]['duration_s'])
        result = pedal_usage(lap(trace(range(5), [1]*5, unit='%'), trace(range(5), [0]*5)))
        self.assertEqual(result['status'], 'UNAVAILABLE')

    def test_missing_trace_malformed_support_and_wrong_trace_type(self):
        source = lap(trace(range(5), [1]*5), trace(range(5), [0]*5))
        for key, value in (('brake_ratio', None), ('brake_ratio', {'time_s': []})):
            source.channels[key] = value
            self.assertEqual(pedal_usage(source)['status'], 'UNAVAILABLE')
        source.channels['brake_ratio'] = trace(range(5), [0]*5)
        source.support['brake_ratio'] = None
        self.assertEqual(pedal_usage(source)['status'], 'UNAVAILABLE')

    def test_no_common_continuous_time_and_bad_boundaries(self):
        result = pedal_usage(lap(trace([0, 1], [1, 1]), trace([3, 4], [0, 0])))
        self.assertEqual(result['status'], 'UNAVAILABLE')
        for start, end in ((0, 0), (2, 1), (False, 4), (0, float('inf'))):
            result = pedal_usage(lap(trace([0, 1], [1, 1]), trace([0, 1], [0, 0]), start, end))
            self.assertEqual(result['status'], 'UNAVAILABLE')

    def test_total_native_sample_limit_is_bounded(self):
        source = lap(trace(range(5), [1]*5), trace(range(5), [0]*5))
        with patch('lmu_pedal_usage.MAX_NATIVE_SAMPLES', 9):
            self.assertEqual(pedal_usage(source)['status'], 'UNAVAILABLE')

    def test_render_resolution_and_validity_do_not_change_pedal_usage(self):
        source = lap(trace(range(5), [0, .1, .1, 0, 0]), trace(range(5), [.1, 0, 0, .1, 0]))
        self.assertEqual(lap_report(source, 3)['pedal_usage'], lap_report(source, 501)['pedal_usage'])
        self.assertIsNone(lap_report(source)['valid'])
        self.assertEqual(lap_report(source)['reference_status'], 'VALIDITY_UNVERIFIED')


if __name__ == '__main__':
    unittest.main()
