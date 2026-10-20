"""Inspection discards changing files; discovery never opens native databases."""
import hashlib
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lmu_routes import LmuRoutesServices, _file_id, register_routes


class FileSnapshotTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.path = self.root / 'synthetic.duckdb'
        self.path.write_bytes(b'synthetic native file')
        self.wal = Path(str(self.path) + '.wal')
        self.summary = SimpleNamespace(metadata={}, channels=(), events=(), sample_counts={}, laps=())

    def client(self, operation=None, dependency_check=lambda: None):
        def summary():
            self.assertNotIn('asyncio-portal', threading.current_thread().name)
            if operation is not None:
                operation()
            return self.summary
        factory = Mock(return_value=SimpleNamespace(summary=summary))
        app = FastAPI()
        register_routes(app, LmuRoutesServices(discover_dirs=lambda: (self.root,),
            list_files=lambda _: (self.path,), dependency_check=dependency_check,
            reader_factory=factory))
        client = TestClient(app)
        self.addCleanup(client.close)
        return client, factory

    def inspect(self, client):
        return client.get('/api/lmu/telemetry/file', params={'file_id': _file_id(self.path)})

    def signature(self, path):
        stat = path.stat()
        return stat.st_size, stat.st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest()

    def test_listing_reports_wal_presence_without_database_or_dependency_access(self):
        def forbidden():
            raise AssertionError('listing must not require DuckDB')
        client, factory = self.client(dependency_check=forbidden)
        for present in (False, True):
            if present:
                self.wal.write_bytes(b'pending writes')
            before = self.signature(self.path)
            response = client.get('/api/lmu/telemetry/files')
            self.assertEqual(response.status_code, 200, response.text)
            info, = response.json()['files']
            self.assertIs(info['wal_present'], present)
            self.assertEqual(info['snapshot_status'], 'WAL_PRESENT' if present else 'UNVERIFIED')
            self.assertNotIn(str(self.root), response.text)
            self.assertEqual(self.signature(self.path), before)
            factory.assert_not_called()

    def test_stable_inspection_preserves_main_and_wal_without_claiming_completion(self):
        client, factory = self.client()
        for present in (False, True):
            if present:
                self.wal.write_bytes(b'unchanged synthetic WAL')
            before = {p: self.signature(p) for p in (self.path, self.wal) if p.exists()}
            response = self.inspect(client)
            self.assertEqual(response.status_code, 200, response.text)
            info = response.json()['file']
            self.assertEqual(info['status'], 'INSPECTED')
            self.assertEqual(info['snapshot_status'], 'WAL_PRESENT' if present else 'UNVERIFIED')
            self.assertEqual(info['reference_status'], 'VALIDITY_UNVERIFIED')
            self.assertIsNone(info['best_lap_time_s'])
            self.assertEqual(before, {p: self.signature(p) for p in before})
            self.assertNotIn(str(self.root), response.text)
        self.assertEqual(factory.call_count, 2)

    def test_main_or_wal_change_discards_summary(self):
        def same_size_update(path):
            before = path.stat()
            path.write_bytes(b'X' * before.st_size)
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 10_000_000))
        operations = {
            'main_growth': lambda: self.path.write_bytes(b'updated and larger synthetic native file'),
            'main_same_size': lambda: same_size_update(self.path),
            'wal_appears': lambda: self.wal.write_bytes(b'new pending writes'),
            'wal_growth': lambda: self.wal.write_bytes(b'updated and larger synthetic WAL'),
            'wal_same_size': lambda: same_size_update(self.wal),
            'wal_disappears': lambda: self.wal.unlink(),
        }
        for name, operation in operations.items():
            with self.subTest(change=name):
                self.path.write_bytes(b'synthetic native file')
                if self.wal.exists():
                    self.wal.unlink()
                if name in ('wal_growth', 'wal_same_size', 'wal_disappears'):
                    self.wal.write_bytes(b'pending writes')
                client, _ = self.client(operation)
                response = self.inspect(client)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertIn('변경', response.json()['detail'])
                self.assertNotIn('file', response.json())
                self.assertNotIn(str(self.root), response.text)

    def test_inspection_os_errors_never_expose_local_paths(self):
        for error in (PermissionError(13, 'access denied', str(self.path)),
                      OSError(5, 'input/output error', str(self.path))):
            def fail(error=error):
                raise error
            with self.subTest(error=type(error).__name__):
                client, _ = self.client(fail)
                response = self.inspect(client)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertIn('접근할 수 없습니다', response.json()['detail'])
                self.assertNotIn(str(self.root), response.text)
                self.assertNotIn(self.path.name, response.text)

    def test_fingerprint_os_error_is_sanitized_and_disappearance_is_404(self):
        client, factory = self.client()
        with patch('lmu_routes._file_fingerprint', side_effect=PermissionError(13, 'denied', str(self.path))):
            response = self.inspect(client)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertNotIn(str(self.root), response.text)
        factory.assert_not_called()
        self.path.unlink()
        response = self.inspect(client)
        self.assertEqual(response.status_code, 404, response.text)
        self.assertNotIn(str(self.root), response.text)


if __name__ == '__main__':
    unittest.main()
