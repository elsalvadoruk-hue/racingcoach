"""Deterministic workbench planning and verification of a single planned change."""
import math


def setup_plan(api, telemetry, body):
    request = api.AnalyzeRequest(
        session_mode=body.get('session_mode', 'BASELINE_3LAP'),
        feedback_phase=body.get('phase') or None, feedback_speed=body.get('speed') or None,
        feedback_symptom=body.get('symptom') or None, user_feedback=body.get('note', ''),
        setup_mode='EXPLORE', persist_memory=False)
    analyzer = api.TelemetryAnalyzer(telemetry)
    full = analyzer.analyze(request)
    corners = [*(full.get('performance') or {}).get('corners', []),
               *(full.get('coaching') or {}).get('clean_segment_corners', [])]
    unique = {corner['id']: corner for corner in corners}
    selected = body.get('corner_id')
    if selected and selected not in unique:
        raise ValueError('선택한 구간을 이 주행에서 찾지 못했습니다. 구간을 다시 선택하세요.')
    scoped = [unique[selected]] if selected else list(unique.values())
    feedback = analyzer._structured_feedback(request, scoped, {})
    summary = api.extract_setup_summary(telemetry.setup)
    # Canonical A/B keys omit the units present in the summary's height keys.
    summary['front_ride_height'] = summary.get('front_ride_height_mm')
    summary['rear_ride_height'] = summary.get('rear_ride_height_mm')
    hypothesis = api.setup_exploration_candidate(request, summary, feedback, len(analyzer.valid_laps), set())
    return {
        'filename': telemetry.path.name,
        'session': {key: full['session'].get(key, telemetry.metadata.get(key)) for key in
                    ('car_name', 'track_name', 'track_config', 'setup_name', 'track_temp_c')},
        'setup': summary, 'valid_laps': len(analyzer.valid_laps),
        'corners': [{'id': c['id'], 'label': c.get('label') or c['id'],
                     'speed_class': c.get('speed_class'), 'min_speed_kph': (c.get('target') or {}).get('min_speed_kph'),
                     'delta_s': c.get('delta_s')} for c in unique.values()],
        'feedback': feedback, 'hypothesis': hypothesis,
        'candidate': hypothesis.get('candidate'),
    }


def verify_planned_change(comparison, planned):
    """Keep subjective outcomes, but never learn an effect for a different change."""
    changes = comparison.get('canonical_setup_changes') or []

    def matches(change):
        try:
            values = [float(change[key]) for key in ('baseline_value', 'candidate_value')]
            requested = [float(planned[key]) for key in ('original_value', 'value')]
            return (change['setting'] == planned['setting'] and all(map(math.isfinite, values + requested))
                    and all(math.isclose(a, b, abs_tol=1e-5) for a, b in zip(values, requested)))
        except (ValueError, TypeError, KeyError):
            return False

    comparison['plan_matches'] = len(changes) == 1 and matches(changes[0])
    if not comparison['plan_matches']:
        comparison.update(effect_confirmed=False, verdict='INCONCLUSIVE',
                          reason='IBT의 실제 변경 항목·값이 계획과 다릅니다. 체감은 남길 수 있지만 이 계획의 효과는 미확정입니다.')
        eligibility = comparison.get('learning_eligibility') or {}
        comparison['learning_eligibility'] = {**eligibility, 'eligible': False,
            'reasons': [*(eligibility.get('reasons') or []), '실제 변경 항목·값이 실험 계획과 다릅니다.']}
    return comparison
