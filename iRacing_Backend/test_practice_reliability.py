"""Regressions from the Long Beach practice: missing inputs, side effects and retained attempts."""
from copy import deepcopy
from pathlib import Path
import tempfile
import time
import unittest

from drill_history import DrillHistory
from live_drill import CornerDrill, measured_inputs
from practice import PracticeStore, check
from practice_metrics import evaluate
from test_live_drill import Driver, L, plan
from test_practice import FOCUS, run


class GoalTests(unittest.TestCase):
    def test_resaving_same_position_with_different_fuel_starts_new_comparison(self):
        drill = CornerDrill()
        drill.start(plan(), warmup=False)
        driver = Driver(drill)
        driver.drive(.20, drill.plan['end_pct'] + .002)
        driver.reset_to(.24)
        driver.drive(.24 + 50 / 30 / L, drill.plan['end_pct'] + .002)
        self.assertEqual(drill.view()['recent_count'], 1)
        old_series = drill.series
        drill.ingest({'distance_pct': .24, 'session_time': driver.time, 'speed_mps': 50., 'fuel_l': 20.,
                      'on_track': True, 'is_replay': False, 'on_pit_road': False}, L)
        self.assertGreater(drill.series, old_series)
        self.assertEqual(drill.view()['recent_count'], 0)
        self.assertFalse(drill.view()['attempts'][-1]['comparable'])

    def test_stable_times_without_measured_goal_never_complete(self):
        for cause in ('OVER_BRAKING_ABS', 'THROTTLE_RETURN_LATE', 'unknown'):
            drill = CornerDrill()
            drill.start(plan(cause=cause), warmup=False)
            drill.saved_pct = .24
            drill.attempts = [dict(origin_m=.24 * L, input_ok=None, time_s=10., brake_start_m=100.) for _ in range(5)]
            self.assertFalse(drill._settled(L), cause)

    def test_abs_is_measured_and_requires_speed_and_braking_distance(self):
        drill = CornerDrill()
        drill.start(plan(cause='OVER_BRAKING_ABS', base_metrics={'abs_duty_pct': 40, 'min_speed_kph': 90,
                                                               'braking_distance_m': 130}), warmup=False)
        driver = Driver(drill)
        driver.drive(.20, drill.plan['end_pct'] + .002)
        for _ in range(5):
            driver.reset_to(.24)
            driver.drive(.24 + 50 / 30 / L, drill.plan['end_pct'] + .002)
        self.assertTrue(drill.done)
        self.assertEqual(drill.attempts[-1]['metrics']['abs_duty_pct'], 0)
        self.assertEqual(drill.attempts[-1]['input_change'], -40)
        self.assertFalse(drill.view()['pace_verified'])
        self.assertEqual(drill.history.view(None)['runs'][0]['outcome'], 'INPUT_ACHIEVED')

    def test_abs_reduction_with_speed_loss_or_missing_brake_distance_is_not_success(self):
        before = {'abs_duty_pct': 40, 'min_speed_kph': 90, 'braking_distance_m': 130}
        for after in ({'abs_duty_pct': 10, 'min_speed_kph': 85, 'braking_distance_m': 120},
                      {'abs_duty_pct': 10, 'min_speed_kph': 90}):
            result = evaluate('OVER_BRAKING_ABS', before, after, L)
            self.assertTrue(result['input_ok'])
            self.assertIsNot(result['goal_ok'], True)

    def test_release_goal_requires_actual_release_position(self):
        before = {'min_speed_kph': 90, 'brake_release_pct': .32, 'avg_abs_steer_deg': 10, 'abs_duty_pct': 20}
        after = dict(before, min_speed_kph=93)
        self.assertFalse(evaluate('BRAKE_RELEASE_LATE', before, after, L)['input_ok'])
        after['brake_release_pct'] = .318
        self.assertTrue(evaluate('BRAKE_RELEASE_LATE', before, after, L)['goal_ok'])

    def test_missing_abs_channel_is_not_zero_and_invalid_sample_breaks_attempt(self):
        drill = CornerDrill()
        drill.start(plan(cause='OVER_BRAKING_ABS'), warmup=False)
        driver = Driver(drill)
        driver.drive(.20, .315)
        samples = deepcopy(drill.current['samples'])
        for sample in samples:
            sample.pop('abs_active', None)
        # Complete the observed section before checking channel absence.
        driver.drive(.315, drill.plan['end_pct'] + .002)
        all_samples = []
        for i in range(700):
            pct = .24 + i * .0002
            all_samples.append({'distance_pct': pct, 'session_time': i / 30, 'speed_mps': 50.,
                                'brake': .8 if pct < .32 else 0., 'throttle': .2 if pct < .35 else 1., 'steering_rad': .1})
        self.assertIsNone(measured_inputs(all_samples, drill.plan, L)['abs_duty_pct'])
        drill.current = {'start_time': 1}
        drill.ingest({'distance_pct': .3, 'session_time': 200., 'on_track': False}, L)
        self.assertIsNone(drill.current)


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.store = PracticeStore()
        self.task = self.store.start(run('a.ibt'), FOCUS)
        self.faster = run('b.ibt', full_throttle=.298, times=(9.8, 9.9, 10.0))

    def test_conditions_or_setup_difference_blocks_confirmation(self):
        for field, value in [('fuel_l', 10), ('track_temp_c', 40), ('tire_compound', 1)]:
            candidate = deepcopy(self.faster)
            for p in candidate['performance']['corner_consistency']['corners'][0]['passes']:
                p['conditions'][field] = value
            result = check(self.task, candidate)
            self.assertEqual(result['status'], 'UNVERIFIED', field)
            self.assertTrue(result['reasons'])
        self.faster['session']['setup_hash'] = 'another'
        self.assertEqual(check(self.task, self.faster)['status'], 'UNVERIFIED')

    def test_small_gain_within_natural_spread_is_only_an_input_change(self):
        candidate = run('b.ibt', full_throttle=.298, times=(9., 10.05, 11.))
        result = check(self.task, candidate)
        self.assertEqual(result['status'], 'INPUT_ONLY')
        self.assertGreater(result['noise_s'], result['total_gain_s'])

    def test_target_lap_input_cannot_stand_in_for_the_timed_passes(self):
        for p in self.faster['performance']['corner_consistency']['corners'][0]['passes']:
            p['metrics']['full_throttle_pct'] = .30
        self.assertEqual(check(self.task, self.faster)['status'], 'FASTER_OTHER')

    def test_missing_guard_and_legacy_snapshot_do_not_confirm(self):
        for p in self.faster['performance']['corner_consistency']['corners'][0]['passes']:
            p['metrics'].pop('throttle_relifts')
        self.assertEqual(check(self.task, self.faster)['status'], 'UNVERIFIED')
        self.task['corners'][0].pop('paired')
        result = check(self.task, run('b.ibt', full_throttle=.298, times=(9.8, 9.9, 10.0)))
        self.assertEqual(result['status'], 'UNVERIFIED')
        self.assertIn('같은 통과', ' '.join(result['reasons']))

    def test_reanalysing_original_file_upgrades_legacy_baseline(self):
        self.task['corners'][0].pop('paired')
        self.task['corners'][0].pop('conditions')
        self.task.pop('setup_hash')
        view = self.store.record(run('a.ibt'))
        self.assertEqual(view['check']['status'], 'WAITING')
        self.assertEqual(self.store.record(self.faster)['check']['status'], 'ACHIEVED')

    def test_different_section_boundaries_are_not_time_improvements(self):
        self.faster['performance']['corners'][0]['exit_pct'] -= .001
        self.assertEqual(check(self.task, self.faster)['status'], 'NO_DATA')

    def test_completed_task_is_rechecked_on_later_normal_run_without_reopening(self):
        self.store.close(self.task['id'], 'DONE')
        self.faster['file']['modified_at'] = time.time() + 1
        view = self.store.record(self.faster)
        self.assertIsNone(view['active'])
        self.assertEqual(view['followups'][0]['check']['status'], 'ACHIEVED')
        self.assertEqual(self.task['status'], 'DONE')
        self.store.record(self.faster)
        self.assertEqual(len(self.task['checks']), 1)
        self.faster['file']['modified_at'] = self.task['closed_at'] - 100
        self.assertEqual(self.store.record(self.faster)['followups'], [])

    def test_history_separates_manual_finish_and_qualified_improvement(self):
        self.task['checks'] = [{'status': 'FASTER_OTHER', 'total_gain_s': 5},
                               {'status': 'ACHIEVED', 'total_gain_s': 6}]  # legacy check lacks qualification
        self.store.close(self.task['id'], 'DONE')
        summary = self.store.history()['by_cause'][0]
        self.assertEqual((summary['done'], summary['verified'], summary['gain_s']), (1, 0, 0))
        self.assertEqual(self.store.history({'car': 'other'})['by_cause'], [])


class RetentionTests(unittest.TestCase):
    def test_every_attempt_survives_stop_and_restart_without_a_browser(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'drills.jsonl'
            drill = CornerDrill()
            drill.history = DrillHistory(path)
            drill.start(plan(task_id='chosen'), warmup=False)
            driver = Driver(drill)
            driver.drive(.20, drill.plan['end_pct'] + .002)
            driver.reset_to(.24)
            driver.drive(.24 + 50 / 30 / L, drill.plan['end_pct'] + .002)
            drill.stop()
            drill.history.close()
            loaded = DrillHistory(path)
            kept = loaded.view('chosen')['runs'][0]
            self.assertEqual((kept['attempt_count'], kept['outcome']), (2, 'STOPPED'))
            self.assertIsNone(kept['attempts'][0]['origin_m'])
            self.assertAlmostEqual(kept['attempts'][1]['origin_m'], .24 * L)
            self.assertIsNone(loaded.error)
            self.assertEqual(loaded.view('another')['runs'], [])

    def test_a_partial_last_line_does_not_destroy_earlier_attempts(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'drills.jsonl'
            store = DrillHistory(path)
            store.record({'kind': 'start', 'id': '1', 'at': 1, 'plan': {'task_id': 't'}})
            store.record({'kind': 'attempt', 'id': '1', 'attempt': {'n': 1}})
            store.close()
            with path.open('a') as handle:
                handle.write('{"kind":')
            again = DrillHistory(path)
            self.assertEqual(again.view('t')['runs'][0]['attempt_count'], 1)
            self.assertIsNotNone(again.error)
            again.record({'kind': 'end', 'id': '1', 'at': 2, 'outcome': 'STOPPED'})
            again.close()
            self.assertEqual(DrillHistory(path).view('t')['runs'][0]['outcome'], 'STOPPED')


if __name__ == '__main__':
    unittest.main()
