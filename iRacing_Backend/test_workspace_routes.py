"""Production desktop API contracts, using synthetic recordings and temporary stores."""
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import numpy as np
from fastapi.testclient import TestClient

# This shared fixture sets temporary persistence paths before backend import.
from test_rolling_best import BackToBackLaps, api
from test_practice import FOCUS, run
from practice import PracticeStore
from workspace_setup import verify_planned_change
import test_partial_coaching as partial_fixtures


class WorkspaceRouteTests(BackToBackLaps, unittest.TestCase):
    def test_external_analysis_adds_notice_without_changing_existing_calculations(self):
        from test_setup_influence import fixture
        data = fixture()
        for grid in (data['target_grid'], data['reference_grid']):
            x = grid['distance_pct']
            grid['Speed'] = grid['speed_kph'] / 3.6
            for corner in data['corners']:
                grid['Throttle'][(x >= corner['entry_pct']) & (x <= corner['exit_pct'])] = .5
        definitions = [{'entry': round(c['entry_pct'] * 2000), 'apex': round((c['entry_pct'] + c['exit_pct']) * 1000),
                        'exit': round(c['exit_pct'] * 2000)} for c in data['corners']]
        source = self.sources['baseline.ibt']
        analyzer = api.TelemetryAnalyzer(source)
        analyzer.grid_points = 2001
        analyzer.track_length_m = 6000
        target = replace(source.laps[-1], lap_time_s=100.2)
        reference = SimpleNamespace(path=Path('reference.csv'), metadata={'lap_time_s': 100., 'driver_name': 'Synthetic'},
                                    distance_pct=data['reference_grid']['distance_pct'],
                                    grid=lambda *args: data['reference_grid'])
        with patch.object(api, 'resolve_external_reference_file', return_value=reference.path), \
             patch.object(api.external_reference_repository, 'load', return_value=reference), \
             patch.object(api, 'external_reference_compatible', return_value=True), \
             patch.object(analyzer, '_lap_grid', return_value=data['target_grid']), \
             patch.object(analyzer, '_detect_corners', return_value=definitions):
            with patch.object(api, 'setup_influence_notice', return_value=None):
                before = analyzer._external_reference_analysis(target, ['reference.csv'])
            after = analyzer._external_reference_analysis(target, ['reference.csv'])
        notice = after['performance_diagnostics'].pop('setup_influence')
        before['performance_diagnostics'].pop('setup_influence')
        self.assertEqual(notice['status'], 'POSSIBLE')
        self.assertEqual(after, before)
        self.assertFalse(after['setup_conclusion_allowed'])  # A single lap still cannot prove a setup cause.

    def setUp(self):
        super().setUp()
        source = self.telemetry([(50, 50), (45, 45), (44, 45)])
        pct = source.channels['LapDistPct']
        source.channels.update(BrakeABSactive=((pct > .2) & (pct < .23)).astype(float),
            Brake=np.where((pct > .18) & (pct < .3), .7, 0),
            Throttle=np.where((pct > .18) & (pct < .36), 0, 1),
            SteeringWheelAngle=np.sin(pct * 2 * np.pi) * .2,
            Lat=50 + np.sin(pct * 2 * np.pi) * .01, Lon=6 + np.cos(pct * 2 * np.pi) * .01)
        source.channels['Speed'] = 30 - 15 * np.exp(-((pct - .28) / .05) ** 2)
        self.sources = {}
        for name in ('baseline.ibt', 'candidate.ibt'):
            path = Path(self.work.name) / name
            path.write_bytes(name.encode('ascii'))
            self.sources[name] = replace(source, path=path, signature=api.file_signature(path))
        self.state = {'analysis_result': None, 'last_analysis_time': None, 'file_signature': None}
        self.store = PracticeStore(Path(self.work.name) / 'practice.json')
        patches = self.enterContext(ExitStack())
        patches.enter_context(patch.object(api, 'shared_sync_state', self.state))
        patches.enter_context(patch.object(api, 'memory_store', None))
        patches.enter_context(patch.object(api, 'practice_store', self.store))
        patches.enter_context(patch.object(api, 'refresh_live_practice'))
        patches.enter_context(patch.object(api, 'resolve_telemetry_file', side_effect=lambda name: self.sources[name].path if name in self.sources else None))
        patches.enter_context(patch.object(api.repository, 'load', side_effect=lambda path: self.sources[path.name]))
        patches.enter_context(patch.object(api, 'review_notes', return_value={'notes': [], 'pending': []}))
        self.client = TestClient(api.app)
        self.addCleanup(self.client.close)

    def post(self, route, body=None):
        return self.client.post('/api/workspace/' + route, json=body or {'filename': 'baseline.ibt'})

    def test_position_chart_is_read_only_and_shared_by_both_games(self):
        before = dict(self.state)
        response = {'status': 'UNAVAILABLE', 'series': [], 'classes': [], 'reason': '기록 없음'}
        with patch('race_positions.read_history', return_value=response) as history:
            for prefix in ('/api/workspace/', '/api/lmu/workspace/'):
                result = self.client.post(prefix + 'race-positions', json={'filename': 'baseline.ibt'})
                self.assertEqual(result.status_code, 200, result.text)
                self.assertEqual(result.json()['status'], 'UNAVAILABLE')
            self.assertEqual(history.call_count, 2)
        self.assertEqual(self.state, before)
        for name, code in (('../secret.ibt', 422), ('missing.ibt', 404)):
            self.assertEqual(self.post('race-positions', {'filename': name}).status_code, code)

    def test_position_chart_flushes_pending_timing_before_reading_for_both_games(self):
        order = []
        def history(_):
            order.append('read')
            return {'status': 'UNAVAILABLE', 'series': []}
        with patch.object(api.live_telemetry, 'flush_race_timing', side_effect=lambda: order.append('flush')), \
             patch('race_positions.read_history', side_effect=history):
            for prefix in ('/api/workspace/', '/api/lmu/workspace/'):
                response = self.client.post(prefix + 'race-positions', json={'filename': 'baseline.ibt'})
                self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(order, ['flush', 'read', 'flush', 'read'])

    def test_session_uses_real_selection_and_updates_practice_context_without_implicit_memory_save(self):
        response = self.post('session', {'filename': 'baseline.ibt', 'target_lap': 3, 'reference_lap': 2, 'session_mode': 'ENDURANCE'})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result['kind'], 'LIVE_LOCAL')
        self.assertEqual((result['defaultTarget'], result['defaultReference']), (3, 2))
        self.assertEqual(result['analysisResult']['session_policy']['session_mode'], 'ENDURANCE')
        self.assertFalse(result['analysisResult']['memory']['persist_requested'])
        self.assertEqual(self.state['analysis_result']['file']['name'], 'baseline.ibt')
        self.assertEqual(set(result['initialComparison']['controlEvents']), {'abs'})
        self.assertTrue(result['initialComparison']['controlEvents']['abs']['target']['available'])
        self.assertGreater(len(result['initialComparison']['controlEvents']['abs']['target']['events']), 0)
        options = result['initialComparison']['practiceSummary']['options']
        self.assertEqual(self.state['analysis_result']['practice_focuses']['WORKSPACE'], [option['focus'] for option in options])
        self.assertNotIn('analysisResult', result['initialComparison'])

    def test_a_named_job_reports_every_analysis_stage_and_ends_complete(self):
        import analysis_progress
        seen, original = [], analysis_progress.stage
        def record(key, laps=None):
            seen.append(key)
            original(key, laps)
        with patch.object(analysis_progress, 'stage', side_effect=record):
            response = self.post('session', {'filename': 'baseline.ibt', 'progress_id': 'job-1'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([key for key in dict.fromkeys(seen)], ['reference', 'laps', 'corners', 'handling', 'repetition', 'summary', 'comparison'])
        done = self.client.get('/api/workspace/progress/job-1').json()
        self.assertEqual((done['percent'], done['stage']), (100, 'done'))
        self.assertEqual(self.client.get('/api/workspace/progress/unknown').json()['percent'], None)

    def test_requests_without_a_job_report_nothing(self):
        response = self.post('session', {'filename': 'baseline.ibt'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn('progress_id', response.text)
        plain = response.json()
        tracked = self.post('session', {'filename': 'baseline.ibt', 'progress_id': 'same-result'}).json()
        plain['analysisResult']['session'].pop('analyzed_at', None)
        tracked['analysisResult']['session'].pop('analyzed_at', None)
        self.assertEqual(plain, tracked)

    def test_failed_analysis_is_not_reported_as_complete(self):
        response = self.post('session', {'filename': 'missing.ibt', 'progress_id': 'job-failed'})
        self.assertEqual(response.status_code, 404)
        status = self.client.get('/api/workspace/progress/job-failed').json()
        self.assertEqual(status['stage'], 'error')
        self.assertLess(status['percent'], 100)

    def test_progress_answers_while_the_analysis_request_is_still_running(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        entered, release = threading.Event(), threading.Event()
        def load(path):
            entered.set()
            if not release.wait(10):
                raise RuntimeError('analysis worker was not released')
            return self.sources[path.name]
        with patch.object(api.repository, 'load', side_effect=load), ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(self.post, 'session', {'filename': 'baseline.ibt', 'progress_id': 'job-running'})
            try:
                self.assertTrue(entered.wait(5))
                status = self.client.get('/api/workspace/progress/job-running').json()
                self.assertEqual(status['stage'], 'load')
                self.assertLess(status['percent'], 100)
                self.assertFalse(pending.done())
            finally:
                release.set()
            self.assertEqual(pending.result(timeout=30).status_code, 200)

    def test_explicit_self_comparison_has_zero_delta_and_does_not_silently_change_reference(self):
        response = self.post('comparison', {'filename': 'baseline.ibt', 'target_lap': 3, 'reference_lap': 3})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result['delta'], 0)
        self.assertEqual(result['reference_lap'], 3)
        deltas = [row['delta_s'] for row in result['trace'] if row.get('delta_s') is not None]
        self.assertTrue(deltas)
        self.assertTrue(all(value == 0 for value in deltas))
        self.assertEqual(result['analysisResult']['comparison']['reference_lap'], 3)
        self.assertEqual(result['practiceSummary']['options'], [])
        self.assertEqual(result['analysisResult']['comparison']['total_delta_s'], 0)

    def test_previewing_another_file_keeps_current_analysis_and_practice_untouched(self):
        self.assertEqual(self.post('session').status_code, 200)
        original = self.state['analysis_result']
        with patch.object(self.store, 'record', wraps=self.store.record) as record:
            response = self.post('session', {'filename': 'candidate.ibt', 'preview': True})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['filename'], 'candidate.ibt')
        self.assertIs(self.state['analysis_result'], original)
        record.assert_not_called()
        applied = self.post('comparison', {'filename': 'candidate.ibt'})
        self.assertEqual(applied.status_code, 200, applied.text)
        self.assertEqual(self.state['analysis_result']['file']['name'], 'candidate.ibt')

    def test_analysis_mode_feedback_and_pit_rule_survive_both_workspace_adapters(self):
        options = {'user_feedback': 'Turn-in needs observation', 'feedback_phase': 'ENTRY', 'feedback_speed': 'LOW',
                   'feedback_symptom': 'UNDERSTEER', 'setup_mode': 'EVIDENCE', 'pit_rule': 'SEQUENTIAL'}
        for route in ('session', 'comparison'):
            with self.subTest(route=route), patch.object(api, 'load_analysis', wraps=api.load_analysis) as load:
                response = self.post(route, {'filename': 'baseline.ibt', 'target_lap': 3, 'reference_lap': 2,
                                            'session_mode': 'ENDURANCE', **options})
            self.assertEqual(response.status_code, 200, response.text)
            request = load.call_args.args[0]
            self.assertIs(load.call_args.kwargs['parsed_telemetry'], self.sources['baseline.ibt'])
            for key, value in options.items():
                self.assertEqual(getattr(request, key), value)
            result = response.json()['analysisResult']
            self.assertIn(options['user_feedback'], result['user_feedback'])
            self.assertEqual(result['session_policy']['session_mode'], 'ENDURANCE')
            self.assertEqual(result['setup_recommendation']['setup_mode'], 'EVIDENCE')

    def test_invalid_analysis_options_are_rejected_by_the_canonical_model(self):
        for fields in ({'feedback_phase': 'UNKNOWN'}, {'setup_mode': 'GUESS'}, {'pit_rule': 'UNKNOWN'}, {'user_feedback': 'x' * 2001}):
            response = self.post('session', {'filename': 'baseline.ibt', **fields})
            self.assertEqual(response.status_code, 422, response.text)
        self.assertIsNone(self.state['analysis_result'])

    def test_lap_overlay_and_file_laps_are_connected_to_real_calculators(self):
        laps = self.post('file-laps')
        self.assertEqual(laps.status_code, 200, laps.text)
        self.assertEqual(len(laps.json()['laps']), 3)
        self.assertTrue(all(lap['status'] == 'VALID' and lap['complete'] for lap in laps.json()['laps']))
        overlay = self.post('overlay', {'filename': 'baseline.ibt', 'laps': [2, 3], 'base_lap': 2, 'start_pct': .1, 'end_pct': .4})
        self.assertEqual(overlay.status_code, 200, overlay.text)
        result = overlay.json()
        self.assertEqual(set(result['series']), {'2', '3'})
        self.assertGreater(len(result['distance_pct']), 100)
        self.assertGreaterEqual(min(result['distance_pct']), .1)

    def test_catalog_best_uses_valid_complete_laps_only(self):
        telemetry = self.sources['baseline.ibt']
        telemetry.laps[0].valid = False
        telemetry.laps[0].lap_time_s = 5.
        telemetry.laps[1].lap_time_s = 99.123
        telemetry.laps[2].lap_time_s = 70.
        telemetry.laps[2].complete = False
        result = api.telemetry_file_visibility(telemetry.path, telemetry)
        self.assertEqual(result['best_lap_s'], 99.123)

    def test_catalog_and_references_use_actual_services(self):
        with patch.object(api, 'list_ibt_files', return_value=[self.sources['baseline.ibt'].path]), patch.object(api, 'quick_file_visibility', return_value={
                'car_name': 'Test', 'session_type': 'Practice', 'valid_laps': 3, 'detected_laps': 3, 'analyzable': True, 'best_lap_s': 99.123}):
            response = self.post('files', {})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['files'][0]['valid_laps'], 3)
        self.assertEqual(response.json()['files'][0]['best_lap_s'], 99.123)
        with patch.object(api, 'list_external_reference_metadata', return_value={'files': [{'name': 'actual.csv', 'usable': True, 'compatible': True}]}):
            response = self.post('references')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['files'][0]['name'], 'actual.csv')

    def test_fast_catalogs_expose_selection_from_cached_counts_without_sample_reads(self):
        paths = [source.path for source in self.sources.values()]
        def visibility(path):
            if path.name == 'baseline.ibt':
                return {'valid_laps': 0, 'complete_laps': 1, 'detected_laps': 1,
                        'analyzable': True, 'status': 'COACHING_ONLY'}
            return {'valid_laps': None, 'detected_laps': None, 'metadata_pending': True}
        with patch.object(api, 'list_ibt_files', return_value=paths), \
             patch.object(api, 'quick_file_visibility', side_effect=visibility) as quick, \
             patch.object(api, 'telemetry_file_visibility', side_effect=AssertionError('deep scan')), \
             patch.object(api.repository, 'load', side_effect=AssertionError('sample read')):
            payloads = [self.post('files', {}).json(), self.client.get('/api/files').json()]
        self.assertEqual(quick.call_count, 4)
        for payload in payloads:
            self.assertEqual(payload['filter_policy'], 'EXCLUDE_CONFIRMED_EMPTY_OR_NO_USABLE_LAPS')
            self.assertEqual([row['selectable'] for row in payload['files']], [False, True])
            self.assertIsNone(payload['files'][1]['valid_laps'])
            self.assertIsNone(payload['files'][1]['complete_laps'])

    def test_preview_of_known_zero_returns_counts_without_references_or_analysis(self):
        for scenario in ('empty', 'incomplete', 'coaching-only'):
            with self.subTest(scenario=scenario):
                source = self.partial_recording()
                if scenario == 'empty':
                    source.laps.clear()
                elif scenario == 'incomplete':
                    source.laps[0].complete = False
                with patch.object(api, 'load_analysis', side_effect=AssertionError('analysis')), \
                     patch('workspace_routes.select_personal_reference', side_effect=AssertionError('reference search')), \
                     patch.object(api, 'list_external_reference_metadata', side_effect=AssertionError('catalog search')):
                    response = self.post('session', {'filename': 'baseline.ibt', 'preview': True})
                self.assertEqual(response.status_code, 200, response.text)
                result = response.json()
                self.assertFalse(result['selectable'])
                self.assertEqual(result['valid_laps'], 0)
                self.assertEqual(result['laps'], [])
                self.assertEqual(result['availability']['detected_laps'], len(source.laps))
                self.assertIsNone(self.state['analysis_result'])
                self.assertNotIn('analysisResult', result)

    def test_reference_catalog_exposes_existing_recommendations_without_private_catalog_paths(self):
        catalog = {'files': [{'name': 'first.csv', 'usable': True, 'compatible': True}],
                   'recommended_selection': ['first.csv'], 'directory': 'private-reference-directory',
                   'catalog_path': 'private-catalog-path'}
        with patch.object(api, 'list_external_reference_metadata', return_value=catalog):
            response = self.post('references')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['recommended_selection'], ['first.csv'])
        self.assertNotIn('directory', response.json())
        self.assertNotIn('catalog_path', response.json())

    def test_setup_plan_is_local_and_setup_compare_uses_real_persistence_handler(self):
        plan = self.post('setup-plan', {'filename': 'baseline.ibt', 'phase': 'EXIT', 'symptom': 'OVERSTEER'})
        self.assertEqual(plan.status_code, 200, plan.text)
        self.assertIn('hypothesis', plan.json())
        handler = AsyncMock(return_value={'comparison': {'verdict': 'INCONCLUSIVE', 'plan_matches': False}, 'memory_record': {'saved': False}})
        body = {'baseline_file': 'baseline.ibt', 'candidate_file': 'candidate.ibt',
                'planned_change': {'setting': 'rear_arb_blades', 'original_value': 3, 'value': 2}, 'trial_id': 'trial-test'}
        with patch.object(api, 'compare_setup_experiments', handler):
            response = self.post('setup-compare', body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['memory_record'], {'saved': False})
        self.assertEqual(handler.await_args.args[0].planned_change, body['planned_change'])
        self.assertEqual(handler.await_args.args[0].trial_id, 'trial-test')

    def test_invalid_paths_missing_files_and_invalid_models_fail_before_analysis(self):
        for name in ('../secret.ibt', '..\\secret.ibt', 'C:\\secret.ibt'):
            self.assertEqual(self.post('session', {'filename': name}).status_code, 422)
        self.assertEqual(self.post('session', {'filename': 'missing.ibt'}).status_code, 404)
        self.assertEqual(self.post('comparison', {'filename': 'baseline.ibt', 'reference_mode': 'SINGLE'}).status_code, 422)
        self.assertIsNone(self.state['analysis_result'])
        self.assertEqual(self.post('overlay', {'filename': 'baseline.ibt', 'points': 1}).status_code, 422)

    def test_workspace_focus_starts_and_closes_via_existing_practice_lifecycle(self):
        result = run('baseline.ibt')
        result['practice_focuses'] = {'WORKSPACE': [FOCUS]}
        self.state['analysis_result'] = result
        response = self.client.post('/api/practice/start', json={'filename': 'baseline.ibt', 'focus_index': 0, 'source': 'WORKSPACE'})
        self.assertEqual(response.status_code, 200, response.text)
        task_id = response.json()['task']['id']
        response = self.client.post(f'/api/practice/{task_id}/close', json={'status': 'DONE'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['task']['status'], 'DONE')

    def partial_recording(self):
        fixture = partial_fixtures.PartialCoachingTests()
        fixture.root = Path(self.work.name)
        source = fixture.telemetry()
        pct = source.channels['LapDistPct']
        source.channels.update(Lat=50 + np.sin(pct * 2 * np.pi) * .01,
            Lon=6 + np.cos(pct * 2 * np.pi) * .01,
            BrakeABSactive=((pct > .49) & (pct < .51)).astype(float))
        original = self.sources['baseline.ibt']
        source = replace(source, path=original.path, signature=original.signature)
        self.sources['baseline.ibt'] = source
        return source

    def test_clean_segment_session_keeps_observed_corners_without_creating_lap_times(self):
        self.partial_recording()
        response = self.post('session')
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result['defaultTarget'], 2)
        self.assertIsNone(result['defaultReference'])
        self.assertEqual(result['lapSelection']['strategy'], 'CLEAN_SEGMENT_COACHING')
        self.assertEqual(result['quality']['valid_laps'], 0)
        self.assertTrue(result['quality']['coaching_ready'])
        comparison = result['initialComparison']
        self.assertTrue(any(corner.get('measurement_usable') for corner in comparison['corners']))
        self.assertTrue(any((corner.get('target') or {}).get('min_speed_kph') is not None for corner in comparison['corners']))
        self.assertTrue(all(corner['delta_s'] is None for corner in comparison['corners']))
        for key in ('targetTime', 'referenceTime', 'delta'):
            self.assertIsNone(comparison[key])
        self.assertEqual(comparison['quality']['coaching_scope'], 'CLEAN_SEGMENTS_ONLY')
        self.assertFalse(comparison['quality']['pace_eligible'])
        self.assertTrue(all(row['delta_s'] is None for row in comparison['trace']))
        dirty = [row for row in comparison['trace'] if .49 < row['distance_pct'] < .51]
        self.assertTrue(dirty)
        self.assertTrue(all(row['target_speed_kph'] is None for row in dirty))
        self.assertEqual(comparison['controlEvents']['abs']['target']['events'], [])
        self.assertIsNone(result['analysisResult']['comparison']['total_delta_s'])
        self.assertEqual(result['analysisResult']['performance']['top_losses'], [])
        self.assertEqual(result['analysisResult']['practice_focuses']['WORKSPACE'], [])

    def test_null_comparison_recovers_selected_clean_lap_and_keeps_shared_context_consistent(self):
        self.partial_recording()
        load = api.load_analysis

        async def missing_comparison(*args, **kwargs):
            result = await load(*args, **kwargs)
            result['comparison'] = None
            return result

        with patch.object(api, 'load_analysis', side_effect=missing_comparison):
            response = self.post('session')
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result['defaultTarget'], 2)
        self.assertTrue(result['initialComparison']['corners'])
        self.assertEqual(result['analysisResult']['comparison']['status'], 'COACHING_ONLY')
        self.assertIsNone(result['analysisResult']['comparison']['target_time_s'])
        self.assertIsNone(self.state['analysis_result']['comparison']['total_delta_s'])

    def test_invalid_self_reference_does_not_unmask_dirty_segments_or_invent_zero_delta(self):
        self.partial_recording()
        body = {'filename': 'baseline.ibt', 'target_lap': 2, 'reference_lap': 2}
        response = self.post('comparison', body)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertIsNone(result['reference_lap'])
        self.assertIsNone(result['referenceTime'])
        self.assertIsNone(result['delta'])
        self.assertEqual(result['reference_track_path'], [])
        self.assertTrue(all(row['delta_s'] is None for row in result['trace']))
        # The legacy detail API already applies the same clean-window mask.
        detail = self.client.post('/api/comparison-detail', json=body)
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertFalse(detail.json()['quality']['pace_eligible'])
        self.assertTrue(all(row['delta_s'] is None for row in detail.json()['trace']))
        self.assertEqual(detail.json()['reference_track_path'], [])

    def test_no_valid_lap_and_no_clean_evidence_returns_a_clear_422(self):
        source = self.partial_recording()
        source.channels['PlayerTrackSurface'][:] = 0
        response = self.post('session')
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn('detail', response.json())


class PlannedSetupChangeTests(unittest.TestCase):
    def comparison(self):
        return {'effect_confirmed': True, 'verdict': 'KEEP', 'learning_eligibility': {'eligible': True},
                'canonical_setup_changes': [{'setting': 'rear_arb_blades', 'baseline_value': 3, 'candidate_value': 2}]}

    def test_exact_actual_single_change_keeps_original_objective_judgment(self):
        result = verify_planned_change(self.comparison(), {'setting': 'rear_arb_blades', 'original_value': 3, 'value': 2})
        self.assertTrue(result['plan_matches'])
        self.assertTrue(result['effect_confirmed'])
        self.assertTrue(result['learning_eligibility']['eligible'])

    def test_different_or_unknown_values_and_multiple_changes_never_learn(self):
        for planned in ({'setting': 'rear_arb_blades', 'original_value': 3, 'value': 1}, {},
                        {'setting': 'rear_arb_blades', 'original_value': 3, 'value': float('nan')}):
            result = verify_planned_change(self.comparison(), planned)
            self.assertFalse(result['effect_confirmed'])
            self.assertFalse(result['learning_eligibility']['eligible'])
            self.assertEqual(result['verdict'], 'INCONCLUSIVE')
        original = self.comparison()
        original['canonical_setup_changes'] *= 2
        self.assertFalse(verify_planned_change(original, {'setting': 'rear_arb_blades', 'original_value': 3, 'value': 2})['plan_matches'])


if __name__ == '__main__':
    unittest.main()
