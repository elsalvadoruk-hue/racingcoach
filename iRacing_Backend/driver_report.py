"""Deterministic local reports of observed facts, with explicit missing evidence."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from bisect import bisect_left, insort
import hashlib
import json
import math
import sqlite3
from statistics import median

from driver_history import DriverHistoryStore
from driver_history_metrics import CALCULATION_VERSION as METRICS_VERSION
from driver_style import build_style
from driver_report_quality import build_quality


CALCULATION_VERSION = 'observed-report-v6'
_REQUIRED_CONDITIONS = ('fuel_level_l', 'track_temp_c', 'air_temp_c', 'track_wetness', 'tire_compound', 'setup_hash')
_CONDITION_LABELS = {'fuel_level_l': '연료량', 'track_temp_c': '노면 온도', 'air_temp_c': '기온',
                     'track_wetness': '노면 젖음', 'tire_compound': '타이어 종류', 'setup_hash': '셋업'}
_REASONS = {
    'NATIVE_IDENTITY_UNAVAILABLE': '독립 세션 수는 미확정입니다. 확인 가능한 파일별 측정과 관측 변화는 표시하며 실력 성장으로 확정하지 않습니다.',
    'REPLAY_STATE_UNAVAILABLE': '리플레이 여부를 확인할 정보가 없어 측정값을 부분 관측으로 표시합니다.',
    'REPLAY_STATE_INVALID': '리플레이 상태값이 올바르지 않아 해당 구간을 측정에서 제외합니다.',
    'REPLAY_SAMPLES_EXCLUDED': '리플레이로 확인된 구간은 측정에서 제외했습니다.',
    'REPLAY_EXCLUDED': '리플레이로 확인된 구간은 측정에서 제외했습니다.',
    'SPEED_UNAVAILABLE': '속도 근거가 없는 구간은 운전 시간과 거리에 포함하지 않습니다.',
    'ON_TRACK_STATE_UNAVAILABLE': '차량의 주행 상태를 확인할 수 없는 구간은 운전량에 포함하지 않습니다.',
    'SESSION_STATE_UNAVAILABLE': '실제 세션 번호를 확인할 수 없는 구간은 운전량에 포함하지 않습니다.',
    'SESSION_TYPE_UNAVAILABLE': '실제 세션 종류가 미확인이라 같은 종류의 기간 비교를 보류합니다.',
    'RECORDING_UTC_UNAVAILABLE': '기록 날짜를 확인할 수 없어 선택 기간과 성장 추세에 배치하지 않습니다.',
    'SAMPLE_GAP': '기록 공백을 채우지 않고 연속해서 관측한 구간만 사용합니다.',
    'SESSION_CLOCK_RESET': '시계가 되돌아간 기록은 서로 다른 원본과 같은 세션으로 합치지 않습니다.',
    'DUPLICATE_SESSION_TIME': '시각이 중복된 구간은 연속 주행으로 연결하지 않습니다.',
    'INVALID_SESSION_TIME': '시각이 올바르지 않은 구간은 측정에서 제외합니다.',
    'SESSION_CLOCK_DISCONTINUITY': '시간이 멈추거나 되돌아간 경계를 연속 주행으로 연결하지 않습니다.',
    'SESSION_TIME_UNAVAILABLE': '실제 기록 시각 채널을 확인할 수 없어 측정값을 계산하지 않습니다.',
    'SOURCE_SEGMENT_UNAVAILABLE': '원본 안의 측정 구간과 시각 기준을 연결할 수 없습니다.',
    'SOURCE_TIME_MAPPING_UNAVAILABLE': '원본 시각을 관측 구간에 연결할 수 없어 측정을 보류합니다.',
    'METRICS_EXTRACTION_FAILED': '관측의 측정 근거를 읽지 못했습니다. 기존 주행 분석은 유지됩니다.',
    'NON_DRIVING_SAMPLES_EXCLUDED': '정차·비주행·확인된 리플레이 표본은 운전량에서 제외했습니다.',
    'SAMPLING_RATE_UNAVAILABLE': '기록 주기를 확인할 수 없어 연속 측정을 확정하지 않습니다.',
    'NO_CONFIRMED_DRIVING_INTERVALS': '운전량을 계산할 연속 주행 구간이 없습니다.',
    'INSUFFICIENT_CONTIGUOUS_SAMPLES': '연속 측정 표본이 부족합니다.',
    'METRICS_UNAVAILABLE': '저장된 관측에 운전량 측정 근거가 없습니다.',
    'LMU_NATIVE_DRIVING_EVIDENCE_UNAVAILABLE': 'LMU 기록에서 누적 운전량의 근거를 아직 확인하지 못했습니다.',
    'LMU_NATIVE_CLOCK_UNAVAILABLE': 'LMU 원본 시계의 연속 측정 근거를 확인할 수 없습니다.',
    'NO_MEASURABLE_LAPS': '완주·연속 시각·기록 시간 조건을 충족한 측정 랩이 없습니다.',
    'CONFLICTING_LAP_TIME': '같은 랩 번호의 기록 시간이 충돌하여 해당 랩을 제외했습니다.',
    'CONFLICTING_LAP_WINDOW': '같은 랩 번호의 시각 범위가 충돌하여 해당 랩을 제외했습니다.',
    'OVERLAPPING_LAP_WINDOWS': '서로 다른 랩의 시각 범위가 겹쳐 해당 랩을 제외했습니다.',
    'MIXED_TIME_BASES': '서로 다른 시계 기준은 합산하지 않고 한 기준의 관측을 사용합니다.',
    'INCOMPLETE_LAP': '완주하지 않은 랩은 측정 랩에서 제외합니다.',
    'ANALYSIS_LAP_INVALID': '기존 랩 분석의 측정 조건을 통과하지 않은 랩은 제외합니다.',
    'PIT_STATE_UNAVAILABLE': '피트 상태가 미확인인 랩은 측정 랩에서 제외합니다.',
    'PIT_SAMPLES_PRESENT': '피트 구간이 포함된 랩은 측정 랩에서 제외합니다.',
    'LAP_DURATION_MISMATCH': '기록된 랩타임과 연속 시각 범위가 일치하지 않는 랩은 제외합니다.',
    'LAP_TIME_DISCONTINUITY': '시간 공백이나 중복이 있는 랩은 측정 랩에서 제외합니다.',
    'SESSION_CHANGE': '실제 세션이 바뀌는 경계를 주행 구간이나 한 랩으로 연결하지 않습니다.',
    'LMU_NATIVE_COVERAGE_UNAVAILABLE': 'LMU 원본 시계의 연속 측정 범위를 확인할 수 없는 랩은 제외합니다.',
    'OUTDATED_METRICS': '이전 계산 또는 측정 근거 추출 실패로 다시 확인해야 할 원본이 있습니다.',
    'OVERLAPPING_SOURCE_LAPS': '같은 차량·코스의 UTC 측정 랩이 다른 원본과 겹쳐 중복 집계하지 않았습니다.',
}


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _date(value):
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return stamp.astimezone(timezone.utc) if stamp.tzinfo is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def _union(windows):
    merged = []
    for start, end in sorted(windows):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    return merged


def _duration(windows):
    return sum(end - start for start, end in _union(windows))


def _overlaps(one, two):
    left, right = 0, 0
    while left < len(one) and right < len(two):
        a, b = one[left], two[right]
        if min(a[1], b[1]) > max(a[0], b[0]) + .000001:
            return True
        if a[1] <= b[1]:
            left += 1
        else:
            right += 1
    return False


def _metric(value=None, reason='', status='UNAVAILABLE'):
    return {'value': round(value, 3) if value is not None else None, 'status': status, 'reason': reason}


def _replay_status(observation, lap=None):
    metrics = observation.get('metrics', {})
    driving = metrics.get('driving', {})
    explicit = (lap or {}).get('replay_status', driving.get('replay_status')) if lap is not None else driving.get('replay_status')
    if explicit in {'CONFIRMED_NON_REPLAY', 'UNKNOWN', 'MIXED', 'CONFIRMED_REPLAY', 'INVALID'}:
        return explicit
    reasons = set(observation.get('coverage', {}).get('reasons', []))
    reasons.update(observation.get('metrics', {}).get('driving', {}).get('reason_codes', []))
    reasons.update((lap or {}).get('reason_codes', []))
    if 'REPLAY_STATE_UNAVAILABLE' in reasons:
        return 'UNKNOWN'
    # Only an actual v1 iRacing measurement proves that the former mandatory
    # replay gate passed. An absent metrics object or unmeasured row proves none.
    if observation.get('game') != 'iracing' or metrics.get('calculation_version') is not None:
        return 'UNKNOWN'
    def measured_lap(item):
        return item.get('measurable') is True and _finite(item.get('lap_time_s')) and item['lap_time_s'] > 0
    if lap is not None:
        measured = measured_lap(lap)
    else:
        measured = (driving.get('status') in {'OBSERVED', 'PARTIAL'}
                    and _finite(driving.get('time_s')) and driving['time_s'] > 0
                    and bool(driving.get('intervals')))
        measured |= any(measured_lap(item) for item in metrics.get('laps', []))
    return 'CONFIRMED_NON_REPLAY' if measured else 'UNKNOWN'


def _combined_replay(statuses):
    values = set(statuses)
    if 'INVALID' in values:
        return 'INVALID'
    if len(values) == 1:
        return next(iter(values))
    return 'MIXED' if values & {'MIXED', 'CONFIRMED_REPLAY'} else 'UNKNOWN'


def _drive_replay(drive):
    return _combined_replay(_replay_status(observation) for observation in drive['observations'])


def _unknown_replay(observation):
    return (_replay_status(observation) == 'UNKNOWN'
            or 'REPLAY_STATE_UNAVAILABLE' in observation.get('metrics', {}).get('driving', {}).get('reason_codes', [])
            or 'REPLAY_STATE_UNAVAILABLE' in observation.get('coverage', {}).get('reasons', [])
            or any(_replay_status(observation, lap) == 'UNKNOWN'
                   for lap in observation.get('metrics', {}).get('laps', [])
                   if lap.get('measurable') is True or 'replay_status' in lap
                   or 'REPLAY_STATE_UNAVAILABLE' in lap.get('reason_codes', [])))


def _distance_lower_bound(sources):
    """Use complete nonoverlapping measurements; never apportion a scalar."""
    selected, total, measured = [], 0., False
    for _, driving in sorted(sources, key=lambda item: (
            -item[1]['distance_m'] if _finite(item[1].get('distance_m')) else math.inf, item[0])):
        distance = driving.get('distance_m')
        if not _finite(distance):
            continue
        candidates = _union(driving['intervals'])
        overlap = False
        for window in candidates:
            index = bisect_left(selected, window)
            if _overlaps([window], selected[max(0, index - 1):index + 1]):
                overlap = True
                break
        if overlap:
            continue
        for window in candidates:
            insort(selected, window)
        total += distance
        measured = True
    return total, measured


def _driving_groups(drives):
    """Keep shared UTC clocks, proven sessions and unshared source clocks distinct."""
    contexts, replay_states = {}, []
    partial = unknown_included = False
    for drive in drives:
        by_basis = {}
        for observation in drive['observations']:
            driving = observation.get('metrics', {}).get('driving', {})
            replay = _replay_status(observation)
            if (driving.get('status') not in {'OBSERVED', 'PARTIAL'}
                    or not _finite(driving.get('time_s')) or not driving.get('intervals')
                    or replay in {'CONFIRMED_REPLAY', 'INVALID'}):
                partial = True
                continue
            basis = observation['coverage']['time_basis']
            if basis not in {'UTC', 'NATIVE_SESSION_TIME', 'SOURCE_RELATIVE'}:
                partial = True
                continue
            by_basis.setdefault(basis, []).append((observation['source_id'], driving))
            replay_states.append(replay)
            unknown_included |= _unknown_replay(observation)
            partial |= driving['status'] != 'OBSERVED' or replay != 'CONFIRMED_NON_REPLAY'
        if not by_basis:
            continue
        # One drive can expose the same measurements on more than one clock.
        basis, sources = max(sorted(by_basis.items()), key=lambda item: (
            _duration([window for _, value in item[1] for window in value['intervals']]), item[0] == 'UTC'))
        partial |= len(by_basis) > 1 or drive['identity_status'] != 'NATIVE'
        windows = [window for _, value in sources for window in value['intervals']]
        partial |= sum(end - start for start, end in windows) > _duration(windows) + .000001
        partial |= any(not _finite(value.get('distance_m')) for _, value in sources)
        key = tuple(drive[field] for field in ('game', 'car_key', 'track_key', 'layout_key'))
        context = contexts.setdefault(key, dict(utc=[], native_utc=[], native_other=[], unshared=[]))
        if basis == 'UTC':
            context['utc'].extend(sources)
            if drive['identity_status'] == 'NATIVE':
                context['native_utc'].extend(sources)
        elif drive['identity_status'] == 'NATIVE':
            context['native_other'].append(sources)
        else:
            context['unshared'].append(sources)
    return contexts, partial, unknown_included, replay_states


def _career(drives):
    contexts, partial, unknown_included, replay_states = _driving_groups(drives)
    time_total = distance_total = 0.
    measured = distance_measured = False
    for context in contexts.values():
        # Native identities prove distinct local clocks. Unidentified UTC
        # sources can be unioned on a shared clock, without declaring a session.
        # An unshared source may overlap either pool; retain a lower bound from
        # the largest supported pool instead of adding potentially repeated data.
        utc_time = _duration([window for _, value in context['utc'] for window in value['intervals']])
        native_time = _duration([window for _, value in context['native_utc'] for window in value['intervals']])
        native_time += sum(_duration([window for _, value in sources for window in value['intervals']])
                           for sources in context['native_other'])
        unshared_time = max((_duration([window for _, value in sources for window in value['intervals']])
                             for sources in context['unshared']), default=0.)
        time_total += max(utc_time, native_time, unshared_time)
        measured |= bool(context['utc'] or context['native_other'] or context['unshared'])
        utc_distance, utc_measured = _distance_lower_bound(context['utc'])
        native_distance, native_measured = _distance_lower_bound(context['native_utc'])
        for sources in context['native_other']:
            value, available = _distance_lower_bound(sources)
            native_distance += value
            native_measured |= available
        unshared_distance, unshared_measured = 0., False
        for sources in context['unshared']:
            value, available = _distance_lower_bound(sources)
            unshared_distance = max(unshared_distance, value)
            unshared_measured |= available
        distance_total += max(utc_distance, native_distance, unshared_distance)
        distance_measured |= utc_measured or native_measured or unshared_measured
        # Overlapping distance scalars cannot prove the shared portion.
        all_windows = [window for _, value in context['utc'] for window in value['intervals']]
        partial |= sum(end - start for start, end in all_windows) > _duration(all_windows) + .000001
        partial |= bool(context['unshared'])
    includes_sources = any(drive['identity_status'] != 'NATIVE' for drive in drives)
    partial |= includes_sources
    state = 'PARTIAL' if partial else 'OBSERVED'
    reason = '관측된 연속 주행 구간만 반영했습니다. 정차·확인된 리플레이·연결 공백은 제외했습니다.'
    if includes_sources:
        reason += ' 독립 세션 식별과 별개로 파일별 측정을 포함한 부분 관측입니다. 같은 차량·코스의 UTC 구간은 합집합으로 계산합니다.'
    if partial:
        reason += ' 겹친 거리와 서로 대응하지 않는 시계는 더하지 않아 확인된 일부의 하한을 표시합니다.'
    if unknown_included:
        reason += ' 리플레이 여부가 미확인인 구간을 포함한 부분 관측입니다.'
    unavailable = '운전량을 계산할 연속 주행 측정 구간이 없습니다. 관측 근거에서 누락 사유를 확인하세요.'
    replay_status = _combined_replay(replay_states)

    def observed_metric(value, available):
        return {**(_metric(value, reason, state) if available else _metric(reason=unavailable)),
                'replay_status': replay_status, 'scope': 'CONNECTED_OBSERVATIONS',
                'scope_label': '파일별 측정을 포함한 주행 관측량' if includes_sources
                               else '확인된 독립 세션의 주행 관측량' if replay_status != 'CONFIRMED_NON_REPLAY'
                               else '확인된 독립 세션의 비리플레이 운전량'}
    result_reason = '공식 최종 클래스 결과를 확인할 수 없습니다.'
    return {'distance_m': observed_metric(distance_total, distance_measured),
        'driving_time_s': observed_metric(time_total, measured),
        'wins': _metric(reason=result_reason), 'podiums': _metric(reason=result_reason),
        'average_position': _metric(reason=result_reason)}


def _lap_selection(drive):
    """A native lap appears once, even when complete in overlapping files."""
    grouped, reasons = {}, set()
    for observation in drive['observations']:
        for lap in observation.get('metrics', {}).get('laps', []):
            if (lap.get('measurable') is True and _finite(lap.get('lap_time_s')) and lap['lap_time_s'] > 0
                    and _finite(lap.get('start_s')) and _finite(lap.get('end_s')) and lap['end_s'] > lap['start_s']
                    and _replay_status(observation, lap) not in {'CONFIRMED_REPLAY', 'INVALID'}):
                grouped.setdefault(lap['lap_number'], []).append((observation['coverage']['time_basis'],
                    {**lap, 'replay_status': _replay_status(observation, lap)}))
    by_basis = {}
    for number, rows in sorted(grouped.items()):
        if max(row['lap_time_s'] for _, row in rows) - min(row['lap_time_s'] for _, row in rows) > .05:
            reasons.add('CONFLICTING_LAP_TIME')
            continue  # conflicting lap evidence never silently becomes a best lap
        bases = {basis for basis, _ in rows}
        if any(max(row[key] for row_basis, row in rows if row_basis == basis)
               - min(row[key] for row_basis, row in rows if row_basis == basis) > .05
               for basis in bases for key in ('start_s', 'end_s')):
            reasons.add('CONFLICTING_LAP_WINDOW')
            continue
        # Time agreement does not establish condition agreement. A duplicate
        # with missing/conflicting conditions may support the observed median,
        # but cannot become a comparable cohort by picking one source's values.
        for basis in bases:
            selected = dict(min((row for row_basis, row in rows if row_basis == basis),
                                key=lambda row: (row['start_s'], row['end_s'])))
            if any(row.get('conditions', {}) != selected.get('conditions', {}) for _, row in rows):
                selected['conditions'] = {}
            inputs = [row['inputs'] for _, row in rows if 'inputs' in row]
            if inputs and all(item == inputs[0] for item in inputs):
                selected['inputs'] = inputs[0]
            elif inputs:
                selected.pop('inputs', None)
                reasons.add('CONFLICTING_INPUT_OBSERVATIONS')
            selected['replay_status'] = _combined_replay(row['replay_status'] for _, row in rows)
            selected['time_basis'] = basis
            by_basis.setdefault(basis, []).append(selected)
    if len(by_basis) > 1:
        reasons.add('MIXED_TIME_BASES')
    # A native lap number can be repeated in overlapping source files, but
    # timestamps from unrelated clocks cannot establish additional laps.
    output = max(sorted(by_basis.items()), key=lambda item: (len(item[1]), item[0] == 'UTC'))[1] if by_basis else []
    blocked = set()
    ordered = sorted(output, key=lambda row: (row['start_s'], row['end_s']))
    widest = None
    for row in ordered:
        if widest is not None and row['start_s'] < widest['end_s'] - .05:
            blocked.update((row['lap_number'], widest['lap_number']))
        if widest is None or row['end_s'] > widest['end_s']:
            widest = row
    if blocked:
        reasons.add('OVERLAPPING_LAP_WINDOWS')
    return [row for row in output if row['lap_number'] not in blocked], reasons


def _clock_scope(drive):
    return tuple(drive[field] for field in ('game', 'car_key', 'track_key', 'layout_key'))


def _shared_clock_laps(drives):
    """Deduplicate UTC laps across file identities without inventing sessions.

    The native-ID pass preserves stronger evidence for condition comparisons.
    Each pass selects nonoverlapping complete laps by earliest finish. Local
    source clocks are never compared to another file's relative seconds.
    """
    selected = {drive['drive_id']: _lap_selection(drive) for drive in drives}
    by_scope = {}
    for drive in drives:
        for lap in selected[drive['drive_id']][0]:
            if lap['time_basis'] == 'UTC':
                by_scope.setdefault(_clock_scope(drive), []).append((drive, lap))
    for candidates in by_scope.values():
        occupied = []
        for native in (True, False):
            available = sorted(((drive, lap) for drive, lap in candidates
                if (drive['identity_status'] == 'NATIVE') == native),
                key=lambda item: (item[1]['end_s'], item[1]['start_s'], item[0]['drive_id']))
            accepted = []
            for drive, lap in available:
                window = [lap['start_s'], lap['end_s']]
                index = bisect_left(occupied, window)
                overlaps = _overlaps([window], occupied[max(0, index - 1):index + 1])
                overlaps |= bool(accepted and lap['start_s'] < accepted[-1][1] - .05)
                if overlaps:
                    rows, reasons = selected[drive['drive_id']]
                    # Candidate objects are shared with the selection list.
                    rows.remove(lap)
                    reasons.add('OVERLAPPING_SOURCE_LAPS')
                else:
                    accepted.append(window)
            occupied = _union(occupied + accepted)
    return selected


def _laps(drive):
    return _lap_selection(drive)[0]


def _stats(laps):
    values = [lap['lap_time_s'] for lap in laps]
    center = median(values)
    return center, median(abs(value - center) for value in values) / center * 100. if len(values) >= 2 else None


def _condition_key(laps):
    keys = set()
    for lap in laps:
        conditions = lap.get('conditions', {})
        if any(key not in conditions or conditions[key] is None for key in _REQUIRED_CONDITIONS):
            return None
        keys.add(json.dumps([conditions[key] for key in _REQUIRED_CONDITIONS], separators=(',', ':')))
    # Exact observed conditions are deliberately conservative for this first
    # report. Missing fields or a moving fuel/weather range cannot prove parity.
    return next(iter(keys)) if len(keys) == 1 else None


def _scope(drive):
    return tuple(drive[key] for key in ('game', 'car_key', 'track_key', 'layout_key', 'session_type'))


def _scope_label(drive):
    labels = drive.get('labels', {})
    return ' · '.join([labels.get('car') or '차량 확인 필요', labels.get('track') or '코스 확인 필요',
                       {'PRACTICE': '연습', 'QUALIFY': '퀄리', 'RACE': '레이스'}.get(drive['session_type'], '세션 확인 필요')])


def _selected_period(drive, cutoff, now):
    if cutoff is None:
        return True
    stamp = _date(drive['recorded_at'])
    return stamp is not None and cutoff <= stamp <= now


def _observation_points(candidates):
    by_day = {}
    for drive, laps in candidates:
        stamp = _date(drive['recorded_at'])
        if stamp is not None:
            by_day.setdefault(stamp.date().isoformat(), []).append((_stats(laps), len(laps)))
    return [{'date': day, 'median_lap_s': round(median(row[0][0] for row in values), 3),
             'mad_pct': (round(median(spreads), 3) if (spreads := [row[0][1] for row in values
                         if row[0][1] is not None]) else None),
             'lap_count': sum(row[1] for row in values), 'observation_count': len(values)}
            for day, values in sorted(by_day.items())]


def _observed_change(points):
    if len(points) < 2:
        return None
    before, after = points[0]['median_lap_s'], points[-1]['median_lap_s']
    return round((after - before) / before * 100., 3)


def _pace(drives, cutoff, now, lap_selections=None):
    result = {'status': 'UNAVAILABLE', 'scope': '', 'median_lap_s': None, 'mad_pct': None,
        'lap_count': 0, 'session_count': 0, 'trend': 'UNAVAILABLE', 'pace_change_pct': None,
        'spread_change_pct': None, 'points': [], 'replay_status': 'UNKNOWN', 'trend_reason_codes': [],
        'observation_count': 0, 'observation_change_pct': None, 'comparison_status': 'UNAVAILABLE',
        'comparison_label': '', 'dispersion_status': 'UNAVAILABLE',
        'reason': '선택 범위 안에 측정 가능한 랩이 없습니다. 관측 근거에서 날짜·랩 측정 조건을 확인하세요.'}
    candidates = [(drive, lap_selections[drive['drive_id']][0] if lap_selections is not None else _laps(drive)) for drive in drives
                  if _selected_period(drive, cutoff, now)
                  and ((stamp := _date(drive['recorded_at'])) is None or stamp <= now)]
    candidates = [(drive, laps) for drive, laps in candidates if laps]
    if not candidates:
        result['trend_reason_codes'] = ['NO_MEASURED_RECORDINGS_IN_PERIOD']
        return result
    latest, latest_laps = max(candidates, key=lambda item: (item[0]['recorded_at'] or '', item[0]['drive_id']))
    center, spread = _stats(latest_laps)
    scope_known = all(latest[field] not in {'', 'UNKNOWN'} for field in ('car_key', 'track_key'))
    observed = [(drive, laps) for drive, laps in candidates
                if drive['drive_id'] == latest['drive_id'] or (scope_known and _scope(drive) == _scope(latest))]
    points = _observation_points(observed)
    result.update(status='PARTIAL', scope=_scope_label(latest), median_lap_s=round(center, 3),
        mad_pct=round(spread, 3) if spread is not None else None,
        dispersion_status='OBSERVED' if spread is not None else 'INSUFFICIENT_LAPS',
        lap_count=len(latest_laps), session_count=int(latest['identity_status'] == 'NATIVE'),
        reason=('선택 범위 안의 최근 한 관측에서 측정한 랩입니다. 공식 유효 판정은 확인되지 않았습니다. '
                '그래프는 같은 차량·코스·세션 종류의 날짜별 관측이며 동일 조건이나 실력 변화를 보증하지 않습니다.'),
        replay_status=_combined_replay(lap['replay_status'] for lap in latest_laps),
        points=points, observation_count=len(observed), observation_change_pct=_observed_change(points),
        comparison_status='OBSERVATION_ONLY', comparison_label='관측 변화 · 동일 조건 미확인')
    condition = _condition_key(latest_laps)
    blocked, explanations = [], []
    if latest['identity_status'] != 'NATIVE':
        blocked.append('NATIVE_IDENTITY_UNAVAILABLE')
        explanations.append('독립 세션 식별이 미확정이므로 파일별 측정과 관측 변화로 표시합니다.')
    if latest['recorded_at'] is None:
        blocked.append('RECORDING_UTC_UNAVAILABLE')
        explanations.append('날짜 미확인 기록의 측정값은 표시하지만 시간 그래프와 기간 추세에는 넣지 않습니다.')
    if result['replay_status'] != 'CONFIRMED_NON_REPLAY':
        explanations.append('리플레이 여부가 미확인인 부분 관측입니다.')
        if latest['game'] == 'iracing':
            blocked.append('REPLAY_STATE_UNAVAILABLE')
            explanations.append('비리플레이로 확인된 랩이 필요해 조건을 맞춘 추세 판단은 보류합니다.')
    if latest['session_type'] == 'UNKNOWN':
        blocked.append('SESSION_TYPE_UNAVAILABLE')
        explanations.append('실제 세션 종류를 확인해야 같은 종류의 기록을 비교할 수 있습니다.')
    if len(latest_laps) < 3:
        blocked.append('INSUFFICIENT_LAPS_PER_SESSION')
        explanations.append('기간 비교에는 각 독립 세션마다 측정 랩 3개 이상이 필요합니다.')
    if condition is None:
        missing = [key for key in _REQUIRED_CONDITIONS
                   if any(lap.get('conditions', {}).get(key) is None for lap in latest_laps)]
        blocked.append('CONDITIONS_MISSING' if missing else 'CONDITIONS_VARY_WITHIN_RECORDING')
        explanations.append('비교 조건 미확인: ' + '·'.join(_CONDITION_LABELS[key] for key in missing) + '.' if missing else
                            '측정 랩의 연료·날씨·타이어·셋업 조건이 모두 같지 않아 기간 비교를 보류합니다.')
    if blocked:
        result['trend_reason_codes'] = blocked
        result['reason'] += ' ' + ' '.join(explanations)
        return result
    comparable = [(drive, laps) for drive, laps in candidates
        if condition is not None and drive['identity_status'] == 'NATIVE'
        and _date(drive['recorded_at']) is not None
        and drive['session_type'] != 'UNKNOWN' and _scope(drive) == _scope(latest)
        and len(laps) >= 3 and _condition_key(laps) == condition
        and (drive['game'] != 'iracing' or all(lap['replay_status'] == 'CONFIRMED_NON_REPLAY' for lap in laps))]
    comparable.sort(key=lambda item: (item[0]['recorded_at'], item[0]['drive_id']))
    if len(comparable) < 4 or len({drive['recorded_at'][:10] for drive, _ in comparable}) < 2:
        result['trend_reason_codes'] = ['INSUFFICIENT_COMPARABLE_SESSIONS']
        result['reason'] += f' 같은 차량·코스·세션 종류와 동일한 관측 조건의 독립 세션 {len(comparable)}개를 확인했습니다. 2일 이상에 걸친 4개 세션이 필요합니다.'
        return result
    split = len(comparable) // 2
    before, after = comparable[:split], comparable[split:]
    # Do not pretend a change within the same observation day is a period trend.
    if before[-1][0]['recorded_at'][:10] >= after[0][0]['recorded_at'][:10]:
        result['trend_reason_codes'] = ['COMPARISON_PERIODS_OVERLAP']
        result['reason'] += ' 비교 전후 묶음에 같은 날짜가 겹쳐 기간 추세를 보류합니다.'
        return result
    previous = [_stats(laps) for _, laps in before]
    recent = [_stats(laps) for _, laps in after]
    old_pace, new_pace = median(row[0] for row in previous), median(row[0] for row in recent)
    old_spread, new_spread = median(row[1] for row in previous), median(row[1] for row in recent)
    change = (new_pace - old_pace) / old_pace * 100.
    noise = max(.1, old_spread + new_spread)
    trend = ('IMPROVING' if change < -noise and new_spread <= old_spread else
             'DECLINING' if change > noise else 'STEADY' if abs(change) <= noise else 'UNAVAILABLE')
    points = _observation_points(comparable)
    result.update(status='OBSERVED', session_count=len(comparable), lap_count=sum(len(laps) for _, laps in comparable),
        median_lap_s=round(new_pace, 3), mad_pct=round(new_spread, 3), trend=trend,
        pace_change_pct=round(change, 3), spread_change_pct=round(new_spread - old_spread, 3), points=points,
        observation_count=len(comparable), observation_change_pct=_observed_change(points),
        comparison_status='CONDITION_MATCHED', comparison_label='관측 조건을 맞춘 비교')
    if trend == 'UNAVAILABLE':
        result['trend_reason_codes'] = ['PACE_AND_SPREAD_DISAGREE']
    result['reason'] = ('같은 조합·실제 세션 종류·관측 조건의 독립 세션을 비교했습니다. 운전 실력의 확정 판정은 아닙니다.'
                        if trend != 'UNAVAILABLE' else '페이스와 편차가 서로 다른 방향으로 변해 단일 성장 판정을 보류합니다.')
    return result


def _diagnostics(drives, lap_selections):
    counts, stale_sources = {}, set()
    measured = replay_unknown = driving_unavailable = source_observations = 0
    for drive in drives:
        laps, conflicts = lap_selections[drive['drive_id']]
        measured += bool(laps)
        replay_unknown += any(_unknown_replay(observation) for observation in drive['observations'])
        reasons = set(conflicts)
        if drive['identity_status'] != 'NATIVE':
            reasons.add('NATIVE_IDENTITY_UNAVAILABLE')
        if not laps:
            reasons.add('NO_MEASURABLE_LAPS')
        available_driving = False
        for observation in drive['observations']:
            source_observations += 1
            metrics = observation.get('metrics', {})
            if (metrics.get('calculation_version') != METRICS_VERSION
                    or 'METRICS_EXTRACTION_FAILED' in metrics.get('driving', {}).get('reason_codes', [])):
                stale_sources.add(observation['source_id'])
                reasons.add('OUTDATED_METRICS')
            driving = metrics.get('driving', {})
            available_driving |= driving.get('status') != 'UNAVAILABLE' and _finite(driving.get('time_s'))
            reasons.update(driving.get('reason_codes', []))
            reasons.update(code for code in observation.get('coverage', {}).get('reasons', []) if code in _REASONS)
            if _unknown_replay(observation):
                reasons.add('REPLAY_STATE_UNAVAILABLE')
            for lap in metrics.get('laps', []):
                reasons.update(lap.get('reason_codes', []))
        driving_unavailable += not available_driving
        for code in reasons:
            counts[code] = counts.get(code, 0) + 1
    measured_laps = sum(len(laps) for laps, _ in lap_selections.values())
    if not drives:
        reason = '주행 파일을 분석하면 관측의 측정 가능 범위와 누락 사유를 보여드립니다.'
    else:
        reason = f'연결 관측 {len(drives)}개 중 {measured}개에서 측정 가능한 {measured_laps}개 랩을 확인했습니다. 같은 원본·확인된 세션·공유 UTC에서 식별할 수 있는 중복을 제외했습니다.'
        if replay_unknown:
            reason += f' {replay_unknown}개 관측은 리플레이 여부가 미확인이라 부분 관측으로 표시합니다.'
        if any(drive['identity_status'] != 'NATIVE' for drive in drives):
            reason += ' 독립 세션 식별이 미확정인 파일도 측정과 관측 변화를 표시하며, 대응하지 않는 시계의 누적은 하한으로 표시합니다.'
        if stale_sources:
            reason += f' 측정 재확인이 필요한 원본 {len(stale_sources)}개가 있습니다.'
    return dict(observation_count=len(drives), source_observation_count=source_observations,
        measured_observation_count=measured, measured_lap_count=measured_laps,
        replay_unknown_observation_count=replay_unknown,
        driving_unavailable_observation_count=driving_unavailable, outdated_source_count=len(stale_sources),
        blocking_reason_counts=[dict(code=code, count=count,
            reason=_REASONS.get(code, '해당 측정 조건을 확인하지 못한 구간은 제외하거나 부분 관측으로 표시합니다.'))
            for code, count in sorted(counts.items())], reason=reason)


def _growth_period(rows):
    """Equal day weight avoids a long recording or split files dominating pace."""
    points = _observation_points(rows)
    if not points:
        return None
    center = median(point['median_lap_s'] for point in points)
    between_days = median(abs(point['median_lap_s'] - center) for point in points)/center*100.
    return dict(start_date=points[0]['date'], end_date=points[-1]['date'], day_count=len(points),
        observation_count=len(rows), session_count=sum(drive['identity_status'] == 'NATIVE' for drive, _ in rows),
        lap_count=sum(len(laps) for _, laps in rows), median_lap_s=round(center, 3),
        mad_pct=(round(median(spreads), 3) if (spreads := [point['mad_pct'] for point in points
                      if point['mad_pct'] is not None]) else None), between_day_mad_pct=round(between_days, 3))


def _growth(drives, cutoff, now, lap_selections, period_days):
    result = dict(status='UNAVAILABLE', comparison_status='UNAVAILABLE', scope='', previous=None, current=None,
        pace_change_pct=None, dispersion_change_pp=None, pace_direction='UNCERTAIN',
        dispersion_direction='UNCERTAIN', retention='UNCERTAIN', comparison_window='UNAVAILABLE',
        baseline='SAME_COMBINATION_RAW_MEDIAN', strengths=[],
        uncertainty=dict(reason_codes=[], reasons=[], sample_size_sufficient=False,
                         pace_noise_pct=None, dispersion_noise_pp=None),
        reason='선택 기간의 최근 조합에서 날짜가 확인된 측정 기록을 비교합니다.')
    selected = [(drive, lap_selections[drive['drive_id']][0]) for drive in drives
                if _selected_period(drive, cutoff, now) and lap_selections[drive['drive_id']][0]
                and ((_date(drive['recorded_at']) or now) <= now)]
    if not selected:
        result['uncertainty'].update(reason_codes=['NO_MEASURED_RECORDINGS_IN_PERIOD'],
            reasons=['선택 기간의 최근 관측이 없어 과거 세션으로 대신 비교하지 않습니다.'])
        return result
    latest, latest_laps = max(selected, key=lambda item: (item[0]['recorded_at'] or '', item[0]['drive_id']))
    result['scope'] = _scope_label(latest)
    if any(latest[field] in {'', 'UNKNOWN'} for field in ('car_key', 'track_key')):
        result['uncertainty'].update(reason_codes=['COMBINATION_IDENTITY_UNAVAILABLE'],
            reasons=['차량·코스의 같은 조합을 확인할 수 없습니다.'])
        return result
    cohort = [(drive, lap_selections[drive['drive_id']][0]) for drive in drives
              if _scope(drive) == _scope(latest) and (stamp := _date(drive['recorded_at'])) is not None
              and stamp <= now and lap_selections[drive['drive_id']][0]]
    current = [(drive, laps) for drive, laps in cohort if _selected_period(drive, cutoff, now)]
    if not current:
        result['uncertainty'].update(reason_codes=['RECORDING_UTC_UNAVAILABLE'],
            reasons=['최근 조합의 실제 주행 날짜를 확인할 수 없습니다.'])
        return result
    previous = [(drive, laps) for drive, laps in cohort if cutoff is not None
                and cutoff - timedelta(days=period_days) <= _date(drive['recorded_at']) < cutoff]
    notes, codes = [], []
    if previous:
        result['comparison_window'] = 'ADJACENT_PERIODS'
    else:
        dates = sorted({drive['recorded_at'][:10] for drive, _ in current})
        if len(dates) >= 2:
            boundary = dates[len(dates)//2]
            previous = [(drive, laps) for drive, laps in current if drive['recorded_at'][:10] < boundary]
            current = [(drive, laps) for drive, laps in current if drive['recorded_at'][:10] >= boundary]
            result['comparison_window'] = 'SPLIT_OBSERVED_DAYS'
            if period_days:
                codes.append('PREVIOUS_PERIOD_EMPTY')
                notes.append('직전 같은 길이의 기간에 기록이 없어 선택 기간의 관측일을 앞·뒤로 나눴습니다.')
        else:
            codes.append('NO_PREVIOUS_COMBINATION_OBSERVATION')
            notes.append('최근 차량·코스 조합의 이전 날짜 기록이 없어 다른 조합을 퇴보 비교로 쓰지 않습니다.')
    result.update(previous=_growth_period(previous), current=_growth_period(current))
    if result['previous'] is None or result['current'] is None:
        result['uncertainty'].update(reason_codes=codes, reasons=notes)
        return result
    old, new = result['previous'], result['current']
    combined = previous + current
    missing = [key for key in _REQUIRED_CONDITIONS
               if any(lap.get('conditions', {}).get(key) is None for _, laps in combined for lap in laps)]
    condition_keys = [_condition_key(laps) for _, laps in combined]
    matched = (not missing and all(key is not None and key == condition_keys[0] for key in condition_keys)
               and all(drive['identity_status'] == 'NATIVE' and drive['session_type'] != 'UNKNOWN'
                       and len(laps) >= 3 and (drive['game'] != 'iracing'
                            or all(lap['replay_status'] == 'CONFIRMED_NON_REPLAY' for lap in laps))
                       for drive, laps in combined)
               and latest['layout_key'] not in {'', 'UNKNOWN'} and len(previous) >= 2 and len(current) >= 2)
    if missing:
        codes.append('CONDITIONS_MISSING')
        notes.append('미확인 조건: ' + '·'.join(_CONDITION_LABELS[key] for key in missing) + '.')
    if any(key is None for key in condition_keys) or len(set(condition_keys)) > 1:
        codes.append('CONDITIONS_NOT_MATCHED')
        notes.append('연료·날씨·타이어·셋업 차이가 포함된 관측 비교입니다.')
    if any(drive['identity_status'] != 'NATIVE' for drive, _ in combined):
        codes.append('NATIVE_IDENTITY_UNAVAILABLE')
        notes.append('파일별 관측을 독립 세션 수로 확정하지 않습니다.')
    if latest['layout_key'] in {'', 'UNKNOWN'}:
        codes.append('LAYOUT_IDENTITY_UNAVAILABLE')
        notes.append('코스 레이아웃 식별이 미확인이라 동일 조건 비교로 확정하지 않습니다.')
    if latest['session_type'] == 'UNKNOWN':
        codes.append('SESSION_TYPE_UNAVAILABLE')
        notes.append('실제 세션 종류가 미확인인 관측 비교입니다.')
    if any(lap['replay_status'] != 'CONFIRMED_NON_REPLAY' for drive, laps in combined
           if drive['game'] == 'iracing' for lap in laps):
        codes.append('REPLAY_STATE_UNAVAILABLE')
        notes.append('일부 관측의 리플레이 상태가 확인되지 않았습니다.')
    spread_available = old['mad_pct'] is not None and new['mad_pct'] is not None
    sufficient = (len(latest_laps) >= 3 and spread_available
                  and all(period['lap_count'] >= 6 and period['observation_count'] >= 2 for period in (old, new)))
    if not sufficient:
        codes.append('LIMITED_COMPARISON_SAMPLE')
        notes.append('기간별 관측 2개·측정 6랩보다 적어 방향은 참고용으로만 읽어 주세요.')
    if len(latest_laps) < 3:
        codes.append('LATEST_OBSERVATION_SAMPLE_LIMITED')
        notes.append('최근 한 관측의 측정 랩이 3개 미만이라 과거 세션으로 최근의 유지 여부를 대신 판단하지 않습니다.')
    change = (new['median_lap_s'] - old['median_lap_s']) / old['median_lap_s'] * 100.
    dispersion = new['mad_pct'] - old['mad_pct'] if spread_available else None
    noise = max(.1, (old['mad_pct'] + new['mad_pct']) if spread_available else 0.,
                old['between_day_mad_pct'] + new['between_day_mad_pct'])
    spread_noise = .05
    pace_direction = 'MAINTAINED' if abs(change) <= noise else 'FASTER' if change < 0 else 'SLOWER'
    spread_direction = ('UNCERTAIN' if dispersion is None else 'MAINTAINED' if abs(dispersion) <= spread_noise
                        else 'NARROWER' if dispersion < 0 else 'WIDER')
    retention = ('UNCERTAIN' if not sufficient else 'MAINTAINED'
                 if change <= noise and dispersion <= spread_noise else 'CHANGED')
    result.update(status='OBSERVED' if matched else 'PARTIAL',
        comparison_status='CONDITION_MATCHED' if matched else 'OBSERVATION_ONLY',
        pace_change_pct=round(change, 3), dispersion_change_pp=round(dispersion, 3) if dispersion is not None else None,
        pace_direction=pace_direction, dispersion_direction=spread_direction, retention=retention,
        uncertainty=dict(reason_codes=sorted(set(codes)), reasons=notes, sample_size_sufficient=sufficient,
                         pace_noise_pct=round(noise, 3), dispersion_noise_pp=spread_noise,
                         method='DESCRIPTIVE_MAD_RANGE'),
        reason='같은 조합의 원시 랩타임 중앙값과 편차를 같은 방식으로 비교합니다. 외부 기준 교체의 영향은 없으며 변화의 원인이나 실력 향상을 확정하지 않습니다.')
    if matched and sufficient and retention == 'MAINTAINED':
        result['strengths'] = [dict(id='PACE_REPRODUCTION', title='이 조합에서 페이스와 편차를 유지한 관측',
            scope=result['scope'], session_count=old['session_count'] + new['session_count'],
            lap_count=old['lap_count'] + new['lap_count'], reason='조건을 확인한 전후 기록에서 나타난 결과이며 조작 성향과 별도입니다.')]
    return result


def build_driver_report(snapshot, game, period_days=30, *, now=None):
    if game not in {'iracing', 'lmu'} or type(period_days) is not int or period_days not in {0, 30, 90}:
        raise ValueError('리포트 조회 조건이 올바르지 않습니다.')
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=period_days) if period_days else None
    drives = [drive for drive in snapshot['drives'] if drive['game'] == game]
    drives.sort(key=lambda drive: (drive['recorded_at'] or '', drive['drive_id']), reverse=True)
    dates = [drive['recorded_at'] for drive in drives if drive['recorded_at']]
    native = [drive for drive in drives if drive['identity_status'] == 'NATIVE']
    period_observations = [drive for drive in drives if _selected_period(drive, cutoff, now)]
    period = [drive for drive in period_observations if drive['identity_status'] == 'NATIVE']
    selected_sources = {row['source_id'] for drive in period_observations for row in drive['observations']}
    lap_selections = _shared_clock_laps(drives)
    diagnostics = _diagnostics(drives, lap_selections)
    period_diagnostics = (diagnostics if not period_days else _diagnostics(period_observations,
        {drive['drive_id']: lap_selections[drive['drive_id']] for drive in period_observations}))
    pace = _pace(drives, cutoff, now, lap_selections)
    style = build_style([drive for drive in period_observations
                         if (_date(drive['recorded_at']) or now) <= now], lap_selections)
    growth = _growth(drives, cutoff, now, lap_selections, period_days)
    coverage = {'source_count': snapshot['source_count'], 'native_session_count': len(native),
        'source_only_count': len(drives) - len(native), 'undated_count': sum(not drive['recorded_at'] for drive in drives),
        'period_session_count': len(period), 'period_observation_count': len(period_observations),
        'period_source_count': len(selected_sources),
        'period_source_only_count': len(period_observations) - len(period),
        'period_measured_observation_count': period_diagnostics['measured_observation_count'],
        'period_replay_unknown_observation_count': period_diagnostics['replay_unknown_observation_count'],
        'period_driving_unavailable_observation_count': period_diagnostics['driving_unavailable_observation_count'],
        'period_reason': period_diagnostics['reason'],
        'period_measured_lap_count': sum(len(lap_selections[drive['drive_id']][0]) for drive in period_observations),
        'earliest_recorded_at': min(dates) if dates else None,
        'latest_recorded_at': max(dates) if dates else None, **diagnostics}
    title, body = ('나의 주행 이야기를 시작해요', '주행 파일을 분석하면 확인된 기록부터 차곡차곡 연결합니다.') if not drives else (
        '이번 기록에서 확인된 것부터', '기록을 연결했습니다. 확인된 관측과 아직 비교할 수 없는 부분을 함께 보여드려요.')
    if pace['trend'] == 'IMPROVING':
        title, body = '이 조합의 대표 페이스가 좋아졌어요', '조건을 확인한 기록에서 대표 랩이 짧아졌고 편차도 늘지 않았습니다. 이 조합의 관측 변화로 읽어 주세요.'
    elif pace['trend'] == 'DECLINING':
        title, body = '최근 페이스를 한 번 살펴볼까요', '조건을 확인한 기록에서 최근 대표 랩이 길어졌습니다. 원인을 단정하지 않고 이번 관측부터 살펴봅니다.'
    if period_days and not period_observations:
        title, body = ('선택 기간의 기록이 아직 없어요',
                       f'최근 {period_days}일에 날짜가 확인된 주행 관측이 없습니다. 전체 보기에서 이전 기록을 확인할 수 있습니다.')
    recent_drives = [{'drive_id': drive['drive_id'], 'recorded_at': drive['recorded_at'],
        'car': drive.get('labels', {}).get('car') or '차량 확인 필요',
        'track': drive.get('labels', {}).get('track') or '코스 확인 필요',
        'session_type': drive['session_type'], 'identity_status': drive['identity_status'],
        'measured_laps': len(lap_selections[drive['drive_id']][0]), 'replay_status': _drive_replay(drive),
        'in_selected_period': True,
        'identity_reason': (_REASONS['NATIVE_IDENTITY_UNAVAILABLE'] if drive['identity_status'] != 'NATIVE'
                            else '플랫폼 세션·사용자 식별자로 연결한 독립 세션입니다.')} for drive in period_observations[:6]]
    discoveries = []
    selected_dates = [drive['recorded_at'] for drive in period_observations if drive['recorded_at']]
    if selected_dates:
        first = min(selected_dates)[:10]
        discovery_key = f'{game}:first:{period_days}:{first}' if period_days else f'{game}:first:{first}'
        discoveries.append({'id': hashlib.sha256(discovery_key.encode()).hexdigest(),
            'date': first, 'title': '선택 기간의 첫 관측' if period_days else '처음 연결한 관측',
            'body': (f'최근 {period_days}일 안에서 날짜가 확인된 첫 기록입니다. 전체 기록의 시작이나 최초 달성을 뜻하지 않습니다.'
                     if period_days else '앱이 보관한 기록의 시작입니다. 생애 첫 주행이나 최초 달성을 뜻하지 않습니다.')})
    overview_scope = (f'최근 {period_days}일 · 원본 {len(selected_sources)}개 · 확인된 세션 {len(period)}개 · '
                      f'파일별 관측 {len(period_observations) - len(period)}개' if period_days else
                      f'원본 {snapshot["source_count"]}개 · 확인된 세션 {len(native)}개 · 파일별 관측 {len(drives) - len(native)}개')
    return {'schema_version': 1, 'calculation_version': CALCULATION_VERSION, 'game': game,
        'period_days': period_days, 'state': 'OBSERVED' if drives else 'EMPTY', 'coverage': coverage,
        'scopes': {'career': 'ALL_CONNECTED_OBSERVATIONS' if not period_days else 'SELECTED_PERIOD_OBSERVATIONS', 'pace': 'SELECTED_PERIOD',
                   'recent_drives': 'ALL_CONNECTED_OBSERVATIONS' if not period_days else 'SELECTED_PERIOD_OBSERVATIONS',
                   'overview': 'ALL_CONNECTED_OBSERVATIONS' if not period_days else 'SELECTED_PERIOD_OBSERVATIONS',
                   'discoveries': 'ALL_CONNECTED_OBSERVATIONS' if not period_days else 'SELECTED_PERIOD_OBSERVATIONS'},
        'scope_labels': {'career': f'최근 {period_days}일의 주행 관측량' if period_days else '연결한 전체 기록의 주행 관측량',
                         'pace': f'최근 {period_days}일 안의 측정 기록' if period_days else '연결한 전체 측정 기록',
                         'recent_drives': f'최근 {period_days}일 안의 최근 6개 관측' if period_days else '연결한 전체 기록에서 최근 6개 관측'},
        'career': _career(period_observations), 'overview': {'title': title, 'body': body, 'scope': overview_scope},
        'pace': pace, 'style': style, 'growth': growth,
        'quality': build_quality([drive for drive in period_observations
                                 if (_date(drive['recorded_at']) or now) <= now]),
        'challenge': {'title': '이번 기록 한 번 살펴보기' if drives else '기록 한 번 살펴보기',
            'body': '기록의 측정 랩과 코너를 살펴보세요. 아직 확인되지 않은 결과를 달성으로 바꾸지 않습니다.' if drives else
                    '이미 있는 주행 파일을 분석하고 이번 주행에서 확인된 내용을 살펴보세요.', 'action': 'analysis'},
        'discoveries': discoveries, 'recent_drives': recent_drives,
        'limitations': ['분석한 원본의 관측이며 게임의 평생 기록이 아닙니다.',
            '파일별 관측을 독립 세션 수로 확정하지 않습니다. UTC 중복과 대응하지 않는 시계는 누적에서 보수적으로 처리합니다.',
            '관측 그래프의 변화에는 연료·날씨·타이어·셋업 차이가 포함될 수 있습니다.',
            '측정 랩은 공식 유효 랩이나 실력 향상을 뜻하지 않습니다.']}


def read_driver_report(game, period_days=30, *, store=None):
    storage = store if store is not None else DriverHistoryStore()
    report = build_driver_report(storage.report_snapshot(game), game, period_days)
    # Results have their own authority and read-only store. Keep the pure
    # observation builder usable without a results database or an API process.
    try:
        from driver_results import DriverResultsStore, read_driver_results
        official = read_driver_results(game, period_days, store=DriverResultsStore(history_path=storage.path))['aggregate']
        for key in ('wins', 'podiums', 'average_position'):
            report['career'][key] = {**official[key], 'scope': 'OFFICIAL_FINAL_CLASS_RESULTS',
                                     'scope_label': (f'최근 {period_days}일의 공식 최종 클래스 결과' if period_days
                                                     else '보관한 공식 최종 클래스 결과')}
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, AttributeError):
        for key in ('wins', 'podiums', 'average_position'):
            report['career'][key] = _metric(reason='공식 결과 저장소를 읽지 못했습니다. 주행 관측은 그대로 표시합니다.')
    return report
