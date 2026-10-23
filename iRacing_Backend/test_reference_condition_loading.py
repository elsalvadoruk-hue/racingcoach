"""Lap condition medians keep exact values while converting only selected samples."""
from types import SimpleNamespace
import unittest

import numpy as np

from reference_conditions import _finite_channel_median


class _ObservedArray(np.ndarray):
    converted_lengths = []

    def astype(self, *args, **kwargs):
        self.converted_lengths.append(len(self))
        return super().astype(*args, **kwargs)


class ReferenceConditionLoadingTests(unittest.TestCase):
    def test_only_selected_lap_samples_are_converted(self):
        values = np.arange(10000, dtype=np.float32).view(_ObservedArray)
        values.converted_lengths.clear()
        telemetry = SimpleNamespace(channels={'Speed': values})
        lap = SimpleNamespace(start_idx=200, end_idx=300)
        self.assertEqual(_finite_channel_median(telemetry, 'Speed', lap), 250.)
        self.assertEqual(values.converted_lengths, [101])

    def test_float64_medians_and_existing_empty_window_fallback_are_exact(self):
        values = np.array([1., np.nan, np.inf, -np.inf, 2.000001, 4.000003, 8.], dtype=np.float32)
        for start, end in ((0, 6), (1, 5), (1, 3), (5, 20), (-5, 3), (5, 2), (50, 60)):
            with self.subTest(start=start, end=end):
                numeric = values.astype(float)
                lower, upper = max(0, start), min(len(numeric), end + 1)
                if upper > lower:
                    numeric = numeric[lower:upper]
                finite = numeric[np.isfinite(numeric)]
                expected = float(np.nanmedian(finite)) if len(finite) else None
                actual = _finite_channel_median(SimpleNamespace(channels={'value': values}), 'value',
                                                SimpleNamespace(start_idx=start, end_idx=end))
                self.assertEqual(actual, expected)
        self.assertIsNone(_finite_channel_median(SimpleNamespace(channels={}), 'missing'))
