"""Synthetic checks for offline metrics; never open personal telemetry/stores."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

import evaluate_handling as evaluation
from measure_corner_events import isolate


class HandlingEvaluationTests(unittest.TestCase):
    def test_native_rates_keep_partial_hits_out_of_observed_denominator(self):
        rows = [
            dict(phase='rotation', observed=True, counter_observed=True,
                 events=[dict(kind='understeer')]),
            dict(phase='rotation', observed=True, counter_observed=True, events=[]),
            dict(phase='rotation', observed=False, counter_observed=True,
                 events=[dict(kind='understeer'), dict(kind='countersteer')]),
        ]
        result = evaluation.slot_rates(rows)
        self.assertEqual(result['ALL']['understeer']['observed_slots'], 2)
        self.assertEqual(result['ALL']['understeer']['observed_hits'], 1)
        self.assertEqual(result['ALL']['understeer']['partial_hits'], 1)
        self.assertEqual(result['ALL']['understeer']['all_hits'], 2)
        self.assertEqual(result['ALL']['understeer']['rate'], .5)
        self.assertEqual(result['ALL']['countersteer']['rate'], 1 / 3)
        self.assertIsNone(result['braking']['understeer']['rate'])

    def test_auc_handles_ties_missing_values_and_minimum_sample_support(self):
        self.assertEqual(evaluation.auc([3, 4, 5], [0, 1, 2]), 1.)
        self.assertEqual(evaluation.auc([1, 1, 1, np.nan], [1, 1, 1]), .5)
        self.assertEqual(evaluation.auc([0, 1, 2], [3, 4, 5]), 0.)
        self.assertIsNone(evaluation.auc([1, 2, np.nan], [1, 2, 3]))

    def _time_rows(self, local_delay=False):
        corners = [dict(id=f'C{index}') for index in range(4)]
        rows = []
        for corner_index, corner in enumerate(corners):
            for lap in range(8):
                hit = lap >= 4
                # Exactly shared lap drift is removed; only C0 gets local delay.
                elapsed = 20. + corner_index * 10 + (lap % 2)
                if local_delay and corner_index == 0 and hit:
                    elapsed += 10.
                rows.append(dict(corner_id=corner['id'], phase='rotation', lap=lap,
                                 time_s=elapsed, v_start=25., observed=True,
                                 counter_observed=True,
                                 events=[dict(kind='understeer')] if hit and corner_index == 0 else []))
        return rows, corners

    def test_adjusted_auc_removes_shared_lap_drift(self):
        rows, corners = self._time_rows()
        result = evaluation.adjusted_auc(rows, corners)
        item = next(i for i in result['items'] if i['kind'] == 'understeer')
        self.assertEqual(item['hit'], 4)
        self.assertEqual(item['control'], 4)
        self.assertEqual(item['auc'], .5)

    def test_adjusted_auc_preserves_corner_specific_slowdown(self):
        rows, corners = self._time_rows(local_delay=True)
        item = evaluation.adjusted_auc(rows, corners)['items'][0]
        self.assertEqual(item['auc'], 1.)

    def test_point_summary_reports_spread_outside_and_signed_motion_bias(self):
        values = [('rotation', [np.log(1.1), np.log(1.2), True, True, -.1]),
                  ('rotation', [np.log(1.1), np.log(.8), True, False, .1]),
                  ('braking', [0., 0., False, False, 0.])]
        result = evaluation.point_summary(values)
        self.assertAlmostEqual(result['rotation']['scatter_pct'], 10.)
        self.assertAlmostEqual(result['rotation']['outside_pct'], 100.)
        self.assertAlmostEqual(result['rotation']['effective_outside_pct'], 50.)
        self.assertAlmostEqual(result['rotation']['ratio_unwinding_pct'], 20.)
        self.assertAlmostEqual(result['rotation']['ratio_turning_in_pct'], -20.)
        self.assertEqual(result['drive']['points'], 0)
        json.dumps(result, allow_nan=False)

    def test_fingerprint_reads_without_changing_source(self):
        with tempfile.TemporaryDirectory(prefix='handling-evaluation-test-') as folder:
            source = Path(folder) / 'synthetic.ibt'
            source.write_bytes(b'synthetic-recording-only')
            before = source.stat()
            result = evaluation.fingerprint(source)
            after = source.stat()
            self.assertEqual(result['sha256'], hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual(result['bytes'], len(b'synthetic-recording-only'))
            self.assertEqual((before.st_size, before.st_mtime_ns), (after.st_size, after.st_mtime_ns))

    def test_output_path_cannot_overwrite_input(self):
        with tempfile.TemporaryDirectory(prefix='handling-output-test-') as folder:
            source = Path(folder) / 'record.json'
            source.write_text('protected synthetic source', encoding='utf-8')
            with self.assertRaises(ValueError):
                evaluation.output_paths(source.with_suffix('.txt'), [source])
            self.assertEqual(source.read_text(encoding='utf-8'), 'protected synthetic source')

    def test_isolation_overrides_inherited_personal_store_locations(self):
        with tempfile.TemporaryDirectory(prefix='handling-store-test-') as folder:
            with patch.dict(os.environ, {'IRACING_DATA_DIR': 'existing-private-data',
                                         'IRACING_TUNER_DB_PATH': 'existing-private.sqlite3',
                                         'IRACING_AI_ENABLED': '1', 'GARAGE61_AUTO_COLLECT': '1'}):
                isolate(Path(folder))
                self.assertTrue(Path(os.environ['IRACING_TUNER_DB_PATH']).is_relative_to(folder))
                self.assertEqual(Path(os.environ['IRACING_DATA_DIR']).resolve(), Path(folder).resolve())
                self.assertEqual(os.environ['IRACING_AI_ENABLED'], '0')
                self.assertEqual(os.environ['GARAGE61_AUTO_COLLECT'], '0')

    def test_import_does_not_initialize_application(self):
        # Fresh process proves this even when the larger test suite imported the app.
        code = ('import sys; import evaluate_handling; '
                'assert "backend_api" not in sys.modules; '
                'assert "app_settings" not in sys.modules')
        result = subprocess.run([sys.executable, '-c', code], cwd=Path(__file__).parent,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_cli_fixed_seed_and_two_hundred_repetitions_are_defaults(self):
        args = evaluation.parser().parse_args([])
        self.assertEqual(args.permutations, 200)
        self.assertEqual(args.seed, 20261023)
        self.assertEqual(args.method, 'v1')

    def test_v1_is_fixed_git_blob_after_product_changes(self):
        with patch.object(sys, 'path', [str(evaluation.ROOT / 'iRacing_Backend'), *sys.path]):
            module, provenance = evaluation.load_method('v1')
        self.assertEqual(module.METHOD, 'personal_corner_phase_v1')
        self.assertEqual(provenance['commit'], evaluation.BASELINE_COMMIT)
        self.assertEqual(provenance['blob'], evaluation.BASELINE_BLOB)

    def _null_fixture(self):
        with patch.object(sys, 'path', [str(evaluation.ROOT / 'iRacing_Backend'), *sys.path]):
            from corner_events import RawLap
            module, _ = evaluation.load_method('v1')
        time = np.arange(501) / 100
        raws = []
        for _ in range(8):
            speed = np.full(len(time), 25.)
            steer = np.full(len(time), .1)
            yaw = (.05 + .025 * np.sin(time * 3)) * speed * steer
            raws.append(RawLap(time.copy(), time / 10,
                              dict(Speed=speed, SteeringWheelAngle=steer, YawRate=yaw,
                                   LatAccel=np.full(len(time), 5.), Brake=np.zeros(len(time)),
                                   Throttle=np.full(len(time), .3)), .01, 3000.))
        model = module.learn_bands(raws)
        phases = [module.prepare_phase(raw, .05, .45, model) for raw in raws]
        captures = [(phase, phases, model) for phase in phases]
        rows = [dict(corner_id='C1', phase='rotation', **module.classify_phase(phase, phases, model))
                for phase in phases]
        return module, captures, rows

    def test_position_null_is_seeded_and_breaks_equal_lap_alignment(self):
        module, captures, rows = self._null_fixture()
        first = evaluation.null_evaluation(module, captures, rows, permutations=200, seed=42)
        same = evaluation.null_evaluation(module, captures, rows, permutations=200, seed=42)
        other = evaluation.null_evaluation(module, captures, rows, permutations=200, seed=43)
        self.assertEqual(first, same)
        self.assertNotEqual(first['permutation_counts'], other['permutation_counts'])
        self.assertEqual(first['actual']['ALL']['understeer']['all_hits'], 0)
        self.assertGreater(sum(draw['ALL']['understeer']['all_hits'] for draw in first['null_rates']), 0)
        counts = np.array(first['permutation_counts'])
        self.assertTrue(np.all(counts[:, :, 4:6] <= counts[:, :, 3:4]))
        self.assertTrue(np.all(counts[:, 2, 5] == 0))
        summary = evaluation.summarize_nulls([first])
        self.assertEqual(summary['rotation']['understeer']['null_rate']['n'], 200)
        json.dumps(first, allow_nan=False)

    def test_relative_grid_does_not_bridge_a_duplicate_timestamp(self):
        module, captures, _ = self._null_fixture()
        phase = captures[0][0]
        local = phase['local']
        local.time_s[100] = local.time_s[99]
        grid = evaluation._phase_grid(module, [phase], captures[0][2], 20.)
        self.assertTrue(np.any(grid[2] == 0))
        self.assertTrue(np.all(grid[2] >= 0))

    def test_cross_file_replication_uses_context_apex_and_fixed_corner_shuffle(self):
        def file(name, offset=0, context='same'):
            corners = [dict(id=f'C{i}', apex_pct=.1 + .15 * i + offset) for i in range(4)]
            rows = [dict(corner_id=corner['id'], phase='rotation',
                         events=[dict(kind='understeer')] if i == 0 else [])
                    for i, corner in enumerate(corners)]
            return dict(file=name, context=context, corners=corners, observations=rows)
        files = [file('A.ibt'), file('B.ibt', .005), file('C.ibt', context='different')]
        first = evaluation.repeated_files(files, seed=123)
        self.assertEqual(first, evaluation.repeated_files(files, seed=123))
        self.assertEqual(len(first['pairs']), 1)
        pair = first['pairs'][0]
        self.assertEqual(pair['matched_corners'], 4)
        self.assertEqual(pair['actual']['understeer']['rate'], 1.)
        self.assertLess(pair['shuffled_corners']['understeer']['mean'], 1.)
        self.assertEqual(pair['shuffled_corners']['understeer']['n'], 200)

    def test_non_recording_input_is_rejected_before_hash_or_app_import(self):
        with tempfile.TemporaryDirectory(prefix='handling-invalid-input-') as folder:
            source = Path(folder) / 'synthetic-not-recording.txt'
            source.write_text('synthetic data', encoding='utf-8')
            with patch.object(evaluation, 'fingerprint') as fingerprint:
                with self.assertRaisesRegex(ValueError, 'IBT'):
                    evaluation.main([str(source)])
                fingerprint.assert_not_called()


if __name__ == '__main__':
    unittest.main()
