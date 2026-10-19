"""Reserve pedal review and braking preview before allowing a transient card."""
import math

import numpy as np


POST_CORNER_S = 5.
PRE_CORNER_S = 5.
CARD_S = 2.5


def number(value):
    return float(value) if type(value) in (int, float) and math.isfinite(value) else None


def quiet_input(sample):
    brake, throttle, steer = (number(sample.get(key)) for key in ('brake', 'throttle', 'steering_rad'))
    return (None not in (brake, throttle, steer) and 0 <= brake <= .05
            and .8 <= throttle <= 1 and abs(steer) <= .12)


def reviewed_exit(view, required_s=POST_CORNER_S):
    """Require continuous measured straight inputs for the chosen review period."""
    sample = view.get('snapshot') or {}
    at = number(sample.get('session_time'))
    if at is None or not quiet_input(sample):
        return False
    ended = number((view.get('corner_pass') or {}).get('exit_at'))
    if ended is not None and not required_s <= at-ended:
        return False
    previous = at
    position = number(sample.get('distance_pct'))
    for row in reversed(view.get('points') or []):
        stamp = number(row.get('session_time'))
        older_position = number(row.get('distance_pct'))
        if (stamp is None or not 0 <= previous-stamp <= .5 or row.get('on_pit_road') is not False
                or not quiet_input(row)):
            return False
        if position is None or older_position is None or not 0 <= (position-older_position) % 1 < .05:
            return False
        position = older_position
        previous = stamp
        if at-stamp >= required_s-1e-6:
            return True
    return False


def reference_clearance(reference, view):
    """Conservative seconds to the next reference input/known corner/coverage gap.

    Use both reference travel time and distance at 115% of current speed. A faster
    reference or accelerating car must not extend a card into a braking preview.
    Missing reference coverage suppresses cards; the pedal graph still works.
    """
    sample = view.get('snapshot') or {}
    at, pct, speed = (number(sample.get(key)) for key in ('session_time', 'distance_pct', 'speed_mps'))
    anchor = reference.at(pct)
    length = number(view.get('track_length_m'))
    result = {'at': at, 'lap': sample.get('lap_number'), 'clear_s': 0.}
    if anchor is None or length is None or length <= 0 or speed is None or speed <= 10:
        return result
    zones = view.get('corner_zones') or []
    angle = reference.grid.get('SteeringWheelAngle')
    brake, throttle = reference.grid.get('Brake'), reference.grid.get('Throttle')
    if brake is None or throttle is None or (angle is None and not zones):
        return result
    start = int(np.searchsorted(reference.distance, pct, side='right'))-1
    old_seconds, old_time = 0., anchor
    count = len(reference.distance)
    for offset in range(count+1):
        index = (start+offset) % count
        shift = (start+offset)//count
        position = float(reference.distance[index])
        stamp = float(reference.times[index])+shift*reference.duration
        distance = max(0., position+shift-pct)*length
        seconds = max(0., min(stamp-anchor, distance/(speed*1.15)))
        values = [stamp, float(brake[index]), float(throttle[index])]
        known_angle = angle is not None and np.isfinite(angle[index])
        if known_angle:
            values.append(float(angle[index]))
        unknown = not np.isfinite(values).all() or (not known_angle and not zones)
        if offset and (stamp-old_time > .5 or stamp < old_time-1e-6):
            unknown = True
        # A CSV that does not cover both sides of the finish line has a gap.
        if shift and (reference.distance[-1] < .9999 or reference.distance[0] > .0001):
            unknown = True
        busy = (values[1] > .05 or values[1] < 0 or not .8 <= values[2] <= 1
                or (known_angle and abs(values[3]) > .12)
                or any(zone['entry_pct'] <= position <= zone['exit_pct'] for zone in zones))
        if unknown or busy:
            result['clear_s'] = old_seconds
            return result
        if seconds >= 12:
            result['clear_s'] = 12.
            return result
        old_seconds, old_time = seconds, stamp
    return result
