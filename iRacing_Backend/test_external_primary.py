"""A Garage61 comparison must describe one observed lap, even with supporting laps."""
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from test_corner_comparison import api, telemetry


class PrimaryReferenceTests(unittest.TestCase):
    def setUp(self):
        source = telemetry(5000, 5001)
        self.target = source.laps[0]
        self.analyzer = api.TelemetryAnalyzer(source)
        axis = source.channels['LapDistPct']
        self.references = {}
        # Different braking strategies, gears and pace. The first lap is neither
        # the fastest nor the median; its missing steering must stay missing.
        for index, (name, seconds) in enumerate([('primary', 96.), ('fast', 90.), ('middle', 93.)]):
            channels = {
                'Speed': 5000 / seconds - 8 * np.exp(-((axis - .3) / .06) ** 2),
                'Brake': np.where((axis > .18 + index * .02) & (axis < .3), .8 - .2 * index, 0.),
                'Throttle': np.where((axis > .18) & (axis < .36 - index * .02), 0., 1.),
                'Gear': np.full(len(axis), 3 + index),
                'SteeringWheelAngle': np.full(len(axis), .1 + .1 * index),
                'BrakeABSactive': ((axis > .22 + index * .02) & (axis < .27)).astype(float),
                'Lat': 50 + .01 * np.sin(axis * 2 * np.pi) + index * .0001,
                'Lon': 6 + .01 * np.cos(axis * 2 * np.pi),
            }
            if index == 0:
                channels['SteeringWheelAngle'][1500:1700] = np.nan
            meta = {'lap_time_s': seconds, 'driver_name': name, 'driver_slug': name,
                    'condition_similarity': {'score': .9, 'coverage': 1., 'setup_compatible': True},
                    'reference_quality_score': 1., 'reference_recency_score': 1.}
            path = Path(name + '.csv')
            self.references[path.name] = api.ExternalReferenceLap(path, meta, axis, axis * seconds, channels)
        stack = self.enterContext(ExitStack())
        stack.enter_context(patch.object(api, 'resolve_external_reference_file', side_effect=lambda name: self.references[name].path if name in self.references else None))
        stack.enter_context(patch.object(api.external_reference_repository, 'load', side_effect=lambda path: self.references[path.name]))
        stack.enter_context(patch.object(api, 'external_reference_compatible', return_value=True))
        stack.enter_context(patch.object(api, 'reference_condition_similarity', side_effect=lambda meta, _: meta['condition_similarity']))
        points = self.analyzer.grid_points - 1
        stack.enter_context(patch.object(self.analyzer, '_detect_corners', return_value=[{'entry': round(.15 * points), 'apex': round(.3 * points), 'exit': round(.42 * points)}]))

    def analyze(self, names=None):
        return self.analyzer._external_reference_analysis(self.target, names or list(self.references))

    def test_every_channel_and_line_are_the_same_actual_primary_lap(self):
        result = self.analyze()
        expected = self.references['primary.csv'].grid(self.analyzer.grid_points, 5000)
        for channel in ('time_s', 'speed_kph', 'Brake', 'Throttle', 'SteeringWheelAngle', 'Gear', 'BrakeABSactive', 'Lat', 'Lon'):
            with self.subTest(channel=channel):
                np.testing.assert_array_equal(self.analyzer._external_detail_grid[channel], expected[channel])
                np.testing.assert_array_equal(self.analyzer._external_line_grid[channel], expected[channel])
        self.assertEqual(result['aggregate']['reference_time_s'], 96.)
        self.assertEqual(result['aggregate']['gap_to_reference_s'], 4.)
        self.assertEqual(result['aggregate']['representative_reference_file'], 'primary.csv')
        self.assertEqual(result['visualizations']['reference_line_source']['chart_basis'], 'ACTUAL_LAP')
        self.assertAlmostEqual(result['visualizations']['trace'][-1]['delta_s'], 4.)

    def test_changing_support_order_does_not_change_primary_but_changing_primary_does(self):
        first = self.analyze()
        self.assertEqual(first['aggregate']['reference_time_s'], self.analyze(['primary.csv', 'middle.csv', 'fast.csv'])['aggregate']['reference_time_s'])
        changed = self.analyze(['fast.csv', 'primary.csv', 'middle.csv'])
        self.assertEqual(changed['aggregate']['reference_time_s'], 90.)
        self.assertEqual(changed['aggregate']['representative_reference_file'], 'fast.csv')

    def test_unavailable_primary_does_not_silently_promote_a_supporting_lap(self):
        result = self.analyze(['missing.csv', 'fast.csv', 'middle.csv'])
        self.assertEqual(result['status'], 'NO_VALID_REFERENCE')
        self.assertTrue(result['errors'])

    def test_support_does_not_change_primary_metrics_or_fill_its_missing_channels(self):
        single = self.analyze(['primary.csv'])
        multiple = self.analyze()
        for key in ('delta_s', 'target', 'reference', 'issue_type', 'driver_action'):
            self.assertEqual(single['corners'][0][key], multiple['corners'][0][key], key)
        support = multiple['corners'][0]['reference_consensus']
        self.assertEqual(support['supporting_driver_count'], 2)
        self.assertEqual(support['supporting_faster_driver_count'], 2)

    def test_incompatible_support_is_not_counted_and_primary_is_still_explicit(self):
        self.references['fast.csv'].metadata['condition_similarity']['setup_compatible'] = False
        self.references['middle.csv'].metadata['condition_similarity']['score'] = .4
        result = self.analyze()
        self.assertFalse(result['setup_conclusion_allowed'])
        self.assertEqual(result['corners'][0]['reference_consensus']['supporting_driver_count'], 0)
        self.assertEqual(result['aggregate']['reference_time_s'], 96.)

    def test_same_driver_laps_do_not_inflate_support(self):
        self.references['middle.csv'].metadata.update(driver_slug='fast', driver_name='fast')
        result = self.analyze()
        self.assertEqual(result['corners'][0]['reference_consensus']['supporting_driver_count'], 1)
        self.assertEqual(result['decision_reference_count'], 2)

    def test_a_manually_selected_incompatible_primary_cannot_enable_setup_conclusions(self):
        self.references['primary.csv'].metadata['condition_similarity']['setup_compatible'] = False
        result = self.analyze()
        self.assertEqual(result['aggregate']['reference_time_s'], 96.)
        self.assertFalse(result['setup_conclusion_allowed'])
        self.assertTrue(result['warnings'])

    def test_workspace_details_abs_and_summary_use_the_same_primary(self):
        from types import SimpleNamespace
        from workspace_routes import workspace_comparison
        services = SimpleNamespace(get_telemetry_analyzer=lambda: api.TelemetryAnalyzer,
            get_ibt_reader=lambda: api.IBTReader,
            comparison_detail_payload=api.comparison_detail_payload,
            resolve_external_reference_file=api.resolve_external_reference_file,
            get_external_reference_repository=lambda: api.external_reference_repository)
        result = workspace_comparison(services, self.analyzer.telemetry, {
            'reference_mode': 'AGGREGATE', 'target_lap': 1,
            'external_reference_files': list(self.references)})
        self.assertEqual(result['referenceTime'], 96.)
        self.assertEqual(result['delta'], 4.)
        self.assertAlmostEqual(result['trace'][-1]['delta_s'], result['delta'])
        self.assertEqual(result['reference_line_source']['reference_file'], 'primary.csv')
        self.assertTrue(result['controlEvents']['abs']['reference']['available'])
        self.assertTrue(result['controlEvents']['abs']['reference']['events'])

    def test_invalid_target_keeps_inputs_but_never_invents_lap_or_corner_loss(self):
        self.target = replace(self.target, valid=False, lap_time_s=None)
        with patch.object(self.analyzer, '_coaching_grid', return_value=self.analyzer._lap_grid(self.analyzer.telemetry.laps[0])):
            result = self.analyze()
        self.assertIsNone(result['aggregate']['gap_to_reference_s'])
        self.assertFalse(result['setup_conclusion_allowed'])
        self.assertTrue(all(row['delta_s'] is None for row in result['visualizations']['trace']))


class RecommendedPrimaryTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.rows = {}
        stack = self.enterContext(ExitStack())
        stack.enter_context(patch.object(api, '_external_reference_paths', side_effect=lambda: list(self.directory.glob('*.csv'))))
        stack.enter_context(patch.object(api, '_reference_catalog', return_value={}))
        stack.enter_context(patch.object(api, 'garage61_reference_metadata', side_effect=lambda path, _: self.rows[path.name]))
        stack.enter_context(patch.object(api, 'external_reference_validation', return_value={'usable': True}))
        stack.enter_context(patch.object(api, 'external_reference_compatibility', return_value={'compatible': True}))
        stack.enter_context(patch.object(api, 'reference_condition_similarity', side_effect=lambda meta, _: meta['condition_similarity']))

    def add(self, name, seconds=90, similarity=.9, coverage=1., compatible=True, quality=1., driver=None):
        filename = name + '.csv'
        (self.directory / filename).write_text('synthetic', encoding='utf-8')
        self.rows[filename] = {'name': filename, 'lap_time_s': seconds, 'driver_name': driver or name,
            'reference_quality_score': quality, 'condition_similarity': {
                'score': similarity, 'coverage': coverage, 'setup_compatible': compatible}}

    def test_recommendation_prioritizes_conditions_coverage_and_quality_before_pace(self):
        self.add('near', seconds=97)
        self.add('less_similar', seconds=85, similarity=.8)
        self.add('less_known', seconds=86, coverage=.5)
        self.add('lower_quality', seconds=87, quality=.65)
        result = api.list_external_reference_metadata(telemetry())
        self.assertEqual(result['recommended_primary'], 'near.csv')
        self.assertEqual(result['recommended_selection'], ['near.csv', 'lower_quality.csv', 'less_known.csv', 'less_similar.csv'])

    def test_no_padding_with_slow_unknown_incompatible_or_duplicate_drivers(self):
        self.add('near', seconds=95, driver='same')
        self.add('duplicate', seconds=96, driver='same')
        self.add('slow', seconds=102)
        self.add('unknown', similarity=.5, coverage=0)
        self.add('bop', compatible=False)
        self.assertEqual(api.list_external_reference_metadata(telemetry())['recommended_selection'], ['near.csv'])

    def test_new_downloads_do_not_hide_a_pinned_primary_beyond_the_shortlist(self):
        for index in range(12):
            self.add(f'lap{index}', seconds=85 + index)
        default = api.list_external_reference_metadata(telemetry())
        self.assertNotIn('lap11.csv', [row['name'] for row in default['files']])
        pinned = api.list_external_reference_metadata(telemetry(), retained_files=['lap11.csv'])
        self.assertIn('lap11.csv', [row['name'] for row in pinned['files']])
        self.assertEqual(pinned['recommended_selection'], default['recommended_selection'])

    def test_other_cars_and_layouts_are_rejected_before_reading_their_csv(self):
        self.add('matching')
        self.add('other_car')
        with patch.object(api, 'external_reference_compatibility', side_effect=lambda meta, _: {
            'compatible': meta['name'] == 'matching.csv', 'method': 'IDS', 'reason': 'CAR_MISMATCH'}), \
             patch.object(api, 'external_reference_validation', return_value={'usable': True}) as validate:
            result = api.list_external_reference_metadata(telemetry())
        self.assertEqual([call.args[0].name for call in validate.call_args_list], ['matching.csv'])
        self.assertEqual(result['scanned_reference_count'], 2)
        self.assertEqual(result['total_compatible'], 1)
        self.assertEqual(result['identity_rejections'], {'CAR_MISMATCH': 1})

    def test_listing_without_a_current_recording_still_validates_the_whole_library(self):
        self.add('first')
        self.add('second')
        with patch.object(api, 'external_reference_validation', return_value={'usable': True}) as validate:
            result = api.list_external_reference_metadata()
        self.assertEqual(validate.call_count, 2)
        self.assertEqual(len(result['files']), 2)


if __name__ == '__main__':
    unittest.main()
