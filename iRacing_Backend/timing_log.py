"""Raw race timing, recorded to learn how iRacing's CarIdxF2Time behaves before any gap call uses it.

Once a second during a race session every car's lap, lap position, F2Time, EstTime, pit road flag and
positions are appended to a gzip'd JSON-lines file in the data folder (timing-logs/). Nothing is said
from it; scripts/read_timing_log.py compares the F2Time gaps with the app's own estimates afterwards.
No driver names are written: cars are their SDK index and class only. The files stay on this computer
(not in the session log export, the share copy or a patch).
"""
from __future__ import annotations

import gzip
import json
import math
import re
import time
from pathlib import Path
from typing import Any, Optional

FORMAT = 'iracing-coach-timing'
VERSION = 1
FIELDS = ['idx', 'class_id', 'lap_completed', 'distance_pct', 'f2_time_s', 'est_time_s', 'on_pit_road',
          'position', 'class_position', 'last_lap_s', 'track_surface']
ROUND = {'distance_pct': 5, 'f2_time_s': 3, 'est_time_s': 3, 'last_lap_s': 3, 'time_remaining_s': 1}
FLUSH_ROWS = 30          # one write every ~30 s of race
KEEP_FILES = 30


def _value(key, value):
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)) and math.isfinite(value):
        return round(value, ROUND[key]) if key in ROUND else value
    return None


class TimingLog:
    def __init__(self, folder: Optional[Path | str] = None):
        self.folder = Path(folder) if folder else None   # None: nothing is written (tests, LiveTelemetry alone)
        self.run_id: Optional[str] = None
        self.path: Optional[Path] = None
        self.rows: list[str] = []
        self.last_time: Optional[float] = None

    def record(self, race: dict[str, Any], run_id: str, session_time: float) -> None:
        if self.folder is None or not race or str(race.get('session_type') or '').lower() != 'race':
            return
        if run_id != self.run_id:
            self.flush()
            self.run_id, self.path, self.last_time = run_id, None, None
        if self.last_time is not None and session_time <= self.last_time:
            return
        self.last_time = session_time
        if self.path is None:
            self.path = self._new_file(race)
            self.rows.append(json.dumps(self._header(race, run_id), ensure_ascii=False))
        cars = [[_value(key, car.get(key)) for key in FIELDS] for car in race.get('cars') or []]
        self.rows.append(json.dumps({'t': round(session_time, 3), 'state': race.get('state'), 'flags': race.get('flags'),
                                     'own': race.get('player_idx'), 'remain_s': _value('time_remaining_s', race.get('time_remaining_s')),
                                     'c': cars}, separators=(',', ':')))
        if len(self.rows) >= FLUSH_ROWS:
            self.flush()

    def flush(self) -> None:
        if not self.rows or self.path is None:
            self.rows = []
            return
        rows, self.rows = self.rows, []
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(self.path, 'at', encoding='utf-8') as handle:   # one gzip member per flush, read back as one stream
            handle.write('\n'.join(rows) + '\n')

    def _header(self, race, run_id):
        classes = {}
        for car in race.get('cars') or []:
            if car.get('class_id') is not None:
                classes[str(car['class_id'])] = {'name': car.get('class_name'), 'est_lap_s': car.get('class_est_lap_s')}
        return {'format': FORMAT, 'version': VERSION, 'run_id': run_id, 'track': race.get('track_name'),
                'track_length_m': race.get('track_length_m'), 'session_duration_s': race.get('session_duration_s'),
                'total_laps': race.get('total_laps'), 'classes': classes, 'fields': FIELDS, 'written': time.strftime('%Y-%m-%d %H:%M:%S')}

    def _new_file(self, race):
        self.folder.mkdir(parents=True, exist_ok=True)
        files = sorted(self.folder.glob('timing-*.jsonl.gz'), key=lambda p: p.stat().st_mtime)
        for old in files[:max(0, len(files) - KEEP_FILES + 1)]:
            old.unlink(missing_ok=True)
        track = re.sub(r'[^\w-]+', '-', str(race.get('track_name') or 'track')).strip('-')[:40] or 'track'
        return self.folder / f"timing-{time.strftime('%Y%m%d-%H%M%S')}-{track}.jsonl.gz"


def read(path: Path | str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Header and rows of one timing file (rows' cars as dicts)."""
    header, rows = {}, []
    with gzip.open(path, 'rt', encoding='utf-8') as handle:
        for line in handle:
            if not line.strip():
                continue
            item = json.loads(line)
            if item.get('format') == FORMAT:
                header = item
                continue
            fields = header.get('fields') or FIELDS
            item['c'] = [dict(zip(fields, car)) for car in item.get('c') or []]
            rows.append(item)
    return header, rows
