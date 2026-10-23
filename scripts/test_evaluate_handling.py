"""Synthetic checks for offline metrics; never open personal telemetry/stores."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from types import SimpleNamespace
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

    def test_current_method_is_v3_with_fixed_coverage_share(self):
        with patch.object(sys, 'path', [str(evaluation.ROOT / 'iRacing_Backend'), *sys.path]):
            module, provenance = evaluation.load_method('v3')
            with self.assertRaisesRegex(ValueError, 'v2'):
                evaluation.load_method('v2')
        self.assertEqual(provenance['method'], 'personal_corner_phase_v3')
        self.assertEqual(module.COVERAGE_MIN, .9)

    def _v3_phases(self, mismatch_samples):
        with patch.object(sys, 'path', [str(evaluation.ROOT / 'iRacing_Backend'), *sys.path]):
            from corner_events import RawLap
            module, _ = evaluation.load_method('v3')
        time = np.arange(501) / 100
        raws = []
        for index in range(8):
            speed = np.full(len(time), 25.)
            steer = np.full(len(time), .1)
            yaw = .05 * speed * steer
            throttle = np.full(len(time), .3)
            if index == 0:
                throttle[100:100 + mismatch_samples] = .9
            raws.append(RawLap(time.copy(), time / 10,
                              dict(Speed=speed, SteeringWheelAngle=steer, YawRate=yaw,
                                   LatAccel=np.full(len(time), 5.), Brake=np.zeros(len(time)),
                                   Throttle=throttle), .01, 3000.))
        model = module.learn_bands(raws)
        phases = [module.prepare_phase(raw, .05, .45, model) for raw in raws]
        return module, model, phases

    def test_grid_null_uses_the_same_compared_time_share_as_the_product(self):
        for samples, expected in ((20, True), (80, False)):
            with self.subTest(samples=samples):
                module, model, phases = self._v3_phases(samples)
                native = module.classify_phase(phases[0], phases, model)
                self.assertEqual(native['observed'], expected)
                self.assertLess(native['known_fraction'], 1.)
                grid = evaluation._phase_grid(module, phases, model, 20.)
                _, observed = evaluation._classify(module, grid, grid[1], model)
                self.assertEqual(bool(observed[0][0]), expected)
                self.assertTrue(observed[2][0])

    def test_split_half_judges_each_half_only_with_its_own_laps(self):
        seen = []

        class Fake:
            @staticmethod
            def analyze_handling(analyzer, corners, include_observations=False):
                laps = [lap.lap_number for lap in analyzer.valid_laps]
                seen.append(laps)
                rows = [dict(corner_id='C1', phase='rotation', observed=True, counter_observed=True,
                             events=[dict(kind='understeer')] if lap % 3 == 0 else []) for lap in laps]
                return dict(observations=rows)

        class Analyzer:
            valid_laps = [type('Lap', (), dict(lap_number=i))() for i in range(1, 10)]

        result = evaluation.split_half_cells(Fake, Analyzer(), [])
        self.assertEqual(result['status'], 'evaluated')
        self.assertEqual(seen, [[1, 2, 3, 4], [5, 6, 7, 8, 9], [1, 3, 5, 7, 9], [2, 4, 6, 8]])
        first, second = result['splits']['first_second']
        self.assertIn(['C1', 'rotation', 'understeer', 4, 1, 1], first['cells'])
        self.assertIn(['C1', 'rotation', 'understeer', 5, 2, 2], second['cells'])  # laps 6 and 9
        Analyzer.valid_laps = Analyzer.valid_laps[:7]
        self.assertEqual(evaluation.split_half_cells(Fake, Analyzer(), [])['status'], 'too_few_laps')

    def test_split_half_scores_identical_halves_above_corner_shuffle(self):
        cells = []
        for i in range(6):
            hits = 2 if i < 2 else 0
            cells.append([f'C{i}', 'rotation', 'understeer', 10, hits, hits])
            cells.append([f'C{i}', 'rotation', 'oversteer', 10, 0, 0])
        half = dict(laps=[], cells=cells)
        files = [dict(file='A.ibt', split_half=dict(status='evaluated',
                                                    splits=dict(first_second=[half, half], odd_even=[half, half])))]
        first = evaluation.split_half_summary(files, permutations=200, seed=5)
        self.assertEqual(first, evaluation.split_half_summary(files, permutations=200, seed=5))
        score = first['first_second']['observed']['understeer']
        self.assertEqual((score['shared'], score['union'], score['rate']), (2, 2, 1.))
        self.assertLess(score['shuffled_corners']['mean'], 1.)
        correlation = first['first_second']['rate_correlation']['understeer']
        self.assertAlmostEqual(correlation['correlation'], 1.)
        self.assertEqual(correlation['cells'], 6)
        self.assertLess(correlation['shuffled_corners']['mean'], 1.)
        json.dumps(first, allow_nan=False)

    def test_non_recording_input_is_rejected_before_hash_or_app_import(self):
        with tempfile.TemporaryDirectory(prefix='handling-invalid-input-') as folder:
            source = Path(folder) / 'synthetic-not-recording.txt'
            source.write_text('synthetic data', encoding='utf-8')
            with patch.object(evaluation, 'fingerprint') as fingerprint:
                with self.assertRaisesRegex(ValueError, 'IBT'):
                    evaluation.main([str(source)])
                fingerprint.assert_not_called()

    def test_time_link_replication_is_directional_seeded_and_reports_missing_support(self):
        cells = [[f'C{i}', 'rotation', 'understeer', 10, 4, 4] for i in range(8)]
        links = [[f'C{i}', 'rotation', 'understeer', 'slower' if i < 2 else 'unclear'] for i in range(8)]
        half = dict(cells=cells, time_links=links)
        files = [dict(split_half=dict(splits=dict(first_second=[half, half])))]
        result = evaluation.time_link_replication(files, 'first_second', seed=42)
        self.assertEqual(result, evaluation.time_link_replication(files, 'first_second', seed=42))
        self.assertEqual((result['first_slower'], result['second_slower'], result['shared']), (2, 2, 2))
        self.assertEqual(result['rate'], 1.)
        self.assertLess(result['shuffled_corners']['mean'], 1.)
        files[0]['split_half']['splits']['first_second'][1] = dict(cells=cells, time_links=[])
        missing = evaluation.time_link_replication(files, 'first_second')
        self.assertEqual(missing['rate'], 0.)
        self.assertEqual(missing['second_testable'], 0)
        self.assertIsNone(evaluation.time_link_replication([], 'first_second')['rate'])

    def _matching_fixture(self, lateral=5.):
        module, model, phases = self._v3_phases(0)
        phase = phases[0]
        model['edges_mps'] = np.array([20., 25.1, 40., 50.])
        phase['bands'] = np.searchsorted(model['edges_mps'], phase['local'].channels['Speed'], side='right')
        phase['local'].channels['LatAccel'][:] = lateral
        phase['q_ready'][100:110] = False
        values = [.05, 25., .1, 0., .3, lateral]
        references = [dict(values=values.copy()) for _ in range(3)]

        def reference_at(reference, pct):
            return np.repeat(np.array(reference['values'])[:, None], len(pct), axis=1)

        return module, model, phase, references, reference_at

    def test_matching_diagnosis_counts_each_condition_independently(self):
        variants = [(1, 25.2), (1, 23.8), (3, .4), (4, .7), (2, -.1), (5, -.2), (5, 7.)]
        for key, (column, value) in zip(evaluation.MATCH_CONDITIONS, variants):
            with self.subTest(condition=key):
                module, model, phase, refs, interpolate = self._matching_fixture(
                    lateral=.2 if key == 'lateral_direction' else 5.)
                refs[2]['values'][column] = value
                with patch.object(module, '_reference_at', side_effect=interpolate):
                    result = evaluation.matching_diagnosis(module, phase, [phase, None, *refs], model)
                n = int(np.sum(phase['inside'] & phase['q_ready'] & (phase['durations'] > 0)))
                self.assertGreater(n, 0)
                self.assertEqual(result['samples'], n)
                self.assertEqual(result['available_pairs'], 3 * n)
                self.assertEqual(result['matched_pairs'], 2 * n)
                self.assertEqual(result['below_three_samples'], n)
                for condition, counts in result['conditions'].items():
                    self.assertEqual(counts['rejected_pairs'], n if condition == key else 0)
                    self.assertEqual(counts['rescued_samples'], n if condition == key else 0)

    def test_matching_diagnosis_overlaps_do_not_claim_single_condition_rescue(self):
        module, model, phase, refs, interpolate = self._matching_fixture()
        refs[2]['values'][1] = 28.  # Fails both quintile and 4%; neither alone restores 3 laps.
        with patch.object(module, '_reference_at', side_effect=interpolate):
            result = evaluation.matching_diagnosis(module, phase, refs, model)
        n = result['samples']
        self.assertEqual(result['conditions']['speed_quintile']['rejected_pairs'], n)
        self.assertEqual(result['conditions']['speed_relative']['rejected_pairs'], n)
        self.assertTrue(all(value['rescued_samples'] == 0 for value in result['conditions'].values()))

    def test_matching_diagnosis_keeps_missing_evidence_separate_and_merges_counts(self):
        module, model, phase, refs, interpolate = self._matching_fixture()
        refs[2]['values'] = [np.nan] * 6
        with patch.object(module, '_reference_at', side_effect=interpolate):
            missing = evaluation.matching_diagnosis(module, phase, refs, model)
        self.assertEqual(missing['available_below_three_samples'], missing['samples'])
        self.assertTrue(all(value == dict(rejected_pairs=0, rescued_samples=0)
                            for value in missing['conditions'].values()))
        empty = evaluation.matching_diagnosis(module, None, [], model)
        self.assertEqual(empty['unavailable_phases'], 1)
        model['edges_mps'][:] = np.nan
        unknown = evaluation.matching_diagnosis(module, phase, refs, model)
        self.assertEqual(unknown['speed_model_unavailable_samples'], unknown['samples'])
        merged = evaluation.merge_matching_counts([missing, missing, empty])
        self.assertEqual(merged['samples'], 2 * missing['samples'])
        self.assertEqual(merged['available_pairs'], 2 * missing['available_pairs'])
        self.assertEqual(merged['unavailable_phases'], 1)
        json.dumps(merged, allow_nan=False)

    def test_matching_diagnosis_leaves_full_product_analysis_identical(self):
        module, _, phases = self._v3_phases(0)
        raws = [phase['raw'] for phase in phases]
        analyzer = SimpleNamespace(
            telemetry=SimpleNamespace(metadata=dict(game='iracing'), signature=('synthetic',)),
            channels=raws[0].channels,
            valid_laps=[SimpleNamespace(lap_number=i) for i in range(len(raws))],
            _lap_grid=lambda lap: dict(raw=raws[lap.lap_number], distance_pct=raws[lap.lap_number].pct),
            _raw_lap_for=lambda grid: grid['raw'],
            _corner_metrics=lambda *args: {},
            _corner_phases=lambda *args: dict(braking=dict(start_pct=.05, end_pct=.05),
                rotation=dict(start_pct=.05, end_pct=.45), drive=dict(start_pct=.45, end_pct=.45)),
        )
        corners = [dict(id='C1', label='합성', entry_pct=.05, apex_pct=.25, exit_pct=.45)]
        before = module.analyze_handling(analyzer, corners, include_observations=True)
        native, captures, points = evaluation.native_evaluation(module, analyzer, corners, collect_points=False)
        diagnosis = evaluation.matching_by_phase(module, captures, native['observations'])
        after = module.analyze_handling(analyzer, corners, include_observations=True)
        self.assertEqual(before, native)
        self.assertEqual(before, after)
        self.assertEqual(points, [])
        self.assertGreater(diagnosis['rotation']['samples'], 0)
        self.assertEqual(diagnosis['ALL'], diagnosis['rotation'])

    def test_matching_diagnosis_cli_requires_v3_before_opening_recordings(self):
        with patch.object(evaluation, 'fingerprint') as fingerprint:
            with self.assertRaisesRegex(ValueError, 'v3'):
                evaluation.main(['--diagnose-matching'])
            fingerprint.assert_not_called()
        self.assertTrue(evaluation.parser().parse_args(['--method', 'v3', '--diagnose-matching']).diagnose_matching)

    def test_braking_diagnosis_splits_native_points_and_measures_fixed_reference_shift(self):
        module, model, phases = self._v3_phases(0)
        for index, phase in enumerate(phases):
            phase['q'] = .02 * (1 + phase['local'].pct)
            phase['raw'].channels['LongAccel'] = np.full(len(phase['raw'].time_s), -2. - index)
        before = module.classify_phase(phases[0], phases, model)
        data = evaluation.braking_diagnosis(module, [(phases[0], phases, model)], [dict(phase='braking')])
        result = evaluation.braking_summary(data)
        self.assertEqual(before, module.classify_phase(phases[0], phases, model))
        self.assertTrue(all(value['points'] > 0 for value in result['thirds'].values()))
        self.assertTrue(all(value['outside_pct'] == 0 for value in result['thirds'].values()))
        for distance, value in result['shifts'].items():
            self.assertGreater(value['n'], 0)
            self.assertLess(value['median'] * float(distance), 0)
            self.assertGreater(value['p90_abs'], 0)
        for value in result['long_differences'].values():
            self.assertEqual(value['median'], 4.)
            self.assertEqual(value['p90'], 7.)
        merged = evaluation.braking_summary(evaluation.merge_braking_data([data, data]))
        self.assertEqual(merged['long_differences']['brake']['n'], result['long_differences']['brake']['n'] * 2)
        self.assertEqual(merged['long_differences']['brake']['median'], 4.)
        json.dumps(result, allow_nan=False)

    def test_optional_long_accel_interpolation_never_bridges_missing_or_duplicate_time(self):
        module, model, phases = self._v3_phases(0)
        raw = phases[0]['raw']
        raw.channels['LongAccel'] = -np.ones(len(raw.time_s))
        raw.channels['LongAccel'][210] = np.nan
        raw.time_s[200] = raw.time_s[199]
        reference = module.prepare_phase(raw, .05, .45, model)
        query = np.array([raw.pct[190], (raw.pct[199] + raw.pct[200]) / 2, raw.pct[210]])
        result = evaluation.reference_channel_at(reference, query, 'LongAccel')
        self.assertEqual(result[0], -1.)
        self.assertTrue(np.all(np.isnan(result[1:])))
        del raw.channels['LongAccel']
        self.assertTrue(np.all(np.isnan(evaluation.reference_channel_at(reference, query, 'LongAccel'))))

    def test_braking_shortage_gate_separates_phase_definition_from_matching_loss(self):
        module, model, phases = self._v3_phases(0)
        raws = [phase['raw'] for phase in phases]
        groups = [[module.prepare_phase(raw, low, high, model) for raw in raws]
                  for low, high in ((.05, .1), (.1, .2), (.2, .45))]
        captures = [(group[0], group, model) for group in groups]
        rows = [dict(phase=name) for name in evaluation.PHASES]
        initial = evaluation.braking_summary(evaluation.braking_diagnosis(module, captures, rows))
        for group in groups:
            for phase in group[1:]:
                phase['local'].channels['Throttle'][:] = .9
        restricted = evaluation.braking_summary(evaluation.braking_diagnosis(module, captures, rows))
        self.assertTrue(initial['stop_before_candidate'])
        self.assertIn('drive', initial['less_than_half_of'])
        self.assertIn('drive', initial['definition_also_short'])
        for phase in evaluation.PHASES:
            self.assertEqual(initial['phase_counts'][phase]['required'], restricted['phase_counts'][phase]['required'])
            self.assertEqual(restricted['phase_counts'][phase]['three_references'], 0)
        self.assertEqual(initial['definition_also_short'], restricted['definition_also_short'])

    def test_braking_diagnosis_cli_rejects_incompatible_modes_before_opening_inputs(self):
        with patch.object(evaluation, 'fingerprint') as fingerprint:
            for args in (['--diagnose-braking'], ['--method', 'v3', '--diagnose-braking', '--resume'],
                         ['--method', 'v3', '--diagnose-braking', '--diagnose-matching']):
                with self.subTest(args=args), self.assertRaises(ValueError):
                    evaluation.main(args)
            fingerprint.assert_not_called()

    def test_population_time_and_exclusions_partition_without_changing_required_mask(self):
        module, model, phases = self._v3_phases(0)
        raw = phases[0]['raw']
        raw.channels['Speed'][100:150] = module.MIN_SPEED
        raw.channels['SteeringWheelAngle'][200:300] = module.MIN_STEER
        raw.channels['YawRate'][350] = np.nan
        aligned = raw.channels['SteeringWheelAngle'].copy()
        aligned[380:400] = 0.
        model['_aligned_steer'] = {id(raw): aligned}
        target = module.prepare_phase(raw, .05, .45, model)
        before = module.classify_phase(target, [target, *phases[1:]], model)
        data = evaluation.population_diagnosis(module, [(target, phases, model)], [dict(phase='braking')])
        result = evaluation.population_summary(data)['braking']
        self.assertEqual(before, module.classify_phase(target, [target, *phases[1:]], model))
        self.assertEqual(result['phase_samples'], 400)
        self.assertAlmostEqual(result['phase_seconds'], 4.)
        self.assertEqual(result['required_samples'], 228)
        self.assertAlmostEqual(result['required_seconds'], 2.28)
        self.assertAlmostEqual(result['required_time_fraction'], .57)
        for key, count in [('missing_required_interval', 2), ('speed_below_minimum', 50),
                           ('steer_below_minimum', 100), ('aligned_steer_below_minimum', 20)]:
            self.assertEqual(result['reasons'][key]['samples'], count)
        self.assertAlmostEqual(result['low_steer_independent']['phase_time_fraction'], .25)
        excluded = sum(v['seconds'] for v in result['reasons'].values())
        self.assertAlmostEqual(excluded + result['required_seconds'], result['phase_seconds'])
        merged = evaluation.population_summary(evaluation.merge_population([data, data]))
        self.assertAlmostEqual(merged['braking']['phase_seconds'], 8.)
        self.assertAlmostEqual(merged['braking']['required_time_fraction'], .57)
        json.dumps(merged, allow_nan=False)

    def test_population_accounts_boundary_time_and_unavailable_phases(self):
        module, model, phases = self._v3_phases(0)
        phase = module.prepare_phase(phases[0]['raw'], .0555, .4505, model)
        data = evaluation.population_diagnosis(module, [(phase, [], model), (None, [], model)],
                                              [dict(phase='braking'), dict(phase='drive')])
        result = evaluation.population_summary(data)
        self.assertAlmostEqual(result['braking']['phase_seconds'], 3.95)
        self.assertAlmostEqual(result['braking']['reasons']['boundary_interval']['seconds'], .005)
        self.assertEqual(result['braking']['reasons']['boundary_interval']['samples'], 0)
        self.assertEqual(result['drive']['unavailable_instances'], 1)
        self.assertIsNone(result['drive']['required_time_fraction'])
        self.assertEqual(module.METHOD, 'personal_corner_phase_v3')

    def test_v4_cli_and_population_flag_keep_diagnosis_gates_explicit(self):
        args = evaluation.parser().parse_args(['--method', 'v4', '--include-population-diagnosis'])
        self.assertEqual(args.method, 'v4')
        self.assertTrue(args.include_population_diagnosis)
        with patch.object(evaluation, 'fingerprint') as fingerprint:
            with self.assertRaisesRegex(ValueError, 'v3 또는 v4'):
                evaluation.main(['--include-population-diagnosis'])
            fingerprint.assert_not_called()

    def test_population_includes_undefined_phases_that_are_not_in_classify_captures(self):
        module, _, _ = self._v3_phases(0)
        native = dict(phase_coverage=[dict(phase='braking', unknown_reasons={'phase_unavailable': 3}),
                                      dict(phase='braking', unknown_reasons={'raw_lap_unavailable': 2}),
                                      dict(phase='drive', unknown_reasons={'inputs_not_comparable': 8})])
        result = evaluation.population_diagnosis(module, [], [], native=native)
        self.assertEqual(result['braking']['unavailable_instances'], 5)
        self.assertEqual(result['drive']['unavailable_instances'], 0)
        self.assertEqual(result['braking']['phase_seconds'], 0.)


if __name__ == '__main__':
    unittest.main()
