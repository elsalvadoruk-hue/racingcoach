import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    import duckdb
except ImportError:
    duckdb = None
import numpy as np

from lmu_telemetry import (
    LmuTelemetryFormatError, LmuTelemetryReader, LmuTelemetryUnavailable,
    discover_lmu_telemetry_dirs, list_lmu_telemetry_files,
)


def make_lmu_file(path):
    """Synthetic schema: t=100..130, 2 Hz, two bounded laps and a tail."""
    con = duckdb.connect(str(path))
    con.execute('CREATE TABLE metadata (key VARCHAR, value VARCHAR)')
    con.executemany('INSERT INTO metadata VALUES (?, ?)', [
        ('TrackName', 'Test Circuit'), ('TrackLayout', 'GP'), ('CarName', 'Test GT3'),
        ('DriverName', '<script>unsafe</script>'), ('SteamID', 'PRIVATE_TEST_ID'),
        ('CarSetup', 'PRIVATE_SETUP'),
    ])
    con.execute('CREATE TABLE channelsList (channelName VARCHAR, frequency DOUBLE, unit VARCHAR)')
    con.executemany('INSERT INTO channelsList VALUES (?, ?, ?)', [
        ('Lap Dist', 2, 'm'), ('GPS Time', 2, 's'), ('Throttle Pos', 2, '%'),
    ])
    for name, values in {
        'Lap Dist': [float((i % 20) * 50) for i in range(61)],
        'GPS Time': [100 + i / 2 for i in range(61)],
        'Throttle Pos': [25.0] * 61,
    }.items():
        con.execute(f'CREATE TABLE "{name}" (value DOUBLE)')
        con.executemany(f'INSERT INTO "{name}" VALUES (?)', [(v,) for v in values])
    for name, rows in {
        'Lap': [(100, 0), (110, 1), (120, 2)],
        'Lap Time': [(110, 10), (120, 10)],
        'In Pits': [(99, 1), (103, 0)],
    }.items():
        con.execute(f'CREATE TABLE "{name}" (ts DOUBLE, value DOUBLE)')
        con.executemany(f'INSERT INTO "{name}" VALUES (?, ?)', rows)
    con.close()


def make_comparison_file(path):
    """Mixed-rate analytic signals with off-grid Lap events and a pit out-lap."""
    make_lmu_file(path)
    with duckdb.connect(str(path)) as con:
        con.execute('DELETE FROM channelsList')
        signals = [('Lap Dist', 10, 'm', None), ('GPS Time', 100, 's', None),
                   ('Ground Speed', 100, 'km/h', 1), ('GPS Speed', 10, 'm/s', 1 / 3.6),
                   ('Throttle Pos', 50, '%', .5), ('Brake Pos', 50, '%', .1),
                   ('Steering Pos', 100, '%', -.2)]
        for name, hz, unit, factor in signals:
            con.execute('INSERT INTO channelsList VALUES (?, ?, ?)', [name, hz, unit])
            con.execute(f'DROP TABLE IF EXISTS "{name}"')
            if name == 'GPS Time':
                value = 't'
            elif name == 'Lap Dist':
                value = 'CASE WHEN t<110 THEN (t-100.0275)*100 WHEN t<120 THEN (t-110)*100 ELSE (t-120)*1000/9 END'
            else:
                value = f't*{factor}'
            con.execute(f'CREATE TABLE "{name}" AS SELECT {value} AS value '
                        f'FROM (SELECT 100.0275+i/{hz}.0 AS t FROM range({30*hz}) r(i))')
        for name, rows in {
            'Lap': [(100.0275, 0), (110, 1), (120, 2), (129, 3)],
            'Lap Time': [(120, 10), (129, 9)], 'In Pits': [(100.0275, 1), (105, 0)],
        }.items():
            con.execute(f'DELETE FROM "{name}"')
            con.executemany(f'INSERT INTO "{name}" VALUES (?, ?)', rows)


@unittest.skipUnless(duckdb, 'LMU 전용 의존성을 설치하지 않은 환경입니다.')
class LmuLapComparisonTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'comparison.duckdb'
        make_comparison_file(self.path)
        self.reader = LmuTelemetryReader(self.path)

    def sql(self, query):
        with duckdb.connect(str(self.path)) as con:
            con.execute(query)

    def test_mixed_rates_match_analytic_signals_at_same_distance_without_writes(self):
        before = hashlib.sha256(self.path.read_bytes()).digest()
        c = self.reader.compare_laps(1, 2, 201)
        self.assertEqual(c.status, 'DIAGNOSTIC')
        self.assertEqual(c.delta_time_s, -1)
        self.assertEqual(c.sample_count, 201)
        self.assertEqual(c.channels['Steering Pos']['unit'], '%')
        self.assertEqual(c.channels['GPS Speed']['unit'], 'km/h')
        self.assertEqual(c.channels['Throttle Pos']['source_frequency_hz'], 50)
        self.assertGreater(c.distance_start_m, 0)
        self.assertLess(c.distance_end_m, 1000)
        for p in c.points:
            self.assertAlmostEqual(p['reference_elapsed_s'], p['distance_m'] / 100, places=7)
            self.assertAlmostEqual(p['target_elapsed_s'], p['distance_m'] * 9 / 1000, places=7)
            for prefix, start in [('reference', 110), ('target', 120)]:
                t = start + p[f'{prefix}_elapsed_s']
                for name, factor in [('Ground Speed', 1), ('GPS Speed', 1),
                                     ('Throttle Pos', .5), ('Brake Pos', .1), ('Steering Pos', -.2)]:
                    self.assertAlmostEqual(p[f'{name}_{prefix}'], t * factor, places=7)
            self.assertAlmostEqual(p['delta_time_s'], -p['distance_m']/1000, places=7)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).digest(), before)
        self.assertFalse(self.path.with_suffix('.duckdb.wal').exists())
        self.assertTrue(all(lap.valid is None for lap in self.reader.summary().laps))

    def test_reversing_reference_reverses_delta(self):
        a, b = self.reader.compare_laps(1, 2), self.reader.compare_laps(2, 1)
        self.assertEqual(a.delta_time_s, -b.delta_time_s)
        for p, q in zip(a.points, b.points):
            self.assertEqual(p['distance_m'], q['distance_m'])
            self.assertAlmostEqual(p['delta_time_s'], -q['delta_time_s'])

    def test_distance_sectors_match_analytic_times_and_distance_weighted_channels(self):
        c = self.reader.compare_laps(1, 2, sector_length_m=250)
        self.assertEqual(c.sector_length_m, 250)
        self.assertEqual(len(c.sectors), 4)
        self.assertEqual(c.sectors[0].distance_start_m, c.distance_start_m)
        self.assertEqual(c.sectors[-1].distance_end_m, c.distance_end_m)
        self.assertEqual([s.distance_end_m for s in c.sectors[:-1]], [250, 500, 750])
        for s in c.sectors:
            width = s.distance_end_m - s.distance_start_m
            midpoint = (s.distance_start_m + s.distance_end_m) / 2
            self.assertAlmostEqual(s.reference_duration_s, width / 100)
            self.assertAlmostEqual(s.target_duration_s, width * 9 / 1000)
            self.assertAlmostEqual(s.delta_time_s, -width / 1000)
            self.assertAlmostEqual(s.cumulative_delta_end_s - s.cumulative_delta_start_s, s.delta_time_s)
            speed = s.channels['GPS Speed']
            self.assertEqual(speed['unit'], 'km/h')
            self.assertAlmostEqual(speed['reference']['distance_mean'], 110 + midpoint / 100)
            self.assertAlmostEqual(speed['target']['entry'], 120 + s.distance_start_m * 9 / 1000)
            self.assertAlmostEqual(speed['target']['exit'], 120 + s.distance_end_m * 9 / 1000)
        self.assertAlmostEqual(sum(s.delta_time_s for s in c.sectors), c.common_delta_time_s)
        self.assertAlmostEqual(c.common_delta_time_s, -(c.distance_end_m-c.distance_start_m)/1000)
        self.assertNotAlmostEqual(c.common_delta_time_s, c.delta_time_s, places=3)

    def test_sector_statistics_retain_short_native_peak_independent_of_display_points(self):
        self.sql('UPDATE "Ground Speed" SET value=CASE WHEN rowid=1500 THEN 300 ELSE 0 END')
        sparse = self.reader.compare_laps(1, 2, 2)
        dense = self.reader.compare_laps(1, 2, 501)
        self.assertEqual(sparse.sectors, dense.sectors)
        self.assertEqual(sparse.common_delta_time_s, dense.common_delta_time_s)
        s = sparse.sectors[1]
        ref = s.channels['Ground Speed']['reference']
        self.assertEqual(ref['maximum'], 300)
        self.assertEqual(ref['minimum'], 0)
        self.assertEqual(ref['entry'], 0)
        self.assertEqual(ref['exit'], 0)
        # The native triangular peak has 2 m base and area 300 km/h*m.
        self.assertAlmostEqual(ref['distance_mean'], 300/(s.distance_end_m-s.distance_start_m))
        self.assertEqual(s.channels['Ground Speed']['target']['maximum'], 0)
        self.assertTrue(all(p['Ground Speed_reference']==0 for p in sparse.points))

    def test_sector_direction_reversal_and_single_partial_bin(self):
        forward = self.reader.compare_laps(1, 2, sector_length_m=2000)
        reverse = self.reader.compare_laps(2, 1, sector_length_m=2000)
        self.assertEqual(len(forward.sectors), 1)
        a, b = forward.sectors[0], reverse.sectors[0]
        self.assertAlmostEqual(a.delta_time_s, -b.delta_time_s)
        self.assertEqual(a.channels['Throttle Pos']['reference'], b.channels['Throttle Pos']['target'])
        self.assertAlmostEqual(a.delta_time_s, forward.common_delta_time_s)

    def test_sector_length_invalid_types_and_limits(self):
        for value in (True, 99, 2001, 500.5, '500'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.reader.compare_laps(1, 2, sector_length_m=value)

    def test_invalid_selection_and_point_limits(self):
        for args in [(1, 1), (True, 2), (1, 2, 1), (1, 2, 502), (1, 2, 2.5), (1, 2, True)]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.reader.compare_laps(*args)
        for args in [(0, 1), (1, 3), (1, 99)]:
            with self.subTest(args=args), self.assertRaises(LmuTelemetryFormatError):
                self.reader.compare_laps(*args)
        self.assertEqual(len(self.reader.compare_laps(1, 2, 2).points), 2)
        self.assertEqual(len(self.reader.compare_laps(1, 2, 501).points), 501)

    def test_truncated_control_samples_are_rejected(self):
        self.sql('DELETE FROM "Throttle Pos" WHERE rowid>=1400')
        with self.assertRaisesRegex(LmuTelemetryFormatError, '샘플'):
            self.reader.compare_laps(1, 2)

    def test_nonfinite_control_samples_are_rejected(self):
        self.sql('UPDATE "Brake Pos" SET value=NULL WHERE rowid=600')
        with self.assertRaisesRegex(LmuTelemetryFormatError, '결측값'):
            self.reader.compare_laps(1, 2)

    def test_declared_but_missing_channel_table_is_a_format_error(self):
        self.sql('DROP TABLE "Brake Pos"')
        with self.assertRaisesRegex(LmuTelemetryFormatError, '채널 테이블'):
            self.reader.compare_laps(1, 2)

    def test_irregular_clock_and_small_distance_reversal_are_rejected(self):
        self.sql('UPDATE "GPS Time" SET value=value+.01 WHERE rowid=800')
        with self.assertRaisesRegex(LmuTelemetryFormatError, 'GPS Time'):
            self.reader.compare_laps(1, 2)
        self.sql('UPDATE "GPS Time" SET value=value-.01 WHERE rowid=800')
        self.sql('UPDATE "Lap Dist" SET value=value-15 WHERE rowid=150')
        with self.assertRaisesRegex(LmuTelemetryFormatError, '단조 증가'):
            self.reader.compare_laps(1, 2)

    def test_delayed_boundary_reset_drops_previous_lap_sample(self):
        self.sql('UPDATE "Lap Dist" SET value=999 WHERE rowid IN (100,200)')
        c = self.reader.compare_laps(1, 2)
        self.assertGreater(c.distance_start_m, 12)
        for p in c.points:
            self.assertAlmostEqual(p['delta_time_s'], -p['distance_m']/1000, places=7)

    def test_missing_optional_channels_and_raw_units(self):
        self.sql("DELETE FROM channelsList WHERE channelName IN ('GPS Speed','Brake Pos','Steering Pos')")
        self.sql("UPDATE channelsList SET unit='raw' WHERE channelName='Throttle Pos'")
        c = self.reader.compare_laps(1, 2)
        self.assertEqual(set(c.channels), {'Ground Speed', 'Throttle Pos'})
        self.assertEqual(c.channels['Throttle Pos']['unit'], 'raw')


@unittest.skipUnless(duckdb, 'LMU 전용 의존성을 설치하지 않은 환경입니다.')
class LmuTelemetryReaderTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'session.duckdb'
        make_lmu_file(self.path)
        self.reader = LmuTelemetryReader(self.path)

    def sql(self, query):
        con = duckdb.connect(str(self.path))
        try:
            con.execute(query)
        finally:
            con.close()

    def test_read_only_and_metadata_allowlist(self):
        before = hashlib.sha256(self.path.read_bytes()).digest()
        summary = self.reader.summary()
        self.assertEqual(summary.metadata['track_name'], 'Test Circuit')
        self.assertNotIn('SteamID', summary.metadata)
        self.assertNotIn('CarSetup', summary.metadata)
        self.assertEqual(summary.sample_counts['Lap Dist'], 61)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).digest(), before)
        self.assertFalse(self.path.with_suffix('.duckdb.wal').exists())

    def test_boundaries_are_timestamp_based_and_partial_tail_is_excluded(self):
        laps = self.reader.summary().laps
        self.assertEqual(len(laps), 2)
        self.assertEqual([lap.lap_number for lap in laps], [0, 1])
        self.assertEqual([(lap.start_index, lap.end_index) for lap in laps], [(0, 20), (20, 40)])
        self.assertEqual([lap.lap_time_s for lap in laps], [10, 10])
        self.assertEqual([lap.status for lap in laps], ['PIT_LAP', 'VALIDITY_UNKNOWN'])
        self.assertTrue(all(lap.valid is None for lap in laps))

    def test_missing_lap_time_never_shifts_next_time_to_previous_lap(self):
        self.sql('DELETE FROM "Lap Time" WHERE ts=110')
        laps = self.reader.summary().laps
        self.assertIsNone(laps[0].recorded_lap_time_s)
        self.assertEqual(laps[1].recorded_lap_time_s, 10)

    def test_timing_mismatch_is_reported(self):
        self.sql('UPDATE "Lap Time" SET value=9 WHERE ts=120')
        self.assertEqual(self.reader.summary().laps[1].status, 'TIMING_MISMATCH')

    def test_distance_reset_cannot_create_extra_lap(self):
        self.sql('UPDATE "Lap Dist" SET value=0 WHERE rowid=30')
        laps = self.reader.summary().laps
        self.assertEqual(len(laps), 2)
        self.assertEqual(laps[1].status, 'DISTANCE_DISCONTINUITY')

    def test_incomplete_samples_are_reported(self):
        self.sql('DELETE FROM "Lap Dist" WHERE rowid>=30')
        self.assertEqual(self.reader.summary().laps[1].status, 'INCOMPLETE')

    def test_missing_lap_boundary_never_falls_back_to_distance_resets(self):
        self.sql('DELETE FROM "Lap" WHERE ts>100')
        self.assertEqual(self.reader.summary().laps, ())

    def test_lap_number_jump_is_not_a_complete_lap(self):
        self.sql('UPDATE "Lap" SET value=4 WHERE ts=120')
        self.assertEqual(len(self.reader.summary().laps), 1)

    def test_duplicate_event_timestamps_rejected(self):
        self.sql('INSERT INTO "Lap" VALUES (110, 1)')
        with self.assertRaises(LmuTelemetryFormatError):
            self.reader.summary()

    def test_units_and_sample_values_are_never_guessed(self):
        specs = {spec.name: spec for spec in self.reader.channel_specs()}
        self.assertEqual(specs['Throttle Pos'].unit, '%')
        np.testing.assert_allclose(self.reader.read_channel('Throttle Pos'), [25] * 61)
        self.sql("UPDATE channelsList SET unit='km' WHERE channelName='Lap Dist'")
        self.assertIsNone(self.reader.summary().laps[1].distance_m)

    def test_invalid_frequency_rejected(self):
        self.sql('UPDATE channelsList SET frequency=0')
        with self.assertRaises(LmuTelemetryFormatError):
            self.reader.summary()

    def test_wrong_schema_and_corrupt_files_rejected(self):
        self.sql('DROP TABLE channelsList')
        with self.assertRaises(LmuTelemetryFormatError):
            self.reader.summary()
        bad = self.path.parent / 'corrupt.duckdb'
        bad.write_bytes(b'not a database')
        with self.assertRaises(LmuTelemetryFormatError):
            LmuTelemetryReader(bad).summary()

    def test_missing_dependency_is_not_misreported_as_corrupt_file(self):
        with patch('lmu_telemetry.require_lmu_dependency', side_effect=LmuTelemetryUnavailable('패키지 없음')):
            with self.assertRaises(LmuTelemetryUnavailable):
                self.reader.summary()

    def test_discovery_reads_only_duckdb_and_configured_lmu_folder(self):
        (self.path.parent / 'other.ibt').write_bytes(b'ignored')
        (self.path.parent / 'session.duckdb.wal').write_bytes(b'ignored')
        with patch.dict(os.environ, {'LMU_TELEMETRY_DIRS': str(self.path.parent),
                                     'IRACING_TELEMETRY_DIR': 'C:/unrelated'}):
            self.assertEqual(discover_lmu_telemetry_dirs(), (self.path.parent.resolve(),))
            self.assertEqual(list_lmu_telemetry_files(), [self.path.resolve()])
        self.assertEqual(list_lmu_telemetry_files([self.path.parent / 'missing']), [])


if __name__ == '__main__':
    unittest.main()
