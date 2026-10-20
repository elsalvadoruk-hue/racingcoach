from contextlib import contextmanager
import hashlib
from pathlib import Path
import shutil
import tempfile
import threading
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from lmu_routes import LmuRoutesServices, register_routes, _file_id
from lmu_telemetry import LmuTelemetryReader, LmuTelemetryUnavailable
from test_lmu_telemetry import duckdb, make_comparison_file


@unittest.skipUnless(duckdb, 'LMU 전용 의존성이 없는 환경입니다.')
class VehicleComparisonRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / 'vehicle.duckdb'
        make_comparison_file(self.path)
        with duckdb.connect(str(self.path)) as con:
            con.execute("INSERT INTO metadata VALUES ('CarClass', 'GT3')")
            con.execute("INSERT INTO channelsList VALUES ('TyresPressure', 4, 'kPa')")
            con.execute('CREATE TABLE TyresPressure AS SELECT 100+i/10.0 AS value1, '
                        '101+i/10.0 AS value2,102+i/10.0 AS value3,103+i/10.0 AS value4 '
                        'FROM range(120) r(i)')
        self.params = {'reference_file_id': _file_id(self.path), 'reference_lap_number': 1,
                       'target_file_id': _file_id(self.path), 'target_lap_number': 2}
        self.route = '/api/lmu/telemetry/vehicle-comparison'

    def client(self, **kwargs):
        parent = self
        class WorkerReader(LmuTelemetryReader):
            @contextmanager
            def _connection(self):
                parent.assertNotIn('asyncio-portal', threading.current_thread().name)
                with super()._connection() as connection:
                    yield connection
        services = {'discover_dirs': lambda: (self.root,), 'reader_factory': WorkerReader,
                    'dependency_check': lambda: None}
        services.update(kwargs)
        app = FastAPI()
        register_routes(app, LmuRoutesServices(**services))
        client = TestClient(app)
        self.addCleanup(client.close)
        return client

    def test_real_worker_vector_comparison_preserves_source_and_privacy(self):
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        response = self.client().get(self.route, params=self.params)
        self.assertEqual(response.status_code, 200, response.text)
        report = response.json()['comparison']
        self.assertIn(report['status'], ('AVAILABLE', 'PARTIAL'))
        self.assertEqual(report['reference_status'], 'VALIDITY_UNVERIFIED')
        self.assertEqual(report['compatibility']['conditions'], 'NOT_COMPARED')
        self.assertEqual(report['channel']['wheel_order'], 'UNVERIFIED')
        self.assertEqual(len(report['channel']['components']), 4)
        first = report['channel']['components'][0]
        self.assertGreater(first['supported_distance_m'], 800)
        self.assertGreater(first['delta']['mean'], 3)
        self.assertLessEqual(len(first['points']), 301)
        for secret in ('PRIVATE_TEST_ID', 'PRIVATE_SETUP', str(self.root), 'unsafe'):
            self.assertNotIn(secret, response.text)
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertFalse(Path(str(self.path) + '.wal').exists())

    def test_cross_file_and_invalid_requests(self):
        second = self.root / 'target.duckdb'
        shutil.copyfile(self.path, second)
        client = self.client()
        response = client.get(self.route, params=self.params | {'target_file_id': _file_id(second)})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['sources']['target']['file_name'], second.name)
        for change in ({'reference_file_id': '../x.duckdb'}, {'points': 2}, {'points': 502},
                       {'reference_lap_number': -1}, {'channel_name': 'SteamID'},
                       {'channel_name': 'CarSetup'}, {'channel_name': 'TyresPressure;DROP TABLE metadata'},
                       {'target_lap_number': 1}):
            with self.subTest(change=change):
                self.assertEqual(client.get(self.route, params=self.params | change).status_code, 422)
        self.assertEqual(client.get(self.route, params=self.params | {'target_file_id': 'a' * 24}).status_code, 404)
        def unavailable():
            raise LmuTelemetryUnavailable('의존성 없음')
        self.assertEqual(self.client(dependency_check=unavailable).get(self.route, params=self.params).status_code, 503)

    def test_wal_only_change_discards_result(self):
        class ChangedReader(LmuTelemetryReader):
            @contextmanager
            def _connection(self):
                with super()._connection() as connection:
                    yield connection
                Path(str(self.path) + '.wal').write_bytes(b'pending synthetic write')
        response = self.client(reader_factory=ChangedReader).get(self.route, params=self.params)
        self.assertEqual(response.status_code, 422)

    def test_main_file_change_discards_computed_result(self):
        from unittest.mock import patch
        def changed(*args, **kwargs):
            with self.path.open('ab') as stream:
                stream.write(b'changed synthetic source')
            return {'status': 'AVAILABLE'}
        with patch('lmu_vehicle_compare.compare_vehicle_laps', side_effect=changed):
            response = self.client().get(self.route, params=self.params)
        self.assertEqual(response.status_code, 422)
        self.assertIn('파일이 변경', response.json()['detail'])


if __name__ == '__main__':
    unittest.main()
