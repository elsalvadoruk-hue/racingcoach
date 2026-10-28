"""Offline observations and event selection. No SDK, audio, settings or network access.

This deliberately replays recorded observations, not the live 30 Hz decision loop.
All boundaries reset comparisons; missing values are never replaced by clear/zero.
"""
from __future__ import annotations

from bisect import bisect_left
from collections import Counter, defaultdict
import math

VERSION = 'oval-review-v1'
MAX_GAP = .5
SIDE = {0: '비활성', 1: '겹침 없음', 2: '왼쪽', 3: '오른쪽',
        4: '양쪽', 5: '왼쪽 두 대', 6: '오른쪽 두 대'}
SIGNALS = ('session_state', 'session_flags', 'pace_mode', 'pits_open',
           'player_pace_line', 'player_pace_row', 'player_pace_flags')
CLEAR = {'clear_all', 'clear_left', 'clear_right'}
LIMITS = [
    '최대 10Hz 관측 기록입니다. 실제 30Hz 판단·음성 출력의 정확한 재현이 아닙니다.',
    '생략·결측·0.5초 초과 공백·시계 역행마다 상태 비교를 다시 시작합니다.',
    'started/completed는 앱의 재생 이력이며 실제 청취나 차량 위치의 정답을 증명하지 않습니다.',
    '게임의 절차 지시·프리 패스·웨이브 어라운드·안전 여부를 확정하지 않습니다.',
    '위트 후보는 관측 자료 검토용입니다. 발화 허용·실제 발화·음성 합성은 하지 않습니다.',
]


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def fields(names, values):
    if isinstance(values, dict):
        return dict(values)
    if isinstance(names, (list, tuple)) and isinstance(values, (list, tuple)):
        return dict(zip(names, values))
    return {}


def decode(header, row, *, include_cars=True):
    """Use the recorded schema only, including older/reordered field lists."""
    own = fields(header.get('own_fields'), row.get('own'))
    oval = fields(header.get('oval_fields'), row.get('oval'))
    return {'t': row.get('t'), 'own': own, 'oval': oval,
            'cars': [fields(header.get('fields'), car) for car in (row.get('c') or [])] if include_cars else []}


def spans(header, rows):
    """Half-open row ranges with continuous, increasing session clocks."""
    cuts = {0}
    selection = header.get('export_selection')
    if selection is not None:
        offset, previous_stop, valid = 0, -1, True
        parts = selection.get('segments', []) if isinstance(selection, dict) else []
        for part in parts:
            if not isinstance(part, dict):
                valid = False
                break
            start, stop = part.get('start'), part.get('stop')
            if type(start) is not int or type(stop) is not int or not 0 <= start < stop or start < previous_stop:
                valid = False
                break
            cuts.add(offset)
            offset += stop - start
            previous_stop = stop
        # Malformed coverage cannot silently imply continuity.
        if not valid or offset != len(rows):
            cuts.update(range(len(rows)))
    result, start, previous = [], 0, None
    for index, row in enumerate(rows):
        t = row.get('t')
        broken = index in cuts or not number(t) or not number(previous) or not 0 < t - previous <= MAX_GAP + 1e-8
        if index and broken:
            result.append((start, index))
            start = index
        previous = t
    if rows:
        result.append((start, len(rows)))
    return result


def observe(header, rows, *, include_cars=True):
    decoded = [decode(header, row, include_cars=include_cars) for row in rows]
    ranges = spans(header, rows)
    events = []
    def add(index, kind, rank, **detail):
        events.append({'index': index, 't': rows[index].get('t'), 'kind': kind, 'rank': rank, **detail})
    for segment, (start, stop) in enumerate(ranges):
        previous, overlap_at, long_sent = None, None, False
        for index in range(start, stop):
            current = decoded[index]
            raw = current['own'].get('car_left_right')
            side = int(raw) if number(raw) and raw in SIDE else None
            old = previous['own'].get('car_left_right') if previous else None
            old = int(old) if number(old) and old in SIDE else None
            if side in (2, 3, 4, 5, 6) and (previous is None or old != side):
                add(index, 'multi_overlap' if side in (4, 5, 6) else 'overlap', 0 if side in (4, 5, 6) else 1,
                    before=old, after=side, segment=segment)
            elif previous is not None and side != old:
                add(index, 'clear_transition' if side == 1 and old in (2, 3, 4, 5, 6) else 'side_change', 1,
                    before=old, after=side, segment=segment)
            if side in (2, 3, 4, 5, 6) and number(current['t']):
                if overlap_at is None:
                    overlap_at = current['t']
                if not long_sent and current['t'] - overlap_at >= 5:
                    add(index, 'long_overlap', 1, seconds=current['t'] - overlap_at, segment=segment)
                    long_sent = True
            else:
                overlap_at, long_sent = None, False
            if previous is not None:
                for name in SIGNALS:
                    old_value, value = previous['oval'].get(name), current['oval'].get(name)
                    if old_value != value:
                        add(index, 'signal_change' if old_value is not None and value is not None else 'signal_missing',
                            2 if old_value is not None and value is not None else 3,
                            field=name, before=old_value, after=value, segment=segment)
                if previous['own'].get('on_pit_road') != current['own'].get('on_pit_road'):
                    add(index, 'pit_change', 2, before=previous['own'].get('on_pit_road'),
                        after=current['own'].get('on_pit_road'), segment=segment)
            previous = current
    return decoded, ranges, events


def radio_anchors(ranges, rows, radio):
    """Only unambiguous same-clock matches. Repeated session clocks stay unaligned."""
    anchors = []
    times = [[rows[i].get('t') for i in range(start, stop)] for start, stop in ranges]
    for ordinal, event in enumerate(radio):
        t = event.get('session_time')
        if not number(t):
            continue
        matches = []
        for segment, ((start, stop), values) in enumerate(zip(ranges, times)):
            if not values or not all(number(v) for v in (values[0], values[-1])) or not values[0] <= t <= values[-1]:
                continue
            at = bisect_left(values, t)
            candidates = [i for i in (at - 1, at) if 0 <= i < len(values)]
            chosen = min(candidates, key=lambda i: abs(values[i] - t))
            if abs(values[chosen] - t) <= .15:
                matches.append((segment, start + chosen))
        if len(matches) == 1:
            segment, index = matches[0]
            anchors.append({'index': index, 't': t, 'kind': 'radio_problem', 'rank': 0,
                            'segment': segment, 'radio_index': ordinal})
    return anchors


def _spread(items):
    """First, last, middle, then subdivisions: deterministic temporal diversity."""
    if not items:
        return []
    out, queue = [items[0]], [(1, len(items))]
    if len(items) > 1:
        out.append(items[-1])
        queue = [(1, len(items) - 1)]
    for start, stop in queue:
        if start < stop:
            middle = (start + stop) // 2
            out.append(items[middle])
            queue.extend(((start, middle), (middle + 1, stop)))
    return out


def select_windows(header, rows, radio=(), width=200, windows=6):
    """At most width*windows original rows. Reserve endpoints and a comparison window."""
    # Selection uses the own/pacing signals. Keep the original car arrays intact;
    # decoding every nearby car is only needed by the offline replay viewer.
    decoded, ranges, events = observe(header, rows, include_cars=False)
    problems = [e for e in radio if e.get('status') in ('failed', 'interrupted')]
    events += radio_anchors(ranges, rows, problems)
    if not rows:
        return [], {}
    width = max(1, min(width, len(rows)))
    chosen = []
    owner = {}
    for start, stop in ranges:
        for index in range(start, stop):
            owner[index] = (start, stop)
    def window(index):
        start, stop = owner[index]
        lo = max(start, min(index - width // 2, stop - width))
        return lo, min(stop, lo + width)
    def add(index):
        lo, hi = window(index)
        if len(chosen) < windows and not any(a <= index < b for a, b in chosen):
            # Overlapping windows are merged below, without duplicating source rows.
            chosen.append((lo, hi))
    add(0)
    add(len(rows) - 1)
    # A quiet comparison is a recorded clear/non-pit sample, not a permission to joke.
    quiet = [i for i, d in enumerate(decoded) if d['own'].get('car_left_right') == 1
             and d['own'].get('on_pit_road') in (False, 0)]
    if quiet:
        add(quiet[len(quiet) // 2])
    groups = defaultdict(list)
    for e in events:
        groups[(e['rank'], e['kind'], e.get('field', ''))].append(e)
    # Round robin across event kinds, so one chattering signal cannot take every slot.
    spread = [_spread(group) for _, group in sorted(groups.items())]
    for depth in range(max((len(g) for g in spread), default=0)):
        for group in spread:
            if depth < len(group):
                add(group[depth]['index'])
        if len(chosen) >= windows:
            break
    for i in range(windows):
        add(i * (len(rows) - 1) // max(1, windows - 1))
    merged = []
    for lo, hi in sorted(chosen):
        if merged and lo < merged[-1][1] and owner[lo] == owner[merged[-1][0]]:
            merged[-1] = (merged[-1][0], max(hi, merged[-1][1]))
        else:
            merged.append((lo, hi))
    included = [e for e in events if any(lo <= e['index'] < hi for lo, hi in merged)]
    count = sum(hi - lo for lo, hi in merged)
    selection = {'mode': 'event_windows', 'version': VERSION,
                 'source_rows': len(rows), 'selected_rows': count, 'omitted_rows': len(rows) - count,
                 'source_segments': len(ranges), 'events': len(events), 'selected_events': len(included),
                 'omitted_events': len(events) - len(included),
                 'event_counts': dict(Counter(e['kind'] for e in events)),
                 'selected_event_counts': dict(Counter(e['kind'] for e in included)),
                 'unaligned_radio_problems': len(problems) - sum(e['kind'] == 'radio_problem' for e in events),
                 'segments': [{'start': lo, 'stop': hi, 't_start': rows[lo].get('t'),
                               't_end': rows[hi - 1].get('t')} for lo, hi in merged]}
    return [row for lo, hi in merged for row in rows[lo:hi]], selection


def review(manifest, header, rows, radio, shadow=(), procedures=()):
    """Machine-readable review plus observation frames for a local replay viewer."""
    decoded, ranges, events = observe(header, rows)
    anchors = radio_anchors(ranges, rows, radio)
    shadow_anchors = radio_anchors(ranges, rows, shadow)
    procedure_anchors = radio_anchors(ranges, rows, procedures)
    procedure_by_frame = defaultdict(list)
    for anchor in procedure_anchors:
        procedure_by_frame[anchor['index']].append(procedures[anchor['radio_index']])
    shadow_by_frame = defaultdict(list)
    for anchor in shadow_anchors:
        shadow_by_frame[anchor['index']].append(shadow[anchor['radio_index']])
    by_frame = defaultdict(list)
    for anchor in anchors:
        by_frame[anchor['index']].append(anchor['radio_index'])
    findings, frames, candidates = [], [], []
    starts = {start for start, _ in ranges}
    for index, d in enumerate(decoded):
        frames.append({'index': index, 'boundary': index in starts, **d,
                       'radio': [radio[i] for i in by_frame[index]], 'banter': shadow_by_frame[index],
                       'procedures': procedure_by_frame[index]})
        for ordinal in by_frame[index]:
            event = radio[ordinal]
            side = d['own'].get('car_left_right')
            occupied = {'clear_all': (2, 3, 4, 5, 6), 'clear_left': (2, 4, 5), 'clear_right': (3, 4, 6)}
            if event.get('status') == 'started' and number(side) and side in occupied.get(event.get('key'), ()):
                findings.append({'kind': 'clear_near_overlap', 'status': 'REVIEW', 't': event.get('session_time'),
                                 'index': index, 'radio_index': ordinal,
                                 'reason': '클리어 재생 시작 근처에 겹침 관측. 10Hz 차이와 실제 영상 확인 필요'})
    # Recorded clear transitions can seed future banter review; missing safety gates block admission.
    for e in events:
        if e['kind'] == 'clear_transition':
            candidates.append({'index': e['index'], 't': e['t'], 'status': 'DEFERRED',
                               'trigger': 'overlap_resolved',
                               'reason': '주행·리플레이·재시작·부하·발화 여유·최근 소재 이력이 모두 확인되지 않음'})
    for ordinal, event in enumerate(radio):
        if event.get('status') in ('failed', 'interrupted'):
            findings.append({'kind': 'playback_' + event['status'], 'status': 'REVIEW',
                             't': event.get('session_time'), 'radio_index': ordinal, 'reason': event.get('reason')})
    recent = {}
    aligned = {a['radio_index']: a for a in anchors}
    for ordinal, event in enumerate(radio):
        if event.get('status') != 'started':
            continue
        key, t = event.get('key'), event.get('session_time')
        old = recent.get(key)
        anchor = aligned.get(ordinal)
        if (old and anchor and old[2] is not None and anchor['segment'] == old[2]
                and number(t) and number(old[0]) and 0 <= t - old[0] < 1 and event.get('event_id') != old[1]):
            findings.append({'kind': 'rapid_repeat', 'status': 'REVIEW', 't': t,
                             'radio_index': ordinal, 'reason': '같은 key의 다른 안내가 1초 안에 시작. 정당한 재진입 여부 검토'})
        recent[key] = (t, event.get('event_id'), anchor['segment'] if anchor else None)
    navigation = list(events)
    navigation.extend({**a, 'kind': 'banter_shadow', 'banter_index': a['radio_index']}
                      for a in shadow_anchors)
    navigation.extend({**a, 'kind':'oval_procedure', 'procedure_index':a['radio_index']} for a in procedure_anchors)
    for anchor in anchors:
        event = radio[anchor['radio_index']]
        if event.get('status') in ('failed', 'interrupted'):
            navigation.append({**anchor, 'kind': 'playback_' + event['status']})
    for finding in findings:
        if 'index' in finding:
            navigation.append({**finding, 'kind': 'review_' + finding['kind']})
    result = {'version': VERSION, 'run_id': manifest.get('run_id'),
              'profile': (manifest.get('spotter') or {}).get('profile', 'UNKNOWN'),
              'collection_versions': manifest.get('collection_versions') or {},
              'export_app_version': manifest.get('export_app_version', manifest.get('app_version')),
              'track': manifest.get('track'), 'car': manifest.get('car'), 'session_type': manifest.get('session_type'),
              'coverage': {'rows': len(rows), 'segments': len(ranges),
                           'selection': header.get('export_selection'),
                           'extras': manifest.get('extras') or {},
                           'radio_rows': len(radio), 'aligned_radio_rows': len(anchors),
                           'unaligned_radio_rows': len(radio) - len(anchors),
                           'banter_rows': len(shadow), 'aligned_banter_rows': len(shadow_anchors),
                           'unaligned_banter_rows': len(shadow) - len(shadow_anchors),
                           'procedure_rows':len(procedures), 'aligned_procedure_rows':len(procedure_anchors),
                           'unaligned_procedure_rows':len(procedures)-len(procedure_anchors),
                           'missing_own_schema': not bool(header.get('own_fields'))},
              'radio_counts': dict(Counter(e.get('status', 'UNKNOWN') for e in radio)),
              'radio_timeline': radio,
              'banter_observations': list(shadow),
              'procedure_observations':list(procedures),
              'feature_coverage':feature_coverage(events, shadow, procedures),
              'events': events, 'navigation': sorted(navigation, key=lambda e: e['index']),
              'findings': findings, 'banter_candidates': candidates,
              'verdict': 'REVIEW' if findings else 'INSUFFICIENT_FOR_PASS', 'limitations': LIMITS}
    return result, frames


def feature_coverage(events, shadow, procedures):
    """Coverage is evidence to review, never an activation decision."""
    counts = Counter(e['kind'] for e in events)
    coverage = {name:{'observations':counts[name], 'mode':'BASIC', 'verdict':'REVIEW_REQUIRED' if counts[name] else 'NOT_OBSERVED'}
                for name in ('overlap','multi_overlap','long_overlap','clear_transition')}
    for name in ('caution','one_lap_to_green','initial_green','restart_green','restart_cancelled',
                 'restart_resolved','pit_permission','line_assignment','end_of_line','free_pass','wave_around'):
        count = sum(r.get('kind') == name for r in procedures)
        coverage[name] = {'observations':count, 'mode':'SHADOW', 'verdict':'REVIEW_REQUIRED' if count else 'NOT_OBSERVED'}
    from banter_catalog import ACTIVE_TRIGGERS
    for name in ACTIVE_TRIGGERS:
        count = sum(r.get('trigger') == name for r in shadow)
        coverage['banter_'+name] = {'observations':count, 'mode':'SHADOW', 'verdict':'REVIEW_REQUIRED' if count else 'NOT_OBSERVED'}
    return coverage


def regression_windows(result, frames, radius=20, maximum=32):
    """Small original observation windows, bounded by recorded segments."""
    windows = []
    events = [e for e in result['navigation'] if e.get('kind') not in ('signal_change','signal_missing')]
    events.sort(key=lambda e: e['kind'] not in ('oval_procedure','banter_shadow'))
    for event in events[:maximum]:
        index = event['index']
        lo = index
        while lo > max(0,index-radius) and not frames[lo]['boundary']:
            lo -= 1
        hi = index+1
        while hi < min(len(frames),index+radius+1) and not frames[hi]['boundary']:
            hi += 1
        windows.append({'event':event, 'source_start':lo, 'source_stop':hi, 'frames':frames[lo:hi]})
    return {'format':'oval-observation-regression-v1', 'run_id':result['run_id'],
            'exact_live_replay':False, 'expected_verdict':'MANUAL_REVIEW_REQUIRED',
            'windows':windows, 'omitted_events':max(0,len(events)-maximum)}
