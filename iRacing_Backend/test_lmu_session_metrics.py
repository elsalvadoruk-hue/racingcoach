import unittest
from types import SimpleNamespace

import numpy as np

from telemetry_core import ChannelTrace
from lmu_session_metrics import session_metrics


def lap(number, duration, midpoint_time):
    times = np.arange(duration+1, dtype=float)
    distances = np.interp(times, [0, midpoint_time, duration], [10, 500, 990])
    return SimpleNamespace(lap_number=number, recorded_lap_time_s=float(duration),
        diagnostic_status='VALIDITY_UNKNOWN', channels={'distance_m':
            ChannelTrace('Lap Dist', 'm', 1., times, distances, np.ones(len(times), dtype=bool))})


class SessionMetricsTests(unittest.TestCase):
    def test_native_piecewise_times_combine_only_observed_common_distance(self):
        summary, splits = session_metrics([lap(2, 12, 5), lap(3, 10, 6)])
        self.assertEqual(summary['median_time_s'], 11.)
        self.assertEqual(summary['spread_s'], 2.)
        self.assertEqual(summary['stddev_s'], 1.)
        self.assertEqual(summary['fastest_recorded_time_s'], 10.)
        self.assertEqual(splits['start_m'], 10.)
        self.assertEqual(splits['end_m'], 990.)
        self.assertEqual(splits['observed_combined_minimum_s'], 9.)
        self.assertEqual([r['source_lap_number'] for r in splits['rows']], [2, 3])
        self.assertFalse(splits['official_sectors'])
        self.assertFalse(splits['full_lap_prediction'])
        self.assertEqual(summary['game_validity'], 'UNVERIFIED')

    def test_one_lap_does_not_produce_a_combined_prediction(self):
        _, splits = session_metrics([lap(2, 12, 5)])
        self.assertIsNone(splits['observed_combined_minimum_s'])
        self.assertEqual(splits['rows'], [])

    def test_a_native_gap_is_not_bridged_by_splits(self):
        first = lap(2, 12, 5)
        first.channels['distance_m'] = ChannelTrace('Lap Dist', 'm', 1.,
            np.array([0., 12.]), np.array([10., 990.]), np.ones(2, dtype=bool))
        summary, splits = session_metrics([first, lap(3, 10, 6)])
        self.assertEqual(summary['count'], 2)
        self.assertEqual(splits['status'], 'UNAVAILABLE')
        self.assertIsNone(splits['observed_combined_minimum_s'])


if __name__ == '__main__':
    unittest.main()
