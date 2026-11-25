"""Compare the same class rivals across two independently observed pit stops."""
from collections import deque
from dataclasses import dataclass, field

from race_corner_cost import num


@dataclass(frozen=True)
class Point:
    group: object
    rank: int
    progress: float
    pit: bool
    pit_flag: bool

    @classmethod
    def read(cls, car):
        lap, pct, rank = (num(car.get(k)) for k in ('lap_completed', 'distance_pct', 'class_position'))
        zone = car.get('track_surface')
        pit = car.get('on_pit_road')
        if (lap is None or lap < 0 or lap != int(lap) or pct is None or not 0 <= pct < 1
                or zone not in (1, 2, 3) or type(pit) is not bool or car.get('is_pace_car')):
            return None
        return cls(car.get('class_id'), int(rank) if rank is not None and rank > 0 and int(rank) == rank else 0,
                   lap + pct, pit or zone in (1, 2), pit)


def order(own, rival):
    """Sign of the rival's longitudinal lead, independently confirmed by class order."""
    if (not own or not rival or own.pit or rival.pit or not own.rank or not rival.rank or own.group is None
            or own.group != rival.group or int(own.progress) != int(rival.progress)):
        return 0
    delta = rival.progress - own.progress
    if abs(delta) >= .45 or delta * (own.rank - rival.rank) <= 0:
        return 0
    return 1 if delta > 0 else -1


@dataclass
class Visit:
    entered: float
    stopped: float = 0.
    longest_stop: float = 0.
    flagged: bool = False
    returned: float = None

    def update(self, now, old, car, metres, dt):
        if self.returned is not None:
            return not car.pit
        if now - self.entered > 240:
            return False
        if car.pit:
            self.flagged |= car.pit_flag
            self.stopped = self.stopped + dt if old.pit_flag and abs(metres) < dt else 0.
            self.longest_stop = max(self.longest_stop, self.stopped)
            return True
        self.returned = now
        return self.flagged and self.longest_stop >= 3 and now - self.entered >= 8


@dataclass
class Pair:
    rival: int
    before: int
    start: float
    visits: dict = field(default_factory=dict)
    samples: int = 0
    rank_after: int = 0
    confirmed: float = None


class PitCycles:
    """Each candidate keeps the original identities until a stop or its evidence becomes invalid."""
    def __init__(self):
        self.frames = deque(maxlen=4)
        self.pairs = {}
        self.last_at = None
        self.own = None
        self.choice = None
        self.heard_stop = None

    def update(self, race, at):
        length, own = num(race.get('track_length_m')), race.get('player_idx')
        if (length is None or length <= 0 or own is None or race.get('position_source') == 'HELD'
                or race.get('state') != 4 or str(race.get('session_type', '')).lower() != 'race'):
            self.__init__()
            return
        stamp = num(race.get('scoring_sample_time'))
        stamp = at if stamp is None else stamp
        if self.own == own and stamp == self.last_at:
            return
        if self.last_at is None or own != self.own or not 0 < stamp - self.last_at <= 1.65:
            self.__init__()
        cars = {c['idx']: point for c in race.get('cars') or () if (point := Point.read(c)) is not None}
        if own not in cars:
            self.__init__()
            return
        dt = stamp - self.last_at if self.last_at is not None else 0
        previous = self.frames[-1] if self.frames else {}
        entries = {idx for idx, car in cars.items() if car.pit and idx in previous and not previous[idx].pit}
        if entries and len(self.frames) == 4 and own in previous:
            for rival, other in previous.items():
                if rival == own or rival in self.pairs or (own not in entries and rival not in entries):
                    continue
                before = order(previous[own], other)
                if (before and abs(previous[own].progress - other.progress) * length <= 500
                        and all(order(f.get(own), f.get(rival)) == before
                                and abs(f[own].rank - f[rival].rank) == 1 for f in self.frames)):
                    self.pairs[rival] = Pair(rival, before, stamp)
        self.choice = None
        candidates = []
        for rival, pair in list(self.pairs.items()):
            if not self._advance(pair, own, previous, cars, entries, length, stamp, dt):
                del self.pairs[rival]
            elif pair.confirmed is not None:
                car, me = cars[rival], cars[own]
                key = 'pit_cycle_ahead' if pair.before < 0 else 'pit_cycle_behind'
                stop = pair.visits[own].entered
                if stop != self.heard_stop:
                    candidates.append((abs(me.rank - car.rank) != 1, abs(me.progress - car.progress),
                                       key, (stop, rival, car.rank)))
        if candidates:
            _, _, key, token = min(candidates)
            self.choice = key, token, f'클래스 {token[2]}위'
        self.own, self.last_at = own, stamp
        self.frames.append(cars)

    def _advance(self, pair, own, previous, cars, entries, length, at, dt):
        rival = pair.rival
        for idx in (own, rival):
            old, car = previous.get(idx), cars.get(idx)
            if not old or not car or car.group != old.group:
                return False
            metres = (car.progress - old.progress) * length
            if not -5 * dt <= metres <= 92 * dt:
                return False
            if idx in entries:
                if idx in pair.visits or at - pair.start > 360:
                    return False
                pair.visits[idx] = Visit(at)
            visit = pair.visits.get(idx)
            if visit and not visit.update(at, old, car, metres, dt):
                return False
        if len(pair.visits) < 2:
            return at - pair.start <= 360
        if any(v.returned is None or v.returned == at for v in pair.visits.values()):
            return True
        car = cars[rival]
        if order(cars[own], car) != -pair.before or (pair.samples and car.rank != pair.rank_after):
            return False
        pair.samples += 1
        pair.rank_after = car.rank
        if pair.samples == 4:
            pair.confirmed = at
        return pair.confirmed is None or at - pair.confirmed <= 20

    def valid(self, key, token, sample):
        at = num(sample.get('session_time'))
        return (self.choice is not None and (key, token) == self.choice[:2]
                and (self.heard_stop is None or token[0] > self.heard_stop)
                and at is not None and 0 <= at - self.last_at <= 1.65
                and not (sample.get('tow_time_s') or 0))

    def delivered(self, token):
        stop = token[0]
        if self.heard_stop is None or stop > self.heard_stop:
            self.heard_stop = stop
        self.pairs = {idx: pair for idx, pair in self.pairs.items()
                      if self.own not in pair.visits or pair.visits[self.own].entered > self.heard_stop}
