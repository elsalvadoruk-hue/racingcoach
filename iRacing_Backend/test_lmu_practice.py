from copy import deepcopy
from dataclasses import replace
import json
import unittest
from unittest.mock import patch

import numpy as np
from lmu_practice import create_practice_baseline, compare_practice
from test_lmu_corners import lap, change


def sources(numbers=(1, 2, 3), salt=0):
    return [dict(file_id=f'{i+salt:024x}', sha256=f'{i+salt:064x}', wal_sha256=None,
                 lap_number=n) for i, n in enumerate(numbers, 1)]


class PracticeTests(unittest.TestCase):
    def setUp(self):
        self.laps = [lap(number=n) for n in (1, 2, 3)]
        self.sources = sources()
        self.baseline = create_practice_baseline(self.laps, self.sources)

    def test_frozen_boundaries_work_without_baseline_or_current_geometry(self):
        current = [replace(l, channels={k: v for k, v in l.channels.items() if not k.endswith('_deg')}) for l in self.laps]
        with patch('lmu_corners._geometry', side_effect=AssertionError('must not redefine')):
            result = compare_practice(self.baseline, current, sources(salt=10))
        self.assertEqual(result['definition'], self.baseline['definition'])
        self.assertEqual(result['corners'][0]['spread_delta_m']['brake_start_m'], 0.)
        self.assertEqual(result['causal_effect'], 'NOT_ESTABLISHED')
        self.assertEqual(result['reference_status'], 'VALIDITY_UNVERIFIED')
        json.dumps(result, allow_nan=False)

    def test_analytic_input_spread_and_missing_passes(self):
        current = [change(l, 'brake_ratio', values=np.where((l.channels['distance_m'].values >= start)&
                   (l.channels['distance_m'].values <= 450), .5, 0)) for l, start in zip(self.laps, (300, 310, 320))]
        result = compare_practice(self.baseline, current, sources(salt=10))
        row = result['corners'][0]
        self.assertAlmostEqual(row['current']['brake_start_m']['spread_m'], 20., delta=2)
        self.assertEqual(row['spread_delta_m']['brake_start_m'], row['current']['brake_start_m']['spread_m'])
        current[0] = change(current[0], 'brake_ratio', values=np.zeros(1501))
        result = compare_practice(self.baseline, current, sources(salt=10))
        self.assertIsNone(result['corners'][0]['spread_delta_m']['brake_start_m'])
        self.assertEqual(result['corners'][0]['current']['brake_start_m']['sample_count'], 2)

    def test_reused_copied_duplicate_and_wrong_stage_laps_rejected(self):
        with self.assertRaises(ValueError):
            compare_practice(self.baseline, self.laps, self.sources)
        copied = [s | {'file_id': f'{i+100:024x}'} for i, s in enumerate(self.sources)]
        with self.assertRaises(ValueError):
            compare_practice(self.baseline, self.laps, copied)
        changed_wal = [s | {'wal_sha256': 'f'*64} for s in copied]
        with self.assertRaises(ValueError):
            compare_practice(self.baseline, self.laps, changed_wal)
        with self.assertRaises(ValueError):
            create_practice_baseline([self.laps[0]]*3, [self.sources[0]]*3)
        support = deepcopy(self.laps[0].support)
        support['brake_ratio']['input_stage'] = 'UNFILTERED'
        with self.assertRaises(ValueError):
            compare_practice(self.baseline, [replace(l, support=support) for l in self.laps], sources(salt=10))

    def test_contract_metadata_and_boundary_change_rejected(self):
        self.assertTrue({'lmu_analysis.py', 'lmu_telemetry.py'} <= set(self.baseline['measurement_contract']))
        for mutation in ('schema', 'method', 'contract', 'boundary'):
            bad = deepcopy(self.baseline)
            if mutation == 'schema': bad['schema_version'] = 99
            if mutation == 'method': bad['definition']['method'] = 'OTHER'
            if mutation == 'contract': bad['measurement_contract'] = {}
            if mutation == 'boundary': bad['definition']['boundaries'][0]['end_m'] = float('nan')
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                compare_practice(bad, self.laps, sources(salt=10))
        changed = [replace(l, metadata=l.metadata | {'car_class': 'Other'}) for l in self.laps]
        with self.assertRaises(ValueError):
            compare_practice(self.baseline, changed, sources(salt=10))
        bad = deepcopy(self.baseline)
        bad['definition']['boundaries'][0]['start_m'] += 1
        with self.assertRaisesRegex(ValueError, '식별자'):
            compare_practice(bad, self.laps, sources(salt=10))

    def test_baseline_snapshot_detaches_references(self):
        self.sources[0]['file_id'] = 'bad'
        self.assertNotEqual(self.baseline['sources'][0]['file_id'], 'bad')


if __name__ == '__main__':
    unittest.main()
