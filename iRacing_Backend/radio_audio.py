"""Finite automatic-radio vocabulary, cached by text and configured voice.

Only public fixed phrases are persisted. Driver statements and AI replies
never enter this disk cache. Missing/corrupt clips are replaced atomically.
"""
import base64
from collections import OrderedDict
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import radio_variations


CALLS = {
    'fuel_critical': ('strategy', '연료가 매우 부족합니다. 피트 진입을 확인하세요.'),
    'fuel_short': ('strategy', '완주 연료가 부족합니다. 추가 급유량을 화면에서 확인하세요.'),
    'fuel_reserve': ('strategy', '완주 여유 연료가 부족합니다. 급유 계획을 확인하세요.'),
    'fuel_save_held': ('strategy', '최근 세 랩 연료 절약 목표를 유지했습니다.'),
    'fuel_save_over': ('strategy', '이번 랩 연료 소비가 절약 목표를 초과했습니다.'),
    'fuel_save_under': ('strategy', '이번 랩 연료 소비가 절약 목표보다 적었습니다.'),
    'fuel_save_on_target': ('strategy', '이번 랩 연료 소비가 절약 목표와 비슷합니다.'),
    'approach': ('race', '같은 클래스 뒤차의 최근 랩 페이스가 더 빠릅니다.'),
    'pit_ahead': ('race', '같은 클래스 앞차가 피트에 들어갔습니다.'),
    'pit_behind': ('race', '같은 클래스 뒤차가 피트에 들어갔습니다.'),
    'class_traffic_ahead': ('race', '다른 클래스 차량이 앞에 있습니다.'),
    'class_traffic_behind': ('race', '다른 클래스 차량이 뒤에 있습니다.'),
    # One named input to change, chosen by where in the corner the driver said it happened.
    'coach_understeer': ('coach', '같은 구간에서 앞이 밀리는 느낌을 반복하셨습니다. 다음에는 진입 속도를 조금 낮춰 보세요.'),
    'coach_understeer_entry': ('coach', '같은 구간에서 앞이 밀리는 느낌을 반복하셨습니다. 다음에는 브레이크를 조금 더 천천히 놓아 보세요.'),
    'coach_understeer_exit': ('coach', '같은 구간에서 앞이 밀리는 느낌을 반복하셨습니다. 다음에는 가속을 조금 늦게 시작해 보세요.'),
    'coach_oversteer': ('coach', '같은 구간에서 뒤가 불안한 느낌을 반복하셨습니다. 다음에는 핸들을 조금 더 천천히 돌려 보세요.'),
    'coach_oversteer_entry': ('coach', '같은 구간에서 뒤가 불안한 느낌을 반복하셨습니다. 다음에는 브레이크를 조금 더 부드럽게 놓아 보세요.'),
    'coach_oversteer_exit': ('coach', '같은 구간에서 뒤가 불안한 느낌을 반복하셨습니다. 다음에는 가속 페달을 조금 더 천천히 밟아 보세요.'),
    'coach_braking': ('coach', '같은 구간에서 제동 불안을 반복하셨습니다. 다음에는 제동을 조금 더 일찍 시작해 보세요.'),
    'pace_faster': ('race', '이번 깨끗한 랩은 최근 기록보다 빨랐습니다.'),
    'pace_slower': ('race', '이번 깨끗한 랩은 최근 기록보다 느렸습니다.'),
    'warning_detail': ('race', '차량 경고가 계속됩니다. 계기판과 피트 작업 지시를 확인하세요.'),
    'incident_coaching': ('coach', '최근 인시던트가 늘었습니다. 다음 랩은 여유를 두고 달려보세요.'),
    'lap_time': ('spotter', '이번 랩 기록이 갱신됐습니다.'),
    'personal_best': ('spotter', '이번 세션 최고 기록입니다.'),
    'position': ('spotter', '클래스 순위가 바뀌었습니다.'),
    'briefing': ('spotter', '경기 브리핑을 준비했습니다. 경기 길이와 피트 제한속도를 화면에서 확인하세요.'),
    'time_remaining': ('spotter', '남은 경기 시간이 줄었습니다. 화면의 남은 시간을 확인하세요.'),
    'laps_remaining': ('spotter', '남은 경기 랩수가 줄었습니다. 화면의 남은 랩수를 확인하세요.'),
    'incident': ('spotter', '인시던트 누적 점수가 증가했습니다.'),
    'tow': ('spotter', '견인 중입니다. 남은 시간을 화면에서 확인하세요.'),
    'race_started': ('spotter', '경기가 시작됐습니다. 출발 신호는 게임에서 확인하세요.'),
}
# Spoken a few seconds before the corner; the measured numbers stay on screen.
CALLS.update({'corner_' + _cause: ('coach', _text) for _cause, _text in {
    'brake_early': '다음 코너. 지난 랩은 제동이 일렀습니다.',
    'min_speed': '다음 코너. 지난 랩은 최저속도가 낮았습니다.',
    'throttle_late': '다음 코너. 지난 랩은 가속이 늦었습니다.',
    'time': '다음 코너. 지난 랩에 느렸습니다.',
}.items()})
for _role, _label in [('coach', '주행 코치'), ('race', '레이스 엔지니어'), ('strategy', '전략 엔지니어')]:
    CALLS['preview_' + _role] = (_role, f'{_label}입니다. 경기 중 필요한 정보를 전달하겠습니다.')


# Other ways to say the same fixed call. The first wording stays in CALLS.
VARIANTS = {
    'fuel_critical': ('이번 랩 박스. 연료가 한 랩 남짓입니다.', '박스, 박스. 연료가 거의 없습니다.', '연료 한계입니다. 이번 랩 피트 들어오세요.'),
    'fuel_short': ('이대로는 완주 연료가 모자랍니다. 급유량은 화면에 있습니다.', '연료가 끝까지 안 됩니다. 필요한 급유량을 화면에서 확인하세요.',
                   '완주까지 연료 부족입니다. 화면의 급유량을 확인하세요.'),
    'fuel_reserve': ('완주는 되지만 여유 연료가 빠듯합니다. 급유 계획을 확인하세요.', '연료 여유분이 모자랍니다. 급유 계획 확인.'),
    'fuel_save_held': ('연료 절약 목표. 세 랩 연속 지켰습니다.', '절약 목표 유지 중입니다. 좋습니다.'),
    'fuel_save_over': ('이번 랩은 절약 목표보다 많이 썼습니다.', '연료 목표 초과입니다. 조금 더 아껴 주세요.', '목표보다 많이 썼습니다. 다음 랩에서 만회합시다.'),
    'fuel_save_under': ('이번 랩은 절약 목표보다 덜 썼습니다. 좋습니다.', '연료 목표 안쪽입니다. 그대로 가세요.'),
    'fuel_save_on_target': ('이번 랩 연료. 목표와 같습니다.', '연료 목표대로입니다.'),
    'approach': ('뒤차 페이스가 더 빠릅니다. 거울 확인하세요.', '뒤차가 최근 랩에서 더 빠릅니다.'),
    'pit_ahead': ('앞차가 피트로 빠졌습니다. 클린 에어입니다.', '앞차가 피트에 들어갔습니다. 지금 페이스를 올려 주세요.', '앞차 피트인. 지금 랩타임을 벌어 둡시다.'),
    'pit_behind': ('뒤차가 피트에 들어갔습니다. 이번 랩은 페이스를 올려 주세요.', '뒤차 피트인. 언더컷 대비해 푸시하세요.', '뒤차가 피트로 들어갔습니다. 페이스 올려 주세요.'),
    'class_traffic_ahead': ('앞에 다른 클래스 차량입니다.', '다른 클래스 차량이 앞에 있습니다. 여유 두세요.'),
    'class_traffic_behind': ('뒤에 다른 클래스 차량입니다.', '다른 클래스 차량이 뒤에서 옵니다.'),
    'pace_faster': ('좋은 랩입니다. 최근 기록보다 빨랐습니다.', '페이스 올라왔습니다. 그대로 갑시다.', '방금 랩. 최근보다 빨랐습니다.'),
    'pace_slower': ('방금 랩은 최근 기록보다 느렸습니다.', '페이스가 조금 떨어졌습니다. 리듬 되찾읍시다.', '이번 랩은 최근보다 느렸습니다. 다음 랩에 집중하세요.'),
    'warning_detail': ('차량 경고가 계속 켜져 있습니다. 계기판을 확인하세요.', '경고가 꺼지지 않습니다. 계기판과 피트 지시를 확인하세요.'),
    'incident_coaching': ('인시던트가 늘고 있습니다. 한 랩은 여유 있게 가세요.', '최근 인시던트가 많습니다. 다음 랩은 안전하게.'),
    'lap_time': ('랩 기록 들어왔습니다.', '이번 랩 기록 확인했습니다.', '랩타임 갱신됐습니다.'),
    'personal_best': ('세션 베스트입니다.', '이번 세션 가장 빠른 랩입니다. 좋습니다.', '베스트 랩 나왔습니다.'),
    'position': ('순위 변동 있습니다.', '클래스 순위가 바뀌었습니다. 화면 확인.', '순위가 달라졌습니다.'),
    'time_remaining': ('남은 시간이 줄었습니다. 화면에서 확인하세요.', '경기 시간 안내입니다. 화면의 남은 시간을 확인하세요.'),
    'laps_remaining': ('남은 랩수가 줄었습니다. 화면에서 확인하세요.', '남은 랩 안내입니다. 화면을 확인하세요.'),
    'incident': ('인시던트 점수가 올랐습니다.', '인시던트 누적이 늘었습니다.'),
    'race_started': ('경기 시작입니다. 출발 신호는 게임에서 확인하세요.', '레이스 들어갑니다. 출발 신호 확인하세요.'),
    'corner_brake_early': ('다음 코너. 지난 랩은 제동을 일찍 시작했습니다.', '다음 코너. 지난 랩 제동 시작이 일렀습니다.'),
    'corner_min_speed': ('다음 코너. 지난 랩은 코너 속도가 낮았습니다.', '다음 코너. 지난 랩 최저속도가 부족했습니다.'),
    'corner_throttle_late': ('다음 코너. 지난 랩은 가속 시작이 늦었습니다.', '다음 코너. 지난 랩 가속이 늦게 들어갔습니다.'),
    'corner_time': ('다음 코너. 지난 랩에 시간을 잃었습니다.', '다음 코너. 지난 랩 손실 구간입니다.'),
}
# The second pack goes after the first: 'key#1' keeps meaning the wording it always did.
VARIANTS.update({key: tuple(VARIANTS.get(key, ())) + more for key, more in radio_variations.CALLS.items()})


def texts(key):
    call = CALLS.get(key)
    return (call[1], *VARIANTS.get(key, ())) if call else ()


def speech_fields(key, seed=0):
    """One wording of a fixed call. The seed only moves once a call was really delivered."""
    options = texts(key)
    if not options:
        return {'speech_key': None, 'spoken_text': None}
    index = seed % len(options)
    return {'speech_key': key if index == 0 else f'{key}#{index}', 'spoken_text': options[index]}


SINO_DIGITS = ('', '일', '이', '삼', '사', '오', '육', '칠', '팔', '구')
NATIVE_ONES = ('', '한', '두', '세', '네', '다섯', '여섯', '일곱', '여덟', '아홉')
NATIVE_TENS = ('', '열', '스물', '서른', '마흔', '쉰', '예순', '일흔', '여든', '아흔')


def sino(number):
    """12 -> 십이, as places are read (십이위)."""
    if number == 0:
        return '영'
    words = ''
    for unit, name in ((1000, '천'), (100, '백'), (10, '십')):
        digit, number = divmod(number, unit)
        if digit:
            words += ('' if digit == 1 else SINO_DIGITS[digit]) + name
    return words + SINO_DIGITS[number]


def native(number):
    """5 -> 다섯, 20 -> 스무, 21 -> 스물한: the counting form used before 랩."""
    if not 0 < number < 100:
        return sino(number)
    if number == 20:
        return '스무'
    return NATIVE_TENS[number // 10] + NATIVE_ONES[number % 10]


def spoken_form(text):
    """Text as it is sent to the voice.

    With a space before 랩 the voice paused inside the phrase ("최근 … 랩",
    "두 … 랩"), so the word is joined to the one before it. Screens keep the
    normal spacing. Repeated call words are the one place a comma is right:
    "박스. 박스." left too long a gap, "박스, 박스." did not.
    """
    # Digits before a unit were stretched ("5위" as "오…위", "5랩" as "오…랩"); written out they were not.
    text = re.sub(r'(?<![\d.])(\d+)위', lambda match: sino(int(match[1])) + '위', text)
    text = re.sub(r'(?<![\d.])(\d+)랩(?!분)', lambda match: native(int(match[1])) + '랩', text)
    # Whole litres joined in Hangul ("오리터"). "오 리터" and "5리터" were stretched, "다섯 리터" is not
    # how litres are counted, and decimals read digit by digit in Hangul came out garbled: they stay digits.
    text = re.sub(r'(?<![\d.])(\d+)리터', lambda match: sino(int(match[1])) + '리터', text)
    text = re.sub(r'(?<=\S) 랩(?!타임)', '랩', text)
    return re.sub(r'쯤 (?=뒤|전|후)', '쯤', text)  # "랩쯤 … 뒤에" paused the same way


def fixed_catalog():
    """Every (role, wording) of the fixed calls and the short acknowledgements."""
    from radio_phrases import ACKS
    found = {(CALLS[key][0], text): None for key in CALLS for text in texts(key)}
    found.update({(role, text): None for role in ('coach', 'race', 'strategy') for text in ACKS})
    return list(found)


class RadioAudioCache:
    def __init__(self, speech, directory):
        self.speech = speech
        self.directory = Path(os.getenv('IRACING_RADIO_AUDIO_DIR') or directory)
        self.lock = threading.Lock()
        self.memory = OrderedDict()
        self.pending = {}

    def path(self, role, text):
        from race_engineer import role_voice
        digest = hashlib.sha256(json.dumps([role_voice(role), spoken_form(text)], ensure_ascii=False).encode()).hexdigest()
        return self.directory / f'{digest}.mp3'

    def cached(self, role, text):
        try:
            return self.path(role, text).stat().st_size >= 100
        except OSError:
            return False

    def clip(self, key):
        base, _, index = key.partition('#')
        return self.phrase(CALLS[base][0], texts(base)[int(index or 0)])

    def phrase(self, role, text):
        """One whole call as a single clip, synthesized once per voice and wording."""
        path = self.path(role, text)
        with self.lock:
            if path.name in self.memory:
                self.memory.move_to_end(path.name)
                return self.memory[path.name]
            self.directory.mkdir(parents=True, exist_ok=True)
            # Never read a file another thread is writing right now (it fails on Windows).
            other = self.pending.get(path.name)
            data = path.read_bytes() if other is None and path.exists() else b''
            if len(data) < 100 and other is None:
                self.pending[path.name] = threading.Event()
        if other is not None:
            # The same wording is being synthesized right now: wait and reuse it.
            other.wait(30)
            return self.phrase(role, text)
        if len(data) < 100:
            # Different clips synthesize side by side; only the bookkeeping is locked.
            try:
                data = base64.b64decode(self.speech.synthesize(text, role=role), validate=True)
                if len(data) < 100:
                    raise ValueError('자동 안내 음성을 준비하지 못했습니다. 화면의 측정값을 확인하세요.')
                temporary = path.with_suffix('.tmp')
                temporary.write_bytes(data)
                temporary.replace(path)
            finally:
                with self.lock:
                    self.pending.pop(path.name).set()
        encoded = base64.b64encode(data).decode('ascii')
        with self.lock:
            self.memory[path.name] = encoded
            while len(self.memory) > 1500:
                self.memory.popitem(last=False)
        return encoded
