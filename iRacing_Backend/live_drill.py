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
import time as wall_time
import uuid
import numpy as np
from typing import Any, Optional
from corner_measurements import CornerMeasurements
from drill_history import DrillHistory
from practice_metrics import TARGETS, KEPT_METRICS, evaluate

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
LIVE_TARGETS = TARGETS


def measured_inputs(samples, plan, length):
    """Resample observed 30 Hz inputs to the same distance grid/definitions as IBT analysis.

    No extrapolation, filling missing channels, or substitute definition for ABS/steering.
    The stream has already been rejected for gaps, traffic, incidents and replay samples.
    """
    if len(samples) < 3:
        return {}
    start = plan['start_pct']
    observed = np.array([(s['distance_pct'] - start) % 1 for s in samples])
    if np.any(np.diff(observed) <= 0):
        return {}
    resolution = _num((plan.get('base_metrics') or {}).get('distance_resolution_m'))
    count = round(length / resolution) + 1 if resolution else max(1400, math.ceil(length / 2) + 1)
    if _num(plan.get('grid_points')):
        count = round(plan['grid_points'])
    count = max(500, min(25000, count))
    pct = np.linspace(0, 1, count)
    pct = pct[np.argsort((pct - start) % 1)]
    positions = (pct - start) % 1
    covered = (positions >= observed[0]) & (positions <= observed[-1])
    grid = {'distance_pct': pct, '_coverage': covered}
    mapping = {'Brake': 'brake', 'Throttle': 'throttle', 'SteeringWheelAngle': 'steering_rad',
               'YawRate': 'yaw_rate', 'LatAccel': 'lat_accel', 'BrakeABSactive': 'abs_active',
               'speed_kph': 'speed_mps', 'time_s': 'session_time'}
    for channel, key in mapping.items():
        values = np.array([_num(s.get(key)) if _num(s.get(key)) is not None else np.nan for s in samples])
        if channel == 'speed_kph':
            values *= 3.6
        if channel == 'BrakeABSactive':
            # Match IBT's binary sample-and-hold; a linear ramp invents ABS states.
            held = np.searchsorted(observed, positions, side='right')-1
            grid[channel] = values[np.clip(held, 0, len(values)-1)].copy()
            grid[channel][~covered] = np.nan
        else:
            grid[channel] = np.interp(positions, observed, values, left=np.nan, right=np.nan)
    indices = [int(np.argmin(abs(positions - ((plan[k] - start) % 1)))) for k in ('entry_pct', 'apex_pct', 'exit_pct')]
    if not indices[0] < indices[1] < indices[2] or not np.all(covered[indices[0]:indices[2] + 1]):
        return {}
    engine = CornerMeasurements()
    engine.track_length_m = length
    values = engine._compute_corner_metrics(grid, *indices)
    # ABS must be observed throughout braking, rather than averaging a partially missing channel.
    entry = slice(indices[0], indices[1] + 1)
    braking = grid['Brake'][entry] > BRAKE_ON
    if not np.all(np.isfinite(grid['BrakeABSactive'][entry][braking])):
        values['abs_duty_pct'] = None
    return {key: values.get(key) for key in KEPT_METRICS}


def _num(value: Any) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else None


class CornerDrill:
    def __init__(self):
        self.history = DrillHistory()
        self.record_id = None
        self.ended = False
        self.plan: Optional[dict[str, Any]] = None
        self.reset()

    def reset(self) -> None:
        self.attempts: list[dict[str, Any]] = []
        self.current: Optional[dict[str, Any]] = None
        self.prev: Optional[dict[str, Any]] = None
        self.saved_pct: Optional[float] = None          # where the driver's start point turned out to be
        self.saved_conditions = None
        self.series = 0
        self.events: list[dict[str, Any]] = []
        self.serial = getattr(self, 'serial', 0)
        self.done = False
        self.baseline_s: Optional[float] = None
        # Warm-up: crossings of the line, the lap out of the pits included.
        self.phase = 'WARMUP'
        self.warm_laps = 0

    # -- control -------------------------------------------------------------------------------
    def start(self, plan: dict[str, Any], warmup: bool = True) -> dict[str, Any]:
        self.stop('RESTARTED')
        self.plan = dict(plan)
        self.reset()
        self.record_id, self.ended = uuid.uuid4().hex, False
        self.history.record({'kind': 'start', 'id': self.record_id, 'at': wall_time.time(), 'plan': self.plan})
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

    def _end(self, outcome):
        if self.record_id and not self.ended:
            self.history.record({'kind': 'end', 'id': self.record_id, 'at': wall_time.time(), 'outcome': outcome})
            self.ended = True

    def stop(self, reason='STOPPED') -> dict[str, Any]:
        self._end(reason)
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
        if plan is None or (self.done and not plan.get('continue_after_goal')):
            return
        pct, speed, time = _num(sample.get('distance_pct')), _num(sample.get('speed_mps')), _num(sample.get('session_time'))
        if (pct is None or time is None or sample.get('on_track') is not True or sample.get('is_replay') is not False
                or sample.get('on_pit_road') is not False or sample.get('car_left_right') in (2, 3, 4, 5, 6)
                or int(sample.get('session_flags') or 0) & 0xC108):
            self.prev = self.current = None
            return
        length = _num(track_length_m) or _num(plan.get('track_length_m'))
        if not length:
            return
        prev, self.prev = self.prev, {'pct': pct, 'time': time, 'speed': speed, 'incidents': sample.get('incidents')}
        if prev and prev.get('incidents') != sample.get('incidents'):
            self.current = None
            return
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
                conditions = {k: sample.get(k) for k in ('speed_mps', 'fuel_l', 'tire_compound', 'track_wetness',
                                                         'brake_bias', 'abs_setting', 'tc_setting')}
                limits = {'speed_mps': 2.0, 'fuel_l': 1.0}
                changed = self.saved_conditions is None or self.saved_pct is None or abs(pct - self.saved_pct) * length > 5
                if not changed:
                    for key, value in conditions.items():
                        previous = self.saved_conditions.get(key)
                        if value != previous and (value is None or previous is None or abs(value - previous) > limits.get(key, 0)):
                            changed = True
                if changed:
                    self.series += 1
                    self.saved_conditions = conditions
                self.saved_pct = pct
                comparable = self._comparable(length)
                self.baseline_s = round(statistics.median(a['time_s'] for a in comparable[:3]), 3) if len(comparable) >= 3 else None
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
                        'full_pct': None, 'coast_s': 0.0, 'last_time': time, 'beeps': set(), 'braking': False, 'samples': []}

    def _record(self, sample: dict[str, Any], pct: float, speed: Optional[float], time: float, length: float) -> None:
        plan, attempt = self.plan, self.current
        attempt['samples'].append(dict(sample))
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
        values = measured_inputs(attempt['samples'], plan, length)
        result = {'n': len(self.attempts) + 1, 'time_s': round(seconds, 3),
                  'series': self.series,
                  'brake_start_m': self._metres(attempt['brake_pct'], length),
                  'full_throttle_m': self._metres(attempt['full_pct'], length),
                  'min_speed_kph': round(attempt['min_kph'], 1) if attempt['min_kph'] is not None else None,
                  'coast_time_s': round(attempt['coast_s'], 2), 'input_change': None, 'input_ok': None,
                  # Only runs from the same saved point are compared in time (the first one, driven in, is longer).
                  'origin_m': round(attempt['start_pct'] * length, 1) if attempt['from_reset'] else None}
        result.update(evaluate(plan.get('cause'), plan.get('base_metrics') or {}, values, length), metrics=values,
                      recorded_at=wall_time.time(), session_end_s=end_time)
        result['brake_release_m'] = self._metres(values.get('brake_release_pct'), length)
        result['conditions'] = dict(self.saved_conditions or {}) if attempt['from_reset'] else {}
        self.attempts.append(result)
        self.history.record({'kind': 'attempt', 'id': self.record_id, 'attempt': result})
        self._emit('end_beep', n=result['n'])
        comparable = self._comparable(length)
        if self.baseline_s is None and len(comparable) == 3:
            self.baseline_s = round(statistics.median(a['time_s'] for a in comparable), 3)
        if not self.done and self._settled(length):
            self.done = True
            self._emit('done')
            self._end('INPUT_ACHIEVED')

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
        return [a for a in self.attempts if a['origin_m'] is not None and abs(a['origin_m'] - here) <= 5.0
                and a.get('series', self.series) == self.series]

    def _settled(self, length: float) -> bool:
        recent = self._comparable(length)[-RECENT:]
        if len(recent) < RECENT:
            return False
        good = [a for a in recent if a.get('goal_ok') is True]
        if len(good) < NEEDED_OK:
            return False
        brakes = [a['brake_start_m'] for a in good if a['brake_start_m'] is not None]
        times = [a['time_s'] for a in good]
        return len(brakes) == len(good) and max(brakes) - min(brakes) <= BRAKE_SPREAD_M and max(times) - min(times) <= TIME_SPREAD_S

    # -- view ----------------------------------------------------------------------------------
    def view(self, after: int = 0) -> dict[str, Any]:
        if self.plan is None:
            return {'active': False}
        length = _num(self.plan.get('track_length_m')) or 1.0
        recent = self._comparable(length)[-RECENT:]
        target = LIVE_TARGETS.get(self.plan.get('cause') or '')
        return {'active': True, 'done': self.done, 'plan': self.plan, 'saved': self.saved_pct is not None,
                'phase': 'DONE' if self.done else self.phase, 'warm_laps': self.warm_laps, 'warmup_laps': self._needed(),
                'attempts': [{**a, 'comparable': a in self._comparable(length)} for a in self.attempts[-30:]], 'baseline_s': self.baseline_s,
                'recent_ok': sum(1 for a in recent if a.get('goal_ok') is True), 'recent_count': len(recent),
                'result': 'INPUT_ACHIEVED' if self.done else 'MEASURING', 'pace_verified': False,
                'history_error': self.history.error,
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
            'base_metrics': dict(base_metrics or {}),
            'target': dict(zip(('metric', 'unit', 'sign', 'goal', 'label'), target)) if target else None,
            'section_m': round(((end - start) % 1.0) * length)}
