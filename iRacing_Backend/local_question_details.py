"""Bounded, on-request explanation facts for local speech questions."""
import math

from instant_answers import number
import local_question_audio as question_audio


LINES = {key: normal for key, (_, normal, _) in question_audio.LINES.items()
         if key not in question_audio.PROMPT_KEYS}


def fixed_catalog(style='default'):
    """Reviewable fixed wording only; this never synthesizes or downloads audio."""
    return tuple((key, *question_audio.wording(key, style)) for key in LINES)


def current_corner(context):
    view = context.get('corner_coach') or {}
    review = view.get('last_lap') or {}
    lap = number(review.get('lap'))
    snapshot = context.get('snapshot') or {}
    if (view.get('comparison_skipped_reason') or lap is None
            or context.get('review_lap_number', snapshot.get('lap_number')) != lap + 1):
        return None
    losses = review.get('losses') or []
    worst = losses[0] if losses else {}
    delta = number(worst.get('delta_s'))
    if not worst.get('id') or not worst.get('call') or delta is None or not .1 <= delta < 10:
        return None
    return review, worst


def corner_repetition(context):
    selected = current_corner(context)
    if not selected:
        return {}
    review, worst = selected
    # Each retained lap contains *all* comparable corners. Absence from the
    # top-three loss list cannot be treated as a clean/no-loss passage.
    records = context.get('question_corner_history') or []
    selected_rows = []
    for record in reversed(records[-5:]):
        lap = number(record.get('lap'))
        if lap is None or lap > review['lap'] or lap < review['lap'] - 4:
            continue
        row = next((row for row in record.get('corners', ()) if row.get('id') == worst['id']), None)
        delta = number((row or {}).get('delta_s'))
        if delta is not None and all(old['lap'] != lap for old in selected_rows):
            selected_rows.append({'lap': lap, 'delta_s': delta})
        if len(selected_rows) == 3:
            break
    if (len(selected_rows) != 3 or selected_rows[0]['lap'] != review['lap']
            or selected_rows[0]['delta_s'] < .1):
        return {}
    count = sum(row['delta_s'] >= .1 for row in selected_rows)
    return {'basis': 'own_clean_segment_best_at_each_lap', 'corner_id': worst['id'],
            'corner_call': worst['call'], 'lap': review['lap'], 'observations': list(reversed(selected_rows)),
            'loss_laps': count, 'compared_laps': 3,
            'line': 'corner_repeat_all' if count == 3 else 'corner_repeat_some' if count == 2 else 'corner_repeat_once'}


def fuel_saving(context):
    pit = context.get('pit_wall') or {}
    if not pit.get('active'):
        return {}
    target, remaining = number(pit.get('fuel_target_l_per_lap')), number(pit.get('remaining_laps'))
    consumption = number(context.get('fuel_avg_l_per_lap'))
    fuel = number((context.get('snapshot') or {}).get('fuel_l'))
    # Reuse the pit calculator's finish endpoint, without adding a spare lap or
    # confusing its separate reserve goal with the literal finish-only question.
    if (target is None or not .01 <= target <= 30 or remaining is None or remaining <= 0
            or consumption is None or not 0 < consumption <= 30 or fuel is None or fuel < 0):
        return {}
    target_hundredths = math.floor(target * 100 + 1e-8)
    if target_hundredths < 1:
        return {}
    snapshot = context.get('snapshot') or {}
    now, lap = number(snapshot.get('session_time')), number(snapshot.get('lap_number'))
    observed = []
    for row in (context.get('fuel_feedback') or [])[-3:]:
        use, at, finished = number(row.get('use_l')), number(row.get('at')), number(row.get('lap'))
        if (use is not None and use > 0 and now is not None and at is not None and 0 <= now - at <= 900
                and lap is not None and finished is not None and lap - 3 <= finished < lap):
            observed.append({'lap': finished, 'use_l': use, 'met_current_target': use <= target_hundredths / 100})
    return {'basis': 'current_finish_fuel_calculation', 'target_l_per_lap': target_hundredths / 100,
            'average_l_per_lap': consumption, 'remaining_laps': remaining,
            'fuel_l': fuel, 'observed_laps': observed,
            # Observed use is evidence, never a guarantee that future laps or
            # an unobserved change in driving can achieve the target.
            'feasibility': 'not_guaranteed',
            'mandatory_stops_remaining': number(pit.get('mandatory_stops_remaining')),
            'energy_shortfall_pct': number(pit.get('energy_finish_shortfall_pct')) if context.get('game') == 'lmu' else None,
            'finish_may_extend': pit.get('finish_may_extend')}


def catching(context, side):
    pit = context.get('pit_wall') or {}
    car = pit.get(side) or {}
    gap, rate = number(car.get('gap_at_line_s')), number(car.get('gap_change_s_per_lap'))
    remaining = number(pit.get('remaining_laps'))
    if (not pit.get('active') or car.get('on_pit_road') or car.get('track_surface') in (-1, 0)
            or car.get('gap_trend') != 'closing' or gap is None or not 0 < gap <= 180
            or rate is None or not -10 < rate < 0):
        return {}
    estimate = gap / -rate
    return {'basis': 'measured_gap_trend_continues', 'side': side, 'rival_idx': car.get('idx'),
            'gap_at_line_s': gap, 'closing_s_per_lap': -rate,
            'catch_laps': estimate, 'spoken_catch_laps': math.ceil(estimate),
            'remaining_laps': remaining,
            'before_finish': estimate <= remaining if remaining is not None and remaining > 0 else None}


def evidence(topic, context):
    if topic == 'fuel_save':
        return fuel_saving(context)
    if topic == 'corner_repeat':
        return corner_repetition(context)
    if topic in ('catch_ahead', 'defend_time', 'closing_behind'):
        return catching(context, 'ahead' if topic == 'catch_ahead' else 'behind')
    if topic in ('corner_loss', 'corner_gain'):
        review = (context.get('corner_coach') or {}).get('last_lap') or {}
        return {'basis': 'own_clean_segment_best_at_each_lap', 'lap': review.get('lap')}
    if topic in ('fuel_finish', 'fuel_add'):
        pit = context.get('pit_wall') or {}
        return {key: number(pit.get(key)) for key in (
            'fuel_finish_shortfall_l', 'fuel_next_stop_l', 'fuel_reserve_l', 'fuel_l_per_lap', 'remaining_laps')}
    if topic == 'race_focus':
        focus = context.get('question_focus') or {}
        return {'basis': focus.get('basis'), 'reason': focus.get('reason'), 'key': focus.get('key'),
                'ahead': catching(context, 'ahead'), 'behind': catching(context, 'behind')}
    return {}
