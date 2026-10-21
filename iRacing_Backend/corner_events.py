"""Corner input events on recorded samples; never sort across missing observations."""
from __future__ import annotations

from dataclasses import dataclass, field
import numpy as np

from telemetry_core import AnalysisLap, ChannelTrace, input_zones


@dataclass(frozen=True)
class RawLap:
    time_s: np.ndarray
    pct: np.ndarray
    channels: dict[str, np.ndarray]
    tick_s: float
    track_length_m: float
    source: str = 'NATIVE'
    _zones: dict = field(default_factory=dict, compare=False, repr=False)


def raw_lap(analyzer, lap) -> RawLap | None:
    channels = analyzer.channels
    if 'SessionTime' not in channels or 'LapDistPct' not in channels:
        return None
    span = slice(lap.start_idx, lap.end_idx + 1)
    times, pct = (np.asarray(channels[key][span], dtype=float)
                  for key in ('SessionTime', 'LapDistPct'))
    tick_rate = analyzer.telemetry.tick_rate
    if len(times) < 2 or tick_rate <= 0 or len(times) != len(pct):
        return None
    return RawLap(times, pct, {k: np.asarray(v[span], dtype=float) for k, v in channels.items()
                             if v is not None and np.ndim(v) == 1 and len(v) == len(channels['SessionTime'])},
                  1. / tick_rate, analyzer.track_length_m,
                  'RESAMPLED' if analyzer.telemetry.metadata.get('game') == 'lmu' else 'NATIVE')


def _edges(raw):
    dt, dp = np.diff(raw.time_s), np.diff(raw.pct)
    return (np.isfinite(dt) & np.isfinite(raw.pct[:-1]) & np.isfinite(raw.pct[1:]) &
            (dt > 0) & (dt <= 1.5 * raw.tick_s + 1e-9) &
            (dp >= 0) & (dp * raw.track_length_m <= 25.))


def _window_ok(raw, low, high, keys):
    """A missing interval invalidates only metrics using that search window."""
    inside = np.isfinite(raw.pct) & (raw.pct >= low) & (raw.pct <= high)
    indices = np.flatnonzero(inside)
    if not len(indices):
        return False
    start, end = max(0, indices[0] - 1), min(len(raw.pct) - 1, indices[-1] + 1)
    if not np.all(np.isfinite(raw.pct[indices[0]:indices[-1] + 1])):
        return False
    # Include intervals which straddle a boundary, but not unrelated intervals.
    left = np.minimum(raw.pct[start:end], raw.pct[start + 1:end + 1])
    right = np.maximum(raw.pct[start:end], raw.pct[start + 1:end + 1])
    overlaps = (right > low) & (left < high)
    if np.any(~_edges(raw)[start:end] & overlaps):
        return False
    if not np.all(np.isfinite(raw.time_s[inside])):
        return False
    for key in keys:
        values = raw.channels.get(key)
        if values is None or not np.all(np.isfinite(values[inside])):
            return False
        valid_pairs = np.isfinite(values[start:end]) & np.isfinite(values[start + 1:end + 1])
        if np.any(~valid_pairs & overlaps):
            return False
    return True


def _event(raw, pct, time_s, index, clipped=False):
    if len(raw.pct) > 1:
        index = min(max(0, index), len(raw.pct) - 2)
        spacing = abs(float(raw.pct[index + 1] - raw.pct[index])) * raw.track_length_m
    else:
        spacing = np.nan
    if not np.isfinite(spacing) or spacing > 25.:
        speed = raw.channels.get('Speed')
        spacing = abs(float(speed[index])) * raw.tick_s if speed is not None else np.nan
    return {'pct': float(pct), 'time_s': float(time_s),
            'uncertainty_m': float(spacing if clipped else spacing / 2) if np.isfinite(spacing) else None,
            'source': 'CLIPPED' if clipped else raw.source}


def _crossings(raw, key, threshold, low, high, rising, first):
    values = raw.channels.get(key)
    if values is None:
        return []
    edges = _edges(raw)
    crossing_mask = ((values[:-1] <= threshold) & (values[1:] > threshold) if rising else
                     (values[:-1] > threshold) & (values[1:] <= threshold))
    hits = []
    for i in np.flatnonzero(edges & crossing_mask):
        fraction = (threshold - values[i]) / (values[i + 1] - values[i])
        pct = raw.pct[i] + fraction * (raw.pct[i + 1] - raw.pct[i])
        if low <= pct <= high:
            hits.append(_event(raw, pct, raw.time_s[i] + fraction *
                               (raw.time_s[i + 1] - raw.time_s[i]), int(i)))
    indices = np.flatnonzero((raw.pct >= low) & (raw.pct <= high) & np.isfinite(raw.time_s))
    if len(indices):
        edge = int(indices[0] if first else indices[-1])
        # A control held at the window edge has no observed crossing in that window.
        if np.isfinite(values[edge]) and values[edge] > threshold:
            clipped = _event(raw, raw.pct[edge], raw.time_s[edge], edge, True)
            if first and (not hits or clipped['pct'] < hits[0]['pct']):
                hits.insert(0, clipped)
            elif not first and (not hits or clipped['pct'] > hits[-1]['pct']):
                hits.append(clipped)
    return hits


def crossing(raw, key, threshold, start_pct, end_pct, rising: bool, first: bool):
    hits = _crossings(raw, key, threshold, start_pct, end_pct, rising, first)
    return (hits[0] if first else hits[-1]) if hits else None


def _input_zones(raw, key, on, off):
    """Adapt continuous recorded blocks to the existing hysteresis/debounce logic."""
    cache_key = (key, on, off)
    if cache_key in raw._zones:
        return raw._zones[cache_key]
    zones = []
    boundaries = np.r_[0, np.flatnonzero(~_edges(raw)) + 1, len(raw.pct)]
    for start, end in zip(boundaries[:-1], boundaries[1:]):
        if end - start < 2 or not np.all(np.isfinite(raw.time_s[start:end])):
            continue
        times = raw.time_s[start:end]
        channels = {}
        for name, values in ((key, raw.channels[key]), ('distance_m', raw.pct * raw.track_length_m)):
            values = values[start:end]
            channels[name] = ChannelTrace(name, '', 1 / raw.tick_s, times, values, np.isfinite(values))
        lap = AnalysisLap(raw.source, 0, float(times[0]), float(times[-1]), None, True,
                          'OBSERVED', {}, channels, {})
        for zone in input_zones(lap, key, on, off, minimum_duration_s=.15):
            zones.append((times[0] + zone['start_elapsed_s'], times[0] + zone['end_elapsed_s']))
    raw._zones[cache_key] = zones
    return zones


def _sustained(raw, key, on, off, low, high):
    if not _window_ok(raw, low, high, (key,)):
        return None
    zones = _input_zones(raw, key, on, off)
    for event in _crossings(raw, key, on, low, high, True, True):
        if any(start - 1e-8 <= event['time_s'] and end - event['time_s'] >= .15 - 1e-8
               for start, end in zones):
            return event
    return None


def _intervals(raw, low, high):
    """Supported time intervals, clipped to the requested distance window."""
    dp, dt = np.diff(raw.pct), np.diff(raw.time_s)
    for i in np.flatnonzero(_edges(raw) & (raw.pct[1:] >= low) & (raw.pct[:-1] <= high)):
        if dp[i] > 0:
            a, b = max(0., (low - raw.pct[i]) / dp[i]), min(1., (high - raw.pct[i]) / dp[i])
        else:
            a, b = 0., 1.
        if b > a:
            yield i, a, b, dt[i]


def _abs(raw, low, high):
    if 'BrakeABSactive' not in raw.channels or not _window_ok(raw, low, high, ('Brake',)):
        return None, None
    brake, active = raw.channels['Brake'], raw.channels['BrakeABSactive']
    braking = observed = engaged = 0.
    for i, left, right, dt in _intervals(raw, low, high):
        slope = brake[i + 1] - brake[i]
        if slope > 0:
            left = max(left, (.08 - brake[i]) / slope)
        elif slope < 0:
            right = min(right, (.08 - brake[i]) / slope)
        elif brake[i] <= .08:
            continue
        seconds = max(0., right - left) * dt
        braking += seconds
        if np.isfinite(active[i]):
            observed += seconds
            if active[i] > .5:
                engaged += seconds
    return (100 * engaged / observed if observed >= .3 - 1e-9 else None,
            observed / braking if braking > 0 else None)


def corner_events(raw, entry_pct, apex_pct, exit_pct, pad_m=75.) -> dict:
    pad = pad_m / raw.track_length_m
    pre, post = max(0., entry_pct - pad), min(1., exit_pct + pad)
    brake_start = _sustained(raw, 'Brake', .08, .05, pre, apex_pct)
    brake_end = min(1., apex_pct + pad)
    brake_release = (crossing(raw, 'Brake', .05, entry_pct, brake_end, False, False)
                     if _window_ok(raw, entry_pct, brake_end, ('Brake',)) else None)
    throttle_first = (crossing(raw, 'Throttle', .20, apex_pct, post, True, True)
                      if _window_ok(raw, apex_pct, post, ('Throttle',)) else None)
    full_throttle = _sustained(raw, 'Throttle', .95, .90, apex_pct, post)
    minimum = None
    if _window_ok(raw, entry_pct, exit_pct, ('Speed',)):
        indices = np.flatnonzero((raw.pct >= entry_pct) & (raw.pct <= exit_pct))
        i = int(indices[np.argmin(raw.channels['Speed'][indices])])
        minimum = _event(raw, raw.pct[i], raw.time_s[i], i)
    abs_share, observed_share = _abs(raw, entry_pct, apex_pct)
    coast = None
    if _window_ok(raw, entry_pct, apex_pct, ('Brake', 'Throttle', 'Speed')):
        coast = sum((b - a) * dt for i, a, b, dt in _intervals(raw, entry_pct, apex_pct)
                    if raw.channels['Brake'][i] < .05 and raw.channels['Throttle'][i] < .05
                    and raw.channels['Speed'][i] * 3.6 > 50.)
    relifts = None
    if throttle_first is not None and _window_ok(raw, throttle_first['pct'], exit_pct, ('Throttle',)):
        relifts, last_power = 0, None
        for i in np.flatnonzero((raw.pct >= throttle_first['pct']) & (raw.pct <= exit_pct)):
            value, time = raw.channels['Throttle'][i], raw.time_s[i]
            if value >= .50:
                last_power = time
            elif value <= .25 and last_power is not None:
                if time - last_power <= .5 + 1e-9:
                    relifts += 1
                last_power = None
    return {'brake_start': brake_start, 'brake_release': brake_release,
            'throttle_first': throttle_first, 'full_throttle': full_throttle,
            'min_speed': minimum, 'abs_time_share': abs_share,
            'abs_observed_share': observed_share, 'coast_time_s': coast, 'relifts': relifts}
