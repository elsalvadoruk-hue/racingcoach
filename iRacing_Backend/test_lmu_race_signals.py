"""Preserve measured race inputs without turning absent data into a signal."""
import unittest

from lmu_frame import normalise as _normalise
from lmu_race_signals import normalise, scoring_cars, yellow_signal, last_lap_signal


class RaceSignalTests(unittest.TestCase):
    def test_local_yellow_is_independent_from_primary_and_fcy_flags(self):
        s = {'primary_flag': 0, 'yellow_flag_state': 0, 'under_yellow': False,
             'sector_flags': [0, 1, 0]}
        self.assertIs(yellow_signal(s), True)
        s['sector_flags'] = [0, 0, 0]
        self.assertIs(yellow_signal(s), False)
        s['sector_flags'] = [0, None, -1]
        self.assertIsNone(yellow_signal(s))
        s['sector_flags'] = [None, 1, -1]
        self.assertIs(yellow_signal(s), True)
        s.update(under_yellow=True, sector_flags=None)
        self.assertIs(yellow_signal(s), True)

    def test_white_flag_has_exact_lap_limit_evidence_only(self):
        s = {'session_type_code': 10, 'session_phase': 5, 'finish_status': 0,
             'session_max_laps': 4, 'total_laps': 2}
        self.assertIs(last_lap_signal(s), False)
        s['total_laps'] = 3
        self.assertIs(last_lap_signal(s), True)
        s['session_phase'] = 6
        s['yellow_flag_state'] = 5
        self.assertIs(last_lap_signal(s), False)
        s.update(session_phase=5, session_max_laps=None, session_remaining_s=50, best_lap_time=117)
        self.assertIsNone(last_lap_signal(s))
        for key, value in [('session_type_code', 8), ('session_max_laps', True),
                           ('total_laps', True), ('session_max_laps', 2147483647)]:
            with self.subTest(key=key):
                original = {**s, 'session_max_laps': 4}
                original[key] = value
                self.assertIsNone(last_lap_signal(original))

    def test_sector_yellow_survives_green_primary_flag_and_no_full_course_yellow(self):
        # Native SDK uses mFlag=0 even while a sector has a local yellow.
        # The older diagnostic captures do not contain this array: absent stays
        # unknown instead of becoming three clear sectors.
        result = _normalise({'primary_flag': 0, 'yellow_flag_state': 0,
                             'under_yellow': False, 'sector_flags': [0, 1, 0], 'sector': 2})
        self.assertEqual(result['sector_flags'], [0, 1, 0])
        self.assertEqual(result['primary_flag'], 0)
        self.assertEqual(result['yellow_flag_state'], 0)
        self.assertEqual(result['sector'], 2)
        self.assertIsNone(_normalise({'primary_flag': 0})['sector_flags'])

    def test_sector_unknowns_are_not_fabricated_as_green(self):
        self.assertEqual(normalise({'sector_flags': [-1, None, 1]})['sector_flags'], [-1, None, 1])
        self.assertEqual(normalise({'sector_flags': [True, 8, '1']})['sector_flags'], [None] * 3)
        for value in (None, [], [0, 0], [0, 0, 0, 0], '001', {0: 1}):
            with self.subTest(value=value):
                self.assertIsNone(normalise({'sector_flags': value})['sector_flags'])
        for value in (True, -1, 3, '1'):
            self.assertIsNone(normalise({'sector': value})['sector'])

    def test_real_zero_values_and_unknowns_remain_distinct(self):
        result = normalise({'brake_bias': 0, 'tc_setting': 0, 'abs_setting': 0,
                            'front_tyre_wet': False, 'overheating': False,
                            'yaw_rate': -.13, 'steering_rad': None})
        self.assertEqual(result['brake_bias'], 0)
        self.assertEqual(result['tc_setting'], 0)
        self.assertIs(result['front_tyre_wet'], False)
        self.assertIs(result['overheating'], False)
        self.assertEqual(result['yaw_rate'], -.13)
        self.assertIsNone(result['steering_rad'])
        self.assertIsNone(result['rear_tyre_wet'])
        self.assertIsNone(result['scoring_cars'])

    def test_invalid_values_do_not_become_flags_or_control_measurements(self):
        result = normalise({'brake_bias': 101, 'tc_setting': True, 'abs_setting': -1,
                            'front_tyre_wet': 1, 'overheating': 'false',
                            'yaw_rate': float('nan'), 'steering_rad': float('inf'),
                            'class_id': 0, 'last_impact_et': 10 ** 1000})
        self.assertTrue(all(value is None for value in result.values()))

    def test_scoring_join_is_bounded_and_ambiguous_ids_withdraw_entire_grid(self):
        self.assertIsNone(scoring_cars([{'vehicle_id': 1}] * 2))
        self.assertIsNone(scoring_cars([{'vehicle_id': True}]))
        self.assertIsNone(scoring_cars([{'vehicle_id': -1}]))
        self.assertIsNone(scoring_cars([{'vehicle_id': i} for i in range(105)]))
        self.assertIsNone(scoring_cars([None]))
        self.assertEqual(scoring_cars([]), [])
        self.assertEqual(len(scoring_cars([{'vehicle_id': i} for i in range(104)])), 104)

    def test_live_normalization_retains_required_inputs_and_drops_private_fields(self):
        result = _normalise({'session_id': 'process:1', 'car_name': 'Example GT3 #32',
                             'track_name': 'Monza', 'vehicle_model': 'Example GT3',
                             'driver_name': 'private', 'brake_bias': 51.25,
                             'scoring_cars': [{'vehicle_id': 77, 'class_id': 300,
                                              'class_position': 2, 'overall_position': 3,
                                              'last_lap_time': 115.6, 'total_laps': 5,
                                              'speed_mps': 65, 'in_pits': False,
                                              'driver_name': 'private', 'steam_id': 'private'}]})
        self.assertEqual(result['car_name'], 'Example GT3 #32')
        self.assertEqual(result['track_name'], 'Monza')
        self.assertEqual(result['brake_bias'], 51.25)
        row = result['scoring_cars'][0]
        self.assertEqual((row['vehicle_id'], row['class_position'], row['last_lap_time']), (77, 2, 115.6))
        self.assertIs(row['in_pits'], False)
        self.assertNotIn('driver_name', result)
        self.assertNotIn('driver_name', row)
        self.assertNotIn('steam_id', row)

    def test_track_labels_cannot_smuggle_control_characters_or_unbounded_data(self):
        for value in ('x' * 65, 'Monza\nprivate', '', None):
            with self.subTest(value=value):
                self.assertIsNone(normalise({'track_name': value})['track_name'])

    def test_invalid_scoring_measurements_stay_unknown(self):
        result = scoring_cars([{'vehicle_id': 1, 'class_position': 0, 'in_pits': 0,
                                'last_lap_time': 0, 'speed_mps': float('nan'),
                                'lap_distance_m': float('inf'), 'track_edge_m': '5',
                                'class_id': True}])[0]
        self.assertTrue(all(value is None for name, value in result.items() if name != 'vehicle_id'))


if __name__ == '__main__':
    unittest.main()
