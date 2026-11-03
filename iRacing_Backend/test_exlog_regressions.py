"""Minimal real exported observations: no external paths, DBs, audio or full SDK replay."""
import unittest

from race_engineer import RaceEngineer
from race_radio import RaceRadio, STATIC_CALLS
from race_result import RaceResultTracker

from exlog_regression_fixtures import CASES as FIXTURE


class ObservedRegressionTests(unittest.TestCase):
    def test_finish_rank_changes_including_cooldown_stay_provisional_and_audio_neutral(self):
        for case in FIXTURE['finish']:
            with self.subTest(case=case['case']):
                tracker, radio = RaceResultTracker(), RaceRadio()
                race = {'game': 'iracing', 'session_type': 'Race', 'player_idx': 0,
                        'cars': [{'idx': 0, 'class_id': 1}]}
                radio.run_id, radio.metadata = 'observed-fixture', race
                previous, identifier = None, None
                for row in case['rows']:
                    sample = {**row, 'session_num': 0, 'on_track': True, 'is_replay': False}
                    tracker.ingest(sample, race, radio.run_id, sample['session_time'])
                    if previous is not None:
                        radio._results(sample, previous, sample['session_time'], race)
                        events = [e for e in radio.pending if e['key'] == 'race_result']
                        self.assertEqual(len(events), 1)
                        event = events[0]
                        identifier = identifier or event['id']
                        self.assertEqual(event['id'], identifier)
                        self.assertIn(event['spoken_text'], STATIC_CALLS['finish'])
                        self.assertTrue(event['prepared_only'])
                    previous = sample
                self.assertEqual(tracker.view()['finish_position'], case['expected_live_rank'])
                self.assertEqual(radio.result['class_position'], case['expected_live_rank'])
                self.assertTrue(tracker.view()['provisional'])
                self.assertTrue(radio.result['provisional'])

    def test_observed_fuel_choices_remain_valid_and_goal_reserve_is_separate(self):
        # Exported remaining distance is rounded. Project it onto a fixed 10-lap
        # scenario to exercise fuel math without inventing a timed-race SDK replay.
        for observation in FIXTURE['fuel_alternatives']['observations']:
            observed = observation['view']
            with self.subTest(key=observation['key']):
                wall = RaceEngineer()
                wall.race = {'game': 'lmu', 'session_type': 'Race', 'state': 4, 'total_laps': 10,
                    'player_idx': 0, 'tank_capacity_l': 100,
                    'cars': [{'idx': 0, 'class_id': 1, 'class_position': 1, 'position': 1,
                              'lap_completed': 0, 'distance_pct': 10 - observed['remaining_laps'], 'last_lap_s': 100}]}
                sample = {'fuel_l': observed['fuel_l'], 'distance_pct': 10 - observed['remaining_laps'],
                          'lap_number': 1, 'session_time': 10}
                finish = wall.view(sample, observed['fuel_l_per_lap'], 1, reserve_laps=.5, fuel_save_mode='finish')
                reserve = wall.view(sample, observed['fuel_l_per_lap'], 1, reserve_laps=.5, fuel_save_mode='reserve')
                self.assertAlmostEqual(finish['fuel_finish_shortfall_l'], observed['fuel_finish_shortfall_l'], delta=.01)
                self.assertAlmostEqual(finish['fuel_to_add_l'], observed['fuel_to_add_l'], delta=.01)
                self.assertEqual(finish['fuel_observed_laps'], 1)
                self.assertEqual(finish['stops_needed'], 1)
                self.assertGreater(reserve['fuel_goal_save_pct'], finish['fuel_goal_save_pct'])
                self.assertEqual(reserve['fuel_to_add_l'], finish['fuel_to_add_l'])


if __name__ == '__main__':
    unittest.main()
