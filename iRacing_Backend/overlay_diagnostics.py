"""Bounded, once-per-pass overlay observations using recorded corner events.

No inference about intent, car damage, causation, or setup effectiveness.
The live and reference curves must cover the same continuous distance window.
"""
from collections import deque
from statistics import median
import math

import numpy as np

from corner_events import RawLap, corner_events, _window_ok

EVENTS = {'brake_start': '제동 시작', 'brake_release': '브레이크 해제',
          'throttle_first': '가속 시작', 'full_throttle': '풀스로틀'}
REQUIREMENTS = {
    'divergence': '같은 위치의 기준·본인 입력과 실제 조작 지점이 필요합니다.',
    'pattern': '연속 입력을 관측한 깨끗한 통과가 3회 필요합니다.',
    'phases': '기준의 해제·가속 경계와 두 기록의 연속 구간 시간이 필요합니다.',
    'common': '연료·노면 온도·젖음이 비슷한 깨끗한 통과가 5회 필요합니다.',
}


def number(value):
    return float(value) if type(value) in (int, float) and math.isfinite(value) else None


def card(title, metric='—', unit='', detail='', stats=(), tone='neutral', available=True):
    return dict(title=title, metric=str(metric), unit=unit, detail=detail,
                stats=list(stats), tone=tone, available=available)


def waiting(title, reason):
    return card(title+' · 측정 대기', detail=reason, available=False)


def stable_transitions(raw, key, low, high, begin, end):
    """Hysteresis and 150 ms dwell on both sides; short noise never adds an event."""
    if not _window_ok(raw, begin, end, (key,)):
        return None
    values = raw.channels.get(key)
    stable = candidate = since = None
    transitions = []
    for index in np.flatnonzero((raw.pct >= begin) & (raw.pct <= end)):
        value, at = values[index], raw.time_s[index]
        state = 1 if value >= high else 0 if value <= low else None
        if state is None:
            candidate = since = None
            continue
        if candidate != state:
            candidate, since = state, at
        if at-since >= .15-1e-8 and stable != state:
            transitions.append(state)
            stable = state
    return transitions


def repeated_inputs(raw, entry, apex, exit_pct):
    brake = stable_transitions(raw, 'Brake', .05, .08, entry, exit_pct)
    throttle = stable_transitions(raw, 'Throttle', .25, .50, apex, exit_pct)
    if brake is None or throttle is None:
        return None
    # The first brake application is not a reapplication. A relift requires
    # an observed sustained acceleration before the sustained lift.
    return {'rebrakes': max(0, brake.count(1)-1),
            'relifts': sum(a == 1 and b == 0 for a, b in zip(throttle, throttle[1:]))}


def sustained_event(raw, event, key, threshold, rising):
    if event is None or event['source'] == 'CLIPPED':
        return False
    at = event['time_s']
    # Recorded samples must support the state for 150 ms on each side.
    if raw.time_s[0] > at-.15 or raw.time_s[-1] < at+.15:
        return False
    values = raw.channels[key]
    before = values[(raw.time_s >= at-.15) & (raw.time_s < at)]
    after = values[(raw.time_s > at) & (raw.time_s <= at+.15)]
    return bool(len(before) and len(after) and
                (np.all(before <= threshold) and np.all(after > threshold) if rising else
                 np.all(before > threshold) and np.all(after <= threshold)))


def phase_deltas(current, reference, events, entry, exit_pct):
    """Same reference-event partition as analysis_corners._corner_phases.

    Entry→reference brake release→reference throttle pickup→exit. A held
    brake without a release stays unknown, rather than inventing a boundary.
    """
    release = events.get('brake_release')
    release = release['pct'] if release and release['source'] != 'CLIPPED' else None
    inside = (reference.pct >= entry) & (reference.pct <= exit_pct)
    if release is None and np.all(reference.channels['Brake'][inside] <= .05):
        release = entry
    if release is None:
        return None
    pickup = events.get('throttle_first')
    pickup = pickup['pct'] if pickup else exit_pct
    release = min(exit_pct, max(entry, release))
    pickup = min(exit_pct, max(release, pickup))
    bounds = [entry, release, pickup, exit_pct]
    def interval(raw, a, b):
        return float(np.interp(b, raw.pct, raw.time_s)-np.interp(a, raw.pct, raw.time_s))
    return {'bounds': bounds, 'deltas': [round(interval(current, a, b)-interval(reference, a, b), 6)
                                        for a, b in zip(bounds, bounds[1:])]}


def compatible(a, b):
    """Known fuel, track temperature and wetness; a missing tire stays explicit."""
    for key, limit in (('fuel_l', 10), ('track_temp_c', 5), ('track_wetness', 0)):
        first, second = number(a.get(key)), number(b.get(key))
        if first is None or second is None or abs(first-second) > limit:
            return False
    x, y = a.get('tire_compound'), b.get('tire_compound')
    return x is None or y is None or x == y


class CornerDiagnostics:
    def __init__(self):
        self.reset()

    def reset(self):
        self.scope = self.last_at = self.ticket = None
        self.rows = {}
        self.result = None

    def build(self, reference, view):
        sample, passed = view.get('snapshot') or {}, view.get('corner_pass') or {}
        at, end = number(sample.get('session_time')), number(passed.get('exit_at'))
        scope = view.get('run_id'), reference, view.get('track_length_m')
        if scope != self.scope or at is not None and self.last_at is not None and not 0 <= at-self.last_at <= 1:
            self.reset()
            self.scope = scope
        self.last_at = at
        active = (view.get('connected') is True and sample.get('on_track') is True
                  and sample.get('is_replay') is False and sample.get('on_pit_road') is False)
        if not active:
            self.rows.clear()
            self.ticket = self.result = None
            return {}
        lap = number(passed.get('lap'))
        if not (passed.get('clean') is True and at is not None and end is not None and 0 <= at-end <= 30
                and lap is not None and number(sample.get('lap_number')) in (lap, lap+1)):
            return {}
        geometry = tuple(number(passed.get(key)) for key in ('entry_pct', 'apex_pct', 'exit_pct'))
        ticket = passed.get('id'), lap, end, geometry
        if ticket == self.ticket:
            return self.result
        self.ticket = ticket
        self.result = self._measure(reference, view, passed, geometry)
        return self.result

    def _measure(self, reference, view, passed, geometry):
        label = str(passed.get('call') or passed.get('id'))
        result = {key: waiting(label+' · '+title, reason) for key, title, reason in (
            ('divergence', '처음 달라진 조작', '같은 위치의 기준·본인 입력과 실제 조작 지점이 필요합니다.'),
            ('pattern', '반복 조작 패턴', '연속 입력을 관측한 깨끗한 통과가 3회 필요합니다.'),
            ('phases', '진입·회전·탈출 손익', '기준의 해제·가속 경계와 두 기록의 연속 구간 시간이 필요합니다.'),
            ('common', '빠른 통과의 공통 패턴', '연료·노면 온도·젖음이 비슷한 깨끗한 통과가 5회 필요합니다.'))}
        entry, apex, exit_pct = geometry
        length = number(view.get('track_length_m'))
        duration = number(passed.get('duration_s'))
        if (None in geometry or not 0 <= entry < apex < exit_pct <= 1 or not length or length <= 0
                or duration is None or duration <= 0):
            return result
        history = view.get('_history') or view.get('points') or ()
        # Keep brackets, including the sample just beyond the interpolated exit.
        start = passed['exit_at']-passed.get('duration_s', 0)-3
        rows = [p for p in history if (number(p.get('session_time')) is not None
                and start <= p['session_time'] <= passed['exit_at']+.5 and p.get('lap_number') == passed['lap'])]
        if len(rows) < 8:
            return result
        times = np.array([p['session_time'] for p in rows], dtype=float)
        pct = np.array([p.get('distance_pct', np.nan) for p in rows], dtype=float)
        if not np.isfinite(pct).all() or not np.all(np.diff(pct) > 0) or not np.all(np.diff(times) > 0):
            return result
        a, b = max(0, int(np.searchsorted(pct, entry))-1), min(len(rows), int(np.searchsorted(pct, exit_pct))+1)
        if pct[a] > entry or pct[b-1] < exit_pct:
            return result
        rows, times, pct = rows[a:b], times[a:b], pct[a:b]
        if any(p.get('on_track') is not True or p.get('is_replay') is not False or p.get('on_pit_road') is not False
               or int(p.get('session_flags') or 0) & 0xC108 or p.get('car_left_right') in (2, 3, 4, 5, 6) for p in rows):
            return result
        counts = [number(p.get('incidents')) for p in rows]
        if any(x is not None and y is not None and x != y for x, y in zip(counts, counts[1:])):
            return result
        tick = min(.1, float(np.median(np.diff(times))))
        channels = {key: np.array([p.get(source) if number(p.get(source)) is not None else np.nan for p in rows], dtype=float)
                    for key, source in (('Brake', 'brake'), ('Throttle', 'throttle'), ('Speed', 'speed_mps'))}
        for key in ('Brake', 'Throttle'):
            if np.any((channels[key] < 0) | (channels[key] > 1)):
                return result
        raw = RawLap(times, pct, channels, tick, length, 'LIVE')
        if not _window_ok(raw, entry, exit_pct, ('Brake', 'Throttle')):
            return result
        observed = corner_events(raw, entry, apex, exit_pct, pad_m=0)
        pattern = repeated_inputs(raw, entry, apex, exit_pct)
        if pattern is None:
            return result
        row = dict(lap=passed['lap'], duration=passed['duration_s'], pattern=pattern,
                   conditions={key: rows[0].get(key) for key in ('fuel_l', 'track_temp_c', 'track_wetness', 'tire_compound')},
                   full=observed['full_throttle']['pct'] if observed.get('full_throttle') and observed['full_throttle']['source'] != 'CLIPPED' else None)
        key = passed.get('id'), geometry
        if key not in self.rows:
            if len(self.rows) >= 64:
                self.rows.pop(next(iter(self.rows)))
            self.rows[key] = deque(maxlen=10)
        passes = self.rows[key]
        if not any(item['lap'] == row['lap'] for item in passes):
            passes.append(row)
        recent = list(passes)[-5:]
        if len(recent) >= 3:
            lifts = sum(item['pattern']['relifts'] > 0 for item in recent)
            brakes = sum(item['pattern']['rebrakes'] > 0 for item in recent)
            result['pattern'] = card(label+' · 재리프트 / 재제동', f'{lifts} / {brakes}', f'회 · 최근 {len(recent)}통과',
                '150ms 이상 유지한 입력 전환 · 실수 여부는 판단하지 않습니다.',
                [['이번 재리프트', f"{pattern['relifts']}회"], ['이번 재제동', f"{pattern['rebrakes']}회"]])
        comparable = [item for item in passes if compatible(item['conditions'], row['conditions'])]
        if len(comparable) >= 5:
            fastest = sorted(comparable, key=lambda item: item['duration'])[:3]
            common = []
            if all(item['pattern']['rebrakes'] == 0 for item in fastest):
                common.append(['재제동', '3회 모두 없음'])
            if all(item['pattern']['relifts'] == 0 for item in fastest):
                common.append(['재리프트', '3회 모두 없음'])
            full = [item['full'] for item in fastest if item['full'] is not None]
            if len(full) == 3:
                common.append(['풀스로틀 위치', f'{(max(full)-min(full))*length:.1f}m 범위'])
            result['common'] = card(label+' · 빠른 3회에서 관측', '3', f'/ {len(comparable)}통과',
                ' · '.join(' '.join(stat) for stat in common[:2])+' · 인과관계 아님' if common else '일치하는 재제동·재리프트 패턴 없음 · 인과관계 아님',
                common or [['공통 입력', '재제동·재리프트 일치 없음']], 'neutral')
        if reference is None:
            return result
        # Use the same recorded event detector on both curves, never a fabricated pedal curve.
        grid = reference.grid
        ref_axis = np.asarray(reference.distance)
        left = max(0, int(np.searchsorted(ref_axis, entry))-1)
        right = min(len(ref_axis), int(np.searchsorted(ref_axis, exit_pct))+2)
        span = slice(left, right)
        coverage = grid.get('_coverage')
        if coverage is not None and not np.all(np.asarray(coverage)[span]):
            return result
        ref_channels = {key: np.asarray(grid[key], dtype=float)[span] for key in ('Brake', 'Throttle')
                        if key in grid and np.ndim(grid[key]) == 1 and len(grid[key]) == len(ref_axis)}
        ref_times = np.asarray(reference.times)[span]
        ref_tick = np.diff(ref_times)
        ref_tick = ref_tick[np.isfinite(ref_tick) & (ref_tick > 0)]
        ref = RawLap(ref_times, ref_axis[span], ref_channels,
                     min(1/3, max(tick, float(np.max(ref_tick)))) if len(ref_tick) else tick, length, 'REFERENCE')
        if ref.pct[0] > entry or ref.pct[-1] < exit_pct or not _window_ok(ref, entry, exit_pct, ('Brake', 'Throttle')):
            return result
        if np.any(np.diff(ref.time_s[(ref.pct >= entry) & (ref.pct <= exit_pct)]) <= 0):
            return result
        reference_events = corner_events(ref, entry, apex, exit_pct, pad_m=0)
        for raw_lap, events in ((raw, observed), (ref, reference_events)):
            for event, channel, threshold, rising in (
                    ('brake_start', 'Brake', .08, True), ('brake_release', 'Brake', .05, False),
                    ('throttle_first', 'Throttle', .20, True), ('full_throttle', 'Throttle', .95, True)):
                value = events.get(event)
                if value and value['source'] != 'CLIPPED' and not sustained_event(raw_lap, value, channel, threshold, rising):
                    events[event] = None
        pairs, different = [], []
        for event, name in EVENTS.items():
            current, target = observed.get(event), reference_events.get(event)
            if not current or not target or 'CLIPPED' in (current['source'], target['source']):
                continue
            difference = (current['pct']-target['pct'])*length
            pairs.append(event)
            uncertainty = (current.get('uncertainty_m') or 0)+(target.get('uncertainty_m') or 0)
            if abs(difference) >= max(5, uncertainty):
                different.append((min(current['pct'], target['pct']), name, difference))
        different.sort()
        if pairs:
            if different:
                _, name, difference = different[0]
                result['divergence'] = card(label+' · '+name+'부터 차이 관측', f'{abs(difference):.1f}',
                    'm 늦음' if difference > 0 else 'm 빠름', f'비교 가능한 {len(pairs)}개 조작 중 첫 차이 · 손실 원인 확정 아님',
                    [[name, f'{delta:+.1f}m'] for _, name, delta in different[1:3]])
            else:
                result['divergence'] = card(label+' · 비교한 조작은 비슷함', '0', '개 큰 차이',
                    f'관측 가능한 {len(pairs)}개 지점 · 최소 5m 및 샘플 해상도 기준')
        phases = phase_deltas(raw, ref, reference_events, entry, exit_pct)
        if phases:
            values = phases['deltas']
            result['phases'] = card(label+' · 진입 / 회전 / 탈출', f'{sum(values):+.3f}', 's',
                ' / '.join(f'{name} {value:+.3f}' for name, value in zip(('진입', '회전', '탈출'), values))+'s · 기준 조작 경계',
                [[name, f'{value:+.3f}s'] for name, value in zip(('진입', '회전', '탈출'), values)],
                'warn' if sum(values) > 0 else 'good')
        return result


def impact_card(view):
    """Read the existing DamageWatch measurements, excluding its estimated time loss."""
    damage = view.get('damage_observation') or {}
    result = damage.get('measured') or {}
    lap = number((view.get('snapshot') or {}).get('lap_number'))
    post = number(result.get('lap'))
    baseline = number(result.get('baseline_laps'))
    if post is None or lap is None or baseline is None or baseline < 2 or not 0 <= lap-post <= 2:
        return waiting('충돌 전후 관측 변화', damage.get('reason') or '접촉·손상 신호 전 2랩과 이후 깨끗한 1랩이 필요합니다.')
    speed, slower, steer = (number(result.get(key)) for key in ('speed_delta_kph', 'time_delta_s', 'steer_delta_deg'))
    if speed is None or slower is None:
        return waiting('충돌 전후 관측 변화', '비교 수치가 확인되지 않았습니다.')
    return card('접촉·손상 신호 뒤 관측 변화', f'{speed:+.1f}', 'km/h 최고속 차이',
        f"랩타임 {slower:+.2f}s / 조향 {f'{steer:+.1f}°' if steer is not None else '관측 부족'} · 전 {int(baseline)}랩 / 후 {int(post)}랩 · 원인 미확정",
        [['랩타임 차이', f'{slower:+.2f}s'],
         ['직선 조향 차이', '관측 부족' if steer is None else f'{steer:+.1f}°']],
        'warn' if speed < -2 or slower > .7 else 'neutral')
