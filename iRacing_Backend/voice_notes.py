"""Persistent driver statements and conservative IBT/corner linking."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time
import uuid

import numpy as np

from live_telemetry import number, session_identity


def feelings(text):
    categories = []
    for key, pattern in [('UNDERSTEER', r'언더|앞.{0,8}(밀|안.{0,2}돌)|차가.{0,3}안.{0,2}돌'),
                         ('OVERSTEER', r'오버|뒤.{0,8}(흐|날|미끄|불안|돌)'),
                         ('BRAKING', r'제동.{0,6}불안|브레이크.{0,8}(흔들|불안)'),
                         ('KERB', r'(연석|커브).{0,8}(튕|불안|접지)'),
                         ('DIFFICULT', r'어렵|힘들|불편|무섭|불안'),
                         ('IMPROVED', r'편해|편하|좋아|나아|개선')]:
        if re.search(pattern, text):
            categories.append(key)
    return categories


class VoiceNoteStore:
    # The ~20s sample trail lives in its own column so lists and polling never parse it.
    COLUMNS = 'id,created_at,updated_at,text,original_text,reply,source,context_json,feeling_json,reply_source,service_error'

    def __init__(self, path):
        self.path = Path(path)
        self.available = True
        self.revision = 0
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._initialize()
        except (OSError, sqlite3.Error):
            self.available = False

    def _initialize(self):
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS voice_notes (
                    id TEXT PRIMARY KEY, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    text TEXT NOT NULL, original_text TEXT NOT NULL, reply TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL, context_json TEXT NOT NULL, feeling_json TEXT NOT NULL,
                    reply_source TEXT NOT NULL DEFAULT '', service_error TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS voice_note_links (
                    note_id TEXT NOT NULL, filename TEXT NOT NULL, file_key TEXT NOT NULL,
                    method TEXT NOT NULL, lap_number INTEGER, distance_pct REAL,
                    PRIMARY KEY(note_id, filename)
                );
                CREATE INDEX IF NOT EXISTS voice_notes_created ON voice_notes(created_at);
                CREATE TABLE IF NOT EXISTS radio_delivery (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at REAL NOT NULL,
                    event_id TEXT NOT NULL, run_id TEXT NOT NULL, status TEXT NOT NULL,
                    event_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS radio_delivery_run ON radio_delivery(run_id, id);
            ''')
            if 'recent_json' not in {row[1] for row in db.execute('PRAGMA table_info(voice_notes)')}:
                db.execute("ALTER TABLE voice_notes ADD COLUMN recent_json TEXT NOT NULL DEFAULT ''")
            # Lossless one-time move of samples saved inside the context by older versions.
            legacy = db.execute("SELECT id, context_json FROM voice_notes WHERE recent_json='' AND context_json LIKE '%\"recent\"%'").fetchall()
            for row in legacy:
                context = json.loads(row['context_json'])
                if not isinstance(context, dict) or 'recent' not in context:
                    continue
                recent = context.pop('recent')
                db.execute('UPDATE voice_notes SET context_json=?, recent_json=? WHERE id=?',
                           (json.dumps(context, ensure_ascii=False, allow_nan=False),
                            json.dumps(recent, ensure_ascii=False, allow_nan=False) if recent else '', row['id']))

    @contextmanager
    def connect(self):
        if not self.available:
            raise ValueError('대화 기록 DB를 열 수 없습니다. 저장 경로의 쓰기 권한을 확인하세요.')
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def decode(self, row):
        data = dict(row)
        recent = data.pop('recent_json', None)
        data['context'] = json.loads(data.pop('context_json'))
        data['feelings'] = json.loads(data.pop('feeling_json'))
        if recent is not None and isinstance(data['context'], dict):
            data['context']['recent'] = json.loads(recent) if recent else []
        return data

    def get(self, note_id, with_recent=True):
        if not self.available:
            return None
        columns = self.COLUMNS + (',recent_json' if with_recent else '')
        with self.connect() as db:
            row = db.execute(f'SELECT {columns} FROM voice_notes WHERE id=?', (note_id,)).fetchone()
        return self.decode(row) if row else None

    def list(self, limit=200, with_recent=False):
        if not self.available:
            return []
        columns = self.COLUMNS + (',recent_json' if with_recent else '')
        with self.connect() as db:
            rows = db.execute(f'SELECT {columns} FROM voice_notes ORDER BY created_at DESC LIMIT ?', (limit,)).fetchall()
        return [self.decode(row) for row in rows]

    def recents(self, note_ids):
        """Sample trails for the few notes that can actually match a file."""
        note_ids, found = list(note_ids), {}
        if not self.available or not note_ids:
            return found
        with self.connect() as db:
            for start in range(0, len(note_ids), 400):
                chunk = note_ids[start:start + 400]
                for row in db.execute(f"SELECT id, recent_json FROM voice_notes WHERE id IN ({','.join('?' * len(chunk))})", chunk):
                    found[row['id']] = json.loads(row['recent_json']) if row['recent_json'] else []
        return found

    def record_radio(self, events):
        with self.connect() as db:
            db.executemany('INSERT INTO radio_delivery(created_at,event_id,run_id,status,event_json) VALUES (?,?,?,?,?)',
                [(time.time(), e['event_id'], e.get('run_id') or '', e['status'],
                  json.dumps(e, ensure_ascii=False, allow_nan=False)) for e in events])
            # Retain up to 100k lifecycle events, without growing forever.
            db.execute('DELETE FROM radio_delivery WHERE id <= (SELECT COALESCE(MAX(id),0)-100000 FROM radio_delivery)')

    def radio_log(self, limit=200, run_id=None):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM radio_delivery ' + ('WHERE run_id=? ' if run_id else '')
                              + 'ORDER BY id DESC LIMIT ?', (run_id, limit) if run_id else (limit,)).fetchall()
        return [{**json.loads(r['event_json']), 'recorded_at': r['created_at'], 'id': r['id']} for r in rows]

    def save(self, text, context, source='TEXT', note_id=None):
        note_id = note_id or str(uuid.uuid4())
        now = time.time()
        context = dict(context)
        recent = context.pop('recent', None)
        with self.connect() as db:
            db.execute('''INSERT OR IGNORE INTO voice_notes
                (id,created_at,updated_at,text,original_text,source,context_json,feeling_json,recent_json)
                VALUES (?,?,?,?,?,?,?,?,?)''',
                (note_id, now, now, text, text, source, json.dumps(context, ensure_ascii=False, allow_nan=False), json.dumps(feelings(text)),
                 json.dumps(recent, ensure_ascii=False, allow_nan=False) if recent else ''))
        self.revision += 1
        return self.get(note_id)

    def update(self, note_id, *, text=None, reply=None, reply_source=None, service_error=None, report_delivery=None):
        values = {'updated_at': time.time()}
        if text is not None:
            values.update(text=text, feeling_json=json.dumps(feelings(text)))
        for name, value in [('reply', reply), ('reply_source', reply_source), ('service_error', service_error)]:
            if value is not None:
                values[name] = value
        if report_delivery is not None:
            note = self.get(note_id, with_recent=False)
            if note and note.get('source') == 'AUTO_REPORT':
                context = {**note['context'], 'report_delivery': report_delivery}
                values['context_json'] = json.dumps(context, ensure_ascii=False, allow_nan=False)
        with self.connect() as db:
            db.execute('UPDATE voice_notes SET ' + ','.join(f'{key}=?' for key in values) + ' WHERE id=?', (*values.values(), note_id))
        self.revision += 1
        return self.get(note_id)

    def link(self, note_id, filename, file_key, method, lap_number, distance_pct):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO voice_note_links VALUES (?,?,?,?,?,?)',
                       (note_id, filename, file_key, method, lap_number, distance_pct))

    def links(self, filename):
        with self.connect() as db:
            return {row['note_id']: dict(row) for row in db.execute('SELECT * FROM voice_note_links WHERE filename=?', (filename,))}


def public_note(note):
    context = note.get('context') or {}
    return {**{k: v for k, v in note.items() if k != 'context'},
            'context': {k: v for k, v in context.items() if k != 'recent'}}


def identity_compatible(live_identity, file_identity):
    # IDs where available; exact paths otherwise. No fuzzy car/track name guessing.
    for id_key, path_key in [('car_id', 'car_path'), ('track_id', 'track_path')]:
        left, right = live_identity.get(id_key), file_identity.get(id_key)
        if left and right and left != '0' and right != '0':
            if str(left) != str(right):
                return False
        elif not live_identity.get(path_key) or live_identity.get(path_key) != file_identity.get(path_key):
            return False
    return (live_identity.get('track_config') or '') == (file_identity.get('track_config') or '')


def file_identity(telemetry):
    info = telemetry.session_info or {}
    return session_identity(info.get('WeekendInfo') or {}, info.get('DriverInfo') or {})


def recording_key(telemetry):
    """Initial samples distinguish replaced recordings without using growing file size."""
    digest = hashlib.sha256()
    digest.update(json.dumps([file_identity(telemetry), telemetry.session_text[:160]],
                             sort_keys=True, ensure_ascii=False).encode('utf-8'))
    for channel in ('SessionTime', 'SessionNum', 'Lap', 'LapDistPct', 'Speed'):
        digest.update(channel.encode('ascii'))
        values = np.asarray(telemetry.channels.get(channel, []), dtype='<f8')[:32].copy()
        values[~np.isfinite(values)] = 0
        digest.update(values.tobytes())
    return digest.hexdigest()


def matching_context(note, telemetry):
    context = note.get('context') or {}
    identity = context.get('identity') or {}
    snapshot = context.get('snapshot') or {}
    other = file_identity(telemetry)
    if not identity_compatible(identity, other) or snapshot.get('is_replay'):
        return None
    strong = False
    for key in ('session_id', 'subsession_id'):
        a, b = identity.get(key), other.get(key)
        if a and b and str(a) != '0' and str(b) != '0':
            if str(a) != str(b):
                return None
            strong = True
    channels = telemetry.channels
    if not {'SessionTime', 'Lap', 'LapDistPct'}.issubset(channels):
        return None
    cached = getattr(telemetry, '_voice_time_index', None)
    if cached is None:
        times = np.asarray(channels['SessionTime'], dtype=float)
        finite_indices = np.flatnonzero(np.isfinite(times))
        order = np.argsort(times[finite_indices], kind='stable')
        cached = (times, finite_indices[order], times[finite_indices[order]])
        telemetry._voice_time_index = cached
    times, finite_indices, sorted_times = cached
    if not len(finite_indices):
        return None
    # SessionTime may reset between sessions in one file; don't interpolate across resets.
    records = context.get('recent') or [snapshot]
    selected, matches = None, 0
    for point in records:
        if number(point.get('session_time')) is None or number(point.get('distance_pct')) is None:
            continue
        lo, hi = np.searchsorted(sorted_times, [point['session_time'] - .12, point['session_time'] + .12])
        candidates = finite_indices[lo:hi]
        if 'SessionNum' in channels and point.get('session_num') is not None:
            candidates = candidates[np.asarray(channels['SessionNum'])[candidates] == point['session_num']]
        if point.get('lap_number') is not None:
            candidates = candidates[np.asarray(channels['Lap'])[candidates] == point['lap_number']]
        candidates = candidates[np.abs(np.asarray(channels['LapDistPct'])[candidates] - point['distance_pct']) <= .002]
        if 'Speed' in channels and number(point.get('speed_mps')) is not None:
            candidates = candidates[np.abs(np.asarray(channels['Speed'])[candidates] - point['speed_mps']) <= .7]
        if not len(candidates):
            continue
        idx = int(candidates[np.argmin(np.abs(times[candidates] - point['session_time']))])
        matches += 1
        selected = {'index': idx, 'lap_number': int(channels['Lap'][idx]),
                    'distance_pct': float(channels['LapDistPct'][idx]), 'session_time': float(times[idx])}
    if selected is None or (not strong and matches < 5):
        return None
    if number(snapshot.get('session_time')) is not None and abs(selected['session_time'] - snapshot['session_time']) > .15:
        return None
    if not strong:
        # Local practice has zero online IDs. Require matching sample fingerprints AND
        # a newly written file within the duration of this run, never just the latest file.
        elapsed = float(np.nanmax(times) - np.nanmin(times))
        age = telemetry.path.stat().st_mtime - note['created_at']
        if age < -3 or age > elapsed + 300:
            return None
    return {**selected, 'method': 'SESSION_AND_SAMPLES' if strong else 'SAMPLES_AND_FILE_TIME', 'matches': matches}


def anchor_corner(note, match, corners):
    def at(pct):
        return next((c for c in corners if number(c.get('entry_pct')) is not None and number(c.get('exit_pct')) is not None
                     and c['entry_pct'] <= pct <= c['exit_pct']), None)
    pct = match['distance_pct']
    corner = at(pct)
    if match.get('method') == 'MANUAL':
        return corner, pct, 'MANUAL'
    if corner:
        return corner, pct, 'AT_SPEECH'
    # A short comment after exit usually refers to the previous turn. Prefer an
    # actually observed turning sample, limited to eight seconds before speaking.
    snapshot = (note.get('context') or {}).get('snapshot') or {}
    t = number(snapshot.get('session_time'))
    if t is not None and not re.search(r'다음|앞으로', note['text']):
        for point in reversed((note.get('context') or {}).get('recent') or []):
            pt = number(point.get('session_time'))
            pp = number(point.get('distance_pct'))
            if pt is None or pp is None or not 0 <= t - pt <= 8 or point.get('lap_number') != match['lap_number']:
                continue
            if abs(number(point.get('steering_rad')) or 0) < .04:
                continue
            corner = at(pp)
            if corner:
                return corner, pp, 'RECENT_CORNER'
    return None, pct, 'LOCATION_REVIEW_NEEDED'


def review_notes(store, analyzer, result, request, max_notes=1000):
    if not store.available:
        return {'notes': [], 'pending': [], 'error': '대화 기록 DB를 열 수 없습니다. 저장 경로를 확인하세요.'}
    telemetry = analyzer.telemetry
    filename = telemetry.path.name
    identity = file_identity(telemetry)
    links = store.links(filename)
    # Allow append-only IBT growth while invalidating links to a replaced file.
    file_key = recording_key(telemetry)
    reviews, pending = [], []
    all_notes = store.list(max_notes)
    # Only notes from this car/track/layout can match; load sample trails for those alone.
    recents = store.recents(note['id'] for note in all_notes
                            if identity_compatible(note.get('context', {}).get('identity') or {}, identity))
    measurements = {}
    for note in all_notes:
        if note['id'] in recents:
            note = {**note, 'context': {**note['context'], 'recent': recents[note['id']]}}
        link = links.get(note['id'])
        if link and link['file_key'] != file_key:
            link = None
        match = matching_context(note, telemetry)
        if link and link['method'] == 'MANUAL':
            match = {**link, 'session_time': None}
        if match is None:
            if not note.get('context', {}).get('identity') or identity_compatible(note['context']['identity'], identity):
                pending.append(public_note(note))
            continue
        lap = next((lap for lap in telemetry.laps if lap.lap_number == match['lap_number']), None)
        if lap is None:
            pending.append(public_note(note))
            continue
        engineer = note.get('context', {}).get('engineer', 'coach')
        if note.get('source') == 'AUTO_REPORT' or engineer in ('race', 'strategy', 'spotter'):
            label = {'coach': '주행 코치', 'race': '레이스 엔지니어', 'strategy': '전략 엔지니어', 'spotter': '스포터'}.get(engineer, '엔지니어')
            if match['method']:
                store.link(note['id'], filename, file_key, match['method'], match['lap_number'], match.get('distance_pct'))
            reviews.append({**public_note(note), 'filename': filename, 'lap_number': match['lap_number'],
                'distance_pct': match.get('distance_pct'), 'corner_id': None, 'corner_label': label + ' 보고',
                'link_method': match['method'], 'anchor_source': 'AT_SPEECH', 'is_engineer_report': True,
                'report_context': note.get('context', {}).get('pit_wall'), 'setup_candidates': [],
                'setup_effect_confirmed': False, 'comparison_note': '당시 보고와 계산 근거를 저장한 기록입니다. 셋업 효과의 근거로 사용하지 않습니다.'})
            continue
        if lap.lap_number not in measurements:
            grid = analyzer._lap_grid(lap) if lap.valid else analyzer._coaching_grid(lap)
            definitions = analyzer._detect_corners(grid)
            # Measure the spoken-about lap, not whichever target happened to be selected.
            reference_lap = next((l for l in analyzer.valid_laps if l.lap_number == result.get('comparison', {}).get('reference_lap')), None) if result.get('comparison') else None
            reference_grid = analyzer._lap_grid(reference_lap) if reference_lap and reference_lap.lap_number != lap.lap_number else None
            corners = analyzer._corner_analysis(grid, reference_grid, definitions)
            comparison_note = '메모를 남긴 랩과 내 기준 랩 비교' if reference_grid else '메모를 남긴 랩의 관측 · 비교 기준 없음'
            if lap.valid and request.external_reference_files:
                external = analyzer._external_reference_analysis(lap, request.external_reference_files)
                if external.get('status') == 'READY' and external.get('corners'):
                    corners = external['corners']
                    comparison_note = '메모를 남긴 랩과 선택한 Garage61 기준 비교'
            measurements[lap.lap_number] = (corners, comparison_note)
        corners, comparison_note = measurements[lap.lap_number]
        corner, pct, origin = anchor_corner(note, match, corners)
        method = match['method']
        if method == 'MANUAL':
            origin = 'MANUAL'
        elif method:
            store.link(note['id'], filename, file_key, method, match['lap_number'], pct)
        evidence = list(corner.get('evidence') or []) if corner else []
        driving = corner.get('driver_action') if corner else None
        if not driving:
            driving = '메모 위치를 확인하고 같은 코너의 진입·브레이크 해제·가속 시점을 관찰하세요.'
        feeling_list = note['feelings']
        specific = [s for s in feeling_list if s in {'UNDERSTEER', 'OVERSTEER', 'BRAKING', 'KERB'}]
        hypothesis = []
        if corner and len(specific) == 1 and corner.get('measurement_usable'):
            phase = 'ENTRY' if re.search(r'진입|들어갈|제동', note['text']) else 'EXIT' if re.search(r'탈출|나갈|가속', note['text']) else 'MID_CORNER'
            feedback_request = request.model_copy(update={'feedback_phase': phase, 'feedback_speed': corner.get('speed_class') or 'MEDIUM',
                'feedback_symptom': specific[0], 'user_feedback': note['text'], 'setup_mode': 'EXPLORE', 'include_ai': False})
            feedback = analyzer._structured_feedback(feedback_request, [corner], {})
            candidate_result = analyzer._setup_recommendation(request.session_mode, result['performance']['classification'],
                [corner], result.get('vehicle') or {}, result['performance'].get('repetition') or {}, {},
                request=feedback_request, feedback=feedback)
            candidates = candidate_result.get('test_candidates') or candidate_result.get('experimental_candidates') or []
            hypothesis = [{**{k: item.get(k) for k in ('setting', 'label', 'original_value', 'value', 'unit', 'direction', 'step_text', 'reason')},
                           'confidence': min(number(item.get('confidence')) or 0, .4)}
                          for item in candidates[:2]]
        reviews.append({**public_note(note), 'filename': filename, 'lap_number': match['lap_number'],
            'distance_pct': pct, 'corner_id': corner.get('id') if corner else None,
            'corner_label': corner.get('label') if corner else '위치 확인 필요', 'link_method': method,
            'anchor_source': origin, 'evidence': evidence, 'driving_action': driving,
            'measurement_usable': bool(corner and corner.get('measurement_usable')),
            'setup_candidates': hypothesis, 'setup_effect_confirmed': False,
            'setup_note': '체감 기반 탐색 가설 · 실제 차고 값 확인 후 한 항목만 A/B 검증하세요.' if hypothesis
                else '셋업 수치 근거가 부족합니다. 느낌을 구체화하고 주행 조작부터 확인하세요.',
            'comparison_note': comparison_note})
    return {'notes': reviews, 'pending': pending[:60], 'linked_count': len(reviews),
            'pending_count': len(pending), 'storage': 'LOCAL_SQLITE', 'audio_stored': False,
            'note': '드라이버의 느낌과 측정 근거를 별도로 표시합니다. 실시간 답변은 주행 후 다시 검증합니다.'}
