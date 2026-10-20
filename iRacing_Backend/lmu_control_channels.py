"""LMU control presentation helpers; no wheel-angle or initial-gear inference."""
from __future__ import annotations

from dataclasses import replace
import numpy as np

from telemetry_core import ChannelTrace


def with_gear_events(lap, events, native_time_axis):
    """Hold recorded Gear change events on the validated native timing axis.

    Event boundaries are included verbatim so shifts are steps, not ramps.
    Before the first observed event the gear is unknown. Zero (neutral) and -1
    (reverse) are recorded states and must never be replaced by guessed gears.
    The trace's frequency is the native timing grid's, not an event frequency.
    """
    support = dict(lap.support)
    channels = dict(lap.channels)
    info = {'source_name': 'Gear', 'unit': 'gear', 'frequency_hz': None,
            'sampling': 'CHANGE_EVENTS_HELD_ON_NATIVE_TIME_AXIS',
            'interpolation': 'STEP', 'neutral': 0, 'reverse': -1}
    support['gear'] = info
    if events is None or not len(events[0]):
        info.update(status='MISSING', reason='기록된 Gear 변경 이벤트가 없습니다.')
        return replace(lap, channels=channels, support=support)
    event_times, event_values = (np.asarray(value, dtype=float) for value in events)
    native = np.asarray(native_time_axis, dtype=float)
    if (len(native) < 2 or not np.all(np.isfinite(native)) or np.any(np.diff(native) <= 0)
            or event_times.shape != event_values.shape or not np.all(np.isfinite(event_times))
            or np.any(np.diff(event_times) <= 0)):
        info.update(status='UNAVAILABLE', reason='기어 이벤트 또는 기준 시간축을 확인할 수 없습니다.')
        return replace(lap, channels=channels, support=support)
    period = float(np.median(np.diff(native)))
    if (not np.allclose(np.diff(native), period, rtol=0, atol=1e-5)
            or lap.start_time_s < native[0] or lap.end_time_s > native[-1] + period):
        info.update(status='UNAVAILABLE', reason='기어 상태를 연결할 연속된 원본 시간축이 없습니다.')
        return replace(lap, channels=channels, support=support)
    times = np.unique(np.r_[lap.start_time_s, lap.end_time_s,
        native[(native >= lap.start_time_s) & (native <= lap.end_time_s)],
        event_times[(event_times >= lap.start_time_s) & (event_times <= lap.end_time_s)]])
    indices = np.searchsorted(event_times, times, side='right') - 1
    values = np.full(len(times), np.nan)
    known = indices >= 0
    values[known] = event_values[indices[known]]
    # LMU reports reverse=-1, neutral=0 and forward integer gears. Other values
    # stay missing, including a fractional value between two real shifts.
    valid = np.isfinite(values) & (values == np.floor(values)) & (values >= -1) & (values <= 10)
    channels['gear'] = ChannelTrace('Gear', 'gear', 1 / period, times, values, valid, 'STEP')
    count = int(np.count_nonzero(~valid))
    info.update(status='AVAILABLE' if count == 0 else 'PARTIAL',
        sample_count=len(times), invalid_samples=count, derived_frequency_hz=1 / period,
        full_lap_coverage=count == 0)
    return replace(lap, channels=channels, support=support)


def control_values(lap, times, channel):
    """JSON-safe native control values for shared comparison and overlay rows."""
    trace = lap.channels.get(channel)
    if trace is None:
        return [None] * len(times)
    values = trace.at(times)
    return [float(value) if np.isfinite(value) else None for value in values]
