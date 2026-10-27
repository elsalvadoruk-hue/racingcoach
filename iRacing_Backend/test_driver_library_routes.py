"""Library job API contracts without the application, personal files or SDK."""
import unittest
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from driver_library_routes import register_library_routes
from driver_history_refresh import register_refresh_routes


class LibraryRouteTests(unittest.TestCase):
    def setUp(self):
        self.service = Mock()
        self.service.status.return_value = {'game': 'iracing', 'status': 'IDLE', 'busy_game': None}
        self.service.start.return_value = {'game': 'iracing', 'status': 'SCANNING'}
        self.service.pause.return_value = {'game': 'iracing', 'status': 'PAUSING'}
        self.blocked = Mock(return_value=False)
        app = FastAPI()
        register_library_routes(app, self.service, blocked=self.blocked)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_get_is_passive_and_start_and_pause_are_explicit(self):
        self.assertEqual(self.client.get('/api/driver-history/library').json()['status'], 'IDLE')
        self.service.start.assert_not_called()
        self.service.pause.assert_not_called()
        self.assertEqual(self.client.post('/api/driver-history/library?game=lmu').status_code, 200)
        self.service.start.assert_called_once_with('lmu')
        self.assertEqual(self.client.post('/api/driver-history/library/pause').json()['status'], 'PAUSING')
        self.service.pause.assert_called_once_with('iracing')

    def test_invalid_game_never_reaches_a_worker(self):
        for method, route in [('get', '/api/driver-history/library'), ('post', '/api/driver-history/library'), ('post', '/api/driver-history/library/pause')]:
            self.assertEqual(getattr(self.client, method)(route + '?game=other').status_code, 422)
        self.service.status.assert_not_called()
        self.service.start.assert_not_called()
        self.service.pause.assert_not_called()

    def test_a_connected_source_refresh_blocks_new_library_work_only(self):
        self.blocked.return_value = True
        response = self.client.post('/api/driver-history/library')
        self.assertEqual(response.status_code, 409)
        self.service.start.assert_not_called()
        self.assertEqual(self.client.get('/api/driver-history/library').status_code, 200)
        self.assertEqual(self.client.post('/api/driver-history/library/pause').status_code, 200)

    def test_library_work_blocks_legacy_refresh_without_blocking_status(self):
        service = Mock()
        service.status.return_value = {'status': 'IDLE'}
        app = FastAPI()
        register_refresh_routes(app, service, blocked=lambda: True)
        with TestClient(app) as client:
            self.assertEqual(client.post('/api/driver-history/refresh').status_code, 409)
            self.assertEqual(client.get('/api/driver-history/refresh').status_code, 200)
        service.start.assert_not_called()


if __name__ == '__main__':
    unittest.main()
