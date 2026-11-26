"""On-request race priorities from existing measured decisions, without an LLM."""
import math
import hashlib
import json

from instant_answers import number

PLAN_ORDER = (
    ('plan_both_early', '피트 시점 앞당기기'), ('plan_fuel_early', '피트 시점 앞당기기'),
    ('energy_early', '피트 시점 앞당기기'), ('plan_both_save', '예정 피트까지 절약'),
    ('plan_fuel_save', '예정 피트까지 연료 절약'), ('energy_save_plan', '예정 피트까지 에너지 절약'),
    ('pit_now_gap', '이번 랩 피트 복귀 공간'), ('stay_for_gap', '다음 랩 피트 복귀 공간'),
    ('fuel_defence_target', '완주 연료 목표'), ('fuel_defence', '절약 중 뒤차 페이스'),
    ('save_stop', '정차 횟수를 줄이는 절약'), ('save_fill', '급유 시간을 줄이는 절약'),
)


def prepare(context, live):
    """Called under the live lock. Never acknowledges/consumes automatic calls."""
    from local_question_answers import wordings
    from live_sectors import place_name
    context.pop('question_focus', None)
    s, pit = context.get('snapshot') or {}, context.get('pit_wall') or {}
    if (not pit.get('active') or s.get('on_track') is not True or s.get('is_replay') is not False
            or s.get('session_state') != 4 or s.get('on_pit_road') is not False
            or s.get('lmu_scoring_held') or s.get('session_flags') is None
            or int(s['session_flags']) & 0xBC119):
        return
    engine = getattr(live, 'race_plan', None)
    plan = pit.get('race_plan') or {}
    now = live.clock() if callable(getattr(live, 'clock', None)) else None
    if engine and now is not None and plan.get('run_id') == s.get('run_id'):
        for key, label in PLAN_ORDER:
            choice = engine.choices.get(key)
            if choice and engine.valid(key, choice[0], now, s):
                _, role, normal, short = engine.call(key, choice[0])
                context['question_focus'] = {'key':key, 'label':label, 'reason':choice[2],
                                             'normal':[(role,normal)], 'short':[(role,short)],
                                             'basis':'current_race_plan', 'session_time':s.get('session_time')}
                return
    # Do not recommend chasing a car while an unresolved imminent fuel/energy
    # problem needs attention. Use the same current pit calculator for numbers.
    low = (number(pit.get('fuel_laps')) is not None and pit['fuel_laps'] <= 2.3)
    energy_low = (context.get('game') == 'lmu' and number(pit.get('energy_laps')) is not None and pit['energy_laps'] <= 2.3)
    urgent = plan.get('target_call') in {'plan_both_early','plan_fuel_early','energy_early'}
    if low or energy_low or urgent:
        candidates = wordings('fuel_add', context) + wordings('fuel_range', context)
        if energy_low: candidates = wordings('energy_remaining', context) + candidates
        if candidates:
            context['question_focus'] = {'key':'pit_resources', 'label':'피트까지 남은 자원 확인',
                                         'reason':'현재 연료 또는 에너지로 갈 수 있는 거리가 짧습니다.',
                                         'normal':candidates, 'short':candidates, 'basis':'current_pit_resources'}
        return
    if plan.get('target_call') in {'plan_both_save','plan_fuel_save','energy_save_plan'}:
        return
    sectors, rivals = getattr(live, 'rival_sectors', None), getattr(live, 'pit_wall', None)
    if sectors is None or rivals is None: return
    if rivals.race.get('position_source') == 'HELD': return
    behind = pit.get('behind') or {}
    remaining = number(pit.get('remaining_laps'))
    gap, change = number(behind.get('gap_at_line_s')), number(behind.get('gap_change_s_per_lap'))
    closing = (remaining is not None and remaining > 0 and gap is not None and gap > 0
               and change is not None and change < 0 and behind.get('gap_trend') == 'closing'
               and math.ceil(gap / -change) <= remaining)
    # A closing rival gets priority over the front-car opportunity.
    side, metric = ('behind', 'loss') if closing else ('ahead', 'gain')
    car = pit.get(side) or {}
    if car.get('idx') is None or car.get('on_pit_road') is not False or car.get('track_surface') != 3:
        return
    rows = sectors.compare(rivals.race.get('player_idx'), car['idx'])
    definitions = getattr(getattr(live, 'corners', None), 'definitions', [])
    target = {**context, 'question_rival_sections': [
        {**row, 'place':place_name(row['middle_pct'], row['sector'], definitions)} for row in rows]}
    comparison = wordings(f'{side}_{metric}', target)
    timing = wordings('defend_time', context) if closing else []
    if not comparison:
        return
    context['question_focus'] = {
        'key':'defend_section' if closing else 'chase_section',
        'label':'뒤차에 시간을 잃는 구간' if closing else '앞차 추격에 유리한 구간',
        'reason':'현재 페이스가 이어지면 결승 전에 뒤차가 따라잡을 것으로 계산됩니다.' if closing else
                 '앞차와 반복 통과한 구간 기록을 비교했습니다. 실제 추월 가능 지점을 뜻하지 않습니다.',
        'normal':timing+comparison, 'short':timing+comparison,
        'basis':'rival_sector_medians', 'rival_idx':car['idx'], 'sections':len(rows),
    }


def words(context, style):
    focus = context.get('question_focus') or {}
    return focus.get('short' if style == 'short' else 'normal') or []


def signature(focus):
    if not focus: return ''
    value = {key:value for key,value in focus.items() if key != 'session_time'}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def current(live, expected):
    """Rebuild on demand so changed rivals, resources or plans invalidate playback."""
    if not expected: return False
    with live.lock:
        context = live.context(include_recent=False)
        prepare(context, live)
        return signature(context.get('question_focus')) == expected
