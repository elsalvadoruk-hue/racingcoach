"""Lap-wide setup *possibility*, separate from setup recommendations/learning.

Version 1 uses conservative observation gates, not calibrated probabilities.
One reference is enough only when the pattern repeats on distinct straights.
Unknown tow/weather/fuel effects are never claimed to have been excluded.
"""
import math

import numpy as np


def _finite(value):
    return isinstance(value, (int, float, np.number)) and not isinstance(value, bool) and math.isfinite(value)


def setup_influence_notice(target_grid, reference_grid, corners, straight, *, pace_eligible,
                           lap_delta_s, current_conditions, reference_conditions):
    result = {'version': 1, 'status': 'NOT_SHOWN', 'reason': 'NO_LAP_WIDE_PATTERN',
              'setup_cause_confirmed': False, 'evidence': {},
              'limitations': ['UNCONTROLLED_REFERENCE_CONDITIONS']}

    def stop(reason, insufficient=False):
        return {**result, 'reason': reason, 'status': 'INSUFFICIENT' if insufficient else 'NOT_SHOWN'}

    if not pace_eligible or not _finite(lap_delta_s):
        return stop('INCOMPLETE_LAP', True)
    if lap_delta_s < .05:
        return stop('NO_MEANINGFUL_LAP_LOSS')
    for row in reference_conditions:
        condition = row.get('condition_similarity') or {}
        score = condition.get('score')
        if condition.get('setup_compatible') is False or (_finite(score) and score < .45):
            return stop('KNOWN_CONDITION_DIFFERENCE')
        for key, limit in (('fuel_l', 15.), ('track_temp_c', 10.), ('wind_speed_kph', 15.)):
            a, b = current_conditions.get(key), row.get(key)
            if _finite(a) and _finite(b) and abs(a - b) > limit:
                return stop('KNOWN_CONDITION_DIFFERENCE')

    keys = ('distance_pct', 'time_s', 'speed_kph', 'Throttle', 'Brake', 'SteeringWheelAngle')
    if not all(key in grid for grid in (target_grid, reference_grid) for key in keys):
        return stop('MISSING_CHANNELS', True)
    x = np.asarray(target_grid['distance_pct'])
    n = len(x)
    if n < 100 or x.ndim != 1 or x[0] > .001 or x[-1] < .999 or not np.all(np.isfinite(x)) or np.any(np.diff(x) <= 0):
        return stop('INCOMPLETE_DISTANCE_COVERAGE', True)
    for grid in (target_grid, reference_grid):
        if any(np.shape(grid[key]) != (n,) for key in keys) or not np.array_equal(x, grid['distance_pct']):
            return stop('UNALIGNED_GRIDS', True)
        if not all(np.all(np.isfinite(grid[key])) for key in keys):
            return stop('MISSING_SAMPLES', True)
        if np.any(np.diff(grid['time_s']) <= 0) or np.any(np.asarray(grid['speed_kph']) <= 0):
            return stop('INVALID_TIMING', True)
        if '_coverage' in grid and not np.all(grid['_coverage']):
            return stop('MISSING_SAMPLES', True)
    delta = np.asarray(target_grid['time_s']) - np.asarray(reference_grid['time_s'])
    observed_lap_delta = float(delta[-1] - delta[0])
    # The notice must describe the same lap comparison as the header. Aggregates
    # with inconsistent timing summaries remain available without this notice.
    if abs(observed_lap_delta - lap_delta_s) > .1 or observed_lap_delta < .05:
        return stop('INCONSISTENT_LAP_TIMING', True)

    bounds = []
    for corner in corners:
        a, b = corner.get('entry_pct'), corner.get('exit_pct')
        if not (_finite(a) and _finite(b) and x[0] <= a < b <= x[-1]):
            return stop('INVALID_CORNER_BOUNDARIES', True)
        bounds.append((a, b))
    bounds.sort()
    if len(bounds) < 3 or any(a < previous[1] for previous, (a, _) in zip(bounds, bounds[1:])):
        return stop('INSUFFICIENT_CORNER_COVERAGE', True)
    corner_deltas = [float(np.interp(b, x, delta) - np.interp(a, x, delta)) for a, b in bounds]
    corner_delta = sum(corner_deltas)
    outside_delta = observed_lap_delta - corner_delta
    gains = sum(value <= -.02 for value in corner_deltas)
    result['evidence'] = {'lap_delta_s': round(observed_lap_delta, 4), 'corner_delta_s': round(corner_delta, 4),
                          'outside_corner_delta_s': round(outside_delta, 4), 'gaining_corners': gains,
                          'compared_corners': len(bounds)}
    if gains < 3 or gains / len(bounds) < .6 or corner_delta > -.05 or outside_delta < abs(corner_delta) + .05:
        return stop('NO_CORNER_GAIN_STRAIGHT_LOSS_PATTERN')

    # Complementary intervals never overlap corners. The two ends of the lap
    # are the same physical straight, even when the finish line splits them.
    outside = [(a, b) for a, b in zip([float(x[0])] + [b for _, b in bounds],
                                     [a for a, _ in bounds] + [float(x[-1])]) if b > a]
    wrap = bool(outside and outside[0][0] == x[0] and outside[-1][1] == x[-1])
    supporting = {}
    for segment in straight.get('segments', []) if straight.get('status') == 'READY' else []:
        fields = ('start_pct', 'end_pct', 'distance_m')
        if not all(_finite(segment.get(key)) for key in fields):
            continue
        start, end = segment['start_pct'], segment['end_pct']
        if end <= start or segment['distance_m'] < 220:
            continue
        # Full throttle can start before the detected corner exit. Measure the
        # intersection again rather than discarding that entire straight or
        # including its corner portion in setup evidence.
        for index, (outside_start, outside_end) in enumerate(outside):
            a, b = max(start, outside_start), min(end, outside_end)
            if b <= a or segment['distance_m'] * (b - a) / (end - start) < 220:
                continue
            mask = (x >= a) & (x <= b)
            late = (x >= a + (b - a) * .7) & (x <= a + (b - a) * .9)
            if not np.any(mask) or not np.any(late):
                continue
            early = a + (b - a) * .15
            entry_gap = float(np.interp(early, x, reference_grid['speed_kph'])
                              - np.interp(early, x, target_grid['speed_kph']))
            late_gap = float(np.median(np.asarray(reference_grid['speed_kph'])[late]
                                       - np.asarray(target_grid['speed_kph'])[late]))
            if entry_gap > 2.5 or late_gap < 2.5 or late_gap - entry_gap < 1.8:
                continue
            controls_ok = True
            for grid in (target_grid, reference_grid):
                support = grid.get('_channel_support') or {}
                for key in keys[2:]:
                    if key in support and not np.all(np.asarray(support[key])[mask]):
                        controls_ok = False
                throttle_ok = np.mean(np.asarray(grid['Throttle'])[mask] >= .94) >= .98
                brake_ok = not np.any(np.asarray(grid['Brake'])[mask] > .03)
                steering_ok = not np.any(np.abs(np.asarray(grid['SteeringWheelAngle'])[mask]) > math.radians(5))
                if not (throttle_ok and brake_ok and steering_ok):
                    controls_ok = False
            loss = float(np.interp(b, x, delta) - np.interp(a, x, delta))
            if controls_ok and loss >= .03:
                bucket = 0 if wrap and index == len(outside) - 1 else index
                # Fragments/duplicates cannot inflate repetition or loss time.
                supporting[bucket] = max(supporting.get(bucket, 0.), loss)
    result['evidence'].update(supporting_straights=len(supporting), full_throttle_loss_s=round(sum(supporting.values()), 4))
    if len(supporting) < 2:
        return stop('NO_REPEATED_STRAIGHT_SUPPORT')
    if sum(supporting.values()) < max(.1, outside_delta * .5):
        return stop('STRAIGHT_SUPPORT_TOO_SMALL')
    return {**result, 'status': 'POSSIBLE', 'reason': 'CORNER_GAIN_REPEATED_STRAIGHT_LOSS'}
