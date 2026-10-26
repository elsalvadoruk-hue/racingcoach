"""Regressions from log001-005; synthetic inputs, never the driver's data."""
from types import SimpleNamespace
import unittest
import numpy as np
from race_radio import RaceRadio
from lmu_ibt import LmuIbtReader


def sample(**changes):
    return dict(session_time=10., on_track=True, is_replay=False, on_pit_road=False,
                session_state=4, session_flags=0, speed_mps=30., brake=0.,
                throttle=.9, steering_rad=0., **changes)


class StartSessionTests(unittest.TestCase):
    def flag(self, kind, flag):
        radio = RaceRadio()
        radio.ingest(sample(), 'run', 10., {'session_type': kind})
        radio.ingest({**sample(), 'session_time': 10.1, 'session_flags': flag},
                     'run', 10.1, {'session_type': kind})
        return radio

    def test_start_flags_only_belong_to_races(self):
        for kind in ('Offline Testing', 'Practice', 'Warmup', '', 'Lone Qualify', 'Open Qualify', 'Race'):
            for key, flag in (('one_to_green', 0x200), ('start_ready', 0x20000000), ('start_set', 0x40000000)):
                with self.subTest(kind=kind, key=key):
                    radio = self.flag(kind, flag)
                    self.assertEqual(key in [e['key'] for e in radio.view(10.1)['events']], kind == 'Race')

    def test_safety_flags_and_race_restart_are_kept(self):
        for kind in ('Offline Testing', 'Practice', 'Race'):
            radio = self.flag(kind, 0x8 | 0x10000 | 0x200)
            keys = {e['key'] for e in radio.view(10.1)['events']}
            self.assertTrue({'yellow', 'black'} <= keys)
            self.assertEqual('one_to_green' in keys, kind == 'Race')

    def test_queued_start_is_invalid_after_session_metadata_changes(self):
        radio = self.flag('Race', 0x200)
        event = next(e for e in radio.pending if e['key'] == 'one_to_green')
        radio.metadata['session_type'] = 'Practice'
        self.assertFalse(radio.valid(event, 10.1))

    def test_state_transition_without_green_is_race_only(self):
        for kind in ('Offline Testing', 'Practice', 'Race'):
            radio = RaceRadio()
            radio.ingest({**sample(), 'session_state': 3}, 'run', 10., {'session_type': kind})
            radio.ingest({**sample(), 'session_time': 10.1}, 'run', 10.1, {'session_type': kind})
            self.assertEqual('race_started' in [e['key'] for e in radio.pending], kind == 'Race')


class TrackLengthTests(unittest.TestCase):
    def test_partial_distance_is_not_a_track_length(self):
        for maximum in (2496.6, 3109.4, 4441.2):
            distance = (np.arange(100.), np.linspace(0., maximum, 100))
            self.assertIsNone(LmuIbtReader._track_length(SimpleNamespace(laps=[]), distance))

    def test_only_finite_positive_complete_lap_measurements_confirm_length(self):
        laps = [SimpleNamespace(status=status, distance_m=value) for status, value in (
            ('VALIDITY_UNKNOWN', 5000.), ('VALIDITY_UNKNOWN', 5002.),
            ('VALIDITY_UNKNOWN', float('nan')), ('VALIDITY_UNKNOWN', -3.),
            ('INCOMPLETE', 1200.), ('DISTANCE_DISCONTINUITY', 9000.))]
        self.assertEqual(LmuIbtReader._track_length(SimpleNamespace(laps=laps), None), 5001.)

    def test_unknown_length_preserves_native_distance_without_invented_wrap(self):
        axis = np.arange(4.)
        distance = np.array([1200., 1300., 0., 100.])
        result = LmuIbtReader._lap_distance(axis, (axis, distance), None)
        np.testing.assert_array_equal(result, distance)
