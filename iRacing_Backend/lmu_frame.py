"""Bounded, numeric-only normalisation of one LMU bridge frame.

Shared by the live adapter (``lmu_irsdk``) and the diagnostic journal so a
recorded drive replays exactly as it was read.
"""
from __future__ import annotations

import hashlib
import math

from lmu_race_signals import normalise as normalise_race_signals

FRESH_SECONDS = .25
# Exact vehicle extents are unavailable; a conservative common envelope.
GEOMETRY = {'model': 'APPROXIMATE_COMMON_ENVELOPE', 'car_length_m': 5.3,
            'car_width_m': 2.2, 'lateral_warning_distance_m': 6.,
            'exit_margin_m': 1., 'maximum_height_difference_m': 2.}


def finite(value, minimum=-1e7, maximum=1e7):
    if type(value) not in (float, int):
        return False
    try:
        return math.isfinite(value) and minimum <= value <= maximum
    except OverflowError:
        return False


def _vector(value):
    return list(value) if isinstance(value, list) and len(value) == 3 and all(finite(v) for v in value) else None


def _id(value):
    return type(value) is int and 0 <= value <= 65535


def _orientation(value):
    if not isinstance(value, list) or len(value) != 3:
        return None
    rows = [_vector(v) for v in value]
    if any(row is None for row in rows):
        return None
    for i in range(3):
        for j in range(3):
            if abs(sum(rows[i][k] * rows[j][k] for k in range(3)) - (1 if i == j else 0)) > .05:
                return None
    a, b, c = rows
    determinant = (a[0] * (b[1]*c[2] - b[2]*c[1]) - a[1] * (b[0]*c[2] - b[2]*c[0])
                   + a[2] * (b[0]*c[1] - b[1]*c[0]))
    return rows if .95 <= determinant <= 1.05 else None


def normalise_traffic(raw, telemetry_et, vehicle_id):
    """Bounded numeric-only journal contract. Any uncertainty withdraws coverage."""
    unavailable = {'status': 'UNAVAILABLE', 'source': 'TELEMETRY', 'complete': False,
                   'player': None, 'vehicles': []}
    if (not isinstance(raw, dict) or raw.get('status') != 'AVAILABLE'
            or raw.get('source') != 'TELEMETRY' or raw.get('complete') is not True
            or not finite(telemetry_et, 0, 1e9) or not _id(vehicle_id)):
        return unavailable
    player, vehicles = raw.get('player'), raw.get('vehicles')
    if not isinstance(player, dict) or not isinstance(vehicles, list) or len(vehicles) > 103:
        return unavailable
    position, orientation = _vector(player.get('position_m')), _orientation(player.get('orientation'))
    if (player.get('vehicle_id') != vehicle_id or type(player.get('vehicle_id')) is not int
            or not finite(player.get('telemetry_et'), 0, 1e9)
            or abs(player['telemetry_et'] - telemetry_et) > 1e-6
            or position is None or orientation is None):
        return unavailable
    clean = []
    ids = {vehicle_id}
    for row in vehicles:
        if not isinstance(row, dict):
            return unavailable
        identity, et = row.get('vehicle_id'), row.get('telemetry_et')
        pos = _vector(row.get('position_m'))
        if (not _id(identity) or identity in ids or not finite(et, 0, 1e9)
                or abs(telemetry_et - et) > FRESH_SECONDS
                or pos is None or row.get('in_pits') is not False
                or type(row.get('control_code')) is not int or row['control_code'] not in (1, 2)):
            return unavailable
        ids.add(identity)
        clean.append({'vehicle_id': identity, 'telemetry_et': et, 'position_m': pos,
                      'in_pits': False, 'control_code': row['control_code']})
    return {'status': 'AVAILABLE', 'source': 'TELEMETRY', 'complete': True,
            'player': {'vehicle_id': vehicle_id, 'telemetry_et': player['telemetry_et'],
                       'position_m': position, 'orientation': orientation}, 'vehicles': clean}


_NUMBERS = {'game_version', 'vehicle_id', 'telemetry_et', 'scoring_et', 'lap_start_et',
            'scoring_lap_start_et', 'total_laps', 'last_lap_time', 'count_lap_flag',
            'track_limits_steps', 'speed_mps', 'throttle_ratio', 'brake_ratio', 'steering_ratio'}
_BOOLEANS = {'telemetry_updated', 'scoring_updated', 'lap_invalidated', 'in_pits', 'realtime', 'paused',
             'abs_active', 'tc_active', 'speed_limiter_active', 'under_yellow', 'in_garage_stall'}
_EVENTS = {'SME_ENTER', 'SME_EXIT', 'SME_STARTUP', 'SME_SHUTDOWN', 'SME_LOAD', 'SME_UNLOAD',
           'SME_START_SESSION', 'SME_END_SESSION', 'SME_ENTER_REALTIME', 'SME_EXIT_REALTIME',
           'SME_UPDATE_SCORING', 'SME_UPDATE_TELEMETRY', 'SME_INIT_APPLICATION',
           'SME_UNINIT_APPLICATION', 'SME_SET_ENVIRONMENT', 'SME_FFB'}
def _finite_number(value, *, minimum=None, maximum=None, exclusive=False):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        if not math.isfinite(value):
            return None
    except OverflowError:
        return None
    if minimum is not None and (value <= minimum if exclusive else value < minimum):
        return None
    if maximum is not None and value > maximum:
        return None
    return value


def _normalise_wheels(raw):
    # Accept one bounded native schema; arbitrary names, extra fields, duplicate
    # positions and reordered arrays cannot change the SDK's fixed wheel order.
    rows = raw if isinstance(raw, list) and len(raw) == 4 else [None] * 4
    result = []
    for index, position in enumerate(('FL', 'FR', 'RL', 'RR')):
        row = rows[index]
        row = row if isinstance(row, dict) and row.get('position') == position else {}
        temperatures = row.get('temperature_c')
        temperatures = temperatures if isinstance(temperatures, list) and len(temperatures) == 3 else [None] * 3
        result.append({'position': position,
                       'pressure_kpa': _finite_number(row.get('pressure_kpa'), minimum=0),
                       'temperature_c': [_finite_number(value, minimum=-273.15, exclusive=True) for value in temperatures],
                       'brake_temp_c': _finite_number(row.get('brake_temp_c'), minimum=-273.15, exclusive=True),
                       'suspension_deflection_m': _finite_number(row.get('suspension_deflection_m')),
                       'ride_height_m': _finite_number(row.get('ride_height_m')),
                       'wear_fraction': _finite_number(row.get('wear_fraction'), minimum=0, maximum=1),
                       'flat': row.get('flat') if isinstance(row.get('flat'), bool) else None,
                       'detached': row.get('detached') if isinstance(row.get('detached'), bool) else None})
    return result


def normalise(raw):
    """Copy only numeric diagnostics and opaque, non-identifying session keys."""
    if not isinstance(raw, dict):
        return None
    result = {}
    for key in _NUMBERS:
        value = raw.get(key)
        result[key] = _finite_number(value)
    gear = raw.get('gear')
    result['gear'] = gear if isinstance(gear, int) and not isinstance(gear, bool) and -1 <= gear <= 2147483647 else None
    result['engine_rpm'] = _finite_number(raw.get('engine_rpm'), minimum=0)
    for key in ('engine_water_temp_c', 'engine_oil_temp_c'):
        result[key] = _finite_number(raw.get(key), minimum=-273.15, exclusive=True)
    result['clutch_ratio'] = _finite_number(raw.get('clutch_ratio'), minimum=0, maximum=1)
    for key in ('fuel_l', 'session_remaining_s'):
        result[key] = _finite_number(raw.get(key), minimum=0)
    for key in ('fuel_capacity_l', 'track_length_m'):
        result[key] = _finite_number(raw.get(key), minimum=0, exclusive=True)
    result['lap_distance_m'] = _finite_number(raw.get('lap_distance_m'))
    result['best_lap_time'] = _finite_number(raw.get('best_lap_time'), minimum=0, exclusive=True)
    result['session_end_et'] = _finite_number(raw.get('session_end_et'), minimum=0)
    for key, minimum, maximum in (
        ('session_type_code', 0, 13), ('session_phase', 0, 9), ('session_max_laps', 1, 2147483646),
        ('overall_position', 1, 255), ('class_position', 1, 104), ('class_vehicle_count', 1, 104),
        ('vehicle_count', 0, 104), ('control_code', -1, 3), ('primary_flag', 0, 6),
        ('yellow_flag_state', -1, 7), ('pit_state', 0, 4), ('finish_status', 0, 3),
        ('outstanding_penalties', 0, 32767),
    ):
        value = raw.get(key)
        result[key] = value if isinstance(value, int) and not isinstance(value, bool) and minimum <= value <= maximum else None
    for key in ('rain_ratio', 'track_wetness_ratio'):
        result[key] = _finite_number(raw.get(key), minimum=0, maximum=1)
    for key in ('ambient_temp_c', 'track_temp_c'):
        result[key] = _finite_number(raw.get(key), minimum=-273.15, exclusive=True)
    result['wheels'] = _normalise_wheels(raw.get('wheels'))
    for key in _BOOLEANS:
        value = raw.get(key)
        result[key] = value if isinstance(value, bool) else None
    session = raw.get('session_id')
    result['session_key'] = hashlib.sha256(session.encode('utf-8')).hexdigest() if isinstance(session, str) and session else None
    events = raw.get('events')
    result['events'] = sorted({e for e in events if isinstance(e, str) and e in _EVENTS}) if isinstance(events, list) else []
    result['traffic'] = normalise_traffic(raw.get('traffic'), result.get('telemetry_et'), result.get('vehicle_id'))
    result.update(normalise_race_signals(raw))
    return result


