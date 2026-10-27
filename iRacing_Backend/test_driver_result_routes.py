"""Result GET is local/read-only; native extraction remains the only write path."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from driver_result_routes import register_result_routes
from driver_results import DriverResultsStore


class DriverResultRoutesTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='driver-result-routes-')
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name)/'never-created'/'results.sqlite3'
        self.store = DriverResultsStore(self.path)
        self.app = FastAPI()
        register_result_routes(self.app, store_factory=lambda: self.store)
        self.client = self.enterContext(TestClient(self.app))

    def test_game_and_all_supported_periods_read_without_creating_storage(self):
        for game in ('iracing', 'lmu'):
            for period in (0, 30, 90):
                response = self.client.get(f'/api/driver-results?game={game}&period_days={period}')
                self.assertEqual(response.status_code, 200)
                self.assertEqual((response.json()['game'], response.json()['period_days']), (game, period))
                self.assertIsNone(response.json()['career']['wins']['value'])
        self.assertFalse(self.path.parent.exists())

    def test_invalid_game_and_period_are_rejected(self):
        for query in ('game=other', 'period_days=-1', 'period_days=365', 'period_days=unknown'):
            self.assertEqual(self.client.get('/api/driver-results?'+query).status_code, 422)

    def test_manual_posted_position_is_not_an_official_import_route(self):
        response = self.client.post('/api/driver-results', json={'class_position': 1, 'official': True})
        self.assertEqual(response.status_code, 405)
        self.assertFalse(self.path.exists())

    def test_storage_error_is_retryable_and_does_not_leak_paths(self):
        self.store.snapshot = Mock(side_effect=ValueError(str(self.path)))
        response = self.client.get('/api/driver-results')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(str(self.path), json.dumps(response.json()))


if __name__ == '__main__':
    unittest.main()
