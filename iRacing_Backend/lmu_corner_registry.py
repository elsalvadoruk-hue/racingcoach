"""LMU analysis corner windows for the shared live corner coach.

The iRacing live coach looks corners up by (car, track, layout); for iRacing
that is the saved ``CornerDefinitionStore``. LMU recordings carry no IDs, so
the analysed windows are registered here by the recording's car and track
names, which are the same names the live SDK reports.
"""
from __future__ import annotations

import math
import threading

from corner_names import spoken_call

_REGISTRY = {}
_LOCK = threading.Lock()


def _key(car, track):
    if not all(isinstance(value, str) and value.strip() for value in (car, track)):
        return None
    return tuple(' '.join(value.split()).casefold() for value in (car, track))


def register(metadata, definitions, names=None):
    """Windows must be ordered, observed and non-overlapping; otherwise ignored."""
    key = _key(metadata.get('car_name'), metadata.get('track_name'))
    if key is None or metadata.get('track_layout') not in (None, '', metadata.get('track_name')):
        return 0  # a layout the live SDK cannot tell apart is never guessed
    rows = []
    for definition in definitions or []:
        positions = [definition.get(field) for field in ('entry_pct', 'apex_pct', 'exit_pct')]
        if not all(isinstance(p, (int, float)) and math.isfinite(p) for p in positions) or not (
                0 <= positions[0] < positions[1] < positions[2] <= 1):
            return 0
        named = (names or {}).get(definition.get('id'), {})
        rows.append({'id': definition['id'], **dict(zip(('entry_pct', 'apex_pct', 'exit_pct'), positions)),
                     'call': spoken_call(named.get('number'), named.get('name'), named.get('spoken'))})
    rows.sort(key=lambda row: row['entry_pct'])
    if not rows or any(a['exit_pct'] > b['entry_pct'] for a, b in zip(rows, rows[1:])):
        return 0
    with _LOCK:
        _REGISTRY[key] = rows
    return len(rows)


def lookup(identity):
    key = _key(identity.get('car_name'), identity.get('track_name'))
    with _LOCK:
        rows = _REGISTRY.get(key)
    return [dict(row) for row in rows] if rows else None
