"""Explicit, bounded LMU diagnostic observation; never official lap validity."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import threading
import time
import uuid

from lmu_validity import LapValidityTracker, VALIDATION_STATE


_NUMBERS = {'game_version', 'vehicle_id', 'telemetry_et', 'scoring_et', 'lap_start_et',
            'scoring_lap_start_et', 'total_laps', 'last_lap_time', 'count_lap_flag',
            'track_limits_steps', 'speed_mps', 'throttle_ratio', 'brake_ratio', 'steering_ratio'}
_BOOLEANS = {'telemetry_updated', 'scoring_updated', 'lap_invalidated', 'in_pits', 'realtime', 'paused'}
_EVENTS = {'SME_ENTER', 'SME_EXIT', 'SME_STARTUP', 'SME_SHUTDOWN', 'SME_LOAD', 'SME_UNLOAD',
           'SME_START_SESSION', 'SME_END_SESSION', 'SME_ENTER_REALTIME', 'SME_EXIT_REALTIME',
           'SME_UPDATE_SCORING', 'SME_UPDATE_TELEMETRY', 'SME_INIT_APPLICATION',
           'SME_UNINIT_APPLICATION', 'SME_SET_ENVIRONMENT', 'SME_FFB'}
_CODES = {'GAME_NOT_RUNNING', 'MULTIPLE_GAME_PROCESSES', 'GAME_EXITED', 'NOT_OPEN',
          'ABI_MISMATCH', 'NATIVE_NOT_BUILT', 'SDK_UNAVAILABLE', 'UNSUPPORTED_PLATFORM',
          'SHARED_LAYOUT_INVALID', 'BUFFER_TOO_SMALL', 'SHARED_MEMORY_UNAVAILABLE',
          'GAME_VERSION_INVALID', 'GAME_VERSION_CHANGED', 'GAME_PROCESS_UNAVAILABLE',
          'SHARED_LOCK_UNAVAILABLE', 'SHARED_EVENTS_UNAVAILABLE', 'SHARED_LAYOUT_UNAVAILABLE',
          'JOURNAL_WRITE_FAILED'}


class _JournalWriteFailure(RuntimeError):
    code = 'JOURNAL_WRITE_FAILED'


def _normalise(raw):
    """Copy only numeric diagnostics and opaque, non-identifying session keys."""
    if not isinstance(raw, dict):
        return None
    result = {}
    for key in _NUMBERS:
        value = raw.get(key)
        result[key] = value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None
    for key in _BOOLEANS:
        value = raw.get(key)
        result[key] = value if isinstance(value, bool) else None
    session = raw.get('session_id')
    result['session_key'] = hashlib.sha256(session.encode('utf-8')).hexdigest() if isinstance(session, str) and session else None
    events = raw.get('events')
    result['events'] = sorted({e for e in events if isinstance(e, str) and e in _EVENTS}) if isinstance(events, list) else []
    return result


def _default_source():
    from lmu_shared_memory import SharedMemorySource
    return SharedMemorySource()


class LmuLiveService:
    def __init__(self, *, source_factory=None, recording_dir=None, clock=time.monotonic, utc_clock=time.time,
                 max_duration_s=900, max_frames=100000, max_bytes=128 * 1024 * 1024,
                 stale_after_s=1.0, join_timeout_s=1.0):
        if any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) or v <= 0
               for v in (max_duration_s, max_frames, max_bytes, stale_after_s, join_timeout_s)):
            raise ValueError('수집 제한은 양수여야 합니다.')
        if (max_duration_s > 900 or max_frames > 100000 or max_bytes > 128 * 1024 * 1024
                or not isinstance(max_frames, int) or not isinstance(max_bytes, int)):
            raise ValueError('기록 제한은 최대 900초, 100000프레임, 128MB 이내여야 합니다.')
        self._factory = source_factory or _default_source
        self._directory = Path(recording_dir) if recording_dir is not None else Path(__file__).resolve().parents[1] / 'logs' / 'lmu-live'
        self._clock = clock
        self._utc_clock = utc_clock
        self._duration, self._max_frames, self._max_bytes = max_duration_s, max_frames, max_bytes
        self._stale, self._join_timeout = stale_after_s, join_timeout_s
        self._control = threading.Lock()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._tracker = LapValidityTracker()
        self._state, self._reason = 'STOPPED', '실시간 관측을 시작하지 않았습니다.'
        self._latest = self._fresh_at = self._last_identity = None
        self._recording_id = None
        self._frames = self._bytes = 0
        self._identity = None
        self._pending = []
        self._journal_open = False
        self._journal_state = 'NOT_STARTED'
        self._started_at = self._read_started_at = self._received_at = None
        self._ended_at = None
        self._last_read_duration = self._last_snapshot_gap = self._last_telemetry_gap = self._last_game_gap = None
        self._max_snapshot_gap = self._max_telemetry_gap = None
        self._stale_count = self._sequence = 0
        self._last_stale = None

    def _timing(self, now):
        if self._ended_at is not None:
            now = min(now, self._ended_at)
        return {
            'recording_elapsed_s': max(0., now - self._started_at) if self._started_at is not None else None,
            'telemetry_age_s': max(0., now - self._fresh_at) if self._fresh_at is not None else None,
            'read_in_progress_s': max(0., now - self._read_started_at) if self._read_started_at is not None else None,
            'last_read_duration_s': self._last_read_duration,
            'last_snapshot_receive_gap_s': self._last_snapshot_gap,
            'max_snapshot_receive_gap_s': self._max_snapshot_gap,
            'last_telemetry_receive_gap_s': self._last_telemetry_gap,
            'max_telemetry_receive_gap_s': self._max_telemetry_gap,
            'last_game_time_gap_s': self._last_game_gap,
            'stale_count': self._stale_count,
        }

    def _row(self, payload, now=None):
        # Capture event time here, never when the worker later drains the queue.
        # UTC is for correlation; monotonic elapsed time determines freshness.
        now = self._clock() if now is None else now
        self._sequence += 1
        return {**payload, 'schema_version': 2, 'sequence': self._sequence,
                'observed_elapsed_s': max(0., now - self._started_at),
                'observed_utc': datetime.fromtimestamp(self._utc_clock(), timezone.utc).isoformat(timespec='milliseconds')}

    def _lifecycle(self, code, events=None):
        # Only the worker drains this queue. Status can interrupt evidence in
        # memory without touching disk; repeated stale/status/stop requests do
        # not enqueue another transition while the worker is blocked in read.
        if self._journal_open:
            now = self._clock()
            self._pending.append(self._row({'type': 'LIFECYCLE', 'reason_code': code,
                                           'timing': self._timing(now), 'lap_events': events or []}, now))

    def _interrupt(self, reason):
        events = self._tracker.interrupt(reason)
        self._lifecycle(reason, events)

    def _expire(self, now):
        if self._state in ('WAITING', 'OBSERVING', 'STALE') and self._fresh_at is not None and now - self._fresh_at >= self._stale:
            if self._state != 'STALE':
                self._stale_count += 1
                self._last_stale = self._timing(now)
                self._interrupt('STALE_TELEMETRY')
            self._latest = None
            self._state, self._reason = 'STALE', '새 주행 데이터가 1초 이상 없어 현재 표시를 숨겼습니다.'

    def status(self):
        with self._lock:
            self._expire(self._clock())
            return deepcopy({'provider': 'LMU_SHARED_MEMORY', 'state': self._state,
                             'collecting': self._thread is not None and self._thread.is_alive() and not self._stop.is_set(),
                             'reason': self._reason, 'valid': None, 'validation_state': VALIDATION_STATE,
                             'snapshot': self._latest, 'recording_id': self._recording_id,
                             'journal_state': self._journal_state,
                             'recording_format_version': 2, 'timing': self._timing(self._clock()),
                             'last_stale': self._last_stale,
                             'frames_recorded': self._frames, 'lap_observations': self._tracker.status()})

    def _terminal(self, state, reason, code=None):
        with self._lock:
            self._interrupt(code or state)
            self._state, self._reason = state, reason
            self._latest = None

    def start(self):
        with self._control:
            if self._thread is not None and self._thread.is_alive():
                return self.status()
            source = None
            journal = None
            try:
                source = self._factory()
                source.open()
                recording_id = uuid.uuid4().hex
                self._directory.mkdir(parents=True, exist_ok=True)
                journal = (self._directory / (recording_id + '.jsonl')).open('x', encoding='utf-8', newline='\n')
            except Exception as exc:
                if source is not None:
                    try:
                        source.close()
                    except Exception:
                        pass
                code = getattr(exc, 'code', None)
                code = code if code in _CODES else 'START_FAILED'
                self._terminal('UNAVAILABLE', '실시간 연결을 시작할 수 없습니다. (' + code + ')')
                return self.status()
            with self._lock:
                self._tracker = LapValidityTracker()
                self._latest = self._fresh_at = self._last_identity = None
                self._recording_id, self._frames, self._bytes = recording_id, 0, 0
                self._identity = None
                self._pending = []
                self._journal_open = True
                self._journal_state = 'ACTIVE'
                self._started_at = self._clock()
                self._ended_at = None
                self._read_started_at = self._received_at = None
                self._last_read_duration = self._last_snapshot_gap = self._last_telemetry_gap = self._last_game_gap = None
                self._max_snapshot_gap = self._max_telemetry_gap = None
                self._stale_count = self._sequence = 0
                self._last_stale = None
                self._lifecycle('STARTED')
                self._state, self._reason = 'WAITING', '연결되었습니다. 새 주행 데이터를 기다립니다.'
                self._stop.clear()
                self._thread = threading.Thread(target=self._run, args=(source, journal, self._started_at),
                                                name='lmu-live-observer', daemon=True)
                try:
                    self._thread.start()
                except Exception:
                    self._thread = None
                    try:
                        source.close()
                    except Exception:
                        pass
                    finally:
                        journal.close()
                    self._journal_open = False
                    self._journal_state = 'INCOMPLETE'
                    self._ended_at = self._clock()
                    self._terminal('ERROR', '실시간 작업을 시작할 수 없습니다. (WORKER_START_FAILED)')
            return self.status()

    def _write(self, journal, payload, *, reserve=False):
        line = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n'
        size = len(line.encode('utf-8'))
        # Keep room for interruption evidence and the final lifecycle marker.
        # An exceptionally small/full journal reports INCOMPLETE, never exceeds
        # its byte ceiling or silently reports a complete recording.
        limit = self._max_bytes - min(16384, self._max_bytes // 4) if reserve else self._max_bytes
        if self._bytes + size > limit:
            return False
        try:
            journal.write(line)
            journal.flush()
            if payload.get('type') == 'LIFECYCLE':
                os.fsync(journal.fileno())
        except Exception:
            self._journal_state = 'INCOMPLETE'
            raise _JournalWriteFailure() from None
        self._bytes += size
        return True

    def _drain(self, journal):
        while self._pending:
            if not self._write(journal, self._pending[0]):
                self._journal_state = 'INCOMPLETE'
                return False
            self._pending.pop(0)
        return True

    def _run(self, source, journal, started):
        try:
            while not self._stop.is_set():
                with self._lock:
                    if not self._drain(journal):
                        self._terminal('LIMIT_REACHED', '상태 기록을 저장할 용량이 부족하여 관측을 종료했습니다.')
                        break
                if self._clock() - started >= self._duration or self._frames >= self._max_frames:
                    self._terminal('LIMIT_REACHED', '실시간 기록 제한에 도달하여 관측을 종료했습니다.')
                    break
                with self._lock:
                    self._read_started_at = self._clock()
                try:
                    raw = source.read(timeout_ms=200)
                finally:
                    # Finish read timing before error reporting or slow close.
                    now = self._clock()
                    with self._lock:
                        self._last_read_duration = max(0., now - self._read_started_at)
                        self._read_started_at = None
                with self._lock:
                    self._expire(now)
                    if self._stop.is_set():
                        break
                    if now - started >= self._duration:
                        self._terminal('LIMIT_REACHED', '실시간 기록 제한에 도달하여 관측을 종료했습니다.')
                        break
                    snapshot = _normalise(raw)
                    if snapshot is not None:
                        stream_identity = (snapshot['session_key'], snapshot['vehicle_id'])
                        if self._identity is not None and stream_identity != self._identity:
                            self._interrupt('SESSION_OR_VEHICLE_CHANGED')
                            self._latest = self._fresh_at = self._last_identity = None
                            self._state, self._reason = 'WAITING', '세션 또는 차량이 바뀌어 새 주행 데이터를 기다립니다.'
                        self._identity = stream_identity
                        identity = (snapshot['session_key'], snapshot['vehicle_id'], snapshot['telemetry_et'])
                        fresh = (snapshot['telemetry_updated'] is True and snapshot['telemetry_et'] is not None
                                 and identity != self._last_identity)
                        self._last_snapshot_gap = max(0., now - self._received_at) if self._received_at is not None else None
                        if self._last_snapshot_gap is not None:
                            self._max_snapshot_gap = max(self._max_snapshot_gap or 0., self._last_snapshot_gap)
                        self._received_at = now
                        telemetry_gap = game_gap = None
                        if fresh:
                            telemetry_gap = max(0., now - self._fresh_at) if self._fresh_at is not None else None
                            if self._last_identity is not None and identity[:2] == self._last_identity[:2]:
                                game_gap = identity[2] - self._last_identity[2]
                            self._last_telemetry_gap, self._last_game_gap = telemetry_gap, game_gap
                            if telemetry_gap is not None:
                                self._max_telemetry_gap = max(self._max_telemetry_gap or 0., telemetry_gap)
                        # A repeated cached telemetry packet can carry new scoring,
                        # but never extends the position/speed freshness deadline.
                        if fresh:
                            self._fresh_at = now
                            self._last_identity = identity
                        events = self._tracker.ingest(snapshot, received_monotonic=now)
                        row = self._row({'type': 'SNAPSHOT', 'snapshot': snapshot, 'lap_events': events,
                                         'timing': {'received_elapsed_s': max(0., now - started),
                                                    'read_duration_s': self._last_read_duration,
                                                    'snapshot_receive_gap_s': self._last_snapshot_gap,
                                                    'telemetry_receive_gap_s': telemetry_gap,
                                                    'game_time_gap_s': game_gap}})
                        try:
                            recorded = self._drain(journal) and self._write(journal, row, reserve=True)
                        except Exception:
                            self._lifecycle('SNAPSHOT_WRITE_FAILED', events)
                            self._journal_state = 'INCOMPLETE'
                            raise
                        if not recorded:
                            self._lifecycle('SNAPSHOT_NOT_RECORDED', events)
                            self._journal_state = 'INCOMPLETE'
                            self._terminal('LIMIT_REACHED', '실시간 기록 용량 제한에 도달하여 관측을 종료했습니다.')
                            break
                        self._frames += 1
                        if fresh:
                            self._latest = snapshot
                            self._state, self._reason = 'OBSERVING', '실시간 신호를 관측 중입니다. 게임 화면 대조 전에는 랩 유효성을 확정하지 않습니다.'
                self._stop.wait(0.005)
        except Exception as exc:
            code = getattr(exc, 'code', None)
            code = code if code in _CODES else 'READ_FAILED'
            state = 'GAME_EXITED' if code == 'GAME_EXITED' else 'ERROR'
            self._terminal(state, '실시간 관측이 종료되었습니다. (' + code + ')', code=code)
        finally:
            try:
                source.close()
            except Exception:
                self._terminal('ERROR', '실시간 연결 정리 중 오류가 발생했습니다. (CLOSE_FAILED)')
            with self._lock:
                if self._read_started_at is not None:
                    self._last_read_duration = max(0., self._clock() - self._read_started_at)
                    self._read_started_at = None
                if self._stop.is_set() and self._state in ('WAITING', 'OBSERVING', 'STALE', 'STOPPING'):
                    self._terminal('STOPPED', '실시간 관측을 중지했습니다.')
                try:
                    if self._drain(journal) and self._journal_state != 'INCOMPLETE':
                        self._journal_state = 'COMPLETE'
                except Exception:
                    self._journal_state = 'INCOMPLETE'
                    self._state, self._reason = 'ERROR', '상태 기록을 저장할 수 없습니다. (JOURNAL_WRITE_FAILED)'
                    self._latest = None
                finally:
                    self._pending.clear()
                    self._journal_open = False
                    self._ended_at = self._clock()
                    try:
                        journal.close()
                    except Exception:
                        self._journal_state = 'INCOMPLETE'
                        self._state, self._reason = 'ERROR', '상태 기록을 닫을 수 없습니다. (JOURNAL_CLOSE_FAILED)'

    def stop(self):
        with self._control:
            thread = self._thread
            if thread is None or not thread.is_alive():
                return self.status()
            if self._stop.is_set():
                return self.status()
            self._stop.set()
            with self._lock:
                self._latest = None
                self._interrupt('STOP_REQUESTED')
            thread.join(timeout=self._join_timeout)
            if thread.is_alive():
                self._terminal('STOPPING', '실시간 연결 종료를 기다립니다. 새 연결은 종료 후 시작할 수 있습니다.')
            return self.status()


def get_live_service():
    """Factory: each application owns its collector and shutdown lifecycle."""
    return LmuLiveService()


def live_status():
    # Compatibility helper stays read-only and creates no transport or files.
    return get_live_service().status()
