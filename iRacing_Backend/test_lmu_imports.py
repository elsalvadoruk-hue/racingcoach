import asyncio
import hashlib
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import ClientDisconnect

from lmu_imports import import_dir, import_recording
from lmu_recordings import recordings, resolve
from lmu_routes import LmuRoutesServices, register_routes
from lmu_telemetry import LmuTelemetryReader, LmuTelemetryUnavailable
from test_lmu_telemetry import duckdb, make_comparison_file


@unittest.skipUnless(duckdb, 'LMU 전용 의존성을 설치하지 않은 환경입니다.')
class LmuImportTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        environment = patch.dict(os.environ, {'IRACING_DATA_DIR': str(self.root / 'app'),
                                              'LMU_TELEMETRY_DIRS': str(self.root / 'game')})
        environment.start()
        self.addCleanup(environment.stop)
        self.source = self.root / 'download.duckdb'
        make_comparison_file(self.source)
        app = FastAPI()
        register_routes(app)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def upload(self, filename='download.duckdb', content=None):
        return self.client.post('/api/lmu/telemetry/import', params={'filename': filename},
                                content=self.source.read_bytes() if content is None else content,
                                headers={'Content-Type': 'application/octet-stream'})

    def test_import_is_durable_discoverable_and_readable_without_changing_source(self):
        before = hashlib.sha256(self.source.read_bytes()).hexdigest()
        response = self.upload('내려받은 기준.duckdb')
        self.assertEqual(response.status_code, 200, response.text)
        row = response.json()['file']
        imported = resolve(row['name'])
        self.assertEqual(imported.parent, import_dir())
        self.assertEqual(imported, resolve(row['file_id']))
        self.assertEqual(recordings(), [imported])
        self.assertEqual(hashlib.sha256(imported.read_bytes()).hexdigest(), before)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), before)
        # A fresh catalog and request use the copy, without an in-memory registration.
        laps = self.client.post('/api/lmu/workspace/file-laps', json={'filename': row['file_id']})
        self.assertEqual(laps.status_code, 200, laps.text)
        self.assertGreater(len(laps.json()['laps']), 0)
        self.assertNotIn(str(self.root), response.text)
        self.assertNotIn('PRIVATE', response.text)

    def test_imported_name_opens_in_the_production_shared_workspace_and_comparison(self):
        # The common test fixture sets temporary persistence before backend import.
        from test_rolling_best import api
        from test_lmu_ibt import make_circuit
        circuit = self.root / 'circuit.duckdb'
        make_circuit(circuit)
        client = TestClient(api.app)
        self.addCleanup(client.close)
        # No lifespan: do not start SDK, collectors, or background services.
        first = client.post('/api/lmu/telemetry/import?filename=lap.duckdb', content=circuit.read_bytes()).json()['file']['name']
        second = client.post('/api/lmu/telemetry/import?filename=reference.duckdb', content=circuit.read_bytes()).json()['file']['name']
        listing = client.post('/api/lmu/workspace/files', json={}).json()['files']
        self.assertEqual({row['name'] for row in listing}, {first, second})
        response = client.post('/api/lmu/workspace/session', json={'filename': first, 'preview': True})
        self.assertEqual(response.status_code, 200, response.text)
        response = client.post('/api/lmu/workspace/comparison', json={'filename': first,
            'reference_filename': second, 'reference_selection': 'MANUAL', 'target_lap': 1, 'reference_lap': 0})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['reference_filename'], second)
        self.assertIn('SURFACE_CONDITION_UNKNOWN', response.json()['personalReference']['conditions']['warnings'])
        with duckdb.connect(str(circuit)) as db:
            db.execute('CREATE TABLE "Maximum Path Wetness (%)" (ts DOUBLE, value DOUBLE)')
            db.execute('INSERT INTO "Maximum Path Wetness (%)" VALUES (0, 0)')
        dry = client.post('/api/lmu/telemetry/import?filename=dry.duckdb', content=circuit.read_bytes()).json()['file']['name']
        with duckdb.connect(str(circuit)) as db:
            db.execute('UPDATE "Maximum Path Wetness (%)" SET value=80')
        wet = client.post('/api/lmu/telemetry/import?filename=wet.duckdb', content=circuit.read_bytes()).json()['file']['name']
        response = client.post('/api/lmu/workspace/comparison', json={'filename': dry,
            'reference_filename': wet, 'reference_selection': 'MANUAL', 'target_lap': 1, 'reference_lap': 0})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn('DRY_WET_MISMATCH', response.json()['detail'])

    def test_same_filename_creates_two_copies_and_preserves_existing_bytes(self):
        first = self.upload().json()['file']['name']
        before = resolve(first).read_bytes()
        second = self.upload().json()['file']['name']
        self.assertNotEqual(first, second)
        self.assertEqual(resolve(first).read_bytes(), before)
        self.assertEqual(len(recordings()), 2)

    def test_bad_paths_extensions_empty_and_invalid_databases_leave_no_copies(self):
        for filename in ('../bad.duckdb', r'C:\bad.duckdb', 'bad.ibt', 'bad.duckdb.wal'):
            self.assertEqual(self.upload(filename).status_code, 422)
        for content in (b'', b'not a database'):
            self.assertEqual(self.upload(content=content).status_code, 422)
        self.assertEqual(list(import_dir().iterdir()), [])
        self.assertTrue(self.source.is_file())

    def test_disconnected_upload_cleans_only_its_temporary_copy(self):
        self.upload()
        existing = recordings()[0]
        async def stream():
            yield b'partial bytes'
            raise ClientDisconnect()
        with self.assertRaises(ClientDisconnect):
            asyncio.run(import_recording(SimpleNamespace(stream=stream), 'cut.duckdb', LmuTelemetryReader))
        self.assertEqual(list(import_dir().iterdir()), [existing])

    def test_dependency_failure_does_not_create_an_import(self):
        app = FastAPI()
        def missing():
            raise LmuTelemetryUnavailable('LMU 패키지를 설치하세요.')
        register_routes(app, LmuRoutesServices(dependency_check=missing))
        with TestClient(app) as client:
            response = client.post('/api/lmu/telemetry/import?filename=test.duckdb', content=b'bytes')
        self.assertEqual(response.status_code, 503)
        self.assertFalse(import_dir().exists())
