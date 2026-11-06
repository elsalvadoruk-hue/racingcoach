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


def advise(points, target, track_temp):
    """Per-wheel advice dicts; a wheel without evidence is withheld."""
    wheels = {}
    for w in WHEELS:
        rows = points.get(w) or []
        if not rows:
            wheels[w] = {'cold_kpa': None, 'reason': 'no_evidence'}
            continue
        ratio, model = fit(rows, track_temp)
        cold = (target + ATM_KPA) / ratio - ATM_KPA
        seen = min(c for _, c, _, _ in rows)
        temps = [t for t, *_ in rows if isinstance(t, (int, float))]
        wheels[w] = {'cold_kpa': round(cold, 1), 'model': model, 'stints': len(rows),
                     'track_temp_range_c': [min(temps), max(temps)] if temps else None,
                     'warning': 'below_lowest_used' if cold < seen - 1 else None}
    return wheels


def recommendation(path, *, game, car, track, targets, fixed_setup, track_temp, kind, installed=None,
                   installed_sources=None, forecast=None):
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
    wheels = advise(points, target, track_temp)
    if all(v['cold_kpa'] is None for v in wheels.values()):
        return {**base, 'status': 'withheld', 'reason': 'no_evidence', 'target_kpa': target, 'target_source': source}
    return {**base, 'status': 'ready', 'target_kpa': round(target, 1), 'target_source': source, 'wheels': wheels}
