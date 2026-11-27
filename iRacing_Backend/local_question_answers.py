"""Voice-only measured questions; no new polling, synthesis or game commands."""
import math

from instant_answers import instant_answer, number
from radio_buttons import TOPICS as BUTTON_TOPICS, wordings as button_wordings
from radio_phrases import build
import local_question_details as details
import local_question_audio as question_audio


EXTRA_TOPICS = {
    'race_focus': ('레이스 판단', '지금 집중할 레이스 판단'),
    'sof': ('랩·세션', '현재 클래스 SOF'),
    'fuel_finish': ('연료', '완주 연료 확인'),
    'fuel_add': ('연료', '이번 피트 추가 급유량'),
    'fuel_save': ('연료', '절약해서 완주할 연료 목표'),
    'corner_repeat': ('코너 코칭', '최근 코너 손실 반복 여부'),
    'race_time': ('랩·세션', '경기 남은 시간'),
    'incidents': ('랩·세션', '인시던트와 한도까지 여유'),
    'incident_limit': ('랩·세션', '인시던트 한도'),
    'pit_speed': ('피트', '피트 제한 속도'),
    'defend_time': ('경쟁차', '뒤차가 따라잡기까지 예상 랩'),
    'ahead_gain': ('경쟁차', '앞차보다 빠른 구간'),
    'ahead_loss': ('경쟁차', '앞차보다 느린 구간'),
    'behind_gain': ('경쟁차', '뒤차보다 빠른 구간'),
    'behind_loss': ('경쟁차', '뒤차보다 느린 구간'),
    'report_fuel': ('종합 보고', '연료 상황 보고'),
    'report_race': ('종합 보고', '경기 상황 보고'),
    'report_ahead': ('종합 보고', '앞차 상황 보고'),
    'report_behind': ('종합 보고', '뒤차 상황 보고'),
    'report_pace': ('종합 보고', '내 랩 기록 보고'),
    'report_pit': ('종합 보고', '피트 전략 보고'),
}
TOPICS = {**BUTTON_TOPICS, **EXTRA_TOPICS}
REPORTS = {
    'report_fuel': ('fuel', 'fuel_range', 'fuel_average', 'fuel_finish'),
    'report_race': ('position', 'race_time', 'laps', 'ahead', 'behind'),
    'report_ahead': ('ahead', 'ahead_lap', 'stint_ahead', 'stops_ahead'),
    'report_behind': ('behind', 'closing_behind', 'stint_behind', 'stops_behind'),
    'report_pace': ('last', 'best', 'theoretical'),
    'report_pit': ('fuel_add', 'pit_rejoin', 'pit_transit'),
}
RIVAL_TOPICS = {'ahead_gain', 'ahead_loss', 'behind_gain', 'behind_loss'}


def enrich(topic, context, live):
    """Read small extra facts only on request, under the existing live lock."""
    items = REPORTS.get(topic, (topic,))
    with live.lock:
        _, sample = live._current_snapshot()
        if not sample or sample.get('run_id') != (context.get('snapshot') or {}).get('run_id') or live.game != context.get('game'):
            return
        if topic == 'race_focus':
            from race_focus import prepare
            prepare(context, live)
            return
        if 'theoretical' in items:
            context['theoretical'] = live.lap_sectors.theoretical()
        if 'sector_delta' in items:
            context['sector_delta'] = live.lap_sectors.delta()
        if 'corner_repeat' in items:
            corners = getattr(live, 'corners', None)
            context['question_corner_history'] = [
                {'lap': row['lap'], 'corners': [dict(c) for c in row['corners']]}
                for row in getattr(corners, 'question_history', ())]
        if topic in ('incidents', 'incident_limit'):
            context['question_incident_limit'] = getattr(getattr(live, 'radio', None), 'metadata', {}).get('incident_limit')
        if topic == 'sof' and context.get('game')=='iracing':
            from live_formation import strength_of_field
            race=getattr(getattr(live,'pit_wall',None),'race',{}) or {}
            own=next((car for car in race.get('cars',[]) if car.get('idx')==race.get('player_idx')), {})
            cars=[car for car in race.get('cars',[]) if own.get('class_id') is not None and car.get('class_id')==own['class_id'] and not car.get('is_pace_car')]
            if (len(cars)>=2 and str(race.get('session_type','')).lower()=='race'
                    and all(not car.get('is_ai') and number(car.get('irating')) is not None and 0<car['irating']<20000 for car in cars)):
                context['question_sof']=strength_of_field(car['irating'] for car in cars)
        if topic in RIVAL_TOPICS:
            from live_sectors import place_name
            side = topic.split('_')[0]
            car = (context.get('pit_wall') or {}).get(side) or {}
            engine = getattr(live, 'pit_wall', None)
            sectors = getattr(live, 'rival_sectors', None)
            if not engine or not sectors or car.get('idx') is None:
                return
            rows = sectors.compare(engine.race.get('player_idx'), car['idx'])
            definitions = getattr(getattr(live, 'corners', None), 'definitions', [])
            context['question_rival_sections'] = [
                {**row, 'place': place_name(row['middle_pct'], row['sector'], definitions)} for row in rows]


def phrase(key, **values):
    item = build(key, **values)
    # Roles are declared in the original prepared-voice catalogue.
    from radio_phrases import PHRASES
    return PHRASES[key][0], item['text']


def wordings(topic, context, style='default'):
    if topic == 'race_focus':
        from race_focus import words
        return words(context, style)
    if topic == 'fuel_save':
        facts = details.fuel_saving(context)
        if not facts:
            return []
        from race_strategy_clips import defence_words
        target = round(facts['target_l_per_lap'] * 100)
        target_words = defence_words(target, None)[1 if style == 'short' else 0]
        return [('strategy', target_words), *button_wordings('fuel_average', context, style)]
    if topic == 'corner_repeat':
        facts = details.corner_repetition(context)
        if not facts:
            return []
        # Name the same corner as the immediately preceding lap's review.
        return [*button_wordings('corner_loss', context, style), question_audio.wording(facts['line'], style)]
    if topic in BUTTON_TOPICS:
        return button_wordings(topic, context, style)
    snapshot, view = context.get('snapshot') or {}, context.get('pit_wall') or {}
    if topic == 'sof':
        value=number(context.get('question_sof'))
        return [phrase('form_sof_class',rating=value)] if context.get('game')=='iracing' and value is not None and 100<=value<=10000 else []
    if topic == 'race_time':
        reply = instant_answer('남은 시간', context)
        return [(reply[0], text) for text in reply[1]] if reply else []
    if topic == 'pit_speed':
        speed = number((context.get('radio') or {}).get('pit_speed_limit_kph'))
        return [phrase('form_pit_speed', kph=speed)] if speed is not None and 0 < speed <= 200 else []
    if topic in ('incidents', 'incident_limit'):
        limit = number(context.get('question_incident_limit'))
        points = number(snapshot.get('incidents'))
        if limit is None or limit != int(limit) or not 1 <= limit <= 1000:
            return []
        if topic == 'incident_limit':
            return [phrase('form_incident_limit', points=limit)]
        if points is not None and points == int(points) and 0 <= points <= limit:
            return [phrase('incident_limit', points=points, left=limit-points)]
        return []
    if not view.get('active'):
        return []
    if topic == 'fuel_finish':
        short = number(view.get('fuel_finish_shortfall_l'))
        if short is None or short < 0:
            return []
        if short > 0:
            return [phrase('fuel_short_by', short=math.ceil(short))]
        # No prepared yes/no sentence: read the range and finish distance as a
        # pair, never tell a driver to stop saving or change their pace.
        distance = button_wordings('fuel_range', context, style)
        remaining = button_wordings('laps', context, style)
        return distance + remaining if distance and remaining else []
    if topic == 'fuel_add':
        from race_engineer import RaceEngineer
        amount = number(view.get('fuel_next_stop_l'))
        if amount is None or amount < 0:
            return []
        if amount == 0:
            return [phrase('pit_fuel_add', liters=0)]
        if view.get('fuel_next_stop_full') and not (number(view.get('tank_capacity_l')) or 0) > 0:
            return []
        liters = RaceEngineer._pit_fuel_litres(view, snapshot)
        if liters is not None and 1 <= liters <= 1000:
            return [phrase('pit_fuel_full' if view.get('fuel_next_stop_full') else 'pit_fuel_add', liters=liters)]
    if topic == 'defend_time':
        car = view.get('behind') or {}
        change, gap = number(car.get('gap_change_s_per_lap')), number(car.get('gap_at_line_s'))
        if (not car.get('on_pit_road') and car.get('track_surface') not in (-1, 0)
                and car.get('gap_trend') == 'closing' and change is not None and change < 0
                and gap is not None and gap > 0):
            laps = math.ceil(gap / -change)
            if 1 <= laps <= 30:
                return [phrase('caught_in', laps=laps)]
    if topic in RIVAL_TOPICS:
        side, metric = topic.split('_')
        car = view.get(side) or {}
        if not car or car.get('on_pit_road') or car.get('track_surface') in (-1, 0):
            return []
        rows = [r for r in context.get('question_rival_sections', []) if number(r.get('delta_s')) is not None]
        row = (min if metric == 'gain' else max)(rows, key=lambda r: r['delta_s'], default=None)
        delta = row['delta_s'] if row else 0
        if (metric == 'gain' and -.1 >= delta > -10) or (metric == 'loss' and .1 <= delta < 10):
            direction = 'faster' if metric == 'gain' else 'slower'
            return [phrase(f'rival_{side}_{direction}_at', place=row['place']), phrase('by_'+direction, loss=abs(delta))]
    return []


def _groups(topic, context, style, detail):
    """Independent facts may survive missing optional audio, labelled pairs may not."""
    words = wordings(topic, context, style)
    if not words:
        return []
    groups = [(topic, words)]
    if topic == 'fuel_finish' and (number((context.get('pit_wall') or {}).get('fuel_finish_shortfall_l')) or 0) > 0:
        extra = wordings('fuel_add', context, style)
        if extra:
            groups.append(('fuel_add', extra))
    elif topic in ('catch_ahead', 'defend_time'):
        extra = button_wordings('laps', context, style)
        if extra:
            groups.append(('laps', extra))
    if topic == 'fuel_save':
        facts = details.fuel_saving(context)
        caveats = []
        if (facts.get('mandatory_stops_remaining') or 0) > 0:
            caveats.append(('strategy', details.LINES['fuel_stop_required']))
        if (facts.get('energy_shortfall_pct') or 0) > .1:
            caveats.append(('strategy', details.LINES['fuel_energy_short']))
        # A no-stop fuel question must not lose a known non-fuel reason for a
        # stop just because that caveat's recording is missing. Keep it atomic.
        groups[0] = (topic, words + caveats)
    if detail in ('short', 'short_explain'):
        if topic == 'catch_ahead':
            # Preserve both a prediction and its remaining race distance.
            facts = details.catching(context, 'ahead')
            if facts and 1 <= facts['spoken_catch_laps'] <= 30:
                groups[0] = (topic, [phrase('catch_in', laps=facts['spoken_catch_laps'])])
        if detail == 'short':
            return groups
    if detail in ('explain', 'short_explain'):
        if topic in ('catch_ahead', 'defend_time'):
            facts = details.catching(context, 'ahead' if topic == 'catch_ahead' else 'behind')
            if topic == 'defend_time':
                basis = button_wordings('behind', context, style) + button_wordings('closing_behind', context, style)
                if basis:
                    groups.append(('defend_time', basis))
            if facts.get('before_finish') is not None:
                key = ('catch' if topic == 'catch_ahead' else 'defend') + ('_within' if facts['before_finish'] else '_after')
                groups.append((topic, [('race', details.LINES[key])]))
        elif topic in ('fuel_finish', 'fuel_add'):
            groups.append((topic, [('strategy', details.LINES['fuel_basis'])]))
            if (number((context.get('pit_wall') or {}).get('fuel_reserve_l')) or 0) > 0:
                groups.append((topic, [('strategy', details.LINES['fuel_reserve'])]))
            extra = button_wordings('fuel_average', context, style) + button_wordings('laps', context, style)
            if extra:
                groups.append((topic, extra))
        elif topic in ('corner_loss', 'corner_gain', 'corner_repeat'):
            groups.append((topic, [('coach', details.LINES['corner_basis'])]))
        elif topic == 'race_focus':
            # Compare the two observed prospects, without inventing a counterfactual
            # lap time for saving fuel or assuming that catching means overtaking.
            focus = context.get('question_focus') or {}
            key = focus.get('key')
            if key in ('pit_now_gap', 'stay_for_gap'):
                items = ('pit_rejoin', 'pit_transit')
            elif key in ('energy_early', 'energy_save_plan'):
                items = ('energy_remaining',)
            elif key in ('pit_resources', 'plan_both_early', 'plan_fuel_early', 'plan_both_save',
                         'plan_fuel_save', 'fuel_defence_target', 'fuel_defence', 'save_stop', 'save_fill'):
                items = ('fuel_finish', 'fuel_average')
                if key in ('plan_both_early', 'plan_both_save'):
                    items += ('energy_remaining',)
            elif key in ('defend_section', 'chase_section'):
                items = ('catch_ahead', 'defend_time')
            else:
                items = ()
            for item in items:
                extra = wordings(item, context, style)
                if extra:
                    groups.append((item, extra))
            extra = button_wordings('laps', context, style)
            if extra:
                groups.append(('laps', extra))
    if topic == 'fuel_save':
        if detail in ('explain', 'short_explain'):
            groups.append((topic, [('strategy', details.LINES['fuel_target_only'])]))
    return groups


def render(topic, context, buttons, *, detail='default'):
    """Keep each labelled answer atomic; report missing fields explicitly."""
    import button_fuel_audio
    packs = getattr(buttons.coach, 'custom_packs', None)
    style = ('short' if detail in ('short', 'short_explain') else button_fuel_audio.style_for(packs))
    parts, spoken, display, missing_data, missing_audio = [], [], [], [], []
    seen = set()
    groups = []
    for item in REPORTS.get(topic, (topic,)):
        prepared = _groups(item, context, style, detail)
        if not prepared:
            missing_data.append(TOPICS[item][1])
            continue
        groups.extend(prepared)
    for item, candidates in groups:
        candidates = [question_audio.select(role, text, packs, detail=detail) for role, text in candidates]
        clips = []
        try:
            for role, text in candidates:
                fuel_take = button_fuel_audio.by_wording().get(text) if role == 'strategy' else None
                clips.append(buttons.read_clip(fuel_take[0] if fuel_take else item, role, text))
        except (OSError, ValueError, KeyError):
            if TOPICS[item][1] not in missing_audio:
                missing_audio.append(TOPICS[item][1])
            display.extend(text for _, text in candidates)
            continue
        for (_, text), clip in zip(candidates, clips):
            if text not in seen:
                parts.append(clip)
                spoken.append(text)
                display.append(text)
                seen.add(text)
    if topic == 'race_focus' and display:
        focus = context['question_focus']
        display = [f"지금 우선할 일: {focus['label']}.", focus['reason'], *display]
    evidence = details.evidence(topic, context)
    explanation = []
    data_notes = []
    def explanation_line(key):
        return question_audio.select(*question_audio.wording(key), packs, detail=detail)[1]
    if topic == 'corner_repeat' and not evidence:
        data_notes.append('반복 여부를 판단하려면 최근 다섯 랩 안에 같은 구간의 비교 가능한 기록이 세 랩 필요합니다.')
    if topic == 'fuel_save' and not evidence:
        data_notes.append('완주 거리와 연료 소비 기록이 확보되어야 절약 목표를 계산할 수 있습니다.')
    if topic in ('corner_loss', 'corner_gain') and display:
        explanation.append(explanation_line('corner_basis'))
    if topic == 'fuel_save' and display:
        explanation.append(explanation_line('fuel_target_only'))
    if topic in ('catch_ahead', 'defend_time') and evidence.get('before_finish') is not None:
        key = ('catch' if topic == 'catch_ahead' else 'defend') + ('_within' if evidence['before_finish'] else '_after')
        explanation.append(explanation_line(key))
    display.extend(line for line in explanation + data_notes if line not in display)
    return {'parts': parts, 'spoken': spoken, 'display': display,
            'missing_data': missing_data, 'missing_audio': missing_audio,
            'answer_evidence': evidence, 'explanation': explanation, 'data_notes': data_notes, 'detail': detail}
