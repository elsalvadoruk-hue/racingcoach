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
from handling import HandlingHistory, METHOD, analyze_handling, classify_phase, learn_bands, prepare_phase
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
                  'LatAccel': yaw * speed, 'Brake': brake, 'Throttle': throttle}, 1 / 60, 3000.)


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
    references = [lap_wave() for _ in range(3)] if references is None else references
    model = learn_bands([raw, *references])
    target = prepare_phase(raw, start, end, model)
    return classify_phase(target, [target, *[prepare_phase(ref, start, end, model) for ref in references]], model)


class HandlingTests(unittest.TestCase):
    def setUp(self):
        self.neutral = lap_wave()
        self.model = learn_bands([self.neutral])

    def test_neutral_measured_reaction_has_no_tendency(self):
        result = analyze_handling(analyzer([None] * 4), [CORNER])
        self.assertEqual(result['corners'], [])
        self.assertEqual(result['source'], 'measured')
        self.assertTrue(all(band['samples'] >= 200 for band in result['k_bands']))

    def test_rotation_seventy_percent_yaw_is_understeer(self):
        result = analyze_handling(analyzer(['understeer'] + [None] * 3), [CORNER])
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
        self.assertFalse(measure(self.neutral, .2, .21)['observed'])
        self.assertTrue(measure(self.neutral, .2, .25)['observed'])

    def test_repeated_braking_and_power_transients_are_the_personal_baseline(self):
        raw = lap_wave()
        t = raw.time_s
        raw.channels['YawRate'] *= 1.3 + .5 * np.sin(t / 2)
        result = measure(raw, .1, .5, references=[raw] * 3)
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
        result = analyze_handling(analyzer(['understeer'] + [None] * 3, lmu=True), [CORNER])
        self.assertTrue(result['approximate'])
        self.assertTrue(all(row['approximate'] for row in result['corners']))

    def test_missing_channels_do_not_classify(self):
        subject = analyzer(['understeer'] + [None] * 3)
        subject.channels.pop('YawRate')
        self.assertIsNone(analyze_handling(subject, [CORNER]))

    def test_comparison_attaches_handling_without_changing_diagnosis(self):
        from closed_loop_coach import detailed_corner_diagnosis
        subject = analyzer(['understeer'] + [None] * 3)
        grid = subject._lap_grid(subject.valid_laps[0])
        axis = grid['distance_pct']
        definition = dict(zip(('entry', 'apex', 'exit'), [int(np.argmin(abs(axis - value))) for value in (.1, .23, .5)]))
        row = subject._corner_analysis(grid, grid, [definition])[0]
        self.assertTrue(any(item['kind'] == 'understeer' for item in row['handling']))
        plain = detailed_corner_diagnosis({**row, 'handling': []}, 3000., 'LOCAL')
        self.assertEqual(plain, detailed_corner_diagnosis(row, 3000., 'LOCAL'))

    def test_analysis_history_uses_explicit_temporary_path(self):
        import backend_api as api
        subject = analyzer(['understeer'] + [None] * 3)
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


if __name__ == '__main__':
    unittest.main()
