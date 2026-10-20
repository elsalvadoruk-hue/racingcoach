from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    import duckdb
except ImportError:
    duckdb = None

from lmu_telemetry import LmuTelemetryReader
from lmu_vehicle import vehicle_report
from test_lmu_telemetry import make_comparison_file


@unittest.skipUnless(duckdb, 'LMU 전용 의존성이 없는 환경입니다.')
class VehicleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'vehicle.duckdb'
        make_comparison_file(self.path)
        self.add_channel('TyresPressure', 7, 'kPa', ['i+100', 'i+200', 'i+300', 'i+400'])
        self.add_channel('FrontRideHeight', 13, 'm', ['i/10000.'])
        self.add_channel('Wheel Speed', 11, 'm/s', ['i/10.', 'i/20.', 'i/30.', 'i/40.'])
        self.reader = LmuTelemetryReader(self.path)

    def sql(self, query, parameters=None):
        with duckdb.connect(str(self.path)) as connection:
            connection.execute(query, parameters)

    def add_channel(self, name, hz, unit, formulas, rows=None):
        with duckdb.connect(str(self.path)) as connection:
            connection.execute('INSERT INTO channelsList VALUES (?,?,?)', [name, hz, unit])
            fields = ['value'] if len(formulas) == 1 else ['value1', 'value2', 'value3', 'value4']
            expressions = ','.join(f'CAST(({formula}) AS FLOAT) AS {field}' for formula, field in zip(formulas, fields))
            connection.execute(f'CREATE TABLE "{name}" AS SELECT {expressions} FROM range({int(30*hz) if rows is None else rows}) r(i)')

    @staticmethod
    def native_indices(hz):
        return [i for i in range(30*hz) if 110 <= 100.0275+i/hz < 120]

    def test_vector_native_clock_stats_peak_selection_and_source_unchanged(self):
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        report = vehicle_report(self.reader, 1, points=3)
        self.assertEqual(report['status'], 'AVAILABLE')
        self.assertEqual(report['channel']['wheel_order'], 'UNVERIFIED')
        self.assertEqual(report['reference_status'], 'VALIDITY_UNVERIFIED')
        self.assertEqual(report['channel']['source_unit'], 'kPa')
        self.assertEqual(report['channel']['unit'], 'kPa')
        indices = self.native_indices(7)
        for j, component in enumerate(report['channel']['components'], 1):
            self.assertEqual(component['id'], f'value{j}')
            self.assertEqual(component['sample_count'], len(indices))
            self.assertEqual(component['mean'], sum(i+j*100 for i in indices)/len(indices))
            self.assertEqual(component['minimum'], min(indices)+j*100)
            self.assertEqual(component['maximum'], max(indices)+j*100)
            self.assertLessEqual(len(component['points']), 3)
            for point in component['points']:
                index = point['value']-j*100
                self.assertAlmostEqual(point['elapsed_s'], 100.0275+index/7-110)
                self.assertGreaterEqual(point['elapsed_s'], 0)
                self.assertLess(point['elapsed_s'], 10)
        json.dumps(report, allow_nan=False)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)
        self.assertFalse(Path(str(self.path)+'.wal').exists())

    def test_scalar_length_and_vector_speed_units_preserve_each_native_frequency(self):
        height = vehicle_report(self.reader, 1, 'FrontRideHeight', 501)
        self.assertEqual(height['channel']['wheel_order'], 'NOT_APPLICABLE')
        self.assertEqual(height['channel']['unit'], 'mm')
        self.assertEqual(height['channel']['frequency_hz'], 13)
        component = height['channel']['components'][0]
        self.assertEqual(component['id'], 'value')
        self.assertAlmostEqual(component['mean'], sum(self.native_indices(13))/len(self.native_indices(13))/10, places=5)
        speed = vehicle_report(self.reader, 1, 'Wheel Speed', 501)
        self.assertEqual(speed['channel']['unit'], 'km/h')
        self.assertEqual(speed['channel']['frequency_hz'], 11)
        self.assertAlmostEqual(speed['channel']['components'][0]['minimum'], min(self.native_indices(11))*.36, places=5)

    def test_missing_values_create_distinct_segments_and_stats_ignore_invalid_only(self):
        self.sql('UPDATE TyresPressure SET value1=CASE WHEN rowid BETWEEN 91 AND 95 THEN NULL WHEN rowid=100 THEN CAST(\'NaN\' AS FLOAT) WHEN rowid=105 THEN CAST(\'Infinity\' AS FLOAT) ELSE value1 END, value2=NULL')
        report = vehicle_report(self.reader, 1, points=501)
        self.assertEqual(report['status'], 'PARTIAL')
        first, second = report['channel']['components'][:2]
        indices = self.native_indices(7)
        expected = [i+100 for i in indices if i not in set(range(91, 96))|{100,105}]
        self.assertEqual(first['invalid_samples'], 7)
        self.assertEqual(first['valid_samples'], len(expected))
        self.assertAlmostEqual(first['mean'], sum(expected)/len(expected))
        segments = {point['segment'] for point in first['points']}
        self.assertEqual(len(segments), 4)
        for a, b in zip(first['points'], first['points'][1:]):
            if b['elapsed_s']-a['elapsed_s'] > 1.5/7:
                self.assertNotEqual(a['segment'], b['segment'])
        self.assertEqual(second['status'], 'UNAVAILABLE')
        self.assertEqual(second['points'], [])
        self.assertIsNone(second['minimum'])
        self.assertIsNone(second['mean'])
        json.dumps(report, allow_nan=False)

    def test_three_point_budget_prioritizes_minmax_and_stats_are_render_independent(self):
        self.sql('UPDATE TyresPressure SET value1=CASE WHEN rowid=103 THEN 999 WHEN rowid=104 THEN -99 ELSE 100 END')
        sparse = vehicle_report(self.reader, 1, points=3)
        dense = vehicle_report(self.reader, 1, points=501)
        for small, full in zip(sparse['channel']['components'], dense['channel']['components']):
            self.assertEqual({k:v for k,v in small.items() if k != 'points'}, {k:v for k,v in full.items() if k != 'points'})
            self.assertLessEqual(len(small['points']), 3)
        values = [p['value'] for p in sparse['channel']['components'][0]['points']]
        self.assertIn(999, values)
        self.assertIn(-99, values)

    def test_missing_unknown_unit_and_wrong_vector_schema_are_unavailable(self):
        missing = vehicle_report(self.reader, 1, 'Brake Thickness')
        self.assertEqual(missing['status'], 'UNAVAILABLE')
        self.assertEqual(missing['channel']['components'], [])
        self.sql("UPDATE channelsList SET unit='unknown' WHERE channelName='TyresPressure'")
        report = vehicle_report(self.reader, 1)
        self.assertEqual(report['status'], 'UNAVAILABLE')
        self.assertIn('단위', report['reason'])
        self.sql("UPDATE channelsList SET unit='kPa' WHERE channelName='TyresPressure'")
        self.sql('ALTER TABLE TyresPressure DROP COLUMN value4')
        report = vehicle_report(self.reader, 1)
        self.assertEqual(report['status'], 'UNAVAILABLE')
        self.assertIn('스키마', report['reason'])

    def test_recorded_brake_thickness_percent_remains_percent(self):
        self.add_channel('Brake Thickness', 10, '%', ['75','76','77','78'])
        report = vehicle_report(self.reader, 1, 'Brake Thickness')
        self.assertEqual(report['status'], 'AVAILABLE')
        self.assertEqual(report['channel']['unit'], '%')
        self.assertEqual([c['mean'] for c in report['channel']['components']], [75,76,77,78])

    def test_string_values_never_coerce_numeric_looking_wrong_schema(self):
        self.sql('ALTER TABLE TyresPressure ALTER COLUMN value1 TYPE VARCHAR')
        report = vehicle_report(self.reader, 1)
        self.assertEqual(report['status'], 'UNAVAILABLE')
        self.assertIn('스키마', report['reason'])

    def test_pit_untimed_unknown_and_short_channel_observations(self):
        self.assertEqual(vehicle_report(self.reader, 0)['status'], 'UNAVAILABLE')
        self.assertEqual(vehicle_report(self.reader, 999)['status'], 'UNAVAILABLE')
        self.sql('DELETE FROM "Lap Time" WHERE ts=120')
        self.assertEqual(vehicle_report(self.reader, 1)['status'], 'UNAVAILABLE')
        self.sql('INSERT INTO "Lap Time" VALUES(120,10)')
        self.sql('DELETE FROM TyresPressure WHERE rowid>=100')
        report = vehicle_report(self.reader, 1)
        self.assertEqual(report['status'], 'PARTIAL')
        self.assertGreater(report['channel']['components'][0]['sample_count'], 0)
        self.assertLess(report['channel']['components'][0]['sample_count'], 70)

    def test_selected_and_summary_native_row_limits_are_checked_before_fetch(self):
        with patch('lmu_vehicle.MAX_NATIVE_ROWS', 50):
            report = vehicle_report(self.reader, 1)
        self.assertEqual(report['status'], 'UNAVAILABLE')
        self.assertIn('한도', report['reason'])
        # Summary dependencies total ~3300 rows; selected channel 210 rows.
        with patch('lmu_vehicle.MAX_SUMMARY_ROWS', 100):
            with patch.object(self.reader, '_summary', side_effect=AssertionError('must not fetch')):
                report = vehicle_report(self.reader, 1)
        self.assertEqual(report['status'], 'UNAVAILABLE')
        self.add_channel('Brakes Temp', 100, 'C', ['0','0','0','0'], rows=5000)
        # The unrelated large vector is catalogued but never fetched.
        with patch('lmu_vehicle.MAX_NATIVE_ROWS', 4000), patch.object(self.reader, '_channel', wraps=self.reader._channel) as fetch:
            report = vehicle_report(self.reader, 1)
            self.assertEqual(report['status'], 'AVAILABLE')
            self.assertEqual([call.args[2] for call in fetch.call_args_list], ['Lap Dist','GPS Time'])
            brake = next(item for item in report['catalog'] if item['name']=='Brakes Temp')
            self.assertEqual(brake['status'], 'UNAVAILABLE')
            selected = vehicle_report(self.reader, 1, 'Brakes Temp')
            self.assertEqual(selected['status'], 'UNAVAILABLE')
            self.assertIn('한도', selected['reason'])

    def test_noncontiguous_source_rows_do_not_compress_the_native_clock(self):
        self.sql('DELETE FROM TyresPressure WHERE rowid=91')
        report = vehicle_report(self.reader, 1)
        self.assertEqual(report['status'], 'UNAVAILABLE')
        self.assertIn('원본 행 순서', report['reason'])

    def test_single_connection_and_argument_allowlist(self):
        calls = []
        original = self.reader._connection
        @contextmanager
        def counted():
            calls.append(1)
            with original() as connection:
                yield connection
        with patch.object(self.reader, '_connection', counted):
            vehicle_report(self.reader, 1)
        self.assertEqual(len(calls), 1)
        for number, name, points in ((True, 'TyresPressure', 301), (1,'TyresPressure', True),
                                     (-1,'TyresPressure', 301), (1,'TyresPressure',2),
                                     (1,'TyresPressure',502), (1,'TyresPressure";DROP TABLE metadata;--',301)):
            with self.subTest(number=number,name=name,points=points), patch.object(self.reader,'_connection',side_effect=AssertionError('no access')):
                with self.assertRaises(ValueError):
                    vehicle_report(self.reader,number,name,points)
        self.assertEqual(vehicle_report(self.reader,1,'tyrespressure')['status'],'AVAILABLE')


if __name__ == '__main__':
    unittest.main()
