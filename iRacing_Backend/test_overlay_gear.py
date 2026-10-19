"""Gear colors and discrete position lookup; no SDK, stores or paid services."""
import unittest

import numpy as np

from overlay_gear import gear_display, gear_value
from overlay_reference import AlignedReference, GridReference


def reference():
    distance = np.linspace(0, 1, 5)
    return AlignedReference(GridReference({'distance_pct': distance, 'time_s': distance*100,
                                         'Gear': np.array([2., 2., 3., 4., 4.])}, 100), 1000)


class GearTests(unittest.TestCase):
    def test_lower_equal_and_higher_use_requested_colors(self):
        for actual, expected in ((2, 'gear_low'), (3, 'text'), (4, 'success')):
            display = gear_display(actual, 3)
            self.assertEqual(display['text'], str(actual))
            self.assertEqual(display['color'], expected)

    def test_unknown_neutral_reverse_and_invalid_values_do_not_claim_a_comparison(self):
        for invalid in (None, True, False, 2.5, float('nan'), float('inf'), '3', -2, 21):
            self.assertIsNone(gear_value(invalid))
            self.assertEqual(gear_display(invalid, 3)['text'], '—')
            self.assertEqual(gear_display(3, invalid)['color'], 'muted')
        for value, label in ((0, 'N'), (-1, 'R')):
            self.assertEqual(gear_display(value, value)['text'], label)
            self.assertEqual(gear_display(value, value)['color'], 'muted')
            self.assertEqual(gear_display(3, value)['color'], 'muted')

    def test_reference_gear_is_held_at_position_without_fractional_or_future_shift(self):
        lap = reference()
        self.assertEqual(lap.gear_at(0), 2)
        self.assertEqual(lap.gear_at(.499999), 2)
        self.assertEqual(lap.gear_at(.5), 3)
        self.assertEqual(lap.gear_at(.749999), 3)
        self.assertEqual(lap.gear_at(.75), 4)
        self.assertEqual(lap.gear_at(1), 4)

    def test_missing_channel_interval_or_invalid_gear_never_fills_from_a_neighbor(self):
        lap = reference()
        for value in (np.nan, 2.5, -2., 21.):
            lap.grid['Gear'][1] = value
            self.assertIsNone(lap.gear_at(.3))
        lap.grid.pop('Gear')
        self.assertIsNone(lap.gear_at(.3))
        lap = reference()
        lap.times[1] = np.nan
        self.assertIsNone(lap.gear_at(.3))
        for pct in (None, -.1, 1.1, float('nan')):
            self.assertIsNone(lap.gear_at(pct))


if __name__ == '__main__':
    unittest.main()
