"""Task-free observations from synthetic inputs; never the user's game or store."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from overlay_automatic import AutomaticDrivingCoach
from overlay_control import OverlayController
from overlay_studio import CoachingDashboard, StudioStore, defaults, select_modules, LEGACY_PRESETS


class AutomaticTests(unittest.TestCase):
    def view(self, at, brake=0., throttle=1., **extra):
        return {'connected': True, 'run_id': 'synthetic-run', 'session': {'kind': 'PRACTICE'},
                'corner_zones': [], 'practice': None,
                'snapshot': {'session_time': at, 'brake': brake, 'throttle': throttle,
                             'speed_mps': 30., 'on_track': True, 'on_pit_road': False,
                             'is_replay': False, 'incidents': 0, **extra}}

    def drive(self, coach, start, end, brake, throttle):
        result = None
        for tick in range(round(start*30), round(end*30)):
            result = coach.build(self.view(tick/30, brake, throttle))
        return result

    def test_no_task_no_reference_no_corners_produces_real_values_in_both_games(self):
        for game in ('iracing', 'lmu'):
            coach = CoachingDashboard()
            for tick in range(65):
                view = self.view(tick/30, 0., 0.)
                view['identity'] = {'game': game}
                result = coach.build(view)
            automatic = result['items']['live']
            self.assertTrue(automatic['available'])
            self.assertGreater(float(automatic['metric']), 1.)
            self.assertEqual(select_modules(defaults(), 'practice', result)['ids'], ['live'])
            self.assertFalse(result['items']['goal']['available'])

    def test_release_to_throttle_uses_observed_threshold_times(self):
        coach = AutomaticDrivingCoach()
        self.drive(coach, 0, 1, .5, 0)
        self.drive(coach, 1, 1.6, 0, 0)
        result = self.drive(coach, 1.6, 2.5, 0, 1)
        self.assertIn('가속 연결', result['title'])
        self.assertAlmostEqual(float(result['metric']), .6, places=2)

    def test_joining_mid_coast_does_not_invent_a_brake_release(self):
        coach = AutomaticDrivingCoach()
        self.drive(coach, 0, 1.6, 0, 0)
        result = self.drive(coach, 1.6, 2.5, 0, 1)
        self.assertIsNone(coach.connection)
        self.assertIn('무입력 관측', result['title'])

    def test_short_noise_does_not_become_a_release_or_acceleration_event(self):
        coach = AutomaticDrivingCoach()
        self.drive(coach, 0, 1, .5, 0)
        self.drive(coach, 1, 1.1, 0, 0)
        self.drive(coach, 1.1, 2, .5, 0)
        self.drive(coach, 2, 3, .5, 1)
        self.assertIsNone(coach.connection)

    def test_gap_pit_replay_yellow_close_car_incident_and_stop_reset_observations(self):
        for patch in ({'on_pit_road': True}, {'is_replay': True}, {'session_flags': 8},
                      {'car_left_right': 2}, {'incidents': 1}, {'speed_mps': 0.}):
            coach = AutomaticDrivingCoach()
            self.drive(coach, 0, 2, 0, 0)
            result = coach.build(self.view(2., **patch))
            self.assertFalse(result['available'], patch)
            self.assertFalse(coach.intervals, patch)
        coach = AutomaticDrivingCoach()
        self.drive(coach, 0, 2, 0, 0)
        result = coach.build(self.view(5., 0, 0))
        self.assertEqual(result['metric'], '입력 수신')
        self.assertFalse(coach.intervals)

    def test_teleport_and_new_run_do_not_bridge_measurements(self):
        coach = AutomaticDrivingCoach()
        coach.build(self.view(0, distance_pct=.2, lap_number=1))
        coach.build(self.view(.1, distance_pct=.7, lap_number=1))
        self.assertFalse(coach.intervals)
        view = self.view(.2, 0, 0); view['run_id'] = 'new-run'
        self.assertEqual(coach.build(view)['metric'], '입력 수신')
        self.assertFalse(coach.intervals)

    def test_rolling_window_is_bounded_and_polling_duplicate_does_not_add_time(self):
        coach = AutomaticDrivingCoach()
        result = self.drive(coach, 0, 120, .5, .5)
        # The display cache prunes at 250 ms intervals (at most 8 more ticks).
        self.assertLessEqual(len(coach.intervals), 310)
        self.assertEqual(result['stats'][-1][1], '10.00s')
        count = len(coach.intervals)
        for _ in range(10): coach.build(self.view(119+29/30, .5, .5))
        self.assertEqual(len(coach.intervals), count)

    def test_formation_has_no_pedal_judgement_and_auto_choice_can_be_removed(self):
        coach = AutomaticDrivingCoach(); view = self.view(0)
        view['session'] = {'kind': 'RACE'}; view['snapshot']['session_state'] = 3
        result = coach.build(view)
        self.assertEqual(result['metric'], '그린 후 관측')
        self.assertFalse(coach.intervals)
        config = defaults(); config['selected']['practice'] = []
        dashboard = CoachingDashboard().build(self.view(0))
        self.assertEqual(select_modules(config, 'practice', dashboard)['ids'], [])
        stopped = CoachingDashboard().build(self.view(0, speed_mps=0))
        self.assertEqual(select_modules(defaults(), 'practice', stopped)['ids'], ['live'])

    def test_saved_default_migration_is_read_only_and_custom_choices_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StudioStore(Path(directory)/'overlay.json')
            old = defaults(); old['slots'] = deepcopy(LEGACY_PRESETS)
            old['selected'] = {key: [v for v in values if v] for key, values in LEGACY_PRESETS.items()}
            store.save(old)
            before = store.path.read_bytes()
            self.assertIn('live', store.load()['selected']['practice'])
            self.assertEqual(before, store.path.read_bytes())
            old['selected']['practice'] = ['goal']
            store.save(old)
            self.assertEqual(store.load()['selected']['practice'], ['goal'])
            old['selected']['practice'] = []
            store.save(old)
            self.assertEqual(store.load()['selected']['practice'], [])

    def test_status_distinguishes_unknown_and_empty_geometry_and_has_no_new_store_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = OverlayController(Path(directory)/'overlay.json')
            self.assertIsNone(controller.status()['content_readiness']['corner_count'])
            controller.latest = self.view(1)
            self.assertEqual(controller.status()['content_readiness'], {'connected': True, 'corner_count': 0})
            controller.latest['corner_zones'] = [{}, {}]
            self.assertEqual(controller.status()['content_readiness']['corner_count'], 2)
            self.assertFalse(list(Path(directory).rglob('*.json')))


if __name__ == '__main__':
    unittest.main()
