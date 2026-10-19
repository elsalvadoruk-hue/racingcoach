"""Small, independently testable helpers for file discovery and partial coaching."""
import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

import numpy as np


class FileMetadataIndex:
    """Signature-keyed metadata, separate from personal learning records."""

    VERSION = 4  # 4: entries carry the session type and the suggested analysis mode.

    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            connection.execute("CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, size INTEGER, modified INTEGER, version INTEGER, payload TEXT)")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get(self, signature):
        with self._connect() as connection:
            row = connection.execute("SELECT payload FROM files WHERE path=? AND size=? AND modified=? AND version=?", (*signature, self.VERSION)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, signature, payload):
        with self._connect() as connection:
            connection.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?)", (*signature, self.VERSION, json.dumps(payload, ensure_ascii=False)))


class CornerDefinitionStore:
    """Keeps automatic corner segments stable across restarts.

    Segment ids are stored with lap history, so the first clean lap that
    defines a car/track/layout must keep defining it. Delete the file to
    re-detect. Unreadable or outdated files are ignored, never repaired.
    """

    VERSION = 1

    def __init__(self, path):
        self.path = Path(path)
        self._items = None
        self._tracks = {}
        self._lock = threading.RLock()

    def find(self, car_id, track_id, track_config):
        """Definitions for the car on track now; the live SDK has no file-style identity."""
        if not car_id or not track_id:
            return None
        with self._lock:
            for key, definitions in reversed(list(self._load().items())):
                identity = json.loads(key)
                if (str(identity[0]), str(identity[3]), str(identity[6] or '')) == (str(car_id), str(track_id), str(track_config or '')):
                    return definitions
        return None

    @staticmethod
    def _key(identity):
        return json.dumps(list(identity), ensure_ascii=False)

    def _load(self):
        if self._items is None:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self._items = dict(data["items"]) if data.get("version") == self.VERSION else {}
                self._tracks = dict(data.get("tracks") or {}) if data.get("version") == self.VERSION else {}
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                self._items, self._tracks = {}, {}
        return self._items

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"version": self.VERSION, "items": self._items, "tracks": self._tracks}, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.path)

    # Confirmed turn numbers and names belong to the layout, not to one car: they are
    # kept as positions on the lap and laid over whatever segments a car produced.
    @staticmethod
    def _track_key(track_id, track_config):
        return json.dumps([str(track_id or ""), str(track_config or "")], ensure_ascii=False)

    def track_names(self, track_id, track_config):
        with self._lock:
            self._load()
            return self._tracks.get(self._track_key(track_id, track_config)) if track_id else None

    def set_track_names(self, track_id, track_config, points):
        if not track_id:
            raise ValueError("트랙 ID가 없어 코너 번호를 저장할 수 없습니다.")
        with self._lock:
            self._load()
            self._tracks[self._track_key(track_id, track_config)] = {"confirmed": True, "points": points}
            self._save()

    def names_for(self, definitions, track_id, track_config):
        """{segment id: {number, name, spoken}} from the confirmed positions, or {} when nothing is confirmed."""
        saved = self.track_names(track_id, track_config)
        if not saved or not saved.get("confirmed"):
            return {}
        names = {}
        for point in saved.get("points") or []:
            inside = [d for d in definitions if d["entry_pct"] - .004 <= point["pct"] <= d["exit_pct"] + .004]
            if inside:
                segment = min(inside, key=lambda d: abs(d["apex_pct"] - point["pct"]))
                names.setdefault(segment["id"], {key: point.get(key) for key in ("number", "name", "spoken")})
        return names

    def get(self, identity):
        with self._lock:
            return self._load().get(self._key(identity))

    def put(self, identity, definitions):
        with self._lock:
            items = self._load()
            if self._key(identity) in items:
                return
            items[self._key(identity)] = definitions
            self._save()


def clean_segment_context(telemetry):
    """Retain measured safe windows without promoting an invalid lap to VALID.

    Unknown pit/on-track state fails closed. Incidents and recording gaps are
    buffered before selecting windows. Lap time is deliberately never inferred.
    """
    channels = getattr(telemetry, "channels", {})
    required = {"SessionTime", "LapDistPct", "Speed", "PlayerTrackSurface", "OnPitRoad"}
    empty = {"status": "UNAVAILABLE", "segments": [], "lap_windows": {}, "coaching_ready": False, "official_laps_added": 0}
    if not required.issubset(channels):
        return {**empty, "reason": "안전한 정상 구간을 확인할 채널이 부족합니다."}
    track_length = float((getattr(telemetry, "metadata", {}) or {}).get("track_length_m") or 1000)
    windows, segments = {}, []
    for lap in telemetry.laps:
        if lap.valid:
            continue
        start, stop = lap.start_idx, lap.end_idx + 1
        time = np.asarray(channels["SessionTime"][start:stop], dtype=float)
        distance = np.asarray(channels["LapDistPct"][start:stop], dtype=float)
        speed = np.asarray(channels["Speed"][start:stop], dtype=float)
        on_track = np.asarray(channels["PlayerTrackSurface"][start:stop], dtype=float)
        pit = np.asarray(channels["OnPitRoad"][start:stop], dtype=float)
        safe = np.isfinite(time) & np.isfinite(distance) & np.isfinite(speed) & np.isfinite(on_track) & np.isfinite(pit)
        safe &= (on_track == 3) & (pit == 0) & (speed >= 4) & (distance >= 0) & (distance <= 1)
        connected = channels.get("IsOnTrack")
        if connected is not None:
            safe &= np.asarray(connected[start:stop], dtype=float) > 0
        incidents = channels.get("PlayerIncidents")
        if incidents is not None:
            counts = np.asarray(incidents[start:stop], dtype=float)
            safe[1:][np.diff(counts) > 0] = False
        if len(time) < 2:
            continue
        jumps = (np.diff(time) <= 0) | (np.diff(time) > .5) | (np.diff(distance) < -1e-5) | (np.diff(distance) * track_length > 80)
        safe[1:][jumps] = False
        safe[:-1][jumps] = False
        yaw = channels.get("YawRate")
        if yaw is not None:
            values = np.asarray(yaw[start:stop], dtype=float)
            safe &= np.isfinite(values) & (np.abs(values) <= 3)
        # Only use a monotonic recording clock for time-based incident padding.
        if not np.all(np.isfinite(time)) or np.any(np.diff(time) <= 0):
            continue
        incident = np.flatnonzero(~safe)
        if len(incident):
            changes = np.zeros(len(safe) + 1, dtype=int)
            left = np.searchsorted(time, time[incident] - 1.5, side="left")
            right = np.searchsorted(time, time[incident] + 1.5, side="right")
            np.add.at(changes, left, 1)
            np.add.at(changes, right, -1)
            safe &= np.cumsum(changes[:-1]) == 0
        edges = np.diff(np.r_[False, safe, False].astype(int))
        for left, right in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)):
            last = right - 1
            length = (distance[last] - distance[left]) * track_length
            duration = time[last] - time[left]
            if duration < 4 or length < 100:
                continue
            item = {"lap_number": lap.lap_number, "original_status": lap.status,
                    "start_idx": int(start + left), "end_idx": int(start + last),
                    "start_pct": float(distance[left]), "end_pct": float(distance[last]),
                    "distance_m": round(float(length), 1), "duration_s": round(float(duration), 2),
                    "scope": "COACHING_ONLY", "official_valid_lap": False}
            segments.append(item)
            windows.setdefault(lap.lap_number, []).append(item)
    return {"status": "AVAILABLE" if segments else "UNAVAILABLE", "segments": segments,
            "lap_windows": windows, "coaching_ready": bool(segments), "official_laps_added": 0,
            "reason": "정상 구간은 코칭과 탐색에만 사용하며 공식 랩 페이스와 A/B 표본에는 포함하지 않습니다."}


def mask_grid_to_clean_windows(grid, windows):
    output = dict(grid)
    distance = grid["distance_pct"]
    mask = np.zeros(len(distance), dtype=bool)
    for window in windows:
        mask |= (distance >= window["start_pct"]) & (distance <= window["end_pct"])
    for key, value in grid.items():
        if isinstance(value, np.ndarray) and key != "distance_pct" and not key.startswith("_"):
            output[key] = np.where(mask, value, np.nan)
    output["_coverage"] = mask & np.asarray(grid.get("_coverage", np.ones(len(mask), dtype=bool)))
    output["_channel_support"] = {key: np.isfinite(value) for key, value in output.items()
                                  if isinstance(value, np.ndarray) and key != "distance_pct" and not key.startswith("_")}
    return output


ENDURANCE_RACE_S = 3600   # a race of an hour or more is analysed as endurance (the driver's rule)


def session_context(session_info):
    """Which session of the event this file was recorded in, with its length.

    The header lists every session of the event as it stood when recording began:
    sessions already run are marked official, so the first one that is not is the
    session being driven. Nothing is returned for a header without that list.
    """
    sessions = ((session_info or {}).get('SessionInfo') or {}).get('Sessions') if isinstance(session_info, dict) else None
    sessions = [s for s in sessions or [] if isinstance(s, dict)]
    if not sessions:
        return {'session_type': None, 'session_duration_s': None, 'session_laps_limit': None}
    current = next((s for s in sessions if not _number(s.get('ResultsOfficial'))), sessions[-1])

    def limit(value):
        number = _number(str(value).split()[0]) if value is not None and str(value).strip() else None
        return number if number is not None and 0 < number < 7 * 86400 else None
    return {'session_type': str(current.get('SessionType') or '').strip() or None,
            'session_duration_s': limit(current.get('SessionTime')), 'session_laps_limit': limit(current.get('SessionLaps'))}


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def suggested_session_mode(session_type, duration_s=None, laps_limit=None, lap_time_s=None):
    """The analysis mode a file starts in. The driver can still change it; a stored choice always wins.

    Qualifying -> QUALIFYING. A race of an hour or more -> ENDURANCE, a shorter one and
    every practice or test -> BASELINE_3LAP. A lap-limited race is timed by the car's
    estimated lap. An unknown session type suggests nothing.
    """
    kind = str(session_type or '').lower()
    if not kind:
        return None
    if 'qual' in kind:
        return 'QUALIFYING'
    if 'race' in kind:
        if not duration_s or duration_s >= 86400:
            duration_s = laps_limit * lap_time_s if laps_limit and lap_time_s else None
        return 'ENDURANCE' if duration_s and duration_s >= ENDURANCE_RACE_S else 'BASELINE_3LAP'
    return 'BASELINE_3LAP'
