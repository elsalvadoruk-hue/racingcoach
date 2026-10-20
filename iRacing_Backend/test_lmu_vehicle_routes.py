from contextlib import contextmanager
import hashlib
from pathlib import Path
import tempfile
import threading
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from lmu_routes import LmuRoutesServices, register_routes, _file_id
from lmu_telemetry import LmuTelemetryReader, LmuTelemetryUnavailable
from test_lmu_telemetry import duckdb, make_comparison_file


@unittest.skipUnless(duckdb, 'LMU 전용 의존성이 없는 환경입니다.')
class VehicleRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root/'vehicle.duckdb'
        make_comparison_file(self.path)
        with duckdb.connect(str(self.path)) as con:
            con.execute("INSERT INTO channelsList VALUES ('TyresPressure',4,'kPa')")
            con.execute('CREATE TABLE TyresPressure AS SELECT 100+i/10.0 AS value1, '
                        '101+i/10.0 AS value2,102+i/10.0 AS value3,103+i/10.0 AS value4 '
                        'FROM range(120) r(i)')
        self.params = {'file_id': _file_id(self.path), 'lap_number': 1}

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
        app = FastAPI(); register_routes(app, LmuRoutesServices(**services))
        client = TestClient(app); self.addCleanup(client.close)
        return client

    def test_vector_observations_thread_privacy_and_source_preservation(self):
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        response = self.client().get('/api/lmu/telemetry/vehicle-channels', params=self.params)
        self.assertEqual(response.status_code, 200, response.text)
        report = response.json()['vehicle']
        self.assertEqual(report['status'], 'AVAILABLE')
        self.assertEqual(report['reference_status'], 'VALIDITY_UNVERIFIED')
        self.assertEqual(report['channel']['wheel_order'], 'UNVERIFIED')
        self.assertEqual(len(report['channel']['components']), 4)
        first = report['channel']['components'][0]
        self.assertEqual(first['valid_samples'], 40)
        self.assertAlmostEqual(first['minimum'], 104.)
        self.assertAlmostEqual(first['maximum'], 107.9)
        self.assertAlmostEqual(first['mean'], 105.95)
        for secret in ('PRIVATE_TEST_ID', 'PRIVATE_SETUP', str(self.root), 'unsafe'):
            self.assertNotIn(secret, response.text)
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertFalse(Path(str(self.path)+'.wal').exists())

    def test_invalid_requests_missing_channel_and_dependency(self):
        client = self.client(); route = '/api/lmu/telemetry/vehicle-channels'
        for change in ({'file_id': '../x.duckdb'}, {'points': 2}, {'points': 502},
                       {'channel_name': 'SteamID'},
                       {'channel_name': 'CarSetup'}, {'channel_name': 'TyresPressure;DROP TABLE metadata'}):
            with self.subTest(change=change):
                self.assertEqual(client.get(route, params=self.params | change).status_code, 422)
        for number in (0, 99):
            response = client.get(route, params=self.params | {'lap_number': number})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['vehicle']['status'], 'UNAVAILABLE')
            self.assertTrue(response.json()['vehicle']['reason'])
        self.assertEqual(client.get(route, params=self.params | {'file_id': 'a'*24}).status_code, 404)
        missing = client.get(route, params=self.params | {'channel_name': 'Fuel Level'})
        self.assertEqual(missing.status_code, 200)
        self.assertEqual(missing.json()['vehicle']['status'], 'UNAVAILABLE')
        def unavailable(): raise LmuTelemetryUnavailable('의존성 없음')
        self.assertEqual(self.client(dependency_check=unavailable).get(route, params=self.params).status_code, 503)

    def test_wal_only_change_discards_result(self):
        class ChangedReader(LmuTelemetryReader):
            @contextmanager
            def _connection(self):
                with super()._connection() as connection:
                    yield connection
                Path(str(self.path)+'.wal').write_bytes(b'pending synthetic write')
        response = self.client(reader_factory=ChangedReader).get('/api/lmu/telemetry/vehicle-channels', params=self.params)
        self.assertEqual(response.status_code, 422)
        self.assertIn('파일이 변경', response.json()['detail'])


if __name__ == '__main__':
    unittest.main()
