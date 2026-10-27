"""Refresh upgrades connected summaries only; all recordings and stores synthetic."""
import hashlib
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import Mock, patch

import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient

from analysis_numeric import file_signature
from driver_history import DriverHistoryStore
from driver_history_refresh import HistoryRefresh, outdated_sources, refresh_summaries, register_refresh_routes
from driver_history_summary import build_source_summary
import test_driver_history_summary as fixtures


class HistoryRefreshTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.DriverHistorySummaryTests()
        self.fixture.addCleanup = self.addCleanup
        self.fixture.setUp()
        self.telemetry = self.fixture.telemetry()
        self.telemetry.channels.update(Speed=np.full(6, 20.), IsOnTrack=np.ones(6))
        self.telemetry.channels.pop('IsReplayPlaying')
        self.telemetry.signature = file_signature(self.telemetry.path)
        self.source_id = hashlib.sha256(self.telemetry.path.read_bytes()).hexdigest()
        self.store = DriverHistoryStore(self.fixture.folder / 'history.sqlite3')
        old = build_source_summary(self.telemetry, self.source_id)
        for item in old['observations']:
            item['metrics'].pop('calculation_version', None)
            item['metrics']['driving'] = dict(status='UNAVAILABLE', time_s=None, distance_m=None,
                                             intervals=[], reason_codes=['REPLAY_STATE_UNAVAILABLE'])
        self.store.record_source(old)
        self.database = self.fixture.folder / 'analysis.sqlite3'
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute('CREATE TABLE sessions(file_hash TEXT,file_path TEXT)')
            db.execute('INSERT INTO sessions VALUES(?,?)', (self.source_id, str(self.telemetry.path)))
            db.execute('INSERT INTO sessions VALUES(?,?)', ('f' * 64, 'NEVER_READ_UNCONNECTED.ibt'))
        self.loader = Mock(return_value=self.telemetry)

    def run_refresh(self, **kwargs):
        return refresh_summaries('iracing', store=self.store, memory_db_path=self.database,
            allowed_paths=kwargs.pop('allowed_paths', lambda _: [self.telemetry.path]),
            load_telemetry=kwargs.pop('load_telemetry', self.loader), **kwargs)

    def test_old_connected_source_is_remeasured_without_changing_memory_db_or_source(self):
        original = self.telemetry.path.read_bytes()
        database = self.database.read_bytes()
        result = self.run_refresh()
        self.assertEqual((result['status'], result['updated'], result['total']), ('COMPLETE', 1, 1))
        self.loader.assert_called_once_with(self.telemetry.path)
        snapshot = self.store.report_snapshot('iracing')
        self.assertEqual(snapshot['source_count'], 1)
        metrics = snapshot['drives'][0]['observations'][0]['metrics']
        self.assertEqual(metrics['calculation_version'], 'observed-metrics-v2')
        self.assertEqual(metrics['driving']['replay_status'], 'UNKNOWN')
        self.assertGreater(metrics['driving']['time_s'], 0)
        self.assertEqual(original, self.telemetry.path.read_bytes())
        self.assertEqual(database, self.database.read_bytes())
        self.assertNotIn(str(self.fixture.folder), json.dumps(result))

    def test_current_summary_is_not_parsed_again(self):
        self.run_refresh()
        self.loader.reset_mock()
        with patch('driver_history_refresh._locators', side_effect=AssertionError('must not query locators')):
            result = self.run_refresh()
        self.assertEqual(result['total'], 0)
        self.loader.assert_not_called()

    def test_extraction_failure_preserves_previous_summary_and_remains_retryable(self):
        before = self.store.report_snapshot('iracing')
        with patch('driver_history_metrics.build_observation_metrics', side_effect=ValueError('PRIVATE_PATH')):
            result = self.run_refresh()
        self.assertEqual((result['updated'], result['failed']), (0, 1))
        self.assertEqual(result['reason_counts'], [{'code': 'METRICS_EXTRACTION_FAILED', 'count': 1}])
        self.assertEqual(before, self.store.report_snapshot('iracing'))
        self.assertEqual(outdated_sources(before), {self.source_id})
        self.assertNotIn('PRIVATE_PATH', json.dumps(result))
        self.assertEqual(self.run_refresh()['updated'], 1)

    def test_current_version_with_extraction_failure_is_still_stale(self):
        with patch('driver_history_metrics.build_observation_metrics', side_effect=ValueError('synthetic failure')):
            failed = build_source_summary(self.telemetry, self.source_id)
        self.store.record_source(failed)
        self.assertEqual(outdated_sources(self.store.report_snapshot('iracing')), {self.source_id})
        self.assertEqual(self.run_refresh()['updated'], 1)

    def test_replaced_first_locator_does_not_hide_an_exact_later_copy(self):
        replaced = self.fixture.folder / 'replaced.ibt'
        replaced.write_bytes(b'wrong content')
        with patch('driver_history_refresh._locators', return_value={self.source_id: [replaced, self.telemetry.path]}), \
                patch('driver_history_refresh._digest', wraps=lambda path: hashlib.sha256(path.read_bytes()).hexdigest()) as digest:
            result = self.run_refresh(allowed_paths=lambda _: [replaced, self.telemetry.path])
        self.assertEqual(result['updated'], 1)
        self.assertEqual(digest.call_count, 2)
        self.loader.assert_called_once_with(self.telemetry.path)

    def test_replaced_saved_locator_falls_back_to_native_clock_and_hash_once_per_file(self):
        replaced = self.fixture.folder / 'replaced.ibt'
        replaced.write_bytes(b'wrong content')
        with patch('driver_history_refresh._locators', return_value={self.source_id: [replaced]}), \
                patch('driver_history_refresh._digest', wraps=lambda path: hashlib.sha256(path.read_bytes()).hexdigest()) as digest:
            result = self.run_refresh(allowed_paths=lambda _: [replaced, self.telemetry.path])
        self.assertEqual(result['updated'], 1)
        self.assertEqual(digest.call_count, 2)
        self.loader.assert_called_once_with(self.telemetry.path)

    def test_unreadable_locator_database_does_not_block_header_fallback(self):
        self.database.write_bytes(b'not a sqlite database')
        before = self.database.read_bytes()
        result = self.run_refresh()
        self.assertEqual(result['updated'], 1)
        self.assertEqual(before, self.database.read_bytes())

    def test_unlisted_saved_path_is_not_opened_or_loaded(self):
        before = self.store.report_snapshot('iracing')
        with patch('driver_history_refresh._digest', side_effect=AssertionError('outside configured library')):
            result = self.run_refresh(allowed_paths=lambda _: [])
        self.assertEqual(result['skipped'], 1)
        self.assertEqual(result['reason_counts'], [{'code': 'ORIGINAL_UNAVAILABLE', 'count': 1}])
        self.assertEqual(before, self.store.report_snapshot('iracing'))
        self.loader.assert_not_called()

    def test_changed_original_never_replaces_previous_summary(self):
        before = self.store.report_snapshot('iracing')
        self.telemetry.path.write_bytes(b'changed')
        result = self.run_refresh()
        self.assertEqual(result['updated'], 0)
        self.assertEqual(result['reason_counts'], [{'code': 'SOURCE_CHANGED', 'count': 1}])
        self.assertEqual(before, self.store.report_snapshot('iracing'))
        self.loader.assert_not_called()

    def test_change_while_loading_does_not_replace_previous_summary(self):
        before = self.store.report_snapshot('iracing')
        def load(_):
            self.telemetry.path.write_bytes(b'changed during parse')
            return self.telemetry
        result = self.run_refresh(load_telemetry=load)
        self.assertEqual(result['updated'], 0)
        self.assertEqual(before, self.store.report_snapshot('iracing'))

    def test_parser_errors_do_not_leak_source_paths(self):
        result = self.run_refresh(load_telemetry=Mock(side_effect=RuntimeError(str(self.telemetry.path))))
        self.assertEqual(result['failed'], 1)
        self.assertEqual(result['processed'], 1)
        self.assertNotIn(str(self.fixture.folder), json.dumps(result))

    def test_missing_locator_database_is_not_created(self):
        self.database = self.fixture.folder / 'missing.sqlite3'
        result = self.run_refresh()
        self.assertEqual(result['updated'], 1)
        self.assertFalse(self.database.exists())

    def test_preview_without_analysis_locator_matches_native_clock_and_exact_content(self):
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute('DELETE FROM sessions')
        with patch('driver_history_refresh._digest', wraps=lambda path: hashlib.sha256(path.read_bytes()).hexdigest()) as digest:
            result = self.run_refresh()
        self.assertEqual(result['updated'], 1)
        digest.assert_called_once()
        self.loader.assert_called_once_with(self.telemetry.path)

    def test_matching_native_date_with_different_content_cannot_seed_or_replace_history(self):
        before = self.store.report_snapshot('iracing')
        self.database = self.fixture.folder / 'missing.sqlite3'
        with self.telemetry.path.open('ab') as source:
            source.write(b'different content with same header clock')
        result = self.run_refresh()
        self.assertEqual(result['updated'], 0)
        self.assertEqual(before, self.store.report_snapshot('iracing'))
        self.loader.assert_not_called()

    def test_empty_new_observation_does_not_erase_connected_history(self):
        before = self.store.report_snapshot('iracing')
        with patch('driver_history_refresh.build_source_summary', return_value={'observations': []}):
            result = self.run_refresh()
        self.assertEqual(result['reason_counts'], [{'code': 'OBSERVATIONS_UNAVAILABLE', 'count': 1}])
        self.assertEqual(before, self.store.report_snapshot('iracing'))

    def test_imported_reference_and_other_game_are_excluded(self):
        for metadata in [{'imported': True}, {'game': 'lmu'}]:
            with self.subTest(metadata=metadata):
                self.telemetry.metadata = metadata
                result = self.run_refresh()
                self.assertEqual(result['updated'], 0)
                self.assertEqual(result['reason_counts'][0]['code'], 'REFERENCE_OR_GAME_MISMATCH')

    def test_status_read_and_duplicate_start_do_not_start_duplicate_workers(self):
        service = HistoryRefresh(memory_db_path=lambda: self.database,
            allowed_paths=lambda _: [self.telemetry.path], load_telemetry=self.loader,
            store_factory=lambda: self.store)
        with patch('driver_history_refresh.threading.Thread') as worker:
            self.assertEqual(service.status('iracing')['status'], 'IDLE')
            self.assertEqual(service.start('iracing')['status'], 'RUNNING')
            self.assertEqual(service.start('iracing')['status'], 'RUNNING')
            worker.assert_called_once()
            with self.assertRaises(Exception) as error:
                service.start('lmu')
            self.assertEqual(error.exception.status_code, 409)

    def test_worker_start_failure_releases_running_slot_for_retry(self):
        service = HistoryRefresh(memory_db_path=lambda: self.database,
            allowed_paths=lambda _: [self.telemetry.path], load_telemetry=self.loader,
            store_factory=lambda: self.store)
        with patch('driver_history_refresh.threading.Thread') as worker:
            worker.return_value.start.side_effect = RuntimeError('PRIVATE thread error')
            self.assertEqual(service.start('iracing')['status'], 'FAILED')
            self.assertIsNone(service._running)
            worker.return_value.start.side_effect = None
            self.assertEqual(service.start('iracing')['status'], 'RUNNING')
            self.assertEqual(worker.call_count, 2)
        self.assertNotIn('PRIVATE', json.dumps(service.status('iracing')))

    def test_status_reason_counts_are_detached_from_internal_progress(self):
        service = HistoryRefresh(memory_db_path=lambda: self.database,
            allowed_paths=lambda _: [], load_telemetry=self.loader)
        service._states['iracing'] = {'status': 'RUNNING', 'reason_counts': [{'code': 'SOURCE_CHANGED', 'count': 1}]}
        returned = service.status('iracing')
        returned['reason_counts'][0]['count'] = 100
        self.assertEqual(service.status('iracing')['reason_counts'][0]['count'], 1)

    def test_read_routes_never_run_refresh_and_invalid_game_is_rejected(self):
        service = Mock()
        service.status.return_value = {'game': 'iracing', 'status': 'IDLE'}
        service.start.return_value = {'game': 'iracing', 'status': 'RUNNING'}
        app = FastAPI()
        register_refresh_routes(app, service)
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/driver-history/refresh').json()['status'], 'IDLE')
            service.start.assert_not_called()
            self.assertEqual(client.post('/api/driver-history/refresh?game=other').status_code, 422)
            service.start.assert_not_called()
            self.assertEqual(client.post('/api/driver-history/refresh?game=iracing').status_code, 200)
            service.start.assert_called_once_with('iracing')


if __name__ == '__main__':
    unittest.main()
