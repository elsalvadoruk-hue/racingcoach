"""Local observed-driving ledger, independent of analysis modes and bookmarks.

Only explicit writes create storage. Sources replace their own compact summaries
atomically; observation windows remain windows, never inferred driving totals.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import threading


SCHEMA_VERSION = 1
SQLITE_TIMEOUT = .2
_SHA = re.compile(r'[a-f0-9]{64}\Z')
_ENUM = re.compile(r'[A-Z][A-Z0-9_]{0,79}\Z')
_GAMES = {'iracing', 'lmu'}
_KINDS = {'PRACTICE', 'QUALIFY', 'RACE', 'UNKNOWN'}
_BASES = {'UTC', 'NATIVE_SESSION_TIME', 'SOURCE_RELATIVE', 'UNKNOWN'}
_IDENTITY_FIELDS = ('game', 'car_key', 'track_key', 'layout_key', 'identity_status')
_CONDITIONS = {'fuel_level_l', 'track_temp_c', 'air_temp_c', 'track_wetness', 'tire_compound', 'setup_hash'}
_COLUMNS = {
    'sources': ('source_id', 'game', 'schema_version'),
    'observations': ('source_id', 'drive_id', 'game', 'recorded_at', 'payload'),
}


def _fail():
    raise ValueError('주행 관측 요약의 저장 형식이 올바르지 않습니다.')


def _hash(value):
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        _fail()
    return value


def _enum(value):
    if not isinstance(value, str) or not _ENUM.fullmatch(value):
        _fail()
    return value


def _choice(value, options):
    if not isinstance(value, str) or value not in options:
        _fail()
    return value


def _key(value):
    if (not isinstance(value, str) or len(value) > 240 or any(ord(c) < 32 for c in value)
            or value.startswith(('/', '\\')) or re.match(r'^[A-Za-z]:', value)):
        _fail()
    return value


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        _fail()
    try:
        number = float(value)
    except (OverflowError, ValueError):
        _fail()
    if not math.isfinite(number):
        _fail()
    return number


def _recorded_at(value):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 40:
        _fail()
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        _fail()
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail()
    return parsed.astimezone(timezone.utc).isoformat(timespec='microseconds')


def _intervals(values):
    if not isinstance(values, list) or len(values) > 10000:
        _fail()
    checked = []
    for interval in values:
        if not isinstance(interval, (list, tuple)) or len(interval) != 2:
            _fail()
        start, end = map(_number, interval)
        if start < 0 or end <= start:
            _fail()
        checked.append([start, end])
    merged = []
    for start, end in sorted(checked):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    return merged


def _conditions(value):
    if not isinstance(value, dict):
        _fail()
    output = {}
    for key in _CONDITIONS:
        if key not in value:
            continue
        item = value[key]
        if item is None:
            output[key] = None
        elif key == 'setup_hash':
            output[key] = _hash(item)
        else:
            output[key] = _number(item)
            if key not in {'air_temp_c', 'track_temp_c'} and output[key] < 0:
                _fail()
        if key + '_source' in value:
            output[key + '_source'] = _enum(value[key + '_source'])
    return output


def _labels(value):
    if not isinstance(value, dict):
        _fail()
    return {key: _key(value[key]) for key in ('car', 'track', 'layout') if key in value}


def _metrics(value):
    if not isinstance(value, dict) or type(value.get('schema_version')) is not int or value['schema_version'] != 1:
        _fail()
    driving = value.get('driving')
    laps = value.get('laps')
    if not isinstance(driving, dict) or not isinstance(laps, list) or len(laps) > 2048:
        _fail()
    status = _choice(driving.get('status'), {'OBSERVED', 'PARTIAL', 'UNAVAILABLE'})
    windows = _intervals(driving.get('intervals', []))
    facts = {}
    for key in ('time_s', 'distance_m'):
        item = driving.get(key)
        facts[key] = None if item is None else _number(item)
        if facts[key] is not None and facts[key] < 0:
            _fail()
    if status == 'UNAVAILABLE' and (windows or any(item is not None for item in facts.values())):
        _fail()
    if status != 'UNAVAILABLE' and (facts['time_s'] is None or abs(facts['time_s'] - sum(b - a for a, b in windows)) > .02):
        _fail()
    reasons = driving.get('reason_codes', [])
    if not isinstance(reasons, list) or len(reasons) > 100:
        _fail()
    checked_laps = []
    for lap in laps:
        if not isinstance(lap, dict) or type(lap.get('lap_number')) is not int or lap['lap_number'] < 0:
            _fail()
        start, end, lap_time = (_number(lap.get(key)) for key in ('start_s', 'end_s', 'lap_time_s'))
        if start < 0 or end <= start or not 0 < lap_time <= 86400 or type(lap.get('measurable')) is not bool:
            _fail()
        checked_laps.append({'lap_number': lap['lap_number'], 'start_s': start, 'end_s': end,
            'lap_time_s': lap_time, 'measurable': lap['measurable'],
            'conditions': _conditions(lap.get('conditions', {})),
            'official_validity': _choice(lap.get('official_validity'), {'UNKNOWN'})})
    return {'schema_version': 1, 'driving': {'status': status, **facts, 'intervals': windows,
        'reason_codes': sorted({_enum(item) for item in reasons})}, 'laps': checked_laps}


def _observation(value, game):
    if not isinstance(value, dict) or value.get('game') != game:
        _fail()
    coverage = value.get('coverage')
    if not isinstance(coverage, dict):
        _fail()
    sample_count = coverage.get('sample_count')
    if isinstance(sample_count, bool) or not isinstance(sample_count, int) or sample_count < 0:
        _fail()
    reasons = coverage.get('reasons', [])
    if not isinstance(reasons, list) or len(reasons) > 100:
        _fail()
    windows = _intervals(coverage.get('intervals'))
    basis = _choice(coverage.get('time_basis'), _BASES)
    if basis == 'UNKNOWN' and windows:
        _fail()
    checked = {
        'drive_id': _hash(value.get('drive_id')),
        'identity_status': _choice(value.get('identity_status'), {'NATIVE', 'SOURCE_ONLY'}),
        'game': game,
        **{key: _key(value.get(key, '')) for key in ('car_key', 'track_key', 'layout_key')},
        'session_type': _choice(value.get('session_type'), _KINDS),
        'session_type_source': _enum(value.get('session_type_source')),
        'recorded_at': _recorded_at(value.get('recorded_at')),
        'recorded_at_source': _enum(value.get('recorded_at_source')),
        'coverage': {'time_basis': basis, 'intervals': windows,
                     'status': _choice(coverage.get('status'), {'OBSERVED', 'PARTIAL', 'UNAVAILABLE'}),
                     'sample_count': sample_count, 'reasons': sorted({_enum(v) for v in reasons})},
        'validity_status': _choice(value.get('validity_status', 'UNKNOWN'), {'UNKNOWN', 'UNVERIFIED'}),
        'conditions': _conditions(value.get('conditions', {})),
    }
    if 'labels' in value:
        checked['labels'] = _labels(value['labels'])
    if 'metrics' in value:
        checked['metrics'] = _metrics(value['metrics'])
    if checked['identity_status'] == 'NATIVE' and any(
        checked[key] in {'', 'UNKNOWN'} for key in ('car_key', 'track_key')
    ):
        _fail()
    return checked


def _summary(value):
    if not isinstance(value, dict) or type(value.get('schema_version')) is not int or value['schema_version'] != SCHEMA_VERSION:
        _fail()
    source_id = _hash(value.get('source_id'))
    game = _choice(value.get('game'), _GAMES)
    observations = value.get('observations')
    if not isinstance(observations, list) or len(observations) > 256:
        _fail()
    checked = [_observation(item, game) for item in observations]
    if len({item['drive_id'] for item in checked}) != len(checked):
        _fail()
    return source_id, game, checked


def _compatible(before, after):
    return (all(before[key] == after[key] for key in _IDENTITY_FIELDS)
            and (before['session_type'] == after['session_type']
                 or 'UNKNOWN' in (before['session_type'], after['session_type'])))


def _aggregate(rows):
    observations = [{'source_id': row[0], **_observation(json.loads(row[1]), row[2])} for row in rows]
    first = observations[0]
    bases = {}
    reasons = set()
    for item in observations:
        if not _compatible(first, item):
            _fail()
        coverage = item['coverage']
        bases.setdefault(coverage['time_basis'], []).extend(coverage['intervals'])
        reasons.update(coverage['reasons'])
    by_basis = {basis: _intervals(windows) for basis, windows in sorted(bases.items())}
    mixed = len(by_basis) > 1
    if mixed:
        reasons.add('MIXED_TIME_BASES')
    if len(observations) > 1:
        reasons.add('SOURCE_SAMPLE_COUNTS_NOT_DEDUPLICATED')
    known_dates = [item['recorded_at'] for item in observations if item['recorded_at'] is not None]
    labels = {}
    for key in ('car', 'track', 'layout'):
        if any(key in item.get('labels', {}) for item in observations):
            known = {item['labels'][key].strip() for item in observations
                     if item.get('labels', {}).get(key, '').strip()}
            # A missing display field must not mask a later known label. Two
            # conflicting names do not change the verified identity keys.
            labels[key] = next(iter(known)) if len(known) == 1 else ''
    kinds = {item['session_type'] for item in observations} - {'UNKNOWN'}
    if len(kinds) > 1:
        _fail()
    has_windows = any(by_basis.values())
    coverage_status = ('UNAVAILABLE' if not has_windows else 'OBSERVED'
                       if not mixed and all(item['coverage']['status'] == 'OBSERVED' for item in observations)
                       else 'PARTIAL')
    return {
        **{key: first[key] for key in ('drive_id', *_IDENTITY_FIELDS)},
        'labels': labels,
        'session_type': next(iter(kinds)) if kinds else 'UNKNOWN',
        'recorded_at': min(known_dates) if known_dates else None,
        'recorded_at_source': first['recorded_at_source'] if len(observations) == 1 else 'SOURCE_OBSERVATIONS',
        'session_type_source': first['session_type_source'] if len(observations) == 1 else 'SOURCE_OBSERVATIONS',
        'validity_status': 'UNVERIFIED' if any(item['validity_status'] == 'UNVERIFIED' for item in observations) else 'UNKNOWN',
        'source_count': len(observations), 'source_ids': [item['source_id'] for item in observations],
        'coverage': {'time_basis': 'MIXED' if mixed else next(iter(by_basis)),
                     'intervals': [] if mixed else next(iter(by_basis.values())),
                     'intervals_by_basis': by_basis, 'status': coverage_status,
                     'sample_count': first['coverage']['sample_count'] if len(observations) == 1 else None,
                     'source_sample_count': sum(item['coverage']['sample_count'] for item in observations),
                     'reasons': sorted(reasons)},
        'observations': observations,
    }


class DriverHistoryStore:
    def __init__(self, path=None):
        if path is None:
            configured = os.environ.get('IRACING_DRIVER_HISTORY_PATH', '').strip()
            if configured:
                path = configured
            else:
                from app_settings import data_dir
                path = data_dir() / 'driver-history.sqlite3'
        self.path = Path(path).expanduser()
        self._write_lock = threading.RLock()

    @contextmanager
    def _connection(self, write=False):
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        target = self.path if write else self.path.resolve().as_uri() + '?mode=ro'
        connection = sqlite3.connect(target, uri=not write, timeout=SQLITE_TIMEOUT)
        try:
            connection.execute('PRAGMA foreign_keys=ON')
            connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            if write and not tables and version == 0:
                connection.execute('CREATE TABLE sources (source_id TEXT PRIMARY KEY, game TEXT NOT NULL, schema_version INTEGER NOT NULL)')
                connection.execute('CREATE TABLE observations (source_id TEXT NOT NULL REFERENCES sources(source_id) ON DELETE CASCADE, drive_id TEXT NOT NULL, game TEXT NOT NULL, recorded_at TEXT, payload TEXT NOT NULL, PRIMARY KEY (source_id,drive_id))')
                connection.execute('CREATE INDEX observations_drive ON observations(drive_id)')
                connection.execute('CREATE INDEX observations_game ON observations(game,drive_id)')
                connection.execute('PRAGMA user_version=1')
            elif version != SCHEMA_VERSION or tables != set(_COLUMNS) or any(
                tuple(row[1] for row in connection.execute(f'PRAGMA table_info({table})')) != columns
                for table, columns in _COLUMNS.items()
            ):
                raise ValueError('지원하지 않는 주행 관측 저장소입니다.')
            yield connection
            if write:
                connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def record_source(self, summary):
        source_id, game, observations = _summary(summary)
        payloads = [(item, json.dumps(item, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':')))
                    for item in observations]
        with self._write_lock, self._connection(write=True) as connection:
            old_game = connection.execute('SELECT game FROM sources WHERE source_id=?', (source_id,)).fetchone()
            if old_game and old_game[0] != game:
                _fail()
            for item, _ in payloads:
                for existing_source, raw in connection.execute('SELECT source_id,payload FROM observations WHERE drive_id=?', (item['drive_id'],)):
                    before = _observation(json.loads(raw), game)
                    if not _compatible(before, item) or (existing_source != source_id and item['identity_status'] == 'SOURCE_ONLY'):
                        raise ValueError('다른 주행 범위에 같은 식별자를 사용할 수 없습니다.')
            connection.execute('INSERT INTO sources VALUES(?,?,?) ON CONFLICT(source_id) DO UPDATE SET schema_version=excluded.schema_version', (source_id, game, SCHEMA_VERSION))
            connection.execute('DELETE FROM observations WHERE source_id=?', (source_id,))
            connection.executemany('INSERT INTO observations VALUES(?,?,?,?,?)',
                                   [(source_id, item['drive_id'], game, item['recorded_at'], payload) for item, payload in payloads])
        return {'saved': True, 'source_id': source_id, 'observation_count': len(observations)}

    def list_drives(self, game=None, limit=100, offset=0):
        if game is not None:
            _choice(game, _GAMES)
        if type(limit) is not int or not 1 <= limit <= 200 or type(offset) is not int or offset < 0:
            _fail()
        if not self.path.exists():
            return []
        with self._connection() as connection:
            where, args = (' WHERE game=?', [game]) if game is not None else ('', [])
            ids = [row[0] for row in connection.execute(
                'SELECT drive_id FROM observations' + where + ' GROUP BY drive_id ORDER BY MAX(recorded_at) DESC,drive_id LIMIT ? OFFSET ?',
                [*args, limit, offset])]
            if not ids:
                return []
            placeholders = ','.join('?' for _ in ids)
            rows = connection.execute(f'SELECT source_id,payload,game,drive_id FROM observations WHERE drive_id IN ({placeholders}) ORDER BY source_id', ids).fetchall()
        grouped = {drive_id: [] for drive_id in ids}
        for row in rows:
            grouped[row[3]].append(row[:3])
        return [_aggregate(grouped[drive_id]) for drive_id in ids]

    def get_drive(self, drive_id):
        _hash(drive_id)
        if not self.path.exists():
            return None
        with self._connection() as connection:
            rows = connection.execute('SELECT source_id,payload,game FROM observations WHERE drive_id=? ORDER BY source_id', (drive_id,)).fetchall()
        return _aggregate(rows) if rows else None

    def report_snapshot(self, game):
        """Read all compact inputs together; report views never reopen recordings."""
        _choice(game, _GAMES)
        if not self.path.exists():
            return {'source_count': 0, 'drives': []}
        with self._connection() as connection:
            source_count = connection.execute('SELECT COUNT(*) FROM sources WHERE game=?', (game,)).fetchone()[0]
            rows = connection.execute('SELECT source_id,payload,game,drive_id FROM observations WHERE game=? ORDER BY drive_id,source_id', (game,)).fetchall()
        grouped = {}
        for source_id, raw, item_game, drive_id in rows:
            grouped.setdefault(drive_id, []).append((source_id, raw, item_game))
        return {'source_count': source_count, 'drives': [_aggregate(items) for items in grouped.values()]}

    def status(self, game=None):
        if game is not None:
            _choice(game, _GAMES)
        result = {'schema_version': SCHEMA_VERSION, 'game': game, 'source_count': 0,
                  'observation_count': 0, 'drive_observation_count': 0, 'native_session_count': 0,
                  'source_only_observation_count': 0, 'unknown_recorded_at_count': 0,
                  'earliest_recorded_at': None, 'latest_recorded_at': None}
        if not self.path.exists():
            return result
        where, args = (' WHERE game=?', [game]) if game is not None else ('', [])
        with self._connection() as connection:
            result['source_count'] = connection.execute('SELECT COUNT(*) FROM sources' + where, args).fetchone()[0]
            rows = connection.execute('SELECT drive_id,recorded_at,payload,game FROM observations' + where, args).fetchall()
        result['observation_count'] = len(rows)
        result['drive_observation_count'] = len({row[0] for row in rows})
        native, source_only, dates = set(), set(), []
        for drive_id, recorded_at, payload, item_game in rows:
            item = _observation(json.loads(payload), _choice(item_game, _GAMES))
            if item['drive_id'] != drive_id or item['recorded_at'] != recorded_at:
                _fail()
            (native if item['identity_status'] == 'NATIVE' else source_only).add(drive_id)
            if recorded_at is None:
                result['unknown_recorded_at_count'] += 1
            else:
                dates.append(recorded_at)
        result.update(native_session_count=len(native), source_only_observation_count=len(source_only),
                      earliest_recorded_at=min(dates) if dates else None, latest_recorded_at=max(dates) if dates else None)
        return result
