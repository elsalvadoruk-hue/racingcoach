"""LMU's own verdict on each lap, kept from the live connection for the recording analysis.

LMU voids a lap for track limits (``mLapInvalidated``) but the DuckDB recording does
not keep that, so the analysis had to judge laps from the track edge itself. Seen
live, each finished lap is stored with the lap time LMU published; a recording later
takes the verdict only for a lap whose number and lap time both match, so a
mismatched session or lap numbering never carries a wrong verdict.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import time

KEEP_SESSIONS = 100
SETTLE_S = 1.          # the flag of the lap just finished may linger this long into the next one
TIME_WAIT_S = 6.       # the lap time is published a moment after the line
MATCH_S = .02          # a recorded lap takes the verdict only with the same lap time
WINDOW_S = 300.        # a recording and a live session belong together within this much of each other
_LOCK = threading.Lock()


def store_path():
    configured = os.getenv('IRACING_LMU_LAP_VERDICTS_PATH', '').strip()
    if configured:
        return Path(configured)
    from app_settings import data_dir
    return Path(data_dir()) / 'lmu-lap-verdicts.json'


def _load(path):
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if isinstance(data, dict) and isinstance(data.get('sessions'), list):
            return data
    except (OSError, ValueError):
        pass
    return {'version': 1, 'sessions': []}


def _number(value):
    return value if type(value) in (int, float) and value == value else None


class LapVerdicts:
    """Fed every live LMU frame; writes one row per finished lap."""

    def __init__(self, path=None, clock=time.time):
        self.path = path
        self.clock = clock
        self.session = None      # the session being driven, as stored
        self.lap = None          # the lap in progress
        self.pending = None      # a finished lap waiting for its published time

    def _store(self):
        return Path(self.path) if self.path else store_path()

    def observe(self, s):
        key, completed, et = s.get('session_key'), _number(s.get('total_laps')), _number(s.get('telemetry_et'))
        if not key or completed is None or et is None or s.get('car_name') is None:
            return
        now = self.clock()
        if self.session is None or self.session['session_key'] != key or self.session['car'] != s.get('car_name'):
            self._flush()
            self.session = {'session_key': key, 'track': s.get('track_name'), 'car': s.get('car_name'),
                            'session_type_code': s.get('session_type_code'), 'first_utc': now, 'last_utc': now, 'laps': {}}
            self.lap = self.pending = None
        self.session['last_utc'] = now
        completed = int(completed)
        if self.lap is None or completed != self.lap['completed']:
            if self.lap is not None and completed == self.lap['completed'] + 1:
                self._flush()
                self.pending = {'lap': completed, 'invalid': self.lap['invalid'], 'at': et,
                                'previous_time': self.lap['last_time'], 'count_flag': s.get('count_lap_flag')}
            self.lap = {'completed': completed, 'start_et': et, 'invalid': False, 'last_time': _number(s.get('last_lap_time'))}
        if s.get('lap_invalidated') is True and et - self.lap['start_et'] > SETTLE_S:
            self.lap['invalid'] = True
        if self.pending:
            last = _number(s.get('last_lap_time'))
            if last and last > 0 and last != self.pending['previous_time']:
                self.pending['time'] = round(last, 3)
                self._flush()
            elif et - self.pending['at'] > TIME_WAIT_S:
                # By now the time is published even when it equals the lap before.
                if last and last > 0:
                    self.pending['time'] = round(last, 3)
                self._flush()

    def _flush(self):
        pending, self.pending = self.pending, None
        if not pending or not self.session:
            return
        self.session['laps'][str(pending['lap'])] = {'invalid': pending['invalid'], 'time': pending.get('time')}
        with _LOCK:
            path = self._store()
            data = _load(path)
            sessions = [x for x in data['sessions'] if not (x.get('session_key') == self.session['session_key']
                                                            and x.get('car') == self.session['car'])]
            sessions.append({**self.session, 'laps': dict(self.session['laps'])})
            data['sessions'] = sessions[-KEEP_SESSIONS:]
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_name(path.name + '.tmp')
                temporary.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
                os.replace(temporary, path)
            except OSError:
                pass


def _recording_start(text):
    try:
        return datetime.strptime(str(text), '%Y-%m-%dT%H_%M_%SZ').replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def verdicts_for(track, car, recording_time, ended_at, path=None):
    """{lap number: (invalid, lap time)} from the live session that matches this recording."""
    start = _recording_start(recording_time)
    if not track or not car or start is None or ended_at is None:
        return {}
    with _LOCK:
        data = _load(Path(path) if path else store_path())
    best, overlap = None, 0.
    for session in data['sessions']:
        if session.get('track') != track or session.get('car') != car:
            continue
        shared = min(ended_at, session['last_utc'] + WINDOW_S) - max(start, session['first_utc'] - WINDOW_S)
        if shared > overlap:
            best, overlap = session, shared
    if not best:
        return {}
    return {int(lap): (bool(v.get('invalid')), v.get('time')) for lap, v in best['laps'].items()
            if isinstance(v, dict) and _number(v.get('time'))}


def game_verdict(verdicts, lap_number, lap_time):
    """True (LMU voided it), False (LMU kept it) or None (no matching live lap)."""
    # Recordings may number a run's first lap 0 where the live count says 1: the lap time decides.
    if not verdicts or not lap_time:
        return None
    for number in (int(lap_number), int(lap_number) + 1, int(lap_number) - 1):
        found = verdicts.get(number)
        if found and abs(found[1] - lap_time) <= MATCH_S:
            return found[0]
    return None
