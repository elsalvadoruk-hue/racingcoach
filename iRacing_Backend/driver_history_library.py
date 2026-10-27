"""Incremental, local-only background import of the configured recording library.

One recording is parsed at a time without touching the selected analysis, AI,
downloads or the original files. Successful file checkpoints survive restarts in
a separate database; paths are hashed locally and never included in responses.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import sqlite3
import threading
from uuid import uuid4

from fastapi import HTTPException

from analysis_numeric import file_signature
from driver_history import DriverHistoryStore
from driver_history_index import _imported_reference
from driver_history_refresh import METRICS_VERSION, _stale_metrics, outdated_sources
from driver_history_summary import build_source_summary
from driver_progress import attach_recording_results, notify_after_save
from driver_results import CALCULATION_VERSION as RESULTS_VERSION
from driver_history_inputs import InputAttempts, current_inputs, measure_source_inputs, source_observations


LIBRARY_VERSION = f'driver-library-v2:{METRICS_VERSION}:{RESULTS_VERSION}'
_GAMES = {'iracing', 'lmu'}
_CACHEABLE_SKIPS = {'IMPORTED_REFERENCE', 'GAME_MISMATCH', 'OBSERVATIONS_UNAVAILABLE', 'UNSUPPORTED_FILE'}
_CHECKPOINT_COLUMNS = ('game', 'path_key', 'size', 'mtime_ns', 'version', 'source_id', 'outcome', 'reason')


def _game(game):
    if game not in _GAMES:
        raise ValueError('지원하지 않는 주행 게임입니다.')


def _path_key(path):
    return hashlib.sha256(os.path.normcase(str(path)).encode('utf-8')).hexdigest()


def _recording_in_use(path, game):
    return game == 'lmu' and Path(str(path) + '.wal').exists()


def _digest(path, should_stop=None):
    """Hash bounded chunks, releasing the file promptly when driving starts."""
    digest = hashlib.sha256()
    with path.open('rb') as source:
        while True:
            if should_stop is not None and should_stop():
                raise InterruptedError('Library hashing interrupted')
            block = source.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class _FileResult:
    action: str
    reason: str = ''
    signature: tuple | None = None
    source_id: str | None = None
    checkpoint: bool = False
    warnings: tuple = ()


class _Checkpoints:
    """A worker-owned connection, distinct from the strict observation ledger."""
    def __init__(self, ledger_path):
        path = Path(ledger_path)
        self.path = path.with_name(path.stem + '.library.sqlite3')

    @contextmanager
    def opened(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5.)
        try:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            if not tables and version == 0:
                connection.execute('CREATE TABLE files (game TEXT NOT NULL,path_key TEXT NOT NULL,'
                    'size INTEGER NOT NULL,mtime_ns INTEGER NOT NULL,version TEXT NOT NULL,'
                    'source_id TEXT,outcome TEXT NOT NULL,reason TEXT NOT NULL,PRIMARY KEY(game,path_key))')
                connection.execute('PRAGMA user_version=1')
                connection.commit()
            elif (tables != {'files'} or version != 1
                    or tuple(row[1] for row in connection.execute('PRAGMA table_info(files)')) != _CHECKPOINT_COLUMNS):
                raise ValueError('지원하지 않는 기록 가져오기 저장소입니다.')
            self.connection = connection
            yield self
        finally:
            connection.close()

    def get(self, game, path_key):
        row = self.connection.execute(
            'SELECT size,mtime_ns,version,source_id,outcome,reason FROM files WHERE game=? AND path_key=?',
            (game, path_key)).fetchone()
        return dict(zip(('size', 'mtime_ns', 'version', 'source_id', 'outcome', 'reason'), row)) if row else None

    def remember(self, game, path_key, result):
        self.connection.execute('INSERT INTO files VALUES(?,?,?,?,?,?,?,?) '
            'ON CONFLICT(game,path_key) DO UPDATE SET size=excluded.size,mtime_ns=excluded.mtime_ns,'
            'version=excluded.version,source_id=excluded.source_id,outcome=excluded.outcome,reason=excluded.reason',
            (game, path_key, result.signature[1], result.signature[2], LIBRARY_VERSION,
             result.source_id, result.action, result.reason))
        self.connection.commit()


def _current_sources(store, game, snapshot=None):
    # Read compact history once per run. Re-reading the entire ledger per file
    # would make an initial library import quadratic in the number of files.
    snapshot = store.report_snapshot(game) if snapshot is None else snapshot
    source_ids = {row['source_id'] for drive in snapshot['drives'] for row in drive['observations']}
    return source_ids - outdated_sources(snapshot)


def _enrich_file(path, game, source_id, signature, *, store, observations, attempts,
                 load_telemetry, enrich_file, should_stop):
    if attempts.failed(game, source_id):
        return _FileResult('failed', 'INPUT_ENRICHMENT_FAILED', signature, source_id, True)
    try:
        if enrich_file is not None:
            payload = enrich_file(path, source_id, observations, should_stop)
        else:
            telemetry = load_telemetry(path)
            if tuple(telemetry.signature) != signature:
                return _FileResult('skipped', 'SOURCE_CHANGED')
            payload = measure_source_inputs(telemetry, source_id, observations)
        if should_stop is not None and should_stop():
            raise InterruptedError('Input enrichment interrupted')
        if file_signature(path) != signature:
            return _FileResult('skipped', 'SOURCE_CHANGED')
        if _recording_in_use(path, game):
            return _FileResult('skipped', 'RECORDING_IN_USE')
        if payload.get('source_id') != source_id or payload.get('game') != game:
            raise ValueError('Input identity mismatch')
        store.enrich_source_inputs(payload)
        # Update this run's compact snapshot only after the atomic write.
        for observation in observations:
            observation['metrics']['input_version'] = payload['input_version']
        return _FileResult('updated', signature=signature, source_id=source_id, checkpoint=True)
    except InterruptedError:
        raise
    except Exception:
        if should_stop is not None and should_stop():
            raise InterruptedError('Input enrichment interrupted') from None
        if file_signature(path) != signature:
            return _FileResult('skipped', 'SOURCE_CHANGED')
        if _recording_in_use(path, game):
            return _FileResult('skipped', 'RECORDING_IN_USE')
        attempts.remember_failure(game, source_id)
        return _FileResult('failed', 'INPUT_ENRICHMENT_FAILED', signature, source_id, True)


def _import_file(path, game, *, store, journal, current, load_telemetry, summarize_file=None, should_stop=None,
                 before_save=None, after_save=None, rechecked=None, input_sources=None, input_attempts=None,
                 enrich_file=None):
    """Return compact progress only; the full telemetry dies before the next file."""
    def check_interrupted():
        if should_stop is not None and should_stop():
            raise InterruptedError('Library reading interrupted')

    try:
        check_interrupted()
        signature = file_signature(path)
        expected_suffix = '.duckdb' if game == 'lmu' else '.ibt'
        if path.suffix.casefold() != expected_suffix:
            return _FileResult('skipped', 'UNSUPPORTED_FILE', signature, checkpoint=True)
        # A DuckDB write-ahead log can contain still-changing recording data.
        # Do not open/recover that database or cache this temporary exclusion.
        if _recording_in_use(path, game):
            return _FileResult('skipped', 'RECORDING_IN_USE')
        cached = journal.get(game, _path_key(path))
        cache_matches = bool(cached and (cached['size'], cached['mtime_ns'], cached['version'])
                             == (*signature[1:], LIBRARY_VERSION))
        if cache_matches:
            if cached['source_id'] in current:
                observations = (input_sources or {}).get(cached['source_id'], [])
                if not observations or all(current_inputs(row['metrics']) for row in observations):
                    return _FileResult('skipped', 'ALREADY_INDEXED')
            if cached['reason'] in _CACHEABLE_SKIPS:
                return _FileResult('skipped', cached['reason'])
        source_id = cached['source_id'] if cache_matches and cached['source_id'] else _digest(path, should_stop)
        if file_signature(path) != signature:
            return _FileResult('skipped', 'SOURCE_CHANGED')
        if _recording_in_use(path, game):
            return _FileResult('skipped', 'RECORDING_IN_USE')
        if source_id in current and (cached is None or cache_matches or rechecked is not None and source_id in rechecked):
            observations = (input_sources or {}).get(source_id, [])
            if input_attempts is not None and observations and not all(current_inputs(row['metrics']) for row in observations):
                return _enrich_file(path, game, source_id, signature, store=store, observations=observations,
                    attempts=input_attempts, load_telemetry=load_telemetry, enrich_file=enrich_file, should_stop=should_stop)
            return _FileResult('skipped', 'ALREADY_INDEXED', signature, source_id, True)
        check_interrupted()
        if summarize_file is not None:
            # The production reader can own an interruptible process and return
            # only compact facts, releasing every full array on cancellation.
            summary = summarize_file(path, source_id, should_stop)
        else:
            # Kept for injected readers and isolated in-process tests.
            telemetry = load_telemetry(path)
            check_interrupted()
            if tuple(telemetry.signature) != signature or file_signature(path) != signature:
                return _FileResult('skipped', 'SOURCE_CHANGED')
            if _recording_in_use(path, game):
                return _FileResult('skipped', 'RECORDING_IN_USE')
            actual_game = 'lmu' if (telemetry.metadata or {}).get('game') == 'lmu' else 'iracing'
            if actual_game != game:
                return _FileResult('skipped', 'GAME_MISMATCH', signature, source_id, True)
            if _imported_reference(telemetry):
                return _FileResult('skipped', 'IMPORTED_REFERENCE', signature, source_id, True)
            summary = build_source_summary(telemetry, source_id)
        check_interrupted()
        if file_signature(path) != signature:
            return _FileResult('skipped', 'SOURCE_CHANGED')
        if _recording_in_use(path, game):
            return _FileResult('skipped', 'RECORDING_IN_USE')
        if summary.get('skip_reason') in {'IMPORTED_REFERENCE', 'GAME_MISMATCH'}:
            return _FileResult('skipped', summary['skip_reason'], signature, source_id, True)
        if summary.get('game') != game:
            return _FileResult('skipped', 'GAME_MISMATCH', signature, source_id, True)
        if summary.get('source_id') != source_id:
            return _FileResult('failed', 'SUMMARY_IDENTITY_MISMATCH')
        if not summary.get('observations'):
            return _FileResult('skipped', 'OBSERVATIONS_UNAVAILABLE', signature, source_id, True)
        if any(_stale_metrics(row.get('metrics', {})) for row in summary['observations']):
            return _FileResult('failed', 'METRICS_EXTRACTION_FAILED')
        if file_signature(path) != signature:
            return _FileResult('skipped', 'SOURCE_CHANGED')
        if _recording_in_use(path, game):
            return _FileResult('skipped', 'RECORDING_IN_USE')
        if summarize_file is None:
            summary = attach_recording_results(telemetry, summary)
        # Discard even a finished summary if foreground work started meanwhile.
        # Once the atomic ledger write begins, finish its checkpoint before yielding.
        check_interrupted()
        if before_save is not None:
            try:
                before_save(store, summary)
            except Exception:
                return _FileResult('failed', 'SOURCE_RESULTS_SAVE_FAILED')
        if file_signature(path) != signature:
            return _FileResult('skipped', 'SOURCE_CHANGED')
        if _recording_in_use(path, game):
            return _FileResult('skipped', 'RECORDING_IN_USE')
        check_interrupted()
        try:
            store.record_source(summary)
        except (OSError, sqlite3.Error, ValueError):
            return _FileResult('failed', 'SOURCE_SAVE_FAILED')
        current.add(source_id)
        if input_sources is not None:
            input_sources[source_id] = summary['observations']
        if rechecked is not None:
            rechecked.add(source_id)
        warnings = notify_after_save(store, summary, after_save)
        return _FileResult('updated', signature=signature, source_id=source_id, checkpoint=True, warnings=tuple(warnings))
    except InterruptedError:
        raise
    except Exception:
        # Parser errors may include private paths, names or original payloads.
        return _FileResult('failed', 'SOURCE_READ_FAILED')


class HistoryLibrary:
    """One sequential worker across both games, with durable per-file resumption."""
    def __init__(self, *, allowed_paths, load_telemetry=None, store_factory=DriverHistoryStore,
                 can_run=None, summarize_file=None, before_save=None, after_save=None, enrich_file=None):
        self.allowed_paths = allowed_paths
        self.load_telemetry = load_telemetry
        self.store_factory = store_factory
        self.can_run = can_run
        self.summarize_file = summarize_file
        self.enrich_file = enrich_file
        self.before_save, self.after_save = before_save, after_save
        self._lock = threading.RLock()
        self._states = {}
        self._running = None
        self._stop = None
        self._thread = None
        self._revision = 0

    @staticmethod
    def _empty(game):
        return dict(game=game, status='IDLE', job_id=None, total=0, processed=0,
                    updated=0, skipped=0, failed=0, warning_count=0, reason_counts=[], revision=0, wait_reason=None,
                    message='기존 주행 파일 전체를 백그라운드에서 가져올 수 있습니다.')

    def _publish(self, game, state):
        with self._lock:
            state = {**state, 'reason_counts': [dict(item) for item in state['reason_counts']]}
            if self._stop and self._stop.is_set() and state['status'] in {'SCANNING', 'RUNNING', 'WAITING'}:
                state.update(status='PAUSING', wait_reason=None, message='기록 가져오기를 중단하고 있습니다.')
            self._revision += 1
            state['revision'] = self._revision
            self._states[game] = state

    def status(self, game):
        _game(game)
        with self._lock:
            state = self._states.get(game, self._empty(game))
            return {**state, 'busy_game': self._running,
                    'reason_counts': [dict(item) for item in state['reason_counts']]}

    def start(self, game):
        _game(game)
        with self._lock:
            if self._running:
                if self._running == game:
                    return self.status(game)
                raise HTTPException(409, '다른 게임의 기록을 가져오는 중입니다. 완료 또는 일시정지 후 시작할 수 있습니다.')
            self._running, self._stop = game, threading.Event()
            state = {**self._empty(game), 'status': 'SCANNING', 'job_id': uuid4().hex,
                     'message': '설정한 폴더에서 전체 주행 파일을 확인하고 있습니다.'}
            self._publish(game, state)
            try:
                self._thread = threading.Thread(target=self._run, args=(game, state, self._stop),
                                                name='driver-history-library', daemon=True)
                self._thread.start()
            except Exception:
                self._running = self._stop = self._thread = None
                state.update(status='FAILED', message='기록 가져오기를 시작하지 못했습니다. 다시 시도해 주세요.')
                self._publish(game, state)
            return self.status(game)

    def pause(self, game):
        _game(game)
        with self._lock:
            if self._running == game:
                self._stop.set()
                self._publish(game, {**self._states[game], 'status': 'PAUSING',
                                    'wait_reason': None, 'message': '기록 가져오기를 중단하고 있습니다.'})
            return self.status(game)

    def shutdown(self, wait=False, timeout=None):
        """Cooperative stop for app shutdown; committed files resume next start."""
        with self._lock:
            if self._running:
                self.pause(self._running)
            thread = self._thread
        if wait and thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def _busy_reason(self):
        if self.can_run is None:
            return None
        try:
            reason = self.can_run()
        except Exception:
            # A failed resource check must not accidentally start heavy work.
            return '주행과 분석 상태를 확인할 때까지 기다리고 있습니다.'
        if reason is None or reason == '':
            return None
        return ' '.join(reason.split())[:180] if isinstance(reason, str) else '주행과 분석 작업이 끝나기를 기다리고 있습니다.'

    def _wait_until_idle(self, game, state, stop, *, scanning=False):
        while not stop.is_set():
            reason = self._busy_reason()
            if reason is None:
                if state['status'] == 'WAITING':
                    state.update(status='SCANNING' if scanning else 'RUNNING', wait_reason=None,
                        message=('설정한 폴더에서 전체 주행 파일을 확인하고 있습니다.' if scanning else
                                 '기존 주행 파일을 하나씩 분석하고 있습니다. 다른 화면을 사용해도 계속됩니다.'))
                    self._publish(game, state)
                return True
            if state['status'] != 'WAITING' or state.get('wait_reason') != reason:
                state.update(status='WAITING', wait_reason=reason,
                             message='주행과 분석 작업이 끝나면 기록 가져오기를 자동으로 이어갑니다.')
                self._publish(game, state)
            stop.wait(.5)
        return False

    def _run(self, game, state, stop):
        try:
            if not self._wait_until_idle(game, state, stop, scanning=True):
                state.update(status='PAUSED', wait_reason=None,
                             message='기록 가져오기를 일시정지했습니다. 이어서 가져올 수 있습니다.')
                return
            store = self.store_factory()
            snapshot = store.report_snapshot(game)
            current = _current_sources(store, game, snapshot)
            input_sources = source_observations(snapshot)
            rechecked = set()
            paths = {}
            for candidate in self.allowed_paths(game):
                if not self._wait_until_idle(game, state, stop, scanning=True):
                    break
                path = Path(candidate).resolve()
                paths.setdefault(os.path.normcase(str(path)), path)
                if len(paths) % 100 == 0:
                    state['total'] = len(paths)
                    self._publish(game, state)
            state.update(status='RUNNING', total=len(paths), wait_reason=None,
                         message='기존 주행 파일을 하나씩 분석하고 있습니다. 다른 화면을 사용해도 계속됩니다.')
            self._publish(game, state)
            reasons = {}
            if paths and not stop.is_set():
                with _Checkpoints(store.path).opened() as journal, InputAttempts(store.path).opened() as input_attempts:
                    for path in paths.values():
                        while self._wait_until_idle(game, state, stop):
                            try:
                                result = _import_file(path, game, store=store, journal=journal, current=current,
                                    rechecked=rechecked,
                                    load_telemetry=self.load_telemetry, summarize_file=self.summarize_file,
                                    input_sources=input_sources, input_attempts=input_attempts, enrich_file=self.enrich_file,
                                    should_stop=lambda: stop.is_set() or self._busy_reason() is not None,
                                    before_save=self.before_save, after_save=self.after_save)
                                break
                            except InterruptedError:
                                # Yield and retry this exact file; no failure,
                                # processed count or successful checkpoint exists.
                                continue
                        else:
                            break
                        state[result.action] += 1
                        state['processed'] += 1
                        if result.reason:
                            reasons[result.reason] = reasons.get(result.reason, 0) + 1
                        for warning in result.warnings:
                            reasons[warning] = reasons.get(warning, 0) + 1
                        state['warning_count'] += bool(result.warnings)
                        state['reason_counts'] = [{'code': code, 'count': count} for code, count in sorted(reasons.items())]
                        if result.checkpoint:
                            journal.remember(game, _path_key(path), result)
                        self._publish(game, state)
            if stop.is_set():
                state.update(status='PAUSED', wait_reason=None, message='기록 가져오기를 일시정지했습니다. 완료한 파일은 유지하며 이어서 가져올 수 있습니다.')
            else:
                state.update(status='COMPLETE', wait_reason=None, message=(
                    f"전체 {state['total']}개 확인을 마쳤습니다. 반영 {state['updated']}개 · 재사용/생략 {state['skipped']}개 · 실패 {state['failed']}개."
                    + (' 입력 보완 실패는 기존 기록 다시 확인에서, 새 파일 가져오기 실패는 다시 가져오기에서 재시도할 수 있습니다.' if state['failed'] else '')
                    + (' 도전 달성 자동 확인을 마치지 못했습니다. 드라이버 리포트에서 다시 확인해 주세요.' if state['warning_count'] else '')))
        except Exception:
            state.update(status='FAILED', wait_reason=None, message='기록 가져오기를 마치지 못했습니다. 완료한 기록은 보존했으며 다시 시도할 수 있습니다.')
        finally:
            # Publish terminal state and release the slot together. A new start
            # immediately after COMPLETE must create a new discovery job.
            with self._lock:
                self._running = self._stop = None
                self._publish(game, state)
