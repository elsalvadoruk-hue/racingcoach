"""Measured basic race calls, variant selection, expiry and specialist handoffs."""
from collections import deque
import math
import statistics
from radio_policy import quiet_window, speech_ok
from spotter_context import TrafficApproach, PitBoxGuide, HazardWatch, progress
from radio_audio import speech_fields
import radio_phrases
import radio_variations
from qualifying_traffic import QualifyingTraffic, is_open_qualifying


STATIC_CALLS = {
    'traffic_faster_behind': ('뒤에서 빠른 클래스 접근.',),
    'traffic_slower_ahead': ('앞의 느린 클래스에 접근 중.',),
    'traffic_lapping_behind': ('뒤에서 한 랩 이상 앞선 차량 접근.',),
    'traffic_lapped_ahead': ('앞의 한 랩 이상 뒤처진 차량에 접근 중.',),
    'hazard_off_ahead': ('전방 트랙 밖 차량. 주의.', '전방에 트랙을 벗어난 차량이 있습니다.'),
    'hazard_slow_ahead': ('전방 느린 차량. 주의.', '전방에 매우 느린 차량이 있습니다.'),
    'close_behind': ('뒤에 바로 붙었습니다.', '뒤차가 바로 뒤에 있습니다.'),
    'green': ('그린 플래그.', '그린 플래그입니다.'),
    'yellow': ('옐로 플래그. 전방 주의.', '옐로 플래그입니다. 주의하세요.'),
    'caution': ('풀코스 옐로. 게임 지시를 따르세요.', '코션입니다. 페이스카와 게임 지시를 확인하세요.'),
    'red': ('레드 플래그. 중단 지시를 확인하세요.', '레드 플래그입니다. 게임 지시를 확인하세요.'),
    'blue': ('블루 플래그. 뒤쪽 차량 확인.', '블루 플래그입니다. 뒤에서 접근합니다.'),
    'debris': ('데브리 플래그. 노면 주의.', '데브리 플래그입니다. 노면 상태를 확인하세요.'),
    'white': ('화이트 플래그. 마지막 랩 표시 확인.', '화이트 플래그입니다. 게임 지시를 확인하세요.'),
    'black': ('블랙 플래그. 패널티 지시 확인.', '블랙 플래그입니다. 게임 지시를 확인하세요.'),
    'dq': ('디스퀄리파이드. 실격 표시입니다.', '디스퀄리파이드 표시가 감지됐습니다.'),
    'repair': ('레드 닷. 피트를 준비하세요.', '레드 닷입니다. 차량 수리 지시 확인.'),
    'finish': ('체커드 플래그. 경기 종료 표시 확인.', '체커드 플래그입니다. 종료 지시를 확인하세요.'),
    'one_to_green': ('그린까지 한 랩입니다.', '한 랩 뒤 그린입니다. 출발을 준비하세요.'),
    'start_ready': ('출발 신호 준비.', '출발 신호를 확인하세요.'),
    'start_set': ('출발 대기. 신호를 기다리세요.', '대기하세요. 출발 신호 전입니다.'),
    'pit_enter': ('피트로드 진입. 제한속도 확인.', '피트 진입입니다. 속도를 확인하세요.'),
    'pit_exit': ('피트로드 벗어났습니다. 합류 차량 확인.', '피트 출구입니다. 합류 전에 주변을 확인하세요.'),
    'pit_speed': ('피트 제한속도 초과. 감속하세요.', '피트에서 너무 빠릅니다. 감속하세요.'),
    'limiter_off': ('피트 리미터가 꺼져 있습니다.', '리미터가 꺼졌습니다. 피트 속도를 확인하세요.'),
    'service_start': ('피트 작업 시작합니다.', '피트 서비스 중입니다.'),
    'service_done': ('피트 작업 완료. 출구 차량 확인.', '작업이 끝났습니다. 출구를 확인하세요.'),
    'stall_left': ('피트 정차 위치가 왼쪽으로 치우쳤습니다.', '차량을 조금 오른쪽으로 옮기세요.'),
    'stall_right': ('피트 정차 위치가 오른쪽으로 치우쳤습니다.', '차량을 조금 왼쪽으로 옮기세요.'),
    'stall_forward': ('피트 정차 위치가 너무 앞입니다.', '조금 뒤로 이동하세요.'),
    'stall_back': ('피트 정차 위치가 너무 뒤입니다.', '조금 앞으로 이동하세요.'),
    'stall_angle': ('피트 정차 각도를 맞추세요.', '피트 작업을 위해 차량 각도를 바로잡으세요.'),
    'service_unavailable': ('해당 피트 작업을 할 수 없습니다.', '피트 작업 불가 표시입니다. 게임을 확인하세요.'),
    'water_temp': ('냉각수 온도 경고.', '수온 경고가 켜졌습니다.'),
    'oil_temp': ('오일 온도 경고.', '유온 경고가 켜졌습니다.'),
    'oil_pressure': ('오일 압력 경고.', '오일 압력 경고가 켜졌습니다.'),
    'fuel_pressure': ('연료 압력 경고.', '연료 압력 경고가 켜졌습니다.'),
    'mandatory_repair': ('필수 수리 표시입니다. 피트를 확인하세요.', '필수 수리가 필요합니다.'),
    'optional_repair': ('선택 수리 필요 신호입니다.', '선택 수리 표시가 켜졌습니다.'),
    'fuel_low': ('연료가 얼마 남지 않았습니다.', '연료 잔량을 확인하세요.'),
    'ten_laps': ('경기 종료까지 열 랩입니다.', '열 랩 남았습니다.'),
    'five_laps': ('경기 종료까지 다섯 랩입니다.', '다섯 랩 남았습니다.'),
}
STATIC_CALLS.update({f'pit_box_{distance}': (f'피트박스 약 {distance}미터.',) + tuple(text.format(distance=distance) for text in radio_variations.PIT_BOX)
                     for distance in PitBoxGuide.THRESHOLDS})
# The second pack of wordings goes after the first, so the first wording of every call stays where it was.
STATIC_CALLS.update({key: tuple(STATIC_CALLS[key]) + more for key, more in radio_variations.STATIC.items()})


def num(value):
    return float(value) if type(value) in (int, float) and math.isfinite(value) else None


def duration(seconds):
    seconds = num(seconds)
    if seconds is None or not 0 < seconds < 86400:
        return None
    minutes = int(seconds // 60)
    remainder = round(seconds - minutes * 60, 2)
    if remainder >= 60:
        minutes += 1
        remainder = 0
    return (f'{minutes}분 ' if minutes else '') + f'{remainder:.2f}초'


QUALI_KEYS = ('lap_time', 'personal_best', 'corner_focus', 'straight_brief')
QUALI_SILENT = ('traffic_', 'close_', 'hazard_', 'class_traffic')
# Flags about other cars or the race start mean nothing on a lone qualifying lap. Green, white and
# the chequered flag are called; so are the driver's own black flag and repair signal.
QUALI_SILENT_FLAGS = ('yellow', 'caution', 'blue', 'debris', 'red', 'one_to_green', 'start_ready', 'start_set')


TRAFFIC_REARM_S = 4.       # a car that was called is called again only after it dropped this far back (or got past)
CLOSE_REARM_S = 1.5
LAP_CALL_SHARE = .8        # a lap time may wait this share of the next lap for room on the radio
LAP_CALL_MAX_S = 115.      # the screen keeps a waiting call for two minutes at most
POSITION_CALL_S = 40.      # a place is only said while it still holds, so it can wait out a run of corners



# Low-fuel warning: about half a lap of the car's own clean-lap use (fuel_l_per_lap, the most used of the
# last clean laps), never later than FUEL_LOW_MIN_L, which is also the limit before a lap is measured.
# Ignore brief SDK blue flags. First-lap startup flags can last longer than this
# (Laguna Seca 2026-10-18: 1.9 s), so a first-lap race also needs measured lapping traffic.
BLUE_HOLD_S = 1.5
# Pit lane speed: with the limiter on, the speed sits up to about 1.5 km/h over the limit, and in the driver's
# 244 recorded pit visits nothing under 3 km/h over ever brought a penalty; both penalties for speed were far
# over (entering at +20 km/h, +32 km/h without the limiter). Called only beyond this margin.
PIT_SPEED_MARGIN_KPH = 3.

FUEL_LOW_LAPS = .5
FUEL_LOW_MIN_L = .5


def fuel_low_limit(sample):
    use = num(sample.get('fuel_l_per_lap'))
    return max(FUEL_LOW_MIN_L, FUEL_LOW_LAPS * use) if use is not None and use > 0 else FUEL_LOW_MIN_L

class RaceRadio:
    """Only real changes produce calls; uncertain gaps establish a new baseline."""
    POSITION_SETTLE_S = 5   # a place must hold this long before it is said
    def __init__(self):
        self.traffic_enabled = True
        self.pit_box_enabled = False
        self.qualifying = False
        self.serial = 0
        self.variant_counts = {}
        self.spoken_counts = {}
        self.history = deque(maxlen=20)
        self.reset()

    def reset(self):
        self.run_id = None
        self.corner_revision = None
        self.fuel_low_stage = 0
        self.blue_since, self.blue_called = None, False
        self.latest, self.metadata = {}, {}
        self.metadata_at = None
        self.pending = deque(maxlen=48)
        self.last_sample = None
        self.sent = {}
        self.result = None
        self.control_advice_allowed = False
        self.setup_advice_allowed = False
        self.car_calls = {}
        self.laps = deque(maxlen=5)
        self.briefed = False
        self.start_armed = False   # the race's grid/formation was seen; green is said once, at the start
        self.history.clear()
        self.tainted = False
        self.pending_lap = None
        self.position_candidate = None
        self.position_since = None
        self.position_changed = False
        self.position_said = None
        self.lap_position = None
        self.quali_lap = None
        self.qualifying = False
        self.open_qualifying = False
        self.quali_traffic = QualifyingTraffic()
        self.quali_traffic_lap = None
        self.lap_done_at = None
        self.quali_called = None
        self.result_called = False
        self.damage_check_at = None
        self.wet_level = self.rain_advice = self.temp_reference = None
        self.dry_laps = deque(maxlen=5)
        self.traffic = TrafficApproach()
        self.hazard = HazardWatch()
        self.pit_box = PitBoxGuide()

    def emit(self, key, now, text=None, role='spotter', priority=1, ttl=18, condition=None, cooldown=30, group=None, delay=0, speech_key=None, speech_text=None,
             speech_parts=None):
        # Lone/unknown qualifying retains its original filter. Shared-track warnings and
        # the measured gap advisor are enabled ONLY for an explicitly Open Qualify session.
        silent_flags = ('one_to_green', 'start_ready', 'start_set') if self.open_qualifying else QUALI_SILENT_FLAGS
        if self.qualifying and ((not self.open_qualifying and key.startswith(QUALI_SILENT)) or key in silent_flags
                                or (priority < 2 and not key.startswith('quali_') and key not in QUALI_KEYS)):
            return
        if now - self.sent.get(key, -math.inf) < cooldown:
            return
        self.sent[key] = now
        # New lap/position/time messages supersede old queued announcements.
        group = group or key
        self.pending = deque((e for e in self.pending if e['group'] != group), maxlen=48)
        self.serial += 1
        count = self.variant_counts.get(key, 0)
        self.variant_counts[key] = count + 1
        variants = STATIC_CALLS.get(key)
        variants = variants or (text if isinstance(text, (tuple, list)) else None)
        variant = count % len(variants) if variants else 0
        if key not in STATIC_CALLS:
            speech_key = speech_key or ('time_remaining' if key.startswith('time_') else 'laps_remaining' if key.startswith('laps_') else key)
        speech = speech_fields(speech_key, count)
        if speech_text:
            # A whole sentence with its numbers, spoken by this role and cached per wording.
            speech = {'speech_key': None, 'spoken_text': speech_text, 'speech_text': speech_text}
        if speech_parts and len(speech_parts) > 1:
            # Whole sentences, each with its own cached clip, said one after another as a single call.
            speech['speech_parts'] = list(speech_parts)
        self.pending.append({'id': f'{self.run_id}:{self.serial}', 'serial': self.serial, 'key': key,
                             'text': variants[variant] if variants else text, 'variant': variant,
                             'engineer': role, 'priority': priority, 'at': now, 'not_before': now + delay,
                             'expires_at': now + ttl + delay, 'condition': condition, 'group': group,
                             'run_id': self.run_id, 'cached': key in STATIC_CALLS,
                             'brief': bool(speech['spoken_text']) or key in ('lap_time', 'personal_best', 'position') or key.startswith(('time_', 'laps_')),
                             **speech})

    def wording(self, phrase, **values):
        """The next wording of a spoken sentence; the screen text of the event stays as it is."""
        count = self.spoken_counts.get(phrase, 0)
        self.spoken_counts[phrase] = count + 1
        return radio_phrases.build(phrase, count, **values)['text']

    def say(self, key, now, values=None, **options):
        """A varied engineer call: one of its wordings with the measured values filled in."""
        built = radio_phrases.build(key, self.variant_counts.get(key, 0), **(values or {}))
        self.emit(key, now, built['text'], role=options.pop('role', built['engineer']), speech_text=built['text'], **options)

    def ingest(self, sample, run_id, now, metadata=None):
        if (self.run_id != run_id or self.last_sample is not None and
                (now - self.last_sample > .5 or sample['session_time'] < self.latest.get('session_time', 0))):
            self.reset()
        self.run_id = run_id
        if metadata is not None:
            self.metadata = dict(metadata)
            self.metadata_at = now
            self.qualifying = 'qual' in str(metadata.get('session_type') or '').lower()
            self.open_qualifying = is_open_qualifying(metadata)
            self.quali_traffic.ingest(metadata, sample, now)
        previous = self.latest
        self.latest, self.last_sample = dict(sample), now
        if sample.get('is_replay') is not False or sample.get('on_track') is not True:
            self.pending.clear()
            self.blue_since, self.blue_called = None, False
            self.traffic.reset()
            self.hazard.reset()
            self.pit_box.reset()
            return
        if metadata is not None:
            if sample.get('on_pit_road') is False and sample.get('session_state') == 4 and not int(sample.get('session_flags') or 0) & 0xC119:
                self.traffic.ingest(metadata, now)
            else:
                self.traffic.reset()
        if sample.get('on_pit_road') is not False or sample.get('session_state') != 4 or int(sample.get('session_flags') or 0) & 0xC119:
            self.traffic.reset()
        self._qualifying_gap(sample, now)
        if self.traffic_enabled:
            for car in self.traffic.view(now)['nearby']:
                # One call for one approach. It starts again once the car got past or dropped back.
                for said in [k for k in self.car_calls if k[0] == car['idx']]:
                    if not said[1].endswith(car['side']) or car['gap_estimate_s'] >= (CLOSE_REARM_S if said[1].startswith('close') else TRAFFIC_REARM_S):
                        del self.car_calls[said]
                key = car['call_key'] or car.get('close_key')
                if not key or (car['idx'], key) in self.car_calls:
                    continue
                if car['call_key']:
                    self.emit(key, now, priority=2, ttl=2, condition=('traffic', (car['idx'], key)),
                              cooldown=30, group='traffic_' + car['side'])
                else:
                    self.emit(key, now, priority=2, ttl=2, condition=('close', car['idx']), cooldown=20, group='traffic_behind')
                if self.pending and self.pending[-1]['key'] == key and self.pending[-1]['at'] == now:
                    self.pending[-1]['car'] = (car['idx'], key)
            near = {car['idx'] for car in self.traffic.view(now)['nearby']}
            self.car_calls = {k: v for k, v in self.car_calls.items() if k[0] in near}
        # A local yellow still needs the location; a full-course caution or red does not.
        racing = (sample.get('on_pit_road') is False and sample.get('session_state') == 4
                  and not int(sample.get('session_flags') or 0) & 0xC011)
        if not racing:
            self.hazard.reset()
        elif metadata is not None:
            self.hazard.ingest(metadata, now)
        for hazard in self.hazard.view(now)[:1]:
            self.emit(hazard['kind'], now, priority=2, ttl=3, condition=('hazard', (hazard['idx'], hazard['kind'])),
                      cooldown=15, group='hazard')
        flags, old_flags = num(sample.get('session_flags')), num(previous.get('session_flags'))
        self._blue_flag(sample, now)
        if flags is not None:
            rising = int(flags) & ~int(old_flags or 0)
            table = [(0x1, 'finish', 2), (0x2, 'white', 2), (0x10, 'red', 2),
                     (0x40, 'debris', 2), (0x10000, 'black', 2), (0x20000, 'dq', 2),
                     (0x100000, 'repair', 2), (0x200, 'one_to_green', 1),
                     (0x800, 'ten_laps', 1), (0x1000, 'five_laps', 1),
                     (0x20000000, 'start_ready', 1), (0x40000000, 'start_set', 2)]
            for bit, key, priority in table:
                if bit == 0x1 and str(self.metadata.get('session_type', '')).lower() == 'race':
                    continue    # the leader's chequered flag; ours is the result as we cross the line
                if rising & bit:
                    # Every flag is the sim's own signal, debris included: while it lies there the sim
                    # raises the flag on each approach, and each time it is called (the driver's decision).
                    self.emit(key, now, priority=priority, condition=('flag', bit), cooldown=10)
            caution_mask = 0xC000
            yellow_mask = 0x108
            if int(flags) & caution_mask and not int(old_flags or 0) & caution_mask:
                self.emit('caution', now, priority=2, condition=('flag', caution_mask))
            elif int(flags) & yellow_mask and not int(old_flags or 0) & (yellow_mask | caution_mask):
                self.emit('yellow', now, priority=2, condition=('flag', yellow_mask))
        state = num(sample.get('session_state'))
        is_race = str(self.metadata.get('session_type', '')).lower() == 'race'
        # Green only starts the race (the driver's decision): never in practice or qualifying, never
        # after a caution, and never again when the link resets mid-race. LMU keeps the green bit
        # up for the whole green phase, so a reconnect would otherwise announce it again.
        if is_race and state in (1, 2, 3):
            self.start_armed = True
        if (self.start_armed and state == 4 and flags is not None and int(flags) & (0x4 | 0x80000000)
                and not int(flags) & (0xC000 | 0x10)):
            self.start_armed = False
            self.emit('green', now, priority=2, condition=('flag', 0x4 | 0x80000000), cooldown=10)
        if is_race and state in (1, 2, 3) and not self.briefed:
            self.briefed = True
            self.emit('briefing', now, self.briefing(), ttl=120, condition=('pre_race', None), cooldown=3600)
        if state == 4 and previous.get('session_state') in (1, 2, 3) and flags is not None and not int(flags) & (0x4 | 0x80000000):
            self.emit('race_started', now, '경기 상태로 전환됐습니다. 출발 신호는 게임에서 확인하세요.', priority=1, cooldown=3600)
        self._pit(sample, previous, now)
        threshold = self.pit_box.ingest(sample, previous, self.metadata, now)
        if threshold is not None and self.pit_box_enabled:
            self.emit(f'pit_box_{threshold}', now, priority=2, ttl=.8,
                      condition=('pit_box', threshold), cooldown=0, group='pit_box')
        self._laps(sample, previous, now)
        self._position(sample, now)
        self._warnings(sample, previous, now)
        self._weather(sample, now)
        self._results(sample, previous, now, metadata)
        if self.damage_check_at is not None and now >= self.damage_check_at:
            self.damage_check_at = None
            warnings = num(sample.get('engine_warnings'))
            # Only the absence of the repair signals is stated, never the absence of damage.
            if warnings is not None and flags is not None and not int(warnings) & 0x180 and not int(flags) & 0x100000:
                self.say('no_repair', now, role='race', condition=('racing', None), cooldown=120, ttl=12)
        remaining = num(sample.get('session_time_remaining_s'))
        old_remaining = num(previous.get('session_time_remaining_s'))
        if state == 4 and remaining is not None and old_remaining is not None:
            for threshold in (600, 300, 120, 60):
                if old_remaining > threshold >= remaining:
                    self.emit(f'time_{threshold}', now, f'경기 시간 약 {threshold // 60}분 남았습니다. 종료 후 마지막 랩은 게임 지시를 확인하세요.', condition=('racing', None), group='time_remaining', cooldown=3600,
                              speech_text=self.wording('time_left', min=threshold // 60))
        laps_left = num(sample.get('session_laps_remaining'))
        old_laps = num(previous.get('session_laps_remaining'))
        if state == 4 and laps_left is not None and old_laps is not None:
            for count in (10, 5, 2, 1):
                if old_laps > count >= laps_left and 0 < laps_left < 32767:
                    # Prefer the official flag when it carries the same information.
                    if not (flags is not None and int(flags) & {10: 0x800, 5: 0x1000, 1: 0x2}.get(count, 0)):
                        self.emit(f'laps_{count}', now, f'내 차량 기준 {count}랩 남았다는 표시입니다.', condition=('laps_left', count), group='laps_remaining', cooldown=3600,
                                  speech_text=self.wording('laps_left', laps=count))

    def _weather(self, sample, now):
        wet, declared = num(sample.get('track_wetness')), sample.get('declared_wet')
        temp = num(sample.get('track_temp_c'))
        if temp is not None:
            if self.temp_reference is None:
                self.temp_reference = temp
            elif abs(temp - self.temp_reference) >= 4:
                self.say('track_temp_down' if temp < self.temp_reference else 'track_temp_up', now,
                         {'deg': abs(temp - self.temp_reference)}, cooldown=300, ttl=30, group='track_temp')
                self.temp_reference = temp
        if wet is None or not 1 <= wet <= 7:  # 1 dry ... 7 extremely wet; 0 is unknown
            return
        old, self.wet_level = self.wet_level, wet
        if old is not None and old <= 2 < wet:
            self.say('track_wet_start', now, priority=2, condition=('wet', (3, 7)), cooldown=120, ttl=20, group='track_state')
        elif old is not None and wet <= 2 < old:
            self.say('track_drying', now, condition=('wet', (1, 2)), cooldown=120, ttl=30, group='track_state')
        # Tyre advice needs the sim to allow wets; the current compound decides which way to advise.
        compound, known = sample.get('tire_compound'), self.metadata.get('tire_types_known')
        on_wets = (compound in (self.metadata.get('wet_compounds') or [])) if known and compound is not None else None
        advice = None
        if on_wets:
            advice = 'slick_box' if wet <= 2 else None
        elif on_wets is False and declared is True:
            last = num(sample.get('lap_last_s'))
            slower = (len(self.dry_laps) >= 2 and last is not None and last > 0
                      and last >= statistics.median(self.dry_laps) * 1.05)
            if wet >= 5 or (wet == 4 and slower):
                advice = 'rain_box'
            elif wet >= 3:
                advice = 'rain_prepare'
        elif wet >= 5:
            # An engineer never says "if you are not on wets". Without the fitted
            # compound only the measured track state is reported.
            advice = 'track_very_wet'
        if advice != self.rain_advice:
            self.rain_advice = advice
            if advice:
                self.say(advice, now, priority=2 if advice.startswith('rain_box') else 1, ttl=45,
                         condition=('rain_advice', advice), cooldown=90, group='rain_advice')

    def _results(self, sample, previous, now, metadata):
        cars = (metadata or {}).get('cars') or []
        own = next((c for c in cars if c.get('idx') == (metadata or {}).get('player_idx')), None)
        lap = num(sample.get('lap_completed'))
        timed = self.quali_lap if self.quali_lap and self.quali_lap['lap'] == lap else None
        # The published lap time and the standings arrive a moment after the line; wait for both, but not for long.
        if (self.qualifying and lap is not None and lap != self.quali_called and self.lap_done_at is not None
                and now - self.lap_done_at >= 1.5 and (timed or now - self.lap_done_at >= 3.2)):
            best = num(own.get('best_lap_s')) if own else None
            result = None
            if best is not None and best > 0 and own.get('class_id') is not None:
                others = sorted(b for b in (num(c.get('best_lap_s')) for c in cars
                                            if c.get('idx') != own['idx'] and c.get('class_id') == own['class_id']) if b is not None and b > 0)
                ahead = [b for b in others if b < best]
                result = (('quali_result', {'pos': len(ahead) + 1, 'gap': best - ahead[0]}) if ahead
                          else ('quali_p1', {'gap': others[0] - best}) if others else ('quali_alone', {}))
            if result or timed:
                self.quali_called, self.quali_lap = lap, None
                parts, screen = list(timed['parts']) if timed else [], list(timed['screen']) if timed else []
                if result:
                    # The result is spoken by the race engineer. Its lap-time wording
                    # uses the same prepared pieces as formation history in that voice.
                    if timed:
                        parts[0] = radio_phrases.build('form_lap', lap=timed['time'])['text']
                    built = radio_phrases.build(result[0], self.variant_counts.get(result[0], 0), **result[1])
                    self.variant_counts[result[0]] = self.variant_counts.get(result[0], 0) + 1
                    parts.append(built['text'])
                    screen.append(built['text'])
                # One call: the lap time, then where it stands.
                self.emit(result[0] if result else timed['key'], now, ' '.join(screen), role='race' if result else 'spotter', ttl=40, cooldown=0,
                          group='lap_time', speech_text=' '.join(parts), speech_parts=parts)
        position, old_lap = num(sample.get('class_position')), num(previous.get('lap_completed'))
        if (not self.result_called and str(self.metadata.get('session_type', '')).lower() == 'race'
                and int(sample.get('session_flags') or 0) & 0x1 and int(previous.get('session_flags') or 0) & 0x1
                and lap is not None and old_lap is not None and lap == old_lap + 1 and position is not None and position > 0):
            # Crossing the line under the chequered flag: the race is over for this car.
            self.result_called = True
            self.result = {'class_position': position, 'at': now}      # kept for the history of this track (live_formation)
            self.say('race_result', now, {'pos': position}, ttl=40, delay=3, cooldown=3600, priority=2)

    def briefing(self):
        m, s = self.metadata, self.latest
        parts = [f"{m.get('track_name') or '이번 트랙'} 레이스 브리핑입니다."]
        laps = num(m.get('total_laps'))
        seconds = num(m.get('session_duration_s'))
        if laps is not None and 0 < laps < 32767:
            parts.append(f'총 {int(laps)}랩 경기입니다.')
        elif seconds is not None and 0 < seconds < 86400:
            parts.append(f'경기 시간은 {seconds / 60:g}분입니다.')
        position = num(s.get('class_position'))
        if position is not None and position > 0:
            parts.append(f'현재 클래스 순위 표시는 {int(position)}위입니다.')
        fuel = num(s.get('fuel_l'))
        if fuel is not None:
            parts.append(f'연료 {fuel:.1f}리터입니다.')
        limit = num(m.get('pit_speed_limit_kph'))
        if limit is not None:
            parts.append(f'피트 제한속도 {limit:g}킬로미터입니다.')
        temp = num(s.get('track_temp_c'))
        if temp is not None:
            parts.append(f'노면 온도 {temp:.0f}도입니다.')
        parts.append('출발 신호와 페이스랩 지시는 게임에서 확인하세요.')
        return ' '.join(parts)

    def welcome(self, now):
        # Enabling late on the grid should still give a current briefing.
        if (self.latest.get('session_state') in (1, 2, 3)
                and str(self.metadata.get('session_type', '')).lower() == 'race'
                and self.last_sample is not None and 0 <= now - self.last_sample < .5
                and not any(e['key'] == 'briefing' and self.valid(e, now) for e in self.pending)):
            self.emit('briefing', now, self.briefing(), ttl=120, condition=('pre_race', None), cooldown=120)

    def _pit(self, s, p, now):
        pit, old = s.get('on_pit_road'), p.get('on_pit_road')
        if pit is True and old is False:
            self.emit('pit_enter', now, priority=2, condition=('pit', True), cooldown=5)
        if pit is False and old is True:
            self.emit('pit_exit', now, priority=2, condition=('pit', False), ttl=5, cooldown=5)
        speed, limit = num(s.get('speed_mps')), num(self.metadata.get('pit_speed_limit_kph'))
        if pit is True and speed is not None and limit is not None and speed * 3.6 > limit + PIT_SPEED_MARGIN_KPH:
            self.emit('pit_speed', now, priority=2, ttl=3, condition=('overspeed', None), cooldown=8)
        warnings, old_warnings = num(s.get('engine_warnings')), num(p.get('engine_warnings'))
        if pit is True and warnings is not None and old_warnings is not None and int(old_warnings) & 0x10 and not int(warnings) & 0x10:
            self.emit('limiter_off', now, priority=2, condition=('limiter_off', None), cooldown=10)
        status = num(s.get('pit_service_status'))
        if pit is True and status is not None and status != p.get('pit_service_status'):
            key = {1: 'service_start', 2: 'service_done', 100: 'stall_left', 101: 'stall_right',
                   102: 'stall_forward', 103: 'stall_back', 104: 'stall_angle', 105: 'service_unavailable'}.get(int(status))
            if key:
                self.emit(key, now, priority=2, condition=('service', status), cooldown=5)
        tow, old_tow = num(s.get('tow_time_s')), num(p.get('tow_time_s'))
        if tow is not None and tow > 0 and old_tow is not None and old_tow <= 0:
            self.emit('tow', now, f'견인 중입니다. 표시된 남은 시간은 약 {tow:.0f}초입니다.', condition=('tow', None),
                      speech_text=self.wording('tow_time', sec=tow))

    def _laps(self, s, p, now):
        lap, old_lap = num(s.get('lap_completed')), num(p.get('lap_completed'))
        incidents, old_incidents = num(s.get('incidents')), num(p.get('incidents'))
        dirty = s.get('on_pit_road') is True or (incidents is not None and old_incidents is not None and incidents != old_incidents)
        if lap is not None and old_lap is not None and lap == old_lap + 1:
            self.lap_done_at = now
            self.pending_lap = {'lap': lap, 'at': now, 'old_last': p.get('lap_last_s'),
                                'old_best': p.get('lap_best_s'), 'clean': not self.tainted and not dirty}
            self.tainted = False
        elif lap != old_lap:
            self.pending_lap = None
            self.tainted = False
        completed = self.pending_lap
        if completed and lap == completed['lap']:
            last = num(s.get('lap_last_s'))
            current, old_current = num(s.get('lap_current_s')), num(p.get('lap_current_s'))
            timer_reset = current is not None and old_current is not None and current < 3 and old_current > current + 5
            if now - completed['at'] > 3:
                self.pending_lap = None
            elif last is not None and 0 < last < 3600 and (last != completed['old_last'] or timer_reset):
                self.pending_lap = None
                # The whole lap in one call, so its parts do not crowd each other out:
                # the time (or the session best), the pace against recent laps, the place.
                best, old_best = num(s.get('lap_best_s')), num(completed['old_best'])
                record = (completed['clean'] and best is not None and abs(last - best) < .02
                          and old_best is not None and best < old_best - .01)
                key = 'personal_best' if record else 'lap_time'
                parts = [self.wording('personal_best_read' if record else 'lap_time_read', lap=last)]
                screen = [f'이번 세션 최고 기록입니다. {duration(last)}.' if record else f'{int(lap)}랩, {duration(last)}입니다.']
                if completed['clean'] and not record and len(self.laps) >= 3:
                    delta = last - statistics.median(l['time'] for l in self.laps)
                    if abs(delta) >= .7:
                        parts.append(self.wording('pace_slower_by' if delta > 0 else 'pace_faster_by', loss=abs(delta)))
                        screen.append(f'이전 {len(self.laps)}개 완주랩의 중앙 기록보다 {abs(delta):.2f}초 {"느립니다" if delta > 0 else "빠릅니다"}.')
                position = num(s.get('class_position'))
                racing = str(self.metadata.get('session_type', '')).lower() == 'race' and s.get('session_state') == 4
                if (racing and position and position > 0 and self.lap_position is not None and position != self.lap_position
                        and now - self.sent.get('position', -math.inf) > 60 and s.get('on_pit_road') is False):
                    parts.append(self.wording('position_now', pos=position))
                    screen.append(f'현재 클래스 {int(position)}위입니다.')
                if racing and position:
                    self.lap_position = position
                if self.qualifying:
                    # The result of the lap (place and gap) joins the lap time in _results.
                    self.quali_lap = {'lap': lap, 'parts': parts, 'screen': screen, 'key': key, 'time': last}
                else:
                    # The time stays true until the next lap ends, so it may wait out a busy radio for most of the lap.
                    self.emit(key, now, ' '.join(screen), ttl=min(LAP_CALL_MAX_S, max(45., last * LAP_CALL_SHARE)),
                              condition=('lap', lap), group='lap_time', cooldown=0,
                              speech_text=' '.join(parts), speech_parts=parts)
                if completed['clean']:
                    self.laps.append({'lap': lap, 'time': last, 'incidents': incidents})
                    wet = num(s.get('track_wetness'))
                    if wet is not None and 1 <= wet <= 2:
                        self.dry_laps.append(last)
                    recent = list(self.laps)[-3:]
                    if (len(recent) == 3 and all(b['lap'] == a['lap'] + 1 for a, b in zip(recent, recent[1:]))
                            and max(l['time'] for l in recent) - min(l['time'] for l in recent) <= .3):
                        spread = max(l['time'] for l in recent) - min(l['time'] for l in recent)
                        self.say('consistent', now, {'gap': max(.1, spread)}, condition=('lap', lap), delay=6, cooldown=300, ttl=40)
        self.tainted = self.tainted or dirty
        if incidents is not None and old_incidents is not None and incidents > old_incidents:
            limit = num(self.metadata.get('incident_limit'))
            if limit is not None and 0 < limit - incidents <= 8:
                self.say('incident_limit', now, {'points': incidents, 'left': limit - incidents},
                         priority=2 if limit - incidents <= 4 else 1, condition=('incidents', incidents), cooldown=8, group='incident')
            else:
                self.emit('incident', now, f'인시던트 누적 {int(incidents)}점입니다.', condition=('incidents', incidents), cooldown=8)
            if incidents - old_incidents >= 2:
                self.damage_check_at = now + 4
            if incidents >= 4 and len(self.laps) >= 2:
                recent = [l['incidents'] for l in self.laps if l['incidents'] is not None]
                if recent and incidents - min(recent) >= 4:
                    self.emit('incident_coaching', now, '최근 완주랩 사이에 인시던트가 늘었습니다. 원인은 아직 확인되지 않았습니다. 다음 랩은 평소보다 여유를 두고 달리고, 주행 후 문제 구간을 함께 확인하겠습니다.', role='coach', delay=6, cooldown=180, condition=('racing', None))

    def _position(self, s, now):
        position = num(s.get('class_position'))
        if s.get('session_state') != 4 or position is None or position <= 0:
            self.position_candidate = self.position_since = None
            return
        if position != self.position_candidate:
            old = self.position_candidate
            self.position_candidate, self.position_since = position, now
            if old is not None:
                self.position_changed = True
            elif self.position_said is None:
                self.position_said = position  # the place held when watching began
        elif s.get('on_pit_road') is True:
            # Places fall one after another during a stop; the place is said once, back on track.
            self.position_since = now
        elif now - self.position_since >= self.POSITION_SETTLE_S and getattr(self, 'position_changed', False):
            self.position_changed = False
            said, self.position_said = self.position_said, position
            wording = 'position_now' if said is None or said == position else 'position_up' if position < said else 'position_down'
            self.emit('position', now, (f'현재 클래스 {int(position)}위입니다.', f'클래스 순위, {int(position)}위입니다.'), condition=('position', position), group='position', cooldown=15, ttl=POSITION_CALL_S,
                      speech_text=self.wording(wording, pos=position))

    def _warnings(self, s, p, now):
        warning, old = num(s.get('engine_warnings')), num(p.get('engine_warnings'))
        if warning is not None and old is not None and num(s.get('speed_mps')) is not None and s['speed_mps'] > 2:
            for bit, key in [(1, 'water_temp'), (2, 'fuel_pressure'), (4, 'oil_pressure'), (0x40, 'oil_temp'), (0x80, 'mandatory_repair'), (0x100, 'optional_repair')]:
                if int(warning) & bit and not int(old) & bit:
                    self.emit(key, now, priority=2, condition=('warning', bit), cooldown=90)
                    self.emit(key + '_detail', now, '차량 경고가 켜졌습니다. 경고 종류와 계기판을 확인하고 피트 또는 주행 중단 여부를 판단하세요. 이 신호만으로 고장 원인은 확정할 수 없습니다.', role='race', delay=5, condition=('warning', bit), cooldown=180, speech_key='warning_detail')
        fuel, before = num(s.get('fuel_l')), num(p.get('fuel_l'))
        if fuel is not None and before is not None and fuel > before + .2:
            self.fuel_low_stage = 0                         # refuelled: a new stint
        # Said twice a stint at most: with about half a lap of this car's fuel left, and when nearly dry.
        stage = 2 if fuel is not None and fuel < FUEL_LOW_MIN_L else 1 if fuel is not None and fuel < fuel_low_limit(s) else 0
        if stage > self.fuel_low_stage and fuel >= 0 and s.get('session_state') in (4, 5) and not int(s.get('session_flags') or 0) & 1:
            self.fuel_low_stage = 2 if fuel_low_limit(s) <= FUEL_LOW_MIN_L else stage
            self.emit('fuel_low', now, priority=2, condition=('low_fuel', None), cooldown=0)

    def _blue_flag_active(self, sample, now):
        flags = num(sample.get('session_flags'))
        if (flags is None or not int(flags) & 0x20 or self.qualifying
                or sample.get('session_state') != 4 or sample.get('on_pit_road') is not False):
            return False
        if str(self.metadata.get('session_type') or '').lower() != 'race':
            return True
        completed = num(sample.get('lap_completed'))
        if completed is not None and completed >= 1:
            return True
        # At the start the SDK can briefly raise blue even though the driver has
        # no lapping traffic. A late starter can still have a genuine first-lap
        # blue: require fresh measured total progress AND a nearby car behind.
        # This only confirms an existing flag; traffic never creates a flag.
        if self.metadata_at is None or not 0 <= now - self.metadata_at < 2:
            return False
        cars = self.metadata.get('cars') or []
        own = next((car for car in cars if car.get('idx') == self.metadata.get('player_idx')), {})
        own_progress = progress(own)
        if own_progress is None:
            return False
        for car in cars:
            if car.get('idx') == own.get('idx'):
                continue
            other_progress = progress(car)
            if other_progress is None:
                continue
            relative = other_progress - own_progress
            behind = (relative + .5) % 1 - .5
            if relative > .5 and -.1 <= behind <= 0:
                return True
        return False

    def _blue_flag(self, sample, now):
        if not self._blue_flag_active(sample, now):
            if self.blue_since is not None:
                # Cleared/unknown flags end this episode permanently. An old
                # waiting call must not return when a new brief flag appears.
                self.pending = deque((event for event in self.pending if event['key'] != 'blue'), maxlen=48)
            self.blue_since, self.blue_called = None, False
        elif self.blue_since is None:
            self.blue_since = now
        elif now - self.blue_since >= BLUE_HOLD_S and not self.blue_called:
            self.blue_called = True
            self.emit('blue', now, priority=2, condition=('blue', self.blue_since), cooldown=10)

    def _qualifying_gap(self, sample, now):
        if (not self.open_qualifying or not self.traffic_enabled
                or self.quali_traffic_lap == sample.get('lap_number')
                or not self.quali_traffic.current(sample, now)):
            return
        if any(e['group'] == 'quali_traffic' and self.valid(e, now) for e in self.pending):
            return
        result = self.quali_traffic.result
        status = result['status']
        values = {'gap': result['front_s'], 'loss': result['rear_s']} if status == 'window' else {}
        # Numbers describe the proposed gap AT THE LINE, not a command to stop or
        # wait at the line. More than one car can rule out the proposed window.
        self.say('quali_gap_' + status, now, values, ttl=8, cooldown=4, group='quali_traffic',
                 condition=('quali_traffic', (sample.get('lap_number'), dict(result))))

    def valid(self, event, now):
        if (self.last_sample is None or not 0 <= now - self.last_sample < .5 or
                self.latest.get('on_track') is not True or self.latest.get('is_replay') is not False
                or event['run_id'] != self.run_id or not event['not_before'] <= now < event['expires_at']):
            return False
        s = self.latest
        kind, value = event.get('condition') or (None, None)
        if kind == 'control_setting':
            from control_recommendations import FIELDS
            field=(value[2] if len(value)>2 else None) or FIELDS.get(value[0])
            is_setup=event.get('control_recommendation',{}).get('setup') is True
            ready=(self.setup_advice_allowed and s.get('setup_fixed') is False) if is_setup else self.control_advice_allowed
            if event.get('control_recommendation',{}).get('straight_only'):
                from radio_policy import quiet_window
                if not quiet_window(s):return False
            return (ready and s.get(field) == value[1] and s.get('on_pit_road') is False
                    and s.get('session_state') == 4 and not self.result)
        if kind == 'quali_traffic':
            if (not self.open_qualifying or not self.traffic_enabled or s.get('lap_number') != value[0]
                    or not self.quali_traffic.current(s, now)):
                return False
            current, told = self.quali_traffic.result, value[1]
            return current['status'] == told['status'] and all(
                abs(current.get(key, 0) - told.get(key, 0)) <= 1.
                for key in ('front_s', 'rear_s', 'delay_s'))
        if kind == 'traffic':
            return self.traffic_enabled and any(c['idx'] == value[0] and c['call_key'] == value[1]
                                               for c in self.traffic.view(now)['nearby'])
        if kind == 'wet':
            return num(s.get('track_wetness')) is not None and value[0] <= s['track_wetness'] <= value[1]
        if kind == 'rain_advice':
            return self.rain_advice == value
        if kind == 'close':
            return self.traffic_enabled and any(c['idx'] == value and c.get('close') for c in self.traffic.view(now)['nearby'])
        if kind == 'straight':
            # A status line for a long straight is only worth starting while enough of it is left.
            ahead = num(s.get('workload_in_s'))
            return ahead is not None and ahead >= value and s.get('on_pit_road') is False
        if kind == 'hazard':
            return any((h['idx'], h['kind']) == value for h in self.hazard.view(now))
        if kind == 'corner_ahead':
            pct = num(s.get('distance_pct'))
            # The browser may already hold this event when new geometry clears the coach.
            return (pct is not None and value[1] < (value[0] - pct) % 1 < .25
                    and (len(value) == 2 or value[2] == self.corner_revision))
        if kind == 'pit_box':
            box = self.pit_box.view()
            return (self.pit_box_enabled and s.get('on_pit_road') is True and box['ready']
                    and (num(s.get('speed_mps')) or 0) >= 2 and s.get('pit_service_status') not in (1, 2, 100, 101, 102, 103, 104, 105)
                    and value * .55 <= box['distance_estimate_m'] <= value)
        if kind == 'blue':
            return value == self.blue_since and self._blue_flag_active(s, now)
        if kind == 'flag': return num(s.get('session_flags')) is not None and bool(int(s['session_flags']) & value)
        if kind == 'warning': return num(s.get('engine_warnings')) is not None and bool(int(s['engine_warnings']) & value)
        if kind == 'pit': return s.get('on_pit_road') is value
        if kind == 'service': return s.get('on_pit_road') is True and s.get('pit_service_status') == value
        if kind == 'overspeed': return s.get('on_pit_road') is True and num(s.get('speed_mps')) is not None and num(self.metadata.get('pit_speed_limit_kph')) is not None and s['speed_mps'] * 3.6 > self.metadata['pit_speed_limit_kph'] + PIT_SPEED_MARGIN_KPH
        if kind == 'limiter_off': return s.get('on_pit_road') is True and num(s.get('engine_warnings')) is not None and not int(s['engine_warnings']) & 0x10
        if kind == 'pre_race': return s.get('session_state') in (1, 2, 3)
        if kind == 'formation': return s.get('session_state') == 3 and s.get('on_pit_road') is False
        if kind == 'racing': return s.get('session_state') == 4
        if kind == 'lap': return s.get('lap_completed') == value
        if kind == 'position': return s.get('class_position') == value and s.get('session_state') == 4
        if kind == 'laps_left': return num(s.get('session_laps_remaining')) is not None and value - 1 < s['session_laps_remaining'] <= value and s.get('session_state') == 4
        if kind == 'incidents': return s.get('incidents') == value
        if kind == 'tow': return (num(s.get('tow_time_s')) or 0) > 0
        if kind == 'low_fuel': return num(s.get('fuel_l')) is not None and s['fuel_l'] < fuel_low_limit(s) and s.get('session_state') in (4, 5) and not int(s.get('session_flags') or 0) & 1
        return True

    def view(self, now):
        events = [{k: v for k, v in e.items() if k != 'condition'} | {'expires_in_ms': round((e['expires_at'] - now) * 1000)}
                  for e in self.pending if self.valid(e, now)]
        # On the formation lap the driver weaves and brakes to warm the car, and has time to listen.
        calm = self.latest.get('session_state') == 3
        return {'events': events, 'quiet': calm or speech_ok(self.latest), 'brief_quiet': calm or speech_ok(self.latest, brief=True), 'serial': self.serial, 'run_id': self.run_id,
                'speech_window_s': self.latest.get('speech_window_s'), 'speech_learned_laps': self.latest.get('speech_learned_laps', 0),
                'session_time': self.latest.get('session_time'), 'lap_number': self.latest.get('lap_number'), 'distance_pct': self.latest.get('distance_pct'),
                'connected': self.last_sample is not None and 0 <= now - self.last_sample < .5,
                'history': list(self.history), 'capabilities': self.capabilities(),
                'traffic': self.traffic.view(now), 'hazards': self.hazard.view(now), 'pit_box': {**self.pit_box.view(), 'enabled': self.pit_box_enabled}}

    def get(self, identifier, now):
        return next((dict(e) for e in self.pending if e['id'] == identifier and self.valid(e, now)), None)

    def ack(self, identifier, now):
        event = self.get(identifier, now)
        if event:
            if event.get('group') == 'quali_traffic':
                self.quali_traffic_lap = self.latest.get('lap_number')
            if event.get('car'):
                # Heard to the end: this car is not called again for a while.
                self.car_calls[tuple(event['car'])] = now
            self.history.appendleft({k: event[k] for k in ('id', 'text', 'engineer', 'at', 'key')})
            self.pending = deque((e for e in self.pending if e['id'] != identifier), maxlen=48)
        return event is not None

    def capabilities(self):
        s = self.latest
        return {'flags': num(s.get('session_flags')) is not None, 'laps': num(s.get('lap_completed')) is not None and num(s.get('lap_last_s')) is not None,
                'traffic': self.traffic.available, 'pit_box': num(self.metadata.get('pit_box_pct')) is not None and num(self.metadata.get('track_length_m')) is not None,
                'pit_service': num(s.get('pit_service_status')) is not None, 'engine': num(s.get('engine_warnings')) is not None,
                'pit_speed': num(self.metadata.get('pit_speed_limit_kph')) is not None, 'position': num(s.get('class_position')) is not None,
                'briefing': bool(self.metadata.get('session_type')), 'remaining': num(s.get('session_time_remaining_s')) is not None or num(s.get('session_laps_remaining')) is not None}
