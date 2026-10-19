"""Compare an observed clean corner with the same interval of the graph reference."""
import math

import numpy as np

from live_corners import BRAKE_ON


def number(value):
    return float(value) if type(value) in (int, float) and math.isfinite(value) else None


def corner_feedback(reference, view):
    measured = view.get('corner_pass') or {}
    sample = view.get('snapshot') or {}
    at, ended = number(sample.get('session_time')), number(measured.get('exit_at'))
    lap, current_lap = number(measured.get('lap')), number(sample.get('lap_number'))
    if (measured.get('clean') is not True or at is None or ended is None or not 0 <= at-ended <= 30
            or lap is None or current_lap not in (lap, lap+1)):
        return None
    entry, apex, exit_ = (number(measured.get(key)) for key in ('entry_pct', 'apex_pct', 'exit_pct'))
    duration, length = number(measured.get('duration_s')), number(view.get('track_length_m'))
    if None in (entry, apex, exit_, duration, length) or not 0 <= entry < apex < exit_ <= 1 or duration <= 0 or length <= 0:
        return None
    before, after = reference.at(entry), reference.at(exit_)
    inside = (reference.distance >= entry) & (reference.distance <= exit_)
    if before is None or after is None or after <= before or not np.isfinite(reference.times[inside]).all():
        return None
    result = {'corner_id': measured.get('id'), 'delta_s': round(duration-(after-before), 3),
              'brake_delta_m': None}
    # Use the same pedal threshold and the same pre-entry reach as the live coach.
    begin = max(0, entry-150/length)
    indices = np.flatnonzero((reference.distance >= begin) & (reference.distance <= apex))
    brake = reference.grid.get('Brake')
    onset = None
    if brake is not None:
        for index in indices:
            if index == 0:
                continue
            old, new = brake[index-1:index+1]
            if np.isfinite([old, new]).all() and old <= BRAKE_ON < new:
                onset = float(reference.distance[index])
                break
    current = number(measured.get('brake_pct'))
    if measured.get('brake_observed') is True and onset is not None and current is not None and begin <= current <= apex:
        result['brake_delta_m'] = round((current-onset)*length, 1)
    return result
