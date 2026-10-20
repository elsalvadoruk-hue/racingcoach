from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lmu_api import create_lmu_application
from lmu_routes import LmuRoutesServices, register_routes
from lmu_telemetry import LmuTelemetryUnavailable
from test_lmu_telemetry import duckdb, make_lmu_file


class LmuRouteTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)

    def client(self, **overrides):
        services = {'discover_dirs': lambda: (self.root,), 'dependency_check': lambda: None}
        services.update(overrides)
        app = FastAPI()
        register_routes(app, LmuRoutesServices(**services))
        client = TestClient(app)
        self.addCleanup(client.close)
        return client

    def test_missing_dependency_status_and_inspection(self):
        def missing():
            raise LmuTelemetryUnavailable('패키지 없음')
        client = self.client(dependency_check=missing)
        self.assertEqual(client.get('/api/lmu/status').json()['dependency'], 'MISSING')
        self.assertEqual(client.get('/api/lmu/telemetry/files').json()['files'], [])
        self.assertEqual(client.get('/api/lmu/telemetry/file', params={'file_id': 'a' * 24}).status_code, 503)

    def test_path_parameters_and_unknown_ids_rejected(self):
        client = self.client()
        for value in ('../session.duckdb', 'C:/session.duckdb', 'bad'):
            self.assertEqual(client.get('/api/lmu/telemetry/file', params={'file_id': value}).status_code, 422)
        self.assertEqual(client.get('/api/lmu/telemetry/file', params={'file_id': 'a' * 24}).status_code, 404)

    def test_live_collection_explicitly_unimplemented(self):
        status = self.client().get('/api/lmu/live/status').json()
        self.assertEqual(status['state'], 'NOT_IMPLEMENTED')
        self.assertFalse(status['collecting'])

    def test_disk_work_runs_outside_event_loop(self):
        def list_files(_dirs):
            self.assertNotIn('asyncio-portal', threading.current_thread().name)
            return []
        self.assertEqual(self.client(list_files=list_files).get('/api/lmu/telemetry/files').status_code, 200)

    @unittest.skipUnless(duckdb, 'LMU 전용 의존성을 설치하지 않은 환경입니다.')
    def test_duplicate_basenames_get_distinct_ids_and_no_automatic_best(self):
        first = self.root / 'session.duckdb'
        second = self.root / 'other' / 'session.duckdb'
        second.parent.mkdir()
        make_lmu_file(first)
        make_lmu_file(second)
        client = self.client(discover_dirs=lambda: (self.root, second.parent))
        listing = client.get('/api/lmu/telemetry/files').json()['files']
        self.assertEqual(len({file['file_id'] for file in listing}), 2)
        payload = client.get('/api/lmu/telemetry/file', params={'file_id': listing[0]['file_id']}).json()['file']
        self.assertEqual(len(payload['laps']), 2)
        self.assertIsNone(payload['best_lap_time_s'])
        self.assertEqual(payload['reference_status'], 'VALIDITY_UNVERIFIED')
        self.assertNotIn('PRIVATE_TEST_ID', str(payload))
        self.assertNotIn('PRIVATE_SETUP', str(payload))

    def test_corrupt_file_has_actionable_error(self):
        path = self.root / 'broken.duckdb'
        path.write_bytes(b'broken')
        client = self.client()
        file_id = client.get('/api/lmu/telemetry/files').json()['files'][0]['file_id']
        if duckdb:
            response = client.get('/api/lmu/telemetry/file', params={'file_id': file_id})
            self.assertEqual(response.status_code, 422)
            self.assertIn('기록을 종료한 파일', response.json()['detail'])

    def test_standalone_application_does_not_import_iracing_or_cloud_services(self):
        backend = Path(__file__).resolve().parent
        script = """import sys
sys.path.insert(0, sys.argv[1])
import lmu_api
for name in ('backend_api', 'live_telemetry', 'irsdk', 'app_settings', 'voice_coach', 'google.genai'):
    assert name not in sys.modules, name
assert not any(route.path.startswith('/api/live-coach') for route in lmu_api.app.routes)
"""
        result = subprocess.run([sys.executable, '-B', '-c', script, str(backend)],
                                cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_diagnostic_screen_is_served_by_lmu_application(self):
        with TestClient(create_lmu_application()) as client:
            response = client.get('/')
            self.assertEqual(response.status_code, 200)
            self.assertIn('LMU 텔레메트리 진단', response.text)
            self.assertIn('textContent', response.text)
            self.assertIn('베스트 랩 비교', response.text)


if __name__ == '__main__':
    unittest.main()
