"""Observed race standings, loaded only when the position chart is opened.

IBT array channels are sampled through a read-only mapping, never added to the
normal analysis channels. LMU/IBT can use the local timing log only when its
session identity AND native UTC/session clocks agree. No mtime-based matching.
"""
from __future__ import annotations

import gzip
from contextlib import closing
import json
import math
import os
from pathlib import Path
import sqlite3

import numpy as np

from analysis_numeric import file_signature
from telemetry_channels import IRSDK_DTYPES

MAX_FRAMES = 12000
MAX_CARS = 128


def _number(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _rank(value):
    value = _number(value)
    return int(value) if value is not None and value.is_integer() and 1 <= value <= MAX_CARS else None


def is_race(value):
    return str(value or '').strip().casefold() in ('race', 'endurance', 'endurance race')


def _empty(reason):
    return dict(status='UNAVAILABLE', reason=reason, series=[], classes=[], start_s=None, end_s=None)


def _append(series, at, overall, class_rank, gap=False):
    """Keep changes, their preceding sample, and both ends of every gap."""
    points = series['points']
    if gap and points and points[-1][1:] != [None, None]:
        points.append([round(points[-1][0] + .001, 3), None, None])
    point = [round(at, 3), overall, class_rank]
    if len(points) >= 2 and points[-1][1:] == point[1:] == points[-2][1:]:
        points[-1] = point
    else:
        points.append(point)


def _result(series, source, step):
    own = next((row for row in series.values() if row['own']), None)
    if own is None or own['class_id'] is None:
        return _empty('내 차량의 클래스를 확인할 수 없어 순위 그래프를 표시하지 않습니다.')
    rows = [row for row in series.values() if row['class_id'] == own['class_id']
            and any(p[2] is not None for p in row['points'])]
    if not rows:
        return _empty('이 기록에 관측된 순위가 없습니다.')
    start = min(row['points'][0][0] for row in rows)
    end = max(row['points'][-1][0] for row in rows)
    classes = list(dict.fromkeys(row['class_id'] for row in rows if row['class_id'] is not None))
    return dict(status='READY', source=source, series=rows, own_class_id=own['class_id'],
                classes=[{'id': key, 'label': next((r['class_name'] for r in rows if r['class_id'] == key and r.get('class_name')), f'클래스 {key}')}
                         for key in classes], start_s=start, end_s=end, sample_interval_s=round(step, 3),
                reason='기록된 구간만 표시합니다. 경기의 출발·최종 결과와 다를 수 있습니다.')


def _series(idx, class_id=None, number=None, own=False, class_name=None):
    return dict(id=str(idx), label=f'#{number}' if number not in (None, '') else f'차량 {idx}',
                class_id=str(class_id) if class_id is not None else None,
                class_name=class_name, own=own, points=[])


def _native(telemetry):
    if telemetry.path.suffix.casefold() != '.ibt' or telemetry.buffer_length <= 0:
        return None
    variables = telemetry.variables
    positions = variables.get('CarIdxPosition')
    own_position = variables.get('PlayerCarPosition')
    if positions is None and own_position is None:
        return None
    n, width = telemetry.record_count, telemetry.buffer_length
    stride = max(telemetry.tick_rate, math.ceil(n / MAX_FRAMES))
    indices = np.arange(0, n, stride, dtype=np.int64)
    if len(indices) and indices[-1] != n - 1:
        indices = np.r_[indices, n - 1]
    with telemetry.path.open('rb') as handle:
        mapping = np.memmap(handle, mode='r', dtype='u1')
        def read(name):
            header = variables.get(name)
            if header is None:
                return None
            dtype = IRSDK_DTYPES.get(header.var_type)
            if (dtype is None or not 1 <= header.count <= MAX_CARS or header.offset < 0
                    or header.offset + header.count * dtype.itemsize > width
                    or telemetry.data_offset + n * width > len(mapping)):
                raise ValueError('순위 채널의 기록 범위가 올바르지 않습니다.')
            view = np.ndarray((n, header.count), dtype=dtype, buffer=mapping,
                              offset=telemetry.data_offset + header.offset, strides=(width, dtype.itemsize))
            return view[indices].copy()
        try:
            times = read('SessionTime')
            nums = read('SessionNum')
            overall = read('CarIdxPosition') if positions else read('PlayerCarPosition')
            classes = read('CarIdxClassPosition') if positions else read('PlayerCarClassPosition')
        finally:
            mapping._mmap.close()
    if times is None or overall is None:
        return None
    times = times[:, 0]
    allowed = {s.get('SessionNum') for s in telemetry.session_info.get('SessionInfo', {}).get('Sessions', [])
               if isinstance(s, dict) and is_race(s.get('SessionType'))}
    mask = np.isfinite(times)
    if nums is not None:
        mask &= np.isin(nums[:, 0], list(allowed))
    elif not is_race(telemetry.metadata.get('session_type')):
        return _empty('레이스·인듀런스 경기 기록에서 사용할 수 있습니다.')
    selected = np.flatnonzero(mask)
    if not len(selected):
        return _empty('이 기록에 레이스 순위 구간이 없습니다.')
    # One file can span sessions. Only the final observed race segment is shown.
    starts = [0] + [i for i in range(1, len(selected))
                    if times[selected[i]] <= times[selected[i-1]]
                    or (nums is not None and nums[selected[i], 0] != nums[selected[i-1], 0])]
    selected = selected[starts[-1]:]
    driver_info = telemetry.session_info.get('DriverInfo', {})
    own = driver_info.get('DriverCarIdx')
    drivers = {d.get('CarIdx'): d for d in driver_info.get('Drivers', [])
               if isinstance(d, dict) and not d.get('CarIsPaceCar') and not d.get('IsSpectator')}
    own_class = drivers.get(own, {}).get('CarClassID')
    if own_class is None:
        return _empty('내 차량의 클래스를 확인할 수 없어 순위 그래프를 표시하지 않습니다.')
    indices_by_car = [idx for idx, driver in drivers.items() if driver.get('CarClassID') == own_class] if positions else [own]
    series = {}
    step = stride / max(1, telemetry.tick_rate)
    for idx in indices_by_car[:MAX_CARS]:
        column = idx if positions else 0
        if type(column) is not int or not 0 <= column < overall.shape[1]:
            continue
        driver = drivers.get(idx, {})
        row = _series(idx, driver.get('CarClassID'), driver.get('CarNumber'), idx == own,
                      driver.get('CarClassShortName') or driver.get('CarClassName'))
        previous = previous_index = None
        for i in selected:
            at = float(times[i])
            gap = previous is not None and (i != previous_index + 1 or at - previous > max(3., step * 2.5))
            _append(row, at, _rank(float(overall[i, column])),
                    _rank(float(classes[i, column])) if classes is not None and column < classes.shape[1] else None, gap)
            previous, previous_index = at, i
        series[str(idx)] = row
    return _result(series, 'IBT', step)


def _clock(telemetry):
    from session_log_extras import recording_window
    window = recording_window(telemetry.path)
    times = telemetry.channels.get('SessionTime')
    if window is None or times is None or len(times) < 2:
        return None
    # Resets cannot be aligned to one native start date.
    first, last = float(times[0]), float(times[-1])
    if not math.isfinite(first) or not math.isfinite(last) or last <= first or np.any(np.diff(times) < 0):
        return None
    return window[0] - first, first, last


def _identity_matches(identity, telemetry):
    meta = telemetry.metadata
    if not isinstance(identity, dict) or not is_race(identity.get('session_type')):
        return False
    if str(identity.get('game') or 'iracing').casefold() != str(meta.get('game') or 'iracing').casefold():
        return False
    return all(identity.get(key) and meta.get(key) and str(identity[key]).casefold() == str(meta[key]).casefold()
               for key in ('car_path', 'track_path'))


def _old_run_matches(database, run_id, telemetry, clock):
    """Legacy log: require two independently stored events with matching clocks."""
    if not database.is_file() or not isinstance(run_id, str) or not run_id:
        return False
    anchor, first, last = clock
    try:
        with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
            records = db.execute('SELECT created_at, event_json FROM radio_delivery WHERE run_id=? AND '
                                 'created_at BETWEEN ? AND ? ORDER BY id LIMIT 2000',
                                 (run_id, anchor + first - 5, anchor + last + 5)).fetchall()
        stamps = set()
        for created, raw in records:
            item = json.loads(raw)
            at = _number(item.get('session_time'))
            wall = _number(item.get('at')) or _number(created)
            if (at is not None and wall is not None and first <= at <= last
                    and abs(wall - at - anchor) <= 5 and _identity_matches(item.get('session'), telemetry)):
                stamps.add(at)
        return len(stamps) >= 2 and max(stamps) - min(stamps) >= 1
    except (sqlite3.Error, ValueError, TypeError):
        return False


def _timing(telemetry, folder, database):
    from timing_log import FORMAT
    clock = _clock(telemetry)
    if clock is None or not folder.is_dir():
        return None
    anchor, first, last = clock
    candidates = []
    for path in sorted(folder.glob('timing-*.jsonl.gz'))[-60:]:
        try:
            with gzip.open(path, 'rt', encoding='utf-8') as handle:
                header = json.loads(handle.readline(262144))
            if header.get('format') != FORMAT:
                continue
            recorded_anchor = _number(header.get('session_utc_offset'))
            matches = (_identity_matches(header.get('identity'), telemetry) and recorded_anchor is not None
                       and abs(recorded_anchor - anchor) <= 5) if 'identity' in header else _old_run_matches(
                           database, header.get('run_id'), telemetry, clock)
            if matches:
                candidates.append((path, header))
        except (OSError, ValueError, EOFError):
            continue
    # Several app runs at the same time are ambiguous; never combine them.
    if len(candidates) != 1:
        return None
    path, header = candidates[0]
    fields = header.get('fields') or []
    if not {'idx', 'position'}.issubset(fields):
        return None
    series = {}
    cars = {str(c['idx']): c for c in header.get('entries', []) if isinstance(c, dict) and 'idx' in c}
    step = max(1., (last - first) / MAX_FRAMES)
    previous = None
    with gzip.open(path, 'rt', encoding='utf-8') as handle:
        next(handle, None)
        for line in handle:
            item = json.loads(line)
            if item.get('format') == FORMAT:
                return None  # Multiple runs inside one old file cannot be safely joined.
            at = _number(item.get('t'))
            if at is None or not first <= at <= last or (previous is not None and at <= previous):
                continue
            if previous is not None and at - previous < step:
                continue
            observed = {}
            for values in (item.get('c') or [])[:MAX_CARS]:
                car = dict(zip(fields, values))
                idx = car.get('idx')
                if type(idx) is int and 0 <= idx < MAX_CARS and not cars.get(str(idx), {}).get('is_pace_car'):
                    observed[str(idx)] = car
            own_class = observed.get(str(item.get('own')), {}).get('class_id')
            if own_class is None:
                own_class = next((r['class_id'] for r in series.values() if r['own']), None)
            for idx, car in observed.items():
                if own_class is None or str(car.get('class_id')) != str(own_class):
                    continue
                if idx not in series:
                    entry = cars.get(idx, {})
                    cls = str(car.get('class_id')) if car.get('class_id') is not None else None
                    label = (header.get('classes', {}).get(cls) or {}).get('name')
                    series[idx] = _series(idx, cls, entry.get('number'), car.get('idx') == item.get('own'), label)
            gap = previous is not None and at - previous > max(3, step * 2.5)
            for idx, row in series.items():
                car = observed.get(idx, {})
                _append(row, at, _rank(car.get('position')), _rank(car.get('class_position')), gap)
            previous = at
    return _result(series, 'LOCAL_TIMING', step)


def read_history(telemetry, *, timing_folder=None, voice_database=None):
    """No persistence, cloud calls or changes to the selected analysis."""
    if not is_race(telemetry.metadata.get('session_type')):
        return _empty('레이스·인듀런스 경기 기록에서 사용할 수 있습니다.')
    before = file_signature(telemetry.path)
    if telemetry.path.with_name(telemetry.path.name + '.wal').exists():
        raise ValueError('아직 기록 중인 파일입니다. 기록이 종료된 뒤 다시 확인하세요.')
    result = _native(telemetry)
    if not result or result.get('status') != 'READY':
        from app_settings import data_dir
        folder = Path(timing_folder) if timing_folder is not None else Path(os.getenv('IRACING_TIMING_LOG_DIR') or data_dir() / 'timing-logs')
        database = Path(voice_database) if voice_database is not None else Path(os.getenv('IRACING_VOICE_DB_PATH') or os.getenv('IRACING_TUNER_DB_PATH') or os.getenv('IRACING_MEMORY_DB_PATH') or data_dir() / 'iracing_tuner.sqlite3')
        result = _timing(telemetry, folder, database) or result
    if file_signature(telemetry.path) != before:
        raise ValueError('주행 기록이 바뀌었습니다. 파일을 다시 선택하세요.')
    return result or _empty('순위 이력이 저장되지 않은 기록입니다. 앱을 켜고 기록한 다음 경기부터 확인할 수 있습니다.')
