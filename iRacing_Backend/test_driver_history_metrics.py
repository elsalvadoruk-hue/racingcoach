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

    def test_quality_retains_invalid_untimed_completed_laps_without_claiming_official_validity(self):
        telemetry = self.telemetry()
        telemetry.laps.extend([SimpleNamespace(lap_number=2, start_idx=0, end_idx=10,
            lap_time_s=None, complete=True, status='INVALID', pit_samples=0),
            SimpleNamespace(lap_number=3, start_idx=0, end_idx=10,
            lap_time_s=None, complete=False, status='PARTIAL', pit_samples=0)])
        quality = build_observation_metrics(telemetry, self.observation())['lap_quality']
        self.assertEqual([row['analysis_validity'] for row in quality['completed_laps']], ['VALID', 'INVALID'])
        self.assertEqual(quality['official_validity'], 'UNKNOWN')
        self.assertEqual(quality['authority'], 'ANALYSIS_POLICY')

    def test_incidents_measure_only_continuous_counter_increases_and_observed_time(self):
        telemetry = self.telemetry()
        telemetry.channels['PlayerCarMyIncidentCount'] = np.array([7, 7, 7, 8, 8, 8, 8, 12, 12, 12, 12])
        incidents = build_observation_metrics(telemetry, self.observation(), offset=100.)['incidents']
        self.assertEqual(incidents['increase_points'], 5)
        self.assertAlmostEqual(incidents['observed_time_s'], 1.)
        self.assertEqual(incidents['events'], [dict(start_s=100.2,end_s=100.3,points=1),dict(start_s=100.6,end_s=100.7,points=4)])
        self.assertEqual(incidents['intervals'], [[100.,101.]])
        self.assertIn('INITIAL_INCIDENT_POINTS_EXCLUDED', incidents['reason_codes'])

    def test_incident_reader_accepts_sdk_boolean_track_channel(self):
        telemetry=self.telemetry()
        telemetry.channels['IsOnTrack']=np.ones(11,dtype=bool)
        telemetry.channels['PlayerCarMyIncidentCount']=np.array([0]*5+[1]*6,dtype=np.int32)
        incidents=build_observation_metrics(telemetry,self.observation())['incidents']
        self.assertEqual((incidents['status'],incidents['increase_points']),('OBSERVED',1))

    def test_counter_is_required_and_yaml_snapshot_is_not_an_increment_history(self):
        telemetry=self.telemetry()
        telemetry.session_info={'DriverInfo':{'DriverIncidentCount':99}}
        telemetry.channels['PlayerIncidents']=np.full(11,0x200)
        incidents=build_observation_metrics(telemetry,self.observation())['incidents']
        self.assertEqual(incidents['status'],'UNAVAILABLE')
        self.assertIsNone(incidents['increase_points'])
        self.assertIsNone(incidents['observed_time_s'])

    def test_observed_counter_zero_is_a_real_zero_and_stops_stay_in_its_own_denominator(self):
        telemetry=self.telemetry()
        telemetry.channels['Speed'][:]=0
        telemetry.channels['PlayerCarMyIncidentCount']=np.zeros(11,dtype=np.int32)
        result=build_observation_metrics(telemetry,self.observation())
        self.assertIsNone(result['driving']['time_s'])
        self.assertEqual(result['incidents']['increase_points'],0)
        self.assertAlmostEqual(result['incidents']['observed_time_s'],1.)
        telemetry.channels['PlayerCarMyIncidentCount'][6:]=1
        incidents=build_observation_metrics(telemetry,self.observation())['incidents']
        self.assertEqual(incidents['increase_points'],1)
        self.assertEqual(incidents['scope'],'ON_TRACK_OBSERVED_TIME')

    def test_counter_reset_and_held_values_are_not_new_points(self):
        telemetry=self.telemetry()
        telemetry.channels['PlayerCarMyIncidentCount']=np.array([7,7,0,0,1,1,1,1,1,1,1])
        incidents=build_observation_metrics(telemetry,self.observation())['incidents']
        self.assertEqual(incidents['increase_points'],1)
        self.assertAlmostEqual(incidents['observed_time_s'],.9)
        self.assertEqual(incidents['intervals'],[[0.,.1],[.2,1.]])
        self.assertIn('INCIDENT_COUNTER_RESET',incidents['reason_codes'])

    def test_counter_jumps_across_gaps_session_changes_and_off_track_are_excluded(self):
        for mode,reason in [('gap','SAMPLE_GAP'),('session','SESSION_CHANGE'),('track','OFF_TRACK_SAMPLES_EXCLUDED')]:
            with self.subTest(mode=mode):
                telemetry=self.telemetry()
                telemetry.channels['PlayerCarMyIncidentCount']=np.array([0,0,0,4,4,4,4,5,5,5,5])
                if mode=='gap':telemetry.channels['SessionTime'][3:]+=.5
                elif mode=='session':telemetry.channels['SessionNum'][3:]=1
                else:telemetry.channels['IsOnTrack'][2:4]=0
                incidents=build_observation_metrics(telemetry,self.observation())['incidents']
                self.assertEqual(incidents['increase_points'],1)
                self.assertIn(reason,incidents['reason_codes'])
                self.assertLess(incidents['observed_time_s'],1.)

    def test_counter_unknown_replay_is_partial_and_confirmed_replay_points_are_excluded(self):
        telemetry=self.telemetry()
        telemetry.channels['PlayerCarMyIncidentCount']=np.array([0,0,0,4,4,4,4,5,5,5,5])
        telemetry.channels.pop('IsReplayPlaying')
        incidents=build_observation_metrics(telemetry,self.observation())['incidents']
        self.assertEqual((incidents['status'],incidents['replay_status'],incidents['increase_points']),('PARTIAL','UNKNOWN',5))
        telemetry.channels['IsReplayPlaying']=np.array([0,0,1,1,1,0,0,0,0,0,0])
        incidents=build_observation_metrics(telemetry,self.observation())['incidents']
        self.assertEqual(incidents['increase_points'],1)
        self.assertIn('REPLAY_SAMPLES_EXCLUDED',incidents['reason_codes'])
        telemetry.channels['IsReplayPlaying'][:]=1
        incidents=build_observation_metrics(telemetry,self.observation())['incidents']
        self.assertEqual(incidents['status'],'UNAVAILABLE')
        self.assertIsNone(incidents['increase_points'])

    def test_malformed_counter_samples_never_become_zero_incidents(self):
        for values in [True,[True]*11,['0']*11,[0]*10,[[0]*11],[-1]*11,[.5]*11,[np.nan]*11,[np.inf]*11]:
            with self.subTest(values=values):
                telemetry=self.telemetry()
                telemetry.channels['PlayerCarMyIncidentCount']=values
                incidents=build_observation_metrics(telemetry,self.observation())['incidents']
                self.assertEqual(incidents['status'],'UNAVAILABLE')
                self.assertIsNone(incidents['increase_points'])

    def test_quality_unknown_replay_and_discontinuous_laps_do_not_claim_official_validity(self):
        telemetry=self.telemetry()
        telemetry.channels.pop('IsReplayPlaying')
        quality=build_observation_metrics(telemetry,self.observation())['lap_quality']
        self.assertEqual((quality['status'],quality['completed_laps'][0]['analysis_validity']),('PARTIAL','VALID'))
        telemetry.channels['SessionTime'][5:]+=.5
        quality=build_observation_metrics(telemetry,self.observation())['lap_quality']
        self.assertEqual(quality['completed_laps'][0]['analysis_validity'],'UNKNOWN')
        self.assertEqual(quality['official_validity'],'UNKNOWN')
        self.assertIn('LAP_TIME_DISCONTINUITY',quality['reason_codes'])

    def test_incident_segment_merge_does_not_join_a_gap_or_charge_the_next_initial_count(self):
        telemetry=self.telemetry()
        telemetry.channels['PlayerCarMyIncidentCount']=np.array([0]*5+[7]*6)
        before=build_observation_metrics(telemetry,self.observation(),end_idx=5)
        after=build_observation_metrics(telemetry,self.observation(),start_idx=6)
        joined=merge_observation_metrics(before,after)['incidents']
        self.assertEqual(joined['increase_points'],0)
        self.assertEqual(joined['intervals'],[[0.,.4],[.6,1.]])
        self.assertAlmostEqual(joined['observed_time_s'],.8)
        self.assertEqual(joined['events'],[])

    def test_iracing_time_distance_and_lap_coordinates_use_the_same_offset(self):
        telemetry = self.telemetry()
        metrics = build_observation_metrics(telemetry, self.observation(), offset=1800000000.)
        driving = metrics['driving']
        self.assertEqual(driving['status'], 'OBSERVED')
        self.assertEqual(driving['replay_status'], 'CONFIRMED_NON_REPLAY')
        self.assertAlmostEqual(driving['time_s'], 1.)
        self.assertAlmostEqual(driving['distance_m'], 15.)
        self.assertEqual(driving['intervals'], [[1800000000., 1800000001.]])
        lap, = metrics['laps']
        self.assertTrue(lap['measurable'])
        self.assertEqual(lap['replay_status'], 'CONFIRMED_NON_REPLAY')
        self.assertEqual(lap['reason_codes'], [])
        self.assertEqual((lap['start_s'], lap['end_s']), (1800000000., 1800000001.))
        self.assertEqual(lap['official_validity'], 'UNKNOWN')
        self.assertEqual(lap['conditions']['fuel_level_l'], 40.)
        self.assertEqual(lap['conditions']['tire_compound'], 0.)
        self.assertEqual(len(lap['conditions']['setup_hash']), 64)

    def test_missing_required_signals_never_turn_into_zero_driving(self):
        for channel in ['Speed', 'IsOnTrack', 'SessionNum']:
            with self.subTest(channel=channel):
                telemetry = self.telemetry()
                telemetry.channels.pop(channel)
                driving = build_observation_metrics(telemetry, self.observation())['driving']
                self.assertEqual(driving['status'], 'UNAVAILABLE')
                self.assertIsNone(driving['time_s'])
                self.assertIsNone(driving['distance_m'])
                self.assertEqual(driving['intervals'], [])

    def test_absent_or_nan_replay_state_preserves_unknown_observed_motion_and_laps(self):
        for mode in ['absent', 'all_nan', 'some_nan']:
            with self.subTest(mode=mode):
                telemetry = self.telemetry()
                if mode == 'absent':
                    telemetry.channels.pop('IsReplayPlaying')
                else:
                    telemetry.channels['IsReplayPlaying'][slice(None) if mode == 'all_nan' else 5] = np.nan
                original = copy.deepcopy(telemetry.channels)
                result = build_observation_metrics(telemetry, self.observation())
                driving, lap = result['driving'], result['laps'][0]
                self.assertEqual(result['calculation_version'], 'observed-metrics-v4')
                self.assertEqual(driving['status'], 'PARTIAL')
                self.assertEqual(driving['replay_status'], 'UNKNOWN')
                self.assertIn('REPLAY_STATE_UNAVAILABLE', driving['reason_codes'])
                self.assertAlmostEqual(driving['time_s'], 1.)
                self.assertAlmostEqual(driving['distance_m'], 15.)
                self.assertTrue(lap['measurable'])
                self.assertEqual(lap['replay_status'], 'UNKNOWN')
                self.assertIn('REPLAY_STATE_UNAVAILABLE', lap['reason_codes'])
                self.assertEqual(lap['official_validity'], 'UNKNOWN')
                self.assertEqual(set(telemetry.channels), set(original))
                for name in original:
                    np.testing.assert_array_equal(telemetry.channels[name], original[name])

    def test_malformed_replay_arrays_are_not_treated_as_an_absent_channel(self):
        for values in [None, 0, [0] * 10, [[0] * 11], ['0'] * 11, [np.inf] * 11, [2] * 11]:
            with self.subTest(values=values):
                telemetry = self.telemetry()
                telemetry.channels['IsReplayPlaying'] = values
                result = build_observation_metrics(telemetry, self.observation())
                self.assertEqual(result['driving']['status'], 'UNAVAILABLE')
                self.assertEqual(result['driving']['replay_status'], 'INVALID')
                self.assertIn('REPLAY_STATE_INVALID', result['driving']['reason_codes'])
                self.assertFalse(result['laps'][0]['measurable'])
                self.assertEqual(result['laps'][0]['replay_status'], 'INVALID')

    def test_known_replay_remains_excluded_with_unknown_samples(self):
        telemetry = self.telemetry()
        telemetry.channels['IsReplayPlaying'][:] = np.nan
        telemetry.channels['IsReplayPlaying'][4:7] = 1
        result = build_observation_metrics(telemetry, self.observation())
        self.assertEqual(result['driving']['replay_status'], 'MIXED')
        self.assertEqual(result['driving']['intervals'], [[0., .3], [.7, 1.]])
        self.assertAlmostEqual(result['driving']['time_s'], .6)
        self.assertIn('REPLAY_SAMPLES_EXCLUDED', result['driving']['reason_codes'])
        self.assertFalse(result['laps'][0]['measurable'])
        self.assertEqual(result['laps'][0]['replay_status'], 'MIXED')

    def test_all_replay_is_unavailable_and_never_a_measured_lap(self):
        telemetry = self.telemetry()
        telemetry.channels['IsReplayPlaying'][:] = 1
        result = build_observation_metrics(telemetry, self.observation())
        self.assertEqual(result['driving']['status'], 'UNAVAILABLE')
        self.assertEqual(result['driving']['replay_status'], 'CONFIRMED_REPLAY')
        self.assertFalse(result['laps'][0]['measurable'])

    def test_replay_evidence_is_evaluated_within_each_individual_lap(self):
        telemetry = self.telemetry()
        telemetry.channels['IsReplayPlaying'][0] = 2
        telemetry.channels['IsReplayPlaying'][1] = 1
        telemetry.laps[0].start_idx = 3
        telemetry.laps[0].lap_time_s = .7
        result = build_observation_metrics(telemetry, self.observation())
        self.assertEqual(result['driving']['replay_status'], 'INVALID')
        self.assertTrue(result['laps'][0]['measurable'])
        self.assertEqual(result['laps'][0]['replay_status'], 'CONFIRMED_NON_REPLAY')
        self.assertEqual(result['laps'][0]['reason_codes'], [])

    def test_global_gaps_and_duplicates_do_not_block_an_individually_continuous_lap(self):
        for times, reason in [([0., .1, 1., 1.1, 1.2], 'SAMPLE_GAP'),
                              ([0., .1, .1, .2, .3], 'DUPLICATE_SESSION_TIME')]:
            with self.subTest(reason=reason):
                telemetry = self.telemetry(times=times)
                telemetry.laps[0].start_idx = 2
                telemetry.laps[0].lap_time_s = .2
                telemetry.channels.pop('IsReplayPlaying')
                lap, = build_observation_metrics(telemetry, self.observation(reasons=[reason]))['laps']
                self.assertTrue(lap['measurable'])
                self.assertEqual(lap['replay_status'], 'UNKNOWN')

    def test_lap_reasons_explain_ineligible_inputs_without_promoting_official_validity(self):
        for field, value, reason in [('complete', False, 'INCOMPLETE_LAP'),
                                     ('status', 'INVALID', 'ANALYSIS_LAP_INVALID'),
                                     ('pit_samples', 1, 'PIT_SAMPLES_PRESENT'),
                                     ('lap_time_s', 90., 'LAP_DURATION_MISMATCH')]:
            with self.subTest(field=field):
                telemetry = self.telemetry()
                setattr(telemetry.laps[0], field, value)
                lap, = build_observation_metrics(telemetry, self.observation())['laps']
                self.assertFalse(lap['measurable'])
                self.assertIn(reason, lap['reason_codes'])
                self.assertEqual(lap['official_validity'], 'UNKNOWN')

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

    def test_segment_merge_retains_unknown_replay_evidence_without_new_edges(self):
        telemetry = self.telemetry()
        before = build_observation_metrics(telemetry, self.observation(), end_idx=5)
        telemetry.channels.pop('IsReplayPlaying')
        after = build_observation_metrics(telemetry, self.observation(), start_idx=6)
        joined = merge_observation_metrics(before, after)
        self.assertEqual(joined['calculation_version'], 'observed-metrics-v4')
        self.assertEqual(joined['driving']['status'], 'PARTIAL')
        self.assertEqual(joined['driving']['replay_status'], 'UNKNOWN')
        self.assertEqual(joined['driving']['intervals'], [[0., .4], [.6, 1.]])

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
