"""Explicit LMU analysis bookmarks and measured summaries, isolated from iRacing.

Reading an empty history never creates a file. Telemetry is always opened by the
existing read-only workspace adapter; this store contains no raw samples/audio.
"""
from __future__ import annotations

import asyncio
import inspect
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid

from fastapi import HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from lmu_telemetry import LmuTelemetryUnavailable


DEFAULT_PATH = Path(__file__).resolve().parent.parent / 'logs' / 'lmu-history' / 'history.sqlite3'


RECORDING_NAME = r'^(?:[a-f0-9]{24}|[^\\/:*?"<>|]{1,240}\.duckdb)$'


class HistorySelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    # A recording's file name (shared workspace) or the earlier 24-hex file ID.
    filename: str = Field(pattern=RECORDING_NAME)
    target_lap: int = Field(ge=0, strict=True)
    reference_filename: str | None = Field(default=None, pattern=RECORDING_NAME)
    reference_lap: int = Field(ge=0, strict=True)
    note: str = Field(default='', max_length=2000)


def measured_summary(session):
    """Retain only server-computed facts and source identities, never GPS/driver."""
    metadata = session.get('session', {})
    target = next((lap for lap in session.get('laps', [])
                   if lap['lap_number'] == session['defaultTarget']), {})
    reference = next((lap for lap in session.get('reference_laps', session.get('laps', []))
                      if lap['lap_number'] == session['defaultReference']), {})
    return {
        'file_name': session.get('file_name') or session.get('filename'),
        'reference_file_name': (session.get('reference_file_name') or session.get('defaultReferenceFile')
                                or session.get('reference_filename') or session.get('filename')),
        **{key: metadata.get(key) for key in ('car_name', 'track_name', 'track_layout', 'session_type', 'recording_time')},
        'target_time_s': target.get('lap_time_s'),
        'reference_time_s': reference.get('lap_time_s'),
        'fastest_recorded_time_s': session.get('lap_statistics', {}).get('fastest_recorded_time_s'),
        'lap_count': len(session.get('laps', [])),
        'measured_lap_count': sum(lap.get('measurement_eligible') is True or lap.get('status') == 'VALID'
                                  for lap in session.get('laps', [])),
        'valid': None, 'reference_status': 'VALIDITY_UNVERIFIED',
    }


class LmuHistoryStore:
    def __init__(self, path=None):
        self.path = Path(path if path is not None else os.environ.get('LMU_HISTORY_PATH', DEFAULT_PATH))

    @contextmanager
    def connection(self, write=False):
        connection = None
        try:
            if write:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                connection = sqlite3.connect(self.path, timeout=10)
                connection.execute('BEGIN IMMEDIATE')
            else:
                connection = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True, timeout=10)
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            if not tables and version == 0 and write:
                connection.execute('CREATE TABLE history (record_id TEXT PRIMARY KEY, selection_key TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, payload TEXT NOT NULL)')
                connection.execute('PRAGMA user_version=1')
            elif tables != {'history'} or version != 1 or tuple(row[1] for row in connection.execute('PRAGMA table_info(history)')) != ('record_id', 'selection_key', 'created_at', 'updated_at', 'payload'):
                raise ValueError('지원하지 않는 LMU 분석 이력 저장소입니다.')
            yield connection
            if write:
                connection.commit()
        except (OSError, sqlite3.Error) as exc:
            raise ValueError('LMU 분석 이력을 읽거나 저장할 수 없습니다.') from exc
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def decode(row):
        try:
            payload = json.loads(row[3])
            HistorySelection.model_validate(payload['selection'])
            if not isinstance(payload['summary'], dict):
                raise ValueError()
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError('LMU 분석 이력의 저장 형식이 올바르지 않습니다.') from exc
        return {'record_id': row[0], 'created_at': row[1], 'updated_at': row[2], **payload}

    def save(self, selection, session):
        checked = HistorySelection.model_validate(selection)
        identity = {'filename': session['filename'], 'target_lap': session['defaultTarget'],
                    'reference_filename': session.get('reference_filename') or session['filename'],
                    'reference_lap': session['defaultReference']}
        # Use the adapter's resolved selection; a default can never identify a different lap.
        HistorySelection.model_validate(identity)
        selection_key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        payload = json.dumps({'selection': identity, 'note': checked.note.strip(),
                              'summary': measured_summary(session)}, ensure_ascii=False, allow_nan=False)
        now = datetime.now(timezone.utc).isoformat()
        with self.connection(write=True) as connection:
            connection.execute('INSERT INTO history VALUES(?,?,?,?,?) ON CONFLICT(selection_key) DO UPDATE SET updated_at=excluded.updated_at,payload=excluded.payload',
                               (uuid.uuid4().hex, selection_key, now, now, payload))
            row = connection.execute('SELECT record_id,created_at,updated_at,payload FROM history WHERE selection_key=?', (selection_key,)).fetchone()
        return self.decode(row)

    def records(self, q='', track='', car='', session_type='', limit=200):
        if not self.path.exists():
            return {'records': [], 'total': 0, 'tracks': [], 'cars': [], 'session_types': []}
        with self.connection() as connection:
            records = [self.decode(row) for row in connection.execute('SELECT record_id,created_at,updated_at,payload FROM history ORDER BY updated_at DESC,record_id')]
        options = {plural: sorted({item['summary'].get(field) for item in records if item['summary'].get(field)})
                   for plural, field in (('tracks', 'track_name'), ('cars', 'car_name'), ('session_types', 'session_type'))}
        matched = [item for item in records if
                   all(not value or item['summary'].get(field) == value for field, value in
                       (('track_name', track), ('car_name', car), ('session_type', session_type))) and
                   (not q or q.casefold() in ' '.join(str(value or '') for value in
                    (*item['summary'].values(), item.get('note'))).casefold())]
        return {'records': matched[:limit], 'total': len(matched), **options}

    def get(self, record_id):
        if not self.path.exists():
            raise KeyError('저장한 LMU 분석을 찾을 수 없습니다.')
        with self.connection() as connection:
            row = connection.execute('SELECT record_id,created_at,updated_at,payload FROM history WHERE record_id=?', (record_id,)).fetchone()
        if row is None:
            raise KeyError('저장한 LMU 분석을 찾을 수 없습니다.')
        return self.decode(row)


def register_history_routes(app, workspace_request, store=None):
    history = store if store is not None else LmuHistoryStore()

    def operation(callback):
        try:
            return callback()
        except HTTPException:
            raise
        except LmuTelemetryUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, str(exc).strip("'")) from exc
        except FileNotFoundError as exc:
            raise HTTPException(404, '기록한 LMU 파일이 이동되거나 삭제됐습니다. 주행 선택에서 파일을 확인하세요.') from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get('/api/lmu/history')
    async def list_history(q: str = Query(default='', max_length=200),
            track: str = Query(default='', max_length=300), car: str = Query(default='', max_length=300),
            session_type: str = Query(default='', max_length=100), limit: int = Query(default=200, ge=1, le=500)):
        return await asyncio.to_thread(lambda: operation(lambda:
            {'status': 'success', **history.records(q, track, car, session_type, limit)}))

    async def session_for(selection):
        # The shared workspace is async; the standalone adapter is a plain function.
        if not inspect.iscoroutinefunction(workspace_request):
            return await asyncio.to_thread(lambda: operation(lambda: workspace_request(selection)))
        try:
            return await workspace_request(selection)
        except HTTPException:
            raise
        except (LmuTelemetryUnavailable, KeyError, FileNotFoundError, ValueError, OSError) as exc:
            def failed():
                raise exc
            return operation(failed)

    @app.post('/api/lmu/history')
    async def save_history(body: HistorySelection):
        session = await session_for(body.model_dump(exclude={'note'}, exclude_none=True))
        return await asyncio.to_thread(lambda: operation(lambda:
            {'status': 'success', 'record': history.save(body.model_dump(), session)}))

    @app.post('/api/lmu/history/{record_id}/open')
    async def open_history(record_id: str):
        record = await asyncio.to_thread(lambda: operation(lambda: history.get(record_id)))
        return {'status': 'success', 'record': record, 'session': await session_for(record['selection'])}
