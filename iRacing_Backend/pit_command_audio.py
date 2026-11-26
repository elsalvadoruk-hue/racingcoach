"""Prepared pit-setting readbacks. Only verified settings use success wording."""
from functools import lru_cache
import hashlib
import json
from pathlib import Path

VOICE = 'ko-KR-Chirp3-HD-Orus'

TARGETS = {15:'네 바퀴', 1:'왼쪽 앞', 2:'오른쪽 앞', 4:'왼쪽 뒤', 8:'오른쪽 뒤',
           5:'왼쪽', 10:'오른쪽', 3:'앞쪽', 12:'뒤쪽'}
FIXED = {
    'clear': ('피트 서비스 모두 취소했습니다.', '피트 서비스 전체 취소.'),
    'clear_fuel': ('급유 취소했습니다.', '급유 취소.'),
    'clear_tires': ('타이어 교체 취소했습니다.', '타이어 교체 취소.'),
    'windshield': ('윈드실드 교체 설정했습니다.', '윈드실드 교체 설정.'),
    'clear_windshield': ('윈드실드 교체 취소했습니다.', '윈드실드 교체 취소.'),
    'fast_repair': ('빠른 수리 설정했습니다.', '빠른 수리 설정.'),
    'clear_repair': ('빠른 수리 취소했습니다.', '빠른 수리 취소.'),
    'command_unconfirmed': ('피트 설정 반영을 확인하지 못했습니다.', '피트 설정 반영 미확인.'),
    'command_blocked': ('피트 명령을 적용하지 못했습니다.', '피트 명령 적용 안 됨.'),
    'command_disabled': ('피트 명령 보내기가 꺼져 있습니다.', '피트 명령 보내기 꺼짐.'),
    'ambiguous_action': ('교체인지 취소인지 다시 말씀해 주세요.', '교체 또는 취소인지 다시 말해 주세요.'),
}


def wordings(command, outcome, short=False):
    i = int(short)
    status = outcome.get('result')
    if status != 'command_applied':
        key = 'ambiguous_action' if command.get('ambiguous_action') and status == 'command_blocked' else status
        return [FIXED[key][i]] if key in FIXED else []
    key = command['id']
    if key in FIXED:
        return [FIXED[key][i]]
    if key in ('fuel_set', 'fuel_finish_set') and command.get('fuel') is not None:
        liters = command['fuel']
        if not 1 <= liters <= 150 or liters != int(liters): return []
        return [f'추가 급유 {int(liters)}리터' + (' 설정.' if short else '로 설정했습니다.')]
    target = TARGETS.get(command.get('mask'))
    if key == 'tires_set' and target and command.get('compound_kind') in ('wet', 'dry'):
        kind = '웻' if command['compound_kind'] == 'wet' else '슬릭'
        return [f'{kind} {target} 교체 ' + ('설정.' if short else '설정했습니다.')]
    if key == 'pressure_set' and target:
        values = set(command.get('pressures', {}).values())
        if len(values) != 1: return []
        kpa = next(iter(values))
        if not 100 <= kpa <= 400 or kpa != int(kpa): return []
        # A wheel phrase and a whole number+unit phrase. Never split digits.
        return [f'{target} 교체 공기압.', f'{int(kpa)}킬로파스칼' + (' 설정.' if short else '로 설정했습니다.')]
    return []


def catalog():
    for short in (False, True):
        style = 'short' if short else 'default'
        texts = [pair[int(short)] for pair in FIXED.values()]
        for mask in TARGETS:
            for kind in ('dry', 'wet'):
                texts += wordings({'id':'tires_set','mask':mask,'compound_kind':kind}, {'result':'command_applied'}, short)
            texts.append(f'{TARGETS[mask]} 교체 공기압.')
        for liters in range(1, 151):
            texts += wordings({'id':'fuel_set','fuel':liters}, {'result':'command_applied'}, short)
        for kpa in range(100, 401):
            texts.append(f'{kpa}킬로파스칼' + (' 설정.' if short else '로 설정했습니다.'))
        for text in dict.fromkeys(texts):
            yield {'style':style, 'role':'strategy', 'text':text}


@lru_cache(maxsize=1)
def texts():
    return frozenset(row['text'] for row in catalog())


def path_for(directory, text):
    digest = hashlib.sha256(json.dumps([VOICE, text], ensure_ascii=False).encode()).hexdigest()
    return Path(directory) / 'pit-readback' / (digest + '.ogg')


def prepared_file(directory, role, text):
    return path_for(directory, text) if role == 'strategy' and text in texts() else None


def render(command, outcome, buttons):
    import button_fuel_audio
    short = button_fuel_audio.style_for(getattr(buttons.coach, 'custom_packs', None)) == 'short'
    texts = wordings(command, outcome, short)
    try:
        parts = [buttons.read_clip('pit_command_readback', 'strategy', text) for text in texts]
    except (OSError, ValueError, KeyError):
        return {'parts':[], 'spoken':[], 'missing':True}
    return {'parts':parts, 'spoken':texts, 'missing':not bool(parts)}
