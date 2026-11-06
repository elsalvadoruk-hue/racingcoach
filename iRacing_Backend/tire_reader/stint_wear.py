"""Double-stint reference: how much tread the tires now fitted would have after one more stint.

Numbers only. Wear alone does not tell grip, so this never says whether to double stint.

Wear per lap comes from this PC's earlier stints of the same car and track:
- LMU: live wear, so a stint's (highest - lowest) wear over its laps.
- iRacing: wear is measured only at a stop. A stop that did not change a tire measured the
  tire that was driven, from new (installed with evidence) over the laps since it went on.
For LMU the current set's live wear also counts.
"""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import statistics

from .snapshot import WHEELS

MIN_LAPS = 5
NEW_SOURCES = ('garage_start', 'pit_tire_change', 'garage_measured', 'pit_new_tire_measured')


def _stint_rates(stint):
    """{wheel: wear per lap} from one stored stint."""
    conditions = stint.get('conditions') or {}
    rates = {}
    if stint.get('game') == 'lmu':
        laps = conditions.get('laps') or 0
        for w in WHEELS:
            wear = (stint.get('wheels') or {}).get(w, {}).get('wear_fraction') or {}
            if laps >= MIN_LAPS and isinstance(wear.get('max'), (int, float)) and isinstance(wear.get('min'), (int, float)):
                rates[w] = (wear['max'] - wear['min']) / laps
        return rates
    stop = stint.get('pit_snapshot') or {}
    last = conditions.get('last_lap')
    for w in WHEELS:
        fitted = (conditions.get('installed_lap') or {}).get(w)
        source = (conditions.get('installed_cold_source') or {}).get(w)
        wear = [v for v in ((stop.get('wheels') or {}).get(w, {}).get('wear_lmr') or []) if isinstance(v, (int, float))]
        if ((stop.get('tire_changed') or {}).get(w) is False and source in NEW_SOURCES
                and isinstance(fitted, int) and isinstance(last, int) and last - fitted + 1 >= MIN_LAPS and wear):
            rates[w] = (1 - min(wear)) / (last - fitted + 1)
    return rates


def wear_rates(path, game, car, track):
    rates = {w: [] for w in WHEELS}
    try:
        with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=.2)) as db:
            rows = db.execute("SELECT data FROM tire_stints WHERE json_extract(data,'$.game')=? AND "
                              "json_extract(data,'$.car')=? AND json_extract(data,'$.track')=? ORDER BY id DESC LIMIT 200",
                              (game, car, track)).fetchall()
    except (OSError, sqlite3.Error):
        return rates
    for (text,) in rows:
        try:
            for w, rate in _stint_rates(json.loads(text)).items():
                if 0 <= rate < .2:
                    rates[w].append(rate)
        except (ValueError, TypeError, AttributeError):
            continue
    return rates


def projection(path, s, *, fuel_laps):
    """Wear left on each tire now fitted after this stint and one more of the same length."""
    rates = wear_rates(path, s.game, s.car, s.track)
    wheels = {}
    for w in WHEELS:
        fitted, source = s.installed_lap.get(w), s.installed_cold_source.get(w)
        on_set = s.lap - fitted if isinstance(s.lap, int) and isinstance(fitted, int) else None
        live = s.wheels[w].wear_fraction if s.game == 'lmu' else None
        history = list(rates[w])
        if live is not None and source in NEW_SOURCES and on_set and on_set >= MIN_LAPS:
            history.insert(0, (1 - live) / on_set)  # this set, measured live
        if not history or on_set is None:
            wheels[w] = {'reason': 'no_wear_rate' if not history else 'unknown_tire_age'}
            continue
        rate = statistics.median(history)
        if live is not None:
            now, basis = live, 'live'
        elif source in NEW_SOURCES:
            now, basis = 1 - rate * on_set, 'estimated_from_rate'
        else:
            wheels[w] = {'reason': 'unknown_tire_age'}
            continue
        left = fuel_laps if isinstance(fuel_laps, (int, float)) else 0
        stint_laps = on_set + left
        at_stop = now - rate * left
        wheels[w] = {'wear_now': round(now, 3), 'wear_now_basis': basis, 'wear_at_stop': round(at_stop, 3),
                     'wear_after_next': round(at_stop - rate * stint_laps, 3), 'rate_per_lap': round(rate, 5),
                     'stints': len(history), 'stint_laps': round(stint_laps, 1)}
    usable = {w: v for w, v in wheels.items() if 'wear_after_next' in v}
    if not usable:
        return {'status': 'withheld', 'reason': 'no_wear_rate', 'wheels': wheels}
    worst = min(usable, key=lambda w: usable[w]['wear_after_next'])
    return {'status': 'ready', 'wheels': wheels, 'worst': worst, 'grip_confirmed': False}
