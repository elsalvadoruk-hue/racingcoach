"""Measured report inputs from already-loaded telemetry; no files or stores open."""
from __future__ import annotations

import hashlib
import json
import math
import re

import numpy as np

from driver_history_incidents import incident_metrics, merge_incidents, unavailable_incidents


CALCULATION_VERSION = 'observed-metrics-v4'


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _number(value):
    if isinstance(value, (bool, np.bool_)):
        return None
    try:
        value = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def _integer(value):
    number = _number(value)
    return int(number) if number is not None and number >= 0 and number.is_integer() else None


def _rate(value):
    return float(value) if type(value) in (int, float) and math.isfinite(value) and value > 0 else None


def _array(channels, name, count=None):
    try:
        values = np.asarray(channels[name], dtype=float)
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    return values if values.ndim == 1 and (count is None or len(values) == count) else None


def display_labels(metadata):
    """Only model/course display fields; never driver, setup, path or filename."""
    metadata = _mapping(metadata)

    def clean(value):
        if not isinstance(value, str) or '/' in value or '\\' in value or re.search(r'\.(?:ibt|duckdb)\b', value, re.I):
            return ''
        value = ' '.join(re.sub(r'[\x00-\x1f\x7f]', ' ', value).split())[:120]
        return '' if value.casefold() in {'unknown', 'unknown car', 'unknown track'} else value

    return dict(car=clean(metadata.get('car_name')), track=clean(metadata.get('track_name')),
                layout=clean(metadata.get('track_config') or metadata.get('track_layout')))


def unavailable_metrics(reason='METRICS_UNAVAILABLE'):
    return dict(schema_version=1, calculation_version=CALCULATION_VERSION,
                driving=dict(status='UNAVAILABLE', time_s=None, distance_m=None,
                intervals=[], reason_codes=[reason], replay_status='UNKNOWN'), laps=[],
                lap_quality=dict(status='UNAVAILABLE',authority='ANALYSIS_POLICY',official_validity='UNKNOWN',
                                 completed_laps=[],reason_codes=[reason]),
                incidents=unavailable_incidents(reason))


def _replay_evidence(channels, count):
    """Absence/NaN is unknown; malformed values never become absent evidence."""
    if 'IsReplayPlaying' not in channels:
        return None, 'UNKNOWN'
    try:
        values = np.asarray(channels['IsReplayPlaying'])
        if values.ndim != 1 or len(values) != count or values.dtype.kind not in 'biuf':
            return None, 'INVALID'
        return values.astype(float, copy=False), None
    except (TypeError, ValueError, OverflowError):
        return None, 'INVALID'


def _replay_window(evidence, left, right):
    values, unavailable = evidence
    if unavailable:
        return unavailable, unavailable == 'UNKNOWN', {
            'REPLAY_STATE_UNAVAILABLE' if unavailable == 'UNKNOWN' else 'REPLAY_STATE_INVALID'}
    values = values[left:right]
    non_replay, replay, unknown = values == 0, values == 1, np.isnan(values)
    invalid = ~(non_replay | replay | unknown)
    reasons = set()
    if np.any(unknown):
        reasons.add('REPLAY_STATE_UNAVAILABLE')
    if np.any(replay):
        reasons.add('REPLAY_SAMPLES_EXCLUDED')
    if np.any(invalid):
        reasons.add('REPLAY_STATE_INVALID')
        status = 'INVALID'
    elif np.any(replay):
        status = 'MIXED' if np.any(non_replay | unknown) else 'CONFIRMED_REPLAY'
    else:
        status = 'UNKNOWN' if np.any(unknown) else 'CONFIRMED_NON_REPLAY'
    return status, non_replay | unknown, reasons


def _combined_replay_status(statuses):
    statuses = set(statuses)
    if 'INVALID' in statuses:
        return 'INVALID'
    if len(statuses) == 1:
        return next(iter(statuses))
    return 'MIXED' if statuses & {'MIXED', 'CONFIRMED_REPLAY'} else 'UNKNOWN'


def _driving(telemetry, times, left, right, offset, game, replay_evidence):
    if game == 'lmu':
        return unavailable_metrics('LMU_NATIVE_DRIVING_EVIDENCE_UNAVAILABLE')['driving']
    count = len(times)
    channels = _mapping(getattr(telemetry, 'channels', None))
    rate = _rate(getattr(telemetry, 'tick_rate', None))
    required = {'Speed': 'SPEED_UNAVAILABLE', 'IsOnTrack': 'ON_TRACK_STATE_UNAVAILABLE',
                'SessionNum': 'SESSION_STATE_UNAVAILABLE'}
    arrays = {key: _array(channels, key, count) for key in required}
    reasons = {reason for key, reason in required.items() if arrays[key] is None}
    if rate is None or rate <= 0:
        reasons.add('SAMPLING_RATE_UNAVAILABLE')
    if right - left < 2:
        reasons.add('INSUFFICIENT_CONTIGUOUS_SAMPLES')
    replay_status, replay_eligible, replay_reasons = _replay_window(replay_evidence, left, right)
    if reasons:
        output = unavailable_metrics()['driving']
        output.update(reason_codes=sorted(reasons | replay_reasons), replay_status=replay_status)
        return output
    reasons.update(replay_reasons)
    window = slice(left, right)
    time = times[window]
    speed, on_track, nums = (arrays[key][window] for key in required)
    finite_time = np.isfinite(time) & (time >= 0)
    known_track = np.isfinite(on_track) & np.isin(on_track, [0., 1.])
    known_num = np.isfinite(nums) & (nums >= 0) & (nums == np.floor(nums))
    known_speed = np.isfinite(speed) & (speed >= 0) & (speed <= 150.)
    known = finite_time & known_track & known_num & known_speed
    eligible = known & replay_eligible & (on_track == 1) & (speed > .5)
    delta = np.diff(time)
    continuous = np.isfinite(delta) & (delta > 0) & (delta <= 1.5 / rate)
    same_session = known_num[:-1] & known_num[1:] & (nums[:-1] == nums[1:])
    edges = continuous & same_session & eligible[:-1] & eligible[1:]
    if not np.all(finite_time):
        reasons.add('INVALID_SESSION_TIME')
    if np.any(np.isfinite(delta) & (delta > 1.5 / rate)):
        reasons.add('SAMPLE_GAP')
    if np.any(np.isfinite(delta) & (delta <= 0)):
        reasons.add('SESSION_CLOCK_DISCONTINUITY')
    if not np.all(known_track):
        reasons.add('ON_TRACK_STATE_UNAVAILABLE')
    if not np.all(known_num):
        reasons.add('SESSION_STATE_UNAVAILABLE')
    if np.any(known_num[:-1] & known_num[1:] & ~same_session):
        reasons.add('SESSION_CHANGE')
    if not np.all(known_speed):
        reasons.add('SPEED_UNAVAILABLE')
    if np.any(known & ~eligible):
        reasons.add('NON_DRIVING_SAMPLES_EXCLUDED')
    indices = np.flatnonzero(edges)
    if not len(indices):
        reasons.add('NO_CONFIRMED_DRIVING_INTERVALS')
        output = unavailable_metrics()['driving']
        output.update(reason_codes=sorted(reasons), replay_status=replay_status)
        return output
    starts = indices[np.r_[True, np.diff(indices) != 1]]
    ends = indices[np.r_[np.diff(indices) != 1, True]] + 1
    intervals = [[round(float(time[start]) + offset, 6), round(float(time[end]) + offset, 6)]
                 for start, end in zip(starts, ends)]
    return dict(status='PARTIAL' if reasons else 'OBSERVED',
                time_s=float(np.sum(delta[edges])),
                distance_m=float(np.sum((speed[:-1][edges] + speed[1:][edges]) * .5 * delta[edges])),
                intervals=intervals, reason_codes=sorted(reasons), replay_status=replay_status)


def _setup_hash(telemetry):
    setup = _mapping(getattr(telemetry, 'setup', None))
    if not setup:
        return None
    try:
        serialized = json.dumps(setup, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, OverflowError):
        return None
    return hashlib.sha256(serialized.encode('utf-8')).hexdigest()


def _conditions(arrays, start, end, setup_hash, game):
    conditions = {}
    fuel = arrays['FuelLevel']
    if fuel is not None and math.isfinite(fuel[start]) and 0 <= fuel[start] <= 1000:
        conditions['fuel_level_l'] = float(fuel[start])
    for name, key in [('TrackTempCrew', 'track_temp_c'), ('AirTemp', 'air_temp_c')]:
        values = arrays[name]
        if values is not None:
            window = values[start:end + 1]
            if len(window) and np.all(np.isfinite(window) & (window >= -100) & (window <= 200)):
                conditions[key] = float(np.median(window))
    if game == 'iracing':
        for name, key, minimum, maximum in [('TrackWetness', 'track_wetness', 1, 7),
                                             ('PlayerTireCompound', 'tire_compound', 0, 100)]:
            values = arrays[name]
            if values is not None:
                window = values[start:end + 1]
                if (len(window) and np.all(np.isfinite(window) & (window >= minimum) & (window <= maximum)
                                           & (window == np.floor(window))) and np.all(window == window[0])):
                    conditions[key] = float(window[0])
    if setup_hash:
        conditions['setup_hash'] = setup_hash
    return conditions


def build_observation_metrics(telemetry, observation, *, start_idx=0, end_idx=None, offset=0.):
    """Extract within one source segment; coordinates match coverage's offset.

    The source's existing analysis verdict is never promoted to official lap
    validity. Missing conditions remain absent; different games stay separate.
    """
    channels = _mapping(getattr(telemetry, 'channels', None))
    times = _array(channels, 'SessionTime')
    if times is None or not len(times):
        return unavailable_metrics('SESSION_TIME_UNAVAILABLE')
    count = len(times)
    left, right = _integer(start_idx), _integer(count if end_idx is None else end_idx)
    offset = _number(offset)
    if left is None or right is None or not 0 <= left < right <= count or offset is None:
        return unavailable_metrics('SOURCE_SEGMENT_UNAVAILABLE')
    game = observation.get('game')
    replay_evidence = _replay_evidence(channels, count)
    result = dict(schema_version=1, calculation_version=CALCULATION_VERSION,
                  driving=_driving(telemetry, times, left, right, offset, game, replay_evidence), laps=[],
                  lap_quality=dict(status='UNAVAILABLE',authority='ANALYSIS_POLICY',official_validity='UNKNOWN',
                                   completed_laps=[],reason_codes=['NO_COMPLETED_LAP_OBSERVATIONS']))
    coverage = _mapping(observation.get('coverage'))
    rate = _rate(getattr(telemetry, 'tick_rate', None))
    result['incidents'] = incident_metrics(channels,times,left,right,offset,rate,game,
        lambda begin,end:_replay_window(replay_evidence,begin,end))
    blocked = {'SAMPLE_GAP', 'SESSION_CLOCK_RESET', 'INVALID_SESSION_TIME', 'DUPLICATE_SESSION_TIME',
               'SAMPLING_RATE_UNAVAILABLE', 'LMU_NATIVE_CLOCK_UNAVAILABLE', 'INSUFFICIENT_CONTIGUOUS_SAMPLES'}
    lmu_blocked = game == 'lmu' and (not coverage.get('intervals') or coverage.get('status') == 'UNAVAILABLE'
                                    or bool(blocked & set(coverage.get('reasons', []))))
    setup_hash = _setup_hash(telemetry)
    nums = _array(channels, 'SessionNum', count)
    laps = getattr(telemetry, 'laps', ())
    if not isinstance(laps, (list, tuple)) or not laps:
        return result
    # Native float32/int channels otherwise get copied for every lap. Validate
    # and convert each relevant array once, after driving's temporary arrays die.
    condition_arrays = {key: _array(channels, key, count) for key in
                        ('FuelLevel', 'TrackTempCrew', 'AirTemp', 'TrackWetness', 'PlayerTireCompound')}
    for lap in laps:
        start, end = (_integer(getattr(lap, key, None)) for key in ('start_idx', 'end_idx'))
        number = _integer(getattr(lap, 'lap_number', None))
        if start is None or end is None or number is None or not left <= start < end < right:
            continue
        first, last = float(times[start]), float(times[end])
        if not math.isfinite(first) or not math.isfinite(last) or first < 0 or last <= first:
            continue
        values = times[start:end + 1]
        differences = np.diff(values)
        continuous = bool(rate is not None and rate > 0 and np.all(np.isfinite(values))
                          and np.all((differences > 0) & (differences <= 1.5 / rate)))
        if getattr(lap,'complete',False) is True:
            quality_reasons=set()
            quality_replay,quality_eligible,quality_replay_reasons = _replay_window(replay_evidence,start,end+1)
            quality_reasons.update(quality_replay_reasons)
            if not continuous:
                quality_reasons.add('LAP_TIME_DISCONTINUITY' if rate else 'SAMPLING_RATE_UNAVAILABLE')
            if lmu_blocked:
                quality_reasons.add('LMU_NATIVE_COVERAGE_UNAVAILABLE')
            if game=='iracing' and (nums is None or not np.all(np.isfinite(nums[start:end+1]))
                    or not np.all(nums[start:end+1]>=0) or not np.all(nums[start:end+1]==np.floor(nums[start:end+1]))
                    or not np.all(nums[start:end+1]==nums[start])):
                quality_reasons.add('SESSION_STATE_UNAVAILABLE')
            verdict=getattr(lap,'status',None)
            validity=verdict if verdict in {'VALID','INVALID'} else 'UNKNOWN'
            if not continuous or lmu_blocked or 'SESSION_STATE_UNAVAILABLE' in quality_reasons or not np.all(quality_eligible):
                validity='UNKNOWN'
            result['lap_quality']['completed_laps'].append(dict(lap_number=number,
                start_s=round(first+offset,6),end_s=round(last+offset,6),analysis_validity=validity,
                replay_status=quality_replay,reason_codes=sorted(quality_reasons)))
        lap_time = _number(getattr(lap, 'lap_time_s', None))
        if lap_time is None or not 0 < lap_time <= 86400:
            continue  # Untimed invalid completed laps remain in lap_quality's denominator.
        blockers = set()
        if not continuous:
            blockers.add('LAP_TIME_DISCONTINUITY' if rate else 'SAMPLING_RATE_UNAVAILABLE')
        if getattr(lap, 'complete', False) is not True:
            blockers.add('INCOMPLETE_LAP')
        if getattr(lap, 'status', None) != 'VALID':
            blockers.add('ANALYSIS_LAP_INVALID')
        pit_samples = _integer(getattr(lap, 'pit_samples', None))
        if pit_samples != 0:
            blockers.add('PIT_STATE_UNAVAILABLE' if pit_samples is None else 'PIT_SAMPLES_PRESENT')
        if rate and abs(lap_time - (last - first)) > max(3. / rate, .05):
            blockers.add('LAP_DURATION_MISMATCH')
        if lmu_blocked:
            blockers.add('LMU_NATIVE_COVERAGE_UNAVAILABLE')
        replay_status, replay_reasons = 'UNKNOWN', set()
        if game == 'iracing':
            if nums is None or not np.all(np.isfinite(nums[start:end + 1]) & (nums[start:end + 1] >= 0)
                                         & (nums[start:end + 1] == np.floor(nums[start:end + 1]))):
                blockers.add('SESSION_STATE_UNAVAILABLE')
            elif not np.all(nums[start:end + 1] == nums[start]):
                blockers.add('SESSION_CHANGE')
            replay_status, replay_eligible, replay_reasons = _replay_window(replay_evidence, start, end + 1)
            if not np.all(replay_eligible):
                blockers.update(replay_reasons - {'REPLAY_STATE_UNAVAILABLE'})
        measurable = not blockers
        from driver_style import measure_inputs
        inputs = measure_inputs(telemetry, start, end, measurable=measurable,
                                replay_status=replay_status, game=game)
        result['laps'].append(dict(lap_number=number, start_s=round(first + offset, 6),
            end_s=round(last + offset, 6), lap_time_s=lap_time, measurable=measurable,
            conditions=_conditions(condition_arrays, start, end, setup_hash, game),
            replay_status=replay_status, reason_codes=sorted(blockers | replay_reasons),
            official_validity='UNKNOWN', inputs=inputs))
    quality=result['lap_quality']
    if quality['completed_laps']:
        reasons=set().union(*(row['reason_codes'] for row in quality['completed_laps']))
        if any(row['analysis_validity']=='UNKNOWN' for row in quality['completed_laps']):
            reasons.add('ANALYSIS_LAP_VERDICT_UNAVAILABLE')
        quality.update(status='PARTIAL' if reasons else 'OBSERVED',reason_codes=sorted(reasons))
    return result


def merge_observation_metrics(before, after):
    """Combine disjoint segments of one native observation, without a new edge."""
    driving = [item['driving'] for item in (before, after)]
    measured = [item for item in driving if item['time_s'] is not None and item['distance_m'] is not None]
    reasons = set().union(*(set(item['reason_codes']) for item in driving))
    replay_status = _combined_replay_status(item.get('replay_status', 'UNKNOWN') for item in driving)
    if replay_status == 'UNKNOWN':
        reasons.add('REPLAY_STATE_UNAVAILABLE')
    status = ('UNAVAILABLE' if not measured else 'OBSERVED'
              if all(item['status'] == 'OBSERVED' for item in driving) and not reasons else 'PARTIAL')
    output = dict(schema_version=1, calculation_version=CALCULATION_VERSION, driving=dict(status=status,
        time_s=sum(item['time_s'] for item in measured) if measured else None,
        distance_m=sum(item['distance_m'] for item in measured) if measured else None,
        intervals=[list(window) for item in driving for window in item['intervals']],
        reason_codes=sorted(reasons), replay_status=replay_status),
        laps=[*before['laps'], *after['laps']])
    empty=unavailable_metrics()
    qualities=[item.get('lap_quality',empty['lap_quality']) for item in (before,after)]
    quality_laps=[row for item in qualities for row in item['completed_laps']]
    quality_reasons=set().union(*(item['reason_codes'] for item in qualities))
    output['lap_quality']=dict(status='UNAVAILABLE' if not quality_laps else 'PARTIAL' if quality_reasons else 'OBSERVED',
        authority='ANALYSIS_POLICY',official_validity='UNKNOWN',completed_laps=quality_laps,reason_codes=sorted(quality_reasons))
    output['incidents']=merge_incidents(*(item.get('incidents',empty['incidents']) for item in (before,after)))
    return output
