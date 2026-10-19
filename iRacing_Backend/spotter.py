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


class Spotter:
    FRESH_SECONDS = .5
    EVENT_SECONDS = .6

    def __init__(self):
        self.repeat_seconds, self.clear_delay = 3., .15
        self.serial = 0
        self.events = deque(maxlen=64)
        self.variants = {}
        self.reset()

    def reset(self):
        self.run_id = None
        self.raw = self.confirmed = self.candidate = None
        self.candidate_since = self.last_sample = self.last_session = self.last_call = None
        self.events.clear()

    def ingest(self, sample, run_id, now):
        raw = sample.get('car_left_right')
        session = sample.get('session_time')
        valid = (type(raw) in (int, float) and raw in SIDES
                 and sample.get('on_track') is True and sample.get('is_replay') is False
                 and sample.get('on_pit_road') is False)
        gap = self.last_sample is not None and now - self.last_sample > self.FRESH_SECONDS
        session_gap = (self.last_session is not None and session is not None
                       and (session < self.last_session or session - self.last_session > self.FRESH_SECONDS))
        if self.run_id != run_id or gap or session_gap or not valid:
            self.reset()
        if not valid:
            return
        self.run_id, self.raw, self.last_sample, self.last_session = run_id, int(raw), now, session
        if raw != self.candidate:
            self.candidate, self.candidate_since = int(raw), now
        delay = self.clear_delay if raw == 1 else .05
        if now - self.candidate_since + 1e-9 < delay:
            return
        if raw != self.confirmed:
            previous = self.confirmed
            self.confirmed = int(raw)
            key = self._transition(previous, int(raw))
            if key:
                self._emit(key, now)
        elif raw != 1 and self.last_call is not None and now - self.last_call >= self.repeat_seconds:
            self._emit(HOLD_KEYS[int(raw)], now)

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
