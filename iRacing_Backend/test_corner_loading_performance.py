"""Repetition keeps all evidence without computing discarded corner details."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from analysis_corners import CornerAnalysis
from analysis_models import SessionMode


class _Subject(CornerAnalysis):
    track_length_m = 1000.

    def __init__(self, *, gap=None):
        self.valid_laps = [SimpleNamespace(lap_number=i, lap_time_s=100. + i * .3)
                           for i in range(1, 7)]
        self.definitions = [{'entry': 10, 'apex': 20, 'exit': 30, 'id': 'C1'},
                            {'entry': 50, 'apex': 60, 'exit': 70, 'id': 'C2'}]
        self.grids = {}
        self.detail_calls = 0
        for lap in self.valid_laps:
            axis = np.linspace(0, 1, 101)
            self.grids[lap.lap_number] = {
                'distance_pct': axis, 'time_s': axis * lap.lap_time_s,
                'speed_kph': np.full(101, 140.),
                'inputs_usable': True, 'extra_steering': lap.lap_number > 1,
            }
        if gap == 'coverage':
            self.grids[4]['_coverage'] = np.ones(101, dtype=bool)
            self.grids[4]['_coverage'][20] = False
        elif gap == 'analysis':
            self.grids[4]['_analysis_coverage'] = np.ones(101, dtype=bool)
            self.grids[4]['_analysis_coverage'][60] = False
        elif gap == 'input':
            self.grids[4]['inputs_usable'] = False
        elif gap == 'reference':
            self.grids[1]['_coverage'] = np.ones(101, dtype=bool)
            self.grids[1]['_coverage'][20] = False
        elif gap == 'fallback':
            self.grids[4]['speed_kph'][20] = np.nan

    def _lap_grid(self, lap):
        return self.grids[lap.lap_number]

    def _detect_corners(self, grid):
        return self.definitions

    def _corner_metrics(self, grid, entry, apex, exit_idx):
        return {'min_speed_kph': 140., 'inputs_usable': grid['inputs_usable'],
                'avg_abs_steer_deg': 16. if grid['extra_steering'] else 10.,
                'steering_efficiency': .8 if grid['extra_steering'] else 1.,
                'min_speed_pct': float(grid['distance_pct'][apex])}

    def _corner_phases(self, *args):
        self.detail_calls += 1
        return {'braking': None}

    def _handling_analysis(self, corners):
        return {'corners': [], 'phase_coverage': []}


class _FullRepetitionSubject(_Subject):
    """Use the previous full-detail route as an aggregation oracle."""
    def _corner_analysis(self, *args, **kwargs):
        kwargs['_details'] = True
        return super()._corner_analysis(*args, **kwargs)


class CornerLoadingPerformanceTests(unittest.TestCase):
    def test_repetition_matches_full_detail_for_modes_and_missing_observations(self):
        for gap in (None, 'coverage', 'analysis', 'input', 'reference', 'fallback'):
            for mode in SessionMode:
                with self.subTest(gap=gap, mode=mode):
                    subject, full = _Subject(gap=gap), _FullRepetitionSubject(gap=gap)
                    actual = subject._repetition_analysis(subject.definitions, mode)
                    expected = full._repetition_analysis(full.definitions, mode)
                    self.assertEqual(actual, expected)
                    self.assertTrue(actual['candidate_setup_patterns'])
                    self.assertEqual(subject.detail_calls, 0)
                    self.assertGreater(full.detail_calls, 0)

    def test_repetition_does_not_compute_discarded_details_or_modify_grids(self):
        subject = _Subject()
        before = deepcopy(subject.grids)
        with (patch('analysis_corners.racing_line_context', side_effect=AssertionError('GPS')),
             patch('analysis_corners.first_deviation', side_effect=AssertionError('deviation')),
             patch.object(subject, '_corner_phases', side_effect=AssertionError('phases')),
              patch.object(subject, '_handling_analysis', side_effect=AssertionError('handling'))):
            result = subject._repetition_analysis(subject.definitions)
        self.assertTrue(result['repeated_setup_patterns'])
        for number, grid in before.items():
            for key, value in grid.items():
                np.testing.assert_equal(subject.grids[number][key], value)

    def test_snapshot_retains_details_and_separate_mutable_corner_rows(self):
        subject = _Subject(gap='coverage')
        rows = subject._memory_corner_metrics()
        self.assertEqual(len(rows), len(subject.valid_laps) * len(subject.definitions))
        self.assertEqual(subject.detail_calls, len(rows))
        for row in rows:
            self.assertTrue({'phases', 'line', 'first_deviation', 'handling',
                             'handling_coverage', 'reference_lap', 'lap_number'} <= row.keys())
        rows[0]['label'] = 'Changed by caller'
        self.assertNotEqual(subject._memory_corner_metrics()[0]['label'], rows[0]['label'])


if __name__ == '__main__':
    unittest.main()
