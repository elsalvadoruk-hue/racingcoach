"""Warn a slowed, off-course player about cars approaching the same course station."""
import math
import re

from race_strategy_clips import REENTRY_WORDS
from spotter_context import finite


def metres(after, before, length):
    return ((after - before + .5) % 1 - .5) * length


class ReentryApproach:
    def __init__(self):
        self.frame = None
        self.episode = None
        self.arrivals = {}
        self.gaps = {}
        self.passed = set()
        self.started = set()
        self.heard = set()
        self.claim = None
        self.event = None
        self.occupied = False

    def reset(self):
        self.__init__()

    def update(self, radio, sample, metadata, now):
        surface = sample.get('track_surface')
        if (not radio.traffic_enabled or not radio.allows('reentry_approach')
                or str(radio.metadata.get('session_type') or '').lower() != 'race'
                or sample.get('session_state') != 4 or sample.get('on_track') is not True
                or sample.get('is_replay') is not False or sample.get('on_pit_road') is not False
                or sample.get('session_flags') is None or int(sample['session_flags']) & 0xC011
                or (finite(sample.get('tow_time_s')) or 0) > 0 or surface not in (0, 3)):
            if self.frame:
                self.reset()
            return
        if self.episode is not None and surface == 3:
            self.reset()
        if metadata is not None:
            self.observe(metadata, surface, now)
        self.occupied = sample.get('car_left_right') in (2, 3, 4, 5, 6)
        if self.episode is None or surface != 0:
            return
        claim = self.current(now)
        if claim == self.claim:
            return
        self.claim, self.event = claim, None
        if not claim:
            return
        episode, cars, take = claim
        default, short = REENTRY_WORDS[take]
        arrival = self.arrivals[cars[0]][0]
        event = radio.emit('reentry_approach', now, default, speech_text=default, priority=2,
                           ttl=arrival-now, cooldown=0, condition=('reentry_approach', claim),
                           group='reentry_approach', prepared_only=True)
        if event:
            self.event = event
            event.update(race_short_text=short, reentry_take=take, reentry_arrival=arrival,
                         reentry_contacts=[f'{episode}/{idx}' for idx in cars])
            radio.track_delivery(event, reentry=(episode, cars))

    def observe(self, race, surface, now):
        length = finite(race.get('track_length_m'))
        stamp = finite(race.get('sample_time'))
        age = finite(race.get('position_age_s'))
        if (not length or length <= 0 or stamp is None or race.get('position_source') == 'HELD'
                or (race.get('game') == 'lmu' and (age is None or not 0 <= age <= .5))
                or re.search(r'figure[\s_-]*(?:eight|8)', str(race.get('track_name'))+' '+str(race.get('track_config')), re.I)):
            self.reset()
            return
        cars = race.get('cars') or ()
        own_idx = race.get('player_idx')
        own = next((c for c in cars if c.get('idx') == own_idx), {})
        pct = finite(own.get('distance_pct'))
        lap = finite(own.get('lap_completed'))
        if (pct is None or not 0 <= pct < 1 or own.get('on_pit_road') is not False
                or own.get('track_surface') != surface or lap is None or lap < 0):
            self.reset()
            return
        if surface == 3:
            # During normal laps only an on-course baseline is needed. Position
            # histories for the field start with the first off-course frame.
            self.frame = (stamp, now, 3, pct, {})
            return
        positions = {idx: value for car in cars if (idx := car.get('idx')) is not None and idx != own_idx
                     and not car.get('is_pace_car') and car.get('track_surface') == 3 and car.get('on_pit_road') is False
                     and (value := finite(car.get('distance_pct'))) is not None and 0 <= value < 1}
        old = self.frame
        if old and stamp == old[0]:
            return
        if old and not (0 < stamp-old[0] <= 1.65 and 0 < now-old[1] <= 1.65):
            self.reset()
            old = None
        self.frame = (stamp, now, surface, pct, positions)
        if not old:
            return
        dt = stamp-old[0]
        own_speed = metres(pct, old[3], length) / dt
        if not -5 <= own_speed <= 90:
            self.reset()
            return
        if old[2] == 3:
            self.episode = stamp
            return
        if self.episode is None:
            return
        gaps, arrivals = {}, {}
        for idx, other in positions.items():
            if idx not in old[4]:
                continue
            other_speed = metres(other, old[4][idx], length) / dt
            if not 0 < other_speed <= 100:
                continue
            gap = metres(pct, other, length)
            if gap <= 0 and idx in self.gaps and self.gaps[idx] > 0:
                self.passed.add(idx)
            if not 0 < gap <= 260:
                continue
            gaps[idx] = gap
            closing = other_speed - own_speed
            if own_speed > 12 or not 10 <= closing <= 90:
                continue
            eta = gap/closing
            if eta > 8:
                continue
            # Keep a longer measurement history for agreement at the 5–6 second boundary.
            # The prediction is indexed by wall time; LMU extrapolated positions already
            # refer to sample_time and a held frame is never a second observation.
            predicted = now + eta
            previous = self.arrivals.get(idx)
            stable = previous is not None and abs(previous[2]-predicted) <= 1
            arrival = min(predicted, previous[2]) if stable else predicted
            arrivals[idx] = (arrival, stable, predicted)
        self.gaps, self.arrivals = gaps, arrivals
        if self.event and self.claim and self.claim[1][0] in arrivals:
            current_arrival = arrivals[self.claim[1][0]][0]
            self.event.update(reentry_arrival=current_arrival, expires_at=current_arrival)

    def current(self, now):
        if self.occupied or not self.frame or self.episode is None or not 0 <= now-self.frame[1] < 1.65:
            return None
        # Only a positively observed passage ends the lead car. Losing its signal
        # never promotes another car to a confirmed follow-up.
        approaching = sorted((info[0], idx, info[1]) for idx, info in self.arrivals.items()
                             if idx not in self.passed and 0 < self.gaps[idx] <= 160)
        if not approaching:
            return None
        arrival, first, stable = approaching[0]
        eta = arrival-now
        if first in self.started or not 0 < eta <= 6:
            return None
        # Preserve the observed lower bound for a fresh warning, but keep an
        # existing warning until the player has checked the actual clip length.
        if (eta < 1.5 or self.gaps[first] < 15) and (not self.claim or first not in self.claim[1]):
            return None
        if eta >= 5 and not stable:
            return None
        train = tuple(idx for at, idx, _ in approaching if at-arrival <= 1.5 and idx not in self.started)
        if len(train)>1:
            return (self.episode, train, 'train')
        prefix = 'follow' if self.heard & self.passed else 'first'
        take = f'{prefix}_{math.floor(eta)}' if eta >= 5 else prefix
        return (self.episode, (first,), take)

    def valid(self, claim, now):
        return claim == self.claim and claim == self.current(now)

    def delivery(self, receipt, status):
        episode, cars = receipt
        if self.episode == episode and status in ('started', 'completed'):
            self.started.update(cars)
            if status == 'completed':
                self.heard.update(cars)
