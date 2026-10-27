"""Source-bound native race classifications, separate from the driving ledger.

iRacing's YAML ResultsPositions.ClassPosition is ZERO based; Position is ONE
based. Live PlayerCarClassPosition/CarIdxClassPosition do not establish a final
classification. Contract references (read without user accounts):
https://github.com/NickThissen/iRacingSdkWrapper/blob/master/iRacingSimulator/Drivers/DriverResults.cs
https://github.com/bengsfort/irsdk-node/blob/main/packages/irsdk-node-native/src/mock-data/session.json

Imports are pure. Only record_batch creates a database; no raw YAML, user names,
native account IDs, file names or source paths are retained.
"""
from __future__ import annotations

from contextlib import contextmanager
from collections import OrderedDict
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
from types import SimpleNamespace


SCHEMA_VERSION = 1
CALCULATION_VERSION = 'native-results-v2'
_SHA = re.compile(r'^[a-f0-9]{64}$')
_CODE = re.compile(r'^[A-Z][A-Z0-9_]{0,79}$')
_FIELDS = {'id', 'game', 'session_id', 'driver_id', 'drive_id', 'recorded_at', 'date_source',
           'status', 'authority', 'identity_status', 'class_position', 'overall_position',
           'finish_status', 'source_kind', 'labels', 'reason_codes', 'reason'}
_COLUMNS = {'sources': ('source_id', 'game', 'coverage'),
            'results': ('source_id', 'id', 'game', 'recorded_at', 'payload')}
_REASONS = {
    'FINAL_CLASSIFICATION_UNAVAILABLE': '공식 최종 클래스 결과가 원본에 없습니다.',
    'NATIVE_IDENTITY_UNAVAILABLE': '네이티브 세션 또는 본인 식별 근거가 부족합니다.',
    'OWN_RESULT_UNAVAILABLE': '본인 차량의 결과 행을 하나로 확인할 수 없습니다.',
    'OWN_DRIVER_UNCONFIRMED': '본인 운전자 연결을 확인할 수 없습니다.',
    'CLASS_POSITION_UNAVAILABLE': '최종 클래스 순위를 확인할 수 없습니다.',
    'INVALID_CLASSIFICATION': '클래스 순위 근거가 서로 일치하지 않습니다.',
    'FINISH_REASON_UNAVAILABLE': '완주 또는 리타이어 사유가 원본에 없습니다.',
    'TEAM_PARTICIPATION_UNCONFIRMED': '팀 경기의 본인 참가 근거를 확인할 수 없습니다.',
    'RECORDING_UTC_UNAVAILABLE': '원본 기록의 UTC 날짜가 없습니다.',
    'CONFLICTING_FINAL_RESULTS': '동일한 시점의 공식 최종 결과가 서로 다릅니다.',
    'NOT_RACE': '경기 세션이 아닙니다.',
    'IMPORTED_REFERENCE': '가져온 비교 기준 기록은 개인 결과에서 제외합니다.',
    'AMBIGUOUS_SESSION': '원본의 세션 번호가 중복되어 연결을 보류합니다.',
    'REPLAY_EXCLUDED': '실시간 리플레이 관측은 새 경기 결과로 보관하지 않습니다.',
    'NO_RECORDED_RACE_RESULT': '이 원본에서 본인의 경기 결과를 확인하지 못했습니다. 예정된 경기 항목은 결과로 보관하지 않습니다.',
    'RACE_FINISH_UNCONFIRMED': '본인 순위표를 관측했지만 경기 종료 여부는 확인하지 못했습니다.',
    'NATIVE_FINISH_OBSERVED': '본인 결과 행과 같은 경기의 쿨다운 상태를 확인했습니다. 공식 최종 결과는 별도 확인이 필요합니다.',
}


def _fail():
    raise ValueError('경기 결과 근거의 형식이 올바르지 않습니다.')


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _rows(value):
    return value if isinstance(value, (list, tuple)) else ()


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':')).encode('utf-8')).hexdigest()


def _hash(value):
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        _fail()
    return value


def _integer(value, minimum=0, maximum=2147483647):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return int(number) if math.isfinite(number) and number.is_integer() and minimum <= number <= maximum else None


def _flag(value):
    return value is True or type(value) in (int, float) and value == 1


def _stamp(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        value = value.isoformat()
    if not isinstance(value, str) or len(value) > 48:
        _fail()
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            _fail()
        return stamp.astimezone(timezone.utc).isoformat(timespec='microseconds')
    except (ValueError, OverflowError):
        _fail()


def _label(value):
    value = ' '.join(str(value or '').split())[:120]
    if re.match(r'^[A-Za-z]:', value) or value.startswith(('/', '\\')) or '://' in value:
        return ''
    return value


def _reason(codes):
    return ' '.join(_REASONS.get(code, '경기 결과의 근거를 확인할 수 없습니다.') for code in codes)


def _finish(row):
    reason = str(row.get('ReasonOutStr') or '').strip().casefold()
    if reason in {'disqualified', 'disqualification', 'dsq', 'dq'}:
        return 'DSQ'
    retired = {'disconnected', 'disconnect', 'retired', 'retirement', 'dnf', 'did not finish',
               'accident', 'crash', 'damage', 'mechanical', 'engine', 'out of fuel', 'tire failure'}
    if reason in retired:
        return 'DNF'
    out = _integer(row.get('ReasonOutId'))
    if out is not None and out > 0:
        # Nonzero means out, but alone cannot distinguish DSQ from retirement.
        # Do not invent a finish cause from an unverified numeric cause map.
        return 'UNKNOWN'
    if out == 0:
        return 'CLASSIFIED'
    return 'UNKNOWN'


def _last(channels, name):
    try:
        values = channels.get(name)
        return values[-1] if values is not None and len(values) else None
    except (TypeError, KeyError, IndexError):
        return None


def _recording_sessions(channels):
    """Use the original SessionNum axis, not the YAML's future session list."""
    import numpy as np
    try:
        nums = np.asarray(channels.get('SessionNum'), dtype=float)
    except (TypeError, ValueError, OverflowError):
        return None, {}
    if nums.ndim != 1 or not len(nums):
        return None, {}
    values = np.unique(nums[np.isfinite(nums)])
    observed = {number for value in values if (number := _integer(value, 0, 1023)) is not None}
    samples = {}
    for number in observed:
        index = int(np.flatnonzero(nums == number)[-1])
        sample = {'session_num': number}
        for key, channel in (('session_state', 'SessionState'), ('is_replay', 'IsReplayPlaying'),
                             ('finish_status', 'PlayerFinishStatus'), ('class_position', 'PlayerCarClassPosition'),
                             ('position', 'PlayerCarPosition')):
            try:
                array = channels.get(channel)
                if array is not None and len(array) == len(nums):
                    value = array[index]
                    sample[key] = value.item() if hasattr(value, 'item') else value
            except (IndexError, TypeError):
                pass
        samples[number] = sample
    return observed, samples


def _extract(info, source_id, game, source_kind, recorded_at, date_source, *, sample=None, metadata=None,
             observed_sessions=None, session_samples=None):
    _hash(source_id)
    if game not in {'iracing', 'lmu'}:
        _fail()
    recorded_at = _stamp(recorded_at)
    info, metadata, sample = _mapping(info), _mapping(metadata), _mapping(sample)
    weekend, drivers = _mapping(info.get('WeekendInfo')), _mapping(info.get('DriverInfo'))
    own_idx = _integer(drivers.get('DriverCarIdx'), 0, 1023)
    owners = [row for row in _rows(drivers.get('Drivers')) if isinstance(row, dict)
              and own_idx is not None and _integer(row.get('CarIdx'), 0, 1023) == own_idx]
    own = owners[0] if len(owners) == 1 else {}
    user_id = _integer(own.get('UserID'), 1)
    native_session = [_integer(weekend.get(name), 1) for name in ('SessionID', 'SubSessionID')]
    sessions = [row for row in _rows(_mapping(info.get('SessionInfo')).get('Sessions'))
                if isinstance(row, dict) and str(row.get('SessionType') or '').strip().casefold() == 'race']
    labels = {'car': _label(own.get('CarScreenName') or metadata.get('car_name')),
              'track': _label(weekend.get('TrackDisplayName') or metadata.get('track_name') or weekend.get('TrackName'))}
    results, coverage_reasons = [], set()
    if metadata.get('imported') is True:
        return dict(schema_version=1, source_id=source_id, game=game, results=[],
                    coverage=dict(status='UNAVAILABLE', reason_codes=['IMPORTED_REFERENCE']))
    numbers = [_integer(row.get('SessionNum')) for row in sessions]
    for index, session in enumerate(sessions):
        number = numbers[index]
        if number is not None and numbers.count(number) > 1:
            coverage_reasons.add('AMBIGUOUS_SESSION')
            continue
        positions = [row for row in _rows(session.get('ResultsPositions')) if isinstance(row, dict)]
        own_rows = [row for row in positions if own_idx is not None and _integer(row.get('CarIdx'), 0, 1023) == own_idx]
        final = _flag(session.get('ResultsOfficial')) if game == 'iracing' else False
        # An event's YAML includes future Race sessions while recording Practice.
        # Only a final native own result can stand independently of the recorded
        # session axis. A scheduled/empty table is not a result observation.
        if ((observed_sessions is not None and number not in observed_sessions and not (final and len(own_rows) == 1))
                or game == 'iracing' and not own_rows):
            coverage_reasons.add('NO_RECORDED_RACE_RESULT')
            continue
        bound_sample = _mapping(_mapping(session_samples).get(number))
        if not bound_sample and _integer(sample.get('session_num')) == number:
            bound_sample = sample
        native = game == 'iracing' and all(native_session) and user_id is not None and number is not None
        session_id = _digest(['result-session', game, *native_session, number]) if native else _digest(['source-session', source_id, index])
        driver_id = _digest(['result-driver', game, user_id]) if user_id else _digest(['source-driver', source_id, own_idx])
        reason_codes = set()
        if not native:
            reason_codes.add('NATIVE_IDENTITY_UNAVAILABLE')
        if len(owners) != 1 or _flag(drivers.get('DriverIsSpectator')) or _flag(own.get('IsSpectator')):
            reason_codes.add('OWN_DRIVER_UNCONFIRMED')
        if _flag(weekend.get('TeamRacing')):
            reason_codes.add('TEAM_PARTICIPATION_UNCONFIRMED')
        if recorded_at is None:
            reason_codes.add('RECORDING_UTC_UNAVAILABLE')
        result_row = own_rows[0] if len(own_rows) == 1 else {}
        class_position, overall_position, finish_status = None, None, 'UNKNOWN'
        if game == 'iracing':
            if not final:
                reason_codes.add('FINAL_CLASSIFICATION_UNAVAILABLE')
            if len(own_rows) != 1:
                reason_codes.add('OWN_RESULT_UNAVAILABLE')
            raw_class = _integer(result_row.get('ClassPosition'), 0, 1023)
            class_position = raw_class+1 if raw_class is not None else None
            overall_position = _integer(result_row.get('Position'), 1, 1024)
            own_class = _integer(own.get('CarClassID'))
            class_indices = {_integer(row.get('CarIdx'), 0, 1023) for row in _rows(drivers.get('Drivers'))
                             if isinstance(row, dict) and own_class is not None
                             and _integer(row.get('CarClassID')) == own_class}
            rank_conflict = any(_integer(row.get('CarIdx'), 0, 1023) != own_idx and
                ((raw_class is not None and _integer(row.get('CarIdx'), 0, 1023) in class_indices
                  and _integer(row.get('ClassPosition'), 0, 1023) == raw_class)
                 or (overall_position is not None and _integer(row.get('Position'), 1, 1024) == overall_position))
                for row in positions)
            if class_position is None:
                reason_codes.add('CLASS_POSITION_UNAVAILABLE')
            elif rank_conflict or overall_position is not None and class_position > overall_position:
                reason_codes.add('INVALID_CLASSIFICATION')
                class_position = None
            if result_row.get('UserID') is not None and _integer(result_row.get('UserID'), 1) != user_id:
                reason_codes.add('OWN_DRIVER_UNCONFIRMED')
            finish_status = _finish(result_row)
            if not final and finish_status == 'CLASSIFIED':
                # ReasonOutId=0 / Running also occurs in an ongoing table.
                # Checkered alone belongs to the leader, so require native
                # cooldown for this exact session and a participating own row.
                finished = (_integer(bound_sample.get('session_state')) == 6
                    and bound_sample.get('is_replay') is False
                    and (_integer(result_row.get('LapsComplete'), 1) is not None))
                finish_status = 'FINISHED' if finished else 'UNKNOWN'
                reason_codes.add('NATIVE_FINISH_OBSERVED' if finished else 'RACE_FINISH_UNCONFIRMED')
            if finish_status == 'UNKNOWN':
                reason_codes.add('FINISH_REASON_UNAVAILABLE')
            blockers = {'NATIVE_IDENTITY_UNAVAILABLE', 'OWN_DRIVER_UNCONFIRMED', 'TEAM_PARTICIPATION_UNCONFIRMED',
                        'FINAL_CLASSIFICATION_UNAVAILABLE', 'OWN_RESULT_UNAVAILABLE', 'CLASS_POSITION_UNAVAILABLE',
                        'INVALID_CLASSIFICATION'}
            official = not blockers.intersection(reason_codes)
            authority = ('IRACING_FINAL_CLASSIFICATION' if final else
                         'IRACING_FINISH_OBSERVATION' if finish_status in {'FINISHED', 'DNF', 'DSQ'} else
                         'IRACING_RESULT_TABLE' if len(own_rows) == 1 else 'UNVERIFIED')
        else:
            final, official = False, False
            reason_codes.add('FINAL_CLASSIFICATION_UNAVAILABLE')
            # A file can contain several session definitions. Native finish
            # fields apply to the recorded/current session, never all of them.
            lmu_sample = (bound_sample or sample) if len(sessions) == 1 else bound_sample
            native_finish = _integer(lmu_sample.get('finish_status'), 0, 3)
            finish_status = {1: 'FINISHED', 2: 'DNF', 3: 'DSQ'}.get(native_finish, 'UNKNOWN')
            class_position = _integer(lmu_sample.get('class_position'), 1, 104)
            overall_position = _integer(lmu_sample.get('position'), 1, 104)
            authority = 'NATIVE_FINISH_OBSERVATION' if native_finish in (1, 2, 3) else 'UNVERIFIED'
        drive_id = None
        if native:
            from driver_history_summary import _context, _digest as history_digest
            *_, history_native, _ = _context(SimpleNamespace(metadata=metadata, session_info=info), game)
            if history_native is not None:
                drive_id = history_digest(['native', *history_native, number])
        codes = sorted(reason_codes)
        results.append(dict(id=_digest(['result', session_id, driver_id]), game=game,
            session_id=session_id, driver_id=driver_id, drive_id=drive_id, recorded_at=recorded_at,
            date_source=date_source if recorded_at else 'UNKNOWN', status='OFFICIAL' if official else 'OBSERVATION',
            authority=authority, identity_status='NATIVE' if native else 'SOURCE_ONLY',
            class_position=class_position, overall_position=overall_position, finish_status=finish_status,
            source_kind=source_kind, labels=labels, reason_codes=codes, reason=_reason(codes)))
        coverage_reasons.update(codes)
    if not sessions:
        coverage_reasons.add('NOT_RACE')
    return dict(schema_version=1, source_id=source_id, game=game, results=results,
        coverage=dict(status='OBSERVED' if results and all(row['status'] == 'OFFICIAL' for row in results) else
                      'PARTIAL' if results else 'UNAVAILABLE', reason_codes=sorted(coverage_reasons)))


def extract_recording_results(telemetry, source_id, *, recorded_at=None):
    """Pure extraction from already-parsed evidence; never reopen the recording.

    The caller can reuse its history summary's native UTC capture date. None
    stays undated: neither filesystem mtime nor simulated calendar is a fallback.
    """
    metadata = _mapping(getattr(telemetry, 'metadata', {}))
    game = 'lmu' if str(metadata.get('game') or '').lower() == 'lmu' else 'iracing'
    channels = _mapping(getattr(telemetry, 'channels', {}))
    observed_sessions, session_samples = _recording_sessions(channels)
    sample = {'finish_status': _last(channels, 'PlayerFinishStatus'),
              'class_position': _last(channels, 'PlayerCarClassPosition'),
              'position': _last(channels, 'PlayerCarPosition')}
    return _extract(getattr(telemetry, 'session_info', {}), source_id, game,
        'LMU_RECORDING' if game == 'lmu' else 'IBT',
        recorded_at,
        'NATIVE_RECORDING_UTC', sample=sample, metadata=metadata,
        observed_sessions=observed_sessions, session_samples=session_samples)


def _live_batch(session_info, *, game='iracing', sample=None, source_id=None,
                 run_id=None, recorded_at=None):
    info, sample = _mapping(session_info), _mapping(sample)
    if _flag(sample.get('is_replay')):
        return None, 'REPLAY_EXCLUDED'
    if source_id is None:
        weekend, drivers = _mapping(info.get('WeekendInfo')), _mapping(info.get('DriverInfo'))
        source_id = _digest(['live-results', game, weekend.get('SessionID'), weekend.get('SubSessionID'),
                             drivers.get('DriverCarIdx'), run_id])
    batch = _extract(info, source_id, game, 'SDK', recorded_at or datetime.now(timezone.utc),
                     'LIVE_CAPTURE_UTC', sample=sample)
    if not any(row['authority'] in {'IRACING_FINAL_CLASSIFICATION', 'IRACING_FINISH_OBSERVATION', 'NATIVE_FINISH_OBSERVATION'}
               for row in batch['results']):
        return None, 'FINAL_CLASSIFICATION_UNAVAILABLE'
    return batch, None


def capture_live_result(session_info, *, game='iracing', sample=None, source_id=None,
                        run_id=None, recorded_at=None, store=None):
    """Ingest a native YAML snapshot outside the SDK sampling lock.

    Provisional table positions, the leader flag and RaceResultTracker's overlay
    finish are never promoted. LMU finish fields remain labelled observations.
    """
    batch, reason = _live_batch(session_info, game=game, sample=sample, source_id=source_id,
                               run_id=run_id, recorded_at=recorded_at)
    if batch is None:
        return {'saved': False, 'reason': reason}
    return (store if store is not None else DriverResultsStore()).record_batch(batch)


class LiveResultCapture:
    """Bounded, owned write queue; SDK callbacks enqueue without database I/O.

    Pending snapshots of the same run coalesce to its latest native update. A
    full queue rejects a new run rather than blocking or losing another run's
    pending final result. The next native tick can submit the snapshot again.
    """
    def __init__(self, *, store_factory=None, max_pending=8):
        if type(max_pending) is not int or not 1 <= max_pending <= 64:
            _fail()
        self.store_factory = store_factory or DriverResultsStore
        self.max_pending = max_pending
        self._condition = threading.Condition()
        self._pending = OrderedDict()
        self._recent = OrderedDict()
        self._thread = None
        self._closing = False
        self._counts = {'saved': 0, 'skipped': 0, 'failed': 0, 'dropped': 0}

    def submit(self, session_info, *, game='iracing', sample=None, run_id=None, recorded_at=None):
        """Copy only native session sections into bounded transient memory."""
        info, sample = _mapping(session_info), _mapping(sample)
        weekend, drivers = _mapping(info.get('WeekendInfo')), _mapping(info.get('DriverInfo'))
        key = (game, str(weekend.get('SessionID') or ''), str(weekend.get('SubSessionID') or ''),
               str(drivers.get('DriverCarIdx')), str(run_id or ''))
        with self._condition:
            if self._closing:
                return False
            if key not in self._pending and len(self._pending) >= self.max_pending:
                self._counts['dropped'] += 1
                return False
            # No parsing, hashing, game reads, storage or network on this path.
            payload = {'session_info': copy.deepcopy({name: info.get(name, {}) for name in
                ('WeekendInfo', 'DriverInfo', 'SessionInfo')}), 'game': game,
                'sample': {name: sample.get(name) for name in ('is_replay', 'finish_status', 'class_position', 'position',
                                                              'session_num', 'session_state')},
                'run_id': run_id, 'recorded_at': recorded_at or datetime.now(timezone.utc)}
            self._pending[key] = payload
            if self._thread is None:
                try:
                    self._thread = threading.Thread(target=self._run, name='driver-result-capture', daemon=True)
                    self._thread.start()
                except Exception:
                    self._pending.clear()
                    self._thread = None
                    self._counts['failed'] += 1
                    return False
            self._condition.notify()
            return True

    def status(self):
        with self._condition:
            return {**self._counts, 'queued': len(self._pending),
                    'running': self._thread is not None and self._thread.is_alive()}

    def shutdown(self, wait=False, timeout=None):
        """Drain queued snapshots, reject new submissions and optionally join."""
        with self._condition:
            self._closing = True
            self._condition.notify_all()
            thread = self._thread
        if wait and thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def _run(self):
        while True:
            with self._condition:
                while not self._pending and not self._closing:
                    self._condition.wait()
                if not self._pending:
                    return
                _, payload = self._pending.popitem(last=False)
            try:
                batch, _ = _live_batch(**payload)
                if batch is None:
                    with self._condition:
                        self._counts['skipped'] += 1
                    continue
                # Capture UTC is excluded from deduplication. Repeated native
                # final tables do not write every second; corrections do.
                signature = _digest({**batch, 'results': [{key: value for key, value in row.items()
                    if key != 'recorded_at'} for row in batch['results']]})
                if self._recent.get(batch['source_id']) == signature:
                    with self._condition:
                        self._counts['skipped'] += 1
                    continue
                self.store_factory().record_batch(batch)
                self._recent[batch['source_id']] = signature
                self._recent.move_to_end(batch['source_id'])
                if len(self._recent) > 128:
                    self._recent.popitem(last=False)
                with self._condition:
                    self._counts['saved'] += 1
            except Exception:
                # The digest is not cached, so a later identical tick retries.
                # Native payloads and exception strings never enter diagnostics.
                with self._condition:
                    self._counts['failed'] += 1


def _validate_batch(batch):
    if not isinstance(batch, dict) or batch.get('schema_version') != 1:
        _fail()
    source_id = _hash(batch.get('source_id'))
    game = batch.get('game')
    if game not in {'iracing', 'lmu'} or not isinstance(batch.get('results'), list) or len(batch['results']) > 256:
        _fail()
    coverage = _mapping(batch.get('coverage'))
    if coverage.get('status') not in {'OBSERVED', 'PARTIAL', 'UNAVAILABLE'}:
        _fail()
    def codes(value):
        if (not isinstance(value, list) or len(value) > 40
                or any(not isinstance(code, str) or not _CODE.fullmatch(code) for code in value)):
            _fail()
        return sorted(set(value))
    coverage = {'status': coverage['status'], 'reason_codes': codes(coverage.get('reason_codes'))}
    rows, ids = [], set()
    for row in batch['results']:
        if not isinstance(row, dict) or set(row) != _FIELDS or row.get('game') != game:
            _fail()
        checked = {**row}
        for key in ('id', 'session_id', 'driver_id'):
            _hash(checked[key])
        if checked['id'] in ids:
            _fail()
        ids.add(checked['id'])
        if checked['drive_id'] is not None:
            _hash(checked['drive_id'])
        checked['recorded_at'] = _stamp(checked['recorded_at'])
        choices = {'status': {'OFFICIAL', 'OBSERVATION'},
            'authority': {'IRACING_FINAL_CLASSIFICATION', 'IRACING_FINISH_OBSERVATION', 'IRACING_RESULT_TABLE',
                          'NATIVE_FINISH_OBSERVATION', 'UNVERIFIED'},
            'identity_status': {'NATIVE', 'SOURCE_ONLY'}, 'finish_status': {'CLASSIFIED', 'DNF', 'DSQ', 'FINISHED', 'UNKNOWN'},
            'date_source': {'NATIVE_RECORDING_UTC', 'LIVE_CAPTURE_UTC', 'UNKNOWN'},
            'source_kind': {'IBT', 'LMU_RECORDING', 'SDK'}}
        if any(checked[key] not in values for key, values in choices.items()):
            _fail()
        for key in ('class_position', 'overall_position'):
            if checked[key] is not None and (type(checked[key]) is not int or not 1 <= checked[key] <= 1024):
                _fail()
        if checked['status'] == 'OFFICIAL' and (game != 'iracing' or checked['identity_status'] != 'NATIVE'
                or checked['authority'] != 'IRACING_FINAL_CLASSIFICATION' or checked['class_position'] is None):
            _fail()
        labels = checked['labels']
        if not isinstance(labels, dict) or set(labels) != {'car', 'track'}:
            _fail()
        checked['labels'] = {key: _label(value) for key, value in labels.items()}
        checked['reason_codes'] = codes(checked['reason_codes'])
        checked['reason'] = _reason(checked['reason_codes'])
        rows.append(checked)
    return source_id, game, coverage, rows


class DriverResultsStore:
    def __init__(self, path=None, *, history_path=None):
        if path is None:
            configured = os.getenv('IRACING_DRIVER_RESULTS_PATH', '').strip()
            if configured:
                path = configured
            else:
                if history_path is None:
                    from driver_history import DriverHistoryStore
                    history_path = DriverHistoryStore().path
                history_path = Path(history_path)
                path = history_path.with_name(history_path.stem+'.results.sqlite3')
        self.path = Path(path).expanduser()
        self._lock = threading.RLock()

    @contextmanager
    def _connection(self, write=False):
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        target = self.path if write else self.path.resolve().as_uri()+'?mode=ro'
        connection = sqlite3.connect(target, uri=not write, timeout=.5)
        try:
            connection.execute('PRAGMA foreign_keys=ON')
            connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            if write and not tables and version == 0:
                connection.execute('CREATE TABLE sources(source_id TEXT PRIMARY KEY,game TEXT NOT NULL,coverage TEXT NOT NULL)')
                connection.execute('CREATE TABLE results(source_id TEXT NOT NULL REFERENCES sources(source_id) ON DELETE CASCADE,'
                    'id TEXT NOT NULL,game TEXT NOT NULL,recorded_at TEXT,payload TEXT NOT NULL,PRIMARY KEY(source_id,id))')
                connection.execute('CREATE INDEX results_game ON results(game,id)')
                connection.execute('PRAGMA user_version=1')
            elif version != 1 or tables != set(_COLUMNS) or any(tuple(row[1] for row in
                    connection.execute(f'PRAGMA table_info({table})')) != columns for table, columns in _COLUMNS.items()):
                _fail()
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def record_batch(self, batch):
        """Replace all results for this exact source in one atomic transaction."""
        source_id, game, coverage, rows = _validate_batch(batch)
        with self._lock, self._connection(write=True) as connection:
            existing = connection.execute('SELECT game FROM sources WHERE source_id=?', (source_id,)).fetchone()
            if existing and existing[0] != game:
                _fail()
            connection.execute('INSERT INTO sources VALUES(?,?,?) ON CONFLICT(source_id) DO UPDATE SET coverage=excluded.coverage',
                               (source_id, game, json.dumps(coverage, ensure_ascii=False, sort_keys=True)))
            connection.execute('DELETE FROM results WHERE source_id=?', (source_id,))
            connection.executemany('INSERT INTO results VALUES(?,?,?,?,?)', [(source_id, row['id'], game,
                row['recorded_at'], json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False)) for row in rows])
        return {'saved': True, 'source_id': source_id, 'result_count': len(rows)}

    def snapshot(self, game):
        if game not in {'iracing', 'lmu'}:
            _fail()
        if not self.path.is_file():
            return {'source_count': 0, 'rows': [], 'source_reasons': []}
        with self._connection() as connection:
            sources = connection.execute('SELECT coverage FROM sources WHERE game=?', (game,)).fetchall()
            rows = connection.execute('SELECT source_id,payload FROM results WHERE game=? ORDER BY id,source_id', (game,)).fetchall()
        return {'source_count': len(sources), 'rows': [(source, json.loads(payload)) for source, payload in rows],
                'source_reasons': [json.loads(row[0])['reason_codes'] for row in sources]}


def _canonical(rows):
    groups = {}
    for source_id, row in rows:
        groups.setdefault(row['id'], []).append((source_id, row))
    output = []
    for candidates in groups.values():
        def authority(item):
            row = item[1]
            observed_authority = {'IRACING_FINISH_OBSERVATION': 2, 'NATIVE_FINISH_OBSERVATION': 2,
                                  'IRACING_RESULT_TABLE': 1}.get(row['authority'], 0)
            return (row['status'] == 'OFFICIAL', observed_authority, row['recorded_at'] is not None,
                    row['recorded_at'] or '', row['source_kind'] == 'SDK')
        priority = max(map(authority, candidates))
        best = [item for item in candidates if authority(item) == priority]
        row = {**best[0][1], 'labels': dict(best[0][1]['labels']),
                      'reason_codes': list(best[0][1]['reason_codes'])}
        classification = {(item['class_position'], item['overall_position'], item['finish_status']) for _, item in best}
        if row['status'] == 'OFFICIAL' and len(classification) > 1:
            row.update(status='OBSERVATION', class_position=None, overall_position=None)
            row['reason_codes'] = sorted(set(row['reason_codes']) | {'CONFLICTING_FINAL_RESULTS'})
            row['reason'] = _reason(row['reason_codes'])
        row['source_count'] = len(candidates)
        row['source_ids'] = sorted(source_id for source_id, _ in candidates)
        output.append(row)
    return sorted(output, key=lambda row: (row['recorded_at'] or '', row['id']), reverse=True)


def _aggregate(rows):
    official = [row for row in rows if row['status'] == 'OFFICIAL']
    eligible = [row for row in official if row['finish_status'] != 'UNKNOWN']
    classified = [row for row in official if row['finish_status'] != 'DSQ' and row['class_position'] is not None]
    def metric(value, known):
        return {'value': value if known else None,
                'status': ('OBSERVED' if known == len(official) and not any(row['status'] != 'OFFICIAL' for row in rows)
                           else 'PARTIAL') if known else 'UNAVAILABLE',
                'reason': ('보관한 공식 최종 클래스 결과를 집계했습니다.' if known else '공식 최종 클래스 결과를 확인할 수 없습니다.')}
    observations = [row for row in rows if row['status'] != 'OFFICIAL']
    finished = sum(row['authority'] in {'IRACING_FINISH_OBSERVATION', 'NATIVE_FINISH_OBSERVATION'}
                   and row['finish_status'] in {'FINISHED', 'DNF', 'DSQ'} for row in observations)
    return {'recorded_results': len(rows), 'official_races': len(official),
        'observation_results': len(observations), 'finish_observations': finished,
        'unconfirmed_results': len(observations)-finished, 'classified_count': len(classified),
        'dnf_count': sum(row['finish_status'] == 'DNF' for row in official),
        'dsq_count': sum(row['finish_status'] == 'DSQ' for row in official),
        'wins': metric(sum(row['class_position'] == 1 and row['finish_status'] not in {'DNF', 'DSQ'} for row in eligible), len(eligible)),
        'podiums': metric(sum(row['class_position'] <= 3 and row['finish_status'] not in {'DNF', 'DSQ'} for row in eligible), len(eligible)),
        'average_position': metric(round(sum(row['class_position'] for row in classified)/len(classified), 3)
                                  if classified else None, len(classified))}


def read_driver_results(game='iracing', period_days=30, *, store=None, now=None):
    if game not in {'iracing', 'lmu'} or type(period_days) is not int or period_days not in {0, 30, 90}:
        _fail()
    snapshot = (store if store is not None else DriverResultsStore()).snapshot(game)
    # v1 created empty placeholders from scheduled Race sections and treated
    # ReasonOutId=0 as a finish even without ResultsOfficial. Correct the view
    # without rewriting a personal database or opening any source recording.
    usable, excluded_ids = [], set()
    for source, row in snapshot['rows']:
        codes = set(row['reason_codes'])
        if (row['status'] != 'OFFICIAL' and 'OWN_RESULT_UNAVAILABLE' in codes
                and row['class_position'] is None and row['finish_status'] == 'UNKNOWN'):
            excluded_ids.add(row['id'])
            continue
        if row['status'] != 'OFFICIAL' and row['authority'] == 'UNVERIFIED' and row['finish_status'] == 'CLASSIFIED':
            codes.add('RACE_FINISH_UNCONFIRMED')
            row = {**row, 'authority': 'IRACING_RESULT_TABLE', 'finish_status': 'UNKNOWN',
                   'reason_codes': sorted(codes), 'reason': _reason(sorted(codes))}
        usable.append((source, row))
    rows = _canonical(usable)
    excluded_empty = len(excluded_ids-{row['id'] for row in rows})
    now = now if now is not None else datetime.now(timezone.utc)
    now = datetime.fromisoformat(_stamp(now))
    cutoff = now-timedelta(days=period_days) if period_days else None
    selected = [row for row in rows if cutoff is None or row['recorded_at'] is not None
                and cutoff <= datetime.fromisoformat(row['recorded_at']) <= now]
    reasons = {}
    for codes in snapshot['source_reasons']:
        for code in codes:
            reasons[code] = reasons.get(code, 0)+1
    for row in rows:
        if 'CONFLICTING_FINAL_RESULTS' in row['reason_codes']:
            reasons['CONFLICTING_FINAL_RESULTS'] = reasons.get('CONFLICTING_FINAL_RESULTS', 0)+1
    return {'game': game, 'period_days': period_days, 'calculation_version': CALCULATION_VERSION,
        'coverage': {'source_count': snapshot['source_count'], 'result_count': len(rows),
            'excluded_empty_result_count': excluded_empty,
            'official_result_count': sum(row['status'] == 'OFFICIAL' for row in rows),
            'observation_count': sum(row['status'] != 'OFFICIAL' for row in rows),
            'period_result_count': len(selected), 'returned_result_count': min(len(selected), 200), 'result_limit': 200,
            'undated_result_count': sum(row['recorded_at'] is None for row in rows),
            'reason_counts': [{'code': code, 'count': count} for code, count in sorted(reasons.items())]},
        'aggregate': _aggregate(selected), 'career': _aggregate(rows), 'results': selected[:200]}
