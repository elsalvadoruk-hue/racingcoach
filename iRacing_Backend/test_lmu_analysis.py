import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from telemetry_core import ChannelTrace
from lmu_analysis import lap_report
from lmu_telemetry import LmuTelemetryReader, LmuTelemetryFormatError
from test_lmu_telemetry import duckdb, make_comparison_file


class TraceTests(unittest.TestCase):
    def test_no_extrapolation_invalid_bridge_or_gap_interpolation(self):
        trace = ChannelTrace('test', 'ratio', 1, np.array([0, 1, 2, 3, 6, 7]),
                             np.array([0, 1, np.nan, 3, 6, 7]), np.ones(6, dtype=bool))
        query = [-1, 0, .5, 1, 1.5, 2, 2.5, 3, 4, 6.5, 8]
        np.testing.assert_allclose(trace.at(query), [np.nan, 0, .5, 1, np.nan, np.nan, np.nan, 3, np.nan, 6.5, np.nan], equal_nan=True)
        self.assertEqual(float(trace.at(.5)), .5)
        self.assertFalse(trace.values.flags.writeable)

    def test_step_states_are_not_fractional_and_input_shape_is_checked(self):
        trace = ChannelTrace('gear', 'gear', 1, np.arange(3), np.array([1, 2, 4]), np.ones(3, bool), 'STEP')
        np.testing.assert_allclose(trace.at([.1, 1, 1.9, 2]), [1, 2, 2, 4])
        with self.assertRaises(ValueError):
            ChannelTrace('bad', 's', 1, np.array([1, 1]), np.ones(2), np.ones(2, bool))


def make_analysis_file(path):
    make_comparison_file(path)
    with duckdb.connect(str(path)) as con:
        con.execute('UPDATE "Ground Speed" SET value=180')
        con.execute('UPDATE "Steering Pos" SET value=25')
        con.execute('UPDATE "Brake Pos" SET value=CASE WHEN t<112 OR t>=115 THEN 0 '
                    'WHEN t<113 THEN (t-112)*100 WHEN t<114 THEN 100 ELSE (115-t)*100 END '
                    'FROM (SELECT rowid AS r,100.0275+rowid/50.0 AS t FROM "Brake Pos") WHERE rowid=r')
        con.execute('UPDATE "Throttle Pos" SET value=CASE WHEN t<116 OR t>=119 THEN 0 '
                    'WHEN t<117 THEN (t-116)*100 WHEN t<118 THEN 100 ELSE (119-t)*100 END '
                    'FROM (SELECT rowid AS r,100.0275+rowid/50.0 AS t FROM "Throttle Pos") WHERE rowid=r')
        for name, expression in [('GPS Latitude', '60+(t-110)*.00005'), ('GPS Longitude', '(t-110)*.0001')]:
            con.execute('INSERT INTO channelsList VALUES (?,10,\'deg\')', [name])
            con.execute(f'CREATE TABLE "{name}" AS SELECT {expression} AS value FROM '
                        '(SELECT 100.0275+i/10.0 AS t FROM range(300) r(i))')


@unittest.skipUnless(duckdb, 'LMU 전용 의존성을 설치하지 않은 환경입니다.')
class LmuAnalysisTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / 'analysis.duckdb'
        make_analysis_file(self.path)
        self.reader = LmuTelemetryReader(self.path)

    def sql(self, query):
        with duckdb.connect(str(self.path)) as con:
            con.execute(query)

    def test_native_axes_units_validity_and_read_only(self):
        before = hashlib.sha256(self.path.read_bytes()).digest()
        lap = self.reader.analysis_lap(1)
        self.assertIsNone(lap.valid)
        self.assertEqual(lap.channels['brake_ratio'].frequency_hz, 50)
        self.assertEqual(lap.channels['latitude_deg'].frequency_hz, 10)
        np.testing.assert_allclose(lap.channels['speed_mps'].values, 50)
        np.testing.assert_allclose(lap.channels['steering_ratio'].values, .25)
        self.assertNotIn('steering_angle_rad', lap.channels)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).digest(), before)
        self.assertFalse(self.path.with_suffix('.duckdb.wal').exists())

    def test_analytic_threshold_crossings_distance_time_and_time_mean(self):
        result = lap_report(self.reader.analysis_lap(1))
        json.dumps(result, allow_nan=False)
        self.assertEqual(result['status'], 'MEASUREMENT_ONLY')
        self.assertEqual(len(result['braking']['zones']), 1)
        zone = result['braking']['zones'][0]
        self.assertEqual(zone['status'], 'COMPLETE')
        self.assertAlmostEqual(zone['start_elapsed_s'], 2.08)
        self.assertAlmostEqual(zone['end_elapsed_s'], 4.95)
        self.assertAlmostEqual(zone['duration_s'], 2.87)
        self.assertAlmostEqual(zone['start_distance_m'], 208)
        self.assertAlmostEqual(zone['distance_m'], 287)
        self.assertEqual(zone['entry_speed_kmh'], 180)
        self.assertEqual(zone['speed_change_kmh'], 0)
        shared = zone['shared_measurement']
        self.assertEqual(shared['status'], 'AVAILABLE')
        self.assertEqual(shared['metrics']['avg_decel_g'], 0)
        self.assertEqual(shared['metrics']['braking_time_s'], 2.87)
        self.assertEqual(shared['metrics']['braking_distance_m'], 287)
        self.assertIsNone(shared['metrics']['peak_decel_g'])
        self.assertAlmostEqual(zone['time_mean_input_ratio'], (.92*1.08/2+1+.95*1.05/2)/2.87, places=4)
        throttle = result['full_throttle']['zones'][0]
        self.assertAlmostEqual(throttle['start_elapsed_s'], 6.95)
        self.assertAlmostEqual(throttle['end_elapsed_s'], 8.1)

    def test_display_resolution_cannot_change_input_measurements(self):
        lap = self.reader.analysis_lap(1)
        small, large = lap_report(lap, 3), lap_report(lap, 501)
        self.assertEqual(small['braking'], large['braking'])
        self.assertEqual(small['full_throttle'], large['full_throttle'])
        self.assertLessEqual(len(small['track_path']['points']), 3)
        self.assertEqual(large['track_path']['geographic_identity'], 'UNVERIFIED')
        self.assertEqual(large['track_path']['segment_count'], 1)

    def test_unknown_unit_and_missing_channel_do_not_make_zero_measurements(self):
        self.sql("UPDATE channelsList SET unit='unknown' WHERE channelName='Brake Pos'")
        self.sql("DELETE FROM channelsList WHERE channelName='GPS Longitude'")
        lap = self.reader.analysis_lap(1)
        self.assertEqual(lap.support['brake_ratio']['status'], 'UNSUPPORTED_UNIT')
        result = lap_report(lap)
        self.assertEqual(result['braking']['status'], 'UNAVAILABLE')
        self.assertEqual(result['track_path']['status'], 'UNAVAILABLE')
        self.assertTrue(result['full_throttle']['zones'])

    def test_invalid_input_splits_runs_and_never_becomes_a_complete_zone(self):
        self.sql('UPDATE "Brake Pos" SET value=NULL WHERE rowid=674')
        result = lap_report(self.reader.analysis_lap(1))
        self.assertEqual(result['channels']['brake_ratio']['invalid_samples'], 1)
        self.assertEqual(result['braking']['status'], 'PARTIAL')
        self.assertEqual(len(result['braking']['zones']), 2)
        self.assertTrue(all(z['status']=='PARTIAL' for z in result['braking']['zones']))
        self.assertTrue(all(z['shared_measurement']['status']=='UNAVAILABLE' for z in result['braking']['zones']))

    def test_out_of_range_ratios_are_masked_not_clipped(self):
        self.sql('UPDATE "Brake Pos" SET value=101 WHERE rowid=674')
        lap = self.reader.analysis_lap(1)
        t = 100.0275+674/50
        self.assertTrue(np.isnan(lap.channels['brake_ratio'].at([t])[0]))
        self.assertEqual(lap.support['brake_ratio']['status'], 'PARTIAL')

    def test_unfiltered_input_takes_priority_and_is_named(self):
        self.sql('CREATE TABLE "Brake Pos Unfiltered" AS SELECT 0.0 AS value FROM "Brake Pos"')
        self.sql("INSERT INTO channelsList VALUES ('Brake Pos Unfiltered',50,'%')")
        result = lap_report(self.reader.analysis_lap(1))
        self.assertEqual(result['channels']['brake_ratio']['input_stage'], 'UNFILTERED')
        self.assertEqual(result['braking']['zones'], [])

    def test_truncation_and_lap_boundary_runs_are_partial(self):
        self.sql('UPDATE "Brake Pos" SET value=100')
        self.sql('DELETE FROM "Brake Pos" WHERE rowid>=800')
        result = lap_report(self.reader.analysis_lap(1))
        self.assertEqual(result['channels']['brake_ratio']['status'], 'PARTIAL')
        self.assertEqual(result['braking']['status'], 'PARTIAL')
        self.assertEqual(result['braking']['zones'][0]['status'], 'PARTIAL')

    def test_missing_coordinates_and_position_jump_are_not_bridged(self):
        self.sql('UPDATE "GPS Longitude" SET value=NULL WHERE rowid=150')
        result = lap_report(self.reader.analysis_lap(1))
        self.assertEqual(result['track_path']['segment_count'], 2)
        self.sql('UPDATE "GPS Longitude" SET value=50 WHERE rowid=160')
        result = lap_report(self.reader.analysis_lap(1))
        self.assertGreater(result['track_path']['position_jump_count'], 0)
        self.assertGreaterEqual(result['track_path']['segment_count'], 3)
        self.assertLess(max(abs(p['x_m']) for p in result['track_path']['points']), 1000)

    def test_bad_clock_reversal_pit_and_invalid_arguments_are_rejected(self):
        for value in (True, -1, 0, 99, '1'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.reader.analysis_lap(value)
        for points in (True, 2, 502, 3.5):
            with self.subTest(points=points), self.assertRaises(ValueError):
                lap_report(self.reader.analysis_lap(1), points)
        self.sql('UPDATE "GPS Time" SET value=value+.01 WHERE rowid=800')
        with self.assertRaises(LmuTelemetryFormatError):
            self.reader.analysis_lap(1)


if __name__ == '__main__':
    unittest.main()
