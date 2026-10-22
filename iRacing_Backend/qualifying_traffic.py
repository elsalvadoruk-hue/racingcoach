"""Open qualifying only: an observed sector-pace estimate, never a clear-track guarantee.

Two complete continuously observed laps are required for EVERY car currently on
track, including other classes. No class estimates or best-lap substitutes are
used. Piecewise linear eight-sector profiles retain the recent range as well as
the median. Projected encounters at every sector boundary exclude start times.
This accounts for a car that catches in a sector and loses the time again later.
"""
from collections import deque
from itertools import accumulate
import math
import statistics


def finite(value):
    return float(value) if type(value) in (int, float) and math.isfinite(value) else None


def is_open_qualifying(metadata):
    # LMU's adapter historically maps every qualifying code to "Open Qualify".
    # It does not observe the private/shared setting: preserve its old behavior.
    return (metadata.get('game') != 'lmu'
            and str(metadata.get('session_type') or '').strip().lower() == 'open qualify')


def elapsed(profile, pct):
    position = pct * len(profile)
    sector = min(len(profile) - 1, int(position))
    return sum(profile[:sector]) + profile[sector] * (position - sector)


def start_window(profiles, positions, own_idx, max_delay=15.):
    """Earliest modelled start within max_delay seconds of our usual line arrival.

    Three seconds is an initial planning allowance (2s separation + 1s for the
    1Hz observation), not a validated safety distance or a handling threshold.
    Ranges of recent profiles widen every excluded interval. All circulating
    cars, on both sides and in all classes, constrain the same start window.
    """
    mine = profiles[own_idx]
    own_cumulative = [list(accumulate((0., *p))) for p in mine]
    own_start = statistics.median(sum(p) - elapsed(p, positions[own_idx]) for p in mine)
    forbidden, crossings = [], []
    for idx, laps in profiles.items():
        if idx == own_idx:
            continue
        cumulatives = [list(accumulate((0., *p))) for p in laps]
        # Cover every passage during our approach and next lap, including cars
        # more than one lap faster. No fixed cycle count silently misses a car.
        shortest = min(map(sum, laps))
        cycles = math.ceil((own_start + max_delay + max(map(sum, mine))) / shortest) + 2
        for cycle in range(-2, cycles + 1):
            starts = [sum(p) - elapsed(p, positions[idx]) + cycle * sum(p) for p in laps]
            crossings.append(statistics.median(starts))
            lows, highs = [], []
            for sector in range(9):
                arrivals = [start + cumulative[sector] for start, cumulative in zip(starts, cumulatives)]
                lows.append(min(arrivals) - max(c[sector] for c in own_cumulative) - 3.)
                highs.append(max(arrivals) - min(c[sector] for c in own_cumulative) + 3.)
            # Arrival difference is continuous between boundaries. A sign change
            # is an encounter even if neither endpoint happens to be within 3s.
            low, high = min(lows), max(highs)
            if high >= own_start and low <= own_start + max_delay:
                forbidden.append((low, high))
    chosen = own_start
    for low, high in sorted(forbidden):
        if low <= chosen <= high:
            # Round the extra time UP, then continue checking all other cars.
            chosen = own_start + math.ceil((high - own_start + .01) * 10) / 10
    if chosen > own_start + max_delay:
        return {'status': 'blocked'}
    ahead = [chosen - at for at in crossings if at < chosen]
    behind = [at - chosen for at in crossings if at >= chosen]
    if not ahead or not behind:
        return {'status': 'unknown'}
    return {'status': 'window', 'delay_s': chosen - own_start,
            'front_s': min(ahead), 'rear_s': min(behind)}


class QualifyingTraffic:
    SECTORS, NEEDED, KEEP = 8, 2, 3
    MAX_AGE_S = 600.

    def __init__(self):
        self.reset()

    def reset(self):
        self.previous, self.marks, self.laps = {}, {}, {}
        self.result, self.at = None, None
        self.revision = 0
        self.incidents = None

    def ingest(self, metadata, sample, now):
        self.revision += 1
        self.result, self.at = None, now
        if not is_open_qualifying(metadata):
            self.reset()
            return
        at = finite(sample.get('session_time'))
        if (at is None or sample.get('session_state') != 4 or sample.get('session_flags') is None
                or int(sample['session_flags']) & 0xC119 or sample.get('is_replay') is not False
                or sample.get('on_track') is not True):
            self.previous.clear()
            self.marks.clear()
            return
        own_idx = metadata.get('player_idx')
        incidents = sample.get('incidents')
        if incidents != self.incidents:
            self.marks.pop(own_idx, None)
        self.incidents = incidents
        cars = [car for car in (metadata.get('cars') or [])[:128] if not car.get('is_pace_car')]
        seen = set()
        active, positions, profiles = [], {}, {}
        unknown = False
        for car in cars:
            idx, pct = car.get('idx'), finite(car.get('distance_pct'))
            seen.add(idx)
            if car.get('on_pit_road') is True or car.get('track_surface') == -1:
                self.previous.pop(idx, None)
                self.marks.pop(idx, None)
                self.laps.pop(idx, None)
                continue
            active.append(idx)
            usable = (pct is not None and 0 <= pct < 1 and car.get('on_pit_road') is False
                      and car.get('track_surface') == 3)
            if not usable:
                unknown = True
                self.previous.pop(idx, None)
                self.marks.pop(idx, None)
                continue
            positions[idx] = pct
            old = self.previous.get(idx)
            self.previous[idx] = (pct, at)
            if old:
                dt, step = at - old[1], (pct - old[0] + .5) % 1 - .5
                if not 0 < dt <= 2.5 or not 0 < step < .1:
                    self.marks.pop(idx, None)
                    unknown = True
                else:
                    last = math.floor(pct * 8) + (8 if pct < old[0] else 0)
                    for line in range(math.floor(old[0] * 8) + 1, last + 1):
                        crossed = old[1] + dt * (line / 8 - old[0]) / step
                        sector = line % 8
                        marks = self.marks.setdefault(idx, [])
                        if sector == 0:
                            if len(marks) == 8:
                                times = [b - a for a, b in zip(marks, [*marks[1:], crossed])]
                                if all(t > 0 for t in times) and 20 <= sum(times) <= 1800:
                                    self.laps.setdefault(idx, deque(maxlen=3)).append((at, times))
                            self.marks[idx] = [crossed]
                        elif len(marks) == sector:
                            marks.append(crossed)
                        else:
                            self.marks.pop(idx, None)
            laps = [p for observed_at, p in self.laps.get(idx, []) if 0 <= at - observed_at <= self.MAX_AGE_S]
            if len(laps) >= self.NEEDED:
                profiles[idx] = laps
        for idx in set(self.previous) | set(self.laps):
            if idx not in seen:
                self.previous.pop(idx, None)
                self.marks.pop(idx, None)
                self.laps.pop(idx, None)
        if own_idx not in active or len(active) <= 1:
            return
        if unknown or any(idx not in profiles for idx in active):
            self.result = {'status': 'unknown'}
            return
        remaining = finite(sample.get('session_time_remaining_s'))
        horizon = max(map(sum, profiles[own_idx]))
        approach = max(sum(p) - elapsed(p, positions[own_idx]) for p in profiles[own_idx])
        if remaining is None or remaining <= horizon + approach:
            return
        self.result = start_window(profiles, positions, own_idx, min(15., remaining - horizon - approach))

    def current(self, sample, now):
        pct = finite(sample.get('distance_pct'))
        window = finite(sample.get('workload_in_s'))
        return (self.result is not None and self.at is not None and 0 <= now - self.at < 1.5
                and sample.get('session_state') == 4 and sample.get('on_pit_road') is False
                and sample.get('session_flags') is not None and not int(sample['session_flags']) & 0xC119
                and sample.get('on_track') is True and sample.get('is_replay') is False
                and pct is not None and .65 <= pct <= .92 and window is not None and window >= 12
                and (finite(sample.get('speed_mps')) or 0) >= 10)
