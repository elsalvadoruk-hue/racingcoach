"""Steering wheel range per LMU car, measured by the live coach.

LMU recordings store steering as % of lock. The live SDK also reports the
car's wheel range, so each drive measures it once and the recording analysis
turns % into an angle. Until a car has been driven live a nominal range is
used, and the analysis says so.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import threading

NOMINAL_RANGE_DEG = 450.      # half range 225 deg; between typical GT3 and prototype locks
_LOCK = threading.Lock()


def store_path():
    configured = os.getenv('IRACING_LMU_STEERING_PATH', '').strip()
    if configured:
        return Path(configured)
    from app_settings import data_dir
    return Path(data_dir()) / 'lmu-steering.json'


def _load(path):
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _key(car):
    return ' '.join(str(car or '').split()).casefold()


def half_range_rad(car):
    """(half wheel range in radians, 'MEASURED' | 'NOMINAL')."""
    with _LOCK:
        measured = _load(store_path()).get(_key(car))
    if isinstance(measured, (int, float)) and 90 <= measured <= 1440:
        return math.radians(measured / 2), 'MEASURED'
    return math.radians(NOMINAL_RANGE_DEG / 2), 'NOMINAL'


def remember(car, range_deg):
    key = _key(car)
    if not key or not isinstance(range_deg, (int, float)) or not 90 <= range_deg <= 1440:
        return
    range_deg = round(float(range_deg), 1)
    with _LOCK:
        path = store_path()
        data = _load(path)
        if data.get(key) == range_deg:
            return
        data[key] = range_deg
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix('.tmp')
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
            temporary.replace(path)
        except OSError:
            pass
