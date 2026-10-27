"""Versioned input-only enrichment of existing compact observations.

Clock/identity scopes reuse the source adapter; no lap-quality, driving-time or
race-result calculation is repeated. Only successful input attempts are marked
on the ledger. Failed automatic attempts live in a separate local journal.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3

import numpy as np

from driver_style import CALCULATION_VERSION as INPUT_VERSION, measure_inputs


def current_inputs(metrics):
    return (isinstance(metrics, dict) and metrics.get('input_version') == INPUT_VERSION
            and 'METRICS_EXTRACTION_FAILED' not in metrics.get('driving', {}).get('reason_codes', []))


def outdated_input_sources(snapshot):
    return {row['source_id'] for drive in snapshot['drives'] for row in drive['observations']
            if not current_inputs(row.get('metrics'))}


def source_observations(snapshot):
    result = {}
    for drive in snapshot['drives']:
        for observation in drive['observations']:
            result.setdefault(observation['source_id'], []).append(observation)
    return result


def input_request(observations):
    """Pass no labels, conditions, result records or private source fields."""
    return [dict(drive_id=row['drive_id'], laps=[{key: lap[key]
        for key in ('lap_number', 'start_s', 'end_s', 'measurable', 'replay_status')}
        for lap in row.get('laps', row.get('metrics', {}).get('laps', []))]) for row in observations]


def measure_source_inputs(telemetry, source_id, observations):
    from driver_history_summary import build_source_summary

    game = 'lmu' if (getattr(telemetry, 'metadata', {}) or {}).get('game') == 'lmu' else 'iracing'
    requests = {row['drive_id']: row for row in input_request(observations)}
    if len(requests) != len(observations) or not requests:
        raise ValueError('Input observation identity unavailable')
    times = np.asarray(telemetry.channels.get('SessionTime', []), dtype=float)
    if times.ndim != 1:
        raise ValueError('Input clock unavailable')
    measured, found = {key: {} for key in requests}, set()

    def details(telemetry, scope, *, start_idx=0, end_idx=None, offset=None):
        drive_id = scope['drive_id']
        if drive_id not in requests:
            return
        found.add(drive_id)
        end_idx = len(times) if end_idx is None else end_idx
        clock = times[start_idx:end_idx]
        finite = np.flatnonzero(np.isfinite(clock))
        ordered = clock[finite] + offset if offset is not None else None
        if ordered is not None and np.any(np.diff(ordered) < 0):
            raise ValueError('Input scope clock reversed')
        for lap in requests[drive_id]['laps']:
            key = (lap['lap_number'], lap['start_s'], lap['end_s'])
            if offset is None:
                raise ValueError('Input clock mapping unavailable')
            # Stored boundaries are rounded to six decimals. Match actual
            # sample boundaries within the same independently identified scope.
            def boundary(stamp):
                left, right = np.searchsorted(ordered, [stamp - 1.1e-6, stamp + 1.1e-6], side='left')
                return finite[left:right]
            starts, ends = boundary(lap['start_s']), boundary(lap['end_s'])
            if not len(starts) or not len(ends):
                continue  # The same native observation can span disjoint scopes.
            if len(starts) != 1 or len(ends) != 1 or key in measured[drive_id]:
                raise ValueError('Ambiguous input clock mapping')
            left, right = int(starts[0]) + start_idx, int(ends[0]) + start_idx
            if right <= left:
                raise ValueError('Invalid input sample range')
            inputs = measure_inputs(telemetry, left, right, measurable=lap['measurable'],
                replay_status=lap['replay_status'], game=game)
            measured[drive_id][key] = {**lap, 'inputs': inputs}

    build_source_summary(telemetry, source_id, detail_builder=details)
    if found != set(requests) or any(len(measured[key]) != len(row['laps']) for key, row in requests.items()):
        raise ValueError('Input observation boundaries unavailable')
    return dict(schema_version=1, source_id=source_id, game=game, input_version=INPUT_VERSION,
                observations=[dict(drive_id=key, laps=list(measured[key].values())) for key in requests])


class InputAttempts:
    """Do not repeatedly parse a failed unchanged source on automatic starts."""
    def __init__(self, ledger_path):
        path = Path(ledger_path)
        self.path = path.with_name(path.stem + '.inputs.sqlite3')

    @contextmanager
    def opened(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5.)
        try:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            if not tables and version == 0:
                connection.execute('CREATE TABLE attempts (game TEXT NOT NULL,source_id TEXT NOT NULL,'
                    'version TEXT NOT NULL,status TEXT NOT NULL,PRIMARY KEY(game,source_id,version))')
                connection.execute('PRAGMA user_version=1')
                connection.commit()
            elif (tables != {'attempts'} or version != 1 or tuple(row[1] for row in
                    connection.execute('PRAGMA table_info(attempts)')) != ('game', 'source_id', 'version', 'status')):
                raise ValueError('Unsupported input attempt journal')
            self.connection = connection
            yield self
        finally:
            connection.close()

    def failed(self, game, source_id):
        return self.connection.execute('SELECT status FROM attempts WHERE game=? AND source_id=? AND version=?',
            (game, source_id, INPUT_VERSION)).fetchone() == ('FAILED',)

    def remember_failure(self, game, source_id):
        self.connection.execute('INSERT OR REPLACE INTO attempts VALUES(?,?,?,?)',
            (game, source_id, INPUT_VERSION, 'FAILED'))
        self.connection.commit()
