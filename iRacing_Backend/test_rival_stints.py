"""Other cars' stints and where a stop now brings us out (2026-10-14)."""
import copy
import unittest
from collections import deque

import radio_phrases
from instant_answers import instant_answer
from live_telemetry import LiveTelemetry
from race_engineer import RaceEngineer
from test_race_engineer import race_fixture
from test_straight_brief import sample


def at_lap(race, laps):
    race = copy.deepcopy(race)
    for car in race['cars']:
        car['lap_completed'] = laps
    return race


class StintTests(unittest.TestCase):
    def test_stints_are_counted_from_the_start_or_from_the_first_stop_seen(self):
        wall = RaceEngineer()
        start = at_lap(race_fixture(), 0)
        wall.ingest(start, 'run', 0)
        race = at_lap(start, 3)
        wall.ingest(race, 'run', 1)
        self.assertEqual(wall.stint(race['cars'][2]), (3, 0))
        race['cars'][2]['on_pit_road'] = True
        wall.ingest(race, 'run', 2)
        self.assertEqual(wall.stint(race['cars'][2]), (3, 1))       # in the pits: the stop is counted
        race['cars'][2]['on_pit_road'] = False
        wall.ingest(race, 'run', 3)
        race = at_lap(race, 7)
        wall.ingest(race, 'run', 4)
        self.assertEqual(wall.stint(race['cars'][2]), (4, 1))       # out on lap 3, four laps since
        self.assertEqual(wall.stint(race['cars'][1]), (7, 0))

    def test_a_car_first_seen_mid_race_is_not_guessed(self):
        wall = RaceEngineer()
        race = at_lap(race_fixture(), 12)
        wall.ingest(race, 'run', 0)
        self.assertEqual(wall.stint(race['cars'][0]), (None, None))   # joined late: nothing claimed
        race['cars'][0]['on_pit_road'] = True
        wall.ingest(race, 'run', 1)
        race['cars'][0]['on_pit_road'] = False
        wall.ingest(race, 'run', 2)
        wall.ingest(at_lap(race, 15), 'run', 3)
        self.assertEqual(wall.stint(at_lap(race, 15)['cars'][0]), (3, None))   # its stint from its stop, its stop count still unknown


class RejoinTests(unittest.TestCase):
    def setUp(self):
        self.race = race_fixture()
        self.wall = RaceEngineer()
        self.wall.ingest(self.race, 'run', 100)
        self.wall.paces = {0: deque([99] * 3), 1: deque([100] * 3), 2: deque([98] * 3)}
        self.snapshot = {'session_time': 100, 'run_id': 'run', 'lap_number': 4, 'distance_pct': .5,
                         'fuel_l': 10, 'speed_mps': 50, 'brake': 0, 'steering_rad': 0, 'on_pit_road': False}

    def view(self, **kwargs):
        return self.wall.view(self.snapshot, 2, 3, **kwargs)

    def test_the_car_a_stop_would_bring_us_out_closest_to(self):
        # The car behind (class 3rd) is about 2.0 s back: a 3-second stop puts us 1 s behind it, a 1-second one 1 s ahead.
        self.assertEqual(self.view(pit_loss_s=3)['rejoin_close'], {'idx': 2, 'class_position': 3, 'margin_s': -1.0, 'stint_laps': None})
        self.assertEqual(self.view(pit_loss_s=1)['rejoin_close']['margin_s'], 1.0)
        self.assertIsNone(self.view(pit_loss_s=30)['rejoin_close'])   # nobody near where we would come out
        self.assertIsNone(self.view()['rejoin_close'])                 # no pit loss known: nothing said

    def test_asked_where_a_stop_now_comes_out(self):
        role, parts = instant_answer('지금 피트하면 몇 위로 나와?', {'pit_wall': self.view(pit_loss_s=3), 'snapshot': {}})
        self.assertEqual((role, parts), ('strategy', ['피트하면 클래스 3위로 나옵니다.', '지금 피트하면 클래스 3위 차 바로 뒤로 나옵니다.', '차이는 1.0초입니다.']))
        self.assertIsNone(instant_answer('지금 피트하면 몇 위로 나와?', {'pit_wall': self.view(), 'snapshot': {}}))

    def test_asked_about_the_stints_and_stops_of_the_cars_around(self):
        self.race = at_lap(self.race, 10)
        self.wall.ingest(self.race, 'run', 101)
        self.wall.stints = {0: {'since': 0, 'stops': 0}, 1: {'since': 7, 'stops': 1}, 2: {'since': 4, 'stops': 2}}
        context = {'pit_wall': self.view(pit_loss_s=3), 'snapshot': {}}
        self.assertEqual(instant_answer('앞차 이번 스틴트 몇 랩 탔어?', context), ('strategy', ['앞차는 이번 스틴트 10랩째입니다.', '우리보다 7랩 더 탔습니다.']))
        self.assertEqual(instant_answer('뒤차 스틴트 몇 랩이야?', context), ('strategy', ['뒤차는 이번 스틴트 6랩째입니다.', '우리보다 3랩 더 탔습니다.']))
        self.assertEqual(instant_answer('앞차 피트 몇 번 했어?', context), ('strategy', ['앞차는 아직 피트하지 않았습니다.']))
        self.assertEqual(instant_answer('뒤차 피트 몇 번 했어?', context), ('strategy', ['뒤차는 피트를 두 번 했습니다.']))
        self.assertEqual(radio_phrases.build('stops_behind', 0, times=2)['text'], '뒤차는 피트를 두 번 했습니다.')

    def test_every_answer_is_a_prepared_sentence(self):
        prepared = {text for _, text in radio_phrases.catalog()}
        for key, values in [('rejoin_ahead_of', {'pos': 7}), ('rejoin_behind', {'pos': 12}), ('rejoin_margin', {'gap': 2.4}),
                            ('stint_that', {'laps': 18}), ('stint_ahead', {'laps': 25}), ('stint_less', {'laps': 6}),
                            ('stops_ahead', {'times': 3}), ('stops_none_behind', {})]:
            for seed in range(2):
                self.assertIn(radio_phrases.build(key, seed, **values)['text'], prepared, key)


class BriefTests(unittest.TestCase):
    def setUp(self):
        self.live = LiveTelemetry(clock=lambda: 1000.)
        self.race = at_lap(race_fixture(), 10)
        self.race['total_laps'] = 40
        self.live.radio.ingest(sample(0), 'run', 1000., {'session_type': 'Race'})
        self.live.pit_wall.ingest(self.race, 'run', 0)
        self.live.pit_wall.paces = {i: deque([100.] * 3) for i in range(3)}
        self.live.pit_wall.stints = {0: {'since': 0, 'stops': 0}, 1: {'since': 7, 'stops': 1}, 2: {'since': 8, 'stops': 1}}

    def options(self):
        found, _ = self.live._brief_options(sample(0, lap_completed=10), 12.)
        return {name: groups for name, _, groups in found}

    def test_a_much_longer_or_shorter_stint_of_the_car_ahead_or_behind_is_said_once(self):
        found = self.options()
        self.assertEqual(found['stint_ahead'], [[('stint_ahead', {'laps': 10}), ('stint_more', {'laps': 7})]])
        self.assertNotIn('stint_behind', found)                          # one lap apart: not worth a line
        self.live._briefed['stint_ahead_told'] = (0, 0, False)          # what was said for this stint of that car
        self.assertNotIn('stint_ahead', self.options())

    def test_where_a_stop_now_comes_out_is_offered_in_the_last_laps_of_fuel(self):
        view = {'active': True, 'stops_needed': 1, 'fuel_laps': 2.2, 'own_stint_laps': 20,
                'rejoin_close': {'idx': 2, 'class_position': 6, 'margin_s': 1.4, 'stint_laps': 9}}
        self.live.pit_wall.view = lambda _sample: view
        self.assertEqual(self.options()['rejoin'], [[('rejoin_ahead_of', {'pos': 6}), ('rejoin_margin', {'gap': 1.4})],
                                                    [('stint_that', {'laps': 9})]])
        view['fuel_laps'] = 9.                                           # the stop is still far off
        self.assertNotIn('rejoin', self.options())


if __name__ == '__main__':
    unittest.main()
