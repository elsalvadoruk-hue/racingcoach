"""Whole-library background import contracts, using synthetic temporary recordings."""
from contextlib import closing
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import weakref

import numpy as np
from fastapi import HTTPException

from analysis_numeric import file_signature
from driver_history import DriverHistoryStore
from driver_history_summary import build_source_summary
from driver_history_library import HistoryLibrary


class Telemetry:
    pass


class HistoryLibraryTests(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory(prefix='driver-library-test-')
        self.addCleanup(work.cleanup)
        self.folder = Path(work.name)
        self.store = DriverHistoryStore(self.folder / 'data/history.sqlite3')
        self.recordings = {}
        self.loader = Mock(side_effect=lambda path: deepcopy(self.recordings[Path(path)]))
        self.services = []
        self.addCleanup(self.stop_services)

    def stop_services(self):
        for service in self.services:
            service.shutdown(wait=True, timeout=3)

    def recording(self, name='old.ibt', *, game='iracing', imported=False):
        path = self.folder / name
        header = bytearray(144)
        struct.pack_into('<10i', header, 0, 2, 0, 10, 1, 0, 0, 3, 0, 1, 8)
        # A 2009 native date proves library import never applies 30/90-day filters.
        struct.pack_into('<Qddii', header, 112, 1230768000, 100., 100.5, 0, 6)
        path.write_bytes(header + name.encode('utf-8'))
        item = Telemetry()
        item.path, item.signature, item.tick_rate = path, file_signature(path), 10
        item.channels = {'SessionTime': 100. + np.arange(6) / 10., 'SessionNum': np.zeros(6),
                         'IsReplayPlaying': np.zeros(6), 'Speed': np.full(6, 20.), 'IsOnTrack': np.ones(6)}
        item.metadata = {'game': game, 'car_path': 'synthetic-car', 'track_path': 'synthetic-track',
                         'track_config': 'full', 'imported': imported}
        item.session_info = {'WeekendInfo': {'SessionID': 123, 'SubSessionID': 456, 'TrackID': 8},
                             'DriverInfo': {'DriverCarIdx': 0, 'Drivers': [{'CarIdx': 0, 'UserID': 99, 'CarID': 7}]},
                             'SessionInfo': {'Sessions': [{'SessionNum': 0, 'SessionType': 'Practice'}]}}
        self.recordings[path] = item
        return item

    def service(self, *, paths=None, loader=None, factory=None, **kwargs):
        service = HistoryLibrary(allowed_paths=paths or (lambda game: list(self.recordings)),
                                 load_telemetry=loader or self.loader,
                                 store_factory=factory or (lambda: self.store), **kwargs)
        self.services.append(service)
        return service

    def terminal(self, service, game='iracing', expected='COMPLETE'):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            state = service.status(game)
            if state['status'] in {'COMPLETE', 'PAUSED', 'FAILED'}:
                self.assertEqual(state['status'], expected, state)
                return state
            threading.Event().wait(.005)
        self.fail('Background library did not finish')

    def index(self, item):
        source_id = hashlib.sha256(item.path.read_bytes()).hexdigest()
        self.store.record_source(build_source_summary(item, source_id))
        return source_id

    def waiting(self, service):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            state = service.status('iracing')
            if state['status'] == 'WAITING':
                return state
            threading.Event().wait(.005)
        self.fail('Background library did not yield to resource policy')

    def test_status_is_read_only_and_does_not_discover_sources_or_create_databases(self):
        paths, factory = Mock(side_effect=AssertionError('no scan')), Mock(side_effect=AssertionError('no store'))
        service = self.service(paths=paths, factory=factory)
        status = service.status('iracing')
        self.assertEqual(status['status'], 'IDLE')
        self.assertIsNone(status['busy_game'])
        self.assertEqual(list(self.folder.iterdir()), [])
        paths.assert_not_called()
        factory.assert_not_called()

    def test_entire_library_including_very_old_recordings_is_imported(self):
        self.recording('past-1.ibt')
        self.recording('past-2.ibt')
        service = self.service()
        service.start('iracing')
        state = self.terminal(service)
        self.assertEqual((state['total'], state['processed'], state['updated'], state['failed']), (2, 2, 2, 0))
        self.assertEqual(self.store.status('iracing')['source_count'], 2)
        self.assertTrue(all(row['recorded_at'].startswith('2009-') for row in self.store.list_drives('iracing')))

    def test_current_connected_content_is_not_parsed_again(self):
        item = self.recording()
        self.index(item)
        service = self.service()
        service.start('iracing')
        state = self.terminal(service)
        self.loader.assert_not_called()
        self.assertEqual((state['updated'], state['skipped']), (0, 1))

    def test_copied_content_is_parsed_once_and_registered_as_one_source(self):
        first, second = self.recording('first.ibt'), self.recording('copy.ibt')
        second.path.write_bytes(first.path.read_bytes())
        second.signature = file_signature(second.path)
        service = self.service()
        service.start('iracing')
        state = self.terminal(service)
        self.assertEqual(self.loader.call_count, 1)
        self.assertEqual((state['updated'], state['skipped']), (1, 1))
        self.assertEqual(self.store.status('iracing')['source_count'], 1)

    def test_successful_checkpoints_avoid_hashing_and_parsing_after_service_restart(self):
        self.recording()
        service = self.service()
        service.start('iracing')
        self.terminal(service)
        self.loader.reset_mock()
        reopened = self.service()
        with patch('driver_history_library._digest', side_effect=AssertionError('cached unchanged file')):
            reopened.start('iracing')
            state = self.terminal(reopened)
        self.assertEqual((state['updated'], state['skipped'], state['failed']), (0, 1, 0))
        self.loader.assert_not_called()

    def test_deleted_ledger_is_rebuilt_even_with_successful_file_checkpoints(self):
        self.recording()
        service = self.service()
        service.start('iracing')
        self.terminal(service)
        self.store.path.unlink()
        self.loader.reset_mock()
        reopened = self.service()
        reopened.start('iracing')
        state = self.terminal(reopened)
        self.assertEqual(state['updated'], 1)
        self.loader.assert_called_once()
        self.assertEqual(self.store.status('iracing')['source_count'], 1)

    def test_result_calculation_change_rechecks_unchanged_sources_with_current_metrics(self):
        self.recording()
        service = self.service()
        service.start('iracing')
        self.terminal(service)
        self.loader.reset_mock()
        with patch('driver_history_library.LIBRARY_VERSION', 'next-results-calculation'):
            reopened = self.service()
            reopened.start('iracing')
            state = self.terminal(reopened)
        self.assertEqual((state['updated'], state['failed']), (1, 0))
        self.loader.assert_called_once()

    def test_version_refresh_reparses_copied_content_once_per_run(self):
        first, second = self.recording('first.ibt'), self.recording('copy.ibt')
        second.path.write_bytes(first.path.read_bytes())
        second.signature = file_signature(second.path)
        service = self.service()
        service.start('iracing')
        self.terminal(service)
        self.loader.reset_mock()
        with patch('driver_history_library.LIBRARY_VERSION', 'next-results-calculation'):
            service.start('iracing')
            state = self.terminal(service)
        self.assertEqual((state['updated'], state['skipped']), (1, 1))
        self.loader.assert_called_once()

    def test_newly_discovered_files_are_imported_on_next_start(self):
        self.recording('first.ibt')
        service = self.service()
        service.start('iracing')
        initial = self.terminal(service)
        self.recording('later.ibt')
        service.start('iracing')
        state = self.terminal(service)
        self.assertNotEqual(initial['job_id'], state['job_id'])
        self.assertEqual((state['total'], state['updated'], state['skipped']), (2, 1, 1))
        self.assertEqual(self.loader.call_count, 2)

    def test_changed_original_adds_new_content_without_destroying_previous_source(self):
        item = self.recording()
        service = self.service()
        service.start('iracing')
        self.terminal(service)
        before = self.store.report_snapshot('iracing')
        item.path.write_bytes(item.path.read_bytes() + b'new bytes')
        item.signature = file_signature(item.path)
        service.start('iracing')
        state = self.terminal(service)
        self.assertEqual(state['updated'], 1)
        after = self.store.report_snapshot('iracing')
        self.assertEqual(after['source_count'], before['source_count'] + 1)

    def test_failed_file_is_retryable_and_never_exposes_private_exception(self):
        self.recording()
        self.loader.side_effect = RuntimeError(str(self.folder) + '/PRIVATE_NAME.ibt')
        service = self.service()
        service.start('iracing')
        failed = self.terminal(service)
        self.assertEqual((failed['failed'], failed['processed']), (1, 1))
        self.assertNotIn(str(self.folder), json.dumps(failed))
        self.assertNotIn('PRIVATE_NAME', json.dumps(failed))
        self.loader.side_effect = lambda path: deepcopy(self.recordings[path])
        service.start('iracing')
        self.assertEqual(self.terminal(service)['updated'], 1)

    def test_empty_recording_is_not_inserted_and_its_skip_is_checkpointed(self):
        item = self.recording()
        item.channels['SessionTime'] = np.array([])
        service = self.service()
        service.start('iracing')
        state = self.terminal(service)
        self.assertEqual((state['skipped'], state['updated']), (1, 0))
        self.assertFalse(self.store.path.exists())
        self.loader.reset_mock()
        reopened = self.service()
        reopened.start('iracing')
        self.assertEqual(self.terminal(reopened)['skipped'], 1)
        self.loader.assert_not_called()

    def test_imported_reference_is_excluded_and_not_reparsed_on_restart(self):
        self.recording(imported=True)
        service = self.service()
        service.start('iracing')
        state = self.terminal(service)
        self.assertEqual((state['updated'], state['skipped']), (0, 1))
        self.assertFalse(self.store.path.exists())
        self.loader.reset_mock()
        reopened = self.service()
        reopened.start('iracing')
        self.terminal(reopened)
        self.loader.assert_not_called()

    def test_other_game_cannot_be_inserted_into_requested_library(self):
        self.recording(game='lmu')
        service = self.service()
        service.start('iracing')
        state = self.terminal(service)
        self.assertEqual((state['updated'], state['skipped']), (0, 1))
        self.assertFalse(self.store.path.exists())

    def test_lmu_duckdb_is_supported_without_promoting_session_identity(self):
        self.recording('past.duckdb', game='lmu')
        service = self.service()
        with patch('driver_history_library._imported_reference', return_value=False):
            service.start('lmu')
            state = self.terminal(service, 'lmu')
        self.assertEqual(state['updated'], 1)
        self.assertEqual(self.store.status('lmu')['source_only_observation_count'], 1)
        self.assertEqual(self.store.status('lmu')['native_session_count'], 0)

    def test_file_changed_during_parse_preserves_old_source_and_remains_retryable(self):
        item = self.recording()
        self.index(item)
        old = self.store.report_snapshot('iracing')
        # Make old metrics stale so this source must actually be loaded.
        summary = build_source_summary(item, hashlib.sha256(item.path.read_bytes()).hexdigest())
        summary['observations'][0]['metrics'].pop('calculation_version')
        self.store.record_source(summary)
        old = self.store.report_snapshot('iracing')
        def change(path):
            path.write_bytes(path.read_bytes() + b'changed during parse')
            return deepcopy(item)
        service = self.service(loader=change)
        service.start('iracing')
        state = self.terminal(service)
        self.assertEqual(state['updated'], 0)
        self.assertEqual(old, self.store.report_snapshot('iracing'))
        self.assertIn('SOURCE_CHANGED', {row['code'] for row in state['reason_counts']})

    def test_changed_during_hash_is_not_parsed_or_checkpointed_as_current(self):
        item = self.recording()
        source_id = hashlib.sha256(item.path.read_bytes()).hexdigest()
        def change(path, should_stop=None):
            path.write_bytes(path.read_bytes() + b'changed while hashing')
            return source_id
        service = self.service()
        with patch('driver_history_library._digest', side_effect=change):
            service.start('iracing')
            state = self.terminal(service)
        self.assertEqual(state['updated'], 0)
        self.assertEqual(state['reason_counts'], [{'code': 'SOURCE_CHANGED', 'count': 1}])
        self.loader.assert_not_called()
        item.signature = file_signature(item.path)
        service.start('iracing')
        self.assertEqual(self.terminal(service)['updated'], 1)

    def test_stale_connected_metrics_are_upgraded_once(self):
        item = self.recording()
        source_id = hashlib.sha256(item.path.read_bytes()).hexdigest()
        summary = build_source_summary(item, source_id)
        summary['observations'][0]['metrics'].pop('calculation_version')
        self.store.record_source(summary)
        service = self.service()
        service.start('iracing')
        self.assertEqual(self.terminal(service)['updated'], 1)
        self.assertEqual(self.loader.call_count, 1)
        service.start('iracing')
        self.terminal(service)
        self.assertEqual(self.loader.call_count, 1)

    def test_metrics_extraction_failure_does_not_replace_previous_summary(self):
        item = self.recording()
        source_id = hashlib.sha256(item.path.read_bytes()).hexdigest()
        summary = build_source_summary(item, source_id)
        summary['observations'][0]['metrics'].pop('calculation_version')
        self.store.record_source(summary)
        before = self.store.report_snapshot('iracing')
        service = self.service()
        with patch('driver_history_metrics.build_observation_metrics', side_effect=ValueError('PRIVATE')):
            service.start('iracing')
            state = self.terminal(service)
        self.assertEqual((state['updated'], state['failed']), (0, 1))
        self.assertEqual(before, self.store.report_snapshot('iracing'))
        service.start('iracing')
        self.assertEqual(self.terminal(service)['updated'], 1)

    def test_start_returns_immediately_and_reuses_active_same_game_job(self):
        self.recording()
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def load(path):
            started.set()
            release.wait(3)
            return deepcopy(self.recordings[path])
        service = self.service(loader=load)
        before = time.perf_counter()
        state = service.start('iracing')
        self.assertLess(time.perf_counter() - before, .5)
        self.assertTrue(started.wait(2))
        same = service.start('iracing')
        self.assertEqual(same['job_id'], state['job_id'])
        self.assertEqual(same['busy_game'], 'iracing')
        release.set()
        self.assertEqual(self.terminal(service)['updated'], 1)

    def test_other_game_reports_busy_and_cannot_start_second_parser(self):
        self.recording()
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def load(path):
            started.set()
            release.wait(3)
            return deepcopy(self.recordings[path])
        service = self.service(loader=load)
        service.start('iracing')
        self.assertTrue(started.wait(2))
        self.assertEqual(service.status('lmu')['busy_game'], 'iracing')
        with self.assertRaises(HTTPException) as raised:
            service.start('lmu')
        self.assertEqual(raised.exception.status_code, 409)
        release.set()
        self.terminal(service)
        self.assertIsNone(service.status('lmu')['busy_game'])

    def test_pause_discards_uncommitted_current_file_and_resume_retries_it(self):
        self.recording('first.ibt')
        self.recording('second.ibt')
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def load(path):
            if path.name == 'first.ibt':
                started.set()
                release.wait(3)
            return deepcopy(self.recordings[path])
        loader = Mock(side_effect=load)
        service = self.service(loader=loader)
        service.start('iracing')
        self.assertTrue(started.wait(2))
        self.assertEqual(service.pause('iracing')['status'], 'PAUSING')
        release.set()
        paused = self.terminal(service, expected='PAUSED')
        self.assertEqual((paused['processed'], paused['updated']), (0, 0))
        self.assertEqual(service.status('iracing')['status'], 'PAUSED')
        service.start('iracing')
        finished = self.terminal(service)
        self.assertEqual((finished['processed'], finished['updated'], finished['skipped']), (2, 2, 0))
        self.assertEqual(loader.call_count, 3)

    def test_shutdown_discards_current_uncommitted_file(self):
        self.recording('first.ibt')
        self.recording('second.ibt')
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def load(path):
            started.set()
            release.wait(3)
            return deepcopy(self.recordings[path])
        service = self.service(loader=load)
        service.start('iracing')
        self.assertTrue(started.wait(2))
        service.shutdown()
        release.set()
        state = self.terminal(service, expected='PAUSED')
        self.assertEqual(state['processed'], 0)
        self.assertEqual(self.store.status('iracing')['source_count'], 0)

    def test_scan_failure_is_sanitized_and_next_start_can_retry(self):
        self.recording()
        paths = Mock(side_effect=OSError('PRIVATE_FOLDER'))
        service = self.service(paths=paths)
        service.start('iracing')
        failed = self.terminal(service, expected='FAILED')
        self.assertNotIn('PRIVATE_FOLDER', json.dumps(failed))
        self.assertIsNone(failed['busy_game'])
        paths.side_effect = None
        paths.return_value = list(self.recordings)
        service.start('iracing')
        self.assertEqual(self.terminal(service)['updated'], 1)

    def test_worker_start_failure_releases_slot_and_can_retry(self):
        self.recording()
        service = self.service()
        with patch('driver_history_library.threading.Thread') as worker:
            worker.return_value.start.side_effect = RuntimeError('PRIVATE_START')
            state = service.start('iracing')
        self.assertEqual(state['status'], 'FAILED')
        self.assertIsNone(state['busy_game'])
        self.assertNotIn('PRIVATE_START', json.dumps(state))
        service.start('iracing')
        self.assertEqual(self.terminal(service)['updated'], 1)

    def test_progress_and_checkpoint_do_not_expose_paths_or_private_names(self):
        self.recording('PRIVATE_RECORDING.ibt')
        service = self.service()
        service.start('iracing')
        state = self.terminal(service)
        self.assertNotIn(str(self.folder), json.dumps(state))
        self.assertNotIn('PRIVATE_RECORDING', json.dumps(state))
        journal, = list(self.store.path.parent.glob('*.library.sqlite3'))
        raw = journal.read_bytes()
        self.assertNotIn(str(self.folder).encode(), raw)
        self.assertNotIn(b'PRIVATE_RECORDING', raw)
        self.assertNotEqual(journal, self.store.path)

    def test_status_returns_detached_reason_counts(self):
        self.recording(imported=True)
        service = self.service()
        service.start('iracing')
        first = self.terminal(service)
        first['reason_counts'][0]['count'] = 99
        self.assertEqual(service.status('iracing')['reason_counts'][0]['count'], 1)

    def test_discovery_deduplicates_repeated_paths(self):
        item = self.recording()
        service = self.service(paths=lambda _: [item.path, item.path])
        service.start('iracing')
        state = self.terminal(service)
        self.assertEqual((state['total'], state['updated']), (1, 1))
        self.loader.assert_called_once()

    def test_unsupported_files_are_skipped_without_parsing(self):
        self.recording('other.txt')
        service = self.service()
        service.start('iracing')
        state = self.terminal(service)
        self.assertEqual((state['updated'], state['skipped']), (0, 1))
        self.loader.assert_not_called()

    def test_each_recording_is_released_before_loading_the_next(self):
        self.recording('first.ibt')
        self.recording('second.ibt')
        references = []
        def load(path):
            if references:
                self.assertIsNone(references[-1](), 'previous full telemetry retained')
            item = deepcopy(self.recordings[path])
            references.append(weakref.ref(item))
            return item
        service = self.service(loader=load)
        service.start('iracing')
        self.assertEqual(self.terminal(service)['updated'], 2)
        self.assertTrue(all(ref() is None for ref in references))

    def test_persistent_journal_keeps_the_ledger_schema_unchanged(self):
        self.recording()
        service = self.service()
        service.start('iracing')
        self.terminal(service)
        with closing(sqlite3.connect(self.store.path.resolve().as_uri()+'?mode=ro', uri=True)) as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(tables, {'sources', 'observations'})

    def test_invalid_game_never_discovers_or_writes_files(self):
        service = self.service()
        for method in [service.status, service.start, service.pause]:
            with self.subTest(method=method.__name__), self.assertRaises(ValueError):
                method('other')
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_duckdb_with_wal_is_retryable_without_opening_or_cache_poisoning(self):
        item = self.recording('active.duckdb', game='lmu')
        wal = Path(str(item.path) + '.wal')
        wal.write_bytes(b'synthetic active recording')
        service = self.service()
        service.start('lmu')
        state = self.terminal(service, 'lmu')
        self.assertEqual((state['updated'], state['skipped']), (0, 1))
        self.loader.assert_not_called()
        wal.unlink()
        with patch('driver_history_library._imported_reference', return_value=False):
            service.start('lmu')
            self.assertEqual(self.terminal(service, 'lmu')['updated'], 1)

    def test_duckdb_wal_created_while_parsing_prevents_inserting_unstable_content(self):
        item = self.recording('active.duckdb', game='lmu')
        def load(path):
            Path(str(path) + '.wal').write_bytes(b'changed while reading')
            return deepcopy(item)
        service = self.service(loader=load)
        service.start('lmu')
        state = self.terminal(service, 'lmu')
        self.assertEqual((state['updated'], state['skipped']), (0, 1))
        self.assertEqual(state['reason_counts'], [{'code': 'RECORDING_IN_USE', 'count': 1}])
        self.assertFalse(self.store.path.exists())

    def test_store_write_failure_is_retryable_and_existing_content_is_unchanged(self):
        self.recording()
        service = self.service()
        with patch.object(self.store, 'record_source', side_effect=sqlite3.OperationalError('PRIVATE database busy')):
            service.start('iracing')
            state = self.terminal(service)
        self.assertEqual((state['updated'], state['failed']), (0, 1))
        self.assertNotIn('PRIVATE', json.dumps(state))
        service.start('iracing')
        self.assertEqual(self.terminal(service)['updated'], 1)

    def test_journal_schema_collision_does_not_overwrite_existing_file(self):
        self.recording()
        self.store.path.parent.mkdir()
        journal = self.store.path.with_name(self.store.path.stem + '.library.sqlite3')
        with closing(sqlite3.connect(journal)) as db, db:
            db.execute('CREATE TABLE unrelated(value TEXT)')
            db.execute("INSERT INTO unrelated VALUES('KEEP')")
        original = journal.read_bytes()
        service = self.service()
        service.start('iracing')
        state = self.terminal(service, expected='FAILED')
        self.assertEqual(state['updated'], 0)
        self.assertEqual(original, journal.read_bytes())
        self.loader.assert_not_called()
        self.assertFalse(self.store.path.exists())

    def test_existing_history_is_read_only_once_per_entire_run(self):
        for index in range(12):
            self.recording(f'past-{index}.ibt')
        service = self.service()
        with patch.object(self.store, 'report_snapshot', wraps=self.store.report_snapshot) as snapshots:
            service.start('iracing')
            state = self.terminal(service)
        self.assertEqual(state['updated'], 12)
        snapshots.assert_called_once_with('iracing')

    def test_resource_policy_waits_without_scanning_then_resumes_automatically(self):
        item = self.recording()
        busy = threading.Event()
        busy.set()
        paths = Mock(return_value=[item.path])
        service = self.service(paths=paths, can_run=lambda: '주행 중' if busy.is_set() else None)
        started = service.start('iracing')
        state = self.waiting(service)
        self.assertEqual((state['processed'], state['failed'], state['wait_reason']), (0, 0, '주행 중'))
        self.assertEqual(started['job_id'], state['job_id'])
        self.assertFalse(self.store.path.parent.exists())
        paths.assert_not_called()
        self.loader.assert_not_called()
        busy.clear()
        final = self.terminal(service)
        self.assertEqual(final['updated'], 1)
        self.assertIsNone(final['wait_reason'])

    def test_subprocess_callback_interrupt_is_not_failure_and_retries_same_file(self):
        item = self.recording()
        busy = threading.Event()
        calls, released = [], []
        def summarize(path, source_id, should_stop):
            calls.append(path)
            if len(calls) == 1:
                busy.set()
                self.assertTrue(should_stop())
                released.append(True)
                raise InterruptedError('synthetic parser released')
            return build_source_summary(deepcopy(item), source_id)
        service = self.service(can_run=lambda: '주행 분석 중' if busy.is_set() else None,
                               summarize_file=summarize)
        service.start('iracing')
        waiting = self.waiting(service)
        self.assertEqual((waiting['processed'], waiting['failed'], waiting['updated']), (0, 0, 0))
        self.assertEqual(released, [True])
        busy.clear()
        final = self.terminal(service)
        self.assertEqual((final['processed'], final['updated'], final['failed']), (1, 1, 0))
        self.assertEqual(calls, [item.path, item.path])
        self.loader.assert_not_called()

    def test_busy_policy_after_summary_discards_uncommitted_result_then_retries(self):
        item = self.recording()
        busy = threading.Event()
        calls = []
        def summarize(path, source_id, should_stop):
            result = build_source_summary(deepcopy(item), source_id)
            calls.append(path)
            if len(calls) == 1:
                busy.set()
            return result
        service = self.service(can_run=lambda: '주행 중' if busy.is_set() else None,
                               summarize_file=summarize)
        service.start('iracing')
        waiting = self.waiting(service)
        self.assertFalse(self.store.path.exists())
        self.assertEqual((waiting['processed'], waiting['updated'], waiting['failed']), (0, 0, 0))
        busy.clear()
        self.assertEqual(self.terminal(service)['updated'], 1)
        self.assertEqual(len(calls), 2)

    def test_manual_pause_while_waiting_is_not_automatically_resumed_when_idle(self):
        self.recording()
        busy = threading.Event()
        busy.set()
        service = self.service(can_run=lambda: '주행 중' if busy.is_set() else None)
        service.start('iracing')
        self.waiting(service)
        service.pause('iracing')
        state = self.terminal(service, expected='PAUSED')
        busy.clear()
        self.assertEqual(service.status('iracing')['status'], 'PAUSED')
        self.assertEqual((state['processed'], state['failed']), (0, 0))
        self.loader.assert_not_called()
        service.start('iracing')
        self.assertEqual(self.terminal(service)['updated'], 1)

    def test_callback_manual_pause_interrupts_current_parse_and_releases_resources(self):
        self.recording()
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def summarize(path, source_id, should_stop):
            started.set()
            while not should_stop():
                if release.wait(.005):
                    break
            if should_stop():
                raise InterruptedError('private cancellation reason')
            return build_source_summary(deepcopy(self.recordings[path]), source_id)
        service = self.service(summarize_file=summarize)
        service.start('iracing')
        self.assertTrue(started.wait(2))
        service.pause('iracing')
        state = self.terminal(service, expected='PAUSED')
        self.assertEqual((state['processed'], state['updated'], state['failed']), (0, 0, 0))
        self.assertFalse(self.store.path.exists())
        self.assertNotIn('private', json.dumps(state))

    def test_cancelled_hash_stops_between_chunks_and_never_calls_parser(self):
        from driver_history_library import _digest
        item = self.recording()
        item.path.write_bytes(b'x' * (3 * 1024 * 1024))
        checks = iter([False, True])
        with self.assertRaises(InterruptedError):
            _digest(item.path, lambda: next(checks, True))
        self.loader.assert_not_called()

    def test_hash_interruption_is_retried_without_incrementing_processed_or_failed(self):
        self.recording()
        busy = threading.Event()
        service = self.service(can_run=lambda: '주행 중' if busy.is_set() else None)
        from driver_history_library import _digest
        calls = []
        def interrupt(path, should_stop=None):
            calls.append(path)
            if len(calls) == 1:
                busy.set()
                raise InterruptedError('hash cancelled')
            return _digest(path, should_stop)
        with patch('driver_history_library._digest', side_effect=interrupt):
            service.start('iracing')
            waiting = self.waiting(service)
            self.assertEqual((waiting['processed'], waiting['failed']), (0, 0))
            busy.clear()
            final = self.terminal(service)
        self.assertEqual((final['processed'], final['updated'], final['failed']), (1, 1, 0))
        self.assertEqual(len(calls), 2)

    def test_callback_identity_mismatch_cannot_replace_recording(self):
        item = self.recording()
        def summarize(path, source_id, should_stop):
            return build_source_summary(deepcopy(item), 'f' * 64)
        service = self.service(summarize_file=summarize)
        service.start('iracing')
        state = self.terminal(service)
        self.assertEqual((state['updated'], state['failed']), (0, 1))
        self.assertFalse(self.store.path.exists())

    def test_callback_imported_reference_skip_is_checkpointed_without_loading(self):
        self.recording()
        summarize = Mock(return_value={'skip_reason': 'IMPORTED_REFERENCE'})
        service = self.service(summarize_file=summarize)
        service.start('iracing')
        self.assertEqual(self.terminal(service)['skipped'], 1)
        service.start('iracing')
        self.assertEqual(self.terminal(service)['skipped'], 1)
        summarize.assert_called_once()
        self.loader.assert_not_called()

    def test_completed_commit_is_checkpointed_before_waiting_for_next_file(self):
        self.recording('first.ibt')
        self.recording('second.ibt')
        busy = threading.Event()
        original = self.store.record_source
        def save(summary):
            result = original(summary)
            busy.set()
            return result
        service = self.service(can_run=lambda: '주행 중' if busy.is_set() else None)
        with patch.object(self.store, 'record_source', side_effect=save):
            service.start('iracing')
            waiting = self.waiting(service)
        self.assertEqual((waiting['processed'], waiting['updated'], waiting['failed']), (1, 1, 0))
        self.assertEqual(self.store.status('iracing')['source_count'], 1)
        busy.clear()
        final = self.terminal(service)
        self.assertEqual((final['processed'], final['updated']), (2, 2))

    def test_interrupted_callback_releases_loaded_arrays_before_waiting(self):
        item = self.recording()
        busy, started = threading.Event(), threading.Event()
        references, calls = [], []
        def summarize(path, source_id, should_stop):
            calls.append(path)
            if len(calls) == 1:
                samples = np.ones(100000)
                references.append(weakref.ref(samples))
                started.set()
                while not should_stop():
                    threading.Event().wait(.005)
                raise InterruptedError('discard full sample arrays')
            return build_source_summary(deepcopy(item), source_id)
        service = self.service(can_run=lambda: '주행 중' if busy.is_set() else None,
                               summarize_file=summarize)
        service.start('iracing')
        self.assertTrue(started.wait(2))
        busy.set()
        self.waiting(service)
        self.assertIsNone(references[0](), 'interrupted full arrays retained during driving')
        busy.clear()
        self.assertEqual(self.terminal(service)['updated'], 1)

    def test_resource_policy_error_waits_without_exposing_private_exception(self):
        self.recording()
        policy = Mock(side_effect=RuntimeError('PRIVATE_RESOURCE_PATH'))
        service = self.service(can_run=policy)
        service.start('iracing')
        waiting = self.waiting(service)
        self.assertEqual(waiting['processed'], 0)
        self.assertNotIn('PRIVATE_RESOURCE_PATH', json.dumps(waiting))
        policy.side_effect = None
        policy.return_value = None
        self.assertEqual(self.terminal(service)['updated'], 1)

    def test_callback_game_mismatch_is_not_inserted(self):
        item = self.recording()
        def summarize(path, source_id, should_stop):
            summary = build_source_summary(deepcopy(item), source_id)
            summary['game'] = 'lmu'
            return summary
        service = self.service(summarize_file=summarize)
        service.start('iracing')
        final = self.terminal(service)
        self.assertEqual((final['updated'], final['skipped']), (0, 1))
        self.assertFalse(self.store.path.exists())

    def test_result_save_failure_is_not_a_history_or_checkpoint_success_and_retries(self):
        self.recording()
        before = Mock(side_effect=RuntimeError('PRIVATE RESULT PATH'))
        after = Mock()
        service = self.service(before_save=before, after_save=after)
        service.start('iracing')
        state = self.terminal(service)
        self.assertEqual((state['updated'], state['failed']), (0, 1))
        self.assertEqual(state['reason_counts'], [{'code': 'SOURCE_RESULTS_SAVE_FAILED', 'count': 1}])
        self.assertNotIn('PRIVATE', json.dumps(state))
        self.assertFalse(self.store.path.exists())
        after.assert_not_called()
        before.side_effect = None
        service.start('iracing')
        self.assertEqual(self.terminal(service)['updated'], 1)
        self.assertEqual(self.loader.call_count, 2)
        after.assert_called_once()

    def test_callbacks_are_ordered_around_the_atomic_history_save(self):
        self.recording()
        events = []
        original = self.store.record_source
        def before(store, summary):
            self.assertIs(store, self.store)
            self.assertIn('race_results', summary)
            self.assertEqual(store.status('iracing')['source_count'], 0)
            events.append('results')
        def record(summary):
            events.append('history')
            return original(summary)
        def after(store, summary):
            self.assertEqual(store.status('iracing')['source_count'], 1)
            events.append('challenges')
        service = self.service(before_save=before, after_save=after)
        with patch.object(self.store, 'record_source', side_effect=record):
            service.start('iracing')
            self.assertEqual(self.terminal(service)['updated'], 1)
        self.assertEqual(events, ['results', 'history', 'challenges'])

    def test_failed_challenge_reconcile_keeps_checkpoint_and_exposes_retry_warning(self):
        self.recording()
        service = self.service(after_save=Mock(side_effect=RuntimeError('PRIVATE JOURNAL PATH')))
        with self.assertLogs('driver_progress', level='WARNING'):
            service.start('iracing')
            state = self.terminal(service)
        self.assertEqual((state['updated'], state['failed'], state['warning_count']), (1, 0, 1))
        self.assertEqual(state['reason_counts'], [{'code': 'CHALLENGE_RECONCILE_FAILED', 'count': 1}])
        self.assertIn('다시 확인', state['message'])
        self.assertNotIn('PRIVATE', json.dumps(state))
        self.loader.reset_mock()
        service.start('iracing')
        self.assertEqual(self.terminal(service)['skipped'], 1)
        self.loader.assert_not_called()

    def test_default_callbacks_create_no_results_or_driver_journal_database(self):
        self.recording()
        service = self.service()
        service.start('iracing')
        self.assertEqual(self.terminal(service)['updated'], 1)
        self.assertFalse(list(self.folder.rglob('*.results.sqlite3')))
        self.assertFalse(list(self.folder.rglob('*.journal.sqlite3')))

    def test_source_changed_while_results_are_saved_never_commits_history(self):
        item = self.recording()
        after = Mock()
        service = self.service(before_save=lambda *_: item.path.write_bytes(b'changed during result transaction'), after_save=after)
        service.start('iracing')
        state = self.terminal(service)
        self.assertEqual((state['updated'], state['skipped']), (0, 1))
        self.assertEqual(state['reason_counts'], [{'code': 'SOURCE_CHANGED', 'count': 1}])
        self.assertFalse(self.store.path.exists())
        after.assert_not_called()


if __name__ == '__main__':
    unittest.main()
