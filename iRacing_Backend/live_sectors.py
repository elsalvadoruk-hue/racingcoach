"""Sector timing from positions: the driver's own laps and, coarsely, every other car's.

Nothing here is estimated beyond linear interpolation between two measured
positions. A sector, a lap or a comparison that was not fully observed is left out.
"""
import math
import statistics
from collections import deque


def finite(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


class LapSectors:
    """The driver's own lap in equal sectors, from the 30Hz samples.

    Keeps the sectors of the fastest clean lap and the fastest clean pass of
    each sector, for this car and layout while the program runs. From them:
    the theoretical best (every best sector in one lap) and how the lap in
    progress compares with the best lap at the last sector line.
    """
    SECTORS = 20

    def __init__(self):
        self.key = None
        self.forget()

    def forget(self):
        self.best_lap, self.best_lap_s, self.best_sectors = None, None, [None] * self.SECTORS
        self.reset()

    def reset(self):
        """Drop the lap in progress; the references stay."""
        self.previous, self.marks, self.clean, self.lap = None, [], False, None

    def ingest(self, sample, identity):
        key = (identity.get('car_id') or identity.get('car_path'), identity.get('track_id') or identity.get('track_path'),
               identity.get('track_config') or '')
        if key != self.key:
            self.key = key
            self.forget()
        pct, at, lap = finite(sample.get('distance_pct')), finite(sample.get('session_time')), sample.get('lap_number')
        valid = (pct is not None and 0 <= pct < 1 and at is not None and type(lap) in (int, float)
                 and sample.get('on_track') is True and sample.get('is_replay') is False and sample.get('on_pit_road') is False)
        previous, self.previous = self.previous, ({'pct': pct, 'at': at, 'lap': lap, 'incidents': sample.get('incidents')} if valid else None)
        if not valid or previous is None:
            self.marks, self.clean = [], False
            return
        dt = at - previous['at']
        wrapped = lap == previous['lap'] + 1 and previous['pct'] > .9 and pct < .1
        step = pct - previous['pct'] + (1 if wrapped else 0)
        if (not 0 < dt <= .5 or not 0 <= step < .05 or (lap != previous['lap'] and not wrapped)
                or sample.get('incidents') != previous['incidents'] or int(sample.get('session_flags') or 0) & 0xC108):
            self.clean = False
        if step <= 0:
            return
        # Every sector line passed between the two samples, at its interpolated time.
        start = previous['pct']
        last = math.floor(pct * self.SECTORS) + (self.SECTORS if wrapped else 0)
        for line in range(math.floor(start * self.SECTORS) + 1, last + 1):
            crossed = previous['at'] + dt * (line / self.SECTORS - start) / step
            if line == self.SECTORS:
                self._finish(crossed)
                self.marks, self.clean, self.lap = [crossed], True, lap
            elif self.clean and len(self.marks) == line % self.SECTORS:
                self.marks.append(crossed)
            else:
                self.clean = False

    def _finish(self, crossed):
        if not self.clean or len(self.marks) != self.SECTORS:
            return
        marks = [*self.marks, crossed]
        sectors = [b - a for a, b in zip(marks, marks[1:])]
        total = marks[-1] - marks[0]
        if any(s <= 0 for s in sectors):
            return
        self.best_sectors = [s if best is None or s < best else best for s, best in zip(sectors, self.best_sectors)]
        if self.best_lap_s is None or total < self.best_lap_s:
            self.best_lap, self.best_lap_s = sectors, total

    def theoretical(self):
        """Every best sector in one lap, with what it would gain on the best lap; None until a clean lap exists."""
        if self.best_lap_s is None or any(s is None for s in self.best_sectors):
            return None
        total = sum(self.best_sectors)
        return {'lap_s': total, 'best_lap_s': self.best_lap_s, 'gain_s': self.best_lap_s - total}

    def delta(self):
        """Lap in progress against the best lap at the last sector line: negative is ahead."""
        done = len(self.marks) - 1
        if not self.clean or self.best_lap is None or done < 1:
            return None
        return {'delta_s': self.marks[-1] - self.marks[0] - sum(self.best_lap[:done]), 'lap': self.lap, 'sectors': done,
                'at_pct': done / self.SECTORS}


class RivalSectors:
    """Sector times of every car from the 1Hz race positions.

    One position a second places a sector line to within a few hundredths at
    racing speed, so only large sectors and repeated laps are compared.
    """
    SECTORS, KEEP, NEEDED = 8, 3, 2

    def __init__(self):
        self.reset()

    def reset(self):
        self.previous, self.marks, self.times = {}, {}, {}

    def ingest(self, cars, at):
        seen = set()
        for car in cars or []:
            idx, pct = car.get('idx'), finite(car.get('distance_pct'))
            usable = (pct is not None and 0 <= pct < 1 and car.get('on_pit_road') is False and car.get('track_surface') == 3)
            old = self.previous.get(idx)
            seen.add(idx)
            if not usable:
                self.previous.pop(idx, None)
                self.marks.pop(idx, None)
                continue
            self.previous[idx] = (pct, at)
            if not old:
                continue
            dt, step = at - old[1], (pct - old[0] + .5) % 1 - .5
            if not 0 < dt <= 2.5 or not 0 <= step < .2:
                self.marks.pop(idx, None)
                continue
            if step == 0:
                continue
            last = math.floor(pct * self.SECTORS) + (self.SECTORS if pct < old[0] else 0)
            for line in range(math.floor(old[0] * self.SECTORS) + 1, last + 1):
                crossed = old[1] + dt * (line / self.SECTORS - old[0]) / step
                line %= self.SECTORS
                mark = self.marks.get(idx)
                # The sector that ends at this line began at the previous line.
                if mark and (mark[0] + 1) % self.SECTORS == line and crossed > mark[1]:
                    self.times.setdefault(idx, {}).setdefault(mark[0], deque(maxlen=self.KEEP)).append(crossed - mark[1])
                self.marks[idx] = (line, crossed)
        for idx in set(self.previous) - seen:
            self.previous.pop(idx, None)
            self.marks.pop(idx, None)

    def compare(self, own, other):
        """Per sector: own median minus the other car's. Negative means the driver is faster there."""
        mine, theirs = self.times.get(own) or {}, self.times.get(other) or {}
        result = []
        for sector in range(self.SECTORS):
            a, b = mine.get(sector), theirs.get(sector)
            if a and b and len(a) >= self.NEEDED and len(b) >= self.NEEDED:
                result.append({'sector': sector, 'delta_s': statistics.median(a) - statistics.median(b),
                               'middle_pct': (sector + .5) / self.SECTORS})
        return result


def place_name(middle_pct, sector, definitions):
    """What to call a stretch of the lap: the confirmed corner nearest its middle, else its number."""
    named = [d for d in definitions or [] if d.get('call') and finite(d.get('apex_pct')) is not None]
    if named:
        nearest = min(named, key=lambda d: abs((d['apex_pct'] - middle_pct + .5) % 1 - .5))
        if abs((nearest['apex_pct'] - middle_pct + .5) % 1 - .5) <= .5 / RivalSectors.SECTORS:
            return f"{nearest['call']} 부근"
    return f'{sector + 1}번 구간'
