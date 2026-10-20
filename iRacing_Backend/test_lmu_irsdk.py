"""LMU frames presented as the iRacing SDK, and the shared live coach on top."""
import json
import os
import tempfile
import unittest

os.environ.setdefault('IRACING_TUNER_DB_PATH', os.path.join(tempfile.mkdtemp(), 'lmu-irsdk.sqlite3'))

from game_sdk import AutoGameSdk
from lmu_irsdk import LmuIrsdk, nearby_traffic, session_type
from lmu_journal import FrameJournal

IDENTITY = [[1., 0., 0.], [0., 1., 0.], [0., 0., 1.]]


def car(vehicle_id, place, distance, *, laps=3, pits=False, lateral=0., edge=6., speed=50.):
    return {'vehicle_id': vehicle_id, 'class_id': 7, 'overall_position': place, 'class_position': place,
            'total_laps': laps, 'finish_status': 0, 'control_code': 0 if vehicle_id == 0 else 1,
            'in_pits': pits, 'sector': 1, 'pit_stops': 0, 'lap_distance_m': distance,
            'path_lateral_m': lateral, 'track_edge_m': edge, 'last_lap_time': 101.2, 'best_lap_time': 100.5,
            'time_behind_next_s': 1., 'time_behind_leader_s': place - 1., 'lap_start_et': 10.,
            'speed_mps': speed, 'laps_behind_next': 0, 'laps_behind_leader': 0}


def frame(et=100., *, phase=5, session=10, speed=50., others=(), flag=0, yellow=0, sectors=(0, 0, 0),
          steering=.1, scoring=True, distance=1000., session_id='session', laps=3, **extra):
    raw = {
        'game_version': 14200, 'events': [], 'session_id': session_id, 'vehicle_id': 0,
        'telemetry_updated': True, 'scoring_updated': True, 'telemetry_et': et, 'scoring_et': et,
        'car_name': 'Team WRT 2026 #32:LM', 'vehicle_model': 'BMW M4 GT3', 'track_name': 'Monza',
        'lap_start_et': et - 30, 'speed_mps': speed, 'throttle_ratio': .8, 'brake_ratio': 0.,
        'steering_ratio': steering, 'steering_rad': steering * 4, 'gear': 4, 'engine_rpm': 7000.,
        'fuel_l': 60., 'fuel_capacity_l': 120., 'realtime': True, 'paused': False, 'in_garage_stall': False,
        'abs_active': False, 'tc_active': False, 'speed_limiter_active': False, 'lap_invalidated': False,
        'overheating': False, 'parts_detached': False, 'brake_bias': 56., 'abs_setting': 5, 'tc_setting': 3,
        'ambient_temp_c': 20., 'track_temp_c': 30., 'rain_ratio': 0., 'track_wetness_ratio': 0.,
        'front_tyre_wet': False, 'rear_tyre_wet': False,
    }
    if scoring:
        raw.update({'total_laps': laps, 'last_lap_time': 101.2, 'best_lap_time': 100.5,
                    'lap_distance_m': distance, 'track_length_m': 5000., 'session_type_code': session,
                    'session_phase': phase, 'session_remaining_s': 600., 'session_end_et': 600.,
                    'overall_position': 2, 'class_position': 2, 'class_vehicle_count': 10, 'vehicle_count': 10,
                    'control_code': 0, 'primary_flag': flag, 'yellow_flag_state': yellow,
                    'sector_flags': list(sectors), 'sector': 1, 'pit_state': 0, 'finish_status': 0,
                    'under_yellow': False, 'in_pits': False, 'count_lap_flag': 2, 'start_light': 0,
                    'start_red_lights': 5, 'outstanding_penalties': 0, 'class_id': 7,
                    'scoring_cars': [car(0, 2, distance, laps=laps), car(5, 1, distance + 300, laps=laps)]})
    raw['traffic'] = {'status': 'AVAILABLE', 'source': 'TELEMETRY', 'complete': True,
                      'player': {'vehicle_id': 0, 'telemetry_et': et, 'position_m': [0., 0., 0.],
                                 'orientation': IDENTITY},
                      'vehicles': [{'vehicle_id': vid, 'telemetry_et': et - age, 'position_m': list(pos),
                                    'in_pits': False, 'control_code': 1} for vid, pos, age in others]}
    raw.update(extra)
    return raw


class Source:
    def __init__(self, frames):
        self.frames = list(frames)

    def open(self):
        return {}

    def read(self, timeout_ms=0):
        return self.frames.pop(0) if self.frames else None

    def close(self):
        pass


def adapter(frames, journal_dir=None):
    now = [0.]
    sdk = LmuIrsdk(source_factory=lambda: Source(frames), clock=lambda: now[0],
                   journal=FrameJournal(journal_dir, clock=lambda: now[0]))
    return sdk, now


def read_all(sdk, now, count, step=1 / 30):
    sdk.startup()
    for _ in range(count):
        now[0] += step
        sdk.freeze_var_buffer_latest()
    return sdk


class TranslationTests(unittest.TestCase):
    def test_driving_inputs_and_session_info_use_iracing_names(self):
        sdk = read_all(*adapter([frame()]), 1)
        self.assertTrue(sdk.is_connected)
        self.assertEqual(sdk['Speed'], 50.)
        self.assertAlmostEqual(sdk['SteeringWheelAngle'], -.4)  # LMU right-positive -> iRacing left-positive
        self.assertEqual(sdk['Lap'], 4)
        self.assertEqual(sdk['LapCompleted'], 3)
        self.assertAlmostEqual(sdk['LapDistPct'], .2)
        self.assertTrue(sdk['IsOnTrack'])
        self.assertEqual(sdk['SessionState'], 4)
        self.assertEqual(sdk['SessionFlags'] & 0x4, 0x4)
        self.assertEqual(sdk['CarIdxLapDistPct'][5], (1300 / 5000))
        self.assertEqual(sdk['CarIdxPosition'][5], 1)
        self.assertEqual(sdk['WeekendInfo']['TrackDisplayName'], 'Monza')
        self.assertEqual(sdk['WeekendInfo']['TrackLength'], '5.0000 km')
        self.assertEqual(sdk['DriverInfo']['DriverCarIdx'], 0)
        self.assertEqual(sdk['DriverInfo']['DriverCarFuelMaxLtr'], 120.)
        self.assertEqual(sdk['SessionInfo']['Sessions'][0]['SessionType'], 'Race')
        self.assertNotIn('PitSvFuel', sdk.var_headers_names)  # unpublished channels stay unknown

    def test_flags_follow_lmu_phase_and_flag_fields(self):
        cases = [(dict(flag=6), 0x20), (dict(sectors=(1, 0, 0)), 0x8), (dict(phase=6), 0x4000),
                 (dict(phase=7), 0x10), (dict(phase=8), 0x1), (dict(phase=3), 0x10000000),
                 (dict(yellow=1), 0x8000)]
        for values, bit in cases:
            with self.subTest(values=values):
                sdk = read_all(*adapter([frame(**values)]), 1)
                self.assertEqual(sdk['SessionFlags'] & bit, bit)

    def test_session_types_and_states(self):
        self.assertEqual(session_type(1), 'Practice')
        self.assertEqual(session_type(6), 'Open Qualify')
        self.assertEqual(session_type(12), 'Race')
        self.assertIsNone(session_type(None))
        for phase, state in ((0, 1), (3, 3), (4, 3), (5, 4), (8, 5)):
            with self.subTest(phase=phase):
                self.assertEqual(read_all(*adapter([frame(phase=phase)]), 1)['SessionState'], state)

    def test_race_length_is_the_remaining_time_before_the_green(self):
        sdk = read_all(*adapter([frame(phase=3, session_end_et=None), frame(101., phase=5, session_end_et=None)]), 2)
        self.assertEqual(sdk['SessionInfo']['Sessions'][0]['SessionTime'], '600.0000 sec')

    def test_short_scoring_gaps_keep_the_last_scoring_and_long_ones_do_not(self):
        sdk = read_all(*adapter([frame(100., flag=6), frame(101., scoring=False), frame(106., scoring=False)]), 2)
        self.assertEqual(sdk['SessionFlags'] & 0x20, 0x20)
        self.assertTrue(sdk['IsOnTrack'])
        sdk.freeze_var_buffer_latest()
        self.assertFalse(sdk['IsOnTrack'])  # five seconds without scoring: not assumed

    def test_stale_frames_disconnect(self):
        sdk, now = adapter([frame()])
        read_all(sdk, now, 1)
        now[0] += 3
        self.assertFalse(sdk.is_connected)

    def test_a_missing_game_reports_why(self):
        class Missing:
            def open(self):
                error = RuntimeError('missing')
                error.code = 'NATIVE_NOT_BUILT'
                raise error
        sdk = LmuIrsdk(source_factory=Missing)
        self.assertFalse(sdk.startup())
        self.assertIn('빌드', sdk.status_message())


class SideBySideTests(unittest.TestCase):
    def side(self, *others, speed=50.):
        return read_all(*adapter([frame(speed=speed, others=others)]), 1)['CarLeftRight']

    def test_left_right_both_and_clear(self):
        self.assertEqual(self.side(), 1)
        self.assertEqual(self.side((3, [3., 0., 1.], 0)), 2)    # local +X is left
        self.assertEqual(self.side((3, [-3., 0., 1.], 0)), 3)
        self.assertEqual(self.side((3, [3., 0., 0.], 0), (4, [-3., 0., 0.], 0)), 4)
        self.assertEqual(self.side((3, [3., 0., 30.], 0)), 1)   # well behind: clear

    def test_parked_on_the_grid_is_not_spotted(self):
        self.assertEqual(self.side((3, [3., 0., 1.], 0), speed=1.), 0)

    def test_a_stale_far_car_does_not_withdraw_the_picture_but_a_stale_near_one_does(self):
        self.assertEqual(self.side((3, [3., 0., 1.], 0), (9, [0., 0., 900.], 5.)), 2)
        self.assertIsNone(self.side((3, [3., 0., 1.], 5.)))

    def test_nearby_traffic_rejects_malformed_positions(self):
        self.assertIsNone(nearby_traffic({'status': 'AVAILABLE', 'player': {'position_m': [0, 0, 0]},
                                          'vehicles': [{'position_m': 'x'}]}, 1., 0))


class AutoGameTests(unittest.TestCase):
    def test_uses_the_game_that_answers_and_returns_to_probing_after_disconnect(self):
        class Off:
            game = 'iracing'
            is_initialized = is_connected = False

            def startup(self):
                return False
        lmu, now = adapter([frame()])
        auto = AutoGameSdk([('iracing', Off()), ('lmu', lmu)])
        self.assertTrue(auto.startup())
        self.assertEqual(auto.game, 'lmu')
        auto.freeze_var_buffer_latest()
        self.assertEqual(auto['Speed'], 50.)
        auto.shutdown()
        self.assertIsNone(auto.game)
        self.assertFalse(auto.is_initialized)
        self.assertIn('LMU', auto.waiting_message)


class LiveCoachTests(unittest.TestCase):
    def test_the_iracing_spotter_and_radio_run_on_lmu_frames(self):
        from live_telemetry import LiveTelemetry
        frames = [frame(100 + i / 30, distance=1000 + i, others=[(3, [3., 0., 1.], 0)] if i >= 40 else [])
                  for i in range(120)]
        sdk, now = adapter(frames)
        now[0] = 1000.
        live = LiveTelemetry(clock=lambda: now[0])
        keys = []
        for _ in frames:
            now[0] += 1 / 30
            live._tick(sdk)
            keys += [event['key'] for event in live.spotter_status()['events']]
        self.assertEqual(live.state, 'CONNECTED')
        self.assertEqual(live.game, 'lmu')
        self.assertIn('LMU', live.message)
        self.assertIn('left', keys)
        self.assertEqual(live.identity['game'], 'lmu')
        self.assertEqual(live.radio.metadata.get('session_type'), 'Race')


class JournalTests(unittest.TestCase):
    def test_live_frames_are_journalled_with_the_traffic_used_and_replay(self):
        with tempfile.TemporaryDirectory() as folder:
            sdk, now = adapter([frame(), frame(101., others=[(3, [3., 0., 1.], 0)])], folder)
            sdk.journal.start()
            read_all(sdk, now, 2)
            status = sdk.journal.stop()
            with open(status['path'], encoding='utf-8') as handle:
                rows = [json.loads(line) for line in handle]
            snapshots = [row['snapshot'] for row in rows if row['type'] == 'SNAPSHOT']
            self.assertEqual(len(snapshots), 2)
            self.assertEqual(rows[-1]['reason_code'], 'STOPPED')
            replay = LmuIrsdk(source_factory=lambda: Source(snapshots), normalised=True, clock=lambda: 0.)
            replay.startup()
            replay.freeze_var_buffer_latest()
            replay.freeze_var_buffer_latest()
            self.assertEqual(replay['CarLeftRight'], 2)

    def test_nothing_is_written_until_recording_starts(self):
        with tempfile.TemporaryDirectory() as folder:
            read_all(*adapter([frame()], folder), 1)
            self.assertEqual(os.listdir(folder), [])


if __name__ == '__main__':
    unittest.main()
