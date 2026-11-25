"""Prepared Orus answers for fuel and virtual-energy buttons, in tenths."""
import base64
from functools import cache
import math
from pathlib import Path

from instant_answers import number


FOLDER = Path('pieces') / 'button-fuel-orus'
LIMITS = {'fuel_range': 2000, 'fuel_average': 300, 'energy_remaining': 1000}
NAMES = {'fuel_range': '지금 연료로 몇 랩 더 갈 수 있어?', 'fuel_average': '최근 랩당 평균 연료 소비는?',
         'energy_remaining': 'LMU 가상 에너지 얼마나 남았어?'}
UNITS = {'fuel_range': '랩', 'fuel_average': '리터', 'energy_remaining': '퍼센트'}
GROUP = '연료·에너지'
STYLES = ('default', 'short')
KINDS = {'fuel_range': '주행 가능 랩', 'fuel_average': '랩당 평균 소비', 'energy_remaining': 'LMU 가상 에너지'}


def style_for(packs):
    selected = packs.selections.get('strategy') if packs else None
    return 'short' if selected and packs.is_builtin(selected) else 'default'


def measurement(topic, context):
    pit = context.get('pit_wall') or {}
    if topic == 'energy_remaining' and context.get('game') != 'lmu':
        return None
    value = number({'fuel_range': pit.get('fuel_laps'), 'fuel_average': context.get('fuel_avg_l_per_lap'),
                    'energy_remaining': pit.get('energy_pct')}[topic])
    if value is not None and 0 <= value <= LIMITS[topic] / 10:
        return math.floor(value * 10 + .5)
    return None


def wording(topic, tenths, style):
    whole, digit = divmod(tenths, 10)
    amount = (f'{whole}.{digit}' if digit else str(whole)) + UNITS[topic]
    if topic == 'fuel_range':
        return f'연료 약 {amount}.' if style == 'short' else f'지금 연료로 약 {amount} 더 갈 수 있습니다.'
    if topic == 'fuel_average':
        return f'랩당 평균 {amount}.' if style == 'short' else f'최근 평균은 랩당 {amount} 정도입니다.'
    return f'가상 에너지 {amount}.' if style == 'short' else f'가상 에너지 {amount} 남았습니다.'


@cache
def by_wording():
    return {wording(topic,n,style): (topic,n,style)
            for topic,limit in LIMITS.items() for style in STYLES for n in range(limit+1)}


def directory(root, topic, style):
    return root / FOLDER / topic / style


@cache
def filenames(topic):
    return frozenset(f'{n}.ogg' for n in range(LIMITS[topic] + 1))


def read(root, topic, tenths, style):
    data = (directory(root, topic, style) / f'{tenths}.ogg').read_bytes()
    if len(data) < 100 or not data.startswith(b'OggS'):
        raise ValueError('준비 음원을 읽을 수 없습니다.')
    return 'data:audio/ogg;codecs=opus;base64,' + base64.b64encode(data).decode('ascii')
