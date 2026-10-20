"""Main-app LMU composition without game access, personal stores or DuckDB."""
import builtins
from contextlib import asynccontextmanager
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

try:
    from application_runtime import create_application
except ModuleNotFoundError as exc:
    if exc.name != 'yaml':
        raise
    create_application = None
from lmu_practice_store import LmuPracticeStore
from lmu_shared_memory import LmuLiveUnavailable, SharedMemorySource


@asynccontextmanager
async def idle_lifespan(_app):
    yield


@unittest.skipIf(create_application is None, 'Main-app tests require the iRacing environment (PyYAML).')
class LmuMainIntegrationTests(unittest.TestCase):
    def test_optional_duckdb_does_not_break_main_routes_or_attach_to_the_game(self):
        original_import = builtins.__import__

        def without_duckdb(name, *args, **kwargs):
            if name == 'duckdb' or name.startswith('duckdb.'):
                raise ModuleNotFoundError('DuckDB deliberately absent')
            return original_import(name, *args, **kwargs)

        def not_built(self):
            raise LmuLiveUnavailable('NATIVE_NOT_BUILT', 'build first')

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'recording.duckdb'
            source.write_bytes(b'not opened without optional dependency')
            store = root / 'lmu-practice.sqlite3'
            with (patch.dict(os.environ, {'LMU_TELEMETRY_DIRS': str(root)}),
                  patch('builtins.__import__', side_effect=without_duckdb),
                  patch.object(LmuPracticeStore, '__init__', lambda self: setattr(self, 'path', store)),
                  patch.object(SharedMemorySource, 'probe', not_built),
                  patch.object(SharedMemorySource, 'open', side_effect=AssertionError('game attach forbidden')) as attach):
                app = create_application('test', idle_lifespan, Mock(status=Mock(return_value={'state': 'STOPPED'})))
                with TestClient(app) as client:
                    status = client.get('/api/lmu/status')
                    self.assertEqual(status.status_code, 200)
                    self.assertEqual(status.json()['dependency'], 'MISSING')
                    self.assertEqual(status.json()['bridge']['state'], 'NATIVE_NOT_BUILT')
                    self.assertTrue(status.json()['bridge']['buildable'])
                    self.assertFalse(status.json()['diagnostic']['recording'])
                    listing = client.get('/api/lmu/telemetry/files')
                    self.assertEqual(listing.status_code, 200)
                    file_id = listing.json()['files'][0]['file_id']
                    self.assertEqual(client.get('/api/lmu/telemetry/file', params={'file_id': file_id}).status_code, 503)
                    self.assertEqual(client.get('/api/lmu/practice/tasks').json()['tasks'], [])
                    cors = client.options('/api/lmu/status', headers={
                        'Origin': 'http://localhost:24157', 'Access-Control-Request-Method': 'GET'})
                    self.assertEqual(cors.headers['access-control-allow-origin'], 'http://localhost:24157')
                attach.assert_not_called()
                self.assertFalse(store.exists())
                self.assertEqual(source.read_bytes(), b'not opened without optional dependency')

    def test_lmu_has_no_second_live_coach(self):
        # Spotter, pit wall, questions and the overlay are the shared iRacing routes.
        app = create_application('test', idle_lifespan, Mock())
        paths = set(app.openapi()['paths'])
        self.assertFalse({p for p in paths if p.startswith(('/api/lmu/spotter', '/api/lmu/voice', '/api/lmu/questions',
                                                            '/api/lmu/overlay', '/api/lmu/live/connection'))})
        self.assertIn('/api/lmu/live/bridge', paths)
        self.assertIn('/api/lmu/live/journal/start', paths)


if __name__ == '__main__':
    unittest.main()
