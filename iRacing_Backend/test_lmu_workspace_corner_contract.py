"""Native input restrictions survive adaptation to shared corner consumers."""
from dataclasses import replace
import unittest

import numpy as np

from lmu_workspace import _comparison
from test_lmu_corners import lap, change


class SharedCornerContractTests(unittest.TestCase):
    def test_stage_mismatch_cannot_reappear_as_ui_pedal_difference(self):
        reference, target = lap(), lap(number=2)
        distance = target.channels['distance_m'].values
        target = change(target, 'brake_ratio', values=np.where((distance >= 330) & (distance <= 480), .6, 0))
        target = change(target, 'throttle_ratio', values=np.where(distance >= 650, 1., 0))
        target = replace(target, support={**target.support, **{
            key: {**target.support[key], 'input_stage': 'UNFILTERED'}
            for key in ('brake_ratio', 'throttle_ratio')}})
        corner = _comparison(reference, target, {})['corners'][0]
        for role in ('target', 'reference'):
            metrics = corner[role]
            self.assertFalse(metrics['inputs_usable'])
            for key in ('brake_start', 'brake_release', 'throttle_first', 'full_throttle'):
                self.assertIsNone(metrics[key+'_m'])
                self.assertIsNone(metrics[key+'_pct'])
            for key in ('peak_brake', 'coast_time_s', 'avg_decel_g', 'braking_distance_m'):
                self.assertIsNone(metrics[key])
            self.assertEqual(metrics['min_speed_kph'], 180)
            self.assertIsNotNone(metrics['duration_s'])
        self.assertEqual(corner['delta_s'], 0)

    def test_same_stage_keeps_native_pedal_measurements(self):
        corner = _comparison(lap(), lap(number=2), {})['corners'][0]
        self.assertEqual(corner['id'], 'C1')
        self.assertEqual(corner['source_id'], 'geometry-1')
        self.assertEqual(corner['geometry_status'], 'REFERENCE_GEOMETRY_UNVERIFIED')
        for role in ('reference', 'target'):
            self.assertTrue(corner[role]['inputs_usable'])
            self.assertIsNotNone(corner[role]['brake_start_pct'])
            self.assertIsNotNone(corner[role]['full_throttle_pct'])

    def test_brake_mismatch_does_not_hide_supported_throttle_positions(self):
        target = lap(number=2)
        target = replace(target, support={**target.support, 'brake_ratio': {
            **target.support['brake_ratio'], 'input_stage': None}})
        corner = _comparison(lap(), target, {})['corners'][0]
        self.assertIsNone(corner['target']['brake_release_pct'])
        self.assertIsNotNone(corner['target']['full_throttle_pct'])

    def test_partial_start_note_is_preserved_without_duplicating_a_self_comparison(self):
        target = lap(number=2)
        target = replace(target, support={**target.support, 'distance_m': {
            **target.support['distance_m'], 'status': 'PARTIAL',
            'full_lap_coverage': False, 'leading_unmapped_s': 2.697229}})
        compared = _comparison(target, target, {})
        self.assertEqual(len(compared['observation_notes']), 1)
        self.assertIn('2.7초', compared['observation_notes'][0])
        self.assertFalse(compared['distance_support']['target']['full_lap_coverage'])
        compared = _comparison(lap(), target, {})
        self.assertEqual(len(compared['observation_notes']), 1)


if __name__ == '__main__':
    unittest.main()
