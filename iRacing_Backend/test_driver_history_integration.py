"""History capture and read API contracts; only synthetic files and temporary stores."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from analysis_numeric import file_signature
from driver_history_index import index_telemetry
from driver_history_routes import register_routes


class HistoryCaptureTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)
        self.path = self.root / 'recording.ibt'
        self.path.write_bytes(b'synthetic recording')
        self.source_id = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.telemetry = SimpleNamespace(path=self.path, signature=file_signature(self.path), metadata={})

    def test_capture_reuses_existing_hash_and_passes_compact_summary_to_store(self):
        summary = {'schema_version': 1, 'source_id': self.source_id, 'game': 'iracing', 'observations': []}
        store = Mock()
        store.record_source.return_value = {'saved': True, 'source_id': self.source_id}
        with patch('driver_history_index.build_source_summary', return_value=summary) as build:
            result = index_telemetry(self.telemetry, self.source_id, store=store)
        build.assert_called_once_with(self.telemetry, self.source_id)
        store.record_source.assert_called_once_with(summary)
        self.assertTrue(result['saved'])

    def test_changed_recording_is_not_attached_to_an_old_content_hash(self):
        self.path.write_bytes(b'changed recording with different size')
        with patch('driver_history_index.build_source_summary') as build:
            result = index_telemetry(self.telemetry, self.source_id, store=Mock())
        self.assertEqual(result['reason'], 'SOURCE_CHANGED')
        build.assert_not_called()

    def test_change_while_building_summary_is_not_persisted(self):
        def build(*_):
            self.path.write_bytes(b'another changed recording')
            return {'source_id': self.source_id}
        store = Mock()
        with patch('driver_history_index.build_source_summary', side_effect=build):
            result = index_telemetry(self.telemetry, self.source_id, store=store)
        self.assertEqual(result['reason'], 'SOURCE_CHANGED')
        store.record_source.assert_not_called()

    def test_imported_reference_is_not_counted_as_personal_driving(self):
        self.telemetry.metadata = {'game': 'lmu', 'imported': True}
        with patch('driver_history_index.build_source_summary') as build:
            result = index_telemetry(self.telemetry, self.source_id, store=Mock())
        self.assertEqual(result['reason'], 'IMPORTED_REFERENCE')
        build.assert_not_called()

    def test_import_folder_is_excluded_even_without_metadata_flag(self):
        self.telemetry.metadata = {'game': 'lmu'}
        with patch('lmu_imports.import_dir', return_value=self.root):
            result = index_telemetry(self.telemetry, self.source_id, store=Mock())
        self.assertEqual(result['reason'], 'IMPORTED_REFERENCE')

    def test_invalid_hash_never_creates_a_store(self):
        with patch('driver_history_index.DriverHistoryStore') as factory:
            with self.assertRaises(ValueError):
                index_telemetry(self.telemetry, 'not-a-content-hash')
        factory.assert_not_called()


class HistoryReadRoutesTests(unittest.TestCase):
    def setUp(self):
        self.store = Mock()
        self.store.status.return_value = {'source_count': 0, 'drive_count': 0}
        self.store.list_drives.return_value = []
        self.store.get_drive.return_value = None
        app = FastAPI()
        register_routes(app, store_factory=lambda: self.store)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_routes_are_read_only_and_pass_game_filter_and_bounded_pagination(self):
        result = self.client.get('/api/driver-history/drives?game=lmu&limit=12&offset=3')
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['drives'], [])
        self.store.list_drives.assert_called_once_with(game='lmu', limit=12, offset=3)
        self.assertEqual(self.client.post('/api/driver-history/drives', json={}).status_code, 405)
        self.store.record_source.assert_not_called()

    def test_status_is_filtered_and_does_not_write(self):
        self.assertEqual(self.client.get('/api/driver-history/status?game=iracing').status_code, 200)
        self.store.status.assert_called_once_with(game='iracing')
        self.store.record_source.assert_not_called()

    def test_invalid_filters_and_ids_are_rejected_before_store_access(self):
        for query in ('game=other', 'limit=0', 'limit=201', 'offset=-1'):
            self.assertEqual(self.client.get('/api/driver-history/drives?' + query).status_code, 422)
        self.assertEqual(self.client.get('/api/driver-history/drives/not-an-id').status_code, 422)
        self.store.list_drives.assert_not_called()
        self.store.get_drive.assert_not_called()

    def test_missing_and_present_drive_have_distinct_responses(self):
        drive_id = 'a' * 64
        self.assertEqual(self.client.get('/api/driver-history/drives/' + drive_id).status_code, 404)
        self.store.get_drive.return_value = {'drive_id': drive_id, 'recorded_at': None}
        result = self.client.get('/api/driver-history/drives/' + drive_id)
        self.assertEqual(result.status_code, 200)
        self.assertIsNone(result.json()['drive']['recorded_at'])

    def test_storage_error_is_korean_and_does_not_leak_database_details(self):
        self.store.status.side_effect = sqlite3.OperationalError('private/path/database.sqlite3 locked')
        result = self.client.get('/api/driver-history/status')
        self.assertEqual(result.status_code, 503)
        self.assertIn('주행 기록', result.json()['detail'])
        self.assertNotIn('private/path', result.text)


class AnalysisCaptureFlowTests(unittest.TestCase):
    def test_analysis_modes_reuse_hash_and_history_failure_retains_analysis(self):
        import asyncio
        from contextlib import ExitStack
        from test_rolling_best import BackToBackLaps, api

        fixture = BackToBackLaps()
        fixture.addCleanup = self.addCleanup
        fixture.setUp()
        telemetry = fixture.telemetry([(50, 50), (48, 48)])
        source_id = hashlib.sha256(telemetry.path.read_bytes()).hexdigest()
        with ExitStack() as patches:
            patches.enter_context(patch.object(api, 'resolve_telemetry_file', return_value=telemetry.path))
            patches.enter_context(patch.object(api.repository, 'load', return_value=telemetry))
            patches.enter_context(patch.object(api, 'telemetry_file_visibility', return_value={}))
            patches.enter_context(patch.object(api, 'memory_store', None))
            patches.enter_context(patch.object(api, 'review_notes', return_value={'notes': [], 'pending': []}))
            patches.enter_context(patch('workspace_personal_reference.index_telemetry'))
            capture = patches.enter_context(patch('driver_history_index.index_telemetry', return_value={'saved': True}))
            results = []
            for mode in (api.SessionMode.BASELINE_3LAP, api.SessionMode.ENDURANCE):
                request = api.AnalyzeRequest(filename=telemetry.path.name, session_mode=mode, persist_memory=False)
                results.append(asyncio.run(api.load_analysis(request, update_current_analysis=False)))
            self.assertEqual(capture.call_count, 2)
            for call in capture.call_args_list:
                self.assertIs(call.args[0], telemetry)
                self.assertEqual(call.args[1], source_id)
            capture.side_effect = sqlite3.OperationalError('locked')
            with self.assertLogs('iRacingSetupTuner', level='WARNING'):
                retained = asyncio.run(api.load_analysis(request, update_current_analysis=False))
            self.assertEqual(retained['session'], results[-1]['session'])
            self.assertEqual(retained['comparison'], results[-1]['comparison'])
            self.assertFalse(retained['memory']['persist_requested'])


class HistoryEndToEndTests(unittest.TestCase):
    def test_native_split_copies_merge_through_capture_storage_and_read_api(self):
        import numpy as np
        from driver_history import DriverHistoryStore

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = DriverHistoryStore(root / 'history.sqlite3')
            info = {
                'WeekendInfo': {'SessionID': 123, 'SubSessionID': 456, 'TrackID': 99},
                'DriverInfo': {'DriverCarIdx': 0, 'Drivers': [{'CarIdx': 0, 'UserID': 42, 'CarID': 8}]},
                'SessionInfo': {'Sessions': [{'SessionNum': 0, 'SessionType': 'Practice'}]},
            }
            for name, contents, times in (
                ('first.ibt', b'first-source', [100., 100.1, 100.2, 100.3, 100.4]),
                ('renamed-copy.ibt', b'first-source', [100., 100.1, 100.2, 100.3, 100.4]),
                ('second.ibt', b'second-source', [100.3, 100.4, 100.5, 101., 101.1]),
            ):
                path = root / name
                path.write_bytes(contents)
                source = SimpleNamespace(path=path, signature=file_signature(path), tick_rate=10,
                    metadata={'game': 'iracing', 'car_platform_id': 8, 'track_platform_id': 99, 'track_config': 'GP'},
                    session_info=info, channels={'SessionTime': np.array(times),
                    'SessionNum': np.zeros(len(times)), 'IsReplayPlaying': np.zeros(len(times))})
                index_telemetry(source, hashlib.sha256(contents).hexdigest(), store=store)
            app = FastAPI()
            register_routes(app, store_factory=lambda: store)
            with TestClient(app) as client:
                status = client.get('/api/driver-history/status?game=iracing').json()
                self.assertEqual(status['source_count'], 2)
                self.assertEqual(status['native_session_count'], 1)
                drives = client.get('/api/driver-history/drives?game=iracing').json()['drives']
                self.assertEqual(len(drives), 1)
                self.assertEqual(drives[0]['coverage']['intervals'], [[100., 100.5], [101., 101.1]])
                self.assertEqual(drives[0]['session_type'], 'PRACTICE')
                self.assertIsNone(drives[0]['recorded_at'])
                self.assertEqual(drives[0]['validity_status'], 'UNKNOWN')
                self.assertNotIn('UserID', str(drives))
                self.assertNotIn(str(root), str(drives))


if __name__ == '__main__':
    unittest.main()
