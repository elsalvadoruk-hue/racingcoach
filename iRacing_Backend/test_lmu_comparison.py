import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from lmu_comparison import compare_laps
from lmu_telemetry import LmuTelemetryReader
from test_lmu_analysis import make_analysis_file
from test_lmu_telemetry import duckdb, make_comparison_file


@unittest.skipUnless(duckdb, 'LMU 전용 의존성을 설치하지 않은 환경입니다.')
class FileComparisonTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.paths = [Path(folder.name)/name for name in ('reference.duckdb','target.duckdb')]
        for path in self.paths:
            make_comparison_file(path)
            with duckdb.connect(str(path)) as con:
                con.execute("INSERT INTO metadata VALUES ('CarClass','GT3')")

    def sql(self, index, query):
        with duckdb.connect(str(self.paths[index])) as con:
            con.execute(query)

    def compare(self, **kwargs):
        return compare_laps(LmuTelemetryReader(self.paths[0]).analysis_lap(1),
                            LmuTelemetryReader(self.paths[1]).analysis_lap(2), **kwargs)

    def test_independent_session_clocks_analytic_values_and_read_only(self):
        self.sql(1, 'UPDATE "GPS Time" SET value=value+1000')
        for table in ('Lap','Lap Time','In Pits'):
            self.sql(1, f'UPDATE "{table}" SET ts=ts+1000')
        before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in self.paths]
        report = self.compare()
        json.dumps(report, allow_nan=False)
        self.assertEqual(report['delta_time_s'], -1)
        self.assertEqual(report['compatibility']['status'], 'METADATA_MATCH_ONLY')
        self.assertIsNone(report['reference_valid'])
        self.assertIsNone(report['target_valid'])
        self.assertEqual(set(report['channels']), {'speed_kmh','throttle_pct','brake_pct','steering_pct'})
        for row in report['points']:
            d = row['distance_m']
            self.assertAlmostEqual(row['reference_elapsed_s'], d/100)
            self.assertAlmostEqual(row['target_elapsed_s'], d*9/1000)
            self.assertAlmostEqual(row['delta_time_s'], -d/1000)
            for role, t in [('reference',110+d/100),('target',120+d*9/1000)]:
                for key, scale in [('speed_kmh',1),('throttle_pct',.5),('brake_pct',.1),('steering_pct',-.2)]:
                    self.assertAlmostEqual(row[f'{key}_{role}'], t*scale)
        for sector in report['sectors']:
            a,b = sector['distance_start_m'], sector['distance_end_m']
            self.assertAlmostEqual(sector['reference_duration_s'], (b-a)/100)
            self.assertAlmostEqual(sector['target_duration_s'], (b-a)*9/1000)
            self.assertAlmostEqual(sector['channels']['throttle_pct']['reference']['distance_mean'],
                                   .5*(110+(a+b)/200))
        self.assertEqual([hashlib.sha256(p.read_bytes()).hexdigest() for p in self.paths], before)
        self.assertTrue(all(not p.with_suffix('.duckdb.wal').exists() for p in self.paths))

    def test_sector_results_independent_of_display_grid_and_direction(self):
        a,b = self.compare(points=3,sector_length_m=250), self.compare(points=501,sector_length_m=250)
        self.assertEqual(a['sectors'],b['sectors'])
        self.assertAlmostEqual(sum(s['delta_time_s'] for s in a['sectors']),a['common_delta_time_s'])
        reverse = compare_laps(LmuTelemetryReader(self.paths[1]).analysis_lap(2),
                               LmuTelemetryReader(self.paths[0]).analysis_lap(1),points=3,sector_length_m=250)
        self.assertAlmostEqual(reverse['delta_time_s'], -a['delta_time_s'])
        for p,q in zip(a['points'],reverse['points']):
            self.assertEqual(p['distance_m'],q['distance_m'])
            self.assertAlmostEqual(p['delta_time_s'],-q['delta_time_s'])

    def test_metadata_missing_or_mismatch_never_infers_compatibility(self):
        for key in ('TrackName','TrackLayout','CarName','CarClass'):
            with self.subTest(key=key):
                with duckdb.connect(str(self.paths[1])) as con:
                    old = con.execute('SELECT value FROM metadata WHERE key=?',[key]).fetchone()[0]
                    con.execute('UPDATE metadata SET value=? WHERE key=?',['Other',key])
                with self.assertRaisesRegex(ValueError,'메타데이터'):
                    self.compare()
                self.sql(1,f"UPDATE metadata SET value='' WHERE key='{key}'")
                with self.assertRaisesRegex(ValueError,'메타데이터'):
                    self.compare()
                with duckdb.connect(str(self.paths[1])) as con:
                    con.execute('UPDATE metadata SET value=? WHERE key=?',[old,key])

    def test_mismatched_distance_extent_is_rejected(self):
        self.sql(1,'UPDATE "Lap Dist" SET value=value*2')
        with self.assertRaisesRegex(ValueError,'거리 범위'):
            self.compare()

    def test_missing_or_invalid_input_is_omitted_without_zero_filling(self):
        self.sql(1,'UPDATE "Brake Pos" SET value=NULL WHERE rowid=1100')
        self.sql(1,"DELETE FROM channelsList WHERE channelName='Throttle Pos'")
        result = self.compare()
        self.assertNotIn('brake_pct',result['channels'])
        self.assertNotIn('throttle_pct',result['channels'])
        self.assertEqual(result['channel_support']['brake_pct']['status'],'UNAVAILABLE')
        self.assertNotIn('brake_pct_target',result['points'][0])
        self.assertTrue(result['sectors'])

    def test_truncated_or_unknown_unit_channel_does_not_narrow_or_fabricate_comparison(self):
        self.sql(1,'DELETE FROM "Brake Pos" WHERE rowid>=1100')
        self.sql(1,"UPDATE channelsList SET unit='unknown' WHERE channelName='Throttle Pos'")
        result = self.compare()
        self.assertNotIn('brake_pct',result['channels'])
        self.assertNotIn('throttle_pct',result['channels'])
        self.assertEqual(result['channel_support']['throttle_pct']['target']['status'],'UNSUPPORTED_UNIT')

    def test_units_are_normalized_and_processing_stages_are_not_mixed(self):
        a=self.compare()
        self.sql(1,'UPDATE "Ground Speed" SET value=value/3.6')
        self.sql(1,"UPDATE channelsList SET unit='m/s' WHERE channelName='Ground Speed'")
        self.sql(1,'UPDATE "Throttle Pos" SET value=value/100')
        self.sql(1,"UPDATE channelsList SET unit='ratio' WHERE channelName='Throttle Pos'")
        b=self.compare()
        for x,y in zip(a['points'],b['points']):
            self.assertAlmostEqual(x['speed_kmh_target'],y['speed_kmh_target'])
            self.assertAlmostEqual(x['throttle_pct_target'],y['throttle_pct_target'])
        self.sql(1,'CREATE TABLE "Brake Pos Unfiltered" AS SELECT value FROM "Brake Pos"')
        self.sql(1,"INSERT INTO channelsList VALUES ('Brake Pos Unfiltered',50,'%')")
        c=self.compare()
        self.assertNotIn('brake_pct',c['channels'])
        self.assertIn('처리 단계',c['channel_support']['brake_pct']['reason'])

    def test_shared_overlay_origin_preserves_offsets_and_splits_missing_positions(self):
        for path in self.paths:
            path.unlink()
            make_analysis_file(path)
            with duckdb.connect(str(path)) as con:
                con.execute("INSERT INTO metadata VALUES ('CarClass','GT3')")
        self.sql(1,'UPDATE "GPS Longitude" SET value=value+.0001')
        self.sql(1,'UPDATE "GPS Longitude" SET value=NULL WHERE rowid=250')
        a=self.compare()
        overlay=a['track_overlay']
        self.assertEqual(overlay['status'],'SCHEMATIC')
        ref,tar=overlay['paths']
        self.assertEqual(tar['segment_count'],2)
        self.assertEqual(ref['points'][0]['x_m'],0)
        self.assertGreater(tar['points'][0]['x_m'],40)
        self.assertEqual(tar['origin'],'REFERENCE_LAP_SAMPLE')
        b=self.compare(points=3)
        self.assertEqual(a['sectors'],b['sectors'])

    def test_bad_arguments(self):
        for override in ({'points':2},{'points':502},{'points':True},{'sector_length_m':99},
                         {'sector_length_m':2001},{'sector_length_m':250.5}):
            with self.subTest(override=override), self.assertRaises(ValueError):
                self.compare(**override)


if __name__=='__main__':
    unittest.main()
