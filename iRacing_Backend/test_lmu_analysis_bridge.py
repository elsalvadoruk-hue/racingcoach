from bisect import bisect_left
from dataclasses import replace
import json
import unittest

import numpy as np

from analysis_bridge import braking_measurement, corner_measurement, measurement_grid
from corner_measurements import CornerMeasurements
from telemetry_core import AnalysisLap, ChannelTrace


def sample_lap():
    channels = {}
    for key, unit, hz, fn in (
        ('distance_m', 'm', 10, lambda t: 80*t-2*t*t),
        ('speed_mps', 'm/s', 100, lambda t: 80-4*t),
        ('brake_ratio', 'ratio', 50, lambda t: np.full_like(t, .6)),
        ('throttle_ratio', 'ratio', 50, lambda t: np.zeros_like(t)),
        ('steering_ratio', 'ratio', 100, lambda t: np.full_like(t, .25)),
    ):
        t = np.arange(10*hz+1)/hz
        channels[key] = ChannelTrace(key, unit, hz, 100+t, fn(t), np.ones(len(t), bool))
    support = {key: {'status': 'AVAILABLE', 'input_stage': 'UNFILTERED'} for key in channels}
    return AnalysisLap('SYNTHETIC', 1, 100, 110, 10, None, 'VALIDITY_UNKNOWN', {}, channels, support)


ZONE = {'status': 'COMPLETE', 'start_elapsed_s': 2.0, 'end_elapsed_s': 6.0}


class AnalysisBridgeTests(unittest.TestCase):
    def test_existing_kernel_matches_analytic_deceleration_and_independent_speed_readings(self):
        lap = sample_lap()
        result = braking_measurement(lap, ZONE)
        json.dumps(result, allow_nan=False)
        self.assertEqual(result['status'], 'AVAILABLE')
        values = result['metrics']
        self.assertEqual(values['braking_distance_m'], 256)
        self.assertEqual(values['braking_time_s'], 4)
        self.assertEqual(values['brake_entry_speed_kph'], 259.2)
        self.assertEqual(values['brake_end_speed_kph'], 201.6)
        self.assertEqual(values['avg_decel_g'], round(4/9.80665, 2))
        self.assertIsNone(values['peak_decel_g'])
        self.assertEqual(result['peak_decel_status'], 'ACCELERATION_NOT_CONNECTED')
        self.assertLessEqual(result['distance_resolution_m'], 2)
        # Scalar inverse of raw 10 Hz distance knots; independent of adapter arrays.
        raw_t = lap.channels['distance_m'].time_s.tolist()
        raw_d = lap.channels['distance_m'].values.tolist()
        observed = []
        for d in np.linspace(152, 408, 129):
            upper = bisect_left(raw_d, d)
            if raw_d[upper] == d:
                stamp = raw_t[upper]
            else:
                lower = upper-1
                stamp = raw_t[lower]+(d-raw_d[lower])/(raw_d[upper]-raw_d[lower])*(raw_t[upper]-raw_t[lower])
            observed.append(((80-4*(stamp-100))*3.6, stamp-102))
        expected = [[mark, round(next(t for v, t in observed if v <= mark), 3)] for mark in range(255, 204, -5)]
        self.assertEqual(values['brake_speed_time'], expected)

    def test_ratio_is_never_passed_as_steering_angle_and_native_clocks_are_preserved(self):
        lap = sample_lap()
        grid = measurement_grid(lap, 102, 106)
        self.assertEqual(grid.support['speed_kph']['frequency_hz'], 100)
        self.assertEqual(grid.support['Brake']['frequency_hz'], 50)
        self.assertEqual(grid.support['Brake']['input_stage'], 'UNFILTERED')
        self.assertEqual(grid.support['SteeringWheelAngle']['status'], 'MISSING')
        self.assertTrue(np.isnan(grid.channels['SteeringWheelAngle']).all())
        self.assertFalse(grid.channels['_channel_support']['SteeringWheelAngle'].any())
        self.assertIsNone(lap.valid)
        np.testing.assert_allclose(lap.channels['steering_ratio'].values, .25)
        shifted = replace(lap, start_time_s=2100, end_time_s=2110,
                          channels={k: replace(v, time_s=v.time_s+2000) for k, v in lap.channels.items()})
        self.assertEqual(braking_measurement(lap, ZONE), braking_measurement(shifted, ZONE))

    def test_native_holes_between_grid_knots_are_rejected_but_outside_holes_do_not_block(self):
        lap = sample_lap()
        trace = lap.channels['speed_mps']
        for mode in ('nan', 'gap', 'mask'):
            values, times, mask = trace.values.copy(), trace.time_s.copy(), trace.valid_mask.copy()
            if mode == 'nan':
                values[203] = np.nan
            elif mode == 'gap':
                values, times, mask = (np.delete(v, 203) for v in (values, times, mask))
            else:
                mask[203] = False
            broken = replace(lap, channels=lap.channels | {'speed_mps': replace(trace, values=values, time_s=times, valid_mask=mask)})
            result = braking_measurement(broken, ZONE)
            self.assertEqual(result['status'], 'UNAVAILABLE', mode)
            self.assertTrue(all(v is None for v in result['metrics'].values()), mode)
            self.assertEqual(result['channel_support']['speed_kph']['status'], 'PARTIAL')
            self.assertEqual(braking_measurement(broken, ZONE | {'start_elapsed_s': 3})['status'], 'AVAILABLE')

    def test_missing_and_unknown_units_and_partial_zones_remain_unavailable(self):
        lap = sample_lap()
        for key in ('speed_mps', 'brake_ratio'):
            channels = dict(lap.channels)
            del channels[key]
            self.assertEqual(braking_measurement(replace(lap, channels=channels), ZONE)['status'], 'UNAVAILABLE')
            channels[key] = replace(lap.channels[key], unit='unknown')
            self.assertEqual(braking_measurement(replace(lap, channels=channels), ZONE)['status'], 'UNAVAILABLE')
        partial = braking_measurement(lap, ZONE | {'status': 'PARTIAL'})
        self.assertEqual(partial['status'], 'UNAVAILABLE')
        self.assertTrue(all(v is None for v in partial['metrics'].values()))
        self.assertEqual(partial['channel_support'], {})

    def test_boundaries_distance_reversal_limits_and_extreme_speed_are_rejected(self):
        lap = sample_lap()
        for start, end, spacing in ((99, 106, 2), (102, 111, 2), (106, 102, 2), (102, 102, 2),
                                    (102, 106, 0), (102, 106, float('nan')), (102, 106, True), (102, 106, .00001)):
            with self.subTest(start=start, end=end, spacing=spacing), self.assertRaises(ValueError):
                measurement_grid(lap, start, end, resolution_m=spacing)
        axis = lap.channels['distance_m']
        values = axis.values.copy(); values[40] = values[39]
        broken = replace(lap, channels=lap.channels | {'distance_m': replace(axis, values=values)})
        self.assertEqual(braking_measurement(broken, ZONE)['status'], 'UNAVAILABLE')
        self.assertEqual(braking_measurement(lap, ZONE | {'start_elapsed_s': 0, 'end_elapsed_s': 11})['status'], 'UNAVAILABLE')
        speed = lap.channels['speed_mps']
        extreme = replace(lap, channels=lap.channels | {'speed_mps': replace(speed, values=np.full_like(speed.values, 10000))})
        self.assertEqual(braking_measurement(extreme, ZONE)['status'], 'UNAVAILABLE')


class CornerBridgeTests(unittest.TestCase):
    def lap(self):
        lap = sample_lap()
        brake = lap.channels['brake_ratio']
        t = brake.time_s-100
        throttle = lap.channels['throttle_ratio']
        channels = lap.channels | {
            'brake_ratio': replace(brake, values=np.where((t >= 3) & (t <= 5), .6, 0)),
            'throttle_ratio': replace(throttle, values=np.where(throttle.time_s >= 107, 1., 0)),
        }
        return replace(lap, channels=channels)

    def test_exact_native_boundary_time_and_kernel_numerical_semantics(self):
        lap = self.lap()
        start, middle, end = 151.3, 327.7, 506.1
        result = corner_measurement(lap, start, middle, end)
        json.dumps(result, allow_nan=False)
        axis = lap.channels['distance_m']
        stamps = np.interp([start, end], axis.values, axis.time_s)
        self.assertEqual(result['metrics']['duration_s'], stamps[1]-stamps[0])
        grid = measurement_grid(lap, *stamps, fixed_distance_bounds=(start, end))
        center = int(np.argmin(abs(grid.distance_m-middle)))
        engine = CornerMeasurements(); engine.track_length_m = end-start
        raw = engine._compute_corner_metrics(grid.channels, 0, center, len(grid.distance_m)-1)
        self.assertEqual(result['metrics']['min_speed_kmh'], raw['min_speed_kph'])
        self.assertEqual(result['metrics']['exit_speed_kmh'], raw['exit_speed_kph'])
        self.assertEqual(result['metrics']['coast_time_s'], raw['coast_time_s'])
        self.assertEqual(result['metrics']['avg_decel_g'], raw['avg_decel_g'])
        self.assertEqual(result['metrics']['braking_distance_m'], raw['braking_distance_m'])
        self.assertAlmostEqual(result['metrics']['brake_start_m'], start+raw['brake_start_pct']*(end-start), places=2)
        self.assertLessEqual(result['distance_resolution_m'], 2)
        self.assertEqual(result['position_method'], 'APPROXIMATE_DISTANCE_GRID_THRESHOLD_OBSERVATION')
        self.assertNotIn('avg_abs_steer_deg', result['metrics'])
        self.assertEqual(result['channel_support']['SteeringWheelAngle']['status'], 'MISSING')
        self.assertIsNone(lap.valid)

    def test_missing_channel_and_wrong_units_do_not_become_zero(self):
        lap = self.lap()
        for key, unavailable in (
            ('speed_mps', ('min_speed_kmh', 'exit_speed_kmh', 'coast_time_s', 'avg_decel_g', 'braking_distance_m')),
            ('brake_ratio', ('brake_start_m', 'brake_release_m', 'peak_brake', 'coast_time_s', 'avg_decel_g', 'braking_distance_m')),
            ('throttle_ratio', ('throttle_first_m', 'full_throttle_m', 'coast_time_s')),
        ):
            for mode in ('missing', 'units'):
                channels = dict(lap.channels)
                if mode == 'missing': del channels[key]
                else: channels[key] = replace(channels[key], unit='unknown')
                result = corner_measurement(replace(lap, channels=channels), 152, 336, 512)
                self.assertEqual(result['status'], 'PARTIAL', (key, mode))
                self.assertEqual(result['metric_support']['duration_s']['status'], 'AVAILABLE')
                for metric in unavailable:
                    self.assertIsNone(result['metrics'][metric], (key, mode, metric))
                    self.assertEqual(result['metric_support'][metric]['status'], 'UNAVAILABLE')
                json.dumps(result, allow_nan=False)

    def test_fixed_landmarks_do_not_shift_when_inverse_time_roundtrip_straddles_grid_count(self):
        lap = self.lap()
        throttle = lap.channels['throttle_ratio']
        lap = replace(lap, channels=lap.channels | {
            'throttle_ratio': replace(throttle, values=np.ones_like(throttle.values))})
        shifted = replace(lap, start_time_s=lap.start_time_s+1234, end_time_s=lap.end_time_s+1234,
                          channels={k: replace(v, time_s=v.time_s+1234) for k, v in lap.channels.items()})
        start, middle, end = 151.3, 331.3, 511.3
        # Old time-only grids genuinely select 182 versus 181 knots here.
        grids, fixed = [], []
        for current in (lap, shifted):
            axis = current.channels['distance_m']
            times = np.interp([start, end], axis.values, axis.time_s)
            grids.append(measurement_grid(current, *times))
            fixed.append(measurement_grid(current, *times, fixed_distance_bounds=(start, end)))
        self.assertNotEqual(len(grids[0].distance_m), len(grids[1].distance_m))
        np.testing.assert_array_equal(fixed[0].distance_m, fixed[1].distance_m)
        self.assertEqual(len(fixed[0].distance_m), 181)
        results = [corner_measurement(current, start, middle, end) for current in (lap, shifted)]
        for metric in ('throttle_first_m', 'full_throttle_m'):
            self.assertEqual(results[0]['metrics'][metric], middle)
            self.assertEqual(results[0]['metrics'][metric], results[1]['metrics'][metric])
            self.assertTrue(results[0]['boundary_observations'][metric+'_active_at_center'])
        self.assertEqual(results[0]['distance_resolution_m'], results[1]['distance_resolution_m'])

    def test_native_hole_and_gap_between_grid_knots_invalidate_whole_metric(self):
        lap = self.lap()
        for key, metric in (('speed_mps', 'min_speed_kmh'), ('brake_ratio', 'peak_brake'),
                            ('throttle_ratio', 'throttle_first_m')):
            trace = lap.channels[key]
            index = int(2.03*trace.frequency_hz)
            for mode in ('mask', 'gap'):
                if mode == 'mask':
                    mask = trace.valid_mask.copy(); mask[index] = False
                    broken = replace(trace, valid_mask=mask)
                else:
                    broken = replace(trace, time_s=np.delete(trace.time_s, index),
                                     values=np.delete(trace.values, index), valid_mask=np.delete(trace.valid_mask, index))
                result = corner_measurement(replace(lap, channels=lap.channels | {key: broken}), 152, 336, 512)
                self.assertIsNone(result['metrics'][metric], (key, mode))
                self.assertEqual(result['metrics']['duration_s'], 6.)

    def test_active_brake_boundaries_and_already_active_throttle_are_explicit(self):
        lap = sample_lap()
        result = corner_measurement(lap, 152, 336, 512)
        for metric in ('brake_start_m', 'brake_release_m', 'avg_decel_g', 'braking_distance_m'):
            self.assertIsNone(result['metrics'][metric])
            self.assertEqual(result['metric_support'][metric]['status'], 'UNAVAILABLE')
        self.assertTrue(result['boundary_observations']['brake_active_at_start'])
        throttle = lap.channels['throttle_ratio']
        result = corner_measurement(replace(lap, channels=lap.channels | {
            'throttle_ratio': replace(throttle, values=np.full_like(throttle.values, .5))}), 152, 336, 512)
        self.assertTrue(result['boundary_observations']['throttle_first_m_active_at_center'])
        self.assertEqual(result['metrics']['throttle_first_m'], result['geometric_center_grid_m'])
        self.assertIsNone(result['metrics']['full_throttle_m'])

    def test_invalid_geometry_and_channel_ranges_remain_explicit(self):
        lap = self.lap()
        for bounds in ((-1, 300, 400), (100, 100, 400), (100, 400, 300),
                       (100, 300, 10000), (100, float('nan'), 400), (True, 300, 400)):
            result = corner_measurement(lap, *bounds)
            self.assertEqual(result['status'], 'UNAVAILABLE', bounds)
            self.assertTrue(all(v is None for v in result['metrics'].values()))
        for key, metric, value in (('speed_mps', 'exit_speed_kmh', 10000), ('brake_ratio', 'peak_brake', 2)):
            trace = lap.channels[key]
            changed = replace(lap, channels=lap.channels | {key: replace(trace, values=np.full_like(trace.values, value))})
            result = corner_measurement(changed, 152, 336, 512)
            self.assertIsNone(result['metrics'][metric])
            json.dumps(result, allow_nan=False)


if __name__ == '__main__':
    unittest.main()
