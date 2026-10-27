"""Synthetic source/clock/identity boundaries; no personal DB or recording reads."""
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from driver_history_summary import build_source_summary, compact_lmu_clock
from telemetry_channels import ANALYSIS_CHANNELS, CORE_CHANNELS


SOURCE_A, SOURCE_B = 'a' * 64, 'b' * 64


class DriverHistorySummaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def telemetry(self, times=None, nums=None, replay=None, game='iracing'):
        times = np.array(times if times is not None else 100. + np.arange(6) / 10.)
        n = len(times)
        path = self.folder / 'fixture.ibt'
        header = bytearray(144)
        struct.pack_into('<10i', header, 0, 2, 0, 10, 1, 0, 0, 3, 0, 1, 8)
        struct.pack_into('<Qddii', header, 112, 1800000000, times[0] if n else 0,
                         times[-1] if n else 0, 0, n)
        path.write_bytes(header)
        return SimpleNamespace(path=path, tick_rate=10, channels={
            'SessionTime': times.copy(),
            'SessionNum': np.array(nums if nums is not None else [0] * n),
            'IsReplayPlaying': np.array(replay if replay is not None else [False] * n)},
            metadata={'game': game, 'car_path': 'car-a', 'track_path': 'track-a', 'track_config': 'layout-a',
                      'session_type': 'Race', 'driver_name': 'PRIVATE_DRIVER_NAME'},
            session_info={
                'WeekendInfo': {'SessionID': 123, 'SubSessionID': 456, 'TrackID': 8},
                'DriverInfo': {'DriverCarIdx': 0, 'Drivers': [
                    {'CarIdx': 0, 'UserID': 772391, 'UserName': 'PRIVATE_DRIVER_NAME', 'CarID': 7}]},
                'SessionInfo': {'Sessions': [
                    {'SessionNum': 0, 'SessionType': 'Practice'}, {'SessionNum': 1, 'SessionType': 'Race'}]}})

    def test_native_identity_uses_real_session_kind_and_actual_utc(self):
        telemetry = self.telemetry()
        observation, = build_source_summary(telemetry, SOURCE_A)['observations']
        self.assertEqual(observation['identity_status'], 'NATIVE')
        self.assertEqual(observation['session_type'], 'PRACTICE')
        self.assertEqual(observation['session_type_source'], 'IRACING_SESSION_NUM')
        self.assertEqual(observation['recorded_at'], datetime.fromtimestamp(1800000000, timezone.utc).isoformat())
        self.assertEqual(observation['recorded_at_source'], 'IBT_DISK_SUBHEADER')
        self.assertEqual(observation['coverage']['intervals'], [[1800000000., 1800000000.5]])
        self.assertEqual(observation['coverage']['status'], 'OBSERVED')
        self.assertEqual(observation['coverage']['sample_count'], 6)
        self.assertEqual(observation['validity_status'], 'UNKNOWN')
        self.assertEqual(observation['conditions'], {})

    def test_source_content_identity_and_analysis_mode_do_not_change_native_drive(self):
        telemetry = self.telemetry()
        first = build_source_summary(telemetry, SOURCE_A)
        telemetry.metadata['session_mode'] = 'QUALIFYING'
        telemetry.metadata['file_modified_at'] = 1
        telemetry.path = telemetry.path.with_name('renamed.ibt')
        with patch('session_log_extras.recording_window', return_value=(1800000000., 1800000000.5)):
            second = build_source_summary(telemetry, SOURCE_B)
        self.assertEqual(first['observations'], second['observations'])
        self.assertNotEqual(first['source_id'], second['source_id'])

    def test_missing_native_owner_stays_source_scoped_and_does_not_expose_names(self):
        telemetry = self.telemetry()
        telemetry.session_info['DriverInfo']['Drivers'][0].pop('UserID')
        a = build_source_summary(telemetry, SOURCE_A)
        b = build_source_summary(telemetry, SOURCE_B)
        self.assertEqual(a['observations'][0]['identity_status'], 'SOURCE_ONLY')
        self.assertNotEqual(a['observations'][0]['drive_id'], b['observations'][0]['drive_id'])
        payload = json.dumps(a)
        self.assertNotIn('PRIVATE_DRIVER_NAME', payload)
        self.assertNotIn(str(self.folder), payload)
        self.assertNotIn('UserID', payload)

    def test_owner_and_native_session_changes_change_drive_identity(self):
        telemetry = self.telemetry()
        first = build_source_summary(telemetry, SOURCE_A)['observations'][0]['drive_id']
        telemetry.session_info['DriverInfo']['Drivers'][0]['UserID'] += 1
        self.assertNotEqual(first, build_source_summary(telemetry, SOURCE_A)['observations'][0]['drive_id'])
        telemetry.session_info['WeekendInfo']['SubSessionID'] += 1
        self.assertNotEqual(first, build_source_summary(telemetry, SOURCE_A)['observations'][0]['drive_id'])

    def test_zero_or_bool_native_identifiers_cannot_establish_native_identity(self):
        for value in [0, '', None, True, 'not-an-id']:
            with self.subTest(value=value):
                telemetry = self.telemetry()
                telemetry.session_info['WeekendInfo']['SessionID'] = value
                self.assertEqual(build_source_summary(telemetry, SOURCE_A)['observations'][0]['identity_status'], 'SOURCE_ONLY')

    def test_names_alone_do_not_establish_native_vehicle_and_track_identity(self):
        telemetry = self.telemetry()
        telemetry.metadata.pop('car_path')
        telemetry.metadata.pop('track_path')
        telemetry.metadata.update(car_name='Vehicle Name', track_name='Track Name')
        telemetry.session_info['WeekendInfo'].pop('TrackID')
        telemetry.session_info['WeekendInfo']['TrackName'] = 'Track Name'
        telemetry.session_info['DriverInfo']['Drivers'][0].pop('CarID')
        observation, = build_source_summary(telemetry, SOURCE_A)['observations']
        self.assertEqual(observation['identity_status'], 'SOURCE_ONLY')

    def test_duplicate_owner_metadata_cannot_choose_a_native_driver(self):
        telemetry = self.telemetry()
        telemetry.session_info['DriverInfo']['Drivers'].append({'CarIdx': 0, 'UserID': 1001, 'CarID': 7})
        self.assertEqual(build_source_summary(telemetry, SOURCE_A)['observations'][0]['identity_status'], 'SOURCE_ONLY')

    def test_track_path_without_layout_does_not_assume_the_native_configuration(self):
        telemetry = self.telemetry()
        telemetry.metadata.pop('track_config')
        telemetry.session_info['WeekendInfo'].pop('TrackID')
        self.assertEqual(build_source_summary(telemetry, SOURCE_A)['observations'][0]['identity_status'], 'SOURCE_ONLY')
        telemetry.metadata['track_platform_id'] = 8
        self.assertEqual(build_source_summary(telemetry, SOURCE_A)['observations'][0]['identity_status'], 'NATIVE')
        telemetry.metadata['track_platform_id'] = True
        self.assertEqual(build_source_summary(telemetry, SOURCE_A)['observations'][0]['identity_status'], 'SOURCE_ONLY')

    def test_actual_session_num_splits_mixed_file_and_prevents_false_utc(self):
        telemetry = self.telemetry(nums=[0, 0, 0, 1, 1, 1])
        observations = build_source_summary(telemetry, SOURCE_A)['observations']
        self.assertEqual([row['session_type'] for row in observations], ['PRACTICE', 'RACE'])
        self.assertEqual(len({row['drive_id'] for row in observations}), 2)
        self.assertTrue(all(row['recorded_at'] is None for row in observations))
        self.assertTrue(all(row['coverage']['time_basis'] == 'NATIVE_SESSION_TIME' for row in observations))
        self.assertEqual(observations[0]['coverage']['intervals'], [[100., 100.2]])
        self.assertEqual(observations[1]['coverage']['intervals'], [[100.3, 100.5]])

    def test_header_analysis_mode_cannot_replace_missing_actual_session_num(self):
        telemetry = self.telemetry()
        telemetry.channels.pop('SessionNum')
        observation, = build_source_summary(telemetry, SOURCE_A)['observations']
        self.assertEqual(observation['identity_status'], 'SOURCE_ONLY')
        self.assertEqual(observation['session_type'], 'UNKNOWN')
        self.assertEqual(observation['session_type_source'], 'UNKNOWN')

    def test_conflicting_native_session_type_is_unknown(self):
        telemetry = self.telemetry()
        telemetry.session_info['SessionInfo']['Sessions'].append({'SessionNum': 0, 'SessionType': 'Race'})
        self.assertEqual(build_source_summary(telemetry, SOURCE_A)['observations'][0]['session_type'], 'UNKNOWN')

    def test_time_gaps_invalid_samples_and_replay_are_not_joined(self):
        telemetry = self.telemetry(times=[100, 100.1, np.nan, 100.3, 100.4, 103, 103.1, 103.2, 103.3],
                                   replay=[0, 0, 0, 0, 0, 0, 1, 0, 0])
        observation, = build_source_summary(telemetry, SOURCE_A)['observations']
        self.assertEqual(observation['coverage']['intervals'], [
            [1800000000., 1800000000.1], [1800000000.3, 1800000000.4],
            [1800000003.2, 1800000003.3]])
        self.assertEqual(observation['coverage']['sample_count'], 7)
        self.assertTrue({'INVALID_SESSION_TIME', 'SAMPLE_GAP', 'REPLAY_EXCLUDED'}.issubset(observation['coverage']['reasons']))

    def test_reversed_clock_creates_source_scoped_epochs_and_no_utc(self):
        telemetry = self.telemetry(times=[100, 100.1, 0, .1])
        observations = build_source_summary(telemetry, SOURCE_A)['observations']
        self.assertEqual(len(observations), 2)
        self.assertEqual([row['coverage']['intervals'] for row in observations], [[[100., 100.1]], [[0., .1]]])
        self.assertTrue(all(row['identity_status'] == 'SOURCE_ONLY' for row in observations))
        self.assertTrue(all(row['recorded_at'] is None for row in observations))
        self.assertEqual(len({row['drive_id'] for row in observations}), 2)

    def test_clock_reset_hidden_by_invalid_sample_stays_source_scoped(self):
        telemetry = self.telemetry(times=[100, 100.1, np.nan, 0, .1])
        observations = build_source_summary(telemetry, SOURCE_A)['observations']
        self.assertEqual(len(observations), 2)
        self.assertTrue(all(row['identity_status'] == 'SOURCE_ONLY' for row in observations))
        self.assertTrue(all(row['recorded_at'] is None for row in observations))
        self.assertEqual([row['coverage']['intervals'] for row in observations], [[[100., 100.1]], [[0., .1]]])

    def test_invalid_tick_rate_cannot_join_samples_using_an_assumed_rate(self):
        for rate in [0, -1, None, True, np.nan, '10']:
            with self.subTest(rate=rate):
                telemetry = self.telemetry()
                telemetry.tick_rate = rate
                observation, = build_source_summary(telemetry, SOURCE_A)['observations']
                self.assertEqual(observation['coverage']['intervals'], [])
                self.assertEqual(observation['coverage']['status'], 'UNAVAILABLE')
                self.assertIn('SAMPLING_RATE_UNAVAILABLE', observation['coverage']['reasons'])

    def test_partial_or_missing_iracing_session_numbers_do_not_establish_utc_anchor(self):
        for nums in [None, [0, 0, np.nan, 0, 0, 0]]:
            with self.subTest(nums=nums):
                telemetry = self.telemetry(nums=nums)
                if nums is None:
                    telemetry.channels.pop('SessionNum')
                self.assertTrue(all(row['recorded_at'] is None for row in build_source_summary(telemetry, SOURCE_A)['observations']))

    def test_malformed_metadata_structures_are_treated_as_missing(self):
        for field, value in [('metadata', ['invalid']), ('session_info', 'invalid')]:
            with self.subTest(field=field):
                telemetry = self.telemetry()
                setattr(telemetry, field, value)
                observations = build_source_summary(telemetry, SOURCE_A)['observations']
                if field == 'session_info':
                    self.assertTrue(all(row['identity_status'] == 'SOURCE_ONLY' for row in observations))
                json.dumps(observations, allow_nan=False)

    def test_no_mtime_or_filename_date_fallback_for_missing_corrupt_clock(self):
        telemetry = self.telemetry()
        telemetry.path.write_bytes(b'no native header')
        observation, = build_source_summary(telemetry, SOURCE_A)['observations']
        self.assertIsNone(observation['recorded_at'])
        self.assertEqual(observation['recorded_at_source'], 'UNKNOWN')
        self.assertEqual(observation['coverage']['time_basis'], 'NATIVE_SESSION_TIME')

    def test_native_window_mismatch_does_not_create_recording_date(self):
        with patch('session_log_extras.recording_window', return_value=(1800000000., 1800000099.)):
            self.assertIsNone(build_source_summary(self.telemetry(), SOURCE_A)['observations'][0]['recorded_at'])

    def test_lmu_metadata_is_real_kind_but_synthetic_session_info_is_not_native(self):
        telemetry = self.telemetry(game='lmu')
        telemetry.channels.pop('SessionNum')
        telemetry.channels.pop('IsReplayPlaying')
        telemetry._driver_history_lmu_clock = compact_lmu_clock(
            telemetry.channels['SessionTime'], 10, '2027-01-15T08_00_00Z')
        with patch('session_log_extras.recording_window', side_effect=AssertionError('no reread')):
            observation, = build_source_summary(telemetry, SOURCE_A)['observations']
        self.assertEqual(observation['identity_status'], 'SOURCE_ONLY')
        self.assertEqual(observation['session_type'], 'RACE')
        self.assertEqual(observation['session_type_source'], 'LMU_RECORDING_METADATA')
        self.assertEqual(observation['recorded_at_source'], 'LMU_RECORDING_TIME')
        self.assertEqual(observation['validity_status'], 'UNKNOWN')

    def test_missing_lmu_kind_and_clock_stay_unknown(self):
        telemetry = self.telemetry(game='lmu')
        telemetry.metadata.pop('session_type')
        telemetry._driver_history_lmu_clock = compact_lmu_clock(telemetry.channels['SessionTime'], 10, None)
        observation, = build_source_summary(telemetry, SOURCE_A)['observations']
        self.assertEqual(observation['session_type'], 'UNKNOWN')
        self.assertEqual(observation['coverage']['time_basis'], 'SOURCE_RELATIVE')
        self.assertEqual(observation['coverage']['intervals'], [[0., .5]])

    def test_lmu_without_native_evidence_cannot_use_the_resampled_axis(self):
        telemetry = self.telemetry(game='lmu')
        with patch('session_log_extras.recording_window', side_effect=AssertionError('no reread')):
            observation, = build_source_summary(telemetry, SOURCE_A)['observations']
        self.assertEqual(observation['coverage']['status'], 'UNAVAILABLE')
        self.assertEqual(observation['coverage']['time_basis'], 'UNKNOWN')
        self.assertEqual(observation['coverage']['intervals'], [])
        self.assertEqual(observation['coverage']['sample_count'], 0)
        self.assertIn('LMU_NATIVE_CLOCK_UNAVAILABLE', observation['coverage']['reasons'])
        self.assertIsNone(observation['recorded_at'])

    def test_lmu_native_sample_count_and_tail_do_not_come_from_60hz_axis(self):
        telemetry = self.telemetry(times=[10., 10. + 1 / 60, 10. + 2 / 60], game='lmu')
        native = np.array([10., 10.01, 10.02, 10.03, 10.04])
        telemetry._driver_history_lmu_clock = compact_lmu_clock(native, 100., None)
        original = native.copy()
        observation, = build_source_summary(telemetry, SOURCE_A)['observations']
        self.assertEqual(observation['coverage']['sample_count'], 5)
        self.assertEqual(observation['coverage']['intervals'], [[0., .04]])
        np.testing.assert_array_equal(native, original)
        self.assertFalse(any(isinstance(value, np.ndarray) for value in telemetry._driver_history_lmu_clock.values()))

    def test_lmu_native_gaps_remain_gaps_after_analysis_resampling(self):
        telemetry = self.telemetry(times=10. + np.arange(61) / 60., game='lmu')
        telemetry._driver_history_lmu_clock = compact_lmu_clock([10., 10.01, 11., 11.01], 100., None)
        observation, = build_source_summary(telemetry, SOURCE_A)['observations']
        self.assertEqual(observation['coverage']['sample_count'], 4)
        self.assertEqual(observation['coverage']['intervals'], [[0., .01], [1., 1.01]])
        self.assertIn('SAMPLE_GAP', observation['coverage']['reasons'])

    def test_lmu_native_reset_splits_epochs_and_withholds_utc(self):
        telemetry = self.telemetry(times=[10., 10. + 1 / 60.], game='lmu')
        telemetry._driver_history_lmu_clock = compact_lmu_clock(
            [10., 10.01, 10.005, 10.02, 10.03], 100., '2027-01-15T08_00_00Z')
        observations = build_source_summary(telemetry, SOURCE_A)['observations']
        self.assertEqual(len(observations), 2)
        self.assertEqual([row['coverage']['sample_count'] for row in observations], [2, 3])
        self.assertEqual(len({row['drive_id'] for row in observations}), 2)
        self.assertTrue(all(row['recorded_at'] is None for row in observations))
        self.assertTrue(all('SESSION_CLOCK_RESET' in row['coverage']['reasons'] for row in observations))
        self.assertEqual([row['coverage']['time_basis'] for row in observations], ['SOURCE_RELATIVE'] * 2)

    def test_lmu_native_invalid_samples_and_unknown_frequency_do_not_join(self):
        for times, rate, reason in [([10., 10.01, np.nan, 10.03, 10.04], 100., 'INVALID_SESSION_TIME'),
                                    ([10., 10.01, 10.02], 0, 'SAMPLING_RATE_UNAVAILABLE')]:
            with self.subTest(reason=reason):
                telemetry = self.telemetry(game='lmu')
                telemetry._driver_history_lmu_clock = compact_lmu_clock(times, rate, None)
                before = copy.deepcopy(telemetry._driver_history_lmu_clock)
                observation, = build_source_summary(telemetry, SOURCE_A)['observations']
                self.assertIn(reason, observation['coverage']['reasons'])
                self.assertEqual(telemetry._driver_history_lmu_clock, before)
                if not rate:
                    self.assertEqual(observation['coverage']['status'], 'UNAVAILABLE')
                    self.assertEqual(observation['coverage']['intervals'], [])
                else:
                    self.assertEqual(observation['coverage']['intervals'], [[0., .01], [.03, .04]])

    def test_lmu_utc_overflow_invalid_endpoints_and_excessive_duration_are_withheld(self):
        for times, recorded in [([10., 10.01, 11.], '9999-12-31T23_59_59Z'),
                                ([10., 10.01, 604811.], '2026-10-20T10_00_00Z'),
                                ([10., 10.01, np.nan], '2026-10-20T10_00_00Z'),
                                ([10., 10.], '2026-10-20T10_00_00Z')]:
            with self.subTest(times=times, recorded=recorded):
                compact = compact_lmu_clock(times, 100., recorded)
                self.assertTrue(all(row['recorded_at'] is None for row in compact['epochs']))
                self.assertTrue(all(row['coverage']['time_basis'] == 'SOURCE_RELATIVE' for row in compact['epochs']))
                json.dumps(compact, allow_nan=False)

    def test_empty_malformed_nonfinite_and_all_replay_sources_have_no_observations(self):
        variants = [{}, {'SessionTime': [[1, 2]]}, {'SessionTime': [np.nan, -1]},
                    {'SessionTime': [0, .1], 'IsReplayPlaying': [1, 1]}]
        for channels in variants:
            with self.subTest(channels=channels):
                telemetry = self.telemetry()
                telemetry.channels = channels
                self.assertEqual(build_source_summary(telemetry, SOURCE_A)['observations'], [])

    def test_builder_preserves_input_arrays_and_serializes_without_nonfinite_values(self):
        telemetry = self.telemetry(times=[100, 100.1, np.nan, 100.3])
        original = copy.deepcopy(telemetry)
        result = build_source_summary(telemetry, SOURCE_A)
        for key in telemetry.channels:
            np.testing.assert_array_equal(telemetry.channels[key], original.channels[key])
        self.assertEqual(telemetry.metadata, original.metadata)
        self.assertEqual(telemetry.session_info, original.session_info)
        json.dumps(result, allow_nan=False)

    def test_source_id_is_validated_and_channel_additions_are_optional(self):
        for source_id in ['', 'x' * 64, 'a' * 63, None]:
            with self.subTest(source_id=source_id), self.assertRaises(ValueError):
                build_source_summary(self.telemetry(), source_id)
        self.assertEqual(build_source_summary(self.telemetry(), SOURCE_A.upper())['source_id'], SOURCE_A)
        self.assertTrue({'SessionNum', 'IsReplayPlaying'}.issubset(ANALYSIS_CHANNELS))
        self.assertFalse({'SessionNum', 'IsReplayPlaying'} & CORE_CHANNELS)


if __name__ == '__main__':
    unittest.main()
