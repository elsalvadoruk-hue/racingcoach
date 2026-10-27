"""Synthetic drive-end evidence, isolated from user games and telemetry files."""
from collections import deque
import unittest
from unittest.mock import patch
import uuid

from live_drive_lifecycle import DriveLifecycle, sdk_boolean
from live_telemetry import CHANNELS, LiveTelemetry


def point(**changes):
    value = dict(session_time=10., on_track=True, is_replay=False, speed_mps=30.)
    value.update(changes)
    return value


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.subject = DriveLifecycle()

    def drive(self, now=0., game='iracing', **changes):
        self.subject.observe_sample(game, point(**changes), now)

    def off(self, now, game='iracing', **changes):
        self.subject.observe_sample(game, point(on_track=False, **changes), now)

    def test_initial_process_identity_is_uuid_and_snapshots_are_independent(self):
        view = self.subject.view()
        self.assertEqual(str(uuid.UUID(view['lifecycle_id'])), view['lifecycle_id'])
        self.assertNotEqual(view['lifecycle_id'], DriveLifecycle().lifecycle_id)
        self.assertEqual(view['phase'], 'IDLE')
        self.assertFalse(view['active_drive'])
        self.assertEqual(view['completed_serial'], 0)
        self.assertIsNone(view['last_completed'])
        view['phase'] = 'ENDED'
        self.assertEqual(self.subject.view()['phase'], 'IDLE')

    def test_explicit_off_track_requires_a_real_drive_and_three_observed_seconds(self):
        self.off(0.); self.off(100.)
        self.assertEqual(self.subject.completed_serial, 0)
        self.drive(101.)
        identity = self.subject.drive_id
        self.off(102.)
        self.assertTrue(self.subject.view()['active_drive'])
        self.assertEqual(self.subject.phase, 'ENDING')
        self.off(103.)
        self.off(104.999)
        self.assertEqual(self.subject.completed_serial, 0)
        self.off(105.)
        self.assertEqual(self.subject.view()['last_completed'], dict(serial=1, game='iracing', reason='OFF_TRACK', drive_id=identity))
        self.assertFalse(self.subject.active_drive)
        self.assertEqual(self.subject.phase, 'ENDED')

    def test_disconnection_requires_ten_seconds_and_does_not_complete_before_drive(self):
        self.subject.observe_disconnected('iracing', 0.)
        self.subject.observe_disconnected('iracing', 100.)
        self.assertEqual(self.subject.completed_serial, 0)
        self.drive(101.)
        self.subject.observe_disconnected('iracing', 102.)
        for now in (104., 106., 108., 110.):
            self.subject.observe_disconnected('iracing', now)
        self.subject.observe_disconnected('iracing', 111.999)
        self.assertEqual(self.subject.completed_serial, 0)
        self.subject.observe_disconnected('iracing', 112.)
        self.assertEqual(self.subject.last_completed['reason'], 'DISCONNECTED')

    def test_views_never_advance_a_pending_completion(self):
        self.drive(); self.off(1.)
        for _ in range(200):
            self.assertEqual(self.subject.view()['completed_serial'], 0)
            self.assertEqual(self.subject.view()['phase'], 'ENDING')
        self.off(2.)
        self.off(4.)
        self.assertEqual(self.subject.completed_serial, 1)

    def test_replay_unknown_and_on_track_return_cancel_pending_completion(self):
        changes = [dict(is_replay=True), dict(is_replay=None), dict(on_track=None),
                   dict(is_replay=2), dict(on_track='false'), dict(speed_mps=0., on_track=True)]
        for change in changes:
            with self.subTest(change=change):
                self.subject = DriveLifecycle()
                self.drive(); self.off(1.)
                self.drive(3., **change)
                self.assertTrue(self.subject.active_drive)
                self.off(5.)
                self.assertEqual(self.subject.completed_serial, 0)
                self.off(6.)
                self.off(8.)
                self.assertEqual(self.subject.completed_serial, 1)

    def test_replay_can_only_finish_after_new_explicit_off_track_evidence(self):
        self.drive(); self.off(1.)
        self.off(10., is_replay=True)
        self.off(100., is_replay=True)
        self.assertEqual(self.subject.phase, 'UNKNOWN')
        self.assertEqual(self.subject.completed_serial, 0)
        self.off(101.); self.off(102.); self.off(103.999)
        self.assertEqual(self.subject.completed_serial, 0)
        self.off(104.)
        self.assertEqual(self.subject.completed_serial, 1)

    def test_parked_on_track_and_pit_samples_never_end_an_active_drive(self):
        self.drive()
        identity = self.subject.drive_id
        for now in (1., 5., 100., 1000.):
            self.drive(now, speed_mps=0., on_pit_road=True)
            self.assertEqual(self.subject.phase, 'DRIVING')
            self.assertEqual(self.subject.drive_id, identity)
        self.assertEqual(self.subject.completed_serial, 0)

    def test_missing_invalid_time_speed_or_boolean_never_arms_a_drive(self):
        invalid = [dict(session_time=None), dict(session_time=-1.), dict(session_time=float('nan')),
                   dict(session_time=True), dict(speed_mps=None), dict(speed_mps=float('inf')),
                   dict(speed_mps=True), dict(speed_mps=1.), dict(speed_mps=-1.),
                   dict(on_track=None), dict(on_track='true'), dict(is_replay=None), dict(is_replay=True)]
        for changes in invalid:
            with self.subTest(changes=changes):
                self.subject = DriveLifecycle()
                self.drive(**changes); self.off(1.); self.off(100.)
                self.assertFalse(self.subject.active_drive)
                self.assertEqual(self.subject.completed_serial, 0)

    def test_sdk_zero_one_booleans_have_the_same_evidence_as_native_bools(self):
        for value, expected in [(False, False), (0, False), (0., False), (True, True), (1, True), (1., True),
                                (None, None), ('0', None), (2, None), (float('nan'), None)]:
            self.assertIs(sdk_boolean(value), expected)
        self.drive(on_track=1, is_replay=0)
        self.off(1., is_replay=0); self.off(2., is_replay=0); self.off(4., is_replay=0)
        self.assertEqual(self.subject.completed_serial, 1)

    def test_reason_changes_start_a_new_sustained_observation_window(self):
        self.drive(); self.off(1.)
        self.subject.observe_disconnected('iracing', 3.)
        for now in (5., 7., 9., 11.):
            self.subject.observe_disconnected('iracing', now)
        self.subject.observe_disconnected('iracing', 12.999)
        self.assertEqual(self.subject.completed_serial, 0)
        self.off(13.); self.off(14.); self.off(15.999)
        self.assertEqual(self.subject.completed_serial, 0)
        self.off(16.)
        self.assertEqual(self.subject.last_completed['reason'], 'OFF_TRACK')

    def test_completion_is_retained_once_and_the_next_drive_has_a_distinct_identity(self):
        self.drive(); self.off(1.); self.off(2.); self.off(4.)
        completed = self.subject.view()['last_completed']
        for now in (5., 50., 100.):
            self.off(now)
            self.subject.observe_disconnected('iracing', now)
        self.assertEqual(self.subject.completed_serial, 1)
        completed['serial'] = 99
        self.assertEqual(self.subject.last_completed['serial'], 1)
        first = self.subject.drive_id
        self.drive(101.)
        self.assertNotEqual(first, self.subject.drive_id)
        self.assertTrue(self.subject.active_drive)
        self.assertEqual(self.subject.last_completed['serial'], 1)
        self.off(102.); self.off(103.); self.off(105.)
        self.assertEqual(self.subject.completed_serial, 2)
        self.assertEqual(self.subject.last_completed['serial'], 2)

    def test_game_change_discards_old_pending_drive_and_preserves_completion_serial(self):
        self.drive(); self.off(1.); self.off(2.); self.off(4.)
        self.drive(5.); self.off(6.)
        self.off(100., game='lmu')
        self.assertEqual(self.subject.active_game, 'lmu')
        self.assertFalse(self.subject.active_drive)
        self.assertEqual(self.subject.completed_serial, 1)
        self.assertEqual(self.subject.last_completed['game'], 'iracing')
        self.drive(101., game='lmu'); self.off(102., game='lmu'); self.off(103., game='lmu'); self.off(105., game='lmu')
        self.assertEqual(self.subject.last_completed['serial'], 2)
        self.assertEqual(self.subject.last_completed['game'], 'lmu')

    def test_unknown_and_backwards_wall_clock_cancel_pending_without_fabricating_end(self):
        self.drive(10.); self.off(11.)
        self.subject.observe_unknown()
        self.assertTrue(self.subject.view()['active_drive'])
        self.assertEqual(self.subject.phase, 'UNKNOWN')
        self.off(100.)
        self.off(99.)
        self.assertEqual(self.subject.phase, 'UNKNOWN')
        self.off(102.)
        self.assertEqual(self.subject.completed_serial, 0)
        self.off(103.)
        self.off(105.)
        self.assertEqual(self.subject.completed_serial, 1)

    def test_invalid_game_and_wall_time_cannot_confirm_a_completion(self):
        self.drive(); self.off(1.)
        self.subject.observe_sample('unknown', point(on_track=False), 50.)
        self.subject.observe_disconnected('iracing', float('inf'))
        self.subject.observe_disconnected('iracing', None)
        self.assertEqual(self.subject.completed_serial, 0)
        self.assertEqual(self.subject.phase, 'UNKNOWN')

    def test_off_track_thread_stall_restarts_the_confirmation_window(self):
        self.drive(); self.off(1.)
        self.off(31.)
        self.assertEqual(self.subject.completed_serial, 0)
        self.assertTrue(self.subject.active_drive)
        self.assertEqual(self.subject.phase, 'ENDING')
        self.off(32.); self.off(33.999)
        self.assertEqual(self.subject.completed_serial, 0)
        self.off(34.)
        self.assertEqual(self.subject.completed_serial, 1)

    def test_disconnected_thread_stall_restarts_the_confirmation_window(self):
        self.drive()
        self.subject.observe_disconnected('iracing', 1.)
        self.subject.observe_disconnected('iracing', 31.)
        self.assertEqual(self.subject.completed_serial, 0)
        self.assertTrue(self.subject.active_drive)
        for now in (33., 35., 37., 39., 40.999):
            self.subject.observe_disconnected('iracing', now)
        self.assertEqual(self.subject.completed_serial, 0)
        self.subject.observe_disconnected('iracing', 41.)
        self.assertEqual(self.subject.completed_serial, 1)


class FakeSdk:
    game = 'iracing'

    def __init__(self):
        self.is_initialized = True
        self.is_connected = True
        self.startup_result = False
        self.values = {CHANNELS[key]: value for key, value in point().items()}
        self.values.update(SessionNum=0, WeekendInfo={}, DriverInfo={})
        self.freeze_count = self.unfreeze_count = self.shutdown_count = 0

    @property
    def var_headers_names(self):
        return list(self.values)

    def __getitem__(self, key):
        return self.values.get(key)

    def startup(self):
        return self.startup_result

    def shutdown(self):
        self.shutdown_count += 1
        self.is_initialized = False

    def freeze_var_buffer_latest(self):
        self.freeze_count += 1

    def unfreeze_var_buffer_latest(self):
        self.unfreeze_count += 1


class LiveLifecycleIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.now = [100.]
        self.live = LiveTelemetry(clock=lambda: self.now[0])
        self.addCleanup(self.live.drill.history.close)
        self.sdk = FakeSdk()
        self.read_race = patch('live_telemetry.read_race', return_value=None)
        self.read_race.start()
        self.addCleanup(self.read_race.stop)

    def tick(self, advance=0., **changes):
        self.now[0] += advance
        self.sdk.values.update({CHANNELS[key]: value for key, value in changes.items()})
        return self.live._tick(self.sdk)

    def test_successful_sdk_read_arms_drive_then_off_track_finishes_once(self):
        self.tick()
        self.assertEqual(self.live.status()['drive_lifecycle']['phase'], 'DRIVING')
        self.assertEqual(self.sdk.freeze_count, 1)
        self.assertEqual(self.sdk.unfreeze_count, 1)
        self.tick(1., on_track=False)
        self.tick(1.)
        self.tick(2.)
        lifecycle = self.live.status()['drive_lifecycle']
        self.assertEqual(lifecycle['last_completed']['reason'], 'OFF_TRACK')
        self.assertEqual(lifecycle['completed_serial'], 1)
        self.tick(100.)
        self.assertEqual(self.live.status()['drive_lifecycle']['completed_serial'], 1)

    def test_startup_false_after_disconnect_is_observed_on_every_retry(self):
        self.tick()
        self.sdk.is_connected = False
        self.assertIsNone(self.tick(1.))
        for _ in range(8):
            self.assertFalse(self.tick(1.))
        self.assertFalse(self.tick(1.999))
        self.assertEqual(self.live.status()['drive_lifecycle']['completed_serial'], 0)
        self.assertFalse(self.tick(.001))
        view = self.live.status()['drive_lifecycle']
        self.assertEqual(view['completed_serial'], 1)
        self.assertEqual(view['last_completed']['reason'], 'DISCONNECTED')

    def test_status_does_not_walk_samples_or_finish_a_drive_without_an_sdk_read(self):
        class HistoryGuard(deque):
            def __iter__(self):
                raise AssertionError('lifecycle status must not walk telemetry')

            def __reversed__(self):
                raise AssertionError('lifecycle status must not reverse telemetry')

        self.tick(); self.tick(1., on_track=False)
        self.live.samples = HistoryGuard(self.live.samples, maxlen=5400)
        self.now[0] += 1000.
        for _ in range(10):
            view = self.live.status()['drive_lifecycle']
            self.assertEqual(view['completed_serial'], 0)
            self.assertTrue(view['active_drive'])

    def test_exception_cancels_pending_and_cannot_become_game_end(self):
        self.tick(); self.tick(1., on_track=False)
        with patch.object(self.live, 'prepare_overlay', side_effect=RuntimeError('synthetic read failure')):
            with self.assertRaisesRegex(RuntimeError, 'synthetic read failure'):
                self.tick(10.)
        view = self.live.status()['drive_lifecycle']
        self.assertEqual(view['completed_serial'], 0)
        self.assertEqual(view['phase'], 'UNKNOWN')
        self.assertTrue(view['active_drive'])
        self.tick(1.)
        self.assertEqual(self.live.status()['drive_lifecycle']['completed_serial'], 0)
        self.tick(1.)
        self.tick(2.)
        self.assertEqual(self.live.status()['drive_lifecycle']['completed_serial'], 1)

    def test_disconnection_processing_error_does_not_publish_a_completed_event(self):
        self.tick(); self.sdk.is_connected = False
        self.tick(1.)
        self.sdk.is_initialized = True
        with patch.object(self.live, '_reset_live', side_effect=RuntimeError('synthetic reset failure')):
            with self.assertRaises(RuntimeError):
                self.tick(20.)
        self.assertEqual(self.live.status()['drive_lifecycle']['completed_serial'], 0)
        self.assertEqual(self.live.status()['drive_lifecycle']['phase'], 'UNKNOWN')

    def test_missing_startup_or_connection_state_stays_unknown(self):
        self.tick(); self.sdk.is_connected = False; self.tick(1.)
        self.sdk.startup_result = None
        self.tick(100.)
        self.assertEqual(self.live.status()['drive_lifecycle']['phase'], 'UNKNOWN')
        self.assertEqual(self.live.status()['drive_lifecycle']['completed_serial'], 0)
        self.sdk.is_initialized = True
        self.sdk.is_connected = None
        self.tick(100.)
        self.assertEqual(self.live.status()['drive_lifecycle']['phase'], 'UNKNOWN')
        self.assertEqual(self.live.status()['drive_lifecycle']['completed_serial'], 0)

    def test_integer_flags_replay_and_game_changes_are_observed_without_changing_sdk_values(self):
        self.tick(on_track=1, is_replay=0)
        self.assertEqual(self.live.status()['drive_lifecycle']['phase'], 'DRIVING')
        self.tick(1., on_track=0, is_replay=1)
        self.assertEqual(self.live.status()['drive_lifecycle']['phase'], 'UNKNOWN')
        self.assertEqual(self.sdk.values['IsReplayPlaying'], 1)
        self.tick(100., on_track=0, is_replay=0)
        self.sdk.game = 'lmu'
        self.tick(10.)
        view = self.live.status()['drive_lifecycle']
        self.assertEqual(view['active_game'], 'lmu')
        self.assertFalse(view['active_drive'])
        self.assertEqual(view['completed_serial'], 0)


if __name__ == '__main__':
    unittest.main()
