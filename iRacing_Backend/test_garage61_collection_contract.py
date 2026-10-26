"""Exercise the real worker/status boundary using temporary files and local HTTP."""
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
import garage61_api_collector as worker
import garage61_crawler as browser_worker
from garage61_collector_manager import Garage61CollectorManager, _atomic_json_write
from reference_conditions import telemetry_reference_conditions, reference_condition_similarity


class CollectionContractTests(unittest.TestCase):
    def setUp(self):
        temp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(temp)
        self.manager = Garage61CollectorManager(self.root, self.root / 'refs', self.root / 'jobs')
        self.request = {'job_id': 'contract-job', 'filename': 'drive.ibt', 'context_key': 'car-track',
                        'created_at': 123., 'target_count': 8, 'car_name': 'Test Car',
                        'track_name': 'Test Track', 'car_platform_id': 10, 'track_platform_id': 20}
        self.paths = self.manager._paths(self.request['job_id'])
        _atomic_json_write(self.paths['request'], self.request)
        _atomic_json_write(self.paths['status'], {**self.request, 'state': 'QUEUED', 'progress': 0.})
        self.enterContext(redirect_stdout(io.StringIO()))

    def test_both_workers_preserve_identity_through_progress_and_completion(self):
        for writer in (worker.StatusWriter, browser_worker.StatusWriter):
            with self.subTest(worker=writer.__module__):
                _atomic_json_write(self.paths['status'], {**self.request, 'state': 'QUEUED'})
                status = writer(self.paths['status'], self.request['job_id'])
                for state in ('AUTHENTICATING', 'DOWNLOADING', 'COMPLETE'):
                    status.update(state, 'synthetic progress', .5)
                    raw = worker.load_json(self.paths['status'])
                    for key in ('filename', 'context_key', 'created_at', 'target_count'):
                        self.assertEqual(raw.get(key), self.request[key])

    def test_manager_repairs_legacy_status_from_its_own_request_only(self):
        _atomic_json_write(self.paths['status'], {'job_id': 'contract-job', 'state': 'COMPLETE',
                                                 'filename': 'wrong.ibt', 'downloaded_count': 8})
        result = self.manager.status('contract-job')
        self.assertEqual(result.get('filename'), 'drive.ibt')
        self.assertEqual(result.get('context_key'), 'car-track')
        self.assertEqual(result['downloaded_count'], 8)
        _atomic_json_write(self.paths['request'], {**self.request, 'job_id': 'another-job'})
        self.assertIsNone(self.manager.status('contract-job').get('context_key'))

    def test_sdk_tire_index_is_unknown_against_garage61_codes_even_when_equal(self):
        telemetry = SimpleNamespace(channels={'PlayerTireCompound': np.zeros(3)}, metadata={})
        current = telemetry_reference_conditions(telemetry)
        self.assertEqual(current['tire_compound'], 0)  # Retain the local SDK value.
        for code in (0, 1, 2):
            with self.subTest(code=code):
                reference = {'tire_compound': code, 'source_type': 'GARAGE61_API'}
                similarity = reference_condition_similarity(reference, current)
                self.assertTrue(similarity['setup_compatible'])
                self.assertEqual(similarity['available_components'], 0)
                self.assertFalse(similarity['tire_compound_comparable'])
                self.assertEqual(worker.condition_score(reference, current)[:2], (.5, 0.))

    def test_known_garage61_tire_mismatch_still_excludes_setup_comparison(self):
        result = reference_condition_similarity({'tire_compound': 1}, {'tire_compound': 2})
        self.assertFalse(result['setup_compatible'])

    def test_real_http_worker_download_reuse_and_partial_contract(self):
        csv_data = ('Speed,LapDistPct,Brake,Throttle,SteeringWheelAngle\n' +
                    ''.join(f'45,{i / 499},0.2,0.6,0.1\n' for i in range(500))).encode()
        car = {'id': 1, 'platform_id': 10, 'name': 'Test Car'}
        track = {'id': 2, 'platform_id': 20, 'name': 'Test Track'}
        laps = [{'id': f'LAP{i}', 'driver': {'slug': f'driver{i}'}, 'car': car, 'track': track,
                 'lapTime': 90. + i, 'clean': True, 'canViewTelemetry': True, 'tireCompound': 1}
                for i in range(3)]
        calls = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                path = self.path.split('?')[0].removeprefix('/api/v1/')
                calls.append(path)
                payload = {'me': {'apiPermissions': ['driving_data']}, 'cars': {'items': [car]},
                           'tracks': {'items': [track]}, 'laps': {'items': laps, 'total': len(laps)}}
                raw = csv_data if path.endswith('/csv') else json.dumps(payload[path]).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        self.addCleanup(setattr, worker, '_active_status', worker._active_status)
        with patch.object(worker, 'API_BASE', f'http://127.0.0.1:{server.server_port}/api/v1'), \
                patch.dict('os.environ', GARAGE61_API_TOKEN='synthetic-test-token'):
            for repeat in range(2):
                self.assertEqual(worker.run(self.paths['request'], self.paths['status'], self.root / 'refs', None), 0)
                raw = worker.load_json(self.paths['status'])
                result = self.manager.status('contract-job')
                self.assertEqual(raw.get('filename'), 'drive.ibt')
                self.assertEqual(raw.get('context_key'), 'car-track')
                self.assertEqual(result['state'], 'PARTIAL')  # Three of the requested eight.
                self.assertEqual(result['context_csv_count'], 3)
                self.assertEqual(result['downloaded_count'], 0 if repeat else 3)
                self.assertEqual(result['cached_csv_count'], 3 if repeat else 0)
            self.assertEqual(sum(path.endswith('/csv') for path in calls), 3)
            scan = worker.load_json(self.root / 'refs/garage61_catalog.json')['context_scans']['car-track']
            self.assertTrue(scan['partial'])


if __name__ == '__main__':
    unittest.main()
