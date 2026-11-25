"""Prepared spotter and pit-wall sentences for rejoining, traffic and strategy."""
import base64
from functools import lru_cache
import hashlib
from pathlib import Path

REENTRY_WORDS = {
    'first': ('차량이 옵니다.', '차량 접근.'),
    'follow': ('한 대 더 옵니다.', '다음 차 접근.'),
    'train': ('여러 대가 연달아 옵니다.', '여러 대 연속 접근.'),
}
for seconds in (5, 6):
    REENTRY_WORDS[f'first_{seconds}'] = (f'차량이 옵니다. 약 {seconds}초 뒤입니다.', f'차량 접근. 약 {seconds}초.')
    REENTRY_WORDS[f'follow_{seconds}'] = (f'한 대 더 옵니다. 약 {seconds}초 뒤입니다.', f'다음 차까지 약 {seconds}초.')

PIT_WORDS = {
    'ahead': ('피트 전에 뒤에 있던 차가 지금은 앞에 있습니다.', '피트 전 뒤차가 지금은 앞.'),
    'behind': ('피트 전에 앞에 있던 차가 지금은 뒤에 있습니다.', '피트 전 앞차가 지금은 뒤.'),
}
RIVAL_LEAD = ('빠른 차가 지나갔습니다. 바로 뒤는', '바로 뒤는')
TARGET_LEAD = ('완주 연료 목표는 랩당', '목표 랩당')
GAP_LEAD = ('뒤차보다 랩당', '뒤차가 랩당')
GAP_END = ('느리면 결승 전에 따라잡힙니다.', '좁히면 결승 전에 붙음.')


def rival_words(rank):
    return (f'{RIVAL_LEAD[0]} 우리 클래스 {rank}위입니다.',
            f'{RIVAL_LEAD[1]} 우리 클래스 {rank}위.')


def pit_cycle_words(side, rank):
    ends = (f' 클래스 {rank}위입니다.', f' 클래스 {rank}위.') if rank else ('', '')
    return tuple(a+b for a,b in zip(PIT_WORDS[side], ends))


def defence_words(target, gap):
    """A fuel target or a separate time-loss warning, each in a whole recording."""
    if target is not None:
        amount = f'{target/100:.2f}'.rstrip('0').rstrip('.')
        return (f'{TARGET_LEAD[0]} {amount}리터입니다.', f'{TARGET_LEAD[1]} {amount}리터.')
    return tuple(f'{before} {gap/10:g}초씩 {after}' for before,after in zip(GAP_LEAD,GAP_END))


def feedback_words(met, closing):
    normal_fuel = '연료 목표를 지켰습니다.' if met else '목표보다 연료를 더 썼습니다.'
    normal_gap = ('뒤차와의 간격은 줄었습니다.' if met else '뒤차와의 간격도 줄었습니다.') if closing else (
        '뒤차와의 간격도 유지했습니다.' if met else '뒤차와의 간격은 유지했습니다.')
    short_fuel = '연료 목표 달성.' if met else '연료 목표 초과.'
    short_gap = '뒤차와 간격 줄어듦.' if closing else '간격은 유지됨.'
    return f'{normal_fuel} {normal_gap}', f'{short_fuel} {short_gap}'


@lru_cache(maxsize=1)
def catalog():
    rows=[]
    for pair in REENTRY_WORDS.values():
        rows.append(('reentry_approach','spotter',pair))
    for rank in range(1,129):
        for side in PIT_WORDS:
            rows.append(('pit_cycle_'+side,'strategy',pit_cycle_words(side,rank)))
        if rank>=2:
            rows.append(('class_pass_rival','spotter',rival_words(rank)))
    for side,pair in PIT_WORDS.items():
        rows.append(('pit_cycle_'+side,'strategy',pair))
    for met in (False,True):
        for closing in (False,True):
            rows.append(('fuel_defence_feedback','strategy',feedback_words(met,closing)))
    for target in range(1,3001):
        rows.append(('fuel_defence_target','strategy',defence_words(target,None)))
    for gap in range(1,321):
        rows.append(('fuel_defence','strategy',defence_words(None,gap)))
    return tuple(rows)


@lru_cache(maxsize=1)
def fixed_sentences():
    return {role:{text:(text,) for _,owner,pair in catalog() if owner==role for text in pair}
            for role in ('spotter','strategy')}


def clauses(role,text):
    if role not in ('spotter','strategy') or not isinstance(text,str):
        return None
    return fixed_sentences()[role].get(text)


def clip_path(directory, voice, words):
    name=hashlib.sha256((voice+'\n'+words).encode()).hexdigest()+'.ogg'
    return Path(directory)/'pieces'/'race-strategy'/name


def files(directory, role, text, packs=None):
    recipe=clauses(role,text)
    if recipe is None:
        return None
    from builtin_radio import PACKS
    from race_engineer import role_voice
    selected=packs.selections.get(role) if packs else None
    voice=('ko-KR-Chirp3-HD-'+PACKS[selected][1]) if selected in PACKS else role_voice(role)
    return (clip_path(directory,voice,text),)


def read_files(paths):
    data=paths[0].read_bytes()
    if not data.startswith(b'OggS') or len(data)<100:
        raise ValueError('무전 음원이 손상됐습니다.')
    return 'data:audio/ogg;codecs=opus;base64,'+base64.b64encode(data).decode('ascii')


@lru_cache(maxsize=4096)
def _seconds(path, modified, size):
    import soundfile as sf
    return sf.info(path).duration


def prepared_seconds(directory, role, text, packs=None):
    paths=files(directory,role,text,packs)
    if not paths:
        return None
    try:
        path=paths[0]
        stat=path.stat()
        return _seconds(str(path),stat.st_mtime_ns,stat.st_size)
    except (OSError, RuntimeError):
        return None
