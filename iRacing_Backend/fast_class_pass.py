"""Identify the position rival left in the mirror immediately after a faster class passes."""
from collections import deque
from dataclasses import dataclass, field
import logging

from radio_policy import speech_seconds
from race_strategy_clips import rival_words
from spotter_context import finite, progress


def signed_gap(pct, reference, length):
    return ((pct - reference + .5) % 1 - .5) * length


@dataclass
class TimedCar:
    pct: float
    lap: float
    cls: int
    since: float
    crossing: float | None = None
    clean_laps: deque = field(default_factory=lambda: deque(maxlen=2))

    def advance(self, pct, lap, at, previous_at, length):
        elapsed = at - previous_at
        metres = signed_gap(pct, self.pct, length)
        if not (0 <= lap - self.lap <= 1 and 0 <= metres <= 105 * elapsed):
            return False
        if self.pct > .85 and pct < .15 and metres > 0:
            crossed = previous_at + elapsed * (1 - self.pct) * length / metres
            if self.crossing is not None:
                duration = crossed - self.crossing
                if 25 <= duration <= 1200:
                    self.clean_laps.append(duration)
                else:
                    self.clean_laps.clear()
            self.crossing = crossed
        self.pct, self.lap = pct, lap
        return True

    def pace(self, at):
        if len(self.clean_laps) == 2 and at - self.crossing <= 1.15 * self.clean_laps[-1]:
            return sum(self.clean_laps) / 2
        return None


class FastClassPass:
    def __init__(self):
        self.observation = None
        self._reset_timing()

    def _reset_timing(self):
        self.cars = {}
        self.measured_paces = {}
        self.order = deque()
        self.last_source = None
        self.last_update = None
        self.recent = {}
        self.token = None
        self.pair = None
        self.event = None

    def valid(self, token, now):
        return (self.token == token and self.token is not None and self.last_update is not None
                and 0 <= now - self.last_update < 1.75 and now < self.until)

    def _observe_cars(self, entries, at, length):
        current = {}
        for idx, car in entries.items():
            pct, lap, cls = car['distance_pct'], car['lap_completed'], car.get('class_id')
            watch = self.cars.get(idx)
            crossing = watch.crossing if watch is not None else None
            if (watch is None or watch.cls != cls
                    or not watch.advance(pct, lap, at, self.last_source, length)):
                watch = TimedCar(pct, lap, cls, at)
            if watch.crossing is not None and watch.crossing != crossing and watch.clean_laps:
                pace = watch.pace(at)
                if pace is None:
                    self.measured_paces.pop(idx, None)
                else:
                    until = watch.crossing + 1.15 * watch.clean_laps[-1]
                    self.measured_paces[idx] = (cls, until, pace)
            current[idx] = watch
        self.cars = current
        # Briefly leaving the surface interrupts this lap. The last two fully observed
        # clean laps still describe the car until it owes us a newer lap.
        self.measured_paces = {idx: value for idx, value in self.measured_paces.items()
                               if value[1] >= at and (idx not in current or current[idx].cls == value[0])}

    def update(self, radio, sample, metadata, now):
        flags = finite(sample.get('session_flags'))
        if (not radio.traffic_enabled or not radio.allows('class_pass_rival') or
                sample.get('session_state') != 4 or flags is None or int(flags) & 0xC119 or
                sample.get('on_track') is not True or sample.get('is_replay') is not False or
                sample.get('on_pit_road') is not False or sample.get('track_surface') != 3):
            if self.last_source is not None:
                self._reset_timing()
            return

        clear = sample.get('car_left_right') == 1
        if not clear or ('position_confirmed' in sample and self.pair
                         and sample['position_confirmed'] != self.pair[2]-1):
            self.token = None
        if metadata is None:
            return
        game = metadata.get('game')
        at = finite(metadata.get('sample_time'))
        session_time = finite(sample.get('session_time'))
        length = finite(metadata.get('track_length_m'))
        age = finite(metadata.get('position_age_s', 0))
        if (game not in ('iracing', 'lmu') or str(metadata.get('session_type', '')).lower() != 'race'
                or at is None or length is None or length <= 0 or metadata.get('position_source') == 'HELD'
                or age is None or not 0 <= age <= .5
                or session_time is None or not 0 <= session_time - at <= 1.75):
            self._reset_timing()
            return
        if at == self.last_source:
            return
        if self.last_source is not None and not 0 < at-self.last_source <= 1.75:
            self._reset_timing()
        entries = {c['idx']: c for c in metadata.get('cars') or []
                   if c.get('idx') is not None and not c.get('is_pace_car') and progress(c) is not None}
        for car in metadata.get('cars') or []:
            if car.get('on_pit_road') is True:
                self.measured_paces.pop(car.get('idx'), None)
        self._observe_cars(entries, at, length)
        self.last_source, self.last_update = at, now
        own = metadata.get('player_idx')
        me = entries.get(own)
        if not me:
            self.order.clear()
            self.token = None
            return
        nearby = sorted((signed_gap(c['distance_pct'], me['distance_pct'], length), idx)
                        for idx, c in entries.items() if idx != own)
        rear = [idx for metres, idx in reversed(nearby) if -180 <= metres < 0][:2]
        front = next((idx for metres, idx in nearby if 0 <= metres <= 180), None)
        gaps = {idx: metres for metres, idx in nearby if abs(metres) <= 180}
        fast, rival = front, rear[0] if rear else None
        rank = self._rival_rank(me, entries.get(rival), length, gaps.get(rival), sample)
        pair = (fast, rival, rank)
        fast_class = finite(entries[fast].get('class_id')) if fast is not None else None
        good = (clear and rank is not None and fast_class is not None and fast_class >= 0
                and gaps[fast] >= 10 and fast_class != me['class_id'])
        if not good or self.pair != pair:
            self.token = None
            self.event = None
        while self.order and at - self.order[0][0] > 8:
            self.order.popleft()
        self.recent = {seen: stamp for seen, stamp in self.recent.items() if at - stamp < 40}
        if good and self.order and pair == self.order[-1][3]:
            closing = (self.order[-1][2] + gaps[rival]) / (at - self.order[-1][0])
            room = (-gaps[rival] - 8) / closing if closing > 0 else None
            if gaps[fast] <= 75 and -gaps[rival] <= 95 and (fast, rival) not in self.recent:
                self._consider(radio, game, at, now, own, pair, gaps, room)
            if self.event is not None:
                self.event['class_pass_contact_at'] = at + room if room is not None else None
        self.order.append((at, rear, -gaps[rear[0]] if rear else None, pair))

    @staticmethod
    def _rival_rank(me, rival, length, gap, sample):
        rank = finite(me.get('class_position'))
        cls = finite(me.get('class_id'))
        if (not rival or rank is None or not rank.is_integer() or not 1 <= rank < 128
                or cls is None or cls < 0 or rival.get('class_id') != cls
                or rival.get('class_position') != rank + 1 or not -130 <= gap <= -10
                or ('position_confirmed' in sample and sample['position_confirmed'] != rank)
                or abs((progress(rival) - progress(me)) * length - gap) >= 1):
            return None
        return int(rank) + 1

    def _consider(self, radio, game, at, now, own, pair, gaps, room):
        fast, rival, rank = pair
        # Before the pass the two closest cars behind must have been fast, then rival.
        # All three need an uninterrupted plausible trajectory through the crossing.
        since = max(self.cars[idx].since for idx in (own, fast, rival))
        if not any(when >= since and 2 <= at - when <= 8 and rear == [fast, rival] and gap >= 8
                   for when, rear, gap, _ in self.order):
            return
        if own not in self.measured_paces or fast not in self.measured_paces:
            return
        faster = self.measured_paces[fast][2]
        own_pace = self.measured_paces[own][2]
        if own_pace - faster < max(3., .04 * own_pace):
            return
        self.recent[fast, rival] = at
        self.observation = {'game': game, 'session_time': at, 'rank': rank, 'fast_idx': fast,
                            'rival_idx': rival, 'rival_behind_m': round(-gaps[rival], 1),
                            'fast_ahead_m': round(gaps[fast], 1)}
        if game == 'lmu':
            logging.getLogger('iRacingLive').info('LMU class-pass candidate run=%s %s', radio.run_id, self.observation)
            return
        normal, short = rival_words(rank)
        duration=finite(radio.prepared_seconds('spotter',normal,short)) if getattr(radio,'prepared_seconds',None) else None
        required=(duration+.5) if duration is not None else speech_seconds(normal)
        if room is not None and room < required:
            return
        self.pair = pair
        self.token = f'{fast}/{rival}/{at}'
        self.until = now + 4
        event = radio.emit('class_pass_rival', now, normal, role='spotter', priority=1, ttl=4,
                           condition=('class_pass', self.token), cooldown=0, speech_text=normal,
                           prepared_only=True)
        if event is not None:
            event['race_short_text'] = short
            event['speech_required_s'] = round(required, 3)
            self.event = event
