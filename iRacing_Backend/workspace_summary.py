"""Read-only practice presentation using the app's deterministic coach."""
from copy import deepcopy


def practice_summary(build_coach, full, corners, external=False):
    # Use the exact displayed comparison, including explicit self-comparisons.
    losses = sorted(corners, key=lambda c: c.get('delta_s') or 0, reverse=True)
    result = {**full, 'performance': {**full.get('performance', {}), 'top_losses': losses},
              'external_reference': {'top_losses': losses} if external else {}}
    coach = build_coach(result, {}, {})
    options = []
    for focus in coach['ranked_focuses'][:3]:
        labels = focus.get('affected_corners') or [focus.get('corner')]
        chosen = [c for c in losses if c.get('label') in labels]
        # Choosing a driving task is an explicit driving-only plan. The default
        # next run still retains the original engine's setup/observation modes.
        driving = {**result, 'setup_recommendation': {},
                   'performance': {**result['performance'], 'top_losses': chosen},
                   'external_reference': {'top_losses': chosen} if external else {}}
        plan = build_coach(driving, {}, {})['next_run']
        options.append({'focus': deepcopy(focus), 'next_run': plan,
                        'corner_ids': [c['id'] for c in chosen]})
    return {'options': options, 'next_run': coach['next_run'], 'policy': coach['policy'],
            'personal_history_used': False}


