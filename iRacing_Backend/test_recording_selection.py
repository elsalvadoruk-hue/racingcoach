"""Selection excludes known empty results while preserving unknown metadata."""
import unittest

from recording_selection import is_selectable_recording


class RecordingSelectionTests(unittest.TestCase):
    def test_confirmed_zero_counts_are_not_candidates_even_for_clean_segment_coaching(self):
        for field in ('detected_laps', 'lap_count', 'complete_laps', 'valid_laps'):
            with self.subTest(field=field):
                self.assertFalse(is_selectable_recording({field: 0, 'analyzable': True, 'status': 'COACHING_ONLY'}))

    def test_unknown_counts_stay_candidates_without_numeric_coercion(self):
        for value in (None, '', '0', False, float('nan'), float('inf')):
            with self.subTest(value=value):
                self.assertTrue(is_selectable_recording(dict.fromkeys(
                    ('detected_laps', 'lap_count', 'complete_laps', 'valid_laps'), value)))
        self.assertTrue(is_selectable_recording({}))
        self.assertTrue(is_selectable_recording({'analyzable': None, 'metadata_pending': True}))

    def test_lmu_measurements_do_not_require_an_official_validity_verdict(self):
        self.assertTrue(is_selectable_recording({'measurement_laps': 2, 'valid_laps': None, 'analyzable': True}))
        self.assertTrue(is_selectable_recording({'measurement_laps': 2, 'valid_laps': 0, 'analyzable': True}))
        self.assertFalse(is_selectable_recording({'measurement_laps': 0, 'valid_laps': None}))
        self.assertFalse(is_selectable_recording({'measurement_laps': 2, 'complete_laps': 0}))

    def test_existing_unavailable_or_explicitly_unselectable_results_stay_blocked(self):
        self.assertFalse(is_selectable_recording({'analyzable': False, 'valid_laps': None}))
        self.assertFalse(is_selectable_recording({'selectable': False, 'valid_laps': 2}))


if __name__ == '__main__':
    unittest.main()
