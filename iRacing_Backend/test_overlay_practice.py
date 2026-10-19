"""End-to-end task/measurement/HUD contracts; no SDK, personal files or network."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

_import_temp = tempfile.TemporaryDirectory(prefix='overlay-practice-import-', ignore_cleanup_errors=True)
for _key, _file in {'IRACING_TUNER_DB_PATH': 'memory.sqlite3', 'IRACING_VOICE_DB_PATH': 'voice.sqlite3',
                    'IRACING_FILE_INDEX_PATH': 'index.sqlite3', 'IRACING_EXTERNAL_REFERENCE_DIR': 'references',
                    'IRACING_CORNER_DEFINITIONS_PATH': 'corners.json', 'IRACING_PIT_KNOWLEDGE_PATH': 'pit.json',
                    'GARAGE61_API_TOKEN_FILE': 'no-token'}.items():
    os.environ[_key] = str(Path(_import_temp.name)/_file)
os.environ.update(IRACING_AI_ENABLED='0', GARAGE61_AUTO_COLLECT='0', GARAGE61_IMPORT_DOWNLOADS='0')

from fastapi.testclient import TestClient
import backend_api as api
from drill_history import DrillHistory
from live_telemetry import LiveTelemetry
from live_drill import measured_inputs, drill_plan
from overlay_reference import GridReference, OverlayReference
from overlay_control import OverlayController
from overlay_session import session_panel
from practice import PracticeStore
from practice_metrics import TARGETS, GUARDS
from test_overlay_reference import Lap, live as reference_view, wait_for
from test_practice import run


def make_result(cause='THROTTLE_RETURN_LATE', baseline=None):
    result = run('overlay-check.ibt')
    values = {'full_throttle_pct': .29, 'brake_start_pct': .225, 'brake_release_pct': .25,
              'abs_duty_pct': 28., 'coast_time_s': .16, 'throttle_relifts': 2,
              'avg_abs_steer_deg': 18., 'steering_corrections': 3, 'steering_efficiency': .45,
              'min_speed_kph': 90., 'exit_speed_kph': 150., 'braking_distance_m': 80.}
    values.update(baseline or {})
    result['performance']['corners'][0]['target'] = values
    for row in result['performance']['corner_consistency']['corners'][0]['passes']:
        row['metrics'] = dict(values)
    result['visualizations'] = {'track_length_m': 3000.}
    result['comparison'] = {'target_lap': 3, 'reference_lap': 7}
    focus = {'root_cause': cause, 'root_cause_label': '실제 선택한 과제', 'affected_corners': ['5번 코너'],
             'source': 'PERSONAL_LAP_COMPARISON', 'action': '선택한 입력 목표'}
    result['practice_focuses'] = {'PERSONAL': [focus], 'GARAGE61': [dict(focus, source='GARAGE61_REFERENCE_CONSENSUS')]}
    result['closed_loop_coach'] = {'ranked_focuses': [focus]}
    return result


class PracticeFlowTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.saved = api.live_telemetry, api.practice_store, api.shared_sync_state.get('analysis_result')
        self.now, self.time = 100., 100.
        self.live = LiveTelemetry(clock=lambda: self.now)
        self.live.drill.history = DrillHistory(self.root/'drills.jsonl')
        api.live_telemetry = self.live
        api.practice_store = PracticeStore(self.root/'practice.json')
        self.client = TestClient(api.app)
        self.identity = {'car_path': 'mclaren720sgt3', 'car_name': 'McLaren', 'track_id': '179', 'track_config': ''}
        self.put(.1)

    def tearDown(self):
        self.live.practice.reset('TEST_END')
        self.live.drill.stop()
        self.live.drill.history.close()
        self.client.close()
        api.live_telemetry, api.practice_store, api.shared_sync_state['analysis_result'] = self.saved
        self.folder.cleanup()

    def put(self, pct, full_at=.285, missing=False, **changes):
        self.now += 1/30
        self.time += 1/30
        p = pct % 1
        speed = 25. if .245 <= p <= .255 else 50.
        row = {'distance_pct': p, 'session_time': self.time, 'speed_mps': speed,
               'brake': .8 if .225 <= p < .25 else 0.,
               'throttle': None if missing else 1. if p >= full_at or p < .225 else .4,
               'steering_rad': .1, 'yaw_rate': .2, 'abs_active': False,
               'on_track': True, 'is_replay': False, 'on_pit_road': False,
               'session_state': 4, 'session_num': 0, 'lap_number': 3+int(pct),
               'session_flags': 0, 'incidents': 0, 'session_type': 'Practice',
               'fuel_l': 40., 'track_temp_c': 30., 'air_temp_c': 23., 'track_wetness': 1,
               'tire_compound': 0, 'brake_bias': 50., 'abs_setting': 5, 'tc_setting': 5, **changes}
        self.live.ingest(self.identity, row)
        self.live.radio.metadata['track_length_m'] = 3000.
        return row

    def drive(self, start, end, **changes):
        rows = []
        at = start
        while at < end:
            row = self.put(at, **changes)
            rows.append(row)
            at += row['speed_mps']/30/3000
        return rows

    def start(self, cause='THROTTLE_RETURN_LATE', baseline=None, source='PERSONAL'):
        result = make_result(cause, baseline)
        api.shared_sync_state['analysis_result'] = result
        response = self.client.post('/api/practice/start', json={'filename': result['file']['name'],
                                   'focus_index': 0, 'source': source})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['task']

    def drill(self, task, warmup=False):
        response = self.client.post(f"/api/practice/{task['id']}/drill", json={
            'filename': 'overlay-check.ibt', 'corner_label': '5번 코너', 'warmup': warmup})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def hud(self):
        view = self.live.overlay_state()
        json.dumps(view, allow_nan=False)
        return view['practice']

    def test_all_seven_goals_reach_hud_with_real_baseline_and_same_pinned_source(self):
        for cause, rule in TARGETS.items():
            with self.subTest(cause=cause):
                task = self.start(cause)
                hud = self.hud()
                self.assertEqual(hud['task_id'], task['id'])
                self.assertEqual(hud['cause'], cause)
                self.assertEqual(hud['corner'], '5번 코너')
                self.assertIn(rule[4], hud['title'])
                self.assertEqual(hud['reference']['source'], 'PERSONAL')
                self.assertEqual(hud['reference']['lap'], 7)
                self.assertEqual(hud['count'], 0)
                if cause == 'OVER_BRAKING_ABS':
                    self.assertEqual((hud['before'], hud['target']), ('28%', '≤23%'))

    def test_ordinary_laps_measure_five_successes_without_explicit_drill(self):
        task = self.start()
        end = task['live_bundle']['plans'][0]['end_pct']+.002
        for lap in range(5):
            self.drive(lap+.13, lap+end)
            if lap < 4:
                self.drive(lap+end, lap+1.13)
        hud = self.hud()
        self.assertEqual(self.client.get('/api/drill').json(), {'active': False})
        self.assertEqual(hud['task_id'], task['id'])
        self.assertEqual(hud['phase'], 'RESULT')
        self.assertEqual(hud['states'], [True]*5)
        self.assertTrue(hud['celebration_id'])
        self.assertEqual(hud['celebration_id'], self.hud()['celebration_id'])
        self.assertFalse(hud['pace_verified'])
        self.live.drill.history.flush()
        history = self.client.get(f"/api/practice/{task['id']}/drills").json()
        self.assertEqual(history['runs'][-1]['attempt_count'], 5)

    def test_all_seven_goals_measure_success_failure_and_failed_guard_from_real_inputs(self):
        # Read actual resampled measurements first, then vary the saved baseline.
        # Neither measured_inputs nor evaluate is mocked on this complete API path.
        probe = self.start()
        end = probe['live_bundle']['plans'][0]['end_pct']+.002
        self.drive(.13, end)
        measured = self.live.practice.trackers[0].attempts[-1]['metrics']
        for cause, (metric, unit, sign, step, _) in TARGETS.items():
            base = dict(measured)
            self.assertIsNotNone(base[metric], cause)
            base[metric] -= sign*step*2/(3000 if unit == 'm' else 1)
            for key, direction, limit, strict, _label in GUARDS[cause]:
                self.assertIsNotNone(base[key], (cause, key))
                base[key] -= limit+(1 if strict else .1) if direction == 'min' else limit-1
            for scenario, expected in (('success', True), ('primary', False), ('guard', False)):
                with self.subTest(cause=cause, scenario=scenario):
                    baseline = dict(base)
                    if scenario == 'primary':
                        baseline[metric] = measured[metric]
                    if scenario == 'guard':
                        key, direction, limit, strict, _label = GUARDS[cause][0]
                        baseline[key] = measured[key]-limit+(1 if direction == 'min' else -1)
                    self.start(cause, baseline)
                    self.put(.13)
                    self.drive(.13+50/30/3000, end)
                    hud = self.hud()
                    self.assertEqual(hud['phase'], 'RESULT')
                    self.assertEqual(hud['goal_ok'], expected, hud)
                    self.assertEqual(hud['states'][-1], expected)

    def test_reanalysis_and_drill_keep_the_task_reference_and_baseline_pinned(self):
        task = self.start()
        saved = deepcopy(task['live_bundle'])
        changed = make_result(baseline={'full_throttle_pct': .33})
        changed['comparison']['reference_lap'] = 11
        changed['performance']['corners'][0]['reference'] = {'brake_start_pct': .20}
        api.practice_store.record(changed)
        api.refresh_live_practice()
        api.shared_sync_state['analysis_result'] = changed
        plan = self.drill(task)['plan']
        self.assertEqual(task['live_bundle'], saved)
        self.assertEqual(plan['base_metrics'], saved['plans'][0]['base_metrics'])
        self.assertEqual(plan['brake_pct'], saved['plans'][0]['brake_pct'])
        self.assertEqual(self.hud()['reference']['lap'], 7)

    def test_changed_conditions_restart_last_five_and_session_clock_reset_clears_results(self):
        task = self.start()
        end = task['live_bundle']['plans'][0]['end_pct']+.002
        self.drive(.13, end)
        self.assertEqual(self.hud()['count'], 1)
        self.drive(end, 1.13, tc_setting=4)
        self.assertEqual(self.hud()['count'], 0)
        self.assertIsNone(self.hud()['celebration_id'])
        self.time = 0
        self.put(.13)
        self.assertEqual(self.hud()['count'], 0)

    def test_warmup_actual_verdict_missing_channels_and_result_expiry(self):
        task = self.start()
        self.drill(task, warmup=True)
        self.assertEqual(self.hud()['phase'], 'WARMUP')
        self.client.post('/api/drill/skip-warmup')
        self.drive(.13, .35, missing=True)
        hud = self.hud()
        self.assertEqual(hud['phase'], 'RESULT')
        self.assertIsNone(hud['goal_ok'])
        self.assertEqual(hud['after'], '—')
        self.assertFalse(hud['five_successes'])
        self.drive(.35, .45)
        self.assertNotEqual(self.hud()['phase'], 'RESULT')

    def test_four_of_five_milestone_continues_to_actual_five_of_five_once(self):
        task = self.start()
        started = self.drill(task)
        end = started['plan']['end_pct']+.002
        self.drive(.13, end)
        for full in (.290, .285, .285, .285, .285):
            self.put(.20, full_at=full)
            self.drive(.20+50/30/3000, end, full_at=full)
        self.assertTrue(self.live.drill.done)
        self.assertEqual(self.hud()['recent_ok'], 4)
        self.assertIsNone(self.hud()['celebration_id'])
        self.put(.20)
        self.drive(.20+50/30/3000, end)
        self.assertEqual(self.hud()['recent_ok'], 5)
        token = self.hud()['celebration_id']
        self.assertTrue(token)
        self.assertEqual(self.hud()['celebration_id'], token)
        self.assertEqual(sum(e['kind'] == 'done' for e in self.live.drill.events), 1)

    def test_replacing_closing_and_changing_identity_clear_old_goal_and_drill(self):
        first = self.start()
        self.drill(first)
        second = self.start('OVER_BRAKING_ABS')
        self.assertFalse(self.live.drill.view()['active'])
        self.assertEqual(self.hud()['task_id'], second['id'])
        self.client.post(f"/api/practice/{second['id']}/close", json={'status': 'DONE'})
        self.assertIsNone(self.hud())
        self.start()
        self.identity = {**self.identity, 'track_id': 'another-track'}
        self.put(.2)
        self.assertIsNone(self.hud())

    def test_pit_replay_gap_and_incident_do_not_create_a_success(self):
        for invalid in ({'on_pit_road': True}, {'is_replay': True}, {'on_track': False},
                        {'car_left_right': 2}, {'session_flags': 0x100}):
            with self.subTest(invalid=invalid):
                self.start()
                self.drive(.13, .4, **invalid)
                self.assertEqual(self.hud()['recent_ok'], 0)
                self.assertIsNone(self.hud()['celebration_id'])

    def test_restored_task_uses_saved_plan_without_original_analysis_or_disk_on_sampling(self):
        task = self.start()
        api.practice_store = PracticeStore(self.root/'practice.json')
        self.live.practice.reset()
        api.shared_sync_state['analysis_result'] = None
        api.refresh_live_practice()
        original_open, sampling_thread = Path.open, threading.get_ident()
        def checked_open(path, *args, **kwargs):
            self.assertNotEqual(threading.get_ident(), sampling_thread, 'No disk on SDK path')
            return original_open(path, *args, **kwargs)
        with patch('pathlib.Path.open', checked_open):
            self.drive(.13, task['live_bundle']['plans'][0]['end_pct']+.002)
            self.assertEqual(self.hud()['task_id'], task['id'])
            self.assertEqual(self.hud()['phase'], 'RESULT')

    def test_qualifying_and_race_do_not_inherit_practice_goal_or_celebration(self):
        self.start()
        self.put(.2, session_type='Qualify', lap_best_s=91.123, session_time_remaining_s=120.)
        state = self.live.overlay_state()
        self.assertIsNone(state['practice'])
        self.assertEqual(state['session']['kind'], 'QUALIFY')
        self.assertEqual(state['session']['left'], '베스트 1:31.123')
        self.put(.2, session_type='Race')
        self.assertEqual(self.live.overlay_state()['session']['kind'], 'RACE')

    def test_qualifying_sector_delta_comes_from_actual_completed_lap_samples(self):
        self.drive(.9, 2.2, session_type='Qualify')
        delta = self.live.lap_sectors.delta()
        self.assertIsNotNone(delta)
        self.assertAlmostEqual(delta['delta_s'], 0., places=2)
        panel = self.live.overlay_state()['session']
        self.assertIn('구간 Δ', panel['title'])
        self.assertNotIn('측정 중', panel['title'])

    def test_race_panel_uses_the_existing_engineer_for_position_gaps_and_fuel(self):
        from collections import deque
        from test_race_engineer import race_fixture
        row = self.put(.5, session_type='Race')
        row['session_time'] += 1/30
        self.now += 1/30
        self.live.ingest(self.identity, row, race=race_fixture())
        self.live.pit_wall.paces = {1: deque([100., 100., 100.])}
        self.live.fuel_history = deque([2., 2., 2.])
        panel = self.live.overlay_state()['session']
        self.assertEqual(panel['title'], '경기 · P2 · 연료 20.0랩')
        self.assertEqual(panel['left'], '앞 약 30.0초')
        self.assertEqual(panel['right'], '뒤 약 2.0초')

    def test_impossible_negative_target_is_not_displayed_as_a_real_target(self):
        self.start('OVER_BRAKING_ABS', {'abs_duty_pct': 2.})
        hud = self.hud()
        self.assertFalse(hud['goal_possible'])
        self.assertEqual(hud['target'], '—')
        self.assertIn('재선택', hud['title'])


class SessionPanelTests(unittest.TestCase):
    def test_race_values_are_existing_measurements_and_unknowns_stay_unknown(self):
        panel = session_panel('Race', {}, race={'class_position': 5, 'fuel_laps': 8.2,
            'ahead': {'gap_estimate_s': 1.4}, 'behind': {'gap_estimate_s': .6}})
        self.assertIn('P5', panel['title'])
        self.assertIn('8.2랩', panel['title'])
        self.assertEqual(panel['left'], '앞 약 1.4초')
        missing = session_panel('Race', {})
        self.assertNotIn('P0', missing['title'])
        self.assertEqual(missing['left'], '앞 —')

    def test_qualifying_delta_is_labeled_as_observed_sector_delta(self):
        panel = session_panel('Lone Qualify', {'lap_best_s': 91.}, {'delta_s': -.123})
        self.assertEqual(panel['title'], '퀄리 · 구간 Δ -0.123초')


class PinnedReferenceTests(unittest.TestCase):
    def test_sample_commands_cannot_inject_a_fictional_celebration_into_live_mode(self):
        with tempfile.TemporaryDirectory() as folder:
            controller = OverlayController(Path(folder)/'overlay.json')
            with self.assertRaisesRegex(RuntimeError, '미리보기'):
                controller.command('demo', {'celebrate': True})

    def test_pinned_personal_and_aggregate_selection_never_fall_back_to_unrelated_garage61(self):
        with tempfile.TemporaryDirectory() as folder:
            loader, automatic = Mock(return_value=Lap()), Mock(side_effect=AssertionError('Wrong source'))
            service = OverlayReference(folder, automatic, pinned_loader=loader)
            try:
                for source in ('PERSONAL', 'GARAGE61'):
                    pinned = {'source': source, 'file': 'original.ibt', 'lap': 7,
                              'files': ['selected-a.csv', 'selected-b.csv'], 'task_id': source}
                    view = reference_view()
                    view['practice'] = {'reference': pinned}
                    service.observe(view, True)
                    wait_for(lambda: service.status()['state'] == 'READY')
                    loader.assert_called_with(pinned)
                    self.assertEqual(service.status()['source'], source)
                    self.assertTrue(service.enrich(view)['comparison'])
                loader.side_effect = ValueError('Missing selected file')
                view['practice']['reference'] = {**pinned, 'task_id': 'missing'}
                service.observe(view, True)
                wait_for(lambda: service.status()['state'] == 'TASK_REFERENCE_MISSING')
                self.assertIsNone(service.enrich(view)['comparison'])
                automatic.assert_not_called()
            finally:
                service.close()

    def test_exact_loader_rejects_partial_garage61_aggregate(self):
        selection = {'file': 'original.ibt', 'source': 'GARAGE61', 'identity':
                     {'car': 'car', 'track': '179', 'config': ''}, 'target_lap': 3,
                     'files': ['a.csv', 'b.csv']}
        telemetry = Mock(metadata={'car_path': 'car', 'track_platform_id': '179'}, laps=[Mock(lap_number=3)])
        analyzer = Mock()
        analyzer._external_reference_analysis.return_value = {'status': 'READY', 'selected_files': selection['files'],
            'selected_reference_count': 1, 'errors': ['b.csv missing']}
        with patch.object(api, 'resolve_telemetry_file', return_value=Path('original.ibt')), \
             patch.object(api.repository, 'load', return_value=telemetry), \
             patch.object(api, 'TelemetryAnalyzer', return_value=analyzer):
            with self.assertRaises(ValueError):
                api.load_task_overlay_reference(selection)


if __name__ == '__main__':
    unittest.main()
