"""Whole-lap pedal observations on supported native time intervals only."""
from __future__ import annotations

import math
import numpy as np


METHOD = 'NATIVE_TIME_THRESHOLD_V1'
THRESHOLD_RATIO = .05
MAX_NATIVE_SAMPLES = 2_000_000
CATEGORY_KEYS = ('THROTTLE_ONLY', 'BRAKE_ONLY', 'OVERLAP', 'BOTH_LOW')
_STAGES = ('UNFILTERED', 'GAME_PROCESSED')


def _finite_number(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def _validate_trace(trace):
    if trace.unit != 'ratio' or trace.interpolation not in ('LINEAR', 'STEP'):
        raise ValueError('페달 단위 또는 보간 방식을 확인할 수 없습니다.')
    if not _finite_number(trace.frequency_hz) or trace.frequency_hz <= 0:
        raise ValueError('페달의 원본 주파수를 확인할 수 없습니다.')
    times, values, mask = trace.time_s, trace.values, trace.valid_mask
    if (not all(isinstance(a, np.ndarray) and a.ndim == 1 for a in (times, values, mask)) or
            times.shape != values.shape or times.shape != mask.shape or
            times.dtype.kind not in 'fiu' or values.dtype.kind not in 'fiu' or mask.dtype.kind != 'b' or
            not np.all(np.isfinite(times)) or np.any(np.diff(times) <= 0)):
        raise ValueError('페달의 원본 시간축 또는 표본 형식이 올바르지 않습니다.')


def _intervals(trace, start, end):
    """Disjoint [left,right) intervals whose threshold state is constant."""
    times, values = trace.time_s, trace.values
    valid = trace.valid_mask & np.isfinite(values) & (values >= 0) & (values <= 1)
    widths = np.diff(times)
    left, right = np.maximum(times[:-1], start), np.minimum(times[1:], end)
    supported = (valid[:-1] & valid[1:] & (widths <= 1.5 / trace.frequency_hz + 1e-9) &
                 (right > left))
    left, right = left[supported], right[supported]
    first, last = values[:-1][supported], values[1:][supported]
    if trace.interpolation == 'STEP':
        active = first >= THRESHOLD_RATIO
    else:
        t0, width = times[:-1][supported], widths[supported]
        # Work in fractions rather than slope to avoid division overflow on
        # extremely short native intervals. Values remain in the ratio range.
        left_values = first + (last-first) * ((left-t0)/width)
        right_values = first + (last-first) * ((right-t0)/width)
        crossing = np.full(len(left), np.nan)
        changing = first != last
        crossing[changing] = t0[changing] + width[changing] * (
            (THRESHOLD_RATIO-first[changing])/(last[changing]-first[changing]))
        split = np.isfinite(crossing) & (crossing > left) & (crossing < right)
        counts = 1 + split.astype(int)
        indices = np.cumsum(counts)-counts
        pieces_left, pieces_right = np.repeat(left, counts), np.repeat(right, counts)
        active = np.repeat((left_values+right_values)/2 >= THRESHOLD_RATIO, counts)
        split_indices = indices[split]
        pieces_right[split_indices] = crossing[split]
        pieces_left[split_indices+1] = crossing[split]
        active[split_indices] = left_values[split] >= THRESHOLD_RATIO
        active[split_indices+1] = right_values[split] >= THRESHOLD_RATIO
        left, right = pieces_left, pieces_right
    if not len(left):
        return left, right, active
    # Most adjacent native edges have the same state. Merge them without
    # merging over a missing sample or a gap, keeping memory/time bounded.
    changes = np.r_[True, (left[1:] != right[:-1]) | (active[1:] != active[:-1])]
    starts = np.flatnonzero(changes)
    ends = np.r_[starts[1:]-1, len(left)-1]
    return left[starts], right[ends], active[starts]


def pedal_usage(lap):
    """Partition observed pedal time; 'both low' does not imply coasting."""
    start, end = getattr(lap, 'start_time_s', None), getattr(lap, 'end_time_s', None)
    duration = float(end-start) if (_finite_number(start) and _finite_number(end) and
                                   end > start and _finite_number(end-start)) else None
    result = {'status': 'UNAVAILABLE', 'method': METHOD, 'threshold_ratio': THRESHOLD_RATIO,
              'lap_duration_s': duration, 'observed_time_s': 0. if duration is not None else None,
              'unobserved_time_s': duration, 'coverage_ratio': 0. if duration is not None else None,
              'input_stage': None, 'reason': None,
              'categories': [{'key': key, 'duration_s': None, 'observed_percent': None}
                             for key in CATEGORY_KEYS]}
    if duration is None:
        result['reason'] = '랩의 시간 경계를 확인할 수 없습니다.'
        return result
    channels, support = getattr(lap, 'channels', None), getattr(lap, 'support', None)
    if not isinstance(channels, dict) or not isinstance(support, dict):
        result['reason'] = '페달 채널과 입력 처리 단계를 확인할 수 없습니다.'
        return result
    traces = [channels.get(key) for key in ('throttle_ratio', 'brake_ratio')]
    if any(trace is None for trace in traces):
        result['reason'] = '스로틀과 브레이크 채널이 모두 필요합니다.'
        return result
    try:
        if sum(len(trace.time_s) for trace in traces) > MAX_NATIVE_SAMPLES:
            raise ValueError('페달 원본 표본 수가 200만 개 제한을 초과했습니다.')
        for trace in traces:
            _validate_trace(trace)
        stages = [support.get(key, {}).get('input_stage') for key in ('throttle_ratio', 'brake_ratio')]
        if stages[0] not in _STAGES or stages[0] != stages[1]:
            raise ValueError('페달의 입력 처리 단계가 미확인이거나 서로 다릅니다.')
    except (ValueError, TypeError, AttributeError) as exc:
        result['reason'] = str(exc) if isinstance(exc, ValueError) else '페달의 원본 표본 형식이 올바르지 않습니다.'
        return result
    result['input_stage'] = stages[0]
    throttle, brake = [_intervals(trace, start, end) for trace in traces]
    totals = [0., 0., 0., 0.]
    # Intersect constant-state native intervals. Neither pedal's sample count
    # or display resolution is used as a time denominator.
    i = j = 0
    while i < len(throttle[0]) and j < len(brake[0]):
        left, right = max(throttle[0][i], brake[0][j]), min(throttle[1][i], brake[1][j])
        if right > left:
            active_throttle, active_brake = throttle[2][i], brake[2][j]
            category = 2 if active_throttle and active_brake else 0 if active_throttle else 1 if active_brake else 3
            totals[category] += float(right-left)
        if throttle[1][i] <= brake[1][j]:
            i += 1
        else:
            j += 1
    observed = math.fsum(totals)
    if observed <= 0:
        result['reason'] = '두 페달을 함께 관측한 연속 시간 구간이 없습니다.'
        return result
    complete = math.isclose(observed, duration, rel_tol=1e-12, abs_tol=1e-9)
    result.update(status='AVAILABLE' if complete else 'PARTIAL',
                  observed_time_s=observed, unobserved_time_s=max(0., duration-observed),
                  coverage_ratio=min(1., observed/duration),
                  reason=None if complete else '랩 경계·결측·원본 표본 간격의 미관측 시간을 제외했습니다.')
    result['categories'] = [{'key': key, 'duration_s': seconds, 'observed_percent': seconds/observed*100}
                            for key, seconds in zip(CATEGORY_KEYS, totals)]
    return result
