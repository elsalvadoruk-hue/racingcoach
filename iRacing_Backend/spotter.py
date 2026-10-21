"""Local overlap state machine. No LLM, SDK writes, or race-line inference."""
from collections import deque
import base64
import hashlib
import json
import os
from pathlib import Path
import threading
from race_radio import STATIC_CALLS
from radio_audio import spoken_form
from radio_phrases import SPOTTER_VOICES  # noqa: F401  (the voices the spotter can be given)
import radio_variations

# iRacing CarLeftRight: 0=off, 1=clear, 2=left, 3=right,
# 4=one on each side, 5=two left, 6=two right. All classes count.
PHRASES = {
    'left': '왼쪽 차량.', 'right': '오른쪽 차량.',
    'both': '양쪽 차량. 라인 유지.',
    'two_left': '왼쪽 두 대.', 'two_right': '오른쪽 두 대.',
    'left_only': '왼쪽만 남았습니다.', 'right_only': '오른쪽만 남았습니다.',
    'left_one': '왼쪽 한 대.', 'right_one': '오른쪽 한 대.',
    'clear_left': '왼쪽 클리어.', 'clear_right': '오른쪽 클리어.',
    'clear_all': '양쪽 클리어.', 'hold': '옆에 있습니다. 라인 유지.',
    'hold_left': '계속 왼쪽.', 'hold_right': '계속 오른쪽.',
    'hold_both': '계속 양쪽.',
    'hold_two_left': '계속 왼쪽 두 대.', 'hold_two_right': '계속 오른쪽 두 대.',
    'unavailable': '스포터 안내 중지.', 'test': '스포터입니다. 좌우 차량을 안내하겠습니다.',
}
PHRASES.update({key: choices[0] for key, choices in STATIC_CALLS.items()})
VARIANTS = {key: (text,) for key, text in PHRASES.items()}
VARIANTS.update(STATIC_CALLS)
VARIANTS.update({
    'left': ('왼쪽 차량.', '왼쪽에 있습니다.'),
    'right': ('오른쪽 차량.', '오른쪽에 있습니다.'),
    'both': ('양쪽 차량. 라인 유지.', '양쪽에 있습니다. 라인 유지.'),
    'two_left': ('왼쪽 두 대.', '왼쪽에 두 대 있습니다.'),
    'two_right': ('오른쪽 두 대.', '오른쪽에 두 대 있습니다.'),
    'clear_left': ('왼쪽 클리어.', '왼쪽 비었습니다.'),
    'clear_right': ('오른쪽 클리어.', '오른쪽 비었습니다.'),
    'clear_all': ('양쪽 클리어.', '양쪽 비었습니다.'),
    'hold': ('옆에 있습니다. 라인 유지.', '계속 옆에 있습니다.', '라인 유지하세요. 옆에 차량 있습니다.'),
})
VARIANTS.update({key: tuple(VARIANTS[key]) + more for key, more in radio_variations.SPOTTER.items()})
STATE_KEYS = {2: 'left', 3: 'right', 4: 'both', 5: 'two_left', 6: 'two_right'}
HOLD_KEYS = {state: 'hold_' + key for state, key in STATE_KEYS.items()}
SIDES = {1: (0, 0), 2: (1, 0), 3: (0, 1), 4: (1, 1), 5: (2, 0), 6: (0, 2)}


def _arrives(old, new):
    """Whether a car is new beside us on either side (not one leaving)."""
    before, after = SIDES.get(old, (0, 0)), SIDES[new]
    return any(a > b for a, b in zip(after, before))


class Spotter:
    """Side calls for cars that stay beside us.

    2026-10-21: the spotter called every overlap the sim reported, so cars sweeping past in under a second
    were called "왼쪽 차량" and "왼쪽 클리어" as they went (an LMU practice: 25 calls in 3 minutes, 16 of 23
    overlaps gone within 0.8 s). A car is now called only once it has stayed beside us ENTER_SECONDS and is
    not moving along our car faster than PASSING_MPS; one never called is never called clear.
    """
    FRESH_SECONDS = .5
    EVENT_SECONDS = .6
    ENTER_SECONDS = .4      # a car beside us this long is staying; one sweeping past is gone sooner
    PASSING_MPS = 7.        # faster along our car than this (25 km/h) it is past before the call ends
    UNKNOWN_SECONDS = 1.    # the side picture may drop out this long (LMU coverage) without starting over
    # "계속 왼쪽" every few seconds of a long side-by-side was most of the side calls in iRacing races
    # (Le Mans lap 1: 32 in 100 s). Two repeats, then one just before each corner while it lasts.
    HOLD_REPEATS = 2
    CORNER_SECONDS = 3.     # a repeat this close to the next braking or turning
    CORNER_REARM_S = 6.     # and the next corner counts once the road ahead is this long again
    # 2026-10-21 (driver's decision, both games): a car we are passing is not called; it is called
    # once it is no slower than us (it may stay beside us), and the pass ends with a clear only.
    WE_PASS_MPS = .5        # slower than us by more than this: we are passing it (measurement noise below)

    def __init__(self):
        self.repeat_seconds, self.clear_delay = 3., .15
        self.serial = 0
        self.events = deque(maxlen=64)
        self.variants = {}
        self.reset()

    def reset(self):
        self.run_id = None
        self.raw = self.confirmed = self.candidate = None
        self.candidate_since = self.last_sample = self.last_session = self.last_call = self.unknown_since = None
        self.holds, self.corner_armed = 0, True
        self.passing = None     # side state of a car we are passing, never called
        self.events.clear()

    def ingest(self, sample, run_id, now):
        raw = sample.get('car_left_right')
        session = sample.get('session_time')
        driving = (sample.get('on_track') is True and sample.get('is_replay') is False
                   and sample.get('on_pit_road') is False)
        known = type(raw) in (int, float) and raw in SIDES
        # 0 is the sim's own "off" (parked, crawling): a state, not a dropout.
        off = type(raw) in (int, float) and raw == 0
        gap = self.last_sample is not None and now - self.last_sample > self.FRESH_SECONDS
        session_gap = (self.last_session is not None and session is not None
                       and (session < self.last_session or session - self.last_session > self.FRESH_SECONDS))
        if self.run_id != run_id or gap or session_gap or not driving or off:
            self.reset()
        if not driving or off:
            return
        self.run_id, self.last_sample, self.last_session = run_id, now, session
        if not known:
            # A missing side picture says nothing about the cars: the call in force stays, silently, and
            # a longer loss starts over without a clear (unknown never becomes "clear").
            self.raw = None
            self.unknown_since = self.unknown_since if self.unknown_since is not None else now
            if now - self.unknown_since > self.UNKNOWN_SECONDS:
                self.confirmed = self.candidate = self.candidate_since = self.last_call = self.passing = None
                self.events.clear()
            return
        self.unknown_since = None
        raw = int(raw)
        self.raw = raw
        if raw != self.candidate:
            self.candidate, self.candidate_since = raw, now
        arriving = raw != 1 and _arrives(self.confirmed, raw)
        delay = self.ENTER_SECONDS if arriving else self.clear_delay if raw == 1 or raw != self.confirmed else .05
        if now - self.candidate_since + 1e-9 < delay:
            return
        if raw == 1 and self.passing is not None and self.confirmed in (None, 1):
            # The pass is done: only the clear is said.
            key, self.passing, self.confirmed = self._transition(self.passing, 1), None, 1
            if key:
                self._emit(key, now)
            return
        if raw != self.confirmed:
            closing = sample.get('side_closing_mps')
            if (arriving and type(closing) in (int, float) and closing == closing
                    and closing > self.PASSING_MPS):
                return          # sweeping past; called if it settles beside us
            relative = sample.get('side_relative_mps')
            if (arriving and type(relative) in (int, float) and relative == relative
                    and relative < -self.WE_PASS_MPS):
                self.passing = raw
                return          # we are passing it; called if it comes back to our speed
            self.passing = None
            previous = self.confirmed
            self.confirmed = raw
            key = self._transition(previous, raw)
            self.holds, self.corner_armed = 0, True
            if key:
                self._emit(key, now)
        elif raw != 1 and self.last_call is not None:
            corner = sample.get('corner_in_s')
            corner = corner if type(corner) in (int, float) and corner == corner else None
            if corner is not None and corner >= self.CORNER_REARM_S:
                self.corner_armed = True
            before_corner = corner is not None and corner <= self.CORNER_SECONDS and self.corner_armed
            if now - self.last_call >= self.repeat_seconds and (self.holds < self.HOLD_REPEATS or before_corner):
                self.holds += 1
                self.corner_armed = self.corner_armed and not before_corner
                self._emit(HOLD_KEYS[raw], now)

    def _transition(self, old, new):
        if new == 1:
            # Unknown / off / reconnection must never manufacture a clear call.
            if old in (2, 5):
                return 'clear_left'
            if old in (3, 6):
                return 'clear_right'
            return 'clear_all' if old == 4 else None
        if old == 4 and new in (2, 3):
            return 'left_only' if new == 2 else 'right_only'
        if old == 5 and new == 2:
            return 'left_one'
        if old == 6 and new == 3:
            return 'right_one'
        return STATE_KEYS[new]

    def _emit(self, key, now):
        self.serial += 1
        self.last_call = now
        variant = self.variants.get(key, 0) % len(VARIANTS[key])
        self.variants[key] = variant + 1
        self.events.append({'serial': self.serial, 'key': key, 'text': VARIANTS[key][variant], 'variant': variant,
                            'state': self.confirmed, 'run_id': self.run_id, 'at': now})

    def view(self, now, after=0, connected=True):
        active = bool(connected and self.last_sample is not None
                      and 0 <= now - self.last_sample < self.FRESH_SECONDS and self.raw in SIDES)
        events = [{**e, 'expires_in_ms': max(0, round((self.EVENT_SECONDS - (now - e['at'])) * 1000))}
                  for e in self.events if active and e['serial'] > after
                  and e['state'] == self.raw == self.confirmed and e['run_id'] == self.run_id
                  and 0 <= now - e['at'] < self.EVENT_SECONDS]
        return {'active': active, 'state': self.confirmed if active else None,
                'sent_at': now,
                'raw_state': self.raw if active else None, 'run_id': self.run_id if active else None,
                'occupied': active and (self.raw != 1 or self.confirmed in STATE_KEYS), 'serial': self.serial, 'events': events,
                'current_key': STATE_KEYS.get(self.confirmed) if active and self.raw == self.confirmed else None,
                'state_label': PHRASES.get(STATE_KEYS.get(self.raw), '주변 차량 겹침 없음') if active else '차량 데이터 대기'}


class AlongsideSpeed:
    """How fast the cars beside us move along our car, from every car's lap position (iRacing, 30 Hz).

    The sim says a car is beside us but not which one or how fast. The cars within WINDOW_M along the
    track are the ones it can be; the slowest of them decides (a car settling beside us must be called).
    """
    WINDOW_M = 6.     # along-track distance at which a car can overlap ours
    NEAR_M = 40.      # cars tracked, so their speed is known before they arrive
    SPAN_S = .25      # speed over at least this long; single 30 Hz steps are too noisy
    KEEP_S = 1.

    def __init__(self):
        self.reset()

    def reset(self):
        self.tracks = {}
        self.relative = None   # signed speed of that slowest car: + it pulls ahead, - we pass it

    def __call__(self, now, own_pct, cars, length_m):
        """cars: {car index: lap fraction} of the other cars on track. Metres per second, or None."""
        if type(own_pct) not in (int, float) or not 0 <= own_pct < 1 or type(length_m) not in (int, float) or not length_m > 100:
            self.reset()
            return None
        tracks, slowest, relative = {}, None, None
        for idx, pct in cars.items():
            if type(pct) not in (int, float) or not 0 <= pct < 1:
                continue
            gap = ((pct - own_pct + .5) % 1 - .5) * length_m
            if abs(gap) > self.NEAR_M:
                continue
            track = self.tracks.get(idx) or deque()
            track.append((now, gap))
            while track and now - track[0][0] > self.KEEP_S:
                track.popleft()
            tracks[idx] = track
            if abs(gap) <= self.WINDOW_M:
                past = next((point for point in reversed(track) if now - point[0] >= self.SPAN_S), None)
                if past is not None:
                    signed = (gap - past[1]) / (now - past[0])
                    if slowest is None or abs(signed) < slowest:
                        slowest, relative = abs(signed), signed
        self.tracks, self.relative = tracks, relative
        return slowest


class SpotterAudio:
    """Only /prepare synthesizes missing clips. Racing uses memory/disk cache."""
    def __init__(self, speech, voice):
        self.speech, self.voice = speech, voice
        self.directory = Path(os.getenv('IRACING_SPOTTER_AUDIO_DIR', str(Path(__file__).resolve().parent / 'spotter_audio')))
        self.lock = threading.Lock()

    def prepare(self):
        with self.lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            clips = {}
            for key, texts in VARIANTS.items():
                choices = []
                for text in texts:
                    digest = hashlib.sha256(json.dumps([self.voice(), spoken_form(text)], ensure_ascii=False).encode()).hexdigest()
                    path = self.directory / f'{digest}.mp3'
                    data = path.read_bytes() if path.exists() else b''
                    if len(data) < 100:
                        data = base64.b64decode(self.speech.synthesize(text, role='spotter'), validate=True)
                        if len(data) < 100:
                            raise ValueError('스포터 음성을 준비하지 못했습니다. 음성 연결을 확인하세요.')
                        temporary = path.with_suffix('.tmp')
                        temporary.write_bytes(data)
                        temporary.replace(path)
                    choices.append({'text': text, 'audio_base64': base64.b64encode(data).decode('ascii')})
                clips[key] = {**choices[0], 'variants': choices}
            return {'voice': self.voice(), 'clips': clips, 'cached': True, 'clip_count': sum(len(v) for v in VARIANTS.values())}
