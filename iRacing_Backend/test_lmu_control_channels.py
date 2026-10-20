import unittest
import numpy as np

from lmu_control_channels import with_gear_events, control_values
from telemetry_core import AnalysisLap


class GearEventTests(unittest.TestCase):
    def lap(self, start=1., end=3.):
        return AnalysisLap('LMU_DUCKDB', 1, start, end, end-start, None,
                           'VALIDITY_UNKNOWN', {}, {}, {})

    def test_keeps_neutral_reverse_and_exact_shift_boundaries(self):
        lap = with_gear_events(self.lap(), (np.array([.5, 1.33, 1.7, 2.01]),
            np.array([-1, 0, 1, 2])), np.arange(0, 4.01, .01))
        self.assertEqual(control_values(lap, [1, 1.329, 1.33, 1.699, 1.7, 2.009, 2.01, 3], 'gear'),
                         [-1., -1., 0., 0., 1., 1., 2., 2.])
        self.assertIsNone(control_values(lap, [.99], 'gear')[0])
        self.assertIsNone(control_values(lap, [3.01], 'gear')[0])
        self.assertEqual(lap.support['gear']['status'], 'AVAILABLE')
        self.assertIsNone(lap.valid)

    def test_no_initial_inference_and_bad_event_blocks_until_next(self):
        lap = with_gear_events(self.lap(), (np.array([1.2, 1.5, 1.9, 2.4, 2.6]),
            np.array([0, 3.5, np.nan, 11, 2])), np.arange(0, 4.01, .01))
        self.assertEqual(control_values(lap, [1, 1.2, 1.4, 1.5, 1.8, 2, 2.5, 2.6], 'gear'),
                         [None, 0., 0., None, None, None, None, 2.])
        self.assertEqual(lap.support['gear']['status'], 'PARTIAL')

    def test_missing_and_non_continuous_time_axis_stay_unavailable(self):
        lap = with_gear_events(self.lap(), None, np.arange(0, 4.01, .01))
        self.assertEqual(lap.support['gear']['status'], 'MISSING')
        self.assertNotIn('gear', lap.channels)
        lap = with_gear_events(self.lap(), (np.array([0]), np.array([1])), np.array([0, 1, 3, 4]))
        self.assertEqual(lap.support['gear']['status'], 'UNAVAILABLE')
        self.assertNotIn('gear', lap.channels)


if __name__ == '__main__':
    unittest.main()
