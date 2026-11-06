"""Conservative tire warnings from fresh, repeated, explicitly known states."""
from collections import deque
import time
import uuid
from .snapshot import TireSnapshot, WHEELS

LABELS = dict(zip(WHEELS, ('왼쪽 앞', '오른쪽 앞', '왼쪽 뒤', '오른쪽 뒤')))
STATE_MAX_AGE_S = 5  # newest agreeing reading
STATE_MAX_GAP_S = 6  # between consecutive readings (3 readings may span up to 12 s)
# Cold tires at the start or after a pit stop always read low. 'Pressure low' waits until
# that wheel has once come up to ok/high, or this many laps have passed since the start
# or the pit exit (a tire still low by then really is low).
WARMUP_LAPS = 2
SHORT_PHRASES = {f'pressure_{state}_{wheel}': f'{label} 타이어 압력 {word}.'
           for wheel, label in LABELS.items() for state, word in (('high', '높음'), ('low', '낮음'))}
SHORT_PHRASES.update({f'temperature_hot_{wheel}': f'{label} 타이어 과열.' for wheel, label in LABELS.items()})
SHORT_PHRASES.update(temperature_cold='타이어 식었음.', tires_ok='타이어 적정 범위 들어옴.',
    trend_front='앞 타이어 온도 계속 상승.', trend_rear='뒤 타이어 온도 계속 상승.',
    input_front='앞 타이어 온도 상승. 진입 속도 조금 낮춤.',
    input_rear='뒤 타이어 온도 상승. 탈출 가속 조금 부드럽게.',
    temperature_recovered='타이어 온도 앞선 랩 수준으로 내려옴.')
PHRASES = {f'pressure_{state}_{wheel}': f'{label} 타이어 압력이 {word}.'
           for wheel, label in LABELS.items() for state, word in (('high', '높습니다'), ('low', '낮습니다'))}
PHRASES.update({f'temperature_hot_{wheel}': f'{label} 타이어가 과열됐습니다.' for wheel, label in LABELS.items()})
PHRASES.update(temperature_cold='타이어가 식었습니다.', tires_ok='타이어가 적정 범위에 들어왔습니다.',
    trend_front='앞 타이어 온도가 계속 오르고 있습니다.', trend_rear='뒤 타이어 온도가 계속 오르고 있습니다.',
    input_front='앞 타이어 온도가 오르고 있습니다. 코너 진입 속도를 조금 낮춰 보세요.',
    input_rear='뒤 타이어 온도가 오르고 있습니다. 코너 탈출에서 가속을 조금 부드럽게 해 주세요.',
    temperature_recovered='타이어 온도가 앞선 랩 수준으로 내려왔습니다.')
# Pressure advice: the numbers are on screen; the voice only says where to look.
# Their clips are made separately; until then these are skipped like any missing clip.
ADVICE_PHRASES = dict(advice_garage='출발 냉간 압력 추천이 준비됐습니다. 화면을 확인해 주세요.',
    advice_next_pit='다음 피트 냉간 압력 추천이 준비됐습니다.',
    track_temp_change='노면 온도가 크게 바뀔 예정입니다. 다음 피트 압력을 확인해 주세요.')
ADVICE_SHORT_PHRASES = dict(advice_garage='출발 압력 추천 준비.', advice_next_pit='다음 피트 압력 추천 준비.',
    track_temp_change='노면 온도 변화 예정. 피트 압력 확인.')


class TireAdvisor:
    """No universal targets, inferred pressure, or wear-to-grip predictions."""
    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        # Advice calls live outside reset(): the garage is not an active cockpit.
        self.advice_event, self.advice_sent = None, set()
        self.reset()

    def offer(self, key, token, *, garage, game='iracing'):
        """Queue one advice call; each (key, token) is said at most once."""
        now = self.clock()
        current = self.advice_event
        if (key, token) in self.advice_sent or current and (current['key'], current['token']) == (key, token):
            return
        self.advice_event = dict(id=uuid.uuid4().hex, key=key, token=token, text=ADVICE_PHRASES[key], family=key,
            created_at=now, expires_at=now + 60, garage=garage, game=game, engineer='coach', priority=0)

    def withdraw(self, key):
        if self.advice_event and self.advice_event['key'] == key:
            self.advice_event = None

    def _advice_quiet(self, event, now):
        if not event['garage']:
            return self.quiet(now)
        s = self.latest  # Not driving: say it while sitting in the garage.
        return bool(s and s.in_garage is True and 0 <= now - s.observed_at < 2.5)

    def reset(self):
        self.identity = None
        self.last_t = None
        self.latest = None
        self.history = {}
        self.sent = {}
        self.pending = None
        self.was_warm = False
        self.ok_sent = False
        self.warm_start_lap, self.warmed, self.on_pit_road = None, set(), False
        from .coaching import TireCoaching
        self.coaching = TireCoaching()

    def observe(self, data, *, enabled, context=None, analyze=False):
        now = self.clock()
        if not (enabled or analyze) or not isinstance(data, dict):
            self.pending = None
            self.coaching.reset()
            return
        try:
            snapshot = TireSnapshot.from_dict(data)
        except (ValueError, TypeError, KeyError):
            self.pending = None
            return
        if (snapshot.identity != self.identity or not snapshot.active or
                self.last_t is not None and snapshot.t is not None and snapshot.t < self.last_t):
            self.reset()
        self.identity, self.last_t, self.latest = snapshot.identity, snapshot.t, snapshot
        if not snapshot.active:
            return
        self.coaching.observe(snapshot, context)
        self._track_warmup(snapshot)
        if not enabled:
            self.pending=None
            return
        for wheel in WHEELS:
            value = snapshot.wheels[wheel]
            for field, state, numeric in (('pressure', value.pressure_state, value.pressure_kpa),
                                          ('temperature', value.temp_state, value.temp_c)):
                key = field, wheel
                history = self.history.setdefault(key, deque(maxlen=5))
                if numeric is not None:
                    # A newly read number with unknown color/range is evidence
                    # that the old classification is no longer confirmed.
                    history.append((state, now))
        self.warmed.update(w for w in WHEELS if self.state('pressure', w, now) in ('ok', 'high'))
        self.current(now)  # Drop expired or no longer applicable calls first.
        if self.pending:
            return
        candidates = []
        for wheel in WHEELS:
            for field, state in (('temperature', 'hot'), ('pressure', 'high'), ('pressure', 'low')):
                if self.state(field, wheel, now) == state and not (state == 'low' and self.warming(wheel, now)):
                    candidates.append(f'{field}_{state}_{wheel}')
        temps = [self.state('temperature', w, now) for w in WHEELS]
        if any(s == 'ok' for s in temps):
            self.was_warm = True
        if self.was_warm and all(s == 'cold' for s in temps):
            candidates.append('temperature_cold')
        if not self.ok_sent and all(self.state(field, w, now) == 'ok'
                                   for field in ('pressure', 'temperature') for w in WHEELS):
            candidates.append('tires_ok')
        if self.coaching.candidate:
            candidates.append(self.coaching.candidate)
        for key in candidates:
            family = 'thermal_coaching' if key in ('trend_front','trend_rear','input_front','input_rear') else key
            previous = self.sent.get(family)
            if previous and (now - previous[0] < 180 or snapshot.lap is None or
                             previous[1] is None or snapshot.lap - previous[1] < 3):
                continue
            self.pending = dict(id=uuid.uuid4().hex, key=key, text=PHRASES[key],
                family=family, created_at=now, expires_at=now + 12, game=snapshot.game, engineer='coach', priority=0)
            break

    def state(self, field, wheel, now):
        history = [(state, t) for state, t in self.history.get((field, wheel), ()) if t <= now]
        if len(history) < 3:
            return None
        # Require the latest three readings to agree. A hand or wheel rim can hide
        # the dash for a few seconds, so readings may be spread out, but the newest
        # must be recent and no gap may be long: intermittent missing measurements
        # cannot turn an old consensus into a new transition.
        last = history[-3:]
        if now - last[-1][1] > STATE_MAX_AGE_S or any(b[1] - a[1] > STATE_MAX_GAP_S for a, b in zip(last, last[1:])):
            return None
        return last[-1][0] if len({s for s, _ in last}) == 1 else None

    def quiet(self, now):
        s = self.latest
        return bool(s and s.active and 0 <= now - s.observed_at < 2.5 and s.on_pit_road is False
            and s.steering is not None and abs(s.steering) < .06
            and s.throttle is not None and s.throttle >= .75
            and s.brake is not None and s.brake <= .03
            and s.speed_mps is not None and s.speed_mps >= 8)

    def applicable(self, event, now):
        key = event['key']
        if key in ('trend_front','trend_rear','input_front','input_rear','temperature_recovered'):
            return bool(self.latest and 0 <= now-self.latest.observed_at < 2.5 and self.coaching.candidate == key)
        if key == 'temperature_cold':
            return all(self.state('temperature', w, now) == 'cold' for w in WHEELS)
        if key == 'tires_ok':
            return all(self.state(f, w, now) == 'ok' for f in ('pressure', 'temperature') for w in WHEELS)
        field, state, wheel = key.split('_')
        if state == 'low' and self.warming(wheel, now):
            return False
        return self.state(field, wheel, now) == state

    def _track_warmup(self, snapshot):
        if snapshot.on_pit_road is True:
            self.on_pit_road = True
        elif snapshot.on_pit_road is False and self.on_pit_road:
            # Leaving the pit lane: fresh or cooled tires warm up again.
            self.on_pit_road, self.warmed, self.warm_start_lap = False, set(), snapshot.lap
        if self.warm_start_lap is None:
            self.warm_start_lap = snapshot.lap

    def warming(self, wheel, now):
        """True while this wheel's 'pressure low' is only the normal warm-up."""
        if wheel in self.warmed:
            return False
        lap = self.latest.lap if self.latest else None
        return lap is None or self.warm_start_lap is None or lap - self.warm_start_lap < WARMUP_LAPS

    def current(self, now):
        event = self.pending
        if event and (now >= event['expires_at'] or not self.applicable(event, now)):
            self.pending = event = None
        if event:
            return {**event, 'quiet': self.quiet(now), 'remaining_s': event['expires_at'] - now}
        advice = self.advice_event
        if advice and now >= advice['expires_at']:
            self.advice_event = advice = None
        return {**advice, 'quiet': self._advice_quiet(advice, now), 'remaining_s': advice['expires_at'] - now} if advice else None

    def ack(self, identifier, now):
        event = self.current(now)
        if not event or event['id'] != identifier or not event['quiet']:
            return False
        if self.advice_event and event['id'] == self.advice_event['id']:
            self.advice_sent.add((event['key'], event['token']))
            self.advice_event = None
            return True
        self.sent[event.get('family',event['key'])] = now, self.latest.lap
        self.coaching.ack(event['key'])
        self.ok_sent |= event['key'] == 'tires_ok'
        self.pending = None
        return True
