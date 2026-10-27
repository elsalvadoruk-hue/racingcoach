"""Compact, source-bound observations for the local driver history foundation.

These ranges describe recorded observations, not driving time, distance, an
official lap verdict or a growth assessment. Importing this module opens no
stores; the caller supplies the recording's already-computed content hash.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import re

import numpy as np


SCHEMA_VERSION = 1
_SHA256 = re.compile(r'^[0-9a-fA-F]{64}$')


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False).encode('utf-8')).hexdigest()


def _integer(value, minimum=0):
    if isinstance(value, (bool, np.bool_)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return int(number) if math.isfinite(number) and number.is_integer() and number >= minimum else None


def _text(value):
    return ' '.join(str(value or '').split()).casefold()[:256]


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _rows(value):
    return value if isinstance(value, (list, tuple)) else ()


def _kind(value):
    value = _text(value)
    if value in {'practice', 'test', 'testing', 'warmup', 'warm up', 'lone practice'}:
        return 'PRACTICE'
    if value in {'qualify', 'qualifying', 'open qualify', 'lone qualify', 'qualification'}:
        return 'QUALIFY'
    if value in {'race', 'endurance', 'endurance race'}:
        return 'RACE'
    return 'UNKNOWN'


def _array(channels, key, length=None):
    try:
        array = np.asarray(channels[key], dtype=float)
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if array.ndim != 1 or (length is not None and len(array) != length):
        return None
    return array


def _context(telemetry, game):
    metadata = _mapping(getattr(telemetry, 'metadata', None))
    info = _mapping(getattr(telemetry, 'session_info', None))
    weekend = _mapping(info.get('WeekendInfo'))
    driver_info = _mapping(info.get('DriverInfo'))
    own_idx = _integer(driver_info.get('DriverCarIdx'))
    owners = [driver for driver in _rows(driver_info.get('Drivers'))
              if isinstance(driver, dict) and own_idx is not None
              and _integer(driver.get('CarIdx')) == own_idx]
    own = owners[0] if len(owners) == 1 else {}
    car_id = _integer(metadata.get('car_platform_id'), 1) or _integer(own.get('CarID'), 1)
    track_id = _integer(metadata.get('track_platform_id'), 1) or _integer(weekend.get('TrackID'), 1)
    car_identity = str(car_id) if car_id else _text(metadata.get('car_path') or own.get('CarPath'))
    track_identity = str(track_id) if track_id else _text(metadata.get('track_path'))
    car = car_identity or _text(metadata.get('car_name'))
    track = track_identity or _text(weekend.get('TrackName') or metadata.get('track_name'))
    layout = _text(metadata.get('track_config') or metadata.get('track_layout')
                   or weekend.get('TrackConfigName'))
    # IDs are used locally only inside the opaque identity digest, never returned.
    native = None
    session_id, subsession_id = (_integer(weekend.get(key), 1) for key in ('SessionID', 'SubSessionID'))
    user_id = _integer(own.get('UserID'), 1)
    if (game == 'iracing' and session_id and subsession_id and user_id and car_identity and track_identity
            and (track_id or layout)):
        native = [game, session_id, subsession_id, user_id, car, track, layout]
    sessions = {}
    for row in _rows(_mapping(info.get('SessionInfo')).get('Sessions')):
        if isinstance(row, dict) and (num := _integer(row.get('SessionNum'))) is not None:
            # Conflicting native metadata does not establish a session kind.
            value = _kind(row.get('SessionType'))
            sessions[num] = value if num not in sessions or sessions[num] == value else 'UNKNOWN'
    return metadata, car or 'UNKNOWN', track or 'UNKNOWN', layout, native, sessions


def _utc_anchor(telemetry, times, session_nums, reversed_clock, tick_rate, game):
    if reversed_clock or len(times) < 2 or not np.all(np.isfinite(times[[0, -1]])):
        return None
    if game == 'iracing' and (session_nums is None or not np.all(np.isfinite(session_nums))):
        return None
    known = session_nums[np.isfinite(session_nums)] if session_nums is not None else np.array([])
    if len(np.unique(known)) > 1 or times[-1] <= times[0]:
        return None
    from session_log_extras import recording_window
    window = recording_window(telemetry.path)
    if window is None:
        return None
    tolerance = max(2. / tick_rate, .01) if tick_rate else .01
    if abs((window[1] - window[0]) - (times[-1] - times[0])) > tolerance:
        return None
    anchor = float(window[0] - times[0])
    try:
        datetime.fromtimestamp(anchor + times[0], timezone.utc)
        datetime.fromtimestamp(anchor + times[-1], timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return anchor


def compact_lmu_clock(gps_time, frequency_hz, recording_time):
    """Summarize an already-read native GPS clock without retaining its array.

    LMU's analysis axis is resampled to 60 Hz and cannot prove the original
    sample count or continuity. Keep only native contiguous ranges and epochs.
    No recording or settings file is opened by this helper.
    """
    compact = dict(schema_version=1, source='LMU_GPS_TIME', epochs=[])
    times = _array({'GPS Time': gps_time}, 'GPS Time')
    if times is None or not len(times):
        return compact
    rate = (float(frequency_hz) if type(frequency_hz) in (int, float)
            and math.isfinite(frequency_hz) and frequency_hz > 0 else None)
    indices = np.flatnonzero(np.isfinite(times) & (times >= 0))
    if not len(indices):
        return compact
    resets = indices[1:][np.diff(times[indices]) < 0]
    from lmu_lap_verdicts import _recording_start
    started = _recording_start(recording_time) if not len(resets) else None
    if started is not None and started <= 0:
        started = None
    from session_log_extras import MAX_RECORDING_SECONDS
    anchor = None
    if (started is not None and np.all(np.isfinite(times[[0, -1]])) and times[0] >= 0
            and 0 < times[-1] - times[0] <= MAX_RECORDING_SECONDS):
        try:
            datetime.fromtimestamp(started, timezone.utc)
            datetime.fromtimestamp(started + float(times[-1] - times[0]), timezone.utc)
            anchor = started - float(times[0])
        except (OverflowError, OSError, ValueError):
            pass
    boundaries = np.r_[0, resets, len(times)]
    for left, right in zip(boundaries[:-1], boundaries[1:]):
        observed = indices[(indices >= left) & (indices < right)]
        if not len(observed):
            continue
        reasons = {'NATIVE_IDENTITY_UNAVAILABLE', 'REPLAY_STATE_UNAVAILABLE'}
        if anchor is None:
            reasons.add('RECORDING_UTC_UNAVAILABLE')
        if len(resets):
            reasons.add('SESSION_CLOCK_RESET')
        if not np.all(np.isfinite(times[left:right]) & (times[left:right] >= 0)):
            reasons.add('INVALID_SESSION_TIME')
        if rate is None:
            reasons.add('SAMPLING_RATE_UNAVAILABLE')
        delta = np.diff(times[observed])
        maximum_gap = 1.5 / rate if rate else 0.
        adjacent = (np.diff(observed) == 1) & (delta > 0) & (delta <= maximum_gap)
        starts = np.r_[0, np.flatnonzero(~adjacent) + 1]
        ends = np.r_[starts[1:] - 1, len(observed) - 1]
        if rate and np.any(delta > maximum_gap):
            reasons.add('SAMPLE_GAP')
        if np.any(delta == 0):
            reasons.add('DUPLICATE_SESSION_TIME')
        offset = anchor if anchor is not None else -float(times[observed[0]])
        intervals = [[round(float(times[observed[start]]) + offset, 6),
                      round(float(times[observed[end]]) + offset, 6)]
                     for start, end in zip(starts, ends) if end > start]
        if not intervals:
            reasons.add('INSUFFICIENT_CONTIGUOUS_SAMPLES')
        stamp = (datetime.fromtimestamp(float(times[observed[0]]) + anchor, timezone.utc).isoformat()
                 if anchor is not None else None)
        compact['epochs'].append(dict(recorded_at=stamp,
            coverage=dict(time_basis='UTC' if anchor is not None else 'SOURCE_RELATIVE',
                          intervals=intervals, status='PARTIAL' if intervals else 'UNAVAILABLE',
                          sample_count=int(len(observed)), reasons=sorted(reasons))))
    return compact


def _lmu_observations(telemetry, source_id):
    metadata, car, track, layout, _, _ = _context(telemetry, 'lmu')
    evidence = _mapping(getattr(telemetry, '_driver_history_lmu_clock', None))
    epochs = (_rows(evidence.get('epochs')) if evidence.get('schema_version') == 1
              and evidence.get('source') == 'LMU_GPS_TIME' else ())
    if not epochs:
        epochs = [dict(recorded_at=None, coverage=dict(time_basis='UNKNOWN', intervals=[], status='UNAVAILABLE',
            sample_count=0, reasons=['LMU_NATIVE_CLOCK_UNAVAILABLE', 'NATIVE_IDENTITY_UNAVAILABLE',
                                    'RECORDING_UTC_UNAVAILABLE', 'REPLAY_STATE_UNAVAILABLE']))]
    kind = _kind(metadata.get('session_type'))
    observations = []
    for segment, epoch in enumerate(epochs):
        coverage = epoch['coverage']
        reasons = set(coverage['reasons'])
        if kind == 'UNKNOWN':
            reasons.add('SESSION_TYPE_UNAVAILABLE')
        observation = dict(drive_id=_digest(['source', source_id, segment]), identity_status='SOURCE_ONLY',
            game='lmu', car_key=car, track_key=track, layout_key=layout,
            session_type=kind, session_type_source='LMU_RECORDING_METADATA' if kind != 'UNKNOWN' else 'UNKNOWN',
            recorded_at=epoch['recorded_at'], recorded_at_source='LMU_RECORDING_TIME' if epoch['recorded_at'] else 'UNKNOWN',
            coverage=dict(time_basis=coverage['time_basis'], intervals=[list(item) for item in coverage['intervals']],
                status=coverage['status'], sample_count=coverage['sample_count'], reasons=sorted(reasons)),
            validity_status='UNKNOWN', conditions={})
        # The analysis axis has no safe mapping after a native clock reset.
        # Other epochs use the already-recorded UTC or source-relative origin.
        channels = _mapping(getattr(telemetry, 'channels', None))
        analysis_time = _array(channels, 'SessionTime')
        offset = None
        if analysis_time is not None and len(analysis_time) and np.isfinite(analysis_time[0]):
            if epoch['recorded_at']:
                offset = datetime.fromisoformat(epoch['recorded_at']).timestamp() - float(analysis_time[0])
            elif coverage['time_basis'] == 'SOURCE_RELATIVE' and 'SESSION_CLOCK_RESET' not in reasons:
                offset = -float(analysis_time[0])
        _add_details(telemetry, observation, offset=offset)
        observations.append(observation)
    return observations


def _add_details(telemetry, observation, *, start_idx=0, end_idx=None, offset=None):
    from driver_history_metrics import build_observation_metrics, display_labels, unavailable_metrics
    observation['labels'] = display_labels(getattr(telemetry, 'metadata', None))
    try:
        observation['metrics'] = (build_observation_metrics(telemetry, observation,
            start_idx=start_idx, end_idx=end_idx, offset=offset) if offset is not None
            else unavailable_metrics('SOURCE_TIME_MAPPING_UNAVAILABLE'))
    except Exception:
        # Optional report inputs never turn a usable existing analysis into an
        # error, and exception text may contain private paths or source values.
        observation['metrics'] = unavailable_metrics('METRICS_EXTRACTION_FAILED')


def build_source_summary(telemetry, source_id):
    """Return compact facts without changing telemetry, hashing it or writing files.

    A native event/driver identity can unite split recordings. Missing native
    identity stays scoped to this content hash and segment. A file's name,
    modification time and analysis mode never establish identity or its date.
    """
    if not isinstance(source_id, str) or not _SHA256.fullmatch(source_id):
        raise ValueError('source_id must be a content SHA256')
    source_id = source_id.lower()
    metadata = _mapping(getattr(telemetry, 'metadata', None))
    game = 'lmu' if _text(metadata.get('game')) == 'lmu' else 'iracing'
    result = dict(schema_version=SCHEMA_VERSION, source_id=source_id, game=game, observations=[])
    if game == 'lmu':
        result['observations'] = _lmu_observations(telemetry, source_id)
        return result
    channels = _mapping(getattr(telemetry, 'channels', None))
    times = _array(channels, 'SessionTime')
    if times is None or not len(times):
        return result
    n = len(times)
    nums = _array(channels, 'SessionNum', n)
    if nums is not None:
        nums = np.where(np.isfinite(nums) & (nums >= 0) & (nums == np.floor(nums)), nums, np.nan)
    replay = _array(channels, 'IsReplayPlaying', n)
    replay_known = np.isfinite(replay) & np.isin(replay, [0., 1.]) if replay is not None else np.zeros(n, dtype=bool)
    is_replay = replay == 1. if replay is not None else np.zeros(n, dtype=bool)
    valid = np.isfinite(times) & (times >= 0) & ~is_replay
    if not np.any(valid):
        return result
    metadata, car, track, layout, native, sessions = _context(telemetry, game)
    rate = getattr(telemetry, 'tick_rate', 0)
    rate = float(rate) if type(rate) in (int, float) and math.isfinite(rate) and rate > 0 else None
    maximum_gap = 1.5 / rate if rate else 0.
    finite_indices = np.flatnonzero(np.isfinite(times) & (times >= 0))
    resets = finite_indices[1:][np.diff(times[finite_indices]) < 0]
    reversed_clock = bool(len(resets))
    anchor = _utc_anchor(telemetry, times, nums, reversed_clock, rate, game)
    num_values = nums if nums is not None else np.full(n, np.nan)
    same_num = (num_values[1:] == num_values[:-1]) | (np.isnan(num_values[1:]) & np.isnan(num_values[:-1]))
    # A reset creates a new source-scoped epoch; its clock is not safely shared
    # with another file, even if the native event metadata remained unchanged.
    breaks = ~same_num
    breaks[resets - 1] = True
    boundaries = np.flatnonzero(np.r_[True, breaks, True])
    groups = {}
    for segment, (left, right) in enumerate(zip(boundaries[:-1], boundaries[1:])):
        indices = np.flatnonzero(valid[left:right]) + left
        if not len(indices):
            continue
        number = _integer(num_values[left])
        strong = native is not None and number is not None and not reversed_clock
        drive_id = _digest(['native', *native, number]) if strong else _digest(['source', source_id, segment])
        if game == 'lmu':
            kind, kind_source = _kind(metadata.get('session_type')), 'LMU_RECORDING_METADATA'
        else:
            kind = sessions.get(number, 'UNKNOWN') if number is not None else 'UNKNOWN'
            kind_source = 'IRACING_SESSION_NUM' if number is not None and number in sessions else 'UNKNOWN'
        if kind == 'UNKNOWN':
            kind_source = 'UNKNOWN'
        reasons = set()
        if not strong:
            reasons.add('NATIVE_IDENTITY_UNAVAILABLE')
        if kind == 'UNKNOWN':
            reasons.add('SESSION_TYPE_UNAVAILABLE')
        if anchor is None:
            reasons.add('RECORDING_UTC_UNAVAILABLE')
        if not np.all(replay_known[left:right]):
            reasons.add('REPLAY_STATE_UNAVAILABLE')
        if np.any(is_replay[left:right]):
            reasons.add('REPLAY_EXCLUDED')
        if not np.all(np.isfinite(times[left:right]) & (times[left:right] >= 0)):
            reasons.add('INVALID_SESSION_TIME')
        if reversed_clock:
            reasons.add('SESSION_CLOCK_RESET')
        if rate is None:
            reasons.add('SAMPLING_RATE_UNAVAILABLE')
        adjacent = (np.diff(indices) == 1) & (np.diff(times[indices]) > 0) & (np.diff(times[indices]) <= maximum_gap)
        starts = np.r_[0, np.flatnonzero(~adjacent) + 1]
        ends = np.r_[starts[1:] - 1, len(indices) - 1]
        if rate and np.any(np.diff(times[indices]) > maximum_gap):
            reasons.add('SAMPLE_GAP')
        if np.any(np.diff(times[indices]) == 0):
            reasons.add('DUPLICATE_SESSION_TIME')
        basis = 'UTC' if anchor is not None else ('NATIVE_SESSION_TIME' if game == 'iracing' else 'SOURCE_RELATIVE')
        offset = anchor if anchor is not None else (-float(times[indices[0]]) if basis == 'SOURCE_RELATIVE' else 0.)
        intervals = [[round(float(times[indices[start]]) + offset, 6),
                      round(float(times[indices[end]]) + offset, 6)]
                     for start, end in zip(starts, ends) if end > start]
        if not intervals:
            reasons.add('INSUFFICIENT_CONTIGUOUS_SAMPLES')
        stamp = datetime.fromtimestamp(float(times[indices[0]]) + anchor, timezone.utc).isoformat() if anchor is not None else None
        observation = dict(drive_id=drive_id, identity_status='NATIVE' if strong else 'SOURCE_ONLY',
                           game=game, car_key=car, track_key=track, layout_key=layout,
                           session_type=kind, session_type_source=kind_source, recorded_at=stamp,
                           recorded_at_source=('IBT_DISK_SUBHEADER' if game == 'iracing' else 'LMU_RECORDING_TIME') if stamp else 'UNKNOWN',
                           coverage=dict(time_basis=basis, intervals=intervals, status=('UNAVAILABLE' if not intervals else ('PARTIAL' if reasons else 'OBSERVED')),
                                         sample_count=int(len(indices)), reasons=sorted(reasons)),
                           validity_status='UNKNOWN', conditions={})
        _add_details(telemetry, observation, start_idx=left, end_idx=right, offset=offset)
        if drive_id in groups:
            existing = groups[drive_id]
            existing['coverage']['intervals'].extend(intervals)
            existing['coverage']['sample_count'] += len(indices)
            existing['coverage']['reasons'] = sorted(set(existing['coverage']['reasons']) | reasons)
            existing['coverage']['status'] = ('UNAVAILABLE' if not existing['coverage']['intervals'] else
                                              ('PARTIAL' if existing['coverage']['reasons'] else 'OBSERVED'))
            from driver_history_metrics import merge_observation_metrics
            existing['metrics'] = merge_observation_metrics(existing['metrics'], observation['metrics'])
        else:
            groups[drive_id] = observation
    result['observations'] = list(groups.values())
    return result
