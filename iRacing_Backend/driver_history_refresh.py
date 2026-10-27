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

from fastapi import HTTPException
from typing import Literal

from analysis_numeric import file_signature
from driver_history import DriverHistoryStore
from driver_history_index import _imported_reference
from driver_history_summary import build_source_summary


METRICS_VERSION = 'observed-metrics-v2'
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


def _digest(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _checked_digest(path, hashes):
    """Reuse a digest only while the same path/size/mtime signature survives."""
    signature = file_signature(path)
    key = str(path.resolve())
    cached = hashes.get(key)
    digest = cached[1] if cached and cached[0] == signature else _digest(path)
    if file_signature(path) != signature:
        return signature, None
    hashes[key] = (signature, digest)
    return signature, digest


def _discover_originals(snapshot, source_ids, allowed, locators, hashes, progress=None):
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
        if progress:
            progress(scanned, len(allowed))
        try:
            window = recording_window(path)
            if window is None or not any(window[0] - .01 <= stamp <= window[1] + .01 for stamp in dates):
                continue
            _, source_id = _checked_digest(path, hashes)
            if source_id in missing:
                locators.setdefault(source_id, []).append(path)
                missing.remove(source_id)
        except Exception:
            continue


def refresh_summaries(game, *, store, memory_db_path, allowed_paths, load_telemetry, progress=None):
    """Refresh stale connected content only; changed/missing sources stay intact."""
    snapshot = store.report_snapshot(game)
    source_ids = outdated_sources(snapshot)
    outcome = dict(game=game, status='RUNNING', total=len(source_ids), processed=0,
                   updated=0, skipped=0, failed=0, reason_counts=[], message='연결한 기록을 다시 확인하고 있습니다.')
    reasons = {}

    def publish():
        outcome['reason_counts'] = [{'code': key, 'count': value} for key, value in sorted(reasons.items())]
        if progress:
            progress({**outcome, 'reason_counts': [dict(item) for item in outcome['reason_counts']]})

    publish()
    if not source_ids:
        outcome.update(status='COMPLETE', message='연결한 기록이 현재 기준으로 확인되어 있습니다.')
        publish()
        return outcome
    try:
        locators = _locators(memory_db_path, source_ids)
    except (OSError, sqlite3.Error):
        # Locators are optional hints; native clocks and exact hashes still work.
        locators = {}
    # One configured-library scan. A saved path outside that library cannot be read.
    allowed = {str(Path(path).resolve()): Path(path) for path in allowed_paths(game)}
    hashes, originals, lookup_reasons = {}, {}, {}
    for source_id in sorted(source_ids):
        for saved in locators.get(source_id, []):
            try:
                path = allowed.get(str(saved.resolve()))
                if path is None or not path.is_file():
                    continue
                _, digest = _checked_digest(path, hashes)
                if digest == source_id:
                    originals[source_id] = path
                    break
                lookup_reasons[source_id] = 'SOURCE_CHANGED'
            except Exception:
                lookup_reasons[source_id] = 'SOURCE_READ_FAILED'
    discovered = {}

    def locating(scanned, total):
        outcome['message'] = f'설정한 기록 폴더에서 원본을 확인하고 있습니다. {scanned}/{total}'
        publish()

    _discover_originals(snapshot, source_ids - originals.keys(), allowed, discovered, hashes, locating)
    originals.update({source_id: paths[0] for source_id, paths in discovered.items()})
    outcome['message'] = '확인한 원본에서 연결 기록의 측정 근거를 갱신하고 있습니다.'
    publish()
    for source_id in sorted(source_ids):
        reason = None
        try:
            path = originals.get(source_id)
            if path is None:
                reason = lookup_reasons.get(source_id, 'ORIGINAL_UNAVAILABLE')
            else:
                before, digest = _checked_digest(path, hashes)
                if digest != source_id or file_signature(path) != before:
                    reason = 'SOURCE_CHANGED'
                else:
                    telemetry = load_telemetry(path)
                    actual_game = 'lmu' if (telemetry.metadata or {}).get('game') == 'lmu' else 'iracing'
                    if actual_game != game or _imported_reference(telemetry):
                        reason = 'REFERENCE_OR_GAME_MISMATCH'
                    elif tuple(telemetry.signature) != before or file_signature(path) != before:
                        reason = 'SOURCE_CHANGED'
                    else:
                        summary = build_source_summary(telemetry, source_id)
                        if not summary.get('observations'):
                            reason = 'OBSERVATIONS_UNAVAILABLE'
                        elif any(_stale_metrics(item.get('metrics', {})) for item in summary['observations']):
                            reason = 'METRICS_EXTRACTION_FAILED'
                        elif file_signature(path) != before:
                            reason = 'SOURCE_CHANGED'
                        else:
                            store.record_source(summary)
                            outcome['updated'] += 1
        except Exception:
            reason = 'SOURCE_READ_FAILED'
        if reason:
            reasons[reason] = reasons.get(reason, 0) + 1
            if reason in {'SOURCE_READ_FAILED', 'METRICS_EXTRACTION_FAILED'}:
                outcome['failed'] += 1
            else:
                outcome['skipped'] += 1
        outcome['processed'] += 1
        publish()
    outcome.update(status='COMPLETE', message=(
        f"기록 {outcome['updated']}개를 갱신했습니다. "
        + ('확인하지 못한 원본은 보존했습니다. 해당 파일을 주행 선택에서 다시 분석해 주세요.'
           if outcome['skipped'] or outcome['failed'] else '드라이버 리포트에 반영했습니다.')))
    publish()
    return outcome


class HistoryRefresh:
    """One background reader at a time; repeat requests observe the same job."""
    def __init__(self, *, memory_db_path, allowed_paths, load_telemetry, store_factory=DriverHistoryStore):
        self.memory_db_path = memory_db_path
        self.allowed_paths = allowed_paths
        self.load_telemetry = load_telemetry
        self.store_factory = store_factory
        self._lock = threading.RLock()
        self._states = {}
        self._running = None

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
            self._running = game
            self._states[game] = dict(game=game, status='RUNNING', total=0, processed=0,
                updated=0, skipped=0, failed=0, reason_counts=[], message='연결한 기록을 확인할 준비를 하고 있습니다.')
            try:
                thread = threading.Thread(target=self._run, args=(game,), name='driver-history-refresh', daemon=True)
                thread.start()
            except Exception:
                self._running = None
                self._states[game].update(status='FAILED',
                    message='기록 확인 작업을 시작하지 못했습니다. 기존 기록은 보존했습니다. 잠시 후 다시 시도해 주세요.')
            return self.status(game)

    def _run(self, game):
        def progress(state):
            with self._lock:
                self._states[game] = state
        try:
            refresh_summaries(game, store=self.store_factory(), memory_db_path=self.memory_db_path(),
                              allowed_paths=self.allowed_paths, load_telemetry=self.load_telemetry, progress=progress)
        except Exception:
            # Parser/library exceptions must never expose personal paths or data.
            state = self.status(game)
            state.update(status='FAILED', message='기록을 다시 확인하지 못했습니다. 기존 기록은 보존했습니다. 잠시 후 다시 시도해 주세요.')
            progress(state)
        finally:
            with self._lock:
                self._running = None


def register_refresh_routes(app, service):
    @app.get('/api/driver-history/refresh')
    async def refresh_status(game: Literal['iracing', 'lmu'] = 'iracing'):
        return service.status(game)

    @app.post('/api/driver-history/refresh')
    async def refresh_start(game: Literal['iracing', 'lmu'] = 'iracing'):
        return service.start(game)
