"""Bounded scoring, car/track labels and driver-control fields from the LMU SDK."""
import math


def real(value, minimum=None, maximum=None):
    if type(value) not in (int, float):
        return None
    try:
        if not math.isfinite(value):
            return None
    except OverflowError:
        return None
    if minimum is not None and value < minimum or maximum is not None and value > maximum:
        return None
    return value


def integer(value, minimum=0, maximum=65535):
    return value if type(value) is int and minimum <= value <= maximum else None


def boolean(value):
    return value if type(value) is bool else None


def yellow_signal(snapshot):
    """Current yellow evidence, independent of the green/blue primary flag.

    The flag-array order is not guaranteed by the installed SDK. Announce a
    generic yellow when any sector reports Pending=1, without claiming which
    sector the player is in. Partial/invalid arrays cannot prove a clearance.
    """
    if snapshot.get('under_yellow') is True:
        return True
    flags = snapshot.get('sector_flags')
    if isinstance(flags, list) and len(flags) == 3:
        if any(type(value) is int and value == 1 for value in flags):
            return True
        if not all(type(value) is int and value == 0 for value in flags):
            return None
    return False if snapshot.get('under_yellow') is False else None


def last_lap_signal(snapshot):
    """Exact lap-limited race final lap; timed-race estimates are not flags.

    SDK yellow_state=5 means the final *caution* lap. Primary mFlag only
    supports green/blue. Neither field can supply a race white-flag edge.
    """
    session_type = integer(snapshot.get('session_type_code'), 10, 13)
    maximum = integer(snapshot.get('session_max_laps'), 1, 2147483646)
    completed = integer(snapshot.get('total_laps'), 0, 32767)
    if session_type is None or maximum is None or completed is None:
        return None
    return (snapshot.get('session_phase') == 5 and snapshot.get('finish_status') == 0
            and completed == maximum - 1)


def scoring_cars(raw):
    if not isinstance(raw, list) or len(raw) > 104:
        return None
    result, seen = [], set()
    for row in raw:
        if not isinstance(row, dict):
            return None
        identity = integer(row.get('vehicle_id'))
        if identity is None or identity in seen:
            return None
        seen.add(identity)
        clean = {'vehicle_id': identity, 'class_id': integer(row.get('class_id'), 1, 4294967295),
                 'overall_position': integer(row.get('overall_position'), 1, 255),
                 'class_position': integer(row.get('class_position'), 1, 104),
                 'total_laps': integer(row.get('total_laps'), 0, 32767),
                 'finish_status': integer(row.get('finish_status'), 0, 3),
                 'control_code': integer(row.get('control_code'), -1, 3),
                 'in_pits': boolean(row.get('in_pits')), 'sector': integer(row.get('sector'), 0, 2),
                 'pit_stops': integer(row.get('pit_stops'), 0, 32767)}
        for name in ('lap_distance_m', 'path_lateral_m', 'track_edge_m'):
            clean[name] = real(row.get(name), -1000000, 1000000)
        for name in ('last_lap_time', 'best_lap_time'):
            value = real(row.get(name), 0, 1000000)
            clean[name] = value if value and value > 0 else None
        for name in ('time_behind_next_s', 'time_behind_leader_s', 'lap_start_et'):
            clean[name] = real(row.get(name), 0, 1e9)
        clean['speed_mps'] = real(row.get('speed_mps'), 0, 500)
        for name in ('laps_behind_next', 'laps_behind_leader'):
            clean[name] = integer(row.get(name), 0, 32767)
        result.append(clean)
    return result


def normalise(raw):
    result = {'scoring_cars': scoring_cars(raw.get('scoring_cars'))}
    # Preserve SDK source order. The SDK does not guarantee the association of
    # the sector-flag array with the player's 0=third/1=first/2=second code.
    flags = raw.get('sector_flags')
    result['sector_flags'] = ([integer(value, -1, 7) for value in flags]
                              if isinstance(flags, list) and len(flags) == 3 else None)
    result['sector'] = integer(raw.get('sector'), 0, 2)
    for name, limit in (('car_name', 64), ('track_name', 64), ('vehicle_model', 30)):
        value = raw.get(name)
        result[name] = (value.strip() if isinstance(value, str) and len(value) <= limit
                        and value.strip() and not any(ord(c) < 32 for c in value) else None)
    for name in ('overheating', 'parts_detached', 'front_tyre_wet', 'rear_tyre_wet'):
        result[name] = boolean(raw.get(name))
    for name, low, high in (('last_impact_et', 0, 1e9), ('last_impact_magnitude', 0, 1e12),
                            ('yaw_rate', -1000, 1000), ('steering_rad', -20, 20),
                            ('brake_bias', 0, 100), ('pit_box_lap_distance_m', 0, 1000000),
                            ('time_gap_ahead_s', 0, 1e6), ('time_gap_behind_s', 0, 1e6),
                            ('place_gap_ahead_s', 0, 1e6), ('place_gap_behind_s', 0, 1e6)):
        result[name] = real(raw.get(name), low, high)
    for name in ('tc_setting', 'abs_setting', 'front_tyre_compound', 'rear_tyre_compound',
                 'start_light', 'start_red_lights', 'track_limits_steps_per_penalty'):
        result[name] = integer(raw.get(name), 0, 255)
    result['class_id'] = integer(raw.get('class_id'), 1, 4294967295)
    return result
