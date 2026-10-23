"""Local-only race traffic observations. No driver names or export integration."""
from __future__ import annotations

import gzip
from collections import deque
from dataclasses import dataclass
import json
import logging
import math
from pathlib import Path
import re
import time
import threading

from race_engineer import parse_measure

logger = logging.getLogger('iRacingLive')
FORMAT = 'iracing-coach-traffic'
FIELDS = ['idx', 'class_id', 'gap_m', 'est_time_s', 'lap_completed', 'position',
          'class_position', 'on_pit_road', 'track_surface', 'local_x_m', 'local_z_m']
OWN_FIELDS = ['lap_completed', 'distance_pct', 'speed_mps', 'brake', 'throttle',
              'steering_rad', 'car_left_right', 'on_pit_road']
# Version 2: the own lap position to 6 places (3 places was ~5 m on a 5 km track).
OWN_DIGITS = [3, 6, 3, 3, 3, 3, 3, 3]
ARRAYS = {'distance_pct': 'CarIdxLapDistPct', 'lap_completed': 'CarIdxLapCompleted',
          'position': 'CarIdxPosition', 'class_position': 'CarIdxClassPosition',
          'on_pit_road': 'CarIdxOnPitRoad', 'track_surface': 'CarIdxTrackSurface',
          'est_time_s': 'CarIdxEstTime', 'class_id': 'CarIdxClass'}


def finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def rounded(value, digits=3):
    return round(value, digits) if finite(value) else None


def traffic_snapshot(sdk, names, sample, identity):
    """Capture published SDK arrays while its buffer is frozen (no SDK writes)."""
    drivers = sdk['DriverInfo'] or {}
    weekend = sdk['WeekendInfo'] or {}
    sessions = (sdk['SessionInfo'] or {}).get('Sessions') or []
    session = next((s for s in sessions if s.get('SessionNum') == sample.get('session_num')), {})
    entries = drivers.get('Drivers') or []
    classes = {d['CarIdx']: d.get('CarClassID') for d in entries if 'CarIdx' in d}
    estimates = {str(d['CarClassID']): {'est_lap_s': rounded(d.get('CarClassEstLapTime'))}
                 for d in entries if d.get('CarClassID') is not None}
    return {'game': getattr(sdk, 'game', identity.get('game', 'iracing')),
            'session_type': identity.get('session_type') or session.get('SessionType'),
            'identity': identity, 'sample': sample,
            'track_length_m': parse_measure(weekend.get('TrackLength'), 'distance'),
            'player_idx': sdk['PlayerCarIdx'] if 'PlayerCarIdx' in names else drivers.get('DriverCarIdx'),
            'arrays': {key: sdk[name] if name in names else None for key, name in ARRAYS.items()},
            'car_classes': classes, 'classes': estimates,
            'local': sdk['TrafficLocal'] if 'TrafficLocal' in names else None}


@dataclass(eq=False)
class _Run:
    run_id: str
    header: dict
    path: Path | None = None
    failed: bool = False
    failure_logged: bool = False


class TrafficLog:
    RATE_S, NEAR_M, KEEP_FILES, FLUSH_S = .1, 200., 30, 30.
    MAX_ROWS, CLOSE_TIMEOUT_S = 300, 2.

    def __init__(self, folder=None):
        self.folder = Path(folder) if folder else None
        self.run_id = self.last_time = self.flushed_at = None
        self._run = None
        self._queue = deque()
        self._condition = threading.Condition()
        self._flush_requested = self._closed = self._drop_logged = False
        self.dropped_rows = 0
        self.worker = None
        # Starting a thread is not part of the SDK record() deadline.
        if self.folder is not None:
            self.worker = threading.Thread(target=self._write, name='traffic-log-writer', daemon=True)
            self.worker.start()

    @property
    def path(self):
        return self._run.path if self._run is not None else None

    @property
    def failed(self):
        return self._run is not None and self._run.failed

    @property
    def queued_rows(self):
        with self._condition:
            return len(self._queue)

    def fail(self, run_id=None):
        """One diagnostic per run, including snapshot failures outside record()."""
        if self._run is None or (run_id is not None and run_id != self.run_id):
            self.run_id = run_id if run_id is not None else self.run_id
            self._run = _Run(self.run_id, {})
        run = self._run
        if not run.failure_logged:
            logger.exception('Race traffic log failed; recording stopped for this run')
        with self._condition:
            run.failed = run.failure_logged = True
            self._queue = deque((r, row) for r, row in self._queue if r is not run)

    def record(self, snapshot, run_id, session_time):
        if self.folder is None or self._closed:
            return
        try:
            self._record(snapshot, run_id, session_time)
        except Exception:
            self.fail()

    def _record(self, snapshot, run_id, session_time):
        if run_id != self.run_id:
            self.flush()
            self.run_id, self.last_time, self.flushed_at = run_id, None, None
            self._run = _Run(run_id, self._header(snapshot)) if snapshot else None
        if self.failed or not snapshot or not finite(session_time):
            return
        sample = snapshot['sample']
        if (str(snapshot.get('session_type') or '').lower() != 'race'
                or not sample.get('on_track') or sample.get('is_replay')):
            return
        if self.last_time is not None and session_time - self.last_time < self.RATE_S - 1e-8:
            return
        length, own_pct = snapshot.get('track_length_m'), sample.get('distance_pct')
        if not finite(length) or length <= 0 or not finite(own_pct) or not 0 <= own_pct <= 1:
            return
        if self.flushed_at is None:
            self.flushed_at = session_time
        self.last_time = session_time
        arrays = snapshot['arrays']
        def at(key, idx):
            values = arrays.get(key)
            return values[idx] if values is not None and idx < len(values) else None
        local = {row['idx']: row for row in snapshot.get('local') or []}
        cars = []
        distances = arrays.get('distance_pct')
        for idx, pct in enumerate(distances if distances is not None else []):
            if idx == snapshot.get('player_idx') or not finite(pct) or not 0 <= pct <= 1:
                continue
            surface = at('track_surface', idx)
            if surface == -1:
                continue
            gap = ((pct - own_pct + .5) % 1 - .5) * length
            if abs(gap) > self.NEAR_M:
                continue
            class_id = at('class_id', idx)
            if class_id is None:
                class_id = snapshot['car_classes'].get(idx)
            position = local.get(idx, {}) if snapshot.get('game') == 'lmu' else {}
            cars.append([idx, rounded(class_id), rounded(gap, 2), rounded(at('est_time_s', idx)),
                         rounded(at('lap_completed', idx)), rounded(at('position', idx)),
                         rounded(at('class_position', idx)), rounded(at('on_pit_road', idx)),
                         rounded(surface), rounded(position.get('x_m'), 2), rounded(position.get('z_m'), 2)])
        # Only detached primitive values cross threads; SDK arrays are never queued.
        row = {'t': round(session_time, 3),
               'own': [rounded(sample.get(k), d) for k, d in zip(OWN_FIELDS, OWN_DIGITS)], 'c': cars}
        with self._condition:
            if self._closed or self._run.failed:
                return
            if len(self._queue) == self.MAX_ROWS:
                self._queue.popleft()
                self.dropped_rows += 1
            self._queue.append((self._run, row))
            if len(self._queue) == self.MAX_ROWS or session_time - self.flushed_at >= self.FLUSH_S:
                self._flush_requested = True
                self.flushed_at = session_time
                self._condition.notify()

    def _header(self, snapshot):
        identity = snapshot['identity']
        return {'format': FORMAT, 'version': 2, 'game': snapshot.get('game'), 'run_id': self.run_id,
                'track': identity.get('track_name'), 'track_id': identity.get('track_id'),
                'track_config': identity.get('track_config'), 'track_length_m': snapshot['track_length_m'],
                'car': identity.get('car_path'), 'player_idx': snapshot.get('player_idx'),
                'classes': snapshot.get('classes') or {}, 'rate_s': self.RATE_S,
                'near_m': self.NEAR_M, 'fields': FIELDS, 'own_fields': OWN_FIELDS}

    def _new_file(self, header):
        self.folder.mkdir(parents=True, exist_ok=True)
        folder = self.folder.resolve()
        files = sorted(folder.glob('traffic-*.jsonl.gz'), key=lambda p: p.stat().st_mtime_ns)
        for old in files[:max(0, len(files) - self.KEEP_FILES + 1)]:
            if old.resolve().parent != folder:
                raise ValueError('Traffic log path leaves its folder')
            old.unlink()
        track = re.sub(r'[^\w-]+', '-', str(header.get('track') or 'track')).strip('-')[:40] or 'track'
        stem = f"traffic-{time.strftime('%Y%m%d-%H%M%S')}-{track}"
        path = folder / f'{stem}.jsonl.gz'
        index = 1
        while path.exists():
            path = folder / f'{stem}-{index}.jsonl.gz'
            index += 1
        # Reserve the name immediately, including two runs within one second.
        path.touch(exist_ok=False)
        return path

    def flush(self):
        """Request a write without waiting on compression, disk, or the worker."""
        with self._condition:
            self._flush_requested = True
            self._condition.notify()

    def _write_rows(self, run, rows):
        """Worker-only filesystem operations, deliberately outside the queue lock."""
        initial = run.path is None
        if initial:
            run.path = self._new_file(run.header)
        with gzip.open(run.path, 'at', encoding='utf-8') as handle:
            if initial:
                handle.write(json.dumps(run.header, ensure_ascii=False, allow_nan=False) + '\n')
            for row in rows:
                handle.write(json.dumps(row, separators=(',', ':'), allow_nan=False) + '\n')

    def _write(self):
        deadline = time.monotonic() + self.FLUSH_S
        while True:
            with self._condition:
                while not self._closed and not self._flush_requested and time.monotonic() < deadline:
                    self._condition.wait(timeout=max(0., deadline - time.monotonic()))
                batch = list(self._queue)
                self._queue.clear()
                closed = self._closed
                self._flush_requested = False
                dropped = self.dropped_rows if not self._drop_logged else 0
                self._drop_logged |= bool(dropped)
            if dropped:
                logger.warning('Traffic log queue full; oldest rows discarded (total %d)', dropped)
            groups = []
            for run, row in batch:
                if not groups or groups[-1][0] is not run:
                    groups.append((run, []))
                groups[-1][1].append(row)
            for run, rows in groups:
                if run.failed:
                    continue
                try:
                    self._write_rows(run, rows)
                except Exception:
                    with self._condition:
                        run.failed = True
                        self._queue = deque((r, row) for r, row in self._queue if r is not run)
                    if not run.failure_logged:
                        run.failure_logged = True
                        logger.exception('Race traffic log failed; recording stopped for this run')
            if closed:
                return
            deadline = time.monotonic() + self.FLUSH_S

    def close(self):
        with self._condition:
            self._closed = True
            self._condition.notify()
        if self.worker is not None and self.worker is not threading.current_thread():
            self.worker.join(timeout=self.CLOSE_TIMEOUT_S)


def read(path):
    header, rows = {}, []
    with gzip.open(path, 'rt', encoding='utf-8') as handle:
        for line in handle:
            if not line.strip():
                continue
            item = json.loads(line)
            if item.get('format') == FORMAT:
                header = item
                continue
            item['own'] = dict(zip(header.get('own_fields') or OWN_FIELDS, item.get('own') or []))
            item['c'] = [dict(zip(header.get('fields') or FIELDS, c)) for c in item.get('c') or []]
            rows.append(item)
    return header, rows
