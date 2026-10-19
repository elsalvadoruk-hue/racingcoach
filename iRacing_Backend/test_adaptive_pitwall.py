"""Driving scenarios: no running game, credentials, cloud calls or personal DB."""
from collections import deque
import copy
import math
import unittest

from live_telemetry import LiveTelemetry
from radio_policy import TrackSpeechWindows, report_window
from race_engineer import RaceEngineer, measured_answer
from test_race_engineer import race_fixture
from voice_coach import ReportRequest


def sample(at=0., lap=1, pct=0., **updates):
    return {'session_time': at, 'lap_number': lap, 'distance_pct': pct,
            'on_track': True, 'is_replay': False, 'on_pit_road': False,
            'car_left_right': 1, 'speed_mps': 40., 'brake': 0., 'steering_rad': 0.,
            'throttle': 1., 'incidents': 0, 'fuel_l': 20., **updates}


class SpeechTimingTests(unittest.TestCase):
    def train(self):
        model = TrackSpeechWindows()
        for lap in range(1, 4):
            for i in range(1000):
                pct = i / 1000
                model.ingest(sample((lap - 1) * 100 + i / 10, lap, pct,
                                    brake=0. if .1 <= pct < .3 else .3))
        return model

    def test_two_complete_laps_learn_straight_and_leave_braking_margin(self):
        model = self.train()
        middle = sample(315, 4, .15)
        view = model.view(middle)
        self.assertGreaterEqual(view['speech_learned_laps'], 2)
        self.assertGreater(view['speech_window_s'], 9)
        self.assertTrue(report_window({**middle, **view}, '연료 목표 유지.'))
        end = sample(329.5, 4, .295)
        self.assertFalse(report_window({**end, **model.view(end)}, '연료 목표 유지.'))
        self.assertEqual(model.view({**middle, 'brake': .4})['speech_window_s'], 0)
        self.assertEqual(model.view({**middle, 'car_left_right': 2})['speech_window_s'], 0)

    def test_incomplete_replay_and_missing_intervals_never_train_a_track(self):
        model = TrackSpeechWindows()
        for lap in range(1, 4):
            for i in range(30):
                model.ingest(sample((lap - 1) * 100 + i, lap, i / 30))
        self.assertEqual(len(model.laps), 0)
        model.ingest(sample(400, 4, .01, is_replay=True))
        self.assertIsNone(model.previous)
        self.assertIsNone(model.view(sample())['speech_window_s'])

    def test_overlap_channel_off_does_not_disable_other_radio_after_learning(self):
        model = self.train()
        self.assertGreater(model.view(sample(315, 4, .15, car_left_right=0))['speech_window_s'], 9)
        self.assertGreater(model.view(sample(315, 4, .15, car_left_right=None))['speech_window_s'], 9)
        self.assertIsNone(model.view(sample(315, 4, .15, on_pit_road=True))['speech_window_s'])

    def test_a_call_does_not_start_inside_the_drivers_hold_and_urgent_fuel_bypasses_it(self):
        wall = RaceEngineer()
        reports = [{'key': 'battle', 'engineer': 'race', 'text': '상세 설명입니다. ' * 20,
                    'short_text': '뒤차 접근 중.', 'priority': 1, 'token': 'car', 'cooldown': 120}]
        s = sample(100, workload_in_s=4.)
        # Braking is four seconds away: the call may start, however long it is, and is not cut later.
        self.assertEqual(wall.select(reports, s, 'all')['text'], reports[0]['text'])
        self.assertIsNone(wall.select(reports, {**s, 'workload_in_s': .7}, 'all'))               # inside the default 1.0 s
        self.assertIsNotNone(wall.select(reports, {**s, 'workload_in_s': .7, 'speech_hold_s': .5}, 'all'))   # his own, shorter hold
        self.assertIsNone(wall.select(reports, {**s, 'workload_in_s': 2., 'speech_hold_s': 3.}, 'all'))      # his own, longer hold
        self.assertIsNone(wall.select(reports, {**s, 'brake': .8}, 'all'))                         # braking now
        self.assertIsNotNone(wall.select(reports, {**s, 'brake': .8, 'speech_hold_s': -1.}, 'all'))   # hold switched off
        self.assertIsNotNone(wall.select(reports, {**s, 'workload_in_s': None}, 'all'))           # braking points not learned yet
        urgent = [{**reports[0], 'priority': 3}]
        self.assertIsNotNone(wall.select(urgent, {**s, 'workload_in_s': 0, 'brake': .8}, 'important'))

    def test_the_learned_stretch_reports_the_plain_time_to_the_next_braking(self):
        model = self.train()
        view = model.view(sample(315, 4, .15, car_left_right=0))
        self.assertGreater(view['workload_in_s'], view['speech_window_s'])   # the display value keeps a margin
        self.assertEqual(model.view(sample(315, 4, .15, brake=.5))['workload_in_s'], 0.)
        self.assertIsNone(TrackSpeechWindows().view(sample(315, 4, .15))['workload_in_s'])

    def test_session_change_resets_learned_layout(self):
        live = LiveTelemetry(clock=lambda: 1)
        live.ingest({'track_id': '1', 'car_id': '2'}, sample())
        live.speech_windows = self.train()
        live.ingest({'track_id': '9', 'car_id': '2'}, sample(1))
        self.assertEqual(live.context()['snapshot']['speech_learned_laps'], 0)


class BattleTests(unittest.TestCase):
    def setUp(self):
        self.race = race_fixture()
        self.wall = RaceEngineer()
        self.wall.ingest(self.race, 'battle', 0)
        self.wall.paces = {i: deque([100.] * 3) for i in range(3)}

    def anchor(self, lap, gap):
        for pct, at in ((.98, lap * 100 - 2), (.01, lap * 100)):
            race = copy.deepcopy(self.race)
            own_lap = lap + 2 if pct == .98 else lap + 3
            race['cars'][1].update(lap_completed=own_lap, distance_pct=pct)
            behind = own_lap + pct - gap / 100
            race['cars'][2].update(lap_completed=math.floor(behind), distance_pct=behind % 1)
            self.wall.ingest(race, 'battle', at)
        self.snapshot = sample(at, lap + 4, pct, fuel_l=40., run_id='battle')

    def view(self):
        return self.wall.view(self.snapshot, 2, 3)

    def test_consistent_same_location_closing_is_reported_after_three_laps(self):
        self.anchor(1, 4.)
        self.anchor(2, 3.5)
        self.assertIsNone(self.view()['behind']['gap_trend'])
        self.anchor(3, 3.)
        car = self.view()['behind']
        self.assertEqual(car['gap_trend'], 'closing')
        self.assertEqual(car['gap_change_s_per_lap'], -.5)
        reports = self.wall.candidates(self.view(), self.snapshot, {})
        self.assertIn('battle_behind', [r['key'] for r in reports])
        self.assertIn('줄고', measured_answer('race', '뒤차가 따라와?', self.view()))

    def test_changing_gap_direction_is_not_a_sustained_approach(self):
        for lap, gap in enumerate((4, 3, 4), 1):
            self.anchor(lap, gap)
        self.assertEqual(self.view()['behind']['gap_trend'], 'steady')
        self.assertNotIn('battle_behind', [r['key'] for r in self.wall.candidates(self.view(), self.snapshot, {})])

    def test_pit_and_different_class_clear_battle_history(self):
        for lap, gap in enumerate((4, 3.5, 3), 1):
            self.anchor(lap, gap)
        changed = copy.deepcopy(self.wall.race)
        changed['cars'][2]['on_pit_road'] = True
        self.wall.ingest(changed, 'battle', 301)
        self.assertEqual(self.wall.gap_history, {})
        changed['cars'][2].update(on_pit_road=False, class_id=20)
        self.wall.ingest(changed, 'battle', 302)
        self.assertIsNone(self.view()['behind'])
        self.assertEqual(self.view()['other_class_traffic'][0]['idx'], 2)

    def test_session_reset_discards_observed_battle(self):
        self.anchor(1, 4)
        self.wall.ingest(self.race, 'new-race', 1)
        self.assertEqual(self.wall.gap_history, {})


class FuelSavingTests(unittest.TestCase):
    def setUp(self):
        self.live = LiveTelemetry(clock=lambda: 1)
        self.race = race_fixture()
        self.live.ingest({'track_id': '1'}, sample(0), self.race)
        self.live.pit_wall.paces = {i: deque([100.] * 3) for i in range(3)}
        self.live.fuel_history.extend([2., 2., 2.])

    def drive(self, lap, start_fuel, use, **updates):
        for i in range(101):
            at = lap * 101 + i
            pct = i / 100 if i < 100 else .0
            self.live.ingest({'track_id': '1'}, sample(at, lap if i < 100 else lap + 1,
                              pct, fuel_l=start_fuel - use * i / 100, **updates))

    def test_target_is_frozen_at_lap_start_and_compared_after_completion(self):
        self.drive(4, 12., 1.4)   # 12 litres for 6.5 laps: an 8 percent saving, within reach
        context = self.live.context()
        result = context['fuel_feedback'][-1]
        self.assertAlmostEqual(result['target_l'], 12 / 6.5, places=3)
        self.assertAlmostEqual(result['use_l'], 1.4)
        view = self.live.race_view()
        reports = self.live.pit_wall.candidates(view, context['snapshot'], context)
        feedback = next(r for r in reports if r['key'] == 'fuel_feedback')
        self.assertIn('덜', feedback['text'])
        self.assertIn('1.40리터', measured_answer('strategy', '절약 목표 지켰어?', view))

    def test_reserve_goal_and_off_mode_are_distinct(self):
        wall = self.live.pit_wall
        s = sample(fuel_l=12.)
        finish = wall.view(s, 2, 3, fuel_save_mode='finish')
        reserve = wall.view(s, 2, 3, reserve_laps=.1, fuel_save_mode='reserve')
        self.assertLess(reserve['fuel_save_target_l_per_lap'], finish['fuel_save_target_l_per_lap'])
        self.assertIsNone(wall.view(s, 2, 3, fuel_save_mode='off')['fuel_save_target_l_per_lap'])
        with self.assertRaises(ValueError):
            ReportRequest(fuel_save_mode='unknown')

    def test_pit_replay_and_long_gap_do_not_create_fuel_feedback(self):
        for updates in ({'on_pit_road': True}, {'is_replay': True}):
            with self.subTest(updates=updates):
                self.live.lap_fuel = None
                self.drive(4, 10, 1.4, **updates)
                self.assertEqual(list(self.live.fuel_feedback), [])
        self.live.lap_fuel = None
        self.live.ingest({'track_id': '1'}, sample(700, 6, 0., fuel_l=10.))
        self.live.ingest({'track_id': '1'}, sample(720, 6, .98, fuel_l=9.))
        self.live.ingest({'track_id': '1'}, sample(721, 7, .01, fuel_l=8.6))
        self.assertEqual(list(self.live.fuel_feedback), [])

    def test_refueling_clears_old_stint_consumption_and_feedback(self):
        self.drive(4, 10, 1.4)
        self.live.ingest({'track_id': '1'}, sample(506, 5, .01, fuel_l=25.))
        self.assertEqual(list(self.live.fuel_history), [])
        self.assertEqual(list(self.live.fuel_feedback), [])


if __name__ == '__main__':
    unittest.main()
