"""Explicit repair of already-connected summaries from their unchanged originals.

The analysis DB supplies locators only. It never creates new driving history,
and neither locators nor filenames are copied to the driver ledger or responses.
"""
from __future__ import annotations

import hashlib
from contextlib import closing
from datetime import datetime
from pathlib import Path
import sqlite3
import threading
from types import SimpleNamespace

from fastapi import HTTPException
from typing import Literal

from analysis_numeric import file_signature
from driver_history import DriverHistoryStore
from driver_history_index import _imported_reference
from driver_history_summary import build_source_summary
from driver_history_metrics import CALCULATION_VERSION
from driver_progress import attach_recording_results, notify_after_save


METRICS_VERSION = CALCULATION_VERSION
_EXTRACTION_FAILURES = {'METRICS_EXTRACTION_FAILED'}


def _stale_metrics(metrics):
    return (metrics.get('calculation_version') != METRICS_VERSION
            or bool(_EXTRACTION_FAILURES.intersection(metrics.get('driving', {}).get('reason_codes', []))))


def outdated_sources(snapshot):
    return {item['source_id'] for drive in snapshot['drives'] for item in drive['observations']
            if _stale_metrics(item.get('metrics', {}))}


def _locators(database, source_ids):
    database = Path(database)
    if not database.is_file() or not source_ids:
        return {}
    # mode=ro avoids the normal memory-store constructor and all its migrations.
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True, timeout=.2)) as db:
        columns = {row[1] for row in db.execute('PRAGMA table_info(sessions)')}
        if not {'file_hash', 'file_path'}.issubset(columns):
            return {}
        found = {}
        ordered = sorted(source_ids)
        for start in range(0, len(ordered), 400):
            batch = ordered[start:start + 400]
            marks = ','.join('?' for _ in batch)
            for source_id, path in db.execute(
                    f'SELECT DISTINCT file_hash,file_path FROM sessions WHERE file_hash IN ({marks})', batch):
                if isinstance(path, str) and path:
                    found.setdefault(source_id, []).append(Path(path))
        return found


def _digest(path, should_stop=None):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        while True:
            if should_stop is not None and should_stop():
                raise InterruptedError('History refresh hashing interrupted')
            block = source.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _checked_digest(path, hashes, should_stop=None):
    """Reuse a digest only while the same path/size/mtime signature survives."""
    signature = file_signature(path)
    key = str(path.resolve())
    cached = hashes.get(key)
    digest = (cached[1] if cached and cached[0] == signature else
              _digest(path, should_stop) if should_stop is not None else _digest(path))
    if file_signature(path) != signature:
        return signature, None
    hashes[key] = (signature, digest)
    return signature, digest


def _discover_originals(snapshot, source_ids, allowed, locators, hashes, progress=None,
                        checked_digest=None, wait_until_idle=None):
    """Narrow missing locators by native recording clocks, then prove SHA256.

    Older picker previews did not persist an analysis locator. Reading their IBT
    headers is cheap; no unrelated recording is parsed or added to the ledger.
    Undated sources without locators require explicit reselection rather than a
    full-library content scan. Native timestamps select candidates only.
    """
    missing = set(source_ids)
    dates = []
    for drive in snapshot['drives']:
        for observation in drive['observations']:
            if observation['source_id'] not in missing or not observation.get('recorded_at'):
                continue
            stamp = datetime.fromisoformat(observation['recorded_at'])
            dates.append(stamp.timestamp())
    if not dates:
        return
    from session_log_extras import recording_window
    for scanned, path in enumerate(allowed.values(), 1):
        if not missing:
            break
        if wait_until_idle:
            wait_until_idle()
        if progress:
            progress(scanned, len(allowed))
        try:
            window = recording_window(path)
            if window is None or not any(window[0] - .01 <= stamp <= window[1] + .01 for stamp in dates):
                continue
            _, source_id = (checked_digest(path) if checked_digest else _checked_digest(path, hashes))
            if source_id in missing:
                locators.setdefault(source_id, []).append(path)
                missing.remove(source_id)
        except InterruptedError:
            raise
        except Exception:
            continue


def refresh_summaries(game, *, store, memory_db_path, allowed_paths, load_telemetry=None, progress=None,
                      can_run=None, summarize_file=None, stop_event=None, before_save=None, after_save=None):
    """Refresh stale connected content only; changed/missing sources stay intact."""
    outcome = dict(game=game, status='RUNNING', total=0, processed=0,
                   updated=0, skipped=0, failed=0, warning_count=0, reason_counts=[], message='연결한 기록을 다시 확인하고 있습니다.')
    reasons = {}
    stop = stop_event if stop_event is not None else threading.Event()

    def publish():
        outcome['reason_counts'] = [{'code': key, 'count': value} for key, value in sorted(reasons.items())]
        if progress:
            progress({**outcome, 'reason_counts': [dict(item) for item in outcome['reason_counts']]})

    def busy_reason():
        if can_run is None:
            return None
        try:
            reason = can_run()
        except Exception:
            return 'STATE_UNAVAILABLE'
        if reason is None or reason == '':
            return None
        # Resource callbacks may return private diagnostics. Publish stable codes.
        return (reason if isinstance(reason, str) and reason in
                {'DRIVING', 'ANALYZING', 'RESOURCE_BUSY', 'COOLDOWN'} else 'BACKGROUND_BUSY')

    def interrupted():
        return stop.is_set() or busy_reason() is not None

    resume_message = outcome['message']

    def wait_until_idle():
        nonlocal resume_message
        while not stop.is_set():
            reason = busy_reason()
            if reason is None:
                if outcome.get('wait_reason') is not None:
                    outcome.update(wait_reason=None, message=resume_message)
                    publish()
                return
            if outcome.get('wait_reason') != reason:
                if outcome.get('wait_reason') is None:
                    resume_message = outcome['message']
                outcome.update(wait_reason=reason,
                    message='주행과 분석 작업이 끝나면 기존 기록 확인을 자동으로 이어갑니다.')
                publish()
            stop.wait(.2)
        raise InterruptedError('History refresh stopped')

    def when_idle(operation):
        while True:
            wait_until_idle()
            try:
                return operation()
            except InterruptedError:
                if stop.is_set():
                    raise
                # Retry this exact file after foreground work and cooldown.
                # No processed count or replacement summary has been committed.

    def check_interrupted():
        if interrupted():
            raise InterruptedError('History refresh measurement interrupted')

    publish()
    wait_until_idle()
    snapshot = store.report_snapshot(game)
    source_ids = outdated_sources(snapshot)
    outcome['total'] = len(source_ids)
    publish()
    if not source_ids:
        outcome.update(status='COMPLETE', message='연결한 기록이 현재 기준으로 확인되어 있습니다.')
        publish()
        return outcome
    wait_until_idle()
    try:
        locators = _locators(memory_db_path, source_ids)
    except (OSError, sqlite3.Error):
        # Locators are optional hints; native clocks and exact hashes still work.
        locators = {}
    # One configured-library scan. A saved path outside that library cannot be read.
    wait_until_idle()
    allowed = {}
    for path in allowed_paths(game):
        wait_until_idle()
        allowed[str(Path(path).resolve())] = Path(path)
    hashes, originals, lookup_reasons = {}, {}, {}

    def checked_digest(path):
        should_stop = interrupted if can_run is not None or stop_event is not None else None
        return when_idle(lambda: _checked_digest(path, hashes, should_stop))

    for source_id in sorted(source_ids):
        for saved in locators.get(source_id, []):
            try:
                wait_until_idle()
                path = allowed.get(str(saved.resolve()))
                if path is None or not path.is_file():
                    continue
                _, digest = checked_digest(path)
                if digest == source_id:
                    originals[source_id] = path
                    break
                lookup_reasons[source_id] = 'SOURCE_CHANGED'
            except InterruptedError:
                raise
            except Exception:
                lookup_reasons[source_id] = 'SOURCE_READ_FAILED'
    discovered = {}

    def locating(scanned, total):
        outcome['message'] = f'설정한 기록 폴더에서 원본을 확인하고 있습니다. {scanned}/{total}'
        publish()

    _discover_originals(snapshot, source_ids - originals.keys(), allowed, discovered, hashes, locating,
                        checked_digest=checked_digest, wait_until_idle=wait_until_idle)
    originals.update({source_id: paths[0] for source_id, paths in discovered.items()})
    outcome['message'] = '확인한 원본에서 연결 기록의 측정 근거를 갱신하고 있습니다.'
    publish()
    for source_id in sorted(source_ids):
        def refresh_one():
            path = originals.get(source_id)
            if path is None:
                return lookup_reasons.get(source_id, 'ORIGINAL_UNAVAILABLE')
            before, digest = checked_digest(path)
            if digest != source_id or file_signature(path) != before:
                return 'SOURCE_CHANGED'
            check_interrupted()
            if summarize_file is not None:
                # A compact child result carries no source path. Preserve the
                # existing exclusion of locally imported LMU reference files.
                if _imported_reference(SimpleNamespace(path=path, metadata={'game': game})):
                    return 'REFERENCE_OR_GAME_MISMATCH'
                summary = summarize_file(path, source_id, interrupted)
                check_interrupted()
                if summary == {'skip_reason': 'IMPORTED_REFERENCE'}:
                    return 'REFERENCE_OR_GAME_MISMATCH'
                if not isinstance(summary, dict) or summary.get('source_id') != source_id:
                    return 'SOURCE_READ_FAILED'
                if summary.get('game') != game:
                    return 'REFERENCE_OR_GAME_MISMATCH'
            else:
                telemetry = load_telemetry(path)
                check_interrupted()
                actual_game = 'lmu' if (telemetry.metadata or {}).get('game') == 'lmu' else 'iracing'
                if actual_game != game or _imported_reference(telemetry):
                    return 'REFERENCE_OR_GAME_MISMATCH'
                if tuple(telemetry.signature) != before or file_signature(path) != before:
                    return 'SOURCE_CHANGED'
                summary = build_source_summary(telemetry, source_id)
            if not summary.get('observations'):
                return 'OBSERVATIONS_UNAVAILABLE'
            if any(_stale_metrics(item.get('metrics', {})) for item in summary['observations']):
                return 'METRICS_EXTRACTION_FAILED'
            if file_signature(path) != before:
                return 'SOURCE_CHANGED'
            if summarize_file is None:
                summary = attach_recording_results(telemetry, summary)
            check_interrupted()
            if before_save is not None:
                try:
                    before_save(store, summary)
                except Exception:
                    return 'SOURCE_RESULTS_SAVE_FAILED'
            if file_signature(path) != before:
                return 'SOURCE_CHANGED'
            check_interrupted()
            store.record_source(summary)
            outcome['updated'] += 1
            warnings = notify_after_save(store, summary, after_save)
            for warning in warnings:
                reasons[warning] = reasons.get(warning, 0) + 1
            outcome['warning_count'] += bool(warnings)
            return None

        try:
            reason = when_idle(refresh_one)
        except InterruptedError:
            raise
        except Exception:
            reason = 'SOURCE_READ_FAILED'
        if reason:
            reasons[reason] = reasons.get(reason, 0) + 1
            if reason in {'SOURCE_READ_FAILED', 'METRICS_EXTRACTION_FAILED', 'SOURCE_RESULTS_SAVE_FAILED'}:
                outcome['failed'] += 1
            else:
                outcome['skipped'] += 1
        outcome['processed'] += 1
        publish()
    outcome.update(status='COMPLETE', message=(
        f"기록 {outcome['updated']}개를 갱신했습니다. "
        + ('확인하지 못한 원본은 보존했습니다. 해당 파일을 주행 선택에서 다시 분석해 주세요.'
           if outcome['skipped'] or outcome['failed'] else '드라이버 리포트에 반영했습니다.')
        + (' 도전 달성 자동 확인을 마치지 못했습니다. 드라이버 리포트에서 다시 확인해 주세요.' if outcome['warning_count'] else '')))
    publish()
    return outcome


class HistoryRefresh:
    """One background reader at a time; repeat requests observe the same job."""
    def __init__(self, *, memory_db_path, allowed_paths, load_telemetry=None,
                 store_factory=DriverHistoryStore, can_run=None, summarize_file=None,
                 before_save=None, after_save=None):
        self.memory_db_path = memory_db_path
        self.allowed_paths = allowed_paths
        self.load_telemetry = load_telemetry
        self.store_factory = store_factory
        self.can_run = can_run
        self.summarize_file = summarize_file
        self.before_save, self.after_save = before_save, after_save
        self._lock = threading.RLock()
        self._states = {}
        self._running = None
        self._stop = None
        self._thread = None

    def status(self, game):
        with self._lock:
            state = self._states.get(game, dict(game=game, status='IDLE', total=0, processed=0,
                updated=0, skipped=0, failed=0, reason_counts=[], message='기존 기록을 다시 확인할 수 있습니다.'))
            return {**state, 'reason_counts': [dict(item) for item in state['reason_counts']]}

    def start(self, game):
        with self._lock:
            if self._running:
                if self._running == game:
                    return self.status(game)
                raise HTTPException(409, '다른 게임의 기록을 확인 중입니다. 완료한 뒤 다시 시도해 주세요.')
            self._running, self._stop = game, threading.Event()
            self._states[game] = dict(game=game, status='RUNNING', total=0, processed=0,
                updated=0, skipped=0, failed=0, reason_counts=[], message='연결한 기록을 확인할 준비를 하고 있습니다.')
            try:
                self._thread = threading.Thread(target=self._run, args=(game, self._stop),
                                                name='driver-history-refresh', daemon=True)
                self._thread.start()
            except Exception:
                self._running = self._stop = self._thread = None
                self._states[game].update(status='FAILED',
                    message='기록 확인 작업을 시작하지 못했습니다. 기존 기록은 보존했습니다. 잠시 후 다시 시도해 주세요.')
            return self.status(game)

    def shutdown(self, wait=False, timeout=None):
        """Stop the owned measurement child and optionally join the reader thread."""
        with self._lock:
            if self._stop is not None:
                self._stop.set()
            thread = self._thread
        if wait and thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def _run(self, game, stop):
        def progress(state):
            with self._lock:
                self._states[game] = state
        try:
            refresh_summaries(game, store=self.store_factory(), memory_db_path=self.memory_db_path(),
                              allowed_paths=self.allowed_paths, load_telemetry=self.load_telemetry,
                              summarize_file=self.summarize_file, can_run=self.can_run,
                              stop_event=stop, progress=progress,
                              before_save=self.before_save, after_save=self.after_save)
        except InterruptedError:
            state = self.status(game)
            state.update(status='IDLE', wait_reason=None,
                message='기록 확인을 중단했습니다. 완료한 기록은 유지하며 다시 확인할 수 있습니다.')
            progress(state)
        except Exception:
            # Parser/library exceptions must never expose personal paths or data.
            state = self.status(game)
            state.update(status='FAILED', message='기록을 다시 확인하지 못했습니다. 기존 기록은 보존했습니다. 잠시 후 다시 시도해 주세요.')
            progress(state)
        finally:
            with self._lock:
                self._running = self._stop = None


def register_refresh_routes(app, service, *, blocked=None):
    @app.get('/api/driver-history/refresh')
    async def refresh_status(game: Literal['iracing', 'lmu'] = 'iracing'):
        return service.status(game)

    @app.post('/api/driver-history/refresh')
    async def refresh_start(game: Literal['iracing', 'lmu'] = 'iracing'):
        if blocked and blocked():
            raise HTTPException(409, '전체 파일을 백그라운드에서 확인 중입니다. 이 작업에서 기존 기록도 함께 갱신합니다.')
        return service.start(game)
