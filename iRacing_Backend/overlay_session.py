"""Compact session facts from measured timing and the existing race engineer."""
from practice_metrics import number


def lap_time(value):
    value = number(value)
    return f'{int(value//60)}:{value%60:06.3f}' if value is not None and value > 0 else '—'


def session_panel(kind, sample, delta=None, race=None):
    kind = str(kind or '').lower()
    if 'qual' in kind:
        change = (delta or {}).get('delta_s')
        title = f'퀄리 · 구간 Δ {change:+.3f}초' if number(change) is not None else '퀄리 · 기준 기록 측정 중'
        remaining = number(sample.get('session_time_remaining_s'))
        return {'kind': 'QUALIFY', 'title': title, 'badge': 'QUALI',
                'left': f"베스트 {lap_time(sample.get('lap_best_s'))}",
                'right': f'남은 {int(remaining//60)}분 {int(remaining%60)}초' if remaining is not None and 0 <= remaining < 86400 else '남은 시간 미측정'}
    if kind == 'race':
        race = race or {}
        position = number(race.get('class_position'))
        fuel = race.get('fuel_laps')
        title = f'경기 · P{int(position)}' if position is not None and position >= 1 and position.is_integer() else '경기 · 순위 확인 중'
        title += f' · 연료 {fuel:.1f}랩' if number(fuel) is not None else ' · 연료 측정 중'
        def gap(side, label):
            car = race.get(side) or {}
            value = car.get('gap_estimate_s')
            laps = car.get('lap_difference')
            if number(laps) is not None and abs(laps) >= 1:
                return f'{label} {abs(laps):.1f}랩 차'
            return f'{label} 약 {value:.1f}초' if number(value) is not None else f'{label} —'
        return {'kind': 'RACE', 'title': title, 'badge': 'RACE',
                'left': gap('ahead', '앞'), 'right': gap('behind', '뒤')}
    return {'kind': 'PRACTICE' if 'practice' in kind or kind == 'testing' else 'LIVE',
            'title': None, 'badge': 'LIVE', 'left': None, 'right': None}
