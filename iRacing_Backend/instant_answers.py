"""Answers to the driver's most common measured questions, built from prepared sentences.

Each answer is a list of whole sentences that already exist as cached clips
(radio_phrases), so it is spoken at once: no synthesis, no model. A question
this module cannot answer from measured values returns None and takes the
normal path.
"""
import math
import re

import radio_phrases


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def sentence(key, **values):
    return radio_phrases.build(key, 0, **values)['text']


def gap_sentences(view, sides):
    found = []
    for side in sides:
        car = view.get(side)
        gap = number(car.get('gap_estimate_s')) if car else None
        if gap is None or car.get('on_pit_road'):
            continue
        if .1 <= gap < 9.95:
            found.append(sentence(f'brief_gap_{side}', gap=gap))
        elif side == 'ahead' and gap <= 180:
            found.append(sentence('ahead_far', sec=gap))
    return found


def catch_sentences(view):
    """How the gap to the car ahead is moving and what closing it would take."""
    car = view.get('ahead')
    gap = number(car.get('gap_estimate_s')) if car else None
    if gap is None or gap > 180 or car.get('on_pit_road'):
        return []
    found = gap_sentences(view, ['ahead'])
    change, line = number(car.get('gap_change_s_per_lap')), number(car.get('gap_at_line_s'))
    remaining = number(view.get('remaining_laps'))
    if car.get('gap_trend') == 'closing' and change is not None and change < 0 and line is not None:
        found.append(sentence('catch_rate', loss=-change))
        if math.ceil(line / -change) <= 30:
            found.append(sentence('catch_in', laps=math.ceil(line / -change)))
    elif remaining and remaining >= 2 and .05 <= gap / remaining <= 3:
        found.append(sentence('catch_need', loss=gap / remaining))
    return found if len(found) > 1 else []


def instant_answer(text, context, live=None):
    """(role, sentences) for a question answered from prepared sentences, else None."""
    compact = ''.join((text or '').split())
    snapshot = context.get('snapshot') or {}
    view = context.get('pit_wall') or {}
    racing = bool(view.get('active'))
    if any(word in compact for word in ('이론', '최적랩', '구간최고')) and live is not None:
        best = live.lap_sectors.theoretical()
        if best:
            parts = [sentence('label_theoretical'), sentence('lap_time_read', lap=best['lap_s'])]
            if best['gain_s'] >= .1:
                parts.append(sentence('theoretical_gain', loss=best['gain_s']))
            return 'spotter', parts
        return None
    if racing and any(word in compact for word in ('따라잡', '닿을', '잡을수', '추격')):
        parts = catch_sentences(view)
        return ('race', parts) if parts else None
    if racing and any(word in compact for word in ('간격', '몇초차', '몇초야', '차이얼마', '얼마나떨어', '얼마나벌어')) and not re.search(r'\d+위', compact):
        sides = ['ahead'] if '앞' in compact and '뒤' not in compact else ['behind'] if '뒤' in compact and '앞' not in compact else ['ahead', 'behind']
        parts = gap_sentences(view, sides)
        return ('race', parts) if parts else None
    if any(word in compact for word in ('순위', '몇등', '몇위')) and not re.search(r'\d+위', compact):
        position = number(snapshot.get('class_position'))
        return ('spotter', [sentence('position_now', pos=position)]) if position and position > 0 else None
    if racing and any(word in compact for word in ('몇랩남', '남은랩', '랩얼마나')) and view.get('finish_laps_to_go'):
        # The counted distance to the flag: one number, no maybes.
        return 'strategy', [sentence('laps_to_go', laps=view['finish_laps_to_go'])]
    if any(word in compact for word in ('남은시간', '얼마나남', '몇분남', '시간얼마', '몇랩남')):
        left, laps = number(snapshot.get('session_time_remaining_s')), number(snapshot.get('session_laps_remaining'))
        if laps is not None and 1 <= laps <= 200 and '랩' in compact:
            return 'spotter', [sentence('laps_left', laps=laps)]
        if left is not None and 60 <= left < 86400 and left // 60 < 600:
            return 'spotter', [sentence('time_left', min=left // 60)]
        return None
    if any(word in compact for word in ('랩타임', '랩기록')):
        best = any(word in compact for word in ('최고', '베스트', '가장빠른'))
        value = number(snapshot.get('lap_best_s' if best else 'lap_last_s'))
        if value is not None and 0 < value < 3600:
            return 'spotter', [sentence('personal_best_read' if best else 'lap_time_read', lap=value)]
    return None
