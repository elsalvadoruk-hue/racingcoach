"""Own final-lap announcements must not use the leader's white flag alone."""
import unittest

from race_engineer import RaceEngineer


def race_and_sample(*, own_lap=10, own_pct=.874, leader_lap=11, leader_pct=.18, time_left=31.6, total=None):
    cars = [{'idx': idx, 'class_id': 10, 'position': idx + 1, 'class_position': idx + 1,
             'lap_completed': lap, 'distance_pct': pct, 'last_lap_s': 61.3,
             'on_pit_road': False, 'track_surface': 3}
            for idx, lap, pct in ((0, leader_lap, leader_pct), (1, own_lap, own_pct))]
    race = {'game': 'iracing', 'session_type': 'Race', 'state': 4, 'flags': 2, 'total_laps': total,
            'time_remaining_s': time_left, 'session_duration_s': 720., 'player_idx': 1,
            'tank_capacity_l': 45., 'cars': cars}
    sample = {'session_time': 785.7, 'lap_completed': own_lap, 'lap_number': own_lap + 1,
              'distance_pct': own_pct, 'session_time_remaining_s': time_left, 'fuel_l': 20.,
              'on_pit_road': False, 'session_state': 4, 'session_flags': 2}
    wall = RaceEngineer()
    wall.race = race
    wall.paces = {0: [61.1] * 3, 1: [61.3] * 3}
    return wall, sample


def calls(wall, sample, view=None):
    view = wall.view(sample, .5, 3) if view is None else view
    return {c['key']: c for c in wall.candidates(view, sample, {})}


class LastLapCallTests(unittest.TestCase):
    def test_leaders_white_waits_until_the_players_own_final_lap(self):
        wall, sample = race_and_sample()
        view = wall.view(sample, .5, 3)
        self.assertEqual((view['finish_total_laps'], view['finish_laps_to_go']), (12, 2))
        self.assertNotIn('last_lap', calls(wall, sample, view))
        sample.update(lap_completed=11, lap_number=12, distance_pct=.001)
        self.assertIn('last_lap', calls(wall, sample, view))

    def test_rounded_one_lap_is_still_two_own_crossings(self):
        wall, sample = race_and_sample(own_pct=.99996)
        view = wall.view(sample, .5, 3)
        self.assertEqual(view['remaining_laps'], 1.0)
        self.assertEqual(view['finish_laps_to_go'], 2)
        self.assertNotIn('last_lap', calls(wall, sample, view))

    def test_global_white_with_unknown_own_finish_is_not_a_final_lap_claim(self):
        wall, sample = race_and_sample(time_left=None)
        wall.paces.clear()
        self.assertIsNone(wall.view(sample)['remaining_laps'])
        self.assertNotIn('last_lap', calls(wall, sample))

    def test_exact_lap_limit_needs_no_leader_pace(self):
        wall, sample = race_and_sample(own_lap=11, own_pct=.05, total=12, time_left=None)
        wall.paces.clear()
        self.assertIn('last_lap', calls(wall, sample))
        sample.update(lap_completed=10, lap_number=11, distance_pct=.99999)
        wall.race['cars'][1].update(lap_completed=10, distance_pct=.99999)
        self.assertNotIn('last_lap', calls(wall, sample))

    def test_lapped_multiclass_uses_own_finish_crossing_not_the_race_lap_limit(self):
        wall, sample = race_and_sample(own_lap=6, own_pct=.3, leader_lap=11, leader_pct=.7, total=12, time_left=None)
        wall.race['cars'][0]['class_id'] = 99
        wall.paces = {0: [60.] * 3, 1: [120.] * 3}
        view = wall.view(sample, .5, 3)
        self.assertEqual((view['finish_method'], view['remaining_laps']), ('LEADER_FINISH_ESTIMATE', .7))
        self.assertIn('last_lap', calls(wall, sample, view))

    def test_own_native_lmu_white_needs_no_pace_estimate(self):
        wall, sample = race_and_sample(time_left=None)
        wall.race['game'] = 'lmu'
        wall.paces.clear()
        view = wall.view(sample)
        view['game'] = 'lmu'
        self.assertIn('last_lap', calls(wall, sample, view))

    def test_unknown_game_cannot_assign_the_meaning_of_white(self):
        wall, sample = race_and_sample(own_lap=11, total=12, time_left=None)
        wall.race.pop('game')
        self.assertNotIn('last_lap', calls(wall, sample))

    def test_timed_endpoint_that_may_extend_cannot_claim_the_final_lap(self):
        wall, sample = race_and_sample(own_lap=11, total=12, time_left=None)
        view = wall.view(sample)
        view.update(finish_method='TIMED_ESTIMATE', finish_may_extend=True)
        self.assertNotIn('last_lap', calls(wall, sample, view))

    def test_current_overall_leaders_white_is_native_even_without_pace(self):
        wall, sample = race_and_sample(time_left=None)
        wall.paces.clear()
        sample['position'] = 1
        self.assertIn('last_lap', calls(wall, sample))

    def test_no_white_or_finished_session_never_adds_last_lap(self):
        wall, sample = race_and_sample(own_lap=11, total=12, time_left=None)
        sample['session_flags'] = 0
        self.assertNotIn('last_lap', calls(wall, sample))
        sample['session_flags'] = 3
        self.assertNotIn('last_lap', calls(wall, sample))

    def test_invalid_completed_lap_cannot_establish_an_own_final_lap(self):
        wall, sample = race_and_sample(own_lap=11, total=12, time_left=None)
        view = wall.view(sample)
        for completed in (None, True, -1, 11.2, float('nan')):
            with self.subTest(completed=completed):
                self.assertNotIn('last_lap', calls(wall, {**sample, 'lap_completed': completed}, view))

    def test_completed_finish_cannot_replay_a_stale_final_lap_view(self):
        wall, sample = race_and_sample(own_lap=11, total=12, time_left=None)
        view = wall.view(sample)
        self.assertNotIn('last_lap', calls(wall, {**sample, 'lap_completed': 12, 'lap_number': 13}, view))
        self.assertNotIn('last_lap', calls(wall, {**sample, 'lap_completed': 12, 'position': 1}, view))
        self.assertNotIn('last_lap', calls(wall, {**sample, 'session_flags': 3}, view))
        self.assertNotIn('last_lap', calls(wall, {**sample, 'session_state': 6}, view))

    def test_last_lap_wording_and_priority_are_preserved(self):
        wall, sample = race_and_sample(own_lap=11, total=12, time_left=None)
        view = wall.view(sample)
        base = calls(wall, sample, view)['last_lap']
        close = {**view['ahead'], 'gap_estimate_s': .8}
        defend = calls(wall, sample, {**view, 'behind': close})['last_lap']
        attack = calls(wall, sample, {**view, 'ahead': close})['last_lap']
        self.assertEqual(base['priority'], 2)
        self.assertIn('마지막 랩', base['spoken_text'])
        self.assertIn('뒤차가 바로 뒤', defend['spoken_text'])
        self.assertIn('앞차가 바로 앞', attack['spoken_text'])


if __name__ == '__main__':
    unittest.main()
