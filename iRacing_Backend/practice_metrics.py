"""Shared, measurable practice goals. Missing evidence never satisfies a criterion."""
import math

VERSION = 2
TARGETS = {
    'THROTTLE_RETURN_LATE': ('full_throttle_pct', 'm', -1, 3.0, '풀스로틀 지점'),
    'BRAKE_RELEASE_LATE': ('brake_release_pct', 'm', -1, 3.0, '브레이크 해제 지점'),
    'OVER_BRAKING_ABS': ('abs_duty_pct', '%p', -1, 5.0, 'ABS 개입'),
    'EXCESS_COASTING': ('coast_time_s', '초', -1, 0.04, '코스팅 시간'),
    'BRAKE_POINT_EARLY': ('brake_start_pct', 'm', 1, 3.0, '제동 시작 지점'),
    'EXIT_INSTABILITY': ('throttle_relifts', '회', -1, 1.0, '스로틀 재리프트'),
    'ROTATION_DEFICIT': ('avg_abs_steer_deg', '°', -1, 2.0, '평균 조향량'),
}
# metric, minimum/maximum change, strict inequality, user-facing success condition
GUARDS = {
    'THROTTLE_RETURN_LATE': [('exit_speed_kph', 'min', 1.5, False, '탈출 속도 +1.5 km/h 이상'),
                             ('throttle_relifts', 'max', 0, False, '스로틀 재리프트 증가 없음')],
    'BRAKE_RELEASE_LATE': [('min_speed_kph', 'min', 1.5, False, '최저속도 +1.5 km/h 이상'),
                           ('avg_abs_steer_deg', 'max', 0, False, '평균 조향량 증가 없음'),
                           ('abs_duty_pct', 'max', 5, False, 'ABS 듀티 증가 ≤5%p')],
    'OVER_BRAKING_ABS': [('min_speed_kph', 'min', 0, False, '최저속도 저하 없음'),
                         ('braking_distance_m', 'max', 0, False, '제동 거리 악화 없음')],
    'EXCESS_COASTING': [('min_speed_kph', 'min', 1.5, False, '최저속도 +1.5 km/h 이상'),
                        ('steering_corrections', 'max', 0, False, '조향 수정 증가 없음')],
    'BRAKE_POINT_EARLY': [('min_speed_kph', 'min', 1, False, '최저속도 +1 km/h 이상'),
                          ('abs_duty_pct', 'max', 5, False, 'ABS 증가 ≤5%p')],
    'EXIT_INSTABILITY': [('steering_corrections', 'max', 0, True, '조향 수정 감소'),
                         ('exit_speed_kph', 'min', 0, False, '탈출 속도 저하 없음')],
    'ROTATION_DEFICIT': [('min_speed_kph', 'min', 0, False, '최저속도 유지 또는 증가'),
                         ('steering_efficiency', 'min', 0, True, '조향 대비 회전량 증가')],
}
KEPT_METRICS = sorted({t[0] for t in TARGETS.values()} | {g[0] for gs in GUARDS.values() for g in gs}
                      | {'braking_distance_m', 'distance_resolution_m'})


def number(value):
    return float(value) if type(value) in (int, float) and math.isfinite(value) else None


def change(before, after, unit='', length=None):
    a, b = number(before), number(after)
    if a is None or b is None:
        return None
    if unit == 'm':
        if not number(length) or length <= 0:
            return None
        return ((b - a + 0.5) % 1.0 - 0.5) * length
    return b - a


def evaluate(cause, before, after, length):
    target = TARGETS.get(cause)
    delta = change(before.get(target[0]), after.get(target[0]), target[1], length) if target else None
    primary = None if delta is None else delta * target[2] >= target[3] - 1e-8
    checks = []
    for metric, direction, limit, strict, label in GUARDS.get(cause, []):
        d = change(before.get(metric), after.get(metric))
        margin = None if d is None else (d - limit if direction == 'min' else limit - d)
        ok = None if margin is None else margin > 1e-8 if strict else margin >= -1e-8
        checks.append({'metric': metric, 'label': label, 'change': round(d, 3) if d is not None else None, 'ok': ok})
    states = [primary] + [c['ok'] for c in checks]
    goal = False if False in states else None if None in states else True
    return {'input_change': round(delta, 3) if delta is not None else None, 'input_ok': primary,
            'goal_ok': goal, 'criteria': checks}


def success_criteria(cause):
    target = TARGETS.get(cause)
    if not target:
        return []
    return [f'{target[4]} {target[3]:g} {target[1]} 이상 {"앞당김" if target[1] == "m" and target[2] < 0 else "지연" if target[1] == "m" else "감소"}'] + [g[4] for g in GUARDS[cause]]
