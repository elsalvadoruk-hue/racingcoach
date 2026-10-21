"""Recorded sample events and their file/live association, with isolated stores."""
import atexit
from dataclasses import replace
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import numpy as np

_runtime = tempfile.TemporaryDirectory(prefix='corner-events-test-')
atexit.register(_runtime.cleanup)
os.environ.setdefault('IRACING_TUNER_DB_PATH', str(Path(_runtime.name) / 'memory.sqlite3'))

from corner_events import RawLap, corner_events, crossing
from corner_measurements import CornerMeasurements
from telemetry_types import LapSegment


def waveform(hz=60):
    time = np.arange(12 * hz + 1) / hz
    brake = np.interp(time, [0, 2, 3, 4, 5, 12], [0, 0, .9, .9, 0, 0])
    throttle = np.interp(time, [0, 1, 2, 6, 8, 12], [1, 1, 0, 0, 1, 1])
    return RawLap(time, time / 12, {'Brake': brake, 'Throttle': throttle,
                  'Speed': 20 + (time - 6) ** 2, 'BrakeABSactive': np.zeros(len(time)),
                  'SteeringWheelAngle': np.full(len(time), .1),
                  'YawRate': np.full(len(time), .2)}, 1 / hz, 300.)


def analyzer_for(raw, points=500):
    from backend_api import TelemetryAnalyzer
    lap = LapSegment(1, 0, len(raw.time_s) - 1, True, True, 'VALID', 12., 0., 1., 1., 0, None)
    telemetry = SimpleNamespace(channels={**raw.channels, 'SessionTime': raw.time_s,
                                         'LapDistPct': raw.pct}, tick_rate=round(1 / raw.tick_s),
                                metadata={'track_length_m': raw.track_length_m}, laps=[lap])
    analyzer = TelemetryAnalyzer(telemetry, grid_points=points)
    return analyzer, lap


class CornerEventTests(unittest.TestCase):
    def test_analytic_crossings_at_sixty_thirty_and_ten_hertz(self):
        answers = {'brake_start': 2 + .08 / .9, 'brake_release': 5 - .05 / .9,
                   'throttle_first': 6.4, 'full_throttle': 7.9, 'min_speed': 6.}
        for hz in (60, 30, 10):
            with self.subTest(hz=hz):
                raw = waveform(hz)
                events = corner_events(raw, .25, .5, 10 / 12)
                for key, time in answers.items():
                    event = events[key]
                    self.assertIsNotNone(event, key)
                    error_m = abs(event['pct'] - time / 12) * raw.track_length_m
                    self.assertLessEqual(error_m, event['uncertainty_m'] + 1e-8)
                    self.assertAlmostEqual(event['time_s'], time)
                    self.assertEqual(event['source'], 'NATIVE')

    def test_file_results_use_raw_events_at_different_grid_resolutions(self):
        raw = waveform()
        events = corner_events(raw, .25, .5, 10 / 12)
        outputs = []
        for points in (601, 1201, 3001):
            analyzer, lap = analyzer_for(raw, points)
            grid = analyzer._lap_grid(lap)
            indices = [int(np.argmin(abs(grid['distance_pct'] - pct))) for pct in (.25, .5, 10 / 12)]
            metrics = analyzer._corner_metrics(grid, *indices)
            self.assertEqual(metrics['event_source'], 'NATIVE')
            self.assertIs(analyzer._raw_lap_for(grid), analyzer._raw_lap_for(grid))
            outputs.append([metrics[k + '_pct'] for k in ('brake_start', 'brake_release',
                                                         'throttle_first', 'full_throttle', 'min_speed')])
        np.testing.assert_allclose(outputs, [outputs[0]] * 3, atol=0, rtol=0)
        self.assertEqual(outputs[0][0], events['brake_start']['pct'])

    def test_short_brake_tap_is_not_the_start_of_braking(self):
        raw = waveform()
        raw.channels['Brake'][(raw.time_s >= .5) & (raw.time_s < .6)] = .8
        events = corner_events(raw, .25, .5, 10 / 12)
        self.assertAlmostEqual(events['brake_start']['time_s'], 2 + .08 / .9)

    def test_full_throttle_requires_sustained_ninety_percent(self):
        raw = waveform()
        raw.channels['Throttle'][(raw.time_s >= 6.1) & (raw.time_s < 6.2)] = 1.
        self.assertAlmostEqual(corner_events(raw, .25, .5, 10 / 12)['full_throttle']['time_s'], 7.9)

    def test_gradual_relift_counts_once_and_needs_rearming(self):
        raw = waveform(10)
        raw.channels['Throttle'] = np.interp(raw.time_s,
            [0, 6, 6.2, 7, 7.4, 8, 8.2, 9, 9.4, 12], [0, 0, .8, .8, .2, .2, .8, .8, .2, .2])
        events = corner_events(raw, .25, .5, 10 / 12)
        self.assertEqual(events['relifts'], 2)
        old = np.count_nonzero((raw.channels['Throttle'][:-1] > .5) &
                               (raw.channels['Throttle'][1:] < .25))
        self.assertEqual(old, 0)

    def test_gap_does_not_create_a_crossing_or_poison_later_throttle(self):
        raw = waveform()
        keep = ~((raw.time_s > 2.05) & (raw.time_s < 2.35))
        raw = replace(raw, time_s=raw.time_s[keep], pct=raw.pct[keep],
                      channels={k: v[keep] for k, v in raw.channels.items()})
        self.assertIsNone(crossing(raw, 'Brake', .08, 0, .5, True, True))
        events = corner_events(raw, .25, .5, 10 / 12)
        self.assertIsNone(events['brake_start'])
        self.assertIsNotNone(events['full_throttle'])

    def test_time_gap_inside_braking_invalidates_its_window(self):
        raw = waveform()
        raw = replace(raw, time_s=raw.time_s + np.where(raw.time_s >= 4., .3, 0))
        events = corner_events(raw, .25, .5, 10 / 12)
        for key in ('brake_start', 'brake_release', 'abs_time_share', 'coast_time_s'):
            self.assertIsNone(events[key], key)
        self.assertIsNotNone(events['throttle_first'])

    def test_missing_channel_and_backward_or_twenty_five_meter_jump(self):
        raw = waveform()
        channels = dict(raw.channels)
        del channels['Brake']
        result = corner_events(replace(raw, channels=channels), .25, .5, 10 / 12)
        self.assertIsNone(result['brake_start'])
        self.assertIsNotNone(result['full_throttle'])
        for pct in (np.array([.1, .09, .11]), np.array([.1, .2, .21])):
            raw = RawLap(np.array([0., .1, .2]), pct, {'Brake': np.array([0., .5, .5])}, .1, 1000.)
            self.assertIsNone(crossing(raw, 'Brake', .08, .08, .22, True, True))

    def test_boundary_hold_is_clipped_and_has_full_interval_uncertainty(self):
        raw = waveform(10)
        event = crossing(raw, 'Brake', .08, .3, .33, True, True)
        self.assertEqual(event['source'], 'CLIPPED')
        self.assertAlmostEqual(event['uncertainty_m'], 2.5)

    def test_abs_time_weight_excludes_missing_states_and_requires_point_three_seconds(self):
        raw = waveform(10)
        # Entire entry/apex braking window: constant brake, high speed then low.
        raw.channels['Brake'][:] = .8
        raw.channels['BrakeABSactive'][:] = np.where(raw.time_s < 4., 1., 0.)
        raw.channels['BrakeABSactive'][(raw.time_s >= 4.) & (raw.time_s < 5.)] = np.nan
        events = corner_events(raw, .25, .5, 10 / 12)
        self.assertAlmostEqual(events['abs_time_share'], 50.)
        self.assertAlmostEqual(events['abs_observed_share'], 2 / 3)
        # Unequal distance steps make a distance-weighted ratio different.
        speed_pct = np.cumsum(np.where(raw.time_s < 4., .002, .0005))
        speed_pct /= speed_pct[-1]
        varied = replace(raw, pct=speed_pct)
        low, apex = speed_pct[30], speed_pct[60]
        events = corner_events(varied, low, apex, speed_pct[100])
        self.assertAlmostEqual(events['abs_time_share'], 50.)
        self.assertNotAlmostEqual(100 * (speed_pct[40] - low) / (apex - low), 50.)
        raw.channels['BrakeABSactive'][:] = np.nan
        raw.channels['BrakeABSactive'][30:32] = 0.
        events = corner_events(raw, .25, .5, 10 / 12)
        self.assertIsNone(events['abs_time_share'])
        self.assertAlmostEqual(events['abs_observed_share'], .2 / 3)

    def test_live_grid_keeps_existing_calculation_and_grid_source(self):
        measurement = CornerMeasurements()
        measurement.track_length_m = 300.
        raw = waveform()
        grid = {**raw.channels, 'distance_pct': raw.pct, 'time_s': raw.time_s,
                'speed_kph': raw.channels['Speed'] * 3.6}
        result = measurement._compute_corner_metrics(grid, 180, 360, 600)
        expected = np.flatnonzero(raw.channels['Brake'][:361] > .08)[0]
        self.assertEqual(result['brake_start_pct'], round(raw.pct[expected], 5))
        self.assertEqual(result['event_source'], 'GRID')
        analyzer, lap = analyzer_for(raw)
        copied = dict(analyzer._lap_grid(lap))
        self.assertIsNone(analyzer._raw_lap_for(copied))

    def test_lmu_circuit_uses_resampled_event_source(self):
        from test_lmu_ibt import duckdb, make_circuit
        if duckdb is None:
            self.skipTest('Optional LMU dependency unavailable')
        from lmu_ibt import LmuIbtReader
        from backend_api import TelemetryAnalyzer
        with tempfile.TemporaryDirectory(dir=_runtime.name) as folder:
            path = Path(folder) / 'circuit.duckdb'
            make_circuit(path)
            analyzer = TelemetryAnalyzer(LmuIbtReader(path).parse())
            grid = analyzer._lap_grid(analyzer.valid_laps[0])
            self.assertEqual(analyzer._corner_metrics(grid, 200, 300, 450)['event_source'], 'RESAMPLED')


if __name__ == '__main__':
    unittest.main()
