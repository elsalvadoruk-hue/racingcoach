"""Production desktop API contracts, using synthetic recordings and temporary stores."""
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
import unittest
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
        overlay = self.post('overlay', {'filename': 'baseline.ibt', 'laps': [2, 3], 'base_lap': 2, 'start_pct': .1, 'end_pct': .4})
        self.assertEqual(overlay.status_code, 200, overlay.text)
        result = overlay.json()
        self.assertEqual(set(result['series']), {'2', '3'})
        self.assertGreater(len(result['distance_pct']), 100)
        self.assertGreaterEqual(min(result['distance_pct']), .1)

    def test_catalog_and_references_use_actual_services(self):
        with patch.object(api, 'list_ibt_files', return_value=[self.sources['baseline.ibt'].path]), patch.object(api, 'quick_file_visibility', return_value={
                'car_name': 'Test', 'session_type': 'Practice', 'valid_laps': 3, 'detected_laps': 3, 'analyzable': True}):
            response = self.post('files', {})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['files'][0]['valid_laps'], 3)
        with patch.object(api, 'list_external_reference_metadata', return_value={'files': [{'name': 'actual.csv', 'usable': True, 'compatible': True}]}):
            response = self.post('references')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['files'][0]['name'], 'actual.csv')

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
