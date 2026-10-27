"""Compact pedal/steering observations; no files, SDK, providers or stores.

The thresholds describe recorded inputs, not quality, lines or preferences.
Native channel periods bound timing resolution even on LMU's uniform axis.
"""
from __future__ import annotations

from collections import defaultdict
from statistics import median
import math
import re

import numpy as np


def _axis(kind):
    counts = dict(event_count=0, sampling_hz=None, timing_uncertainty_s=None)
    return ({**counts, 'linked_count': 0, 'separated_count': 0, 'overlap_pct': None}
            if kind == 'brake_steer' else
            {**counts, 'progressive_count': 0, 'rapid_count': 0, 'rise_median_s': None})


def _empty(reason):
    return dict(schema_version=1, status='UNAVAILABLE', sample_count=0,
                brake_steer=_axis('brake_steer'), throttle=_axis('throttle'), reason_codes=[reason])


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _array(channels, name, count):
    try:
        raw = np.asarray(channels[name])
        if raw.dtype.kind not in 'biuf' or raw.ndim != 1 or len(raw) != count:
            return None
        return raw  # Convert only the lap slice when arithmetic needs floats.
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def _runs(active):
    indices = np.flatnonzero(active)
    if not len(indices):
        return []
    return zip(indices[np.r_[True, np.diff(indices) != 1]],
               indices[np.r_[np.diff(indices) != 1, True]])


def measure_inputs(telemetry, start, end, *, measurable, replay_status, game):
    """Measure complete input episodes inside one independently eligible lap.

    end is inclusive. Unknown replay remains an explicit partial observation;
    confirmed replay, pits and broken lap clocks never supply input patterns.
    """
    metadata = getattr(telemetry, 'metadata', {}) or {}
    if isinstance(metadata, dict) and metadata.get('imported') is True:
        return _empty('IMPORTED_REFERENCE')
    if not measurable:
        return _empty('LAP_NOT_MEASURABLE')
    if replay_status in {'CONFIRMED_REPLAY', 'MIXED', 'INVALID'}:
        return _empty('REPLAY_SAMPLES_EXCLUDED')
    channels = getattr(telemetry, 'channels', {})
    channels = channels if isinstance(channels, dict) else {}
    try:
        count = len(channels['SessionTime'])
    except (KeyError, TypeError):
        return _empty('SESSION_TIME_UNAVAILABLE')
    if not 0 <= start < end < count:
        return _empty('SOURCE_SEGMENT_UNAVAILABLE')
    rate = getattr(telemetry, 'tick_rate', None)
    if not _finite(rate) or not 10 <= rate <= 2000:
        return _empty('INPUT_SAMPLING_RATE_INSUFFICIENT')
    time = _array(channels, 'SessionTime', count)
    if time is None:
        return _empty('SESSION_TIME_UNAVAILABLE')
    time = time[start:end + 1].astype(float, copy=False)
    delta = np.diff(time)
    if (not np.all(np.isfinite(time)) or not np.all((delta > 0) & (delta <= 1.5 / rate))):
        return _empty('INPUT_TIME_DISCONTINUITY')
    sampling = metadata.get('history_input_sampling', {}) if isinstance(metadata, dict) else {}
    if game == 'lmu' and (not isinstance(sampling, dict) or not sampling.get('native_hz')):
        return _empty('LMU_NATIVE_INPUT_RATE_UNAVAILABLE')
    if game == 'lmu' and sampling.get('pit_state_available') is not True:
        return _empty('PIT_STATE_UNAVAILABLE')
    if game == 'lmu' and (not _finite(sampling.get('pit_state_start_s')) or time[0] < sampling['pit_state_start_s']):
        return _empty('PIT_STATE_UNAVAILABLE')
    reasons = set()
    if replay_status == 'UNKNOWN':
        reasons.add('REPLAY_STATE_UNAVAILABLE')
    if game == 'lmu':
        reasons.add('INTERPOLATED_NATIVE_INPUTS')
    speed = _array(channels, 'Speed', count)
    track = _array(channels, 'IsOnTrack', count)
    if speed is None or track is None:
        return _empty('INPUT_DRIVING_STATE_UNAVAILABLE')
    speed, track = speed[start:end + 1], track[start:end + 1]
    eligible = np.isfinite(speed) & (speed > 5.) & (speed <= 150.) & (track == 1.)
    pits = _array(channels, 'OnPitRoad', count)
    if pits is not None:
        eligible &= pits[start:end + 1] == 0.
    if not np.all(eligible):
        reasons.add('NON_DRIVING_INPUTS_EXCLUDED')
    result = dict(schema_version=1, status='UNAVAILABLE', sample_count=int(np.count_nonzero(eligible)),
                  brake_steer=_axis('brake_steer'), throttle=_axis('throttle'), reason_codes=[])

    def hz(names):
        rates = [rate]
        if game == 'lmu':
            raw = sampling.get('native_hz', {})
            units = sampling.get('native_units', {})
            for name in names:
                expected = {'km/h', 'kmh', 'kph'} if name == 'Speed' else {'%'}
                if str(units.get(name, '')).strip().casefold() not in expected:
                    reasons.add('INPUT_UNITS_UNCONFIRMED')
                    return None
                value = raw.get(name)
                if not _finite(value) or not 10 <= value <= 2000:
                    reasons.add('INPUT_SAMPLING_RATE_INSUFFICIENT')
                    return None
                rates.append(value)
        return float(min(rates))

    brake = _array(channels, 'Brake', count)
    steer = _array(channels, 'SteeringWheelAngle', count)
    throttle = _array(channels, 'Throttle', count)
    if brake is None: reasons.add('BRAKE_INPUT_UNAVAILABLE')
    if steer is None: reasons.add('STEERING_INPUT_UNAVAILABLE')
    if throttle is None: reasons.add('THROTTLE_INPUT_UNAVAILABLE')
    if brake is not None and steer is not None:
        axis = result['brake_steer']
        native_rate = hz(('Speed', 'Brake', 'SteeringWheelAngle'))
        if native_rate:
            axis.update(sampling_hz=native_rate, timing_uncertainty_s=round(2. / native_rate, 6))
            brake, steer = brake[start:end + 1], np.abs(steer[start:end + 1])
            threshold = .05  # radians of wheel angle, not a geographic corner
            if game == 'lmu':
                half_range = sampling.get('steering_half_range_rad')
                if not _finite(half_range) or half_range <= 0:
                    reasons.add('STEERING_SCALE_UNAVAILABLE')
                    native_rate = None
                else:
                    steer = steer / half_range
                    threshold = .02  # recorded fraction of lock; nominal range cancels
                    reasons.add('STEERING_NORMALIZED_LOCK')
            if native_rate:
                valid = eligible & np.isfinite(brake) & (brake >= 0) & (brake <= 1.)
                valid &= np.isfinite(steer) & (steer <= (1.05 if game == 'lmu' else 30.))
                if not np.all(valid | ~eligible): reasons.add('INVALID_BRAKE_STEER_INPUT')
                percentages = []
                for left, right in _runs(valid & (brake >= .1)):
                    if left == 0 or right == len(time)-1 or not valid[left-1] or not valid[right+1]:
                        continue
                    duration = time[right+1] - time[left]
                    if not .15 <= duration <= 30 or np.max(brake[left:right+1]) < .2:
                        continue
                    # Only describe braking followed by recorded steering.
                    limit = min(len(time), int(np.searchsorted(time, time[right+1] + 1.5)))
                    if not np.any(valid[left:limit] & (steer[left:limit] >= threshold)):
                        continue
                    weights = delta[left:right+1]
                    overlap = float(np.sum(weights[steer[left:right+1] >= threshold]))
                    share = float(overlap / duration * 100.)
                    percentages.append(share)
                    axis['linked_count'] += int(share >= 20. and overlap >= .15)
                    axis['separated_count'] += int(share <= 5.)
                axis.update(event_count=len(percentages),
                            overlap_pct=round(median(percentages), 3) if percentages else None)
    if throttle is not None:
        axis = result['throttle']
        native_rate = hz(('Speed', 'Throttle'))
        if native_rate:
            error = 2. / native_rate
            axis.update(sampling_hz=native_rate, timing_uncertainty_s=round(error, 6))
            throttle = throttle[start:end+1].astype(float, copy=False)
            valid = eligible & np.isfinite(throttle) & (throttle >= 0) & (throttle <= 1.)
            if not np.all(valid | ~eligible): reasons.add('INVALID_THROTTLE_INPUT')
            durations = []
            for left, right in _runs(valid & (throttle > .2)):
                if left == 0 or not valid[left-1]:
                    continue
                highs = np.flatnonzero(throttle[left:right+1] >= .8)
                if not len(highs):
                    continue
                high = left + int(highs[0])
                duration = float(time[high] - time[left-1])
                if not 0 < duration <= 8.:
                    continue
                durations.append(duration)
                rising = bool(np.all(np.diff(throttle[left-1:high+1]) >= -.1))
                axis['progressive_count'] += int(rising and duration - error >= .6)
                axis['rapid_count'] += int(rising and duration + error <= .35)
            axis.update(event_count=len(durations),
                        rise_median_s=round(median(durations), 4) if durations else None)
    any_events = any(result[key]['event_count'] for key in ('brake_steer', 'throttle'))
    if not any_events: reasons.add('NO_COMPLETE_INPUT_EPISODES')
    result.update(status=('PARTIAL' if reasons else 'OBSERVED') if any_events else 'UNAVAILABLE',
                  reason_codes=sorted(reasons))
    return result


def validate_inputs(value):
    """Allowlisted optional schema-v1 fields; old summaries have no inputs."""
    def fail():
        raise ValueError('주행 입력 요약의 저장 형식이 올바르지 않습니다.')
    if not isinstance(value, dict) or type(value.get('schema_version')) is not int or value['schema_version'] != 1:
        fail()
    if value.get('status') not in {'OBSERVED', 'PARTIAL', 'UNAVAILABLE'}:
        fail()
    count = value.get('sample_count')
    if type(count) is not int or not 0 <= count <= 100000000:
        fail()
    reasons = value.get('reason_codes')
    if (not isinstance(reasons, list) or len(reasons) > 40 or
            any(not isinstance(item, str) or not re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}', item) for item in reasons)):
        fail()
    result = dict(schema_version=1, status=value['status'], sample_count=count, reason_codes=sorted(set(reasons)))
    for name, counts, metric, maximum in (
        ('brake_steer', ('linked_count', 'separated_count'), 'overlap_pct', 100.),
        ('throttle', ('progressive_count', 'rapid_count'), 'rise_median_s', 8.)):
        axis = value.get(name)
        if not isinstance(axis, dict): fail()
        checked = {}
        for key in ('event_count', *counts):
            number = axis.get(key)
            if type(number) is not int or not 0 <= number <= 1000000: fail()
            checked[key] = number
        if sum(checked[key] for key in counts) > checked['event_count']: fail()
        for key, high in ((metric, maximum), ('sampling_hz', 2000.), ('timing_uncertainty_s', 1.)):
            number = axis.get(key)
            if number is not None and (not _finite(number) or not 0 <= number <= high): fail()
            checked[key] = number
        if checked['event_count'] and (checked[metric] is None or not checked['sampling_hz']
                                      or checked['timing_uncertainty_s'] is None): fail()
        if not checked['event_count'] and checked[metric] is not None: fail()
        result[name] = checked
    if value['status'] == 'UNAVAILABLE' and any(result[key]['event_count'] for key in ('brake_steer', 'throttle')):
        fail()
    return result


def _label(drive):
    labels = drive.get('labels', {})
    return ' · '.join(filter(None, (labels.get('car') or '차량 확인 필요',
        labels.get('track') or '코스 확인 필요', labels.get('layout'),
        {'PRACTICE':'연습', 'QUALIFY':'퀄리', 'RACE':'레이스'}.get(drive['session_type'], '세션 종류 미확인'))))


def build_style(drives, lap_selections):
    """Each combination and day has equal influence; named patterns need repeats."""
    result = dict(status='UNAVAILABLE', items=[], scope_count=0, weighting='EQUAL_COMBINATION_AND_DAY',
                  reason='측정 가능한 페달·조향 입력을 확인하면 조작 패턴을 보여드립니다. 성향과 빠르기·강점은 별도입니다.')
    groups, labels, unavailable = defaultdict(list), {}, set()
    for drive in drives:
        laps = lap_selections.get(drive['drive_id'], ([], set()))[0]
        key = tuple(drive[field] for field in ('game', 'car_key', 'track_key', 'layout_key', 'session_type'))
        labels[key] = _label(drive)
        for lap in laps:
            inputs = lap.get('inputs')
            if not inputs:
                unavailable.add('INPUT_SUMMARY_UNAVAILABLE')
                continue
            unavailable.update(inputs.get('reason_codes', []))
        groups[key].append((drive, laps))
    for axis_name, value_key, positive, negative, unit, neutral, names in (
        ('brake_steer', 'overlap_pct', 'linked_count', 'separated_count', '%', '제동·조향 겹침 관측', ('제동 연결형', '조작 분리형')),
        ('throttle', 'rise_median_s', 'progressive_count', 'rapid_count', 's', '스로틀 전개 관측', ('점진 가속형', '빠른 가속 전개형'))):
        summaries, totals, sessions, days, native_ids = [], [], set(), set(), set()
        rates, timing_errors = [], []
        all_qualified, scope_labels, evidence_days, native_support = True, [], [], []
        reasons = set(unavailable)
        for key, rows in groups.items():
            by_day, known_rows = defaultdict(list), []
            for drive, laps in rows:
                axes = [lap['inputs'][axis_name] for lap in laps if lap.get('inputs')
                        and lap['inputs'][axis_name]['event_count'] > 0]
                if not axes: continue
                rates.extend(axis['sampling_hz'] for axis in axes)
                timing_errors.extend(axis['timing_uncertainty_s'] for axis in axes)
                events = sum(axis['event_count'] for axis in axes)
                pro, con = (sum(axis[category] for axis in axes) for category in (positive, negative))
                value = median(axis[value_key] for axis in axes)
                day = (drive.get('recorded_at') or '')[:10]
                # Undated observations are useful, but each is not another day.
                by_day[day].append((value, pro/events, con/events))
                totals.append((events, pro, con))
                sessions.add(drive['drive_id'])
                if day: days.add(day)
                native = (drive['identity_status'] == 'NATIVE' and bool(day)
                          and all(lap.get('replay_status') == 'CONFIRMED_NON_REPLAY' for lap in laps if lap.get('inputs')))
                if native:
                    native_ids.add(drive['drive_id'])
                if native and events >= 5:
                    known_rows.append((day, pro/events, con/events))
                if day: evidence_days.append((day, pro, con))
            if not by_day: continue
            # First normalize observations within day, then days within scope.
            day_values = [(median(row[0] for row in values), median(row[1] for row in values),
                           median(row[2] for row in values)) for values in by_day.values()]
            summaries.append(tuple(sum(row[index] for row in day_values)/len(day_values) for index in range(3)))
            scope_labels.append(labels[key])
            qualified = len(known_rows) >= 3 and len({row[0] for row in known_rows}) >= 2
            all_qualified &= qualified and all(field not in {'', 'UNKNOWN'} for field in key[1:3])
            if qualified:
                by_native_day = defaultdict(list)
                for day, pro, con in known_rows:
                    by_native_day[day].append((pro, con))
                daily = [(median(row[0] for row in values), median(row[1] for row in values))
                         for values in by_native_day.values()]
                native_support.append(tuple(sum(row[index] for row in daily)/len(daily) for index in (0, 1)))
        if not summaries: continue
        value, pro_share, con_share = (sum(row[index] for row in summaries)/len(summaries) for index in range(3))
        direction = 0 if pro_share >= .7 else 1 if con_share >= .7 else None
        repeated = (all_qualified and direction is not None and len(native_support) == len(summaries)
                    and sum(row[direction] for row in native_support)/len(native_support) >= .7)
        if not repeated: reasons.add('PERSISTENT_PATTERN_EVIDENCE_INSUFFICIENT')
        if len(summaries) == 1: reasons.add('SINGLE_COMBINATION_SCOPE')
        events = sum(row[0] for row in totals)
        opposing = sum(row[2 if direction != 1 else 1] for row in totals)
        counter_days = [day for day, pro, con in evidence_days if (pro > con if direction == 1 else con > pro)]
        scope = ' / '.join(dict.fromkeys(scope_labels))
        if len(scope) > 220: scope = f'{len(summaries)}개 차량·코스 조합'
        result['items'].append(dict(id=axis_name, axis=axis_name,
            title=names[direction] if repeated else neutral, status='REPEATED' if repeated else 'OBSERVATION_ONLY',
            value=round(value, 3), unit=unit, event_count=events, observation_count=len(sessions),
            sampling_hz=min(rates), timing_uncertainty_s=max(timing_errors),
            session_count=len(native_ids), day_count=len(days), scope=scope,
            scope_count=len(summaries),
            reason=(('이 범위의 독립 세션과 서로 다른 날에 반복된 입력입니다. 빠르기나 선호·기량을 뜻하지 않습니다.' if repeated else
                    '확인한 입력 수치를 먼저 표시합니다. 성향 이름에는 조합마다 2일 이상·독립 세션 3개·세션당 입력 5회 이상의 반복 근거가 필요합니다.')
                    + (' 리플레이 여부가 미확인인 파일의 부분 관측을 포함합니다.' if 'REPLAY_STATE_UNAVAILABLE' in reasons else '')
                    + (' LMU 보간 자료는 원본 채널 주기를 기준으로 계산했습니다.' if 'INTERPOLATED_NATIVE_INPUTS' in reasons else '')),
            reason_codes=sorted(reasons),
            counter_evidence=dict(event_count=opposing, share_pct=round((con_share if direction != 1 else pro_share)*100., 2),
                                  dates=sorted(set(counter_days))[-3:]),
            method=('브레이크 10% 이상 구간의 조향 겹침 비율 중앙값을 조합·날짜에 같은 비중으로 요약합니다. 코너 통과 위치는 판정하지 않습니다.' if axis_name == 'brake_steer' else
                    '스로틀 20% 이하에서 80% 이상까지 전개한 시간의 중앙값을 조합·날짜에 같은 비중으로 요약합니다. 실제 가속 성과는 별도입니다.')))
        result['scope_count'] = max(result['scope_count'], len(summaries))
    if result['items']:
        result['status'] = 'OBSERVED' if all(item['status'] == 'REPEATED' for item in result['items']) else 'PARTIAL'
    else:
        result['reason_codes'] = sorted(unavailable or {'NO_COMPLETE_INPUT_EPISODES'})
    return result
