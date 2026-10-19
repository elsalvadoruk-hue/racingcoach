import copy
import unittest
from collections import deque

import radio_phrases
from instant_answers import instant_answer
from race_engineer import RaceEngineer
from test_race_engineer import race_fixture

LAP_S, USE = 100., 2.5


class TimedFinishTests(unittest.TestCase):
    """A timed race: the leader's first crossing after the clock runs out ends it; the player finishes at his next crossing."""

    def setUp(self):
        self.race = race_fixture()
        self.race.update(total_laps=None, session_duration_s=2700., tank_capacity_l=100.)
        self.wall = RaceEngineer()
        self.wall.ingest(self.race, 'timed', 0)
        self.wall.paces = {i: deque([LAP_S] * 3) for i in range(3)}

    def at(self, time_left, own_pct, leader_pct, fuel=30., lap=10, **snapshot):
        race = copy.deepcopy(self.race)
        race['time_remaining_s'] = time_left
        race['cars'][0].update(lap_completed=lap, distance_pct=leader_pct)          # the overall leader
        race['cars'][1].update(lap_completed=lap, distance_pct=own_pct)             # the player
        race['cars'][2].update(lap_completed=lap, distance_pct=(own_pct - .3) % 1)
        self.wall.race = race
        return {'session_time': 2700. - time_left, 'session_time_remaining_s': time_left, 'run_id': 'timed', 'lap_number': lap + 1,
                'distance_pct': own_pct, 'fuel_l': fuel, 'speed_mps': 60., 'brake': 0., 'steering_rad': 0., 'on_pit_road': False,
                'session_flags': 0, **snapshot}

    def view(self, *args, **kwargs):
        s = self.at(*args, **kwargs)
        return self.wall.view(s, USE, 3), s

    def test_laps_to_the_flag_follow_the_leader_and_the_margins_are_measured(self):
        # 950 s left. The leader is at 0.20 of his lap: 9.7 laps at the buzzer, so he finishes 30 s after it.
        # The player is at 0.50: when the leader finishes he is at 10.3, so he drives to the end of his eleventh lap from here.
        view, _ = self.view(950., .50, .20)
        self.assertEqual((view['finish_method'], view['finish_laps_to_go'], view['remaining_laps']), ('TIMED_ESTIMATE', 11, 10.5))
        self.assertEqual((view['finish_leader_margin_s'], view['finish_own_margin_s']), (30., 30.))
        self.assertFalse(view['finish_may_extend'] or view['finish_may_shorten'])
        self.assertIsNone(view['fuel_next_stop_longer_l'])

    def test_a_leader_about_to_cross_just_after_the_clock_may_add_a_lap(self):
        # The leader would cross 4 s after the clock: a little faster and he crosses before it, and the race runs a lap longer.
        view, _ = self.view(946., .20, .50)
        self.assertEqual((view['finish_leader_margin_s'], view['finish_may_extend'], view['finish_may_shorten']), (4., True, False))
        self.assertEqual(view['fuel_next_stop_longer_l'], round(view['fuel_to_add_l'] + USE, 2))

    def test_the_player_just_ahead_of_the_leader_at_the_flag_may_save_a_lap(self):
        # The player crosses 5 s before the leader takes the flag and so has a whole lap more: if the leader catches him, a lap less.
        view, _ = self.view(950., .25, .20)
        self.assertEqual((view['finish_own_margin_s'], view['finish_may_shorten'], view['finish_may_extend']), (5., True, False))
        self.assertEqual(view['finish_laps_to_go'], 11)

    def reports(self, *args, **kwargs):
        view, s = self.view(*args, **kwargs)
        return {r['key']: r for r in self.wall.candidates(view, s, {})}, view

    def test_the_last_stop_is_called_as_one_number_of_litres(self):
        reports, view = self.reports(946., .20, .50, fuel=5.5)       # two laps and a bit in the tank, 9.8 to go, half a lap in reserve
        plan = reports['pit_fuel_plan']
        self.assertEqual(view['fuel_to_add_l'], 20.25)
        # One number: the race may still run a lap longer, so that lap is in it (20.25 + 2.5, rounded up).
        self.assertEqual((plan['speech_text'], 'speech_parts' in plan), ('급유는 23리터입니다.', False))
        self.assertEqual((plan['engineer'], plan['priority']), ('strategy', 2))
        sure, _ = self.reports(950., .50, .20, fuel=5.5)
        self.assertEqual(sure['pit_fuel_plan']['speech_text'], '급유는 22리터입니다.')
        self.assertNotIn('pit_fuel_plan', self.reports(950., .50, .20, fuel=20.)[0])      # not near the stop yet

    def test_a_full_tank_is_not_given_as_a_number(self):
        self.race['tank_capacity_l'] = 30.
        reports, view = self.reports(2500., .50, .20, fuel=5.5, lap=1)
        self.assertTrue(view['fuel_next_stop_full'])
        self.assertNotIn('pit_fuel_plan', reports)

    def test_the_pit_window_opens_when_one_stop_reaches_the_flag(self):
        self.race['tank_capacity_l'] = 30.
        far, view = self.reports(2500., .50, .20, fuel=28., lap=1)
        self.assertNotIn('pit_window', far)                           # a full tank would not reach the flag yet
        self.assertTrue(view['fuel_next_stop_full'])
        near, view = self.reports(700., .50, .20, fuel=12., lap=8)
        self.assertEqual(near['pit_window']['speech_text'], '지금부터는 한 번 급유로 끝까지 갑니다.')
        self.assertEqual((view['stops_needed'], view['fuel_next_stop_full']), (1, False))

    def test_a_tank_that_only_just_reaches_must_be_run_down_before_the_stop(self):
        # 10.5 laps to go, 1.6 in the tank at mid-lap: the last possible stop is a lap and a half away and leaves
        # nine laps. A 23 litre tank holds 9.2 laps: one stop, but only from there. With the half lap of reserve
        # counted, the old sum asked for a second stop.
        self.race['tank_capacity_l'] = 23.
        reports, view = self.reports(950., .50, .20, fuel=4.)
        self.assertEqual((view['stops_needed'], view['fuel_run_dry'], view['fuel_next_stop_full']), (1, True, True))
        self.assertEqual(reports['pit_two']['speech_parts'], ['두 랩 안에 피트합니다.', '연료를 끝까지 쓰고 들어와야 합니다.'])
        # A bigger tank reaches from a lap sooner as well: nothing to add.
        self.race['tank_capacity_l'] = 30.
        reports, view = self.reports(950., .50, .20, fuel=4.)
        self.assertEqual((view['stops_needed'], view['fuel_run_dry']), (1, False))
        self.assertNotIn('speech_parts', reports['pit_two'])
        # Too small even from the last possible stop: two stops, as before.
        self.race['tank_capacity_l'] = 20.
        self.assertEqual(self.view(950., .50, .20, fuel=4.)[0]['stops_needed'], 2)

    def test_the_box_call_says_when_in_the_lap_it_applies(self):
        early, _ = self.reports(950., .05, .20, fuel=2.6)            # just over a lap left at the start of the lap
        self.assertEqual(early['fuel_critical']['speech_text'], '이번 랩 끝나고 피트합니다.')
        late, _ = self.reports(880., .80, .20, fuel=.8)
        self.assertEqual(late['fuel_critical']['speech_text'], '이번 랩에 피트 들어오세요.')

    def test_a_lap_more_is_only_reported_when_it_leaves_the_tank_short(self):
        view, s = self.view(950., .50, .20)
        self.wall.candidates(view, s, {})
        self.assertEqual(self.wall.finish_seen['said'], 21)
        view, s = self.view(930., .80, .75)                           # the leader now makes it across before the clock
        self.assertEqual(view['finish_total_laps'], 22)
        self.assertNotIn('finish_laps', {r['key'] for r in self.wall.candidates(view, s, {})})   # only just changed: wait and see
        view, s = self.view(905., .05, .0, lap=11)
        self.assertNotIn('finish_laps', {r['key'] for r in self.wall.candidates(view, s, {})})   # the tank still reaches the flag: nothing to do
        view, s = self.view(905., .05, .0, lap=11, fuel=20.)
        report = {r['key']: r for r in self.wall.candidates(view, s, {})}['finish_laps']
        self.assertEqual(report['speech_text'], '한 랩 늘었습니다. 연료가 모자랍니다.')
        self.wall.delivered(report, s['session_time'])
        view, s = self.view(880., .30, .25, lap=11, fuel=20.)
        self.assertNotIn('finish_laps', {r['key'] for r in self.wall.candidates(view, s, {})})

    def test_laps_to_go_is_answered_at_once_as_one_number(self):
        view, s = self.view(946., .20, .50)
        self.assertEqual(instant_answer('몇 랩 남았어?', {'pit_wall': view, 'snapshot': s}), ('strategy', ['10랩 남았습니다.']))   # no maybes
        view, s = self.view(950., .50, .20)
        self.assertEqual(instant_answer('몇 랩 남았어?', {'pit_wall': view, 'snapshot': s})[1], ['11랩 남았습니다.'])

    def test_every_sentence_is_prepared(self):
        prepared = {text for _, text in radio_phrases.catalog()}
        for text in ('급유는 22리터입니다.', '지금부터는 한 번 급유로 끝까지 갑니다.', '한 랩 늘었습니다. 연료가 모자랍니다.', '11랩 남았습니다.',
                     '연료를 끝까지 쓰고 들어와야 합니다.', '이번 랩 끝나고 피트합니다.', '이번 랩에 피트 들어오세요.'):
            self.assertIn(text, prepared)


if __name__ == '__main__':
    unittest.main()
