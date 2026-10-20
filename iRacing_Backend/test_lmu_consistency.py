from types import SimpleNamespace
import json
import math
import unittest
from unittest.mock import patch

from lmu_consistency import corner_consistency


def comparison(target_time, *, reference_time=2., identity='fixed', extra=True):
    def row(key, a, b):
        def measure(value):
            usable = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0
            return {'metrics': {'duration_s': value, 'brake_start_m': 100+value if usable else None,
                                'full_throttle_m': 200+value if usable else None}}
        return {'id': key, 'label': '구간 '+key, 'start_m': 100., 'end_m': 300.,
                'reference': measure(a), 'target': measure(b),
                'delta_support': {metric: {'status': 'AVAILABLE'} for metric in
                                  ('brake_start_m', 'throttle_first_m', 'full_throttle_m')}}
    rows = [row('1', reference_time, target_time)]
    if extra:
        rows.append(row('2', 5., 5.))
    return {'status': 'AVAILABLE', 'definition': {'definition_id': identity}, 'corners': rows}


class ConsistencyTests(unittest.TestCase):
    def laps(self, n=3):
        return [SimpleNamespace(lap_number=i) for i in range(1, n+1)]

    def test_analytic_manual_sample_statistics_and_observation_focus(self):
        with patch('lmu_consistency.compare_corners', side_effect=[comparison(4.), comparison(10.)]):
            result = corner_consistency(self.laps())
        json.dumps(result, allow_nan=False)
        self.assertEqual(result['status'], 'AVAILABLE')
        row = result['corners'][0]
        self.assertEqual(row['sample_count'], 3)
        self.assertEqual([p['selection_index'] for p in row['passes']], [0, 1, 2])
        self.assertEqual(row['median_duration_s'], 4.)
        self.assertEqual(row['spread_s'], 8.)
        self.assertAlmostEqual(row['std_duration_s'], math.sqrt(104/9))
        self.assertEqual(result['corners'][1]['spread_s'], 0.)
        self.assertEqual(result['focus'][0]['id'], '1')
        self.assertIn('다음 주행', result['focus'][0]['next_check'])
        self.assertEqual(result['conditions'], 'NOT_COMPARED')
        self.assertEqual(result['reference_status'], 'VALIDITY_UNVERIFIED')
        self.assertIsNone(result['automatic_best_lap'])
        self.assertEqual(result['focus_basis'], 'BRAKING_AND_EXIT_INPUT_REPEATABILITY')
        self.assertEqual(row['input_consistency']['brake_start_m']['spread_m'], 8.)
        self.assertEqual(row['input_consistency']['brake_start_m']['median_m'], 104.)
        self.assertTrue(row['input_consistency']['brake_start_m']['approximate'])

    def test_input_stages_and_three_pass_coverage_are_required_for_repeatability(self):
        for status in ('INPUT_STAGE_MISMATCH', None):
            different = comparison(10.)
            different['corners'][0]['delta_support']['brake_start_m'] = {'status': status}
            with self.subTest(status=status), patch('lmu_consistency.compare_corners',
                    side_effect=[comparison(4.), different]):
                result = corner_consistency(self.laps())
            row = result['corners'][0]
            self.assertEqual(row['status'], 'AVAILABLE')
            self.assertEqual(row['input_consistency']['brake_start_m']['status'], 'INPUT_STAGE_MISMATCH')
            self.assertIsNone(row['input_consistency']['brake_start_m']['spread_m'])
            self.assertEqual(row['input_consistency']['full_throttle_m']['status'], 'AVAILABLE')
        with patch('lmu_consistency.compare_corners', side_effect=[comparison(4.), comparison(None), comparison(10.)]):
            result = corner_consistency(self.laps(4))
        self.assertEqual(result['corners'][0]['input_consistency']['brake_start_m']['sample_count'], 3)
        self.assertEqual(result['corners'][0]['input_consistency']['brake_start_m']['spread_m'], 8.)

    def test_missing_passes_never_become_zero_or_three_pass_statistics(self):
        for bad in (None, -1, 0, float('nan'), True):
            with self.subTest(bad=bad), patch('lmu_consistency.compare_corners',
                    side_effect=[comparison(4.), comparison(bad)]):
                result = corner_consistency(self.laps())
            row = result['corners'][0]
            self.assertEqual(result['status'], 'PARTIAL')
            self.assertEqual(row['sample_count'], 2)
            self.assertEqual(row['status'], 'INSUFFICIENT_PASSES')
            self.assertIsNone(row['median_duration_s'])
            self.assertIsNone(row['spread_s'])
            self.assertNotIn('1', [f['id'] for f in result['focus']])
            json.dumps(result, allow_nan=False)

    def test_insufficient_geometry_or_definition_change_has_no_statistics(self):
        for report in ({'status': 'UNAVAILABLE', 'reason': '좌표 없음'}, comparison(10., identity='other')):
            with self.subTest(report=report), patch('lmu_consistency.compare_corners',
                    side_effect=[comparison(4.), report]):
                result = corner_consistency(self.laps())
            self.assertEqual(result['status'], 'UNAVAILABLE')
            self.assertEqual(result['corners'], [])
            self.assertEqual(result['focus'], [])
            self.assertTrue(result['reason'])

    def test_reference_geometry_reused_and_selection_bounds(self):
        laps = self.laps(4)
        with patch('lmu_consistency.compare_corners', side_effect=[comparison(3.), comparison(4.), comparison(5.)]) as compare:
            result = corner_consistency(laps)
        self.assertEqual([call.args for call in compare.call_args_list], [(laps[0], lap) for lap in laps[1:]])
        self.assertEqual(result['corners'][0]['sample_count'], 4)
        for n in (0, 1, 2, 7):
            with self.subTest(n=n), self.assertRaises(ValueError):
                corner_consistency(self.laps(n))


if __name__ == '__main__':
    unittest.main()
