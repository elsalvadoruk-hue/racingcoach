"""Compact automatic personal lap library. Raw telemetry stays in its original file.

This independent table shares the analysis database without changing explicitly
assigned session types or promoting observations to setup evidence.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import sqlite3
from contextlib import contextmanager

import app_settings


def identity(metadata):
    def token(value):
        return ' '.join(str(value or '').casefold().split())
    # LMU names the entry (team, year, number); its model key matches every livery of one car.
    car = (token(metadata.get('car_model_key')) or token(metadata.get('car_platform_id'))
           or token(metadata.get('car_path')) or token(metadata.get('car_name')))
    track_id = token(metadata.get('track_platform_id'))
    track = track_id or token(metadata.get('track_path')) or token(metadata.get('track_name'))
    # iRacing track IDs identify the layout. Names must retain the layout.
    layout = '' if track_id else token(metadata.get('track_config'))
    if car in ('unknown', 'unknown car', 'n/a'):
        car = ''
    if track in ('unknown', 'unknown track', 'n/a'):
        track = ''
    return {'car': car, 'track': track, 'layout': layout}


def _number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def _wet(conditions):
    pct = _number(conditions.get('track_wetness_pct'))
    code = _number(conditions.get('track_wetness_code'))
    rain = _number(conditions.get('precipitation_pct'))
    minimum_wetness = _number(conditions.get('minimum_path_wetness_pct'))
    declared = conditions.get('weather_declared_wet')
    # Positive surface/rain observations override a dry declaration.
    if (pct is not None and pct > 1) or (minimum_wetness is not None and minimum_wetness > 1) or (code is not None and code >= 3) or (rain is not None and rain > 1) or declared is True:
        return 'WET'
    if pct is not None or (code is not None and code in (1, 2)) or declared is False:
        return 'DRY'
    return 'UNKNOWN'


def compatibility(current, reference, same_file=False):
    """Conservative pace comparison, with missing conditions explicitly reported."""
    wet_a, wet_b = _wet(current), _wet(reference)
    reasons, unknown = [], []
    if wet_a != 'UNKNOWN' and wet_b != 'UNKNOWN' and wet_a != wet_b:
        reasons.append('DRY_WET_MISMATCH')
    if 'UNKNOWN' in (wet_a, wet_b):
        unknown.append('SURFACE_CONDITION_UNKNOWN')
        if not same_file:
            reasons.append('SURFACE_CONDITION_UNKNOWN')
    for name, limit in (('track_temp_c', 12), ('air_temp_c', 10), ('precipitation_pct', 15),
                        ('track_wetness_pct', 20), ('minimum_path_wetness_pct', 20), ('track_wetness_code', 1), ('wind_speed_kph', 25)):
        a, b = _number(current.get(name)), _number(reference.get(name))
        if a is not None and b is not None and abs(a - b) > limit:
            reasons.append(name.upper() + '_DIFFERENCE')
    for name in ('tire_compound',):
        a, b = current.get(name), reference.get(name)
        if a is not None and b is not None and str(a) != str(b):
            reasons.append(name.upper() + '_MISMATCH')
    return {'compatible': not reasons, 'reasons': reasons, 'warnings': unknown,
            'surface': {'current': wet_a, 'reference': wet_b},
            'status': 'INCOMPATIBLE' if reasons else 'PARTIAL' if unknown else 'SIMILAR'}


def default_path():
    return Path(os.getenv('IRACING_PERSONAL_REFERENCE_DB_PATH') or os.getenv('IRACING_TUNER_DB_PATH')
                or os.getenv('IRACING_MEMORY_DB_PATH') or app_settings.data_dir() / 'iracing_tuner.sqlite3')


class PersonalReferenceIndex:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else default_path()

    @contextmanager
    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute('''CREATE TABLE IF NOT EXISTS personal_reference_laps (
            game TEXT NOT NULL, path TEXT NOT NULL, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
            car TEXT NOT NULL, track TEXT NOT NULL, layout TEXT NOT NULL,
            lap INTEGER NOT NULL, lap_time_s REAL NOT NULL, conditions_json TEXT NOT NULL,
            PRIMARY KEY (game,path,lap))''')
        connection.execute('CREATE INDEX IF NOT EXISTS personal_reference_identity ON personal_reference_laps(game,car,track,layout,lap_time_s)')
        # One row per indexed recording, also when it has no eligible lap, so a
        # recording is read once per version instead of on every selection.
        connection.execute('''CREATE TABLE IF NOT EXISTS personal_reference_files (
            game TEXT NOT NULL, path TEXT NOT NULL, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
            version TEXT NOT NULL, PRIMARY KEY (game,path))''')
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def source_current(row):
        try:
            stat = Path(row['path']).stat()
            return stat.st_size == row['size'] and stat.st_mtime_ns == row['mtime_ns']
        except OSError:
            return False

    def has_recording(self, game, path, version=''):
        with self._connect() as db:
            row = db.execute('SELECT * FROM personal_reference_files WHERE game=? AND path=?',
                             (game, str(Path(path).resolve()))).fetchone()
        return row is not None and row['version'] == version and self.source_current(row)

    def index_recording(self, game, path, identity, laps, expected_signature=None, version=''):
        path = Path(path).resolve()
        stat = path.stat()
        if expected_signature is not None and (stat.st_size, stat.st_mtime_ns) != tuple(expected_signature[-2:]):
            raise ValueError('Recording changed while indexing personal laps')
        rows = []
        if identity.get('car') and identity.get('track'):
            for lap in laps:
                seconds = _number(lap.get('lap_time_s'))
                if not lap.get('eligible') or seconds is None or seconds <= 0:
                    continue
                rows.append((game, str(path), stat.st_size, stat.st_mtime_ns, identity['car'], identity['track'],
                             identity.get('layout', ''), lap['lap'], seconds,
                             json.dumps(lap.get('conditions') or {}, ensure_ascii=False, allow_nan=False)))
        with self._connect() as db:
            db.execute('DELETE FROM personal_reference_laps WHERE game=? AND path=?', (game, str(path)))
            db.executemany('INSERT INTO personal_reference_laps VALUES (?,?,?,?,?,?,?,?,?,?)', rows)
            db.execute('INSERT OR REPLACE INTO personal_reference_files VALUES (?,?,?,?,?)',
                       (game, str(path), stat.st_size, stat.st_mtime_ns, version))
        return len(rows)

    def candidates(self, game, identity):
        if not identity.get('car') or not identity.get('track'):
            return []
        with self._connect() as db:
            rows = db.execute('SELECT * FROM personal_reference_laps WHERE game=? AND car=? AND track=? AND layout=? ORDER BY lap_time_s,path,lap',
                              (game, identity['car'], identity['track'], identity.get('layout', ''))).fetchall()
        return [{**dict(row), 'filename': Path(row['path']).name, 'conditions': json.loads(row['conditions_json'])}
                for row in rows if self.source_current(row)]

    def previous_analysis_paths(self):
        """Seed pre-existing saved analyses when a library is first introduced."""
        with self._connect() as db:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sessions'").fetchone()
            if not exists:
                return []
            columns = {row[1] for row in db.execute('PRAGMA table_info(sessions)')}
            if 'file_path' not in columns:
                return []
            return [Path(row[0]) for row in db.execute('SELECT DISTINCT file_path FROM sessions WHERE file_path IS NOT NULL')]
