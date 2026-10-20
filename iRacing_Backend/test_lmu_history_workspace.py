"""Saved LMU analysis opens the real read-only adapter using disposable files."""
import hashlib
from pathlib import Path
import tempfile
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lmu_corner_names import LmuCornerNamesStore
from lmu_history import LmuHistoryStore, register_history_routes
from lmu_routes import LmuRoutesServices, _file_id, _file_fingerprint
from lmu_workspace import workspace_payload
from test_lmu_analysis import make_analysis_file
from test_lmu_telemetry import duckdb


@unittest.skipUnless(duckdb, 'LMU optional DuckDB is unavailable.')
class LmuHistoryWorkspaceTests(unittest.TestCase):
    def test_cross_file_save_reload_reopen_leaves_native_recordings_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first, reference = root / 'race.duckdb', root / 'qualifying.duckdb'
            for path in (first, reference):
                make_analysis_file(path)
                with duckdb.connect(str(path)) as connection:
                    connection.execute("INSERT INTO metadata VALUES ('CarClass', 'GT3')")
            paths = (first, reference)
            before = {path: (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns) for path in paths}
            services = LmuRoutesServices(discover_dirs=lambda: (root,), dependency_check=lambda: None,
                                        corner_names_store=LmuCornerNamesStore(root / 'corners.json'))
            adapter = lambda body: workspace_payload(services, paths, _file_id, _file_fingerprint, 'session', body)
            store_path = root / 'history' / 'analysis.sqlite3'
            body = {'filename': _file_id(first), 'target_lap': 2, 'reference_filename': _file_id(reference),
                    'reference_lap': 1, 'note': '입력 위치 확인'}
            app = FastAPI()
            register_history_routes(app, adapter, LmuHistoryStore(store_path))
            with TestClient(app) as client:
                response = client.post('/api/lmu/history', json=body)
                self.assertEqual(response.status_code, 200, response.text)
                record = response.json()['record']
                self.assertEqual(record['summary']['file_name'], 'race.duckdb')
                self.assertEqual(record['summary']['reference_file_name'], 'qualifying.duckdb')
                self.assertIsNone(record['summary']['valid'])
            restarted_app = FastAPI()
            register_history_routes(restarted_app, adapter, LmuHistoryStore(store_path))
            with TestClient(restarted_app) as client:
                response = client.post(f"/api/lmu/history/{record['record_id']}/open")
                self.assertEqual(response.status_code, 200, response.text)
                current = response.json()['session']
                self.assertEqual(current['defaultTarget'], 2)
                self.assertEqual(current['defaultReference'], 1)
                self.assertEqual(current['reference_filename'], _file_id(reference))
                self.assertIsNone(current['initialComparison']['reference_valid'])
                self.assertEqual(client.get('/api/lmu/history').json()['total'], 1)
            after = {path: (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns) for path in paths}
            self.assertEqual(before, after)
            self.assertTrue(all(not Path(str(path) + '.wal').exists() for path in paths))
            self.assertFalse((root / 'corners.json').exists())


if __name__ == '__main__':
    unittest.main()
