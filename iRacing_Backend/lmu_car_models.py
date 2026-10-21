"""One car per LMU model, whatever the livery (team, year and number).

LMU names an entry, not a model: ``Team WRT 2026 #32:LM`` and ``Team WRT 2026
#69:LM`` are the same BMW M4 LMGT3. Keyed by entry, a new livery started over:
"first time on this combination", no race history, no corner splits, no
practice task. The live SDK sends the model (``mVehicleModel``); recordings do
not, but every entry of a model shares the same garage ranges
(``lmu_telemetry.car_model_key``). So the model is learned live per entry, and
a recording's garage fingerprint carries it to the other liveries of that car.

Until a model is known for an entry, the entry keeps its old livery key.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import threading

_LOCK = threading.Lock()
_CACHE = {'path': None, 'mtime': None, 'data': None}


def store_path():
    configured = os.getenv('IRACING_LMU_CAR_MODELS_PATH', '').strip()
    if configured:
        return Path(configured)
    from app_settings import data_dir
    return Path(data_dir()) / 'lmu-car-models.json'


def _slug(text):
    return re.sub(r'[^a-z0-9]+', '-', str(text or '').lower()).strip('-')


def _entry(car):
    return ' '.join(str(car or '').split()).casefold()


def _clean(model):
    model = ' '.join(str(model or '').split())
    return model if model and _slug(model) else None


def _load():
    path = store_path()
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        mtime = None
    if _CACHE['path'] == path and _CACHE['mtime'] == mtime and _CACHE['data'] is not None:
        return _CACHE['data']
    data = {'version': 1, 'entries': {}, 'fingerprints': {}}
    if mtime is not None:
        try:
            loaded = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(loaded, dict):
                data['entries'] = {k: v for k, v in (loaded.get('entries') or {}).items() if isinstance(v, str)}
                data['fingerprints'] = {k: v for k, v in (loaded.get('fingerprints') or {}).items() if isinstance(v, str)}
        except (OSError, ValueError):
            pass
    _CACHE.update(path=path, mtime=mtime, data=data)
    return data


def _save(data):
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True), encoding='utf-8')
    os.replace(temporary, path)
    _CACHE.update(path=path, mtime=path.stat().st_mtime_ns, data=data)


def model_for(car, fingerprint=None):
    """The model name of this entry, or None while it is unknown."""
    with _LOCK:
        data = _load()
        return data['entries'].get(_entry(car)) or (data['fingerprints'].get(fingerprint) if fingerprint else None)


def learn(car, model=None, fingerprint=None):
    """Live: the entry's model. Recording: the entry's garage fingerprint (joined to a known model)."""
    entry, model = _entry(car), _clean(model)
    if not entry:
        return
    with _LOCK:
        data = _load()
        changed = False
        if model and data['entries'].get(entry) != model:
            data['entries'][entry] = model
            changed = True
        known = model or data['entries'].get(entry)
        if fingerprint and known and data['fingerprints'].get(fingerprint) != known:
            data['fingerprints'][fingerprint] = known
            changed = True
        if fingerprint and not known and data['fingerprints'].get(fingerprint):
            # Another livery of a model seen before: this entry is that model too.
            data['entries'][entry] = data['fingerprints'][fingerprint]
            changed = True
        if changed:
            try:
                _save(data)
            except OSError:
                pass


def car_key(car, model=None, fingerprint=None):
    """(id, display name) used as iRacing's CarID/CarPath and CarScreenName."""
    model = _clean(model) or model_for(car, fingerprint)
    if model:
        return f'lmu-car-{_slug(model)}', model
    slug = _slug(car)
    return (f'lmu-car-{slug}' if slug else ''), car


def prepare(data_dir, recordings):
    """At start-up, before any store loads: learn models from the recordings' garages, then move old data."""
    from lmu_telemetry import LmuTelemetryReader
    seen = []
    for path in recordings:
        try:
            metadata = LmuTelemetryReader(path).metadata()
        except Exception:
            continue          # locked, still recording, damaged: learned another time
        if metadata.get('car_name') and metadata.get('car_model_key'):
            seen.append((metadata['car_name'], metadata['car_model_key']))
    for _ in range(2):        # a fingerprint learned from one livery names the others
        for car, fingerprint in seen:
            learn(car, fingerprint=fingerprint)
    return migrate_saved_keys(data_dir)


def legacy_key(car):
    slug = _slug(car)
    return f'lmu-car-{slug}' if slug else ''


def migrate_saved_keys(data_dir):
    """Move per-livery saved data (corner splits, race history) to the model's key, once its model is known.

    Existing model data is kept; a livery entry only fills what the model does not have yet.
    """
    data_dir = Path(data_dir)
    with _LOCK:
        entries = dict(_load()['entries'])
    renames = {}
    for entry, model in entries.items():
        old = legacy_key(entry)
        new = f'lmu-car-{_slug(model)}'
        if old and old != new:
            renames[old] = (new, model)
    if not renames:
        return 0
    moved = 0
    corners = data_dir / 'corner-definitions.json'
    try:
        data = json.loads(corners.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        data = None
    if isinstance(data, dict) and isinstance(data.get('items'), dict):
        items, changed = {}, False
        for key, value in data['items'].items():
            try:
                parts = json.loads(key)
            except ValueError:
                parts = None
            if isinstance(parts, list) and len(parts) >= 3 and parts[0] in renames:
                new, model = renames[parts[0]]
                parts[0:3] = [new, new, model]
                key, changed = json.dumps(parts, ensure_ascii=False), True
                moved += 1
                if key in items or key in data['items']:
                    continue                      # the model already has these splits
            items[key] = value
        if changed:
            data['items'] = items
            _write(corners, data)
    history = data_dir / 'race-history.json'
    try:
        data = json.loads(history.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        data = None
    if isinstance(data, dict) and isinstance(data.get('races'), dict):
        races, sources, changed = {}, {}, False
        for key, value in data['races'].items():
            car, _, rest = key.partition('|')
            if car in renames and isinstance(value, list):
                key, changed = f'{renames[car][0]}|{rest}', True
                moved += 1
            races.setdefault(key, []).extend(value if isinstance(value, list) else [])
            sources[key] = sources.get(key, 0) + 1
        for key, count in sources.items():
            if count > 1:   # several liveries raced here: one history, oldest first
                races[key] = sorted(races[key], key=lambda race: race.get('at') or 0)[-20:]
        if changed:
            data['races'] = races
            _write(history, data)
    return moved


def _write(path, data):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    os.replace(temporary, path)
