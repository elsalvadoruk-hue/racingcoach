"""Cold pressure advice from this PC's own evidenced stints. A reference, never a setup change.

Evidence: stints of the same game/car/track whose installed cold pressure is known
(installed.py: 'garage_start' or 'pit_tire_change'), from race or practice, with at
least MIN_LAPS eligible laps (history.py rules: no quali, pit, warm-up or gaps).

Model, per wheel: r = hot_abs / cold_abs (absolute pressures). Hot pressure is the
median of the eligible laps. With enough stints over a wide enough track temperature
range, r is fitted linearly against track temperature; otherwise the nearest stint's
r is moved by DEFAULT_SLOPE per degree (an estimate, reported as such).

Target hot pressure: the driver's saved range for the car, else the range in which the
dash colors said 'ok' (learned from observations). Without a target there is no advice.
Fixed or unconfirmed setups get no advice (pressure changes are not allowed or unknown).

Floor: setups usually already sit at the lowest allowed cold pressure, so the advice never
goes below the driver's entered minimum for the car, else the lowest cold pressure this car
has run with. At the floor the advice is "keep", with the hot pressure that will result.
Only a raise or a lower that is possible counts as a change worth a voice call.
"""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import statistics

from .snapshot import WHEELS

ATM_KPA = 101.325
MIN_LAPS = 3
FIT_MIN_STINTS = 3
FIT_MIN_SPAN_C = 4.0
# Gas in the tire warms roughly half a degree per degree of track temperature, at about
# 360 K: about 0.0014 of the absolute pressure ratio per degree. Used only without a fit.
DEFAULT_SLOPE = .0014
# iRacing: setup/pit service values with confirmation; LMU: cold pressures measured live (installed.py).
EVIDENCE_SOURCES = ('garage_start', 'pit_tire_change', 'garage_measured', 'pit_new_tire_measured')
EVIDENCE_SESSIONS = {'Race', 'Practice', 'Offline Testing'}
LEARN_MIN_OK = 30
CHANGE_KPA = 1.5  # smaller differences (about 0.2 psi) are advised as "keep"
GT3_MIN_COLD_KPA = 159.0  # see default_floor()


def _connect(path):
    return closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=.2))


def stint_points(stint):
    """[(wheel, track_temp_c, cold_kpa, hot_kpa, laps)] from one stored stint or current tire set."""
    conditions = stint.get('conditions') or {}
    laps = stint.get('eligible_laps') or []
    sessions = set(conditions.get('session_types') or [])
    if len(laps) < MIN_LAPS or not sessions or not sessions <= EVIDENCE_SESSIONS:
        return []
    track = conditions.get('track_temp_c')
    points = []
    for w in WHEELS:
        cold = (conditions.get('installed_cold_kpa') or {}).get(w)
        source = (conditions.get('installed_cold_source') or {}).get(w)
        hot = [row.get(f'{w}_kpa') for row in laps if isinstance(row.get(f'{w}_kpa'), (int, float))]
        if cold is None or source not in EVIDENCE_SOURCES or len(hot) < MIN_LAPS:
            continue
        points.append((w, track, cold, statistics.median(hot), len(hot)))
    return points


def evidence(path, game, car, track):
    points = {w: [] for w in WHEELS}
    try:
        with _connect(path) as db:
            rows = db.execute("SELECT data FROM tire_stints WHERE json_extract(data,'$.game')=? AND "
                              "json_extract(data,'$.car')=? AND json_extract(data,'$.track')=? ORDER BY id DESC LIMIT 200",
                              (game, car, track)).fetchall()
    except (OSError, sqlite3.Error):
        return points
    for (text,) in rows:
        try:
            for w, *point in stint_points(json.loads(text)):
                points[w].append(tuple(point))
        except (ValueError, TypeError, AttributeError):
            continue
    return points


def current_set(path, game, car, track, installed, sources):
    """Eligible laps already driven on the tires now fitted (same installed pressures)."""
    if not installed or any(installed.get(w) is None for w in WHEELS):
        return None
    try:
        with _connect(path) as db:
            rows = db.execute("SELECT lap,data FROM tire_laps WHERE eligible=1 AND json_extract(data,'$.game')=? AND "
                              "json_extract(data,'$.car')=? AND json_extract(data,'$.track')=? ORDER BY id DESC LIMIT 60",
                              (game, car, track)).fetchall()
    except (OSError, sqlite3.Error):
        return None
    laps, track_temps, sessions = [], [], set()
    for lap, text in rows:
        data = json.loads(text)
        conditions = data.get('conditions') or {}
        if conditions.get('installed_cold_kpa') != installed:
            break  # older tires: stop at the first lap of another set
        laps.append({'lap': lap, **{f'{w}_kpa': data['wheels'][w].get('pressure_kpa', {}).get('median') for w in WHEELS}})
        track_temps.append(conditions.get('track_temp_c'))
        sessions.update(conditions.get('session_types') or [])
    temps = [t for t in track_temps if isinstance(t, (int, float))]
    return {'eligible_laps': laps, 'conditions': {
        'track_temp_c': statistics.median(temps) if temps else None, 'session_types': sorted(sessions),
        'installed_cold_kpa': installed, 'installed_cold_source': dict(sources or {})}}


def learned_window(path, car, game='iracing'):
    """Pressure range the dash colors called 'ok' for this car: (low_kpa, high_kpa, target_kpa) or None."""
    ok, low, high = [], [], []
    try:
        with _connect(path) as db:
            rows = db.execute("SELECT data FROM tire_observations WHERE game=? AND car=? ORDER BY id DESC LIMIT 20000",
                              (game, car)).fetchall()
    except (OSError, sqlite3.Error):
        return None
    for (text,) in rows:
        try:
            wheels = json.loads(text).get('wheels') or {}
        except ValueError:
            continue
        for value in wheels.values():
            kpa, state = value.get('pressure_kpa'), value.get('pressure_state')
            if isinstance(kpa, (int, float)):
                {'ok': ok, 'low': low, 'high': high}.get(state, []).append(kpa)
    if len(ok) < LEARN_MIN_OK:
        return None
    lower = (max(low) + min(ok)) / 2 if low and max(low) < min(ok) else min(ok)
    upper = (min(high) + max(ok)) / 2 if high and min(high) > max(ok) else max(ok)
    return lower, upper, statistics.median(ok)


def target_kpa(targets, learned):
    low, high = targets.get('pressure_low_bar'), targets.get('pressure_high_bar')
    if low is not None and high is not None:
        return (low + high) * 50, 'driver_range'
    if learned:
        return learned[2], 'dash_color'
    return None, None


def fit(points, track_temp):
    """points: [(track_temp, cold, hot, laps)] -> (ratio, model) at track_temp."""
    usable = [(t, (hot + ATM_KPA) / (cold + ATM_KPA)) for t, cold, hot, _ in points]
    with_temp = [(t, r) for t, r in usable if isinstance(t, (int, float))]
    span = max(t for t, _ in with_temp) - min(t for t, _ in with_temp) if with_temp else 0
    if track_temp is not None and len(with_temp) >= FIT_MIN_STINTS and span >= FIT_MIN_SPAN_C:
        mean_t = statistics.mean(t for t, _ in with_temp); mean_r = statistics.mean(r for _, r in with_temp)
        slope = (sum((t - mean_t) * (r - mean_r) for t, r in with_temp)
                 / sum((t - mean_t) ** 2 for t, _ in with_temp))
        return mean_r + slope * (track_temp - mean_t), 'fitted'
    if track_temp is not None and with_temp:
        t0, r0 = min(with_temp, key=lambda item: abs(item[0] - track_temp))
        return r0 + DEFAULT_SLOPE * (track_temp - t0), 'nearest_with_default_slope'
    return statistics.median(r for _, r in usable), 'median_without_temperature'


def lowest_used(path, game, car):
    """Per wheel, the lowest cold pressure this car has run with here (setup value or measured).

    Drivers usually run the minimum the setup allows, so this is the practical floor
    when the driver has not entered the car's minimum.
    """
    lowest = {w: None for w in WHEELS}
    try:
        with _connect(path) as db:
            rows = db.execute('SELECT data FROM tire_observations WHERE game=? AND car=? ORDER BY id DESC LIMIT 20000',
                              (game, car)).fetchall()
    except (OSError, sqlite3.Error):
        return lowest
    for (text,) in rows:
        try:
            data = json.loads(text)
        except ValueError:
            continue
        for key in ('cold_pressure_kpa', 'installed_cold_kpa'):
            for w, value in (data.get(key) or {}).items():
                if w in lowest and isinstance(value, (int, float)) and 40 <= value <= 400:
                    lowest[w] = value if lowest[w] is None else min(lowest[w], value)
    return lowest


def default_floor(game, car):
    """iRacing GT3 starting pressure minimum, as the app's setup analysis already uses
    (setup_analysis.ADJUSTMENT_RULES['starting_pressure_kpa'], 159 kPa = 23.0 psi, the BMW
    M4 GT3's real setting). Not in iRacing's public notes: the 2021 notes said 148 kPa
    (21.5 psi) before later tire updates. Other cars and LMU have no default."""
    from .vehicle_dash import canonical_car
    car = canonical_car(car) if game == 'iracing' else None
    return GT3_MIN_COLD_KPA if car and car != 'porsche9922cup' else None


def floors(targets, lowest, default=None):
    """{wheel: (floor_kpa, source)}: the driver's minimum for the car, else the lower of the
    class default and the lowest this car has actually run (lower use proves it is allowed)."""
    minimum = targets.get('pressure_min_cold_bar')
    if minimum is not None:
        return {w: (minimum * 100, 'driver_minimum') for w in WHEELS}
    result = {}
    for w in WHEELS:
        used = lowest.get(w)
        if default is not None and (used is None or default <= used):
            result[w] = (default, 'gt3_default')
        else:
            result[w] = (used, 'lowest_used') if used is not None else (None, None)
    return result


def advise(points, target, track_temp, floor=None, current=None):
    """Per-wheel advice dicts; a wheel without evidence is withheld.

    The cold pressure never goes below the floor (setups usually sit at the minimum already):
    then the advice is to stay there, with the hot pressure that will result.
    """
    floor, current = floor or {}, current or {}
    wheels = {}
    for w in WHEELS:
        rows = points.get(w) or []
        if not rows:
            wheels[w] = {'cold_kpa': None, 'reason': 'no_evidence'}
            continue
        ratio, model = fit(rows, track_temp)
        raw = (target + ATM_KPA) / ratio - ATM_KPA
        low, low_source = floor.get(w, (None, None))
        at_minimum = low is not None and raw < low - .5
        cold = low if at_minimum else raw
        hot = (cold + ATM_KPA) * ratio - ATM_KPA
        now = current.get(w)
        change = round(cold - now, 1) if isinstance(now, (int, float)) else None
        action = (None if change is None else 'raise' if change >= CHANGE_KPA
                  else 'lower' if change <= -CHANGE_KPA else 'keep')
        temps = [t for t, *_ in rows if isinstance(t, (int, float))]
        wheels[w] = {'cold_kpa': round(cold, 1), 'raw_kpa': round(raw, 1), 'at_minimum': at_minimum,
                     'floor_kpa': round(low, 1) if low is not None else None, 'floor_source': low_source,
                     'expected_hot_kpa': round(hot, 1), 'hot_over_target_kpa': round(hot - target, 1),
                     'current_kpa': now, 'change_kpa': change, 'action': action,
                     'model': model, 'stints': len(rows),
                     'track_temp_range_c': [min(temps), max(temps)] if temps else None}
    return wheels


def recommendation(path, *, game, car, track, targets, fixed_setup, track_temp, kind, installed=None,
                   installed_sources=None, forecast=None, current=None):
    """current: the cold pressures the advice is compared with (setup in the garage, tires now fitted)."""
    """kind: 'garage' (start of the next run) or 'next_pit' (tires fitted at the next stop)."""
    base = {'kind': kind, 'track_temp_c': track_temp, 'forecast': forecast}
    if fixed_setup is not False:
        return {**base, 'status': 'withheld', 'reason': 'fixed_or_unknown_setup'}
    learned = learned_window(path, car, game)
    target, source = target_kpa(targets, learned)
    if target is None:
        return {**base, 'status': 'withheld', 'reason': 'no_target_range', 'learned_window_kpa': learned}
    points = evidence(path, game, car, track)
    if kind == 'next_pit':
        current = current_set(path, game, car, track, installed, installed_sources)
        for w, *point in stint_points(current) if current else []:
            # The tires on the car now are the closest evidence for this session.
            points[w].insert(0, tuple(point))
    wheels = advise(points, target, track_temp, floors(targets, lowest_used(path, game, car), default_floor(game, car)), current)
    if all(v['cold_kpa'] is None for v in wheels.values()):
        return {**base, 'status': 'withheld', 'reason': 'no_evidence', 'target_kpa': target, 'target_source': source}
    actions = {v.get('action') for v in wheels.values() if v.get('cold_kpa') is not None}
    return {**base, 'status': 'ready', 'target_kpa': round(target, 1), 'target_source': source, 'wheels': wheels,
            # 'change' only when a wheel should move; staying at the floor is not a change.
            'action': 'change' if actions & {'raise', 'lower'} else 'keep' if actions <= {'keep'} else None,
            'at_minimum': [w for w, v in wheels.items() if v.get('at_minimum')]}
