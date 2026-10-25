"""Task-free, bounded observations of the driver's actual pedal inputs.

No corner geometry, saved lap, SDK write, or judgement of optimal technique.
"""
from collections import deque
import math


def number(value):
    return float(value) if type(value) in (int, float) and math.isfinite(value) else None


class AutomaticDrivingCoach:
    def __init__(self):
        self.reset()

    def reset(self):
        self.run = None
        self.previous = None
        self.intervals = deque(maxlen=600)
        self.states = {'brake': None, 'throttle': None}
        self.candidates = {}
        self.braked = False
        self.release_at = None
        self.connection = None
        self.cached_at = None
        self.cached = None

    def transition(self, key, value, low, high, at):
        state = True if value >= high else False if value <= low else None
        if state is None:
            self.candidates.pop(key, None)
            return
        candidate, since = self.candidates.get(key, (None, at))
        if candidate != state:
            since = at
        self.candidates[key] = state, since
        if at-since < .15-1e-8 or self.states[key] == state:
            return
        old, self.states[key] = self.states[key], state
        if key == 'brake':
            if state:
                self.braked, self.release_at = True, None
            elif old is True and self.braked and self.states['throttle'] is False:
                self.release_at = since
        elif state and old is False and self.release_at is not None:
            elapsed = since-self.release_at
            if 0 <= elapsed <= 10:
                self.connection = (at, elapsed)
            self.release_at = None

    def build(self, view):
        sample = view.get('snapshot') or {}
        at, brake, throttle, speed = (number(sample.get(key)) for key in
                                      ('session_time', 'brake', 'throttle', 'speed_mps'))
        run = (view.get('run_id'), tuple((view.get('identity') or {}).get(key)
               for key in ('game', 'car_id', 'car_path', 'track_id', 'track_config')))
        previous = self.previous
        dt = at-previous['at'] if previous and at is not None else None
        if run != self.run or dt is not None and (dt < 0 or dt > .5):
            self.reset()
            self.run = run
            previous = None
        item = dict(title='자동 주행 코칭 · 입력 대기', metric='주행 대기', unit='',
                    detail='과제 없이 작동합니다. 트랙에서 주행하면 실제 조작을 관측합니다.',
                    stats=[], tone='neutral', available=False)
        formation = (view.get('session') or {}).get('kind') == 'RACE' and sample.get('session_state') in (1, 2, 3)
        valid = (view.get('connected') is True and sample.get('on_track') is True
                 and sample.get('is_replay') is False and sample.get('on_pit_road') is False
                 and at is not None and brake is not None and throttle is not None and speed is not None
                 and 0 <= brake <= 1 and 0 <= throttle <= 1 and speed >= 5
                 and not int(sample.get('session_flags') or 0) & 0xC108
                 and sample.get('car_left_right') not in (2, 3, 4, 5, 6) and not formation)
        if previous and sample.get('incidents') != previous.get('incidents'):
            valid = False
        if not valid:
            self.reset()
            self.run = run
            if formation:
                item.update(title='포메이션 · 자동 관측 대기', metric='그린 후 관측',
                            detail='출발 준비 중에는 조작 평가를 보류합니다.', available=True)
            else:
                item['detail'] = '트랙 주행에서 자동 관측합니다. 피트·리플레이·저속·옐로·근접 구간은 비교에서 제외합니다.'
            return item
        if previous and dt == 0:
            return self.cached or item
        if previous:
            old_pct, pct = number(previous.get('distance_pct')), number(sample.get('distance_pct'))
            old_lap, lap = number(previous.get('lap')), number(sample.get('lap_number'))
            if old_pct is not None and pct is not None:
                rollover = old_lap is not None and lap == old_lap+1 and old_pct > .9 and pct < .1
                step = pct-old_pct+(1 if rollover else 0)
                if not 0 <= step < .05 or old_lap != lap and not rollover:
                    self.reset()
                    self.run = run
                    previous = None
        if previous:
            # Count an interval only when both endpoints agree. Transition
            # intervals remain unclassified rather than manufacturing precision.
            coast = brake <= .05 and throttle <= .20 and previous['brake'] <= .05 and previous['throttle'] <= .20
            overlap = brake >= .08 and throttle >= .20 and previous['brake'] >= .08 and previous['throttle'] >= .20
            self.intervals.append((previous['at'], at, coast, overlap))
        self.previous = dict(at=at, brake=brake, throttle=throttle, incidents=sample.get('incidents'),
                             distance_pct=sample.get('distance_pct'), lap=sample.get('lap_number'))
        self.transition('brake', brake, .05, .08, at)
        self.transition('throttle', throttle, .20, .25, at)
        if self.cached_at is not None and 0 <= at-self.cached_at < .25:
            return self.cached
        while self.intervals and self.intervals[0][1] <= at-10:
            self.intervals.popleft()
        duration = coast_s = overlap_s = 0.
        for begin, end, coast, overlap in self.intervals:
            seconds = end-max(begin, at-10)
            duration += seconds
            coast_s += seconds if coast else 0
            overlap_s += seconds if overlap else 0
        item.update(available=True, title='자동 주행 코칭 · 관측 시작', metric='입력 수신',
                    detail='과제·비교 기록 없이 조작 연결을 관측합니다.')
        if duration >= 1:
            item.update(title=f'최근 {duration:.0f}초 · 무입력 관측', metric=f'{coast_s:.2f}', unit='s',
                        detail=f'동시 입력 {overlap_s:.2f}s · 무입력이 곧 시간 손실이라는 뜻은 아닙니다.',
                        stats=[['관측 시간', f'{duration:.1f}s'], ['동시 입력', f'{overlap_s:.2f}s']])
            if self.connection and 0 <= at-self.connection[0] <= 10:
                item.update(title='직전 제동 해제 → 가속 연결', metric=f'{self.connection[1]:.2f}',
                            detail=f'최근 무입력 {coast_s:.2f}s · 동시 입력 {overlap_s:.2f}s · 관측값',
                            stats=[['최근 무입력', f'{coast_s:.2f}s'], ['동시 입력', f'{overlap_s:.2f}s']])
        self.cached_at, self.cached = at, item
        return item
