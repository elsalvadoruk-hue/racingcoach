"""Unvalidated handling thresholds on known synthetic reactions; no personal stores."""
import atexit
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

_runtime = tempfile.TemporaryDirectory(prefix='handling-test-')
atexit.register(_runtime.cleanup)
os.environ.setdefault('IRACING_TUNER_DB_PATH', str(Path(_runtime.name) / 'memory.sqlite3'))

from corner_events import RawLap
from handling import HandlingHistory, METHOD, analyze_handling, classify_phase, learn_bands, prepare_phase, estimate_lag, lagged_values, steer_rate
from telemetry_types import LapSegment


def lap_wave(kind=None):
    time = np.arange(6001) / 60
    speed = 25 + time / 5
    steering = np.full(len(time), .1)
    yaw = .05 * steering * speed
    brake = np.where((time >= 10) & (time < 20), .6, 0.)
    throttle = np.select([time < 10, time < 25, time < 30, time < 40], [.3, 0., .4, 1.], default=.4)
    if kind == 'understeer':
        yaw[(time >= 21) & (time < 24)] *= .7
    elif kind == 'counter':
        steering[(time >= 28) & (time < 28.2)] *= -1
        throttle[(time >= 28) & (time < 28.2)] = 1.
    elif kind == 'lift':
        yaw[(time >= 40.05) & (time < 40.4)] *= 1.3
    return RawLap(time, time / 100, {'Speed': speed, 'SteeringWheelAngle': steering, 'YawRate': yaw,
                  'LatAccel': .05 * .1 * speed * speed, 'Brake': brake, 'Throttle': throttle}, 1 / 60, 3000.)


def analyzer(kinds, *, lmu=False, scale='MEASURED'):
    from backend_api import TelemetryAnalyzer
    raws = [lap_wave(kind) for kind in kinds]
    laps = []
    times = []
    offset = 0.
    for index, (kind, raw) in enumerate(zip(kinds, raws)):
        duration = 102. if kind else 100.
        times.append(raw.time_s * duration / 100 + offset)
        laps.append(LapSegment(index + 1, index * 6001, (index + 1) * 6001 - 1,
                               True, True, 'VALID', duration, 0., 1., 1., 0, None))
        offset += duration + 1
    telemetry = SimpleNamespace(channels={key: np.concatenate([raw.channels[key] for raw in raws]) for key in raws[0].channels},
                                tick_rate=60, metadata={'track_length_m': 3000., 'game': 'lmu' if lmu else 'iracing',
                                                       'steering_scale': scale}, laps=laps)
    telemetry.channels.update(SessionTime=np.concatenate(times), LapDistPct=np.concatenate([raw.pct for raw in raws]))
    return TelemetryAnalyzer(telemetry)


CORNER = {'id': 'C1', 'label': '자동 구간 1', 'entry_pct': .1, 'apex_pct': .23, 'exit_pct': .5}


def measure(raw, start, end, references=None):
    references = [lap_wave() for _ in range(5)] if references is None else references
    model = learn_bands([raw, *references])
    target = prepare_phase(raw, start, end, model)
    return classify_phase(target, [target, *[prepare_phase(ref, start, end, model) for ref in references]], model)


def reaction_scale(factor):
    raw = lap_wave()
    raw.channels['YawRate'] *= factor
    return raw


class HandlingTests(unittest.TestCase):
    def setUp(self):
        self.neutral = lap_wave()
        self.model = learn_bands([self.neutral])

    def test_neutral_measured_reaction_has_no_tendency(self):
        result = analyze_handling(analyzer([None] * 6), [CORNER])
        self.assertEqual(result['corners'], [])
        self.assertEqual(result['source'], 'measured')
        self.assertTrue(all(band['samples'] >= 200 for band in result['k_bands']))

    def test_rotation_seventy_percent_yaw_is_understeer(self):
        result = analyze_handling(analyzer(['understeer'] + [None] * 5), [CORNER])
        self.assertTrue(any(item['kind'] == 'understeer' and item['phase'] == 'rotation' for item in result['corners']))
        self.assertIsNone(result['corners'][0]['time_cost_s'])

    def test_two_tenths_countersteering_is_a_separate_measured_event(self):
        result = measure(lap_wave('counter'), .25, .5)
        self.assertTrue(any(item['kind'] == 'countersteer' and item['with'] == 'power' for item in result['events']))

    def test_thirteen_tenths_yaw_after_lift_is_oversteer(self):
        result = measure(lap_wave('lift'), .25, .5)
        self.assertTrue(any(item['kind'] == 'oversteer' and item['with'] == 'lift' for item in result['events']))

    def test_no_personal_baseline_does_not_hide_countersteering(self):
        result = measure(lap_wave('counter'), .25, .5, references=[])
        self.assertFalse(result['observed'])
        self.assertEqual([item['kind'] for item in result['events']], ['countersteer'])

    def test_at_least_three_other_laps_and_two_hundred_local_samples(self):
        self.assertFalse(measure(self.neutral, .2, .25, references=[lap_wave()] * 2)['observed'])
        self.assertFalse(measure(self.neutral, .2, .205)['observed'])
        self.assertTrue(measure(self.neutral, .2, .25)['observed'])

    def test_repeated_braking_and_power_transients_are_the_personal_baseline(self):
        raw = lap_wave()
        t = raw.time_s
        raw.channels['YawRate'] *= 1.3 + .5 * np.sin(t / 2)
        result = measure(raw, .1, .5, references=[raw] * 5)
        self.assertTrue(result['observed'])
        self.assertAlmostEqual(result['b_median'], 1.)
        self.assertEqual(result['events'], [])

    def test_target_cannot_supply_its_own_reference(self):
        model = learn_bands([self.neutral])
        target = prepare_phase(self.neutral, .2, .25, model)
        self.assertFalse(classify_phase(target, [target] * 4, model)['observed'])

    def test_inputs_must_be_comparable_and_partial_coverage_stays_unknown(self):
        raw = lap_wave()
        raw.channels['Throttle'][1500:1800] = 1.
        result = measure(raw, .25, .5)
        self.assertFalse(result['observed'])
        self.assertLess(result['known_fraction'], 1.)

    def test_speed_boundaries_are_stable_sample_quintiles(self):
        raw = self.neutral
        stable = (raw.channels['Brake'] < .05) & (raw.channels['Throttle'] > .05) & (raw.channels['Throttle'] < .6)
        stable[0] = False
        np.testing.assert_allclose(self.model['edges_mps'], np.percentile(raw.channels['Speed'][stable], [20, 40, 60, 80]))
        self.assertEqual(len(self.model['bands']), 5)

    def test_cost_requires_three_observed_laps_on_each_side(self):
        result = analyze_handling(analyzer(['understeer'] * 3 + [None] * 3), [CORNER])
        row = next(item for item in result['corners'] if item['kind'] == 'understeer' and item['phase'] == 'rotation')
        self.assertEqual((row['laps'], row['of_laps']), (3, 6))
        self.assertAlmostEqual(row['time_cost_s'], .8, places=3)
        result = analyze_handling(analyzer(['understeer'] * 2 + [None] * 3), [CORNER])
        self.assertTrue(all(row['time_cost_s'] is None for row in result['corners']))

    def test_lmu_requires_measured_steering_and_marks_approximation(self):
        self.assertIsNone(analyze_handling(analyzer(['understeer'], lmu=True, scale='NOMINAL'), [CORNER]))
        result = analyze_handling(analyzer(['understeer'] + [None] * 5, lmu=True), [CORNER])
        self.assertTrue(result['approximate'])
        self.assertTrue(all(row['approximate'] for row in result['corners']))

    def test_missing_channels_do_not_classify(self):
        subject = analyzer(['understeer'] + [None] * 5)
        subject.channels.pop('YawRate')
        self.assertIsNone(analyze_handling(subject, [CORNER]))

    def test_comparison_attaches_handling_without_changing_diagnosis(self):
        from closed_loop_coach import detailed_corner_diagnosis
        subject = analyzer(['understeer'] + [None] * 5)
        grid = subject._lap_grid(subject.valid_laps[0])
        axis = grid['distance_pct']
        definition = dict(zip(('entry', 'apex', 'exit'), [int(np.argmin(abs(axis - value))) for value in (.1, .23, .5)]))
        row = subject._corner_analysis(grid, grid, [definition])[0]
        self.assertTrue(any(item['kind'] == 'understeer' for item in row['handling']))
        plain = detailed_corner_diagnosis({**row, 'handling': []}, 3000., 'LOCAL')
        self.assertEqual(plain, detailed_corner_diagnosis(row, 3000., 'LOCAL'))

    def test_analysis_history_uses_explicit_temporary_path(self):
        import backend_api as api
        subject = analyzer(['understeer'] + [None] * 5)
        source = subject.telemetry
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'synthetic.ibt'
            path.write_bytes(b'synthetic')
            source.metadata.update(car_path='synthetic-car', track_platform_id=1)
            telemetry = api.ParsedTelemetry(path, ('synthetic', 1, 1), 60, 0, len(source.channels['SessionTime']),
                                            0, '', {}, {}, source.channels, source.metadata, {}, source.laps)
            subject = api.TelemetryAnalyzer(telemetry)
            axis = subject._lap_grid(source.laps[0])['distance_pct']
            definition = dict(zip(('entry', 'apex', 'exit'), [int(np.argmin(abs(axis - value))) for value in (.1, .23, .5)]))
            history_path = Path(folder) / 'handling.json'
            with patch.dict(os.environ, {'IRACING_HANDLING_HISTORY_PATH': str(history_path)}), patch.object(subject, '_detect_corners', return_value=[definition]):
                result = subject.analyze(api.AnalyzeRequest(include_ai=False, persist_memory=False))
            self.assertTrue(history_path.exists())
            self.assertTrue(result['handling']['corners'])
            self.assertFalse(result['handling']['thresholds_validated'])

    def test_short_pulse_or_gap_never_satisfies_continuity(self):
        raw = lap_wave()
        raw.channels['YawRate'][1260:1272] *= .7  # only 0.2 s, below 0.3 s
        self.assertEqual(measure(raw, .2, .25)['events'], [])
        raw.channels['YawRate'][1260:1284] *= .7
        raw.channels['YawRate'][1272:1278] = np.nan
        result = measure(raw, .2, .25)
        self.assertEqual(result['events'], [])
        self.assertFalse(result['observed'])

    def test_history_overwrites_one_file_and_requires_distinct_files(self):
        with tempfile.TemporaryDirectory() as folder:
            history = HandlingHistory(Path(folder) / 'handling.json')
            metadata = {'car_path': 'test-car', 'track_platform_id': 1, 'track_config': 'test'}
            item = {**CORNER, 'corner_id': 'C1', 'kind': 'understeer', 'phase': 'rotation',
                    'laps': 3, 'of_laps': 6, 'time_cost_s': .1}
            result = {'corners': [item]}
            self.assertEqual(history.update(metadata, ('one', 1, 1), result, now=1), [])
            self.assertEqual(history.update(metadata, ('one', 1, 1), result, now=2), [])
            repeat = history.update(metadata, ('two', 1, 1), result, now=3)
            self.assertEqual(repeat[0]['files'], 2)
            history.update(metadata, ('one', 1, 1), {'corners': []}, now=4)
            stored = json.loads(history.path.read_text(encoding='utf-8'))
            self.assertEqual(sum(len(rows) for values in stored['corners'].values() for rows in values.values()), 1)

    def test_history_keeps_only_fifty_files_and_separates_contexts(self):
        with tempfile.TemporaryDirectory() as folder:
            history = HandlingHistory(Path(folder) / 'history.json')
            metadata = {'car_path': 'test-car', 'track_platform_id': 1}
            item = {**CORNER, 'corner_id': 'C1', 'kind': 'oversteer', 'phase': 'drive',
                    'laps': 1, 'of_laps': 3, 'time_cost_s': None}
            result = {'corners': [item]}
            for i in range(51):
                history.update(metadata, (str(i), 1, 1), result, now=i)
            stored = json.loads(history.path.read_text(encoding='utf-8'))
            self.assertEqual(len(stored['files']), 50)
            self.assertEqual(sum(len(values) for values in stored['corners'].values()), 50)
            self.assertEqual(history.update({**metadata, 'car_path': 'another'}, ('other', 1, 1), result, now=52), [])

    def test_history_never_mixes_absolute_and_personal_methods(self):
        with tempfile.TemporaryDirectory() as folder:
            history = HandlingHistory(Path(folder) / 'history.json')
            metadata = {'car_path': 'test-car', 'track_platform_id': 1}
            item = {**CORNER, 'corner_id': 'C1', 'kind': 'oversteer', 'phase': 'drive',
                    'laps': 1, 'of_laps': 4, 'time_cost_s': None}
            history.update(metadata, ('old', 1), {'method': 'absolute_v0', 'corners': [item]})
            self.assertEqual(history.update(metadata, ('new', 1), {'method': METHOD, 'corners': [item]}), [])

    def test_coverage_partitions_slots_and_preserves_unknown_laps(self):
        result = analyze_handling(analyzer([None] * 2), [CORNER])
        coverage = result['coverage']
        self.assertEqual(coverage['classifiable'], 0)
        self.assertEqual(coverage['unknown_fraction'], 1.)
        self.assertEqual(coverage['applicable'] + coverage['not_applicable'], coverage['total_slots'])

    def test_unknown_reasons_distinguish_lap_sample_and_input_shortages(self):
        few = measure(self.neutral, .2, .25, references=[lap_wave()] * 2)
        self.assertIn('reference_laps_shortage', few['unknown_reasons'])
        short = measure(self.neutral, .2, .205)
        self.assertIn('reference_samples_shortage', short['unknown_reasons'])
        changed = lap_wave()
        changed.channels['Throttle'][1500:1800] = 1.
        unmatched = measure(changed, .25, .5)
        self.assertIn('inputs_not_comparable', unmatched['unknown_reasons'])
        self.assertFalse(unmatched['observed'])
        self.assertEqual(measure(self.neutral, .2, .25)['unknown_reasons'], [])

    def test_per_lap_release_observations_are_explicit_and_preserve_detection(self):
        subject = analyzer(['understeer'] + [None] * 5)
        ordinary = analyze_handling(subject, [CORNER])
        detailed = analyze_handling(subject, [CORNER], include_observations=True)
        self.assertNotIn('observations', ordinary)
        observations = detailed.pop('observations')
        self.assertEqual(ordinary, detailed)
        self.assertTrue(any(row['lap'] == 1 and row['phase'] == 'rotation' and
                            any(e['kind'] == 'understeer' for e in row['events']) for row in observations))
        missing = analyze_handling(analyzer([None] * 2), [CORNER])
        self.assertGreater(missing['unknown_reasons']['reference_laps_shortage'], 0)
        self.assertTrue(any(row['unknown_reasons'] for row in missing['phase_coverage']))

    def test_high_reference_spread_suppresses_twenty_percent_differences(self):
        references = [reaction_scale(np.exp(value)) for value in (-.25, -.12, 0., .12, .25)]
        for factor in (.8, 1.2):
            with self.subTest(factor=factor):
                result = measure(reaction_scale(factor), .2, .25, references)
                self.assertTrue(result['observed'])
                self.assertEqual(result['events'], [])
                self.assertLess(abs(result['z_median']), 2.5)

    def test_low_reference_spread_detects_sixteen_percent_differences(self):
        references = [reaction_scale(np.exp(value)) for value in (-.02, -.01, 0., .01, .02)]
        for factor, kind in ((.84, 'understeer'), (1.16, 'oversteer')):
            with self.subTest(factor=factor):
                result = measure(reaction_scale(factor), .2, .25, references)
                self.assertEqual([event['kind'] for event in result['events']], [kind])
                self.assertAlmostEqual(result['events'][0]['z_median'], np.log(factor) / (1.4826 * .01))

    def test_three_or_four_references_without_local_point_spread_are_unknown(self):
        for count in (3, 4):
            with self.subTest(count=count):
                result = measure(reaction_scale(.7), .2, .25, [lap_wave() for _ in range(count)])
                self.assertFalse(result['observed'])
                self.assertEqual(result['known_fraction'], 0.)
                self.assertEqual(result['events'], [])
                self.assertIn('noise_unknown', result['unknown_reasons'])
                self.assertNotIn('reference_laps_shortage', result['unknown_reasons'])

    def test_three_or_four_references_borrow_same_phase_point_spread(self):
        for count in (3, 4):
            with self.subTest(count=count):
                references = [reaction_scale(np.exp(value)) for value in (-.02, -.01, 0., .01, .02)]
                for reference in references[count:]:
                    reference.channels['Throttle'][(reference.pct >= .22) & (reference.pct <= .23)] = 1.
                result = measure(reaction_scale(.84), .2, .25, references)
                self.assertTrue(result['observed'])
                self.assertEqual(result['unknown_reasons'], [])
                self.assertEqual([event['kind'] for event in result['events']], ['understeer'])

    def test_noise_fallback_does_not_borrow_from_boundary_padding(self):
        references = [lap_wave() for _ in range(5)]
        references[-1].channels['Throttle'][references[-1].pct >= .2] = 1.
        result = measure(reaction_scale(.7), .2, .25, references)
        self.assertFalse(result['observed'])
        self.assertEqual(result['known_fraction'], 0.)
        self.assertIn('noise_unknown', result['unknown_reasons'])
        self.assertEqual(result['events'], [])

    def test_zero_spread_uses_minimum_ratio_without_nonfinite_json(self):
        for factor, expected in ((.86, []), (1.14, []), (.84, ['understeer']), (1.16, ['oversteer'])):
            with self.subTest(factor=factor):
                with np.errstate(divide='raise', invalid='raise'):
                    result = measure(reaction_scale(factor), .2, .25)
                self.assertTrue(result['observed'])
                self.assertEqual([event['kind'] for event in result['events']], expected)
                json.dumps(result, allow_nan=False)

    def test_large_z_still_needs_minimum_ratio_difference(self):
        references = [reaction_scale(np.exp(value)) for value in (-.002, -.001, 0., .001, .002)]
        result = measure(reaction_scale(.9), .2, .25, references)
        self.assertLess(result['z_median'], -2.5)
        self.assertEqual(result['events'], [])

    def test_borrowed_zero_noise_detects_events_and_missing_noise_preserves_countersteer(self):
        raw = lap_wave('counter')
        raw.channels['YawRate'][(raw.time_s >= 21) & (raw.time_s < 21.4)] *= .7
        references = [lap_wave() for _ in range(5)]
        for reference in references[3:]:
            # Only the event has three matches; enough reference evidence elsewhere supplies noise.
            reference.channels['Throttle'][(reference.time_s >= 21.1) & (reference.time_s < 21.3)] = 1.
        # Noise is zero, so this borrowed-noise case remains detectable.
        detected = measure(raw, .2, .3, references)
        self.assertIn('understeer', [event['kind'] for event in detected['events']])
        unknown = measure(raw, .2, .3, references[:3])
        self.assertEqual([event['kind'] for event in unknown['events']], ['countersteer'])

    def test_large_spread_breaks_sustained_runs(self):
        raw = lap_wave()
        raw.channels['YawRate'][(raw.time_s >= 21) & (raw.time_s < 21.4)] *= .7
        references = [lap_wave() for _ in range(5)]
        for reference, factor in zip(references, (.55, .7, 1., 1.3, 1.6)):
            reference.channels['YawRate'][(reference.time_s >= 21.1) & (reference.time_s < 21.3)] *= factor
        result = measure(raw, .2, .25, references)
        self.assertTrue(result['observed'])
        self.assertEqual(result['events'], [])

    def test_noise_metadata_and_event_z_are_added_without_changing_existing_fields(self):
        result = analyze_handling(analyzer(['understeer'] + [None] * 5), [CORNER], include_observations=True)
        self.assertEqual(result['method'], 'personal_corner_phase_v2')
        self.assertEqual(result['noise_rules'], {'z_limit': 2.5, 'min_ratio': [.85, 1.15],
                                                'spread': 'mad', 'min_laps_for_point_spread': 5})
        self.assertTrue(result['corners'])
        self.assertTrue(all('z_median' in item for item in result['corners']))
        self.assertTrue(all('z_median' in event for row in result['observations'] for event in row['events']))
        self.assertNotIn('_z_samples', json.dumps(result))
        json.dumps(result, allow_nan=False)

    def test_summary_z_uses_all_sustained_samples_instead_of_lap_medians(self):
        subject = analyzer(['understeer'] * 3 + [None] * 5)
        raw = lap_wave()
        baseline = raw.channels['YawRate']
        yaw = subject.channels['YawRate']
        for index in (0, 1):
            values = baseline.copy()
            values[(raw.time_s >= 21) & (raw.time_s < 21.4)] *= .6
            yaw[index * 6001:(index + 1) * 6001] = values
        for index, log_factor in enumerate((-.02, -.01, 0., .01, .02), start=3):
            yaw[index * 6001:(index + 1) * 6001] *= np.exp(log_factor)
        samples, medians = [], []
        def capture(phase, references, model):
            measured = classify_phase(phase, references, model)
            for event in measured['events']:
                if event['kind'] == 'understeer':
                    samples.extend(event['_z_samples'])
                    medians.append(event['z_median'])
            return measured
        with patch('handling.classify_phase', side_effect=capture):
            result = analyze_handling(subject, [CORNER], include_observations=True)
        item = next(row for row in result['corners'] if row['kind'] == 'understeer' and row['phase'] == 'rotation')
        self.assertAlmostEqual(item['z_median'], np.median(samples))
        self.assertNotAlmostEqual(item['z_median'], np.median(medians))
        self.assertNotIn('_z_samples', json.dumps(result))

    def test_history_never_mixes_v1_and_v2(self):
        with tempfile.TemporaryDirectory() as folder:
            history = HandlingHistory(Path(folder) / 'history.json')
            metadata = {'car_path': 'test-car', 'track_platform_id': 1}
            item = {**CORNER, 'corner_id': 'C1', 'kind': 'understeer', 'phase': 'rotation',
                    'laps': 1, 'of_laps': 6, 'time_cost_s': None}
            history.update(metadata, ('old', 1), {'method': 'personal_corner_phase_v1', 'corners': [item]})
            self.assertEqual(history.update(metadata, ('new', 1), {'method': METHOD, 'corners': [item]}), [])
            repeats = history.update(metadata, ('another', 1), {'method': METHOD, 'corners': [item]})
            self.assertEqual(repeats[0]['files'], 2)

    def test_same_lateral_acceleration_and_speed_with_more_steering_is_detected(self):
        for factor in (1.3, 1.7):
            with self.subTest(steering_factor=factor):
                target = lap_wave()
                target.channels['SteeringWheelAngle'] *= factor
                result = measure(target, .2, .25)
                self.assertTrue(result['observed'])
                self.assertEqual([event['kind'] for event in result['events']], ['understeer'])
                self.assertAlmostEqual(result['b_median'], 1 / factor)

    def test_incompatible_lateral_acceleration_is_excluded_and_explained(self):
        for factor in (1.5, -1.):
            with self.subTest(lateral_factor=factor):
                references = [lap_wave() for _ in range(5)]
                for reference in references:
                    reference.channels['LatAccel'] *= factor
                result = measure(reaction_scale(.7), .2, .25, references)
                self.assertFalse(result['observed'])
                self.assertIn('lateral_not_comparable', result['unknown_reasons'])
                self.assertIn('inputs_not_comparable', result['unknown_reasons'])
                self.assertEqual(result['events'], [])

    def test_relative_speed_rejects_five_percent_even_within_same_quintile(self):
        for speed, observed in ((20.79, True), (21., False)):
            with self.subTest(reference_speed=speed):
                target = lap_wave()
                target.channels['Speed'][:] = 20.
                references = [lap_wave() for _ in range(5)]
                for reference in references:
                    reference.channels['Speed'][:] = speed
                model = {'edges_mps': np.array([5., 15., 30., 40.]), 'bands': []}
                phase = prepare_phase(target, .2, .25, model)
                refs = [prepare_phase(ref, .2, .25, model) for ref in references]
                result = classify_phase(phase, refs, model)
                self.assertEqual(result['observed'], observed)
                if not observed:
                    self.assertIn('inputs_not_comparable', result['unknown_reasons'])
                    self.assertNotIn('lateral_not_comparable', result['unknown_reasons'])

    def test_lateral_acceleration_uses_larger_absolute_or_relative_tolerance(self):
        for target_ay, difference, expected in ((4., .79, True), (4., .81, False),
                                               (12., 1.19, True), (12., 1.21, False)):
            with self.subTest(target_ay=target_ay, difference=difference):
                target = lap_wave()
                target.channels['LatAccel'][:] = target_ay
                references = [lap_wave() for _ in range(5)]
                for reference in references:
                    reference.channels['LatAccel'][:] = target_ay + difference
                result = measure(target, .2, .25, references)
                self.assertEqual(result['observed'], expected)
                self.assertEqual('lateral_not_comparable' in result['unknown_reasons'], not expected)

    def test_reference_rules_describe_speed_and_lateral_matching(self):
        result = analyze_handling(analyzer([None] * 6), [CORNER])
        rules = result['reference_rules']
        self.assertEqual(rules['max_speed_relative'], .04)
        self.assertEqual(rules['max_lat_accel_abs'], .8)
        self.assertEqual(rules['max_lat_accel_relative'], .1)
        self.assertNotIn('max_steer_ratio', rules)
        self.assertNotIn('max_speed_difference_mps', rules)






def response():
    time = np.arange(1201) / 100
    steer = .15 + .08 * np.sin(2 * np.pi * .7 * time) + .02 * np.sin(2 * np.pi * 1.3 * time)
    yaw = np.interp(time - .1, time, steer) * 25 * .05
    return RawLap(time, time / 12, {'Speed': np.full(len(time), 25.),
        'SteeringWheelAngle': steer, 'YawRate': yaw, 'LatAccel': np.full(len(time), 5.),
        'Brake': np.zeros(len(time)), 'Throttle': np.full(len(time), .3)}, .01, 3000.)


class LagTests(unittest.TestCase):
    def test_estimate_recovers_tenth_second_measured_delay(self):
        raw = response()
        lag = estimate_lag([raw], [raw.time_s > .4])
        self.assertGreaterEqual(lag, .08)
        self.assertLessEqual(lag, .12)

    def test_uncorrelated_yaw_falls_back_to_zero(self):
        raw = response()
        raw.channels['YawRate'][:] = np.random.default_rng(42).normal(size=len(raw.pct))
        self.assertEqual(estimate_lag([raw], [np.ones(len(raw.pct), dtype=bool)]), 0.)

    def test_time_distance_gap_and_duplicate_cannot_supply_past_steering(self):
        raw = response()
        for times, pct in ((np.r_[raw.time_s[:500], raw.time_s[500:] + 1], raw.pct),
                           (raw.time_s, np.r_[raw.pct[:500], raw.pct[500:] - .1]),
                           (np.r_[raw.time_s[:500], raw.time_s[499], raw.time_s[501:]], raw.pct)):
            subject = replace(raw, time_s=times, pct=pct)
            values = lagged_values(subject, subject.channels['SteeringWheelAngle'], .1)
            self.assertTrue(np.all(np.isnan(values[500:509])))
            self.assertTrue(np.isfinite(values[520]))

    def test_steer_derivative_does_not_cross_duplicate_time(self):
        raw = response()
        times = raw.time_s.copy(); times[500] = times[499]
        self.assertTrue(np.isnan(steer_rate(replace(raw, time_s=times))[500]))

    def test_alignment_removes_false_over_during_unwinding(self):
        raw = response()
        time = raw.time_s
        # A sustained rapid unwind produces a delayed-yaw ratio above the fixed floor.
        steer = .3 * np.exp(-1.8 * np.clip(time - 3., 0., .7))
        raw.channels['SteeringWheelAngle'][:] = steer
        raw.channels['YawRate'][:] = np.interp(time - .1, time, steer) * 25 * .05
        refs = [response() for _ in range(5)]
        for ref in refs:
            ref.channels['SteeringWheelAngle'][:] = .15
            ref.channels['YawRate'][:] = .15 * 25 * .05
        model = learn_bands([raw, *refs])
        before = prepare_phase(raw, .2, .4, model)
        references = [prepare_phase(ref, .2, .4, model) for ref in refs]
        self.assertIn('oversteer', [e['kind'] for e in classify_phase(before, references, model)['events']])
        model['_aligned_steer'] = {id(raw): lagged_values(raw, steer, .1)}
        after = prepare_phase(raw, .2, .4, model)
        self.assertNotIn('oversteer', [e['kind'] for e in classify_phase(after, references, model)['events']])





class DynamicsTests(unittest.TestCase):
    def test_raw_lap_missing_required_channel_stays_unknown(self):
        subject = analyzer([None] * 6)
        original = subject._raw_lap_for
        def incomplete(grid):
            raw = original(grid)
            return replace(raw, channels={key: value for key, value in raw.channels.items() if key != 'YawRate'})
        with patch.object(subject, '_raw_lap_for', side_effect=incomplete):
            measured = analyze_handling(subject, [CORNER])
        self.assertEqual(measured['corners'], [])
        self.assertEqual(measured['coverage']['classifiable'], 0)
        self.assertGreater(measured['coverage']['unknown'], 0)

    def test_small_aligned_steering_explains_unknown_without_shrinking_counter_coverage(self):
        raw = lap_wave()
        refs = [lap_wave() for _ in range(5)]
        model = learn_bands([raw, *refs])
        model['_aligned_steer'] = {id(raw): np.full(len(raw.pct), .02)}
        target = prepare_phase(raw, .2, .25, model)
        references = [prepare_phase(ref, .2, .25, model) for ref in refs]
        measured = classify_phase(target, references, model)
        self.assertFalse(measured['observed'])
        self.assertTrue(measured['counter_observed'])
        self.assertEqual(measured['known_fraction'], 0.)
        self.assertIn('steer_lag_below_minimum', measured['unknown_reasons'])
        self.assertEqual(measured['events'], [])

    def test_excluded_fast_steering_breaks_detection_runs(self):
        raw = reaction_scale(.7)
        refs = [lap_wave() for _ in range(5)]
        model = learn_bands([raw, *refs])
        target = prepare_phase(raw, .2, .205, model)
        references = [prepare_phase(ref, .2, .205, model) for ref in refs]
        # Use a longer window so local-band sample support is observed.
        target = prepare_phase(raw, .2, .25, model)
        references = [prepare_phase(ref, .2, .25, model) for ref in refs]
        self.assertIn('understeer', [event['kind'] for event in classify_phase(target, references, model)['events']])
        target['steady_steer'] = np.arange(len(target['q'])) % 10 != 0
        measured = classify_phase(target, references, model)
        self.assertTrue(measured['observed'])
        self.assertEqual(measured['events'], [])

    def test_fast_steering_exclusion_never_hides_measured_countersteer(self):
        raw = lap_wave('counter')
        model = learn_bands([raw])
        target = prepare_phase(raw, .25, .5, model)
        target['steady_steer'] = np.zeros(len(target['q']), dtype=bool)
        measured = classify_phase(target, [], model)
        self.assertEqual([event['kind'] for event in measured['events']], ['countersteer'])

    def test_file_reports_lag_and_per_phase_ninetieth_percentile(self):
        result = analyze_handling(analyzer([None] * 6), [CORNER])
        self.assertGreaterEqual(result['steer_lag_s'], 0.)
        self.assertLessEqual(result['steer_lag_s'], .3)
        self.assertEqual(set(result['fast_steer_rate_rad_s']), {'braking', 'rotation', 'drive'})
        self.assertTrue(all(value == 0. for value in result['fast_steer_rate_rad_s'].values()))



if __name__ == '__main__':
    unittest.main()
