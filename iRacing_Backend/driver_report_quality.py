"""Read-only quality summaries, keeping numerator and denominator on one scope."""
from __future__ import annotations

import math
from bisect import bisect_left, insort


def _finite(value):
    return type(value) in {int, float} and math.isfinite(value)


def _duration(windows):
    return sum(end - start for start, end in windows)


def _overlap(one, two):
    return any(min(b, d) > max(a, c) + .000001 for a, b in one for c, d in two)


def _scope(drive):
    return tuple(drive[key] for key in ('game', 'car_key', 'track_key', 'layout_key'))


def _available(value=None, reason='', **details):
    return dict(value=round(value, 3) if value is not None else None,
                status='PARTIAL' if value is not None else 'UNAVAILABLE', reason=reason, **details)


def _lap_ratio(drives):
    rows = []
    missing = 0
    for drive in drives:
        # Different clocks cannot establish additional laps in the same drive.
        bases, verdicts = {}, {}
        for observation in drive['observations']:
            quality = observation.get('metrics', {}).get('lap_quality', {})
            missing += not bool(quality.get('completed_laps'))
            basis = observation['coverage']['time_basis']
            for row in quality.get('completed_laps', []):
                if row.get('replay_status') in {'CONFIRMED_REPLAY', 'INVALID'}:
                    continue
                bases.setdefault(basis, {}).setdefault(row['lap_number'], []).append(row)
                verdicts.setdefault(row['lap_number'], set()).add(row.get('analysis_validity', 'UNKNOWN'))
        if not bases:
            continue
        basis, numbered = max(bases.items(), key=lambda item: (len(item[1]), item[0] == 'UTC'))
        for number, copies in numbered.items():
            states = verdicts[number]
            row = min(copies, key=lambda item: (item['end_s'], item['start_s']))
            consistent = all(max(item[key] for item in copies) - min(item[key] for item in copies) <= .05
                             for key in ('start_s', 'end_s'))
            state = next(iter(states)) if len(states) == 1 and consistent else 'UNKNOWN'
            rows.append(dict(drive=drive, basis=basis, number=number, state=state,
                             start=row['start_s'], end=row['end_s']))
    def shared_laps(items):
        components = []
        for row in sorted(items, key=lambda row: (row['start'], row['end'])):
            if components and row['start'] < components[-1]['end'] - .000001:
                group = components[-1]
                group['end'] = max(group['end'], row['end'])
                if (group['state'] != row['state'] or abs(group['start'] - row['start']) > .05
                        or abs(group['first_end'] - row['end']) > .05):
                    group['state'] = 'UNKNOWN'
            else:
                components.append(dict(row, first_end=row['end']))
        return components

    contexts = {}
    for row in rows:
        drive = row['drive']
        context = contexts.setdefault(_scope(drive), dict(utc=[], native_utc=[], native_other={}, unshared={}))
        if row['basis'] == 'UTC':
            context['utc'].append(row)
            if drive['identity_status'] == 'NATIVE':
                context['native_utc'].append(row)
        elif drive['identity_status'] == 'NATIVE':
            context['native_other'].setdefault(drive['drive_id'], []).append(row)
        else:
            context['unshared'].setdefault(drive['drive_id'], []).append(row)
    accepted = []
    for context in contexts.values():
        pools = [shared_laps(context['utc']), shared_laps(context['native_utc'])
                 + [row for items in context['native_other'].values() for row in shared_laps(items)]]
        pools += [shared_laps(items) for items in context['unshared'].values()]
        accepted.extend(max(pools, key=len))
    valid = sum(row['state'] == 'VALID' for row in accepted)
    invalid = sum(row['state'] == 'INVALID' for row in accepted)
    unknown = len(accepted) - valid - invalid
    denominator = valid + invalid
    reason = ('완주 랩 중 앱의 분석 판정이 확인된 랩을 분모로 계산합니다. '
              '피트·인시던트·랩타임 등 기존 분석 기준이며 게임의 공식 유효 판정이 아닙니다. '
              '판정 미확인 랩과 확인된 리플레이는 제외하고 공유 UTC의 복사 랩은 한 번만 셉니다. '
              '시계가 서로 대응하지 않는 파일은 확인된 가장 큰 묶음만 사용하며 판정이 충돌한 랩은 보류합니다.')
    if unknown or missing:
        reason += f' 판정 미확인 {unknown}랩, 완주 판정 근거가 없는 관측 {missing}개는 비율에 넣지 않았습니다.'
    if not denominator:
        reason = '완주 랩의 분석 판정이 아직 없습니다. 전체 기록 분석 또는 기존 기록 다시 확인 후 반영됩니다.'
    return _available(valid / denominator * 100. if denominator else None, reason,
                      valid_laps=valid, invalid_laps=invalid, classified_laps=denominator,
                      completed_laps=len(accepted), unknown_laps=unknown,
                      authority='ANALYSIS_POLICY', scope='SELECTED_PERIOD_COMPLETED_LAPS')


def _nonoverlapping(sources):
    groups, conflicting = {}, set()
    for index, item in enumerate(sources):
        key = tuple(tuple(window) for window in item['intervals'])
        evidence = (item['increase_points'], tuple((event['start_s'], event['end_s'], event['points'])
                                                  for event in item.get('events', [])))
        groups.setdefault(key, []).append((index, evidence))
    for items in groups.values():
        if len({evidence for _, evidence in items}) > 1:
            conflicting.update(index for index, _ in items)
    chosen, occupied = [], []
    eligible = [item for index, item in enumerate(sources) if index not in conflicting]
    for item in sorted(eligible, key=lambda item: (-_duration(item['intervals']), item['source_id'])):
        overlaps = False
        for window in item['intervals']:
            index = bisect_left(occupied, window)
            if _overlap([window], occupied[max(0, index-1):index+1]):
                overlaps = True
                break
        if not overlaps:
            chosen.append(item)
            for window in item['intervals']:
                insort(occupied, window)
    return chosen


def _incident_rate(drives):
    contexts = {}
    for drive in drives:
        # Use the same competitive-session scope for both points and time.
        # Stored observations already carry this type; no source re-read is needed.
        if drive.get('session_type') not in {'RACE', 'QUALIFY'}:
            continue
        per_basis = {}
        for observation in drive['observations']:
            incident = observation.get('metrics', {}).get('incidents', {})
            if (incident.get('status') not in {'OBSERVED', 'PARTIAL'}
                    or incident.get('replay_status') in {'CONFIRMED_REPLAY', 'INVALID'}
                    or not _finite(incident.get('increase_points'))
                    or not incident.get('intervals') or incident.get('observed_time_s', 0) <= 0):
                continue
            item = dict(incident, source_id=observation['source_id'])
            per_basis.setdefault(observation['coverage']['time_basis'], []).append(item)
        if not per_basis:
            continue
        basis, sources = max(per_basis.items(), key=lambda item: (
            sum(_duration(value['intervals']) for value in _nonoverlapping(item[1])), item[0] == 'UTC'))
        context = contexts.setdefault(_scope(drive), dict(utc=[], native=[], unshared=[]))
        if basis == 'UTC':
            context['utc'].extend(sources)
        elif drive['identity_status'] == 'NATIVE':
            context['native'].extend(_nonoverlapping(sources))
        else:
            context['unshared'].extend(sources)
    chosen = []
    for context in contexts.values():
        # UTC and native session clocks may describe the same driving. Retain
        # the largest supported pool, never divide a lower-bound point count
        # by the longer union of unrelated observations.
        pools = [_nonoverlapping(context['utc']), context['native']]
        pools += [[item] for item in context['unshared']]
        chosen.extend(max(pools, key=lambda items: sum(_duration(item['intervals']) for item in items)))
    points = sum(item['increase_points'] for item in chosen)
    seconds = sum(_duration(item['intervals']) for item in chosen)
    reason = ('레이스·퀄리 세션만 집계하며 연습과 세션 종류 미확인 기록은 점수와 시간 모두에서 제외합니다. '
              '개인 인시던트 누적값의 증가점수 ÷ 같은 구간의 트랙 위 관측시간 × 3600입니다. '
              '첫 표본의 이미 누적된 점수·카운터 리셋·기록 공백은 제외합니다. '
              '겹친 원본은 완전한 관측 하나를 선택하고 동일 구간의 점수가 충돌하면 제외합니다. 서로 대응하지 않는 시계는 합산하지 않습니다. '
              '정차를 포함한 관측 구간의 비율이며 공식 경기 전체의 점수나 사고 횟수가 아닙니다.')
    if not seconds:
        reason = ('선택 기간에 레이스·퀄리의 개인 인시던트 카운터와 같은 구간의 관측시간이 없습니다. '
                  '연습과 세션 종류 미확인 기록은 점수와 시간 모두에서 제외합니다.')
    return _available(points / seconds * 3600. if seconds else None, reason,
                      increase_points=points if seconds else None, observed_time_s=round(seconds, 3),
                      observation_count=len(chosen), scope='RACE_QUALIFY_ON_TRACK_OBSERVED_TIME',
                      session_types=['RACE', 'QUALIFY'])


def build_quality(drives):
    return dict(valid_lap_ratio=_lap_ratio(drives), incidents_per_hour=_incident_rate(drives))
