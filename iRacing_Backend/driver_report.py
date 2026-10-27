"""Deterministic local reports of observed facts, with explicit missing evidence."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from statistics import median

from driver_history import DriverHistoryStore


CALCULATION_VERSION = 'observed-report-v1'
_REQUIRED_CONDITIONS = ('fuel_level_l', 'track_temp_c', 'air_temp_c', 'track_wetness', 'tire_compound', 'setup_hash')


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


def _career(drives):
    time_total, distance_total, measured, distance_measured = 0., 0., 0, 0
    partial = False
    for drive in drives:
        if drive['identity_status'] != 'NATIVE':
            partial = True
            continue
        by_basis = {}
        for observation in drive['observations']:
            driving = observation.get('metrics', {}).get('driving', {})
            if driving.get('status') == 'UNAVAILABLE' or not _finite(driving.get('time_s')):
                partial = True
                continue
            basis = observation['coverage']['time_basis']
            if basis not in {'UTC', 'NATIVE_SESSION_TIME'}:
                partial = True
                continue
            by_basis.setdefault(basis, []).append((observation['source_id'], driving))
            partial |= driving.get('status') != 'OBSERVED'
        if not by_basis:
            continue
        # Different clocks cannot be summed. Pick the widest observed clock.
        _, sources = max(sorted(by_basis.items()), key=lambda item: _duration(
            [window for _, value in item[1] for window in value.get('intervals', [])]))
        partial |= len(by_basis) > 1
        windows = [window for _, item in sources for window in item.get('intervals', [])]
        time_total += _duration(windows)
        measured += 1
        selected = []
        for _, driving in sorted(sources, key=lambda item: (-item[1]['time_s'], item[0])):
            value = driving.get('distance_m')
            if not _finite(value):
                partial = True
                continue
            candidate = driving.get('intervals', [])
            if _overlaps(candidate, selected):
                # Scalars do not prove the distance in the overlapping part.
                # Retain a lower bound from whole non-overlapping observations.
                partial = True
                continue
            selected = _union(selected + candidate)
            distance_total += value
            distance_measured += 1
    state = 'PARTIAL' if partial else 'OBSERVED'
    reason = ('확정된 세션의 확인 가능한 운전 구간만 반영했습니다. 겹친 원본의 거리는 중복 합산하지 않아 일부만 포함될 수 있습니다.'
              if partial else '확인된 운전 구간의 합계입니다. 정차·리플레이·연결 공백을 제외했습니다.')
    unavailable = '독립 세션과 실제 운전 구간을 확인한 기록이 아직 없습니다.'
    result_reason = '공식 최종 클래스 결과를 확인할 수 없습니다.'
    return {'distance_m': _metric(distance_total, reason, state) if distance_measured else _metric(reason=unavailable),
        'driving_time_s': _metric(time_total, reason, state) if measured else _metric(reason=unavailable),
        'wins': _metric(reason=result_reason), 'podiums': _metric(reason=result_reason),
        'average_position': _metric(reason=result_reason)}


def _laps(drive):
    """A native lap appears once, even when complete in overlapping files."""
    grouped = {}
    for observation in drive['observations']:
        for lap in observation.get('metrics', {}).get('laps', []):
            if lap.get('measurable') is True and _finite(lap.get('lap_time_s')) and lap['lap_time_s'] > 0:
                grouped.setdefault(lap['lap_number'], []).append(lap)
    output = []
    for number, rows in sorted(grouped.items()):
        if max(row['lap_time_s'] for row in rows) - min(row['lap_time_s'] for row in rows) > .05:
            continue  # conflicting lap evidence never silently becomes a best lap
        selected = dict(min(rows, key=lambda row: (row['start_s'], row['end_s'])))
        # Time agreement does not establish condition agreement. A duplicate
        # with missing/conflicting conditions may support the observed median,
        # but cannot become a comparable cohort by picking one source's values.
        if any(row.get('conditions', {}) != selected.get('conditions', {}) for row in rows):
            selected['conditions'] = {}
        output.append(selected)
    return output


def _stats(laps):
    values = [lap['lap_time_s'] for lap in laps]
    center = median(values)
    return center, median(abs(value - center) for value in values) / center * 100.


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


def _pace(drives, cutoff, now):
    result = {'status': 'UNAVAILABLE', 'scope': '', 'median_lap_s': None, 'mad_pct': None,
        'lap_count': 0, 'session_count': 0, 'trend': 'UNAVAILABLE', 'pace_change_pct': None,
        'spread_change_pct': None, 'points': [], 'reason': '측정 가능한 랩과 비교 조건을 먼저 확인합니다.'}
    candidates = [(drive, _laps(drive)) for drive in drives
                  if (stamp := _date(drive['recorded_at'])) is not None and cutoff <= stamp <= now]
    candidates = [(drive, laps) for drive, laps in candidates if laps]
    if not candidates:
        return result
    latest, latest_laps = max(candidates, key=lambda item: (item[0]['recorded_at'], item[0]['drive_id']))
    center, spread = _stats(latest_laps)
    result.update(status='PARTIAL', scope=_scope_label(latest), median_lap_s=round(center, 3),
        mad_pct=round(spread, 3), lap_count=len(latest_laps), session_count=1,
        reason='최근 한 기록의 측정 랩입니다. 공식 유효 여부와 기간 비교 조건은 확인이 필요합니다.',
        points=[{'date': _date(latest['recorded_at']).date().isoformat(), 'median_lap_s': round(center, 3),
                 'mad_pct': round(spread, 3), 'lap_count': len(latest_laps)}])
    condition = _condition_key(latest_laps)
    comparable = [(drive, laps) for drive, laps in candidates
        if condition is not None and drive['identity_status'] == 'NATIVE'
        and drive['session_type'] != 'UNKNOWN' and _scope(drive) == _scope(latest)
        and len(laps) >= 3 and _condition_key(laps) == condition]
    comparable.sort(key=lambda item: (item[0]['recorded_at'], item[0]['drive_id']))
    if latest['identity_status'] != 'NATIVE' or len(comparable) < 4 or len({drive['recorded_at'][:10] for drive, _ in comparable}) < 2:
        return result
    split = len(comparable) // 2
    before, after = comparable[:split], comparable[split:]
    # Do not pretend a change within the same observation day is a period trend.
    if before[-1][0]['recorded_at'][:10] >= after[0][0]['recorded_at'][:10]:
        return result
    previous = [_stats(laps) for _, laps in before]
    recent = [_stats(laps) for _, laps in after]
    old_pace, new_pace = median(row[0] for row in previous), median(row[0] for row in recent)
    old_spread, new_spread = median(row[1] for row in previous), median(row[1] for row in recent)
    change = (new_pace - old_pace) / old_pace * 100.
    noise = max(.1, old_spread + new_spread)
    trend = ('IMPROVING' if change < -noise and new_spread <= old_spread else
             'DECLINING' if change > noise else 'STEADY' if abs(change) <= noise else 'UNAVAILABLE')
    by_day = {}
    for drive, laps in comparable:
        by_day.setdefault(drive['recorded_at'][:10], []).append((_stats(laps), len(laps)))
    points = [{'date': day, 'median_lap_s': round(median(row[0][0] for row in values), 3),
        'mad_pct': round(median(row[0][1] for row in values), 3), 'lap_count': sum(row[1] for row in values)}
        for day, values in sorted(by_day.items())]
    result.update(status='OBSERVED', session_count=len(comparable), lap_count=sum(len(laps) for _, laps in comparable),
        median_lap_s=round(new_pace, 3), mad_pct=round(new_spread, 3), trend=trend,
        pace_change_pct=round(change, 3), spread_change_pct=round(new_spread - old_spread, 3), points=points)
    result['reason'] = ('같은 조합·실제 세션 종류·관측 조건의 독립 세션을 비교했습니다. 운전 실력의 확정 판정은 아닙니다.'
                        if trend != 'UNAVAILABLE' else '페이스와 편차가 서로 다른 방향으로 변해 단일 성장 판정을 보류합니다.')
    return result


def build_driver_report(snapshot, game, period_days=30, *, now=None):
    if game not in {'iracing', 'lmu'} or type(period_days) is not int or period_days not in {30, 90}:
        raise ValueError('리포트 조회 조건이 올바르지 않습니다.')
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=period_days)
    drives = [drive for drive in snapshot['drives'] if drive['game'] == game]
    drives.sort(key=lambda drive: (drive['recorded_at'] or '', drive['drive_id']), reverse=True)
    dates = [drive['recorded_at'] for drive in drives if drive['recorded_at']]
    native = [drive for drive in drives if drive['identity_status'] == 'NATIVE']
    period = [drive for drive in native if (stamp := _date(drive['recorded_at'])) is not None and cutoff <= stamp <= now]
    pace = _pace(drives, cutoff, now)
    coverage = {'source_count': snapshot['source_count'], 'native_session_count': len(native),
        'source_only_count': len(drives) - len(native), 'undated_count': sum(not drive['recorded_at'] for drive in drives),
        'period_session_count': len(period), 'earliest_recorded_at': min(dates) if dates else None,
        'latest_recorded_at': max(dates) if dates else None}
    title, body = ('나의 주행 이야기를 시작해요', '주행 파일을 분석하면 확인된 기록부터 차곡차곡 연결합니다.') if not drives else (
        '이번 기록에서 확인된 것부터', '기록을 연결했습니다. 확인된 관측과 아직 비교할 수 없는 부분을 함께 보여드려요.')
    if pace['trend'] == 'IMPROVING':
        title, body = '이 조합의 대표 페이스가 좋아졌어요', '조건을 확인한 기록에서 대표 랩이 짧아졌고 편차도 늘지 않았습니다. 이 조합의 관측 변화로 읽어 주세요.'
    elif pace['trend'] == 'DECLINING':
        title, body = '최근 페이스를 한 번 살펴볼까요', '조건을 확인한 기록에서 최근 대표 랩이 길어졌습니다. 원인을 단정하지 않고 이번 관측부터 살펴봅니다.'
    recent_drives = [{'drive_id': drive['drive_id'], 'recorded_at': drive['recorded_at'],
        'car': drive.get('labels', {}).get('car') or '차량 확인 필요',
        'track': drive.get('labels', {}).get('track') or '코스 확인 필요',
        'session_type': drive['session_type'], 'identity_status': drive['identity_status'],
        'measured_laps': len(_laps(drive))} for drive in drives[:6]]
    discoveries = []
    if dates:
        first = min(dates)[:10]
        discoveries.append({'id': hashlib.sha256(f'{game}:first:{first}'.encode()).hexdigest(),
            'date': first, 'title': '처음 연결한 관측', 'body': '앱이 보관한 기록의 시작입니다. 생애 첫 주행이나 최초 달성을 뜻하지 않습니다.'})
    return {'schema_version': 1, 'calculation_version': CALCULATION_VERSION, 'game': game,
        'period_days': period_days, 'state': 'OBSERVED' if drives else 'EMPTY', 'coverage': coverage,
        'career': _career(drives), 'overview': {'title': title, 'body': body,
            'scope': f'원본 {snapshot["source_count"]}개 · 확인된 세션 {len(native)}개 · 미확정 관측 {len(drives) - len(native)}개'},
        'pace': pace, 'style': {'status': 'UNAVAILABLE', 'items': [], 'reason': '지속적인 운전 성향을 설명할 코너별 비교 근거를 먼저 확인합니다.'},
        'challenge': {'title': '이번 기록 한 번 살펴보기' if drives else '기록 한 번 살펴보기',
            'body': '기록의 측정 랩과 코너를 살펴보세요. 아직 확인되지 않은 결과를 달성으로 바꾸지 않습니다.' if drives else
                    '이미 있는 주행 파일을 분석하고 이번 주행에서 확인된 내용을 살펴보세요.', 'action': 'analysis'},
        'discoveries': discoveries, 'recent_drives': recent_drives,
        'limitations': ['분석한 원본의 관측이며 게임의 평생 기록이 아닙니다.',
            '식별이 미확정인 원본은 독립 세션 수·누적 운전량·성장 추세에 합산하지 않습니다.',
            '측정 랩은 공식 유효 랩이나 실력 향상을 뜻하지 않습니다.']}


def read_driver_report(game, period_days=30, *, store=None):
    storage = store if store is not None else DriverHistoryStore()
    return build_driver_report(storage.report_snapshot(game), game, period_days)
