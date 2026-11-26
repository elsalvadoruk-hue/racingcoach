"""Voice-only measured questions; no new polling, synthesis or game commands."""
import math

from instant_answers import instant_answer, number
from radio_buttons import TOPICS as BUTTON_TOPICS, wordings as button_wordings
from radio_phrases import build


EXTRA_TOPICS = {
    'race_focus': ('레이스 판단', '지금 집중할 레이스 판단'),
    'sof': ('랩·세션', '현재 클래스 SOF'),
    'fuel_finish': ('연료', '완주 연료 확인'),
    'fuel_add': ('연료', '이번 피트 추가 급유량'),
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


def render(topic, context, buttons):
    """Keep each labelled answer atomic; report missing fields explicitly."""
    import button_fuel_audio
    style = button_fuel_audio.style_for(getattr(buttons.coach, 'custom_packs', None))
    parts, spoken, display, missing_data, missing_audio = [], [], [], [], []
    seen = set()
    for item in REPORTS.get(topic, (topic,)):
        candidates = wordings(item, context, style)
        if not candidates:
            missing_data.append(TOPICS[item][1])
            continue
        clips = []
        try:
            for role, text in candidates:
                fuel_take = button_fuel_audio.by_wording().get(text) if role == 'strategy' else None
                clips.append(buttons.read_clip(fuel_take[0] if fuel_take else item, role, text))
        except (OSError, ValueError, KeyError):
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
    return {'parts': parts, 'spoken': spoken, 'display': display,
            'missing_data': missing_data, 'missing_audio': missing_audio}
