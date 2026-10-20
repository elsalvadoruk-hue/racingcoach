"""History contracts use synthetic measured sessions and temporary local stores."""
from contextlib import closing
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lmu_history import LmuHistoryStore, register_history_routes
from lmu_telemetry import LmuTelemetryUnavailable


FILE_A, FILE_B = 'a' * 24, 'b' * 24


def selection(file_id=FILE_A):
    return {'filename': file_id, 'target_lap': 1, 'reference_filename': FILE_B, 'reference_lap': 1}


def session(file_id=FILE_A, car='GT3', track='Monza', session_type='Race'):
    return {'provider': 'LMU', 'filename': file_id, 'file_name': file_id + '.duckdb',
            'reference_filename': FILE_B, 'reference_file_name': 'qualifying.duckdb',
            'defaultTarget': 1, 'defaultReference': 1,
            'session': {'car_name': car, 'track_name': track, 'track_layout': 'GP',
                        'session_type': session_type, 'driver_name': 'Never persist driver'},
            'laps': [{'lap_number': 1, 'lap_time_s': 122.1597, 'valid': None, 'measurement_eligible': True},
                     {'lap_number': 0, 'lap_time_s': None, 'valid': None, 'measurement_eligible': False}],
            'reference_laps': [{'lap_number': 1, 'lap_time_s': 115.6653, 'valid': None, 'measurement_eligible': True}],
            'lap_statistics': {'fastest_recorded_time_s': 122.1597},
            'track_path': [{'lat': 60., 'lon': 0.}], 'voice_answer': 'Do not persist'}


class LmuHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'history' / 'history.sqlite3'
        self.store = LmuHistoryStore(self.path)

    def test_read_empty_and_missing_do_not_create_a_store(self):
        self.assertEqual(self.store.records()['records'], [])
        with self.assertRaises(KeyError):
            self.store.get('missing')
        self.assertFalse(self.path.parent.exists())

    def test_snapshot_reopens_after_restart_and_retains_unknown_validity(self):
        original = session()
        record = self.store.save({**selection(), 'note': '  제동 확인  '}, original)
        restored = LmuHistoryStore(self.path).get(record['record_id'])
        self.assertEqual(restored['selection'], selection())
        self.assertEqual(restored['note'], '제동 확인')
        self.assertEqual(restored['summary']['target_time_s'], 122.1597)
        self.assertEqual(restored['summary']['reference_time_s'], 115.6653)
        self.assertIsNone(restored['summary']['valid'])
        self.assertEqual(restored['summary']['measured_lap_count'], 1)
        self.assertEqual(restored['summary']['lap_count'], 2)
        encoded = str(restored)
        self.assertNotIn('Never persist driver', encoded)
        self.assertNotIn('voice_answer', encoded)
        self.assertNotIn('track_path', encoded)
        self.assertEqual(original, session())

    def test_file_and_reference_identity_disambiguate_equal_lap_numbers(self):
        first = self.store.save(selection(), session())
        second = self.store.save(selection(FILE_B), session(FILE_B))
        third_session = session()
        third_session['reference_filename'] = FILE_A
        third = self.store.save({**selection(), 'reference_filename': FILE_A}, third_session)
        self.assertEqual(len({first['record_id'], second['record_id'], third['record_id']}), 3)
        self.assertEqual(self.store.records()['total'], 3)

    def test_resaving_same_resolved_selection_updates_one_record(self):
        first = self.store.save(selection(), session())
        updated = self.store.save({**selection(), 'note': '다음 주행'}, session())
        self.assertEqual(first['record_id'], updated['record_id'])
        self.assertEqual(first['created_at'], updated['created_at'])
        self.assertEqual(self.store.records()['total'], 1)
        self.assertEqual(updated['note'], '다음 주행')

    def test_search_filters_and_options_include_saved_metadata_and_note(self):
        self.store.save({**selection(), 'note': '브레이크 일찍'}, session())
        self.store.save(selection(FILE_B), session(FILE_B, 'Prototype', 'Spa', 'Qualifying'))
        self.assertEqual(self.store.records(q='브레이크')['total'], 1)
        self.assertEqual(self.store.records(q='spa')['total'], 1)
        self.assertEqual(self.store.records(car='GT3', track='Monza', session_type='Race')['total'], 1)
        self.assertEqual(self.store.records(track='Monza', car='Prototype')['total'], 0)
        self.assertEqual(self.store.records()['tracks'], ['Monza', 'Spa'])
        self.assertEqual(len(self.store.records(limit=1)['records']), 1)
        self.assertEqual(self.store.records(limit=1)['total'], 2)

    def test_env_path_is_lmu_specific_and_lazy(self):
        with patch.dict(os.environ, {'LMU_HISTORY_PATH': str(self.path), 'IRACING_TUNER_DB_PATH': 'never.sqlite3'}):
            self.assertEqual(LmuHistoryStore().path, self.path)
            self.assertFalse(self.path.exists())

    def test_wrong_database_is_never_modified(self):
        import sqlite3
        self.path.parent.mkdir()
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute('CREATE TABLE private_data (secret TEXT)')
            connection.execute("INSERT INTO private_data VALUES ('unchanged')")
            connection.commit()
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, '지원하지 않는'):
            self.store.save(selection(), session())
        self.assertEqual(self.path.read_bytes(), before)

    def client(self, adapter=None):
        app = FastAPI()
        self.adapter = adapter or Mock(return_value=session())
        register_history_routes(app, self.adapter, self.store)
        return TestClient(app)

    def test_http_save_recalculates_summary_instead_of_accepting_client_metrics(self):
        with self.client() as client:
            invalid = client.post('/api/lmu/history', json={**selection(), 'target_time_s': 1})
            self.assertEqual(invalid.status_code, 422)
            self.adapter.assert_not_called()
            response = client.post('/api/lmu/history', json={**selection(), 'note': '테스트'})
            self.assertEqual(response.status_code, 200, response.text)
            self.adapter.assert_called_once_with(selection())
            self.assertEqual(response.json()['record']['summary']['target_time_s'], 122.1597)

    def test_http_reopen_uses_stored_full_identity_and_recomputes_current_session(self):
        record = self.store.save(selection(), session())
        fresh = session()
        fresh['laps'][0]['lap_time_s'] = 123.
        with self.client(Mock(return_value=fresh)) as client:
            response = client.post('/api/lmu/history/' + record['record_id'] + '/open', json={})
            self.assertEqual(response.status_code, 200, response.text)
            self.adapter.assert_called_once_with(selection())
            self.assertEqual(response.json()['session']['laps'][0]['lap_time_s'], 123.)
            self.assertEqual(response.json()['record']['summary']['target_time_s'], 122.1597)

    def test_http_missing_source_and_missing_dependency_have_useful_status(self):
        record = self.store.save(selection(), session())
        for exception, expected in ((FileNotFoundError(), 404), (LmuTelemetryUnavailable('DuckDB 필요'), 503), (ValueError('랩 누락'), 422)):
            with self.subTest(expected=expected), self.client(Mock(side_effect=exception)) as client:
                response = client.post('/api/lmu/history/' + record['record_id'] + '/open')
                self.assertEqual(response.status_code, expected)
                self.assertEqual(client.post('/api/lmu/history', json=selection()).status_code, expected)
        self.assertEqual(self.store.records()['total'], 1)

    def test_http_list_is_lazy_searchable_and_validates_limits(self):
        with self.client() as client:
            self.assertEqual(client.get('/api/lmu/history').json()['total'], 0)
            self.assertFalse(self.path.exists())
            self.assertEqual(client.get('/api/lmu/history?limit=501').status_code, 422)
            self.assertEqual(client.post('/api/lmu/history', json={**selection(), 'target_lap': True}).status_code, 422)
            self.assertEqual(client.post('/api/lmu/history', json={**selection(), 'filename': '../secret'}).status_code, 422)
            self.assertEqual(client.post('/api/lmu/history', json={**selection(), 'note': 'x' * 2001}).status_code, 422)
            self.assertFalse(self.path.exists())
            client.post('/api/lmu/history', json={**selection(), 'note': '테스트'})
            self.assertEqual(client.get('/api/lmu/history', params={'q': '테스트'}).json()['total'], 1)
            self.assertEqual(client.post('/api/lmu/history/missing/open').status_code, 404)


if __name__ == '__main__':
    unittest.main()
