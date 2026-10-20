"""Personal best library: isolated SQLite, observed weather, current source files."""
from pathlib import Path
from contextlib import closing
import sqlite3
import tempfile
import unittest

from personal_reference import PersonalReferenceIndex, compatibility, identity


class PersonalReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.index = PersonalReferenceIndex(self.root / 'existing.sqlite3')
        self.key = identity({'car_name': 'GT3', 'track_name': 'Monza', 'track_config': 'GP'})

    def put(self, name, seconds, **conditions):
        path = self.root / name
        path.write_bytes(name.encode())
        self.index.index_recording('IRACING', path, self.key, [
            {'lap': 3, 'eligible': True, 'lap_time_s': seconds, 'conditions': conditions},
            {'lap': 4, 'eligible': False, 'lap_time_s': 1, 'conditions': conditions}])
        return path

    def test_fastest_order_persists_without_raw_channels_or_touching_existing_tables(self):
        with closing(sqlite3.connect(self.index.path)) as db, db:
            db.execute('CREATE TABLE user_notes(value TEXT)')
            db.execute("INSERT INTO user_notes VALUES ('keep')")
        self.put('slow.ibt', 120, weather_declared_wet=False)
        self.put('fast.ibt', 117, weather_declared_wet=False)
        reopened = PersonalReferenceIndex(self.index.path)
        self.assertEqual([row['lap_time_s'] for row in reopened.candidates('IRACING', self.key)], [117, 120])
        self.assertFalse(reopened.candidates('LMU', self.key))
        with closing(sqlite3.connect(self.index.path)) as db, db:
            self.assertEqual(db.execute('SELECT value FROM user_notes').fetchone()[0], 'keep')
            self.assertNotIn('channels', [row[1] for row in db.execute('PRAGMA table_info(personal_reference_laps)')])

    def test_stale_missing_or_changed_source_never_selected(self):
        path = self.put('best.ibt', 100)
        self.assertTrue(self.index.has_recording('IRACING', path))
        path.write_bytes(b'changed recording')
        self.assertFalse(self.index.has_recording('IRACING', path))
        self.assertEqual(self.index.candidates('IRACING', self.key), [])
        path.unlink()
        self.assertEqual(self.index.candidates('IRACING', self.key), [])

    def test_different_vehicle_or_layout_and_invalid_laps_never_enter_candidates(self):
        self.put('best.ibt', 100)
        self.assertEqual(len(self.index.candidates('IRACING', self.key)), 1)
        for field in ('car', 'track', 'layout'):
            self.assertFalse(self.index.candidates('IRACING', {**self.key, field: 'other'}))

    def test_dry_wet_mismatch_rejected_with_positive_surface_signal_overriding_dry_declaration(self):
        dry = {'weather_declared_wet': False, 'track_wetness_code': 1}
        for wet in ({'weather_declared_wet': True}, {'weather_declared_wet': False, 'track_wetness_code': 5},
                    {'weather_declared_wet': False, 'precipitation_pct': 20}, {'track_wetness_pct': 45}):
            self.assertFalse(compatibility(dry, wet)['compatible'])

    def test_reasonably_similar_conditions_allowed_extreme_temperature_tire_or_rain_rejected(self):
        base = {'weather_declared_wet': False, 'track_temp_c': 30, 'air_temp_c': 20, 'tire_compound': 0}
        self.assertTrue(compatibility(base, {**base, 'track_temp_c': 37, 'air_temp_c': 24})['compatible'])
        self.assertFalse(compatibility(base, {**base, 'track_temp_c': 50})['compatible'])
        self.assertFalse(compatibility(base, {**base, 'tire_compound': 1})['compatible'])
        self.assertFalse(compatibility({'track_wetness_pct': 10}, {'track_wetness_pct': 80})['compatible'])
        self.assertFalse(compatibility({'minimum_path_wetness_pct': 10}, {'minimum_path_wetness_pct': 80})['compatible'])
        self.assertEqual(compatibility({'minimum_path_wetness_pct': 0}, {})['surface']['current'], 'UNKNOWN')

    def test_unknown_weather_is_explicit_and_never_treated_as_dry_across_files(self):
        result = compatibility({}, {})
        self.assertFalse(result['compatible'])
        self.assertEqual(result['surface']['current'], 'UNKNOWN')
        same_file = compatibility({}, {}, same_file=True)
        self.assertTrue(same_file['compatible'])
        self.assertEqual(same_file['status'], 'PARTIAL')
        self.assertEqual(same_file['warnings'], ['SURFACE_CONDITION_UNKNOWN'])

    def test_existing_analysis_paths_seed_library_without_altering_sessions(self):
        with closing(sqlite3.connect(self.index.path)) as db, db:
            db.execute('CREATE TABLE sessions(file_path TEXT)')
            db.execute('INSERT INTO sessions VALUES (?)', (str(self.root / 'old.ibt'),))
        self.assertEqual(self.index.previous_analysis_paths(), [self.root / 'old.ibt'])

    def test_snapshot_of_recording_that_changed_during_parse_is_never_fresh_indexed(self):
        path = self.put('best.ibt', 100)
        stat = path.stat()
        signature = (str(path), stat.st_size, stat.st_mtime_ns)
        path.write_bytes(b'recording is still growing')
        with self.assertRaises(ValueError):
            self.index.index_recording('IRACING', path, self.key, [
                {'lap': 3, 'eligible': True, 'lap_time_s': 90}], expected_signature=signature)
        self.assertEqual(self.index.candidates('IRACING', self.key), [])


if __name__ == '__main__':
    unittest.main()
