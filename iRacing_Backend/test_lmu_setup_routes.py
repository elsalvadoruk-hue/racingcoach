from pathlib import Path
import hashlib
import json
import tempfile
import threading
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from lmu_routes import LmuRoutesServices, register_routes, _file_id
from lmu_telemetry import LmuTelemetryReader, LmuTelemetryUnavailable
from test_lmu_telemetry import duckdb, make_lmu_file


class SetupRoutesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.paths = [self.root / name for name in ('reference.duckdb', 'target.duckdb')]
        for path in self.paths:
            path.write_bytes(b'synthetic read-only source')
        self.ids = [_file_id(path) for path in self.paths]

    def client(self, **overrides):
        options = dict(discover_dirs=lambda: (self.root,), reader_factory=lambda path: path,
                       dependency_check=lambda: None)
        options.update(overrides)
        app = FastAPI()
        register_routes(app, LmuRoutesServices(**options))
        client = TestClient(app)
        self.addCleanup(client.close)
        return client

    def test_summary_and_comparison_run_in_worker_and_preserve_sources(self):
        def report(path):
            self.assertNotIn('asyncio-portal', threading.current_thread().name)
            return {'status': 'AVAILABLE', 'items': [], 'scope': 'RECORDING_METADATA'}
        with patch('lmu_setup.setup_report', side_effect=report), patch(
                'lmu_setup.compare_setups', return_value={'status': 'AVAILABLE', 'rows': []}) as compare:
            client = self.client()
            summary = client.get('/api/lmu/telemetry/setup', params={'file_id': self.ids[0]})
            self.assertEqual(summary.status_code, 200, summary.text)
            self.assertEqual(summary.json()['setup']['scope'], 'RECORDING_METADATA')
            response = client.get('/api/lmu/telemetry/setup-comparison', params={
                'reference_file_id': self.ids[0], 'target_file_id': self.ids[1]})
            self.assertEqual(response.status_code, 200, response.text)
            compare.assert_called_once()
            self.assertNotIn(str(self.root), response.text)
        for path in self.paths:
            self.assertEqual(path.read_bytes(), b'synthetic read-only source')
            self.assertFalse(Path(str(path) + '.wal').exists())

    def test_invalid_unknown_and_same_file_requests(self):
        client = self.client()
        self.assertEqual(client.get('/api/lmu/telemetry/setup', params={'file_id': '../x'}).status_code, 422)
        self.assertEqual(client.get('/api/lmu/telemetry/setup', params={'file_id': 'f'*24}).status_code, 404)
        self.assertEqual(client.get('/api/lmu/telemetry/setup-comparison', params={
            'reference_file_id': self.ids[0], 'target_file_id': self.ids[0]}).status_code, 422)

    def test_main_and_wal_changes_discard_results(self):
        for comparison in (False, True):
            for wal in (False, True):
                with self.subTest(comparison=comparison, wal=wal):
                    def changed(path):
                        target = Path(str(path) + '.wal') if wal else path
                        target.write_bytes(b'changed source')
                        return {'status': 'AVAILABLE'}
                    with patch('lmu_setup.setup_report', side_effect=changed), patch(
                            'lmu_setup.compare_setups', return_value={'status': 'AVAILABLE'}):
                        route = '/api/lmu/telemetry/setup-comparison' if comparison else '/api/lmu/telemetry/setup'
                        params = {'reference_file_id': self.ids[0], 'target_file_id': self.ids[1]} if comparison else {'file_id': self.ids[0]}
                        self.assertEqual(self.client().get(route, params=params).status_code, 422)
                    for path in self.paths:
                        path.write_bytes(b'synthetic read-only source')
                        Path(str(path) + '.wal').unlink(missing_ok=True)

    def test_dependency_and_parser_errors(self):
        def missing():
            raise LmuTelemetryUnavailable('missing dependency')
        params = {'file_id': self.ids[0]}
        self.assertEqual(self.client(dependency_check=missing).get('/api/lmu/telemetry/setup', params=params).status_code, 503)
        with patch('lmu_setup.setup_report', side_effect=ValueError('unsupported metadata')):
            self.assertEqual(self.client().get('/api/lmu/telemetry/setup', params=params).status_code, 422)
        with patch('lmu_setup.setup_report', side_effect=PermissionError(str(self.paths[0]))):
            response = self.client().get('/api/lmu/telemetry/setup', params=params)
            self.assertEqual(response.status_code, 422)
            self.assertNotIn(str(self.root), response.text)

    @unittest.skipUnless(duckdb, 'LMU dependency unavailable')
    def test_real_duckdb_reports_only_safe_fields_without_modifying_recordings(self):
        before = []
        for index, path in enumerate(self.paths):
            path.unlink()
            make_lmu_file(path)
            raw = {'WM_COMPOUND-W_FL': {'available': True, 'value': index,
                    'stringValue': 'Medium', 'minValue': 0, 'maxValue': 2},
                   'arbitrary_private': 'PRIVATE_SETUP'}
            with duckdb.connect(str(path)) as con:
                con.execute('UPDATE metadata SET value=? WHERE key=?', [json.dumps(raw), 'CarSetup'])
            before.append(hashlib.sha256(path.read_bytes()).hexdigest())
        client = self.client(reader_factory=LmuTelemetryReader)
        response = client.get('/api/lmu/telemetry/setup', params={'file_id': self.ids[0]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['setup']['items'][0]['stringValue'], 'Medium')
        response = client.get('/api/lmu/telemetry/setup-comparison', params={
            'reference_file_id': self.ids[0], 'target_file_id': self.ids[1]})
        self.assertEqual(response.status_code, 200, response.text)
        row = response.json()['comparison']['rows'][0]
        self.assertEqual(row['state'], 'UNKNOWN')
        self.assertEqual(row['reason'], 'VALUE_CHANGED_WITH_SAME_DISPLAY')
        for secret in ('PRIVATE_TEST_ID', 'PRIVATE_SETUP', str(self.root), 'arbitrary_private'):
            self.assertNotIn(secret, response.text)
        self.assertEqual(before, [hashlib.sha256(path.read_bytes()).hexdigest() for path in self.paths])
        self.assertFalse(any(Path(str(path) + '.wal').exists() for path in self.paths))


if __name__ == '__main__':
    unittest.main()
