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
