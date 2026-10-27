"""Local journal HTTP validation and read/write boundaries, no application SDK."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from driver_history import DriverHistoryStore
from driver_journal import DriverJournal
from driver_journal_routes import register_journal_routes
from test_driver_history import identity
from test_driver_report import current_metrics, facts


class JournalRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='driver-journal-routes-test-')
        self.addCleanup(self.temp.cleanup)
        self.history = DriverHistoryStore(Path(self.temp.name) / 'history.sqlite3')
        self.now = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
        self.journal = DriverJournal(self.history.path, clock=lambda: self.now)
        app = FastAPI()
        register_journal_routes(app, history_factory=lambda: self.history, journal_factory=lambda: self.journal)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.history.record_source(current_metrics(facts('baseline', 'baseline')))
        self.drive_id = identity('baseline')
        self.feeling_url = '/api/driver-journal/feelings/' + self.drive_id

    def get(self):
        response = self.client.get('/api/driver-journal')
        self.assertEqual(response.status_code, 200)
        return response.json()

    def accept(self):
        candidate = self.get()['candidates'][0]
        return self.client.post('/api/driver-journal/challenges', json={key: candidate[key] for key in ('candidate_id', 'drive_id')})

    def test_get_is_passive_and_feeling_mutations_return_selected_context(self):
        with patch.object(self.journal, 'reconcile', side_effect=AssertionError('GET must not evaluate')):
            self.assertIsNone(self.get()['feeling'])
        self.assertFalse(self.journal.path.exists())
        response = self.client.put(self.feeling_url, json={'response': 'DIFFICULT'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['feeling']['response'], 'DIFFICULT')
        self.assertEqual(response.json()['selected_drive_id'], self.drive_id)
        response = self.client.put(self.feeling_url, json={'response': 'DIFFICULT', 'difficulty': 'BRAKING'})
        self.assertEqual(response.json()['feeling']['difficulty'], 'BRAKING')
        self.assertIsNone(self.client.delete(self.feeling_url).json()['feeling'])

    def test_bad_body_and_identifiers_are_rejected_before_write(self):
        for payload in ({'response': 'UNKNOWN'}, {'response': 'NEUTRAL', 'difficulty': 'BRAKING'},
                        {'response': 'DIFFICULT', 'difficulty': 'OTHER'}, {'response': 'NEUTRAL', 'private_path': 'forbidden'}):
            self.assertEqual(self.client.put(self.feeling_url, json=payload).status_code, 422)
        self.assertEqual(self.client.put('/api/driver-journal/feelings/nope', json={'response': 'NEUTRAL'}).status_code, 422)
        self.assertEqual(self.client.get('/api/driver-journal?game=other').status_code, 422)
        self.assertEqual(self.client.get('/api/driver-journal?drive_id=bad').status_code, 422)
        self.assertFalse(self.journal.path.exists())

    def test_wrong_game_or_missing_drive_cannot_receive_feeling(self):
        self.assertEqual(self.client.put(self.feeling_url + '?game=lmu', json={'response': 'NEUTRAL'}).status_code, 404)
        self.assertEqual(self.client.get('/api/driver-journal?drive_id=' + identity('absent')).status_code, 404)
        self.assertFalse(self.journal.path.exists())

    def test_challenge_select_cancel_and_explicit_reconcile(self):
        response = self.accept()
        self.assertEqual(response.status_code, 200)
        active = response.json()['active_challenge']
        self.history.record_source(current_metrics(facts('after', 'after', date='2026-10-27T12:01:00Z')))
        self.now += timedelta(hours=1)
        self.assertEqual(self.get()['active_challenge']['status'], 'ACTIVE')
        response = self.client.post('/api/driver-journal/reconcile')
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()['active_challenge'])
        self.assertEqual(response.json()['challenge_history'][0]['status'], 'ACHIEVED')
        self.assertEqual(self.client.post('/api/driver-journal/challenges/' + active['id'] + '/cancel').status_code, 409)

    def test_cross_game_cancel_does_not_change_original_goal(self):
        active = self.accept().json()['active_challenge']
        url = '/api/driver-journal/challenges/' + active['id'] + '/cancel'
        self.assertEqual(self.client.post(url + '?game=lmu').status_code, 404)
        self.assertEqual(self.get()['active_challenge']['id'], active['id'])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['challenge_history'][0]['status'], 'CANCELLED')

    def test_processing_error_is_generic_and_does_not_expose_private_path(self):
        with patch.object(self.journal, 'read', side_effect=OSError('PRIVATE RECORDING PATH')):
            response = self.client.get('/api/driver-journal')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('PRIVATE', response.text)


if __name__ == '__main__':
    unittest.main()
