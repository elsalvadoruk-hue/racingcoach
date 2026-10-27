"""Input patterns are observations, never skill or personality scores."""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from pathlib import Path
import tempfile
import unittest

import numpy as np

from driver_history_metrics import build_observation_metrics
from driver_style import measure_inputs, build_style, validate_inputs


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
        'OnPitRoad':np.zeros(len(time)),
        'SessionNum': np.zeros(len(time)), 'IsReplayPlaying': np.zeros(len(time)),
        'Brake': brake, 'SteeringWheelAngle': steer, 'Throttle': throttle},
        metadata={'game': game}, setup={}, laps=[SimpleNamespace(lap_number=1, start_idx=0,
            end_idx=len(time)-1, lap_time_s=float(time[-1]), complete=True, status='VALID', pit_samples=0)])


def measured_input(**changes):
    value = measure_inputs(telemetry(), 0, 240, measurable=True,
                           replay_status='CONFIRMED_NON_REPLAY', game='iracing')
    value.update(changes)
    return value


def extended_telemetry(*,modulated=False,corrections=False,spread=False):
    sample=telemetry(hz=50)
    time=np.arange(0.,32.02,.02);phase=np.mod(time,5.)
    brake=np.interp(phase,[0.,.4,1.3,1.6,2.5,2.7,5.],[0.,0.,.8,.8,0.,0.,0.])
    steer=np.interp(phase,[0.,.7,1.9,2.3,3.5,3.7,5.],[0.,0.,.4,.4,0.,0.,0.])
    throttle=np.interp(phase,[0.,2.5,2.8,3.2,4.4,4.6,5.],[0.,0.,.9,.9,.9,0.,0.])
    if modulated:
        throttle[(phase>=3.25)&(phase<3.65)]=.5
    if corrections:
        steer[(phase>=2.)&(phase<2.4)]=.2
        steer[(phase>=2.4)&(phase<2.7)]=.4
    gear=np.where(phase<3.9,2.,3.)
    rpm=np.full(len(time),6200.)
    if spread:
        for cycle,level in enumerate([4000.,5000.,6000.,7000.,8000.,9000.,10000.]):
            rpm[(time>=cycle*5)&(time<(cycle+1)*5)]=level
    sample.tick_rate=50
    sample.channels=dict(SessionTime=time,SessionNum=np.zeros(len(time)),IsReplayPlaying=np.zeros(len(time)),
        Speed=np.full(len(time),25.),IsOnTrack=np.ones(len(time)),OnPitRoad=np.zeros(len(time)),
        Brake=brake,Throttle=throttle,SteeringWheelAngle=steer,Gear=gear,RPM=rpm)
    sample.laps=[SimpleNamespace(lap_number=1,start_idx=0,end_idx=len(time)-1,lap_time_s=float(time[-1]),
                                complete=True,status='VALID',pit_samples=0)]
    return sample


def extended_inputs(**kwargs):
    sample=extended_telemetry(**kwargs)
    return measure_inputs(sample,0,len(sample.channels['SessionTime'])-1,measurable=True,
                          replay_status='CONFIRMED_NON_REPLAY',game='iracing')


def drive(index, *, identity='NATIVE', day=None, car='car', inputs=None, replay='CONFIRMED_NON_REPLAY'):
    return {'drive_id': str(index), 'identity_status': identity, 'game': 'iracing',
        'recorded_at': f'2026-10-{day or 20 + index:02d}T10:00:00Z', 'car_key': car,
        'track_key': 'track', 'layout_key': 'gp', 'session_type': 'PRACTICE',
        'labels': {'car': car, 'track': 'track'},
        'test_laps': [{'inputs': deepcopy(inputs or measured_input()), 'replay_status': replay}] * 2}


class DriverStyleTests(unittest.TestCase):
    def test_sign_changes_split_steering_episodes_and_short_blips_do_not_become_corrections(self):
        sample=extended_telemetry();time=sample.channels['SessionTime'];steer=np.zeros(len(time))
        steer[(time>=1)&(time<2)]=.4;steer[(time>=2)&(time<3)]=-.4
        sample.channels['SteeringWheelAngle']=steer
        result=measure_inputs(sample,0,len(time)-1,measurable=True,replay_status='CONFIRMED_NON_REPLAY',game='iracing')
        self.assertEqual(result['steering_application']['event_count'],2)
        self.assertEqual(result['steering_corrections']['value'],0.)
        sample.channels['Gear'][:]=2.;sample.channels['Gear'][180]=3.
        sample.channels['Throttle'][:]=.9
        sample.channels['Throttle'][175]=.5
        result=measure_inputs(sample,0,len(time)-1,measurable=True,replay_status='CONFIRMED_NON_REPLAY',game='iracing')
        self.assertIsNone(result['shift_consistency']['value'])

    def test_slow_recovery_survives_native_period_roundoff_and_small_noise_is_ignored(self):
        sample=telemetry(hz=20);time=sample.channels['SessionTime'];phase=np.mod(time,4.)
        sample.channels['SteeringWheelAngle']=np.interp(phase,[0.,.5,1.6,2.1,2.5,2.9,3.5,4.],
                                                        [0.,0.,.3,.3,.2,.3,0.,0.])
        result=measure_inputs(sample,0,len(time)-1,measurable=True,replay_status='CONFIRMED_NON_REPLAY',game='iracing')
        self.assertEqual(result['steering_corrections']['value'],1.)
        sample=extended_telemetry();time=sample.channels['SessionTime'];phase=np.mod(time,5.)
        sample.channels['Throttle']+=np.where((phase>3.2)&(phase<4.3),.025*np.sin(time*100),0.)
        sample.channels['SteeringWheelAngle']+=np.where((phase>1.9)&(phase<2.3),.01*np.sin(time*100),0.)
        result=measure_inputs(sample,0,len(time)-1,measurable=True,replay_status='CONFIRMED_NON_REPLAY',game='iracing')
        self.assertEqual(result['throttle_modulation']['value'],0.)
        self.assertEqual(result['steering_corrections']['value'],0.)

    def test_extended_pit_replay_and_invalid_gaps_cannot_be_bridged_into_complete_episodes(self):
        for condition in ('pit','replay','gap'):
            sample=extended_telemetry();time=sample.channels['SessionTime'];replay='CONFIRMED_NON_REPLAY'
            if condition=='pit':sample.channels['OnPitRoad'][:]=1.
            if condition=='replay':replay='CONFIRMED_REPLAY'
            if condition=='gap':time[100]+=.5
            result=measure_inputs(sample,0,len(time)-1,measurable=True,replay_status=replay,game='iracing')
            self.assertTrue(all(result[name]['event_count']==0 for name in ('brake_application','brake_release',
                'throttle_modulation','steering_application','steering_corrections','coasting','shift_consistency')),condition)

    def test_coasting_uses_the_same_observed_edges_and_preserves_a_measured_zero(self):
        sample=extended_telemetry();time=sample.channels['SessionTime']
        sample.channels['Brake'][:]=0.;sample.channels['Throttle'][:]=0.
        result=measure_inputs(sample,0,len(time)-1,measurable=True,replay_status='CONFIRMED_NON_REPLAY',game='iracing')
        self.assertEqual(result['coasting']['value'],100.)
        self.assertEqual(result['coasting']['coasting_time_s'],result['coasting']['observed_time_s'])
        sample.channels['Throttle'][:]=1.
        result=measure_inputs(sample,0,len(time)-1,measurable=True,replay_status='CONFIRMED_NON_REPLAY',game='iracing')
        self.assertEqual(result['coasting']['value'],0.)
        self.assertEqual(result['coasting']['negative_count'],0)

    def test_lmu_shift_requires_native_rpm_units_and_timestamped_gear_evidence_not_gear_hz(self):
        sample=extended_telemetry();time=sample.channels['SessionTime'];sample.metadata['game']='lmu'
        sampling=dict(native_hz=dict.fromkeys(('Speed','Brake','Throttle','SteeringWheelAngle','RPM'),20.),
            native_units=dict(Speed='km/h',Brake='%',Throttle='%',SteeringWheelAngle='%',RPM='rpm'),
            pit_state_available=True,pit_state_start_s=0.,steering_half_range_rad=5.,
            gear_events_available=True,gear_event_start_s=0.)
        sample.metadata['history_input_sampling']=sampling
        result=measure_inputs(sample,0,len(time)-1,measurable=True,replay_status='UNKNOWN',game='lmu')
        self.assertGreater(result['shift_consistency']['event_count'],0)
        self.assertEqual(result['shift_consistency']['sampling_hz'],20.)
        self.assertAlmostEqual(result['shift_consistency']['timing_uncertainty_s'],.1)
        sampling['native_units']['RPM']='unknown'
        result=measure_inputs(sample,0,len(time)-1,measurable=True,replay_status='UNKNOWN',game='lmu')
        self.assertIsNone(result['shift_consistency']['value'])
        self.assertIn('INPUT_UNITS_UNCONFIRMED',result['shift_consistency']['reason_codes'])
    def test_all_seven_extended_axes_measure_actual_complete_native_waveforms(self):
        result=extended_inputs()
        for name in ('brake_application','brake_release','throttle_modulation','steering_application',
                     'steering_corrections','coasting','shift_consistency'):
            self.assertGreater(result[name]['event_count'],0,name)
            self.assertIsNotNone(result[name]['value'],name)
        self.assertGreater(result['brake_application']['value'],.45)
        self.assertGreater(result['brake_release']['value'],.45)
        self.assertGreater(result['steering_application']['value'],.6)
        self.assertEqual(result['throttle_modulation']['value'],0.)
        self.assertEqual(result['steering_corrections']['value'],0.)
        self.assertGreater(result['coasting']['value'],0.)
        self.assertEqual(result['shift_consistency']['value'],0.)
        self.assertEqual(validate_inputs(result),result)

    def test_real_modulation_corrections_and_rpm_dispersion_change_the_measured_axes(self):
        result=extended_inputs(modulated=True,corrections=True,spread=True)
        self.assertGreaterEqual(result['throttle_modulation']['value'],1.)
        self.assertGreaterEqual(result['steering_corrections']['value'],1.)
        self.assertGreater(result['shift_consistency']['value'],5.)
        self.assertGreater(result['shift_consistency']['negative_count'],0)

    def test_shift_rpm_is_never_pooled_across_target_gears_and_missing_channels_are_local(self):
        sample=extended_telemetry()
        time=sample.channels['SessionTime']
        # Each target gear is perfectly repeatable even though their RPM differ.
        for cycle in range(7):
            selected=(time>=cycle*5)&(time<(cycle+1)*5)
            if cycle%2:
                sample.channels['Gear'][selected]+=2
                sample.channels['RPM'][selected]=9000.
        result=measure_inputs(sample,0,len(time)-1,measurable=True,replay_status='CONFIRMED_NON_REPLAY',game='iracing')
        self.assertEqual(result['shift_consistency']['value'],0.)
        self.assertEqual({row['target_gear'] for row in result['shift_consistency']['gear_groups']},{3,5})
        sample.channels.pop('RPM')
        result=measure_inputs(sample,0,len(time)-1,measurable=True,replay_status='CONFIRMED_NON_REPLAY',game='iracing')
        self.assertIsNone(result['shift_consistency']['value'])
        self.assertGreater(result['brake_application']['event_count'],0)

    def test_extended_payload_rejects_impossible_counts_and_derived_denominators(self):
        for axis,key,value in [('brake_application','positive_count',999),('coasting','coasting_time_s',9999.),
                               ('shift_consistency','value',50.),('brake_release','value',float('nan')),
                               ('steering_application','timing_uncertainty_s',0.)]:
            result=extended_inputs();result[axis][key]=value
            with self.subTest(axis=axis),self.assertRaises(ValueError):validate_inputs(result)

    def test_extended_validation_requires_same_gear_groups_and_preserves_v1_without_current_marker(self):
        result=extended_inputs();result['shift_consistency'].pop('gear_groups')
        with self.assertRaises(ValueError):validate_inputs(result)
        result=extended_inputs();result.pop('brake_release')
        with self.assertRaises(ValueError):validate_inputs(result)
        result.pop('input_version')
        self.assertNotIn('brake_release',validate_inputs(result))

    def test_rpm_native_period_uncertainty_can_withhold_a_style_vote_without_hiding_observed_mad(self):
        sample=extended_telemetry();time=sample.channels['SessionTime'];phase=np.mod(time,5.)
        before_shift=(phase>=3.74)&(phase<3.9)
        sample.channels['RPM'][before_shift]=4000.+(phase[before_shift]-3.74)/.16*2200.
        sample.tick_rate=50
        result=measure_inputs(sample,0,len(time)-1,measurable=True,replay_status='CONFIRMED_NON_REPLAY',game='iracing')
        self.assertEqual(result['shift_consistency']['value'],0.)
        self.assertGreater(result['shift_consistency']['gear_groups'][0]['rpm_uncertainty_pct'],2.)
        self.assertEqual(result['shift_consistency']['positive_count'],0)
        self.assertEqual(result['shift_consistency']['negative_count'],0)
        self.assertEqual(validate_inputs(result),result)

    def test_extended_inputs_round_trip_through_an_isolated_history_store(self):
        from driver_history import DriverHistoryStore
        from driver_history_summary import build_source_summary
        with tempfile.TemporaryDirectory(prefix='style-nine-axis-') as folder:
            sample=extended_telemetry();sample.path=Path(folder)/'synthetic.ibt'
            summary=build_source_summary(sample,'a'*64)
            store=DriverHistoryStore(Path(folder)/'history.sqlite3');store.record_source(summary)
            saved=store.report_snapshot('iracing')['drives'][0]['observations'][0]['metrics']['laps'][0]['inputs']
        self.assertEqual(saved['shift_consistency']['gear_groups'][0]['count'],6)
        self.assertEqual(saved['input_version'],'driver-inputs-v2')

    def test_extended_axes_repeat_only_with_native_sessions_and_equal_day_scope_weight(self):
        inputs=extended_inputs();rows=[drive(index,inputs=inputs) for index in (1,2,3)]
        result=build_style(rows,{row['drive_id']:(row['test_laps'],set()) for row in rows})
        for name in ('brake_application','brake_release','throttle_modulation','steering_application',
                     'steering_corrections','shift_consistency'):
            item=next(item for item in result['items'] if item['axis']==name)
            self.assertEqual(item['status'],'REPEATED',name)
        for row in rows:row['identity_status']='SOURCE_ONLY'
        result=build_style(rows,{row['drive_id']:(row['test_laps'],set()) for row in rows})
        self.assertTrue(all(item['status']!='REPEATED' for item in result['items']))
    def test_legacy_two_axis_payload_is_accepted_and_report_retains_all_nine_axes(self):
        legacy=measured_input()
        legacy.pop('input_version',None)
        for axis in ('brake_application','brake_release','throttle_modulation','steering_application',
                     'steering_corrections','coasting','shift_consistency'):
            legacy.pop(axis,None)
        checked=validate_inputs(legacy)
        row=drive(1,inputs=checked)
        result=build_style([row],{row['drive_id']:(row['test_laps'],set())})
        self.assertEqual(len(result['items']),9)
        self.assertEqual(next(item for item in result['items'] if item['axis']=='shift_consistency')['status'],'UNAVAILABLE')
        for item in result['items']:
            for key in ('short_label','meaning','example','interpretation','calculation','names'):
                self.assertTrue(item[key])

    def test_new_input_version_is_present_even_when_measurement_is_unavailable(self):
        from driver_style import INPUT_CALCULATION_VERSION,DRIVER_INPUT_CHANNELS
        self.assertTrue({'Gear','RPM','OnPitRoad'}.issubset(DRIVER_INPUT_CHANNELS))
        result=measure_inputs(telemetry(),0,240,measurable=False,replay_status='UNKNOWN',game='iracing')
        self.assertEqual(result['input_version'],INPUT_CALCULATION_VERSION)
        self.assertIn('brake_application',result)

    def test_actual_brake_steer_overlap_and_throttle_rises_are_compact(self):
        result = measured_input()
        self.assertEqual(result['status'], 'OBSERVED')
        self.assertEqual(result['brake_steer']['event_count'], 3)
        self.assertEqual(result['brake_steer']['linked_count'], 3)
        self.assertGreater(result['brake_steer']['overlap_pct'], 60)
        self.assertEqual(result['throttle']['event_count'], 3)
        self.assertGreater(result['throttle']['rise_median_s'], .5)
        self.assertLess(len(str(result)), 4500)

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
        self.assertEqual(len(result['items']), 9)
        self.assertTrue(all(item['status'] in {'OBSERVATION_ONLY','UNAVAILABLE'} for item in result['items']))
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
