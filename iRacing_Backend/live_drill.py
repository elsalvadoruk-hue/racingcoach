"""Corner drill with iRacing's Active Reset: one corner of today's practice task, driven again and again.

The driver presses Active Reset's own keys (save a start point, then run back to it); this program never
sends anything to the sim (SDK read-only). It only reads the samples:
- a run back to the start point shows as the car jumping backwards along the lap between two samples;
- every attempt from the start point to the end point is measured like a corner of the live coach
  (time, where braking starts, lowest speed, where full throttle comes back);
- beeps: three counting down to the reference braking point (the last one is the point itself), one at
  the reference full-throttle point (like Bloops' throttle cue: one sound at the moment), two at the end
  point (the driver's cue to press "run"), one where the plan suggests saving while nothing is saved yet;
- the drill is done when the asked input reaches its goal in most of the last few attempts and those
  attempts repeat closely (thresholds below are first values, not yet checked on track).
"""
from __future__ import annotations

import math
import statistics
from typing import Any, Optional

BRAKE_ON = 0.08
FULL_THROTTLE = 0.95
COAST = 0.05
BEEP_LEAD_S = (1.0, 0.5, 0.0)          # three beeps; the last one is the braking point itself
BEEP_LATENCY_S = 0.08                  # sample, socket and sound latency taken off the beep times
JUMP_M = 40.0                          # a backwards jump longer than this between two samples is a reset
RECENT = 5                             # attempts looked at for the result
NEEDED_OK = 4                          # of those, how many must reach the input goal
BRAKE_SPREAD_M = 6.0                   # braking start of those attempts within this many metres
TIME_SPREAD_S = 0.20                   # and their times within this
MAX_GAP_S = 0.5
# Warm-up (the driver's rule, 2026-10-17): the tyres are warm after about two minutes. A lap under 80 s
# takes two crossings of the line counting the lap out of the pits; a longer lap takes the first one.
SHORT_LAP_S = 80.0
VOICE_KINDS = ('warmup', 'warmup_one', 'warmup_last', 'ready', 'done')   # said by the coach (radio_audio.CALLS 'drill_*')


def warmup_crossings(lap_time_s: Optional[float]) -> int:
    lap = _num(lap_time_s)
    return 1 if lap is not None and lap >= SHORT_LAP_S else 2

# Root cause -> what is measured live and which way it should move (like practice.CAUSE_TARGETS).
LIVE_TARGETS = {
    'THROTTLE_RETURN_LATE': ('full_throttle_pct', 'm', -1, 3.0, '풀스로틀 지점'),
    'BRAKE_RELEASE_LATE': ('min_speed_kph', 'km/h', 1, 1.5, '최저 속도'),
    'BRAKE_POINT_EARLY': ('brake_start_pct', 'm', 1, 3.0, '제동 시작 지점'),
    'EXCESS_COASTING': ('coast_time_s', '초', -1, 0.04, '코스팅 시간'),
}


def _num(value: Any) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else None


class CornerDrill:
    def __init__(self):
        self.plan: Optional[dict[str, Any]] = None
        self.reset()

    def reset(self) -> None:
        self.attempts: list[dict[str, Any]] = []
        self.current: Optional[dict[str, Any]] = None
        self.prev: Optional[dict[str, Any]] = None
        self.saved_pct: Optional[float] = None          # where the driver's start point turned out to be
        self.events: list[dict[str, Any]] = []
        self.serial = 0
        self.done = False
        self.baseline_s: Optional[float] = None
        # Warm-up: crossings of the line, the lap out of the pits included.
        self.phase = 'WARMUP'
        self.warm_laps = 0

    # -- control -------------------------------------------------------------------------------
    def start(self, plan: dict[str, Any], warmup: bool = True) -> dict[str, Any]:
        self.plan = dict(plan)
        self.reset()
        if warmup:
            self._emit('warmup' if self._needed() > 1 else 'warmup_one')
        else:
            self._ready()
        return self.view()

    def skip_warmup(self) -> dict[str, Any]:
        """The tyres are already warm (the driver was out before starting the drill)."""
        if self.plan is not None and self.phase == 'WARMUP':
            self._ready()
        return self.view()

    def _needed(self) -> int:
        return int((self.plan or {}).get('warmup_crossings') or 2)

    def _ready(self) -> None:
        self.phase = 'READY'
        self._emit('ready')

    def stop(self) -> dict[str, Any]:
        self.plan = None
        self.reset()
        return self.view()

    # -- samples -------------------------------------------------------------------------------
    def _emit(self, kind: str, **values: Any) -> None:
        self.serial += 1
        self.events.append({'serial': self.serial, 'kind': kind, **values})
        self.events = self.events[-20:]

    def _ahead(self, pct: float, target: float) -> float:
        """Lap fraction from pct forward to target (0..1)."""
        return (target - pct) % 1.0

    def ingest(self, sample: dict[str, Any], track_length_m: Optional[float]) -> None:
        plan = self.plan
        if plan is None or self.done:
            return
        pct, speed, time = _num(sample.get('distance_pct')), _num(sample.get('speed_mps')), _num(sample.get('session_time'))
        if pct is None or time is None or sample.get('is_replay') or sample.get('on_pit_road'):
            self.prev = None
            return
        length = _num(track_length_m) or _num(plan.get('track_length_m'))
        if not length:
            return
        prev, self.prev = self.prev, {'pct': pct, 'time': time, 'speed': speed}
        if self.phase == 'WARMUP':
            if prev is not None and prev['pct'] > 0.9 and pct < 0.1 and 0 <= time - prev['time'] <= MAX_GAP_S:
                self.warm_laps += 1
                if self.warm_laps >= self._needed():
                    self._ready()
                elif self.warm_laps == self._needed() - 1:
                    self._emit('warmup_last')
            return                                          # cold tyres: nothing is measured yet
        if prev is not None:
            moved = ((pct - prev['pct'] + 0.5) % 1.0 - 0.5) * length
            if moved < -JUMP_M and 0 <= time - prev['time'] <= MAX_GAP_S:
                # Active Reset ran: the car is back at the saved start point. A new attempt begins here.
                self.saved_pct = pct
                self.phase = 'RUNNING'
                self._begin(pct, time, from_reset=True)
                return
            if self.current is not None and (time - prev['time'] > MAX_GAP_S or time < prev['time']):
                self.current = None                              # a gap: this attempt cannot be timed
        if self.current is None:
            # Driving into the section the ordinary way (before anything is saved, or after a lap).
            if prev is not None and self._crossed(prev['pct'], pct, plan['start_pct']):
                self._begin(plan['start_pct'], self._cross_time(prev, pct, time, plan['start_pct']))
                if self.saved_pct is None:
                    self._emit('save_hint')                     # here would be a good place to save the start point
            return
        self._record(sample, pct, speed, time, length)
        if prev is not None and self._crossed(prev['pct'], pct, plan['end_pct']):
            self._finish(self._cross_time(prev, pct, time, plan['end_pct']), length)

    @staticmethod
    def _crossed(before: float, after: float, mark: float) -> bool:
        step = (after - before) % 1.0
        return 0 < step < 0.5 and (mark - before) % 1.0 <= step

    @staticmethod
    def _cross_time(prev: dict[str, Any], pct: float, time: float, mark: float) -> float:
        step = (pct - prev['pct']) % 1.0
        share = ((mark - prev['pct']) % 1.0) / step if step else 1.0
        return prev['time'] + (time - prev['time']) * share

    def _begin(self, pct: float, time: float, from_reset: bool = False) -> None:
        self.current = {'start_time': time, 'start_pct': pct, 'from_reset': from_reset, 'brake_pct': None, 'release_pct': None, 'min_kph': None,
                        'full_pct': None, 'coast_s': 0.0, 'last_time': time, 'beeps': set(), 'braking': False}

    def _record(self, sample: dict[str, Any], pct: float, speed: Optional[float], time: float, length: float) -> None:
        plan, attempt = self.plan, self.current
        brake, throttle = _num(sample.get('brake')), _num(sample.get('throttle'))
        dt = max(0.0, time - attempt['last_time'])
        attempt['last_time'] = time
        before_apex = self._ahead(plan['start_pct'], pct) <= self._ahead(plan['start_pct'], plan['apex_pct'])
        if brake is not None and brake > BRAKE_ON:
            if attempt['brake_pct'] is None and before_apex:
                attempt['brake_pct'] = pct
            attempt['braking'] = True
        elif attempt['braking'] and brake is not None and brake <= 0.05 and attempt['release_pct'] is None:
            attempt['release_pct'] = pct
        in_corner = self._ahead(plan['entry_pct'], pct) <= self._ahead(plan['entry_pct'], plan['exit_pct'])
        if speed is not None and in_corner:
            kph = speed * 3.6
            attempt['min_kph'] = kph if attempt['min_kph'] is None else min(attempt['min_kph'], kph)
        if (throttle is not None and throttle >= FULL_THROTTLE and attempt['full_pct'] is None and not before_apex):
            attempt['full_pct'] = pct
        if (in_corner and before_apex and throttle is not None and brake is not None and throttle < COAST and brake < COAST
                and speed is not None and speed * 3.6 > 50):
            attempt['coast_s'] += dt
        # Beeps counting down to the reference braking point.
        target = _num(plan.get('brake_pct'))
        if target is not None and speed and speed > 5 and len(attempt['beeps']) < len(BEEP_LEAD_S):
            ahead = self._ahead(pct, target)
            if ahead < 0.5:
                seconds = ahead * length / speed - BEEP_LATENCY_S
                for index, lead in enumerate(BEEP_LEAD_S):
                    if seconds <= lead and index not in attempt['beeps']:
                        attempt['beeps'].add(index)
                        self._emit('brake_beep', step=index + 1, last=index == len(BEEP_LEAD_S) - 1)
        # One beep where the reference lap is back at full throttle.
        throttle_at = _num(plan.get('throttle_pct'))
        if throttle_at is not None and speed and speed > 5 and not attempt.get('throttle_beep') and not before_apex:
            ahead = self._ahead(pct, throttle_at)
            if ahead < 0.5 and ahead * length / speed <= BEEP_LATENCY_S:
                attempt['throttle_beep'] = True
                self._emit('throttle_beep')

    def _finish(self, end_time: float, length: float) -> None:
        plan, attempt = self.plan, self.current
        self.current = None
        seconds = end_time - attempt['start_time']
        if not 0 < seconds < 120:
            return
        values = {'brake_start_pct': attempt['brake_pct'], 'min_speed_kph': attempt['min_kph'],
                  'full_throttle_pct': attempt['full_pct'], 'coast_time_s': attempt['coast_s']}
        result = {'n': len(self.attempts) + 1, 'time_s': round(seconds, 3),
                  'brake_start_m': self._metres(attempt['brake_pct'], length),
                  'full_throttle_m': self._metres(attempt['full_pct'], length),
                  'min_speed_kph': round(attempt['min_kph'], 1) if attempt['min_kph'] is not None else None,
                  'coast_time_s': round(attempt['coast_s'], 2), 'input_change': None, 'input_ok': None,
                  # Only runs from the same saved point are compared in time (the first one, driven in, is longer).
                  'origin_m': round(attempt['start_pct'] * length, 1) if attempt['from_reset'] else None}
        target = LIVE_TARGETS.get(plan.get('cause') or '')
        base = _num(plan.get('base_value'))
        if target and base is not None and values.get(target[0]) is not None:
            metric, unit, sign, goal, _label = target
            change = values[metric] - base
            if unit == 'm':
                change = ((change + 0.5) % 1.0 - 0.5) * length
            result['input_change'] = round(change, 2)
            result['input_ok'] = change * sign >= goal
        self.attempts.append(result)
        self._emit('end_beep', n=result['n'])
        comparable = self._comparable(length)
        if self.baseline_s is None and len(comparable) == 3:
            self.baseline_s = round(statistics.median(a['time_s'] for a in comparable), 3)
        if self._settled(length):
            self.done = True
            self._emit('done')

    def _metres(self, pct: Optional[float], length: float) -> Optional[float]:
        """A point as metres after the section start (comparable between attempts)."""
        if pct is None:
            return None
        return round(self._ahead(self.plan['start_pct'], pct) * length, 1)

    def _comparable(self, length: float) -> list[dict[str, Any]]:
        """Attempts that ran from the start point saved now (within 5 m: a new save starts a new series)."""
        if self.saved_pct is None:
            return []
        here = self.saved_pct * length
        return [a for a in self.attempts if a['origin_m'] is not None and abs(a['origin_m'] - here) <= 5.0]

    def _settled(self, length: float) -> bool:
        recent = self._comparable(length)[-RECENT:]
        if len(recent) < RECENT:
            return False
        measured = [a for a in recent if a['input_ok'] is not None]
        good = [a for a in recent if a['input_ok']] if measured else recent
        if len(good) < NEEDED_OK:
            return False
        brakes = [a['brake_start_m'] for a in good if a['brake_start_m'] is not None]
        times = [a['time_s'] for a in good]
        return (not brakes or max(brakes) - min(brakes) <= BRAKE_SPREAD_M) and max(times) - min(times) <= TIME_SPREAD_S

    # -- view ----------------------------------------------------------------------------------
    def view(self, after: int = 0) -> dict[str, Any]:
        if self.plan is None:
            return {'active': False}
        length = _num(self.plan.get('track_length_m')) or 1.0
        recent = self._comparable(length)[-RECENT:]
        target = LIVE_TARGETS.get(self.plan.get('cause') or '')
        return {'active': True, 'done': self.done, 'plan': self.plan, 'saved': self.saved_pct is not None,
                'phase': 'DONE' if self.done else self.phase, 'warm_laps': self.warm_laps, 'warmup_laps': self._needed(),
                'attempts': self.attempts[-30:], 'baseline_s': self.baseline_s,
                'recent_ok': sum(1 for a in recent if a['input_ok']), 'recent_count': len(recent),
                'needed': {'recent': RECENT, 'ok': NEEDED_OK, 'brake_spread_m': BRAKE_SPREAD_M, 'time_spread_s': TIME_SPREAD_S},
                'target': {'label': target[4], 'unit': target[1], 'sign': target[2], 'goal': target[3]} if target else None,
                'events': [event for event in self.events if event['serial'] > after], 'serial': self.serial}


def drill_plan(corners: list[dict[str, Any]], label: str, cause: Optional[str], base_metrics: dict[str, Any],
               track_length_m: float) -> dict[str, Any]:
    """Where to save the start point and where the attempt ends, for one corner of the analysis.

    Start: about 250 m before the reference braking point, but after the previous corner is left behind
    (its full-throttle point + 30 m). End: 100 m past this corner's full-throttle point, before the next
    corner's braking. Both are suggestions on straights; the driver saves wherever is safe for him."""
    ordered = sorted((c for c in corners if _num(c.get('apex_pct')) is not None), key=lambda c: c['apex_pct'])
    index = next((i for i, c in enumerate(ordered) if c.get('label') == label), None)
    if index is None or not track_length_m:
        raise ValueError('이 코너를 분석 결과에서 찾지 못했습니다.')
    corner, length = ordered[index], float(track_length_m)
    previous, following = ordered[index - 1], ordered[(index + 1) % len(ordered)]

    def point(item: dict[str, Any], *keys: str) -> Optional[float]:
        for side in ('reference', 'target'):
            for key in keys:
                value = _num((item.get(side) or {}).get(key))
                if value is not None:
                    return value
        return None

    metres = 1.0 / length
    brake = point(corner, 'brake_start_pct') or corner['entry_pct']
    leave_previous = (point(previous, 'full_throttle_pct') or previous['exit_pct']) + 30 * metres
    start = (brake - 250 * metres) % 1.0
    if (brake - leave_previous) % 1.0 < (brake - start) % 1.0:
        start = leave_previous % 1.0                                   # a shorter straight: start once out of the previous corner
    out = (point(corner, 'full_throttle_pct') or corner['exit_pct']) + 100 * metres
    next_brake = (point(following, 'brake_start_pct') or following['entry_pct']) - 30 * metres
    end = out % 1.0
    if following is not corner and (next_brake - corner['exit_pct']) % 1.0 < (out - corner['exit_pct']) % 1.0:
        end = max(next_brake, corner['exit_pct'] + 30 * metres) % 1.0
    target = LIVE_TARGETS.get(cause or '')
    return {'label': label, 'cause': cause, 'track_length_m': round(length, 1),
            'start_pct': round(start, 6), 'end_pct': round(end, 6), 'brake_pct': round(brake % 1.0, 6),
            'throttle_pct': round(point(corner, 'full_throttle_pct') % 1.0, 6) if point(corner, 'full_throttle_pct') is not None else None,
            'entry_pct': corner['entry_pct'], 'apex_pct': corner['apex_pct'], 'exit_pct': corner['exit_pct'],
            'base_value': _num((base_metrics or {}).get(target[0])) if target else None,
            'section_m': round(((end - start) % 1.0) * length)}
