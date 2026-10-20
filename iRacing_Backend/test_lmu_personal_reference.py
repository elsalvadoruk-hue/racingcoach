"""Automatic LMU reference selection uses isolated stores and read-only fixtures."""
import hashlib
from pathlib import Path
import tempfile
import unittest

from lmu_routes import LmuRoutesServices, _file_id, _file_fingerprint
from lmu_workspace import workspace_payload
from personal_reference import PersonalReferenceIndex
from lmu_personal_reference import reference_compatibility
from test_lmu_analysis import make_analysis_file
from test_lmu_telemetry import duckdb


@unittest.skipUnless(duckdb, 'Optional LMU dependency unavailable')
class LmuPersonalReferenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.paths = [self.root / name for name in ('current.duckdb', 'faster.duckdb')]
        self.index = PersonalReferenceIndex(self.root / 'references.sqlite3')
        for path in self.paths:
            make_analysis_file(path)
            with duckdb.connect(str(path)) as db:
                db.execute("INSERT INTO metadata VALUES ('CarClass','GT3')")
        # Shorter, consistently resampled second recording, including event clocks.
        with duckdb.connect(str(self.paths[1])) as db:
            db.execute('UPDATE channelsList SET frequency=frequency/0.8')
            db.execute('UPDATE "GPS Time" SET value=100+(value-100)*0.8')
            for table in ('Lap', 'Lap Time', 'In Pits'):
                db.execute(f'UPDATE "{table}" SET ts=100+(ts-100)*0.8')
            db.execute('UPDATE "Lap Time" SET value=value*0.8')
        self.services = LmuRoutesServices(dependency_check=lambda: None,
                                         personal_reference_index=self.index)

    def surface(self, number, value, channel='Maximum Path Wetness (%)'):
        with duckdb.connect(str(self.paths[number])) as db:
            db.execute(f'CREATE TABLE "{channel}" (ts DOUBLE,value DOUBLE)')
            db.execute(f'INSERT INTO "{channel}" VALUES (99,?)', [value])

    def session(self):
        return workspace_payload(self.services, self.paths, _file_id, _file_fingerprint,
                                 'session', {'filename': _file_id(self.paths[0]),
                                             'target_lap': 1, 'reference_selection': 'AUTO'})

    def test_known_similar_conditions_select_fastest_other_file_and_persist(self):
        for n in range(2):
            self.surface(n, 0)
        before = [hashlib.sha256(path.read_bytes()).digest() for path in self.paths]
        result = self.session()
        self.assertEqual(result['reference_filename'], _file_id(self.paths[1]))
        self.assertEqual(result['defaultReference'], 2)
        self.assertAlmostEqual(result['initialComparison']['referenceTime'], 7.2)
        self.assertIsNone(result['initialComparison']['reference_valid'])
        self.assertEqual(result['personalReference']['conditions']['status'], 'SIMILAR')
        self.assertTrue(self.index.has_recording('LMU', self.paths[0]))
        self.assertTrue(self.index.has_recording('LMU', self.paths[1]))
        self.assertEqual(before, [hashlib.sha256(path.read_bytes()).digest() for path in self.paths])

    def test_wet_other_file_is_excluded(self):
        self.surface(0, 0)
        self.surface(1, 80)
        result = self.session()
        self.assertEqual(result['reference_filename'], _file_id(self.paths[0]))
        self.assertEqual(result['defaultReference'], 2)

    def test_zero_minimum_does_not_prove_dry_but_unknown_crossfile_can_be_fastest(self):
        for n in range(2):
            self.surface(n, 0, 'Minimum Path Wetness (%)')
        result = self.session()
        self.assertEqual(result['reference_filename'], _file_id(self.paths[1]))
        self.assertEqual(result['defaultReference'], 2)
        self.assertEqual(result['personalReference']['conditions']['status'], 'PARTIAL')
        self.assertIn('SURFACE_CONDITION_UNKNOWN', result['personalReference']['conditions']['warnings'])

    def test_missing_weather_selects_fastest_other_file_without_declaring_dry_or_game_valid(self):
        result = self.session()
        self.assertEqual(result['reference_filename'], _file_id(self.paths[1]))
        self.assertAlmostEqual(result['initialComparison']['referenceTime'], 7.2)
        self.assertIsNone(result['initialComparison']['reference_valid'])
        conditions = result['personalReference']['conditions']
        self.assertEqual(conditions['weather_group'], {'current': 'UNKNOWN', 'reference': 'UNKNOWN'})
        self.assertEqual(conditions['policy'], 'RAIN_OR_SURFACE_ONLY')

    def test_known_rain_difference_excludes_other_file(self):
        self.surface(0, 0, 'Rain Intensity (%)')
        self.surface(1, 50, 'Rain Intensity (%)')
        result = self.session()
        self.assertEqual(result['reference_filename'], _file_id(self.paths[0]))

    def test_other_vehicle_cannot_supply_reference(self):
        with duckdb.connect(str(self.paths[1])) as db:
            db.execute("UPDATE metadata SET value='Different vehicle' WHERE key='CarName'")
        result = self.session()
        self.assertEqual(result['reference_filename'], _file_id(self.paths[0]))

    def test_same_target_best_is_an_explicit_zero_baseline(self):
        result = workspace_payload(self.services, [self.paths[0]], _file_id, _file_fingerprint,
                                   'comparison', {'filename': _file_id(self.paths[0]),
                                                  'target_lap': 2, 'reference_selection': 'AUTO'})
        self.assertTrue(result['personalReference']['same_lap'])
        self.assertEqual(result['delta'], 0)

    def test_other_layout_cannot_supply_reference(self):
        for n in range(2):
            self.surface(n, 0)
        with duckdb.connect(str(self.paths[1])) as db:
            db.execute("UPDATE metadata SET value='Different layout' WHERE key='TrackLayout'")
        result = self.session()
        self.assertEqual(result['reference_filename'], _file_id(self.paths[0]))


class LmuReferenceWeatherPolicyTests(unittest.TestCase):
    def test_large_temperature_tire_wind_and_wetness_differences_are_allowed(self):
        first = {'precipitation_pct': 20, 'track_temp_c': 10, 'air_temp_c': 0,
                 'tire_compound': 0, 'wind_speed_kph': 0, 'track_wetness_pct': 10}
        second = {'precipitation_pct': 80, 'track_temp_c': 55, 'air_temp_c': 40,
                  'tire_compound': 2, 'wind_speed_kph': 60, 'track_wetness_pct': 90}
        result = reference_compatibility(first, second)
        self.assertTrue(result['compatible'])
        self.assertEqual(result['status'], 'SIMILAR')

    def test_unknown_weather_allows_crossfile_without_claiming_similarity(self):
        result = reference_compatibility({}, {'precipitation_pct': 80})
        self.assertTrue(result['compatible'])
        self.assertEqual(result['status'], 'PARTIAL')
        self.assertEqual(result['weather_group']['current'], 'UNKNOWN')

    def test_positive_rain_and_no_rain_differ_but_both_no_rain_ignore_surface_amount(self):
        self.assertFalse(reference_compatibility({'precipitation_pct': 0},
                                                {'precipitation_pct': 20})['compatible'])
        self.assertTrue(reference_compatibility({'precipitation_pct': 0, 'track_wetness_pct': 0},
                                               {'precipitation_pct': 0, 'track_wetness_pct': 80})['compatible'])

    def test_without_rain_observations_known_dry_wet_remain_separate(self):
        self.assertFalse(reference_compatibility({'track_wetness_pct': 0},
                                                {'track_wetness_pct': 80})['compatible'])
