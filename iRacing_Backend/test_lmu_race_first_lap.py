"""Recover the game's timed Race start without treating formation as a lap."""
import hashlib
from pathlib import Path
import tempfile
import unittest

import numpy as np

from lmu_analysis import lap_report
from lmu_comparison import compare_laps
from lmu_telemetry import LmuTelemetryFormatError, LmuTelemetryReader
from test_lmu_analysis import make_analysis_file
from test_lmu_telemetry import duckdb


def make_race_file(path):
    make_analysis_file(path)
    with duckdb.connect(str(path)) as connection:
        connection.executemany('INSERT INTO metadata VALUES (?, ?)',
            [('SessionType', 'Race'), ('CarClass', 'GT3')])
        for name, rows in {
            'Lap': [(100.0275, 0), (120., 1), (129., 2)],
            'Lap Time': [(120., 10.), (129., 9.)],
            'In Pits': [(100.0275, 0)],
        }.items():
            connection.execute(f'DELETE FROM "{name}"')
            connection.executemany(f'INSERT INTO "{name}" VALUES (?, ?)', rows)
        connection.execute('CREATE TABLE "Current Sector" (ts DOUBLE, value DOUBLE)')
        connection.executemany('INSERT INTO "Current Sector" VALUES (?, ?)',
            [(100.0275, 1), (108., 0), (110.012, 1), (113., 2),
             (117., 0), (120., 1), (123., 2), (126., 0), (129., 1)])
        # Distance freezes after Race start while native time/pedals continue.
        connection.execute('UPDATE "Lap Dist" SET value=CASE '
            'WHEN t<110 THEN (t-100.0275)*100 WHEN t<112 THEN .001 '
            'WHEN t<120 THEN (t-112)*125 ELSE (t-120)*1000/9 END '
            'FROM (SELECT rowid AS r,100.0275+rowid/10.0 AS t FROM "Lap Dist") WHERE rowid=r')


@unittest.skipUnless(duckdb, 'LMU 전용 의존성이 없습니다.')
class RaceFirstLapTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name)/'race.duckdb'
        make_race_file(self.path)
        self.reader = LmuTelemetryReader(self.path)

    def sql(self, query):
        with duckdb.connect(str(self.path)) as connection:
            connection.execute(query)

    def test_first_race_time_is_recovered_and_later_counters_are_offset_consistently(self):
        before = (self.path.stat().st_mtime_ns, hashlib.sha256(self.path.read_bytes()).digest())
        first, second = self.reader.summary().laps
        self.assertEqual([first.lap_number, second.lap_number], [1, 2])
        self.assertEqual([first.status, second.status], ['VALIDITY_UNKNOWN']*2)
        self.assertEqual(first.start_time_s, 110.)
        self.assertEqual(first.end_time_s, 120.)
        self.assertEqual(first.lap_time_s, 10.)
        self.assertIsNone(first.valid)
        provenance = first.timing_provenance
        self.assertEqual(provenance['method'], 'RACE_INITIAL_RECORDED_TIME_CORROBORATED_V1')
        self.assertEqual(provenance['source_lap_counter'], 0)
        self.assertEqual(provenance['original_start_time_s'], 100.0275)
        self.assertAlmostEqual(provenance['excluded_leading_s'], 9.9725)
        self.assertAlmostEqual(provenance['sector_start_time_s'], 110.012)
        self.assertLess(provenance['distance_reset_before_time_s'], 110.)
        self.assertGreater(provenance['distance_reset_after_time_s'], 110.)
        self.assertEqual(second.timing_provenance,
            {'method': 'RACE_LAP_COUNTER_OFFSET_V1', 'source_lap_counter': 1})
        self.assertEqual(self.reader.analysis_lap(1).start_time_s, 110.)
        self.assertEqual(self.reader.analysis_lap(2).start_time_s, 120.)
        with self.assertRaises(LmuTelemetryFormatError):
            self.reader.analysis_lap(0)
        self.assertEqual(before, (self.path.stat().st_mtime_ns, hashlib.sha256(self.path.read_bytes()).digest()))
        self.assertFalse(Path(str(self.path)+'.wal').exists())

    def test_missing_sector_transition_or_distance_reset_cannot_recover_a_start(self):
        for query in ('DELETE FROM "Current Sector"',
                      'UPDATE "Current Sector" SET value=2 WHERE ts=108',
                      'UPDATE "Current Sector" SET ts=110.2 WHERE ts=110.012',
                      'UPDATE "Lap Dist" SET value=rowid*10'):
            with self.subTest(query=query):
                temporary = tempfile.TemporaryDirectory()
                self.addCleanup(temporary.cleanup)
                path = Path(temporary.name)/'uncorroborated.duckdb'
                make_race_file(path)
                with duckdb.connect(str(path)) as connection:
                    connection.execute(query)
                first = LmuTelemetryReader(path).summary().laps[0]
                self.assertEqual(first.lap_number, 0)
                self.assertEqual(first.start_time_s, 100.0275)
                self.assertIsNone(first.timing_provenance)
                self.assertNotEqual(first.status, 'VALIDITY_UNKNOWN')

    def test_ambiguous_recorded_time_does_not_recover(self):
        self.sql('INSERT INTO "Lap Time" VALUES (119.98,10)')
        first = self.reader.summary().laps[0]
        self.assertEqual(first.lap_number, 0)
        self.assertIsNone(first.recorded_lap_time_s)
        self.assertIsNone(first.timing_provenance)
        self.assertNotEqual(first.status, 'VALIDITY_UNKNOWN')

    def test_practice_numbering_and_mismatches_stay_unchanged(self):
        self.sql("UPDATE metadata SET value='Practice' WHERE key='SessionType'")
        first, second = self.reader.summary().laps
        self.assertEqual([first.lap_number, second.lap_number], [0, 1])
        self.assertNotEqual(first.status, 'VALIDITY_UNKNOWN')
        self.assertIsNone(first.timing_provenance)
        self.assertEqual(second.status, 'VALIDITY_UNKNOWN')

    def test_later_race_timing_mismatch_remains_rejected(self):
        self.sql('UPDATE "Lap Time" SET value=8 WHERE ts=129')
        first, second = self.reader.summary().laps
        self.assertEqual(first.status, 'VALIDITY_UNKNOWN')
        self.assertEqual(second.lap_number, 2)
        self.assertEqual(second.status, 'TIMING_MISMATCH')
        with self.assertRaises(LmuTelemetryFormatError):
            self.reader.analysis_lap(2)

    def test_pit_state_in_recovered_race_span_is_excluded(self):
        self.sql('INSERT INTO "In Pits" VALUES (111,1),(113,0)')
        first = self.reader.summary().laps[0]
        self.assertEqual(first.lap_number, 1)
        self.assertEqual(first.status, 'PIT_LAP')
        self.assertIsNone(first.valid)
        with self.assertRaises(LmuTelemetryFormatError):
            self.reader.analysis_lap(1)

    def test_formation_pit_state_is_not_current_race_pit_state(self):
        self.sql('INSERT INTO "In Pits" VALUES (105,1),(109,0)')
        self.assertEqual(self.reader.summary().laps[0].status, 'VALIDITY_UNKNOWN')

    def test_frozen_start_is_unmapped_but_native_pedal_time_and_later_distance_remain_usable(self):
        lap = self.reader.analysis_lap(1)
        distance = lap.channels['distance_m']
        info = lap.support['distance_m']
        self.assertEqual(info['status'], 'PARTIAL')
        self.assertFalse(info['full_lap_coverage'])
        self.assertAlmostEqual(info['leading_unmapped_s'], 1.9275)
        self.assertTrue(np.isnan(distance.at(110.5)))
        self.assertTrue(np.isnan(distance.at(111.5)))
        self.assertTrue(np.isfinite(distance.at(114.)))
        self.assertEqual(lap.support['timing'], self.reader.summary().laps[0].timing_provenance)
        self.assertNotIn('timing_provenance', lap.metadata)
        report = lap_report(lap)
        self.assertGreater(report['pedal_usage']['observed_time_s'], 9.9)
        self.assertEqual(report['braking']['status'], 'PARTIAL')
        comparison = compare_laps(lap, lap)
        self.assertEqual(comparison['delta_time_s'], 0.)
        self.assertGreaterEqual(comparison['points'][0]['target_elapsed_s'], 1.9275-1e-9)
        self.assertTrue(all(point['delta_time_s'] == 0 for point in comparison['points']))
        next_lap = self.reader.analysis_lap(2)
        self.assertEqual(next_lap.support['distance_m']['status'], 'AVAILABLE')
        self.assertTrue(next_lap.support['distance_m']['full_lap_coverage'])


if __name__ == '__main__':
    unittest.main()
