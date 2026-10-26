"""A possibility notice must be supported by lap-wide, repeated observations."""
import copy
import unittest
from types import SimpleNamespace

import numpy as np

from setup_influence import setup_influence_notice


def fixture():
    x = np.linspace(0., 1., 2001)
    corners = [{'id': f'C{i}', 'entry_pct': a, 'exit_pct': b}
               for i, (a, b) in enumerate(((.1, .2), (.4, .5), (.7, .8)), 1)]
    # Three -0.10 s corner gains, two +0.25 s straight losses: +0.20 s overall.
    delta = np.interp(x, [0, .1, .2, .22, .38, .4, .5, .52, .68, .7, .8, 1],
                      [0, 0, -.1, -.1, .15, .15, .05, .05, .3, .3, .2, .2])
    ref = {'distance_pct': x, 'time_s': x * 100, 'speed_kph': x * 0 + 240,
           'Throttle': x * 0 + 1, 'Brake': x * 0, 'SteeringWheelAngle': x * 0}
    own = {key: value.copy() for key, value in ref.items()}
    own['time_s'] += delta
    segments = []
    for a, b in ((.22, .38), (.52, .68)):
        mask = (x >= a) & (x <= b)
        own['speed_kph'][mask] -= np.linspace(0, 5, mask.sum())
        segments.append({'start_pct': a, 'end_pct': b, 'distance_m': 800,
                         'entry_speed_deficit_kph': 0., 'late_speed_deficit_kph': 4., 'gap_growth_kph': 4.})
    return dict(target_grid=own, reference_grid=ref, corners=corners,
                straight={'status': 'READY', 'segments': segments}, pace_eligible=True,
                lap_delta_s=.2, current_conditions={}, reference_conditions=[{'condition_similarity': {'score': .5}}])


class SetupInfluenceTests(unittest.TestCase):
    def call(self, **changes):
        data = fixture(); data.update(changes)
        return setup_influence_notice(**data)

    def test_single_reference_can_show_a_possibility_after_two_distinct_straights(self):
        result = self.call()
        self.assertEqual(result['status'], 'POSSIBLE')
        self.assertAlmostEqual(result['evidence']['corner_delta_s'], -.3)
        self.assertAlmostEqual(result['evidence']['outside_corner_delta_s'], .5)
        self.assertEqual(result['evidence']['supporting_straights'], 2)
        self.assertFalse(result['setup_cause_confirmed'])
        self.assertNotIn('candidate', result)

    def test_missing_or_invalid_lap_timing_does_not_make_a_setup_claim(self):
        for changes in ({'pace_eligible': False}, {'lap_delta_s': None}, {'lap_delta_s': float('nan')}, {'lap_delta_s': -.1}):
            with self.subTest(changes=changes):
                self.assertNotEqual(self.call(**changes)['status'], 'POSSIBLE')

    def test_exit_speed_loss_without_late_gap_growth_is_not_a_setup_signal(self):
        data = fixture()
        for segment in data['straight']['segments']:
            segment.update(entry_speed_deficit_kph=6, late_speed_deficit_kph=6, gap_growth_kph=0)
        data['target_grid']['speed_kph'][:] = data['reference_grid']['speed_kph'] - 6
        self.assertEqual(setup_influence_notice(**data)['reason'], 'NO_REPEATED_STRAIGHT_SUPPORT')

    def test_one_straight_or_two_fragments_of_the_same_straight_do_not_count_as_repeated(self):
        data = fixture()
        data['straight']['segments'] = [data['straight']['segments'][0]]
        data['straight']['segments'].append({**data['straight']['segments'][0], 'start_pct': .24, 'end_pct': .36})
        self.assertEqual(setup_influence_notice(**data)['reason'], 'NO_REPEATED_STRAIGHT_SUPPORT')

    def test_start_and_end_of_lap_are_one_straight(self):
        data = fixture()
        data['lap_delta_s'] = .3
        x = data['target_grid']['distance_pct']
        data['target_grid']['time_s'] = data['reference_grid']['time_s'] + np.interp(
            x, [0, .1, .2, .4, .5, .7, .8, 1], [0, .3, .2, .2, .1, .1, 0, .3])
        data['straight']['segments'] = [{**data['straight']['segments'][0], 'start_pct': a, 'end_pct': b}
                                      for a, b in ((.01, .09), (.81, .99))]
        for a, b in ((.01, .09), (.81, .99)):
            mask = (x >= a) & (x <= b)
            data['target_grid']['speed_kph'][mask] -= np.linspace(0, 5, mask.sum())
        result = setup_influence_notice(**data)
        self.assertNotEqual(result['status'], 'POSSIBLE')
        self.assertEqual(result['evidence']['supporting_straights'], 1)

    def test_major_lift_brake_or_steering_in_a_straight_suppresses_notice(self):
        for key, value in (('Throttle', .7), ('Brake', .2), ('SteeringWheelAngle', .4)):
            data = fixture()
            mask = (data['target_grid']['distance_pct'] > .22) & (data['target_grid']['distance_pct'] < .38)
            data['target_grid'][key][mask] = value
            with self.subTest(key=key):
                self.assertNotEqual(setup_influence_notice(**data)['status'], 'POSSIBLE')

    def test_missing_channels_nan_holes_and_nonmonotonic_time_are_insufficient(self):
        for change in ('channel', 'nan', 'coverage', 'time', 'axis'):
            data = fixture()
            if change == 'channel': del data['target_grid']['Brake']
            if change == 'nan': data['reference_grid']['time_s'][900] = np.nan
            if change == 'coverage':
                data['target_grid']['_coverage'] = np.ones(2001, dtype=bool)
                data['target_grid']['_coverage'][450] = False
            if change == 'time': data['target_grid']['time_s'][500] = -1
            if change == 'axis': data['reference_grid']['distance_pct'][500] += .1
            with self.subTest(change=change):
                self.assertEqual(setup_influence_notice(**data)['status'], 'INSUFFICIENT')

    def test_known_condition_differences_suppress_the_setup_notice(self):
        for current, reference in (({}, {'condition_similarity': {'setup_compatible': False}}),
                                   ({'fuel_l': 60}, {'fuel_l': 10}),
                                   ({'track_temp_c': 50}, {'track_temp_c': 25}),
                                   ({'wind_speed_kph': 30}, {'wind_speed_kph': 0}),
                                   ({}, {'condition_similarity': {'score': .3}})):
            with self.subTest(current=current, reference=reference):
                self.assertEqual(self.call(current_conditions=current, reference_conditions=[reference])['reason'], 'KNOWN_CONDITION_DIFFERENCE')

    def test_unknown_condition_values_are_not_invented_or_treated_as_matching(self):
        result = self.call(reference_conditions=[{}])
        self.assertEqual(result['status'], 'POSSIBLE')
        self.assertIn('UNCONTROLLED_REFERENCE_CONDITIONS', result['limitations'])

    def test_one_good_corner_or_overlapping_corner_definitions_cannot_drive_lap_wide_notice(self):
        data = fixture()
        self.assertNotEqual(self.call(corners=data['corners'][:1])['status'], 'POSSIBLE')
        data['corners'][1]['entry_pct'] = .15
        self.assertEqual(setup_influence_notice(**data)['status'], 'INSUFFICIENT')

    def test_all_corner_loss_and_tiny_differences_do_not_show(self):
        data = fixture()
        data['target_grid']['time_s'] = data['reference_grid']['time_s'] * 1.002
        self.assertNotEqual(setup_influence_notice(**data)['status'], 'POSSIBLE')
        self.assertNotEqual(self.call(lap_delta_s=.01)['status'], 'POSSIBLE')

    def test_inputs_are_not_mutated(self):
        data = fixture(); before = copy.deepcopy(data)
        setup_influence_notice(**data)
        for grid in ('target_grid', 'reference_grid'):
            for key in data[grid]: np.testing.assert_array_equal(data[grid][key], before[grid][key])
        self.assertEqual(data['corners'], before['corners'])
        self.assertEqual(data['straight'], before['straight'])

    def test_trace_loss_must_match_the_displayed_comparison(self):
        self.assertEqual(self.call(lap_delta_s=2.)['reason'], 'INCONSISTENT_LAP_TIMING')

    def test_speed_gap_without_material_full_throttle_time_loss_is_not_enough(self):
        data = fixture()
        # Most of the delay was before the full-throttle part of these straights.
        x = data['target_grid']['distance_pct']
        data['target_grid']['time_s'] = data['reference_grid']['time_s'] + np.interp(
            x, [0, .1, .2, .22, .38, .4, .5, .52, .68, .7, .8, 1],
            [0, 0, -.1, .11, .15, .15, .05, .26, .3, .3, .2, .2])
        self.assertEqual(setup_influence_notice(**data)['reason'], 'STRAIGHT_SUPPORT_TOO_SMALL')

    def test_synthetic_nonuniform_distance_axis_uses_interpolation_not_point_counts(self):
        data = fixture()
        for grid in ('target_grid', 'reference_grid'):
            old = data[grid]
            new_x = np.linspace(0, 1, 2001) ** 1.2
            data[grid] = {key: new_x if key == 'distance_pct' else np.interp(new_x, old['distance_pct'], value)
                          for key, value in old.items()}
        self.assertEqual(setup_influence_notice(**data)['status'], 'POSSIBLE')

    def test_real_straight_detector_and_notice_work_together(self):
        from analysis_external import ExternalAnalysis
        data = fixture()
        # Break the full-throttle regions at the actual corners so detection
        # does not interpret the entire lap as one candidate straight.
        for grid in (data['target_grid'], data['reference_grid']):
            x = grid['distance_pct']
            for corner in data['corners']:
                grid['Throttle'][(x >= corner['entry_pct']) & (x <= corner['exit_pct'])] = .5
        data['straight'] = ExternalAnalysis._straight_performance_diagnostics(
            SimpleNamespace(track_length_m=6000.), data['target_grid'], data['reference_grid'], {}, 1, 1, .5, False)
        self.assertEqual(data['straight']['status'], 'READY')
        self.assertEqual(setup_influence_notice(**data)['status'], 'POSSIBLE')

    def test_workspace_adapter_delivers_only_the_current_external_notice(self):
        from unittest.mock import patch
        from workspace_routes import workspace_comparison
        data = fixture()
        notice = setup_influence_notice(**data)
        external = {'status': 'READY', 'aggregate': {'target_time_s': 100.2, 'reference_time_s': 100.,
                                                  'gap_to_reference_s': .2},
                    'corners': data['corners'], 'performance_diagnostics': {'setup_influence': notice}}
        target = SimpleNamespace(valid=True)
        analyzer = SimpleNamespace(_select_laps=lambda *args: (target, None, None),
                                   _lap_grid=lambda *args: data['target_grid'],
                                   _external_reference_analysis=lambda *args: external)
        services = SimpleNamespace(get_telemetry_analyzer=lambda: lambda telemetry: analyzer,
                                   get_ibt_reader=lambda: None, comparison_detail_payload=lambda *args: {})
        with patch('workspace_routes.comparison_controls', return_value={}):
            result = workspace_comparison(services, None, {'target_lap': 1, 'reference_mode': 'SINGLE',
                                                         'external_reference_files': ['synthetic.csv']})
        self.assertEqual(result['setupInfluence'], notice)
        self.assertEqual(result['delta'], .2)

    def test_full_throttle_before_corner_exit_is_clipped_to_the_following_straight(self):
        data = fixture()
        for segment in data['straight']['segments']:
            segment['start_pct'] -= .04
            segment['distance_m'] *= 1.25
        self.assertEqual(setup_influence_notice(**data)['status'], 'POSSIBLE')

    def test_a_straight_too_short_after_clipping_is_not_support(self):
        data = fixture()
        data['straight']['segments'][0].update(start_pct=.15, end_pct=.27, distance_m=260.)
        self.assertEqual(setup_influence_notice(**data)['reason'], 'NO_REPEATED_STRAIGHT_SUPPORT')

    def test_a_large_exit_deficit_with_additional_straight_loss_still_does_not_make_this_claim(self):
        data = fixture()
        data['target_grid']['speed_kph'] -= 5
        self.assertEqual(setup_influence_notice(**data)['reason'], 'NO_REPEATED_STRAIGHT_SUPPORT')


if __name__ == '__main__':
    unittest.main()
