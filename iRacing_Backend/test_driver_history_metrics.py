"""Synthetic report measurements; no personal recording, database or SDK reads."""
import copy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from driver_history_metrics import (build_observation_metrics, display_labels,
                                    merge_observation_metrics, unavailable_metrics)
from driver_history_summary import build_source_summary, compact_lmu_clock


class DriverHistoryMetricsTests(unittest.TestCase):
    def telemetry(self, *, times=None, game='iracing'):
        times = np.array(times if times is not None else np.arange(11) / 10.)
        count = len(times)
        return SimpleNamespace(path='synthetic-not-read.ibt', tick_rate=10, channels={
            'SessionTime': times, 'Speed': np.linspace(10., 20., count),
            'IsReplayPlaying': np.zeros(count), 'IsOnTrack': np.ones(count),
            'SessionNum': np.zeros(count), 'FuelLevel': np.linspace(40., 39., count),
            'TrackTempCrew': np.full(count, 30.), 'AirTemp': np.full(count, 20.),
            'TrackWetness': np.ones(count), 'PlayerTireCompound': np.zeros(count)},
            metadata={'game': game, 'car_name': 'Synthetic GT3', 'track_name': 'Synthetic Track',
                      'track_config': 'GP', 'driver_name': 'PRIVATE_NAME', 'session_type': 'Practice'},
            session_info={}, setup={'Chassis': {'Wing': '5'}},
            laps=[SimpleNamespace(lap_number=1, start_idx=0, end_idx=count - 1,
                                  lap_time_s=float(times[-1] - times[0]), complete=True,
                                  status='VALID', pit_samples=0)])

    def observation(self, game='iracing', reasons=()):
        return {'game': game, 'coverage': {'time_basis': 'NATIVE_SESSION_TIME', 'reasons': list(reasons),
                                         'status': 'OBSERVED', 'intervals': [[0., 1.]]}}

    def test_iracing_time_distance_and_lap_coordinates_use_the_same_offset(self):
        telemetry = self.telemetry()
        metrics = build_observation_metrics(telemetry, self.observation(), offset=1800000000.)
        driving = metrics['driving']
        self.assertEqual(driving['status'], 'OBSERVED')
        self.assertAlmostEqual(driving['time_s'], 1.)
        self.assertAlmostEqual(driving['distance_m'], 15.)
        self.assertEqual(driving['intervals'], [[1800000000., 1800000001.]])
        lap, = metrics['laps']
        self.assertTrue(lap['measurable'])
        self.assertEqual((lap['start_s'], lap['end_s']), (1800000000., 1800000001.))
        self.assertEqual(lap['official_validity'], 'UNKNOWN')
        self.assertEqual(lap['conditions']['fuel_level_l'], 40.)
        self.assertEqual(lap['conditions']['tire_compound'], 0.)
        self.assertEqual(len(lap['conditions']['setup_hash']), 64)

    def test_missing_required_signals_never_turn_into_zero_driving(self):
        for channel in ['Speed', 'IsReplayPlaying', 'IsOnTrack', 'SessionNum']:
            with self.subTest(channel=channel):
                telemetry = self.telemetry()
                telemetry.channels.pop(channel)
                driving = build_observation_metrics(telemetry, self.observation())['driving']
                self.assertEqual(driving['status'], 'UNAVAILABLE')
                self.assertIsNone(driving['time_s'])
                self.assertIsNone(driving['distance_m'])
                self.assertEqual(driving['intervals'], [])

    def test_known_replay_off_track_and_stationary_samples_are_excluded(self):
        for channel, value in [('IsReplayPlaying', 1), ('IsOnTrack', 0), ('Speed', .5), ('Speed', 151)]:
            with self.subTest(channel=channel, value=value):
                telemetry = self.telemetry()
                telemetry.channels[channel][4:7] = value
                driving = build_observation_metrics(telemetry, self.observation())['driving']
                self.assertEqual(driving['status'], 'PARTIAL')
                self.assertEqual(driving['intervals'], [[0., .3], [.7, 1.]])
                self.assertAlmostEqual(driving['time_s'], .6)

    def test_unknown_boolean_states_and_nonfinite_speed_remove_both_adjacent_edges(self):
        for channel, value in [('IsReplayPlaying', 2), ('IsOnTrack', np.nan), ('Speed', np.inf)]:
            with self.subTest(channel=channel):
                telemetry = self.telemetry()
                telemetry.channels[channel][5] = value
                driving = build_observation_metrics(telemetry, self.observation())['driving']
                self.assertEqual(driving['intervals'], [[0., .4], [.6, 1.]])
                self.assertAlmostEqual(driving['time_s'], .8)

    def test_gaps_resets_duplicate_times_and_session_changes_are_not_joined(self):
        for times, numbers, expected in [([0., .1, 1., 1.1], [0] * 4, [[0., .1], [1., 1.1]]),
                                        ([1., 1.1, 0., .1], [0] * 4, [[1., 1.1], [0., .1]]),
                                        ([0., .1, .1, .2], [0] * 4, [[0., .1], [.1, .2]]),
                                        ([0., .1, .2, .3], [0, 0, 1, 1], [[0., .1], [.2, .3]])]:
            with self.subTest(times=times, numbers=numbers):
                telemetry = self.telemetry(times=times)
                telemetry.channels['SessionNum'] = np.array(numbers)
                metrics = build_observation_metrics(telemetry, self.observation())
                self.assertEqual(metrics['driving']['intervals'], expected)
                self.assertAlmostEqual(metrics['driving']['time_s'], .2)
                self.assertFalse(metrics['laps'][0]['measurable'] if metrics['laps'] else False)

    def test_invalid_or_missing_time_and_invalid_rate_are_unavailable(self):
        for rate in [0, -1, np.nan, None, True, '10']:
            telemetry = self.telemetry()
            telemetry.tick_rate = rate
            self.assertEqual(build_observation_metrics(telemetry, self.observation())['driving']['status'], 'UNAVAILABLE')
        telemetry.channels.pop('SessionTime')
        self.assertEqual(build_observation_metrics(telemetry, self.observation())['driving']['status'], 'UNAVAILABLE')

    def test_all_stopped_samples_are_unavailable_rather_than_a_claim_of_zero_distance(self):
        telemetry = self.telemetry()
        telemetry.channels['Speed'][:] = 0
        driving = build_observation_metrics(telemetry, self.observation())['driving']
        self.assertIsNone(driving['distance_m'])
        self.assertIsNone(driving['time_s'])
        self.assertIn('NO_CONFIRMED_DRIVING_INTERVALS', driving['reason_codes'])

    def test_only_complete_contiguous_valid_parsed_laps_are_measurable(self):
        for field, value in [('complete', False), ('status', 'INVALID'), ('pit_samples', 1),
                             ('lap_time_s', 90.)]:
            with self.subTest(field=field):
                telemetry = self.telemetry()
                setattr(telemetry.laps[0], field, value)
                lap, = build_observation_metrics(telemetry, self.observation())['laps']
                self.assertFalse(lap['measurable'])
                self.assertEqual(lap['official_validity'], 'UNKNOWN')
        telemetry.laps[0].lap_time_s = None
        self.assertEqual(build_observation_metrics(telemetry, self.observation())['laps'], [])

    def test_segment_restriction_never_moves_a_lap_across_a_session_boundary(self):
        telemetry = self.telemetry()
        metrics = build_observation_metrics(telemetry, self.observation(), start_idx=0, end_idx=6)
        self.assertEqual(metrics['laps'], [])
        self.assertAlmostEqual(metrics['driving']['time_s'], .5)

    def test_missing_or_changing_conditions_are_not_filled_with_defaults(self):
        telemetry = self.telemetry()
        telemetry.channels.pop('FuelLevel')
        telemetry.channels['AirTemp'][2] = np.nan
        telemetry.channels['TrackWetness'][5:] = 2
        telemetry.channels.pop('PlayerTireCompound')
        telemetry.setup = {}
        lap, = build_observation_metrics(telemetry, self.observation())['laps']
        self.assertEqual(lap['conditions'], {'track_temp_c': 30.})

    def test_lmu_never_uses_the_interpolated_speed_for_driving_totals(self):
        telemetry = self.telemetry(game='lmu')
        lap, = build_observation_metrics(telemetry, self.observation('lmu'))['laps']
        self.assertTrue(lap['measurable'])
        self.assertEqual(lap['official_validity'], 'UNKNOWN')
        driving = build_observation_metrics(telemetry, self.observation('lmu'))['driving']
        self.assertEqual(driving['status'], 'UNAVAILABLE')
        self.assertIsNone(driving['distance_m'])

    def test_lmu_native_gaps_resets_and_missing_clock_withhold_lap_measurement(self):
        for reason in ['SAMPLE_GAP', 'SESSION_CLOCK_RESET', 'LMU_NATIVE_CLOCK_UNAVAILABLE']:
            with self.subTest(reason=reason):
                telemetry = self.telemetry(game='lmu')
                lap, = build_observation_metrics(telemetry, self.observation('lmu', [reason]))['laps']
                self.assertFalse(lap['measurable'])

    def test_labels_are_display_metadata_only_and_strip_paths_and_personal_fields(self):
        metadata = self.telemetry().metadata
        self.assertEqual(display_labels(metadata), {'car': 'Synthetic GT3', 'track': 'Synthetic Track', 'layout': 'GP'})
        for name in ['C:\\Users\\Driver\\car.ibt', '/home/private/course', 'recording.duckdb']:
            self.assertEqual(display_labels({'car_name': name, 'track_path': name, 'driver_name': 'PRIVATE'})['car'], '')

    def test_inputs_remain_unchanged_and_output_is_strict_json(self):
        telemetry = self.telemetry()
        original = copy.deepcopy(telemetry)
        result = build_observation_metrics(telemetry, self.observation())
        for name, array in telemetry.channels.items():
            np.testing.assert_array_equal(array, original.channels[name])
        self.assertEqual(telemetry.metadata, original.metadata)
        self.assertEqual(telemetry.setup, original.setup)
        payload = json.dumps(result, allow_nan=False)
        self.assertNotIn('PRIVATE_NAME', payload)

    def test_segment_merge_does_not_create_a_driving_edge_between_segments(self):
        before, after = unavailable_metrics(), unavailable_metrics()
        for value, window in [(before, [0., 1.]), (after, [2., 3.])]:
            value['driving'] = dict(status='OBSERVED', time_s=1., distance_m=10., intervals=[window], reason_codes=[])
        joined = merge_observation_metrics(before, after)
        self.assertEqual(joined['driving']['intervals'], [[0., 1.], [2., 3.]])
        self.assertEqual(joined['driving']['time_s'], 2.)
        self.assertEqual(joined['driving']['distance_m'], 20.)

    def test_summary_connects_metrics_and_labels_and_preserves_analysis_on_extraction_failure(self):
        telemetry = self.telemetry()
        with patch('session_log_extras.recording_window', return_value=None):
            observation, = build_source_summary(telemetry, 'a' * 64)['observations']
        self.assertEqual(observation['labels']['car'], 'Synthetic GT3')
        self.assertAlmostEqual(observation['metrics']['driving']['time_s'], 1.)
        with patch('session_log_extras.recording_window', return_value=None), \
                patch('driver_history_metrics.build_observation_metrics', side_effect=ValueError('PRIVATE_PATH')):
            failed, = build_source_summary(telemetry, 'a' * 64)['observations']
        self.assertEqual(failed['coverage'], observation['coverage'])
        self.assertEqual(failed['metrics']['driving']['status'], 'UNAVAILABLE')
        self.assertNotIn('PRIVATE_PATH', json.dumps(failed))

    def test_lmu_summary_maps_native_utc_without_another_recording_read(self):
        telemetry = self.telemetry(game='lmu')
        telemetry._driver_history_lmu_clock = compact_lmu_clock(telemetry.channels['SessionTime'], 10., '2026-10-20T10_00_00Z')
        with patch('session_log_extras.recording_window', side_effect=AssertionError('no reread')):
            observation, = build_source_summary(telemetry, 'a' * 64)['observations']
        lap, = observation['metrics']['laps']
        self.assertEqual((lap['start_s'], lap['end_s']), (1792490400., 1792490401.))
        self.assertTrue(lap['measurable'])
        self.assertIsNone(observation['metrics']['driving']['time_s'])


if __name__ == '__main__':
    unittest.main()
