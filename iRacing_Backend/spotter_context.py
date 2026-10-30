"""Conservative along-track traffic and locally checked pit-box estimates."""
from collections import deque
import math


def finite(value):
    return float(value) if type(value) in (int, float) and math.isfinite(value) else None


def progress(car):
    lap, pct = finite(car.get('lap_completed')), finite(car.get('distance_pct'))
    if (lap is None or lap < 0 or pct is None or not 0 <= pct < 1
            or car.get('on_pit_road') is not False or car.get('track_surface') != 3):
        return None
    return lap + pct


def estimated_gap_s(own, car, delta, fallback_pace):
    """Time gap for a signed lap-fraction offset.

    CarIdxEstTime shares one scale only inside a class, so it is used for
    same-class cars when it agrees with the distance offset. Everything else
    stays the distance-times-pace estimate.
    """
    fallback = abs(delta) * fallback_pace if fallback_pace else None
    lap = finite(own.get('class_est_lap_s'))
    a, b = finite(own.get('est_time_s')), finite(car.get('est_time_s'))
    if (lap is None or not 20 < lap < 1800 or a is None or b is None or own.get('class_id') is None
            or car.get('class_id') != own.get('class_id') or not 0 <= a <= lap * 1.2 or not 0 <= b <= lap * 1.2):
        return fallback
    gap = (b - a + lap / 2) % lap - lap / 2
    expected = delta * lap
    if gap * delta < 0 or abs(gap - expected) > max(1.5, abs(expected) * .35):
        return fallback
    return abs(gap)


def time_at(track, value):
    """When a car was at this point of its run, from its recent 1Hz positions. None outside what was seen."""
    points = list(track)
    for (t0, p0), (t1, p1) in zip(points, points[1:]):
        if p0 <= value <= p1:
            return t0 if p1 <= p0 else t0 + (t1 - t0) * (value - p0) / (p1 - p0)
    return None


class TrafficApproach:
    """Cars near the player and whether they are really getting closer.

    Closing is judged on the gap in seconds: how long ago the car in front passed the point where the car
    behind is now. The gap in metres shrinks in every braking zone while a car simply follows, which used to
    be called as an approach again and again (2026-10-12: 32 calls in a 25 minute race).
    """
    TRACK_S = 20          # seconds of positions kept for every car
    TREND_S = 4.          # the gap must be watched this long before a trend is believed
    CLOSING_RATE = .02    # seconds of gap lost per second
    CLOSING_DROP_S = .15  # and at least this much in all, above the noise of 1Hz positions
    NEAR_S, NEAR_ETA_S, FAR_ETA_S = 2.5, 45., 15.

    def __init__(self):
        self.reset()

    def reset(self):
        self.observations = {}
        self.tracks = {}
        self.armed = {}
        self.nearby = []
        self.updated_at = None
        self.source_at = None
        self.available = False

    def ingest(self, race, now):
        if self.updated_at == now:
            return
        wall_at = now
        source_at = finite(race.get('sample_time'))
        if source_at is not None:
            if self.source_at == source_at:
                return
            if self.source_at is not None and source_at < self.source_at:
                self.reset()
            now = source_at
        if race.get('position_age_s') is not None and not 0 <= race['position_age_s'] <= 2:
            self.reset()
            return
        cars = race.get('cars') or []
        own = next((c for c in cars if c.get('idx') == race.get('player_idx')), {})
        own_progress = progress(own)
        own_pace = finite(own.get('last_lap_s'))
        if own_pace is None or not 20 < own_pace < 1800:
            own_pace = None
        if own_progress is None or own.get('class_id') is None:
            self.reset()
            return
        self.updated_at, self.available, self.source_at = wall_at, True, source_at
        # Lap counts only describe race order; practice/qualifying counts are unrelated.
        race_session = str(race.get('session_type') or '').lower() == 'race'
        own_class_pace = finite(own.get('class_est_lap_s'))
        observations, nearby, armed, tracks = {}, [], {}, {}
        for car in cars:
            current = progress(car)
            if current is None or car.get('idx') is None:
                continue
            track = self.tracks.get(car['idx'], deque(maxlen=self.TRACK_S))
            if track and (not .4 <= now - track[-1][0] <= 2 or not 0 <= current - track[-1][1] <= (now - track[-1][0]) / 20):
                track = deque(maxlen=self.TRACK_S)
            track.append((now, current))
            tracks[car['idx']] = track
        self.tracks = tracks
        own_track = tracks.get(own.get('idx'), ())
        for car in cars:
            current = progress(car)
            if car.get('idx') == own.get('idx') or current is None or car.get('class_id') is None:
                continue
            relative = current - own_progress
            delta = (relative + .5) % 1 - .5
            if abs(delta) < .0001 or own_pace and abs(delta) * own_pace > 12:
                continue
            side = 'ahead' if delta > 0 else 'behind'
            history = self.observations.get(car['idx'], deque(maxlen=8))
            if history:
                previous = history[-1]
                dt = now - previous['at']
                if (not .4 <= dt <= 2 or previous['side'] != side
                        or not 0 <= current - previous['car'] <= dt / 20
                        or not 0 <= own_progress - previous['own'] <= dt / 20):
                    history = deque(maxlen=4)
            # Seconds between the two cars at the same point of the track (whole laps between them left out).
            passed = (time_at(own_track, own_progress + delta) if side == 'behind'
                      else time_at(tracks.get(car['idx'], ()), current - delta))
            seconds = now - passed if passed is not None else None
            history.append({'at': now, 'gap': abs(delta), 'side': side, 'car': current, 'own': own_progress, 'seconds': seconds})
            observations[car['idx']] = history
            closing, eta = False, None
            timed = [h for h in history if h['seconds'] is not None]
            if len(timed) >= 3 and timed[-1]['at'] - timed[0]['at'] >= self.TREND_S:
                drop = timed[0]['seconds'] - timed[-1]['seconds']
                rate = drop / (timed[-1]['at'] - timed[0]['at'])
                closing = (rate >= self.CLOSING_RATE and drop >= self.CLOSING_DROP_S
                           and timed[-1]['seconds'] <= min(h['seconds'] for h in timed[:-1]) + .05)
                if closing:
                    eta = seconds / rate
            other_pace = finite(car.get('last_lap_s'))
            other_class_pace = finite(car.get('class_est_lap_s'))
            different = car['class_id'] != own['class_id']
            if (own_class_pace is not None and other_class_pace is not None
                    and 20 < own_class_pace < 1800 and 20 < other_class_pace < 1800):
                # One spin or out-lap must not relabel a whole class.
                pace_difference = own_class_pace - other_class_pace
            else:
                pace_difference = own_pace - other_pace if own_pace and other_pace is not None and 20 < other_pace < 1800 else None
            kind = 'other_class' if different else 'same_class'
            key = None
            if different and pace_difference is not None:
                if pace_difference >= max(2, (own_class_pace or own_pace or 0) * .02):
                    kind = 'faster_class'
                    if side == 'behind': key = 'traffic_faster_behind'
                elif pace_difference <= -max(2, (own_class_pace or own_pace or 0) * .02):
                    kind = 'slower_class'
                    if side == 'ahead': key = 'traffic_slower_ahead'
            elif not different and race_session:
                lap_offset = round(relative - delta)
                if lap_offset >= 1:
                    kind = 'lapping_car'
                    if side == 'behind': key = 'traffic_lapping_behind'
                elif lap_offset <= -1:
                    kind = 'lapped_car'
                    if side == 'ahead': key = 'traffic_lapped_ahead'
            gap = seconds if seconds is not None else estimated_gap_s(own, car, delta, own_pace)
            if gap is None or gap > 12:
                continue
            # One call when a car arrives on the bumper; it re-arms after dropping back.
            arrived = side == 'behind' and gap <= .5 and len(history) >= 2 and self.armed.get(car['idx'], True)
            armed[car['idx']] = not arrived and (self.armed.get(car['idx'], True) or side != 'behind' or gap >= .8)
            nearby.append({'idx': car['idx'], 'name': car.get('name'), 'class_name': car.get('class_name'),
                           'side': side, 'kind': kind, 'gap_estimate_s': round(gap, 1),
                           'close': side == 'behind' and gap <= .7, 'close_key': 'close_behind' if arrived else None,
                           'approaching': closing, 'catch_estimate_s': round(eta, 1) if eta is not None else None,
                           'time_gap_s': round(seconds, 2) if seconds is not None else None,
                           'gap_source': 'PASSAGE_HISTORY' if seconds is not None else 'PACE_ESTIMATE',
                           'observed_at': now,
                           'position_samples': min(len(own_track), len(tracks.get(car['idx'], ()))),
                           'timing_samples': len(timed),
                           'preparation_reason': None if seconds is not None else 'PASSAGE_HISTORY_WARMUP',
                           'call_key': key if closing and (eta <= self.FAR_ETA_S or seconds <= self.NEAR_S and eta <= self.NEAR_ETA_S) else None})
        self.observations, self.nearby, self.armed = observations, nearby, armed

    def view(self, now):
        fresh = self.updated_at is not None and 0 <= now - self.updated_at < 2
        nearby = sorted(self.nearby, key=lambda c: c['gap_estimate_s']) if fresh else []
        return {'available': self.available and fresh,
                'source_time': self.source_at,
                'preparation_reason': ('STALE_OR_MISSING_POSITIONS' if not self.available or not fresh else
                                       'PASSAGE_HISTORY_WARMUP' if not nearby else None),
                'ahead': next((c for c in nearby if c['side'] == 'ahead'), None),
                'behind': next((c for c in nearby if c['side'] == 'behind'), None), 'nearby': nearby}


def traffic_context(race, at):
    """Observation context for a corner, never an attribution of a time loss.

    Reuse the traffic watch's 2.5 s proximity window. Incomplete coverage is
    UNKNOWN; it cannot silently become a solo reference.
    """
    stamp = finite(race.get('sample_time'))
    cars = [c for c in race.get('cars') or [] if not c.get('is_pace_car')]
    own = next((c for c in cars if c.get('idx') == race.get('player_idx')), {})
    pct = finite(own.get('distance_pct'))
    if stamp is None or at is None or not 0 <= at - stamp <= 2 or pct is None or progress(own) is None:
        return 'UNKNOWN'
    if race.get('position_age_s') is not None and not 0 <= race['position_age_s'] <= 2:
        return 'UNKNOWN'
    pace = finite(own.get('last_lap_s'))
    if pace is None or not 20 < pace < 1800:
        pace = finite(own.get('class_est_lap_s'))
    unknown = False
    for car in cars:
        if car is own or car.get('on_pit_road') is True:
            continue
        if progress(car) is None:
            unknown = True
            continue
        delta = (car['distance_pct'] - pct + .5) % 1 - .5
        if pace and 20 < pace < 1800 and abs(delta) * pace <= TrafficApproach.NEAR_S:
            return 'TRAFFIC_PRESENT'
        if pace is None:
            unknown = True
    return 'UNKNOWN' if unknown else 'SOLO'


class HazardWatch:
    """Off-track or nearly stopped cars a few seconds ahead, from 1Hz positions.

    Only measured position and surface state are used. A single wide moment
    is ignored: off-track needs two observations, slow needs a measured
    interval, and the player must be at racing speed.

    "Slow" is judged against the place the car is in, never against the player:
    a car in a hairpin is slower than a player on the straight behind it and is
    no hazard. The usual pace of each stretch is learned from every car seen
    there; until a stretch is known only a car that has all but stopped is called.
    """
    BINS, KNOWN, SLOW, STOPPED = 200, 8, .35, .1

    def __init__(self):
        self.rates = {}
        self.reset()

    def usual(self, pct):
        """Typical progress per second on this stretch, or None while it is not known."""
        seen = self.rates.get(int(pct * self.BINS) % self.BINS)
        return sorted(seen)[len(seen) * 3 // 4] if seen and len(seen) >= self.KNOWN else None

    def reset(self):
        self.previous = {}
        self.hazards = []
        self.updated_at = None

    def ingest(self, race, now):
        cars = race.get('cars') or []
        own = next((c for c in cars if c.get('idx') == race.get('player_idx')), {})
        own_pct = finite(own.get('distance_pct'))
        pace = finite(own.get('last_lap_s'))
        if pace is None or not 20 < pace < 1800:
            pace = finite(own.get('class_est_lap_s'))
        if (own_pct is None or not 0 <= own_pct < 1 or own.get('on_pit_road') is not False
                or own.get('track_surface') != 3 or pace is None or not 20 < pace < 1800):
            self.reset()
            return
        old_own = self.previous.get(own['idx'])
        current, hazards = {own['idx']: {'at': now, 'pct': own_pct, 'surface': 3}}, []
        for car in cars:
            pct, surface = finite(car.get('distance_pct')), car.get('track_surface')
            if (car.get('idx') == own['idx'] or pct is None or not 0 <= pct < 1
                    or car.get('on_pit_road') is not False or surface not in (0, 3)):
                continue
            current[car['idx']] = {'at': now, 'pct': pct, 'surface': surface}
            gap = ((pct - own_pct + .5) % 1 - .5) * pace
            old = self.previous.get(car['idx'])
            if not old or not .4 <= now - old['at'] <= 2:
                continue
            moved = (pct - old['pct'] + .5) % 1 - .5
            rate = moved / (now - old['at'])
            usual = self.usual(pct)
            if surface == 3 and old['surface'] == 3 and 0 < moved < .05:
                self.rates.setdefault(int(pct * self.BINS) % self.BINS, deque(maxlen=30)).append(rate)
            if not 1.5 <= gap <= 10 or not old_own or old['at'] != old_own['at']:
                continue
            own_moved = (own_pct - old_own['pct'] + .5) % 1 - .5
            if own_moved * pace / (now - old['at']) < .5:
                continue
            slow = rate < self.STOPPED / pace or (usual is not None and rate < usual * self.SLOW)
            kind = ('hazard_off_ahead' if surface == 0 and old['surface'] == 0
                    else 'hazard_slow_ahead' if surface == 3 and slow else None)
            if kind:
                hazards.append({'idx': car['idx'], 'name': car.get('name'), 'kind': kind, 'gap_estimate_s': round(gap, 1)})
        self.previous, self.updated_at = current, now
        self.hazards = sorted(hazards, key=lambda h: h['gap_estimate_s'])

    def view(self, now):
        return self.hazards if self.updated_at is not None and 0 <= now - self.updated_at < 2 else []


class PitBoxGuide:
    """Uses lap-distance projection, checked against actual pit-lane motion.

    A fresh entry and at least 15m of consistent motion are required. Never
    infers a box after towing, a jump, a reversal or an invalid SDK position.
    Distances remain estimates even when the local projection is consistent.
    """
    THRESHOLDS = (200, 100, 50, 20, 10)

    def __init__(self):
        self.reset()

    def reset(self):
        self.last = None
        self.identity = None
        self.entered = False
        self.blocked = False
        self.travel, self.projected = 0., 0.
        self.distance = None
        self.reason = '피트 진입 대기'
        self.crossed = set()
        self.last_threshold = None

    def ingest(self, sample, previous, metadata, now):
        self.last_threshold = None
        if sample.get('on_pit_road') is not True:
            self.reset()
            return None
        if previous.get('on_pit_road') is False:
            self.reset()
            self.entered = True
        length, box = finite(metadata.get('track_length_m')), finite(metadata.get('pit_box_pct'))
        pct, speed = finite(sample.get('distance_pct')), finite(sample.get('speed_mps'))
        signature = (length, box, metadata.get('player_idx'))
        valid = (length is not None and 100 < length < 100000 and box is not None and 0 <= box < 1
                 and type(metadata.get('player_idx')) is int and 0 <= metadata['player_idx'] < 128
                 and pct is not None and 0 <= pct < 1 and speed is not None and speed >= 0
                 and sample.get('on_track') is True and sample.get('is_replay') is False
                 and (finite(sample.get('tow_time_s')) or 0) <= 0)
        if not valid or not self.entered or self.identity is not None and self.identity != signature:
            self.blocked = True
            self.distance = None
            self.reason = '박스 위치·주행 데이터 확인 필요'
            return None
        self.identity = signature
        if self.blocked:
            self.distance = None
            return None
        projected_distance = ((box - pct) % 1) * length
        old = self.last
        self.last = {'at': now, 'pct': pct, 'speed': speed, 'projected_distance': projected_distance}
        if old is None:
            self.reason = '피트로드 이동 확인 중'
            return None
        dt = now - old['at']
        movement = ((pct - old['pct'] + .5) % 1 - .5) * length
        travel = (speed + old['speed']) * .5 * dt
        if (not 0 < dt <= .5 or movement < -.5 or movement > max(4, travel * 2.5)
                or travel > 2 and movement < travel * .3):
            self.blocked = True
            self.distance = None
            self.reason = '위치 변화 불일치 · 이번 진입 안내 중지'
            return None
        if projected_distance > old['projected_distance'] + length * .5:
            self.blocked = True
            self.distance = None
            self.reason = '박스 위치 통과'
            return None
        self.travel += max(0, travel)
        self.projected += max(0, movement)
        if self.travel < 15 or self.projected < 5:
            self.reason = '피트로드 이동 확인 중'
            return None
        scale = self.travel / self.projected
        if not .5 <= scale <= 2:
            self.blocked = True
            self.distance = None
            self.reason = '거리 추정 불안정 · 이번 진입 안내 중지'
            return None
        old_distance = self.distance
        self.distance = projected_distance * scale
        self.reason = '피트박스까지 추정 거리'
        if speed < 2 or sample.get('pit_service_status') in (1, 2, 100, 101, 102, 103, 104, 105):
            return None
        # Only the newest crossed threshold is announced; no catch-up queue.
        thresholds = [t for t in self.THRESHOLDS if t not in self.crossed
                      and old_distance is not None and old_distance > t + 1e-6 and self.distance <= t + 1e-6]
        self.crossed.update(thresholds)
        if thresholds:
            self.last_threshold = min(thresholds)
            return self.last_threshold
        return None

    def view(self):
        return {'distance_estimate_m': round(self.distance) if self.distance is not None else None,
                'ready': self.distance is not None and not self.blocked, 'reason': self.reason}
