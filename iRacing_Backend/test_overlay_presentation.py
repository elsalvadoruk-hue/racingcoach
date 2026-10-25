from copy import deepcopy
import unittest

from overlay_presentation import CoachingPresentation
from overlay_studio import defaults


class PresentationTests(unittest.TestCase):
    def setUp(self):
        self.coach = CoachingPresentation()
        self.config = defaults()
        self.config['selected']['practice'] = ['score', 'live', 'pattern']
        self.view = {'connected': True, 'run_id': 'synthetic', 'snapshot': {
            'session_time': 0., 'distance_pct': .1, 'speed_mps': 50., 'on_track': True,
            'on_pit_road': False, 'is_replay': False, 'brake': 0., 'throttle': 1.,
            'steering_rad': 0., 'car_left_right': 0, 'session_flags': 0, 'lap_number': 2}}
        self.dashboard = {'reference': {'source': 'GARAGE61', 'lap_id': 'synthetic', 'lap_time_s': 97.607},
                          'items': {'live': {'title': '자동 관측', 'available': True, 'metric': '0.60'},
                                    'score': {'title': 'C1', 'available': True, 'metric': '+0.100'}}}
        self.at = 0.

    def tick(self, duration=.1, **values):
        count = round(duration/.1)
        for _ in range(count):
            self.at = round(self.at+.1, 6)
            self.view['snapshot'].update(session_time=self.at, **values)
            self.result = self.coach.build(self.view, self.dashboard, self.config, 'practice')
        return self.result

    def corner(self, name, delta):
        self.view['corner_pass'] = {'id': name, 'call': name, 'lap': 2, 'exit_at': self.at, 'clean': True}
        self.view['feedback'] = {'corner_id': name, 'delta_s': delta}
        self.dashboard['items']['score'].update(title=name, metric=f'{delta:+.3f}')
        return self.tick(.1, brake=.3, throttle=0.)

    def test_three_linked_corners_show_middle_largest_delta_after_straight(self):
        for name, delta in [('C1', .1), ('C2', .7), ('C3', .2)]:
            self.corner(name, delta)
            self.assertEqual(self.tick(1.5, brake=0., throttle=1.)['state'], 'waiting')
        result = self.tick(2.2)
        self.assertEqual(result['items']['score']['title'], 'C2')
        self.assertEqual(result['group_count'], 3)
        frozen = deepcopy(result['items'])
        self.dashboard['items']['score']['metric'] = '+9.999'
        self.assertEqual(self.tick(5.5)['items'], frozen)
        self.assertEqual(self.dashboard['items']['score']['metric'], '+9.999')

    def test_short_known_straight_does_not_flash_and_next_corner_hides_early(self):
        self.corner('C1', .2)
        self.assertEqual(self.tick(4., brake=0., throttle=1., workload_in_s=5.)['state'], 'waiting')
        result = self.tick(.1, workload_in_s=15.)
        self.assertEqual(result['state'], 'holding')
        self.assertEqual(self.tick(.1, workload_in_s=1.)['state'], 'busy')

    def test_unknown_forecast_first_drive_has_stable_automatic_observation(self):
        self.config['selected']['practice'] = ['live']
        result = self.tick(2.2)
        self.assertEqual(result['items']['live']['metric'], '0.60')
        self.dashboard['items']['live']['metric'] = '0.90'
        self.assertEqual(self.tick(7.7)['items']['live']['metric'], '0.60')
        self.assertEqual(self.tick(.3)['items']['live']['metric'], '0.90')

    def test_pit_replay_run_reference_selection_and_gap_clear_frozen_data(self):
        for change in ('pit', 'replay', 'run', 'reference', 'source', 'task', 'selection', 'gap'):
            with self.subTest(change=change):
                self.setUp(); self.corner('C1', .3); self.tick(2.2, brake=0., throttle=1.)
                self.view.pop('corner_pass'); self.view.pop('feedback')
                if change == 'pit': self.view['snapshot']['on_pit_road'] = True
                if change == 'replay': self.view['snapshot']['is_replay'] = True
                if change == 'run': self.view['run_id'] = 'new'
                if change == 'reference': self.dashboard['reference']['lap_id'] = 'new'
                if change == 'source': self.config['source'] = 'personal'
                if change == 'task': self.view['practice'] = {'scope': 'new-task'}
                if change == 'selection': self.config['selected']['practice'] = []
                if change == 'gap': self.at += 1.
                self.tick()
                self.assertIsNone(self.coach.active)
                self.assertFalse(self.coach.pending)

    def test_lap_only_waits_for_lap_and_survives_six_second_measurement_phase(self):
        self.config.update(timing='lap'); self.config['selected']['practice'] = ['summary']
        self.dashboard['items']['summary'] = {'available': True, 'metric': '2 / 3'}
        self.corner('C1', .4)
        self.assertFalse(self.coach.pending)
        self.view['session_events'] = {'lap': {'at': self.at}}
        self.tick(7., brake=.3)
        self.dashboard['lap_result'] = False
        result = self.tick(2.2, brake=0., throttle=1.)
        self.assertEqual(result['items']['summary']['metric'], '2 / 3')

    def test_interrupted_card_joins_new_corner_group_and_unchecked_score_never_appears(self):
        self.corner('C1', .1); self.tick(2.2, brake=0., throttle=1.)
        self.corner('C2', .8)
        result = self.tick(8., brake=0., throttle=1.)
        self.assertEqual(result['items']['score']['title'], 'C2')
        self.config['selected']['practice'] = ['live']
        result = self.tick(2.2)
        self.assertNotIn('score', result['items'])

    def test_wrapped_corner_zone_and_stale_reference_clearance(self):
        self.view.update(track_length_m=5000., corner_zones=[{'entry_pct': .98, 'exit_pct': .02}])
        self.view['snapshot']['distance_pct'] = .99
        self.assertEqual(self.tick(2.2)['state'], 'busy')
        self.view['snapshot']['distance_pct'] = .05
        self.view['card_window'] = {'at': -1, 'lap': 2, 'clear_s': 0.}
        self.assertEqual(self.tick(2.2)['state'], 'holding')

    def test_pending_and_dedup_are_bounded(self):
        for index in range(200):
            self.corner(str(index), .1)
        self.assertLessEqual(len(self.coach.pending), 32)
        self.assertLessEqual(len(self.coach.seen), 64)


if __name__ == '__main__':
    unittest.main()
