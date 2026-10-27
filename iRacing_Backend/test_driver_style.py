"""Input patterns are observations, never skill or personality scores."""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest

import numpy as np

from driver_history_metrics import build_observation_metrics
from driver_style import measure_inputs, build_style


def telemetry(hz=20, game='iracing'):
    time = np.arange(0, 12 + 1 / hz, 1 / hz)
    brake = np.zeros(len(time))
    steer = np.zeros(len(time))
    throttle = np.zeros(len(time))
    for start in (1., 5., 9.):
        brake[(time >= start) & (time < start + 1)] = .6
        steer[(time >= start + .3) & (time < start + 2)] = .2
        ramp = (time >= start + 1) & (time <= start + 2)
        throttle[ramp] = time[ramp] - start - 1
        throttle[(time > start + 2) & (time < start + 3)] = 1.
    return SimpleNamespace(tick_rate=hz, channels={
        'SessionTime': time, 'Speed': np.full(len(time), 20.), 'IsOnTrack': np.ones(len(time)),
        'SessionNum': np.zeros(len(time)), 'IsReplayPlaying': np.zeros(len(time)),
        'Brake': brake, 'SteeringWheelAngle': steer, 'Throttle': throttle},
        metadata={'game': game}, setup={}, laps=[SimpleNamespace(lap_number=1, start_idx=0,
            end_idx=len(time)-1, lap_time_s=float(time[-1]), complete=True, status='VALID', pit_samples=0)])


def measured_input(**changes):
    value = measure_inputs(telemetry(), 0, 240, measurable=True,
                           replay_status='CONFIRMED_NON_REPLAY', game='iracing')
    value.update(changes)
    return value


def drive(index, *, identity='NATIVE', day=None, car='car', inputs=None, replay='CONFIRMED_NON_REPLAY'):
    return {'drive_id': str(index), 'identity_status': identity, 'game': 'iracing',
        'recorded_at': f'2026-10-{day or 20 + index:02d}T10:00:00Z', 'car_key': car,
        'track_key': 'track', 'layout_key': 'gp', 'session_type': 'PRACTICE',
        'labels': {'car': car, 'track': 'track'},
        'test_laps': [{'inputs': deepcopy(inputs or measured_input()), 'replay_status': replay}] * 2}


class DriverStyleTests(unittest.TestCase):
    def test_actual_brake_steer_overlap_and_throttle_rises_are_compact(self):
        result = measured_input()
        self.assertEqual(result['status'], 'OBSERVED')
        self.assertEqual(result['brake_steer']['event_count'], 3)
        self.assertEqual(result['brake_steer']['linked_count'], 3)
        self.assertGreater(result['brake_steer']['overlap_pct'], 60)
        self.assertEqual(result['throttle']['event_count'], 3)
        self.assertGreater(result['throttle']['rise_median_s'], .5)
        self.assertLess(len(str(result)), 1500)

    def test_missing_channel_blocks_only_its_axis_and_unknown_replay_remains_partial(self):
        sample = telemetry()
        sample.channels.pop('SteeringWheelAngle')
        result = measure_inputs(sample, 0, 240, measurable=True, replay_status='UNKNOWN', game='iracing')
        self.assertIsNone(result['brake_steer']['overlap_pct'])
        self.assertEqual(result['throttle']['event_count'], 3)
        self.assertEqual(result['status'], 'PARTIAL')
        self.assertIn('STEERING_INPUT_UNAVAILABLE', result['reason_codes'])
        self.assertIn('REPLAY_STATE_UNAVAILABLE', result['reason_codes'])

    def test_gap_invalid_signal_pit_replay_and_insufficient_rate_do_not_make_patterns(self):
        for change in ('gap', 'invalid', 'pit', 'replay', 'rate'):
            with self.subTest(change=change):
                sample = telemetry()
                replay, measurable = 'CONFIRMED_NON_REPLAY', True
                if change == 'gap': sample.channels['SessionTime'][30] += .8
                if change == 'invalid': sample.channels['Throttle'][:] = 101.
                if change == 'pit': measurable = False
                if change == 'replay': replay = 'CONFIRMED_REPLAY'
                if change == 'rate': sample.tick_rate = 2
                result = measure_inputs(sample, 0, 240, measurable=measurable, replay_status=replay, game='iracing')
                self.assertEqual(result['throttle']['event_count'], 0)

    def test_metrics_attach_input_facts_only_to_measurable_laps(self):
        sample = telemetry()
        observation = {'game': 'iracing', 'coverage': {'intervals': [[0., 12.]], 'status': 'OBSERVED', 'reasons': []}}
        result = build_observation_metrics(sample, observation)
        self.assertIn('inputs', result['laps'][0])
        self.assertEqual(result['laps'][0]['inputs']['brake_steer']['linked_count'], 3)
        sample.laps[0].pit_samples = 1
        result = build_observation_metrics(sample, observation)
        self.assertEqual(result['laps'][0]['inputs']['status'], 'UNAVAILABLE')

    def test_source_only_is_useful_without_a_persistent_style_name(self):
        rows = [drive(1, identity='SOURCE_ONLY')]
        result = build_style(rows, {row['drive_id']: (row['test_laps'], set()) for row in rows})
        self.assertEqual(result['status'], 'PARTIAL')
        self.assertEqual(len(result['items']), 2)
        self.assertTrue(all(item['status'] == 'OBSERVATION_ONLY' for item in result['items']))
        self.assertEqual(result['items'][0]['session_count'], 0)
        self.assertGreater(result['items'][0]['value'], 0)

    def test_repetition_requires_three_independent_sessions_on_two_days(self):
        rows = [drive(index) for index in (1, 2, 3)]
        selected = {row['drive_id']: (row['test_laps'], set()) for row in rows}
        result = build_style(rows, selected)
        self.assertEqual(result['items'][0]['title'], '제동 연결형')
        self.assertEqual(result['items'][0]['status'], 'REPEATED')
        self.assertEqual(result['items'][0]['session_count'], 3)
        for row in rows: row['recorded_at'] = '2026-10-20T10:00:00Z'
        result = build_style(rows, selected)
        self.assertEqual(result['items'][0]['status'], 'OBSERVATION_ONLY')

    def test_long_combination_has_equal_weight_and_opposing_evidence_is_visible(self):
        linked = measured_input()
        separated = deepcopy(linked)
        separated['brake_steer'].update(linked_count=0, separated_count=3, overlap_pct=0.)
        rows = [drive(index, day=20, car='long', inputs=linked) for index in range(20)]
        rows += [drive(21, day=21, car='short', inputs=separated)]
        result = build_style(rows, {row['drive_id']: (row['test_laps'], set()) for row in rows})
        brake = result['items'][0]
        self.assertLess(brake['value'], 50)
        self.assertGreater(brake['counter_evidence']['event_count'], 0)
        self.assertEqual(result['scope_count'], 2)
        self.assertEqual(result['weighting'], 'EQUAL_COMBINATION_AND_DAY')

    def test_lmu_uses_native_period_not_interpolated_sixty_hz_and_keeps_nominal_scale_explicit(self):
        sample = telemetry(game='lmu')
        sample.metadata['history_input_sampling'] = {'native_hz': dict.fromkeys(
            ('Speed', 'Brake', 'Throttle', 'SteeringWheelAngle'), 20.), 'interpolated': True,
            'native_units': {'Speed':'km/h','Brake':'%','Throttle':'%','SteeringWheelAngle':'%'},
            'pit_state_available': True, 'pit_state_start_s': 0., 'steering_half_range_rad': 5.}
        result = measure_inputs(sample, 0, 240, measurable=True, replay_status='UNKNOWN', game='lmu')
        self.assertGreater(result['throttle']['event_count'], 0)
        self.assertIn('INTERPOLATED_NATIVE_INPUTS', result['reason_codes'])
        self.assertEqual(result['throttle']['sampling_hz'], 20.)
        sample.metadata['history_input_sampling']['native_hz']['Throttle'] = 2.
        result = measure_inputs(sample, 0, 240, measurable=True, replay_status='UNKNOWN', game='lmu')
        self.assertEqual(result['throttle']['event_count'], 0)
        sample.metadata['history_input_sampling']['native_hz']['Throttle'] = 20.
        sample.metadata['history_input_sampling']['native_units']['Throttle'] = 'unknown'
        result = measure_inputs(sample, 0, 240, measurable=True, replay_status='UNKNOWN', game='lmu')
        self.assertEqual(result['throttle']['event_count'], 0)
        self.assertIn('INPUT_UNITS_UNCONFIRMED', result['reason_codes'])
        sample.metadata['history_input_sampling']['pit_state_start_s'] = 5.
        result = measure_inputs(sample, 0, 240, measurable=True, replay_status='UNKNOWN', game='lmu')
        self.assertEqual(result['status'], 'UNAVAILABLE')

    def test_unconfirmed_files_cannot_reverse_what_native_repeats_support(self):
        separated = measured_input()
        separated['brake_steer'].update(linked_count=0, separated_count=3, overlap_pct=0.)
        rows = [drive(index, inputs=separated) for index in (1, 2, 3)]
        rows += [drive(index+100, day=index+1, identity='SOURCE_ONLY') for index in range(20)]
        result = build_style(rows, {row['drive_id']: (row['test_laps'], set()) for row in rows})
        self.assertEqual(result['items'][0]['status'], 'OBSERVATION_ONLY')
        self.assertNotEqual(result['items'][0]['title'], '제동 연결형')

    def test_imported_reference_and_nonfinite_values_never_enter_input_observations(self):
        sample = telemetry()
        sample.metadata['imported'] = True
        result = measure_inputs(sample, 0, 240, measurable=True,
                                replay_status='CONFIRMED_NON_REPLAY', game='iracing')
        self.assertEqual(result['status'], 'UNAVAILABLE')
        self.assertIn('IMPORTED_REFERENCE', result['reason_codes'])


if __name__ == '__main__':
    unittest.main()
