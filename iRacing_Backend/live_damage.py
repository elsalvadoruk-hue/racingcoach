"""After a contact: did the car change? Measured from the laps before and after, nothing else.

The sim only says that a repair is available, and how long it takes once the car
stands in its pit stall. What can be measured while driving is whether the laps
after the contact differ from the laps before it: lap time, top speed, and where
the steering wheel sits on a straight (a bent car needs the wheel held off-centre).
Nothing here names a damaged part or a cause.
"""
import math
import statistics
from collections import deque


def finite(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


class DamageWatch:
    """Compares the first clean lap after a contact with the clean laps of the same stint before it."""
    # Unverified first values, from one endurance race: a repaired 4x contact moved the wheel by 0.6 degrees
    # on the straight while lap time and top speed stayed inside their normal lap-to-lap spread.
    BASELINE_LAPS, STRAIGHT_SAMPLES = 2, 90
    SLOWER_S, SLOWER_BEYOND_S = .7, .2        # against the median, and beyond the slowest lap before
    VMAX_KPH, VMAX_BEYOND_KPH = 2., 1.
    STEER_DEG, STEER_BEYOND_DEG = .4, .2
    # What lost top speed costs a lap when the lap time itself does not show it (a tow, a better lap):
    # the flat-out time of the lap times the share of speed lost, halved because the deficit only
    # builds up along a straight. A rough estimate, not checked against repaired cars yet.
    VMAX_TIME_SHARE = .5

    def __init__(self):
        self.reset()

    def reset(self):
        self.history = deque(maxlen=5)   # clean laps of this stint: {'lap', 'time', 'vmax', 'steer'}
        self.current = None              # what the lap in progress has shown so far
        self.finished = None             # the lap that last ended: its number, top speed and straight-line steering
        self.contact_lap = None
        self.previous = None
        self.result = None
        self.observed = None  # HUD-only raw changes; audio thresholds and estimates stay separate.
        self._observed_last = None
        self._observed_after_lap = None

    def ingest(self, sample):
        self._observe_continuity(sample)
        lap, speed = sample.get('lap_number'), finite(sample.get('speed_mps'))
        if sample.get('on_track') is not True or sample.get('is_replay') is not False or type(lap) not in (int, float) or speed is None:
            self.previous = None
            self.observed = None
            return
        if sample.get('on_pit_road') is True:
            # A stop changes tyres, fuel and possibly the car: the stint before it is no reference any more.
            self.history.clear()
            self.observed = None
            self.current, self.contact_lap, self.previous = None, None, None
            return
        if self.current is None or self.current['lap'] != lap:
            if self.current is not None:
                steer = self.current['steer']
                self.finished = {'lap': self.current['lap'], 'vmax': self.current['vmax'] * 3.6, 'flat_s': self.current['flat_s'],
                                 'steer': math.degrees(statistics.median(steer)) if len(steer) >= self.STRAIGHT_SAMPLES else None}
            self.current = {'lap': lap, 'vmax': 0., 'steer': [], 'flat_s': 0., 'at': None}
        self.current['vmax'] = max(self.current['vmax'], speed)
        throttle, yaw, steering = finite(sample.get('throttle')), finite(sample.get('yaw_rate')), finite(sample.get('steering_rad'))
        at, before_at = finite(sample.get('session_time')), self.current['at']
        if throttle is not None and throttle >= .98 and at is not None and before_at is not None and 0 < at - before_at < .5:
            self.current['flat_s'] += at - before_at
        self.current['at'] = at
        # Flat out and going straight: this is where an off-centre wheel shows.
        if throttle is not None and throttle >= .98 and yaw is not None and abs(yaw) < .02 and steering is not None and speed > 40:
            self.current['steer'].append(steering)
        old = self.previous or {}
        incidents, before = finite(sample.get('incidents')), finite(old.get('incidents'))
        warnings, old_warnings = finite(sample.get('engine_warnings')), finite(old.get('engine_warnings'))
        hit = incidents is not None and before is not None and incidents - before >= 2
        signal = warnings is not None and old_warnings is not None and int(warnings) & 0x180 and not int(old_warnings) & 0x180
        detached = sample.get('lmu_parts_detached') is True and old.get('lmu_parts_detached') is False
        impact, old_at = finite(sample.get('lmu_last_impact_et')), finite(old.get('session_time'))
        native_hit = (impact is not None and old_at is not None and at is not None
                      and 0 < at - old_at <= .5
                      and old_at < impact <= at and impact != old.get('lmu_last_impact_et'))
        if hit or signal or detached or native_hit:
            self.contact_lap, self.result = lap, None
            self.observed = None
        self.previous = {'incidents': incidents, 'engine_warnings': warnings, 'session_time': at,
                         'lmu_last_impact_et': impact,
                         'lmu_parts_detached': sample.get('lmu_parts_detached')}

    def lap_done(self, seconds):
        """The lap that just ended was clean and took `seconds`. Returns the comparison once,
        for the first clean lap after a contact."""
        measured, self.finished = self.finished, None
        if not measured or not seconds:
            return None
        entry = {**measured, 'time': seconds}
        lap = entry['lap']
        if self.contact_lap is None or lap <= self.contact_lap:
            if self.contact_lap is None:
                self.history.append(entry)
            return None
        before = [item for item in self.history if item['lap'] < self.contact_lap]
        self.contact_lap = None
        self.history.clear()
        self.history.append(entry)       # the car as it is now is the reference from here on
        if len(before) < self.BASELINE_LAPS:
            return None
        times, speeds = [item['time'] for item in before], [item['vmax'] for item in before]
        steers = [item['steer'] for item in before if item['steer'] is not None]
        slower = seconds - statistics.median(times)
        vmax_loss = statistics.median(speeds) - entry['vmax']
        steer = abs(entry['steer'] - statistics.median(steers)) if steers and entry['steer'] is not None else None
        eligible = [item for item in before if self._observed_after_lap is None or item['lap'] > self._observed_after_lap]
        self.observed = None
        if len(eligible) >= self.BASELINE_LAPS and (self._observed_after_lap is None or lap > self._observed_after_lap):
            known_steers = [item['steer'] for item in eligible if item['steer'] is not None]
            self.observed = {'lap': lap, 'baseline_laps': len(eligible),
                             'time_delta_s': round(seconds-statistics.median(item['time'] for item in eligible), 3),
                             'speed_delta_kph': round(entry['vmax']-statistics.median(item['vmax'] for item in eligible), 3),
                             'steer_delta_deg': (round(entry['steer']-statistics.median(known_steers), 3)
                                                 if len(known_steers) >= self.BASELINE_LAPS and entry['steer'] is not None else None)}
        slower = slower if slower >= self.SLOWER_S and seconds > max(times) + self.SLOWER_BEYOND_S else 0.
        vmax_loss = vmax_loss if vmax_loss >= self.VMAX_KPH and entry['vmax'] < min(speeds) - self.VMAX_BEYOND_KPH else 0.
        # Seconds a lap the damage costs: what the lap time shows, or what the lost top speed must cost.
        flat = statistics.median(item.get('flat_s') or 0. for item in before)
        from_speed = flat * vmax_loss / statistics.median(speeds) * self.VMAX_TIME_SHARE if vmax_loss else 0.
        self.result = {
            'lap': lap, 'baseline_laps': len(before),
            'slower_s': slower, 'vmax_loss_kph': vmax_loss, 'loss_per_lap_s': max(slower, from_speed),
            'steer_deg': None if steer is None else steer if steer >= self.STEER_DEG and (
                entry['steer'] > max(steers) + self.STEER_BEYOND_DEG or entry['steer'] < min(steers) - self.STEER_BEYOND_DEG) else 0.,
        }
        return self.result

    def _observe_continuity(self, sample):
        """Stricter HUD support, independent of the existing audio calculation."""
        at, lap, pct = (finite(sample.get(key)) for key in ('session_time', 'lap_number', 'distance_pct'))
        previous = self._observed_last
        self._observed_last = (at, lap, pct)
        supported = (at is not None and lap is not None and pct is not None and 0 <= pct < 1
                     and sample.get('on_track') is True and sample.get('is_replay') is False
                     and sample.get('on_pit_road') is False and finite(sample.get('speed_mps')) is not None
                     and not int(sample.get('session_flags') or 0) & 0xC108
                     and sample.get('car_left_right') not in (2, 3, 4, 5, 6))
        if supported and previous and all(value is not None for value in previous):
            old_at, old_lap, old_pct = previous
            rollover = lap == old_lap+1 and old_pct > .9 and pct < .1
            supported = 0 < at-old_at <= .5 and 0 <= pct-old_pct+(1 if rollover else 0) < .05 and (lap == old_lap or rollover)
        else:
            supported = False
        if not supported:
            # Exclude the partial/dirty lap and everything earlier from future HUD baselines.
            if lap is not None:
                self._observed_after_lap = lap
            elif previous and previous[1] is not None:
                self._observed_after_lap = previous[1]
            self.observed = None

    def overlay_view(self, sample):
        """Read-only view. A signal is not proof of contact or a damaged part."""
        if sample.get('on_track') is not True or sample.get('is_replay') is not False or sample.get('on_pit_road') is not False:
            return {'measured': None, 'reason': '주행 중 깨끗한 랩에서 비교합니다.'}
        known = (finite(sample.get('incidents')) is not None or finite(sample.get('engine_warnings')) is not None
                 or type(sample.get('lmu_parts_detached')) is bool)
        return {'measured': dict(self.observed) if self.observed else None,
                'reason': ('이 게임의 접촉·손상 신호를 확인할 수 없습니다.' if not known else
                           '접촉·손상 신호 이후 깨끗한 완료 랩을 기다립니다.' if self.contact_lap is not None else
                           '접촉·손상 신호 전 2랩과 이후 깨끗한 1랩이 필요합니다.')}


def damage_parts(result):
    """The comparison as (sentence key, values) pairs, in the order they are said."""
    parts = []
    if result['slower_s']:
        parts.append(('damage_slower', {'loss': result['slower_s']}))
    if result['vmax_loss_kph']:
        parts.append(('damage_vmax', {'kph': result['vmax_loss_kph']}))
    changed = bool(parts)
    if not parts:
        parts.append(('damage_same', {}))
    if result['steer_deg']:
        parts.append(('damage_steer', {'steer': result['steer_deg']}))
    elif result['steer_deg'] is not None and not changed:
        # "The wheel is straight too" only follows "nothing changed"; after a loss it sounds like good news.
        parts.append(('damage_steer_same', {}))
    return parts


class PitStopWatch:
    """What a repair costs, the moment the sim says it: the car is stopped in its stall.

    The repair time only exists once the car stands in its stall. Fuelling starts at once and,
    in the stops measured so far, tyres and repairs ran alongside it, so the driver's question
    is whether the repair outlasts the fuel. The fuelling rate is measured from this session's
    own stops; without one, the fuel time is not given.
    """
    def __init__(self):
        self.reset()

    def reset(self):
        self.rates = deque(maxlen=5)   # litres per second, one per measured stop
        self.fill = None               # the fuelling in progress: (time, litres) when it began and now
        self.called = False

    def fuel_rate(self):
        return statistics.median(self.rates) if self.rates else None

    def ingest(self, sample, capacity_l=None):
        """Returns {'kind', 'repair_s', 'fuel_s'} once per stop, when the repair time first appears."""
        at, fuel = finite(sample.get('session_time')), finite(sample.get('fuel_l'))
        if sample.get('on_pit_road') is not True or at is None:
            self._close()
            self.called = False
            return None
        if fuel is not None:
            if self.fill is None:
                self.fill = {'at': at, 'fuel': fuel, 'last_at': at, 'last': fuel}
            elif fuel > self.fill['last'] + 1e-4:
                self.fill.update(last_at=at, last=fuel)
            elif fuel <= self.fill['fuel'] + 1e-4:
                self.fill.update(at=at, fuel=fuel, last_at=at, last=fuel)   # not fuelling yet: the start moves along
        mandatory, optional = finite(sample.get('pit_repair_left_s')), finite(sample.get('pit_optional_repair_left_s'))
        kind = 'mandatory' if mandatory and mandatory > 0 else 'optional' if optional and optional > 0 else None
        if kind is None or self.called:
            return None
        self.called = True
        planned, flags = finite(sample.get('pit_sv_fuel')), finite(sample.get('pit_sv_flags'))
        add = planned if planned is not None and flags is not None and int(flags) & 0x10 else 0.
        if capacity_l and fuel is not None:
            add = min(add, max(0., capacity_l - fuel))
        rate = self.fuel_rate()
        return {'kind': kind, 'repair_s': mandatory if kind == 'mandatory' else optional,
                'fuel_s': add / rate if rate and add > 1 else None}

    def _close(self):
        fill, self.fill = self.fill, None
        if fill and fill['last'] - fill['fuel'] >= 5 and fill['last_at'] - fill['at'] >= 5:
            self.rates.append((fill['last'] - fill['fuel']) / (fill['last_at'] - fill['at']))


REPAIR_EVEN_S = 5.     # closer than this either way is not a difference worth calling


def repair_verdict(stop, damage, laps_to_go):
    """Seconds an optional repair gains (positive) or costs (negative) by the finish; None when it cannot be weighed.

    The repair costs the time it keeps the car in the stall beyond the fuelling. Leaving it costs the measured
    loss on every lap still to drive. Being passed on the straights, or holding cars up, is not in this number.
    """
    if stop['kind'] != 'optional' or not damage or damage.get('loss_per_lap_s') is None or not laps_to_go or laps_to_go <= 0:
        return None
    return damage['loss_per_lap_s'] * laps_to_go - max(0., stop['repair_s'] - (stop.get('fuel_s') or 0.))


def repair_parts(stop, damage=None, laps_to_go=None):
    """The repair time and which way is faster to the finish; without that, the fuel time and what the contact changed."""
    parts = [('repair_mandatory' if stop['kind'] == 'mandatory' else 'repair_optional', {'sec': stop['repair_s']})]
    verdict = repair_verdict(stop, damage, laps_to_go)
    if verdict is not None:
        # One sentence to decide on (what the contact changed was said on track after the first clean lap).
        if abs(verdict) < REPAIR_EVEN_S:
            return parts + [('repair_even', {})]
        return parts + [('repair_gain' if verdict > 0 else 'repair_cost', {'sec': min(180., max(5., abs(verdict)))})]
    if stop.get('fuel_s'):
        parts.append(('repair_fuel_time', {'sec': stop['fuel_s']}))
    if damage:
        # Everything the contact changed. A car that laps as fast but lost top speed is passed on every
        # straight, so "no lap time lost" alone reads as "do not repair" (2026-10-12 race).
        found = []
        if damage['slower_s']:
            found.append(('repair_pace_loss', {'loss': damage['slower_s']}))
        if damage['vmax_loss_kph']:
            found.append(('damage_vmax', {'kph': damage['vmax_loss_kph']}))
        if damage['steer_deg']:
            found.append(('damage_steer', {'steer': damage['steer_deg']}))
        parts += found or [('damage_same', {})]
    return parts
