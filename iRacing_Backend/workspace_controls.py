"""Native ABS observations before chart downsampling; no storage or runtime imports."""
import numpy as np

SIGNALS = {'abs': ('BrakeABSactive', 'BrakeABScutPct')}


def unavailable(reason='MISSING_CHANNEL'):
    return {'available': False, 'reason': reason, 'source': None, 'events': [], 'coverage': []}


def control_signal(distance, times, values, track_length, source, allowed=None):
    """Keep every native active interval, including pulses lost by chart downsampling.

    A state is held only to the next observed sample. Gaps, reversed distance,
    unknown flags and unclean portions never become inactive observations.
    """
    if values is None:
        return unavailable()
    distance, times, values = (np.asarray(a, dtype=float) for a in (distance, times, values))
    valid = np.isfinite(distance) & np.isfinite(times) & np.isfinite(values) & (distance >= 0) & (distance <= 1)
    valid &= values >= 0 if source == 'BrakeABScutPct' else np.isin(values, [0, 1])
    if allowed is not None:
        valid &= np.asarray(allowed, dtype=bool)
    if not np.any(valid):
        return unavailable('NO_OBSERVATIONS')
    events, coverage = [], []
    for i in np.flatnonzero(valid):
        end = i
        if i + 1 < len(values) and valid[i + 1]:
            dt, dp = times[i + 1] - times[i], distance[i + 1] - distance[i]
            if 0 < dt <= .5 and dp >= 0 and (not track_length or dp * track_length <= 25):
                end = i + 1
        a, b = round(float(distance[i]), 7), round(float(distance[end]), 7)
        if coverage and coverage[-1][1] == a:
            coverage[-1][1] = b
        else:
            coverage.append([a, b])
        if values[i] > 0:
            events.append([a, b, round(float(times[end] - times[i]), 6)])
    return {'available': True, 'reason': None, 'source': source, 'events': events, 'coverage': coverage}


def _controls(distance, times, channels, track_length, allowed=None):
    return {kind: control_signal(distance, times, next((channels[key] for key in keys if key in channels), None),
                                track_length, next((key for key in keys if key in channels), None), allowed)
            for kind, keys in SIGNALS.items()}


def lap_controls(api, telemetry, lap, grid=None):
    if lap is None:
        return {kind: unavailable('NO_REFERENCE') for kind in SIGNALS}
    cache = telemetry.__dict__.setdefault('_native_control_observations', {})
    if lap.lap_number in cache:
        return cache[lap.lap_number]
    channels = telemetry.channels
    extra = {key for keys in SIGNALS.values() for key in keys if key in telemetry.variables and key not in channels}
    if extra:
        reader = api.IBTReader(telemetry.path)
        channels = {**channels, **reader._read_channels(variables=telemetry.variables, data_offset=telemetry.data_offset,
                    buffer_len=telemetry.buffer_length, record_count=telemetry.record_count, requested=extra)}
    section = slice(lap.start_idx, lap.end_idx + 1)
    distance = np.asarray(channels['LapDistPct'][section], dtype=float)
    times = channels.get('SessionTime')
    if times is None:
        return {kind: unavailable('NO_OBSERVATIONS') for kind in SIGNALS}
    allowed = None
    if not lap.valid and grid is not None:
        axis = grid['distance_pct']
        indices = np.clip(np.searchsorted(axis, distance), 0, len(axis) - 1)
        allowed = (np.asarray(grid.get('_coverage', np.ones(len(axis), dtype=bool))) &
                   np.asarray(grid.get('_analysis_coverage', np.ones(len(axis), dtype=bool))))[indices]
    result = _controls(distance, times[section], {key: values[section] for key, values in channels.items()
                       if any(key in keys for keys in SIGNALS.values())}, telemetry.metadata.get('track_length_m'), allowed)
    cache[lap.lap_number] = result
    return result


def external_controls(api, reference, track_length):
    return _controls(reference.distance_pct, reference.sample_time_s, reference.channels, track_length)


def comparison_controls(api, telemetry, target, reference, grid=None, external=None, aggregate=False):
    own = lap_controls(api, telemetry, target, grid)
    base = ({kind: unavailable('AGGREGATE_REFERENCE') for kind in SIGNALS} if aggregate else
            external_controls(api, external, telemetry.metadata.get('track_length_m')) if external is not None else
            lap_controls(api, telemetry, reference))
    return {kind: {'target': own[kind], 'reference': base[kind]} for kind in SIGNALS}
