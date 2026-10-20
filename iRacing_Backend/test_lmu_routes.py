from pathlib import Path
import asyncio
from contextlib import asynccontextmanager
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx

from lmu_api import create_lmu_application
from lmu_routes import LmuRoutesServices, register_routes
from lmu_telemetry import LmuTelemetryReader, LmuTelemetryUnavailable
from test_lmu_telemetry import duckdb, make_comparison_file, make_lmu_file
from test_lmu_analysis import make_analysis_file


class FakeLiveService:
    def __init__(self, available=True):
        self.available = available
        self.collecting = False
        self.calls = []

    def _record(self, operation):
        if 'asyncio-portal' in threading.current_thread().name:
            raise AssertionError('Live operations must run outside the event loop')
        self.calls.append(operation)

    def status(self):
        self._record('status')
        return {'provider': 'LMU_SHARED_MEMORY',
                'state': 'OBSERVING' if self.collecting else 'STOPPED' if self.available else 'UNAVAILABLE',
                'collecting': self.collecting, 'valid': None}

    def start(self):
        self._record('start')
        self.collecting = self.available
        return self.status()

    def stop(self):
        self._record('stop')
        self.collecting = False
        return self.status()


class LmuRouteTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)

    def client(self, **overrides):
        services = {'discover_dirs': lambda: (self.root,), 'dependency_check': lambda: None,
                    'live_service': FakeLiveService()}
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

    def test_live_status_never_starts_collection_and_runs_in_thread(self):
        service = FakeLiveService()
        client = self.client(live_service=service)
        self.assertFalse(client.get('/api/lmu/live/status').json()['collecting'])
        overview = client.get('/api/lmu/status').json()
        self.assertFalse(overview['live']['collecting'])
        self.assertIn('LIVE_DIAGNOSTICS_CONTROL', overview['capabilities'])
        self.assertEqual(service.calls, ['status', 'status'])
        self.assertEqual(client.get('/api/lmu/live/start').status_code, 405)
        self.assertEqual(client.get('/api/lmu/live/stop').status_code, 405)
        self.assertEqual(service.calls, ['status', 'status'])

    def test_live_explicit_start_stop_idempotence_and_thread_isolation(self):
        service = FakeLiveService()
        client = self.client(live_service=service)
        for _ in range(2):
            payload = client.post('/api/lmu/live/start').json()
            self.assertTrue(payload['collecting'])
            self.assertIsNone(payload['valid'])
        self.assertTrue(client.get('/api/lmu/live/status').json()['collecting'])
        for _ in range(2):
            self.assertFalse(client.post('/api/lmu/live/stop').json()['collecting'])
        self.assertEqual(service.calls.count('start'), 2)
        self.assertEqual(service.calls.count('stop'), 2)

    def test_live_unavailable_start_returns_diagnostics(self):
        service = FakeLiveService(available=False)
        client = self.client(live_service=service)
        response = client.post('/api/lmu/live/start')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['state'], 'UNAVAILABLE')
        self.assertFalse(response.json()['collecting'])
        self.assertIsNone(response.json()['valid'])
        self.assertNotIn(str(self.root), response.text)
        self.assertNotIn('SteamID', response.text)
        self.assertNotIn('setup', response.text)

    def test_live_injected_apps_are_isolated_and_stop_at_shutdown(self):
        first, second = FakeLiveService(), FakeLiveService()
        app1, app2 = FastAPI(), FastAPI()
        register_routes(app1, LmuRoutesServices(live_service=first))
        register_routes(app2, LmuRoutesServices(live_service=second))
        with TestClient(app1) as client1, TestClient(app2) as client2:
            self.assertEqual(first.calls, [])
            self.assertEqual(second.calls, [])
            self.assertTrue(client1.post('/api/lmu/live/start').json()['collecting'])
            self.assertFalse(client2.get('/api/lmu/live/status').json()['collecting'])
            self.assertNotIn('start', second.calls)
        self.assertFalse(first.collecting)
        self.assertFalse(second.collecting)
        self.assertEqual(first.calls.count('stop'), 1)
        self.assertEqual(second.calls.count('stop'), 1)

    def test_live_shutdown_preserves_existing_lifespan(self):
        events = []

        @asynccontextmanager
        async def lifespan(app):
            events.append('enter')
            try:
                yield {'existing_resource': 'preserved'}
            finally:
                events.append('exit')

        service = FakeLiveService()
        app = FastAPI(lifespan=lifespan)
        register_routes(app, LmuRoutesServices(live_service=service))
        with TestClient(app) as client:
            self.assertEqual(events, ['enter'])
            self.assertEqual(service.calls, [])
            client.post('/api/lmu/live/start')
        self.assertEqual(events, ['enter', 'exit'])
        self.assertEqual(service.calls[-2:], ['stop', 'status'])

    def test_live_default_service_is_lazy_and_resolved_outside_event_loop(self):
        service = FakeLiveService()
        calls = []

        def factory():
            self.assertNotIn('asyncio-portal', threading.current_thread().name)
            calls.append('resolve')
            return service

        app = FastAPI()
        with patch('lmu_live.get_live_service', side_effect=factory, create=True):
            register_routes(app, LmuRoutesServices(discover_dirs=lambda: (),
                                                 list_files=lambda dirs: []))
            with TestClient(app) as client:
                self.assertEqual(calls, [])
                self.assertEqual(client.get('/api/lmu/telemetry/files').json()['files'], [])
                self.assertEqual(calls, [])
                self.assertFalse(client.get('/api/lmu/live/status').json()['collecting'])
                self.assertFalse(client.get('/api/lmu/live/status').json()['collecting'])
                self.assertEqual(calls, ['resolve'])
                self.assertNotIn('start', service.calls)
            self.assertEqual(service.calls[-2:], ['stop', 'status'])

    def test_unused_live_default_service_is_not_resolved_on_shutdown(self):
        app = FastAPI()
        with patch('lmu_live.get_live_service', create=True) as factory:
            register_routes(app)
            with TestClient(app):
                factory.assert_not_called()
            factory.assert_not_called()

    def test_live_concurrent_default_initialization_creates_one_service(self):
        created = []

        def factory():
            # Release the GIL while constructing so simultaneous requests would
            # expose a missing initialization lock.
            time.sleep(0.03)
            service = FakeLiveService()
            created.append(service)
            return service

        app = FastAPI()

        async def requests():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url='http://test') as client:
                responses = await asyncio.gather(*[
                    client.get('/api/lmu/live/status') for _ in range(8)])
                self.assertTrue(all(response.status_code == 200 for response in responses))

        with patch('lmu_live.get_live_service', side_effect=factory, create=True):
            register_routes(app)
            asyncio.run(requests())
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].calls, ['status'] * 8)

    def test_live_default_factory_creates_separate_services_for_separate_apps(self):
        created = []

        def factory():
            service = FakeLiveService()
            created.append(service)
            return service

        first, second = FastAPI(), FastAPI()
        with patch('lmu_live.get_live_service', side_effect=factory, create=True):
            register_routes(first)
            register_routes(second)
            with TestClient(first) as client1, TestClient(second) as client2:
                self.assertTrue(client1.post('/api/lmu/live/start').json()['collecting'])
                self.assertFalse(client2.get('/api/lmu/live/status').json()['collecting'])
                self.assertEqual(len(created), 2)
                self.assertIsNot(created[0], created[1])
            self.assertTrue(all(not service.collecting for service in created))

    @unittest.skipUnless(duckdb, 'LMU 전용 의존성을 설치하지 않은 환경입니다.')
    def test_manual_comparison_and_validation(self):
        make_comparison_file(self.root / 'comparison.duckdb')
        client = self.client()
        file_id = client.get('/api/lmu/telemetry/files').json()['files'][0]['file_id']
        params = {'file_id': file_id, 'reference_lap': 1, 'target_lap': 2, 'points': 201}
        response = client.get('/api/lmu/telemetry/compare', params=params)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['reference_status'], 'VALIDITY_UNVERIFIED')
        self.assertEqual(payload['comparison']['delta_time_s'], -1)
        self.assertEqual(len(payload['comparison']['points']), 201)
        self.assertEqual(payload['comparison']['sector_length_m'], 500)
        self.assertEqual(len(payload['comparison']['sectors']), 2)
        self.assertAlmostEqual(sum(s['delta_time_s'] for s in payload['comparison']['sectors']),
                               payload['comparison']['common_delta_time_s'])
        smaller = client.get('/api/lmu/telemetry/compare', params=params | {'sector_length_m': 250}).json()['comparison']
        self.assertEqual(len(smaller['sectors']), 4)
        self.assertNotIn('PRIVATE_TEST_ID', str(payload))
        self.assertNotIn(str(self.root), str(payload))
        self.assertEqual(client.get('/api/lmu/status').json()['analysis'], 'MANUAL_LAP_COMPARISON')
        for override in ({'reference_lap': 0}, {'target_lap': 1}, {'target_lap': 99},
                         {'points': 1}, {'points': 502}, {'points': '2.5'},
                         {'sector_length_m': 99}, {'sector_length_m': 2001}, {'sector_length_m': '250.5'},
                         {'file_id': '../session.duckdb'}, {'reference_lap': -1}):
            with self.subTest(override=override):
                self.assertEqual(client.get('/api/lmu/telemetry/compare', params=params | override).status_code, 422)
        self.assertEqual(client.get('/api/lmu/telemetry/compare', params=params | {'file_id': 'a'*24}).status_code, 404)
        self.assertEqual(client.get('/api/lmu/telemetry/compare', params={'file_id': file_id}).status_code, 422)

    def test_comparison_requires_dependency_and_runs_outside_event_loop(self):
        def missing():
            raise LmuTelemetryUnavailable('패키지 없음')
        params = {'file_id': 'a'*24, 'reference_lap': 1, 'target_lap': 2}
        self.assertEqual(self.client(dependency_check=missing).get('/api/lmu/telemetry/compare', params=params).status_code, 503)
        def list_files(_dirs):
            self.assertNotIn('asyncio-portal', threading.current_thread().name)
            return []
        self.assertEqual(self.client(list_files=list_files).get('/api/lmu/telemetry/compare', params=params).status_code, 404)

    def comparison_files(self, client):
        for name in ('old.duckdb','new.duckdb'):
            path = self.root/name
            make_comparison_file(path)
            with duckdb.connect(str(path)) as con:
                con.execute("INSERT INTO metadata VALUES ('CarClass','GT3')")
        files = {f['file_name']:f['file_id'] for f in client.get('/api/lmu/telemetry/files').json()['files']}
        return {'reference_file_id':files['old.duckdb'],'target_file_id':files['new.duckdb'],
                'reference_lap':1,'target_lap':2}

    @unittest.skipUnless(duckdb, 'LMU 전용 의존성을 설치하지 않은 환경입니다.')
    def test_file_comparison_sources_validation_and_private_fields(self):
        client = self.client()
        params = self.comparison_files(client)
        response = client.get('/api/lmu/telemetry/compare-files',params=params)
        self.assertEqual(response.status_code,200)
        result = response.json()
        self.assertEqual(result['sources']['reference']['file_name'],'old.duckdb')
        self.assertEqual(result['sources']['target']['file_name'],'new.duckdb')
        self.assertEqual(result['comparison']['delta_time_s'],-1)
        self.assertEqual(result['comparison']['compatibility']['status'],'METADATA_MATCH_ONLY')
        self.assertEqual(result['comparison']['corner_comparison']['status'], 'UNAVAILABLE')
        self.assertTrue(result['comparison']['corner_comparison']['reason'])
        for value in ('PRIVATE_SETUP','PRIVATE_TEST_ID',str(self.root),'unsafe'):
            self.assertNotIn(value,response.text)
        for override in ({'reference_file_id':'../file.duckdb'},{'reference_lap':0},{'target_lap':99},
                         {'points':2},{'points':502},{'sector_length_m':99},
                         {'target_file_id':params['reference_file_id'],'target_lap':1}):
            with self.subTest(override=override):
                self.assertEqual(client.get('/api/lmu/telemetry/compare-files',params=params|override).status_code,422)
        self.assertEqual(client.get('/api/lmu/telemetry/compare-files',params=params|{'target_file_id':'a'*24}).status_code,404)
        self.assertEqual(client.get('/api/lmu/telemetry/compare-files',params=params|{'target_file_id':params['reference_file_id']}).status_code,200)
        with duckdb.connect(str(self.root/'new.duckdb')) as con:
            con.execute("UPDATE metadata SET value='Other' WHERE key='TrackLayout'")
        self.assertEqual(client.get('/api/lmu/telemetry/compare-files',params=params).status_code,422)

    @unittest.skipUnless(duckdb, 'LMU 전용 의존성을 설치하지 않은 환경입니다.')
    def test_corner_consistency_selection_privacy_and_unsupported_geometry(self):
        client = self.client()
        params = self.comparison_files(client)
        selections = [{'file_id': key, 'lap_number': number}
                      for key, number in ((params['reference_file_id'], 1),
                                          (params['reference_file_id'], 2),
                                          (params['target_file_id'], 1))]
        route = '/api/lmu/telemetry/corner-consistency'
        response = client.post(route, json={'selections': selections})
        self.assertEqual(response.status_code, 200)
        report = response.json()['consistency']
        self.assertEqual(report['status'], 'UNAVAILABLE')
        self.assertEqual(report['lap_count'], 3)
        self.assertEqual([s['selection_index'] for s in report['sources']], [0, 1, 2])
        self.assertIsNone(report['automatic_best_lap'])
        for value in ('PRIVATE_SETUP', 'PRIVATE_TEST_ID', str(self.root), 'unsafe'):
            self.assertNotIn(value, response.text)
        for selected in (selections[:2], selections*3, [selections[0]]*3,
                         selections[:2]+[{'file_id': '../x', 'lap_number': 1}],
                         selections[:2]+[{'file_id': selections[2]['file_id'], 'lap_number': True}],
                         selections[:2]+[{'file_id': selections[2]['file_id'], 'lap_number': 0}]):
            with self.subTest(selected=selected):
                self.assertEqual(client.post(route, json={'selections': selected}).status_code, 422)
        missing = selections[:2]+[{'file_id': 'a'*24, 'lap_number': 1}]
        self.assertEqual(client.post(route, json={'selections': missing}).status_code, 404)
        with duckdb.connect(str(self.root/'new.duckdb')) as con:
            con.execute("UPDATE metadata SET value='Other' WHERE key='TrackLayout'")
        self.assertEqual(client.post(route, json={'selections': selections}).status_code, 422)

    @unittest.skipUnless(duckdb, 'LMU 전용 의존성을 설치하지 않은 환경입니다.')
    def test_corner_consistency_file_change_and_worker_isolation(self):
        parent = self
        class ChangedReader(LmuTelemetryReader):
            def analysis_lap(self, number):
                parent.assertNotIn('asyncio-portal', threading.current_thread().name)
                lap = super().analysis_lap(number)
                stat = self.path.stat()
                os.utime(self.path, ns=(stat.st_atime_ns, stat.st_mtime_ns+1000000000))
                return lap
        client = self.client(reader_factory=ChangedReader)
        params = self.comparison_files(client)
        selections = [{'file_id': key, 'lap_number': number} for key, number in (
            (params['reference_file_id'], 1), (params['reference_file_id'], 2), (params['target_file_id'], 1))]
        response = client.post('/api/lmu/telemetry/corner-consistency', json={'selections': selections})
        self.assertEqual(response.status_code, 422)
        self.assertIn('파일이 변경', response.json()['detail'])

    @unittest.skipUnless(duckdb, 'LMU 전용 의존성을 설치하지 않은 환경입니다.')
    def test_corner_consistency_wal_only_change_is_rejected(self):
        count = [0]
        class WalChangedReader(LmuTelemetryReader):
            def analysis_lap(self, number):
                lap = super().analysis_lap(number)
                count[0] += 1
                if count[0] == 3:
                    Path(str(self.path)+'.wal').write_bytes(b'synthetic pending write')
                return lap
        client = self.client(reader_factory=WalChangedReader)
        params = self.comparison_files(client)
        paths = [self.root/'old.duckdb', self.root/'new.duckdb']
        before = [(p.stat().st_size, p.stat().st_mtime_ns) for p in paths]
        selections = [{'file_id': key, 'lap_number': number} for key, number in (
            (params['reference_file_id'], 1), (params['reference_file_id'], 2), (params['target_file_id'], 1))]
        response = client.post('/api/lmu/telemetry/corner-consistency', json={'selections': selections})
        self.assertEqual(response.status_code, 422)
        self.assertIn('파일이 변경', response.json()['detail'])
        self.assertEqual([(p.stat().st_size, p.stat().st_mtime_ns) for p in paths], before)

    def test_file_comparison_dependency_and_thread_isolation(self):
        def missing():
            raise LmuTelemetryUnavailable('패키지 없음')
        params = {'reference_file_id':'a'*24,'target_file_id':'b'*24,'reference_lap':1,'target_lap':2}
        self.assertEqual(self.client(dependency_check=missing).get('/api/lmu/telemetry/compare-files',params=params).status_code,503)
        def list_files(_dirs):
            self.assertNotIn('asyncio-portal',threading.current_thread().name)
            return []
        self.assertEqual(self.client(list_files=list_files).get('/api/lmu/telemetry/compare-files',params=params).status_code,404)

    @unittest.skipUnless(duckdb, 'LMU 전용 의존성을 설치하지 않은 환경입니다.')
    def test_replay_manual_selection_validation_privacy_and_no_automatic_best(self):
        path=self.root/'replay.duckdb'
        make_analysis_file(path)
        with duckdb.connect(str(path)) as con:
            con.execute("INSERT INTO metadata VALUES ('CarClass','GT3')")
        client=self.client()
        key=client.get('/api/lmu/telemetry/files').json()['files'][0]['file_id']
        selections=[{'file_id':key,'lap_number':i} for i in (1,2)]
        body={'selections':selections,'points':101}
        response=client.post('/api/lmu/telemetry/replay',json=body)
        self.assertEqual(response.status_code,200)
        payload=response.json()['replay']
        self.assertEqual(payload['frame_count'],101)
        self.assertEqual(len(payload['sources']),2)
        self.assertEqual(payload['status'],'OFFLINE_DIAGNOSTIC_REPLAY')
        self.assertTrue(all(path['valid'] is None for path in payload['paths']))
        for value in ('PRIVATE_SETUP','PRIVATE_TEST_ID',str(self.root),'unsafe'):
            self.assertNotIn(value,response.text)
        for override in ({'selections':selections[:1]},{'selections':selections*4},
                         {'selections':[selections[0],selections[0]]},
                         {'points':2},{'points':1002},{'points':True},
                         {'selections':[selections[0],{'file_id':'../file.duckdb','lap_number':2}]},
                         {'selections':[selections[0],{'file_id':key,'lap_number':True}]},
                         {'selections':[selections[0],{'file_id':key,'lap_number':0}]}):
            with self.subTest(override=override):
                self.assertEqual(client.post('/api/lmu/telemetry/replay',json=body|override).status_code,422)
        unknown={'selections':[selections[0],{'file_id':'a'*24,'lap_number':1}]}
        self.assertEqual(client.post('/api/lmu/telemetry/replay',json=unknown).status_code,404)

    def test_replay_dependency_and_thread_isolation(self):
        body={'selections':[{'file_id':'a'*24,'lap_number':i} for i in (1,2)]}
        def missing():
            raise LmuTelemetryUnavailable('패키지 없음')
        self.assertEqual(self.client(dependency_check=missing).post('/api/lmu/telemetry/replay',json=body).status_code,503)
        def listing(_dirs):
            self.assertNotIn('asyncio-portal',threading.current_thread().name)
            return []
        self.assertEqual(self.client(list_files=listing).post('/api/lmu/telemetry/replay',json=body).status_code,404)

    @unittest.skipUnless(duckdb, 'LMU 전용 의존성을 설치하지 않은 환경입니다.')
    def test_replay_file_change_rejected(self):
        path=self.root/'replay.duckdb'
        make_analysis_file(path)
        with duckdb.connect(str(path)) as con:
            con.execute("INSERT INTO metadata VALUES ('CarClass','GT3')")
        class ChangedReader(LmuTelemetryReader):
            def analysis_lap(self,number):
                lap=super().analysis_lap(number)
                stat=self.path.stat()
                os.utime(self.path,ns=(stat.st_atime_ns,stat.st_mtime_ns+1000000000))
                return lap
        client=self.client(reader_factory=ChangedReader)
        key=client.get('/api/lmu/telemetry/files').json()['files'][0]['file_id']
        response=client.post('/api/lmu/telemetry/replay',json={'selections':[{'file_id':key,'lap_number':i} for i in (1,2)]})
        self.assertEqual(response.status_code,422)
        self.assertIn('파일이 변경',response.json()['detail'])

    @unittest.skipUnless(duckdb, 'LMU 전용 의존성을 설치하지 않은 환경입니다.')
    def test_file_changed_during_comparison_is_rejected(self):
        class ChangedReader(LmuTelemetryReader):
            def analysis_lap(self, number):
                self_test.assertNotIn('asyncio-portal',threading.current_thread().name)
                lap = super().analysis_lap(number)
                stat=self.path.stat()
                os.utime(self.path,ns=(stat.st_atime_ns,stat.st_mtime_ns+1000000000))
                return lap
        self_test=self
        client=self.client(reader_factory=ChangedReader)
        params=self.comparison_files(client)
        response=client.get('/api/lmu/telemetry/compare-files',params=params)
        self.assertEqual(response.status_code,422)
        self.assertIn('파일이 변경',response.json()['detail'])

    @unittest.skipUnless(duckdb, 'LMU 전용 의존성을 설치하지 않은 환경입니다.')
    def test_lap_analysis_route_validation_privacy_and_missing_map(self):
        make_comparison_file(self.root / 'comparison.duckdb')
        client = self.client()
        file_id = client.get('/api/lmu/telemetry/files').json()['files'][0]['file_id']
        params = {'file_id': file_id, 'lap_number': 1}
        response = client.get('/api/lmu/telemetry/lap-analysis', params=params)
        self.assertEqual(response.status_code, 200)
        result = response.json()['analysis']
        self.assertIsNone(result['valid'])
        self.assertEqual(result['reference_status'], 'VALIDITY_UNVERIFIED')
        self.assertEqual(result['track_path']['status'], 'UNAVAILABLE')
        self.assertNotIn('PRIVATE_SETUP', response.text)
        self.assertNotIn('PRIVATE_TEST_ID', response.text)
        self.assertNotIn(str(self.root), response.text)
        for override in ({'lap_number': 0}, {'lap_number': 99}, {'lap_number': -1},
                         {'points': 2}, {'points': 502}, {'file_id': '../file.duckdb'}):
            with self.subTest(override=override):
                self.assertEqual(client.get('/api/lmu/telemetry/lap-analysis', params=params|override).status_code, 422)
        self.assertEqual(client.get('/api/lmu/telemetry/lap-analysis', params=params|{'file_id':'a'*24}).status_code, 404)

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
            self.assertIn('두 랩 수동 비교', response.text)
            self.assertIn('베스트 랩 자동 선택', response.text)


if __name__ == '__main__':
    unittest.main()
