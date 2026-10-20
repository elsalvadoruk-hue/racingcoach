"""Independent, read-only native LMU telemetry inspection.

No iRacing imports, settings, caches, databases or SDK lifecycle are shared.
Bounded laps are diagnostics, not claims about the game's lap validity.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import math
import os
from pathlib import Path
from typing import Any, Iterable, Optional

import numpy as np


class LmuTelemetryUnavailable(RuntimeError):
    """The optional LMU dependency is unavailable."""


class LmuTelemetryFormatError(ValueError):
    """The file does not have a readable native LMU schema."""


@dataclass(frozen=True)
class LmuChannelSpec:
    name: str
    frequency_hz: float
    unit: str


@dataclass(frozen=True)
class LmuLapSegment:
    lap_number: int
    start_time_s: float
    end_time_s: float
    start_index: int
    end_index: int  # exclusive, in the Lap Dist channel
    distance_m: Optional[float]
    lap_time_s: float
    recorded_lap_time_s: Optional[float]
    valid: Optional[bool]
    status: str


@dataclass(frozen=True)
class LmuTelemetrySummary:
    path: Path
    metadata: dict[str, Any]
    channels: tuple[LmuChannelSpec, ...]
    events: tuple[str, ...]
    sample_counts: dict[str, int]
    laps: tuple[LmuLapSegment, ...]


def require_lmu_dependency():
    try:
        import duckdb
    except ImportError as exc:
        raise LmuTelemetryUnavailable(
            "LMU 파일을 읽으려면 requirements_lmu.txt의 패키지를 설치해야 합니다."
        ) from exc
    return duckdb


def _quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


class LmuTelemetryReader:
    """Read only tables, with external access and extension loading disabled."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        if self.path.suffix.casefold() != ".duckdb":
            raise LmuTelemetryFormatError("LMU 파일은 .duckdb 확장자여야 합니다.")
        if not self.path.is_file():
            raise FileNotFoundError(self.path)

    @contextmanager
    def _connection(self):
        duckdb = require_lmu_dependency()
        connection = None
        try:
            connection = duckdb.connect(str(self.path), read_only=True, config={
                "enable_external_access": "false",
                "autoinstall_known_extensions": "false",
                "autoload_known_extensions": "false",
            })
            connection.execute("BEGIN TRANSACTION")
            yield connection
        except duckdb.Error as exc:
            raise LmuTelemetryFormatError(
                "LMU 데이터베이스를 읽을 수 없습니다. 기록을 종료한 파일인지 확인하세요."
            ) from exc
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _tables(connection) -> dict[str, tuple[str, ...]]:
        rows = connection.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_type = 'BASE TABLE' AND table_schema = 'main'"
        ).fetchall()
        tables = {}
        for (name,) in rows:
            columns = connection.execute(f"PRAGMA table_info({_quote(name)})").fetchall()
            tables[name] = tuple(str(row[1]) for row in columns)
        for required, fields in (("metadata", {"key", "value"}),
                                 ("channelslist", {"channelname", "frequency", "unit"})):
            table = next((name for name in tables if name.casefold() == required), None)
            if table is None or not fields.issubset({col.casefold() for col in tables[table]}):
                raise LmuTelemetryFormatError(f"LMU 필수 테이블 또는 열이 없습니다: {required}")
        return tables

    @staticmethod
    def _table(tables, name: str) -> Optional[str]:
        return next((key for key in tables if key.casefold() == name.casefold()), None)

    def _metadata(self, connection, tables) -> dict[str, Any]:
        table = self._table(tables, "metadata")
        rows = connection.execute(f"SELECT key, value FROM {_quote(table)}").fetchall()
        # Do not send SteamID, CarSetup or arbitrary metadata to the browser.
        aliases = {"TrackName": "track_name", "TrackLayout": "track_layout",
                   "CarName": "car_name", "CarClass": "car_class",
                   "DriverName": "driver_name", "SessionType": "session_type",
                   "RecordingTime": "recording_time", "Version": "version"}
        return {aliases[key]: str(value or "") for key, value in rows if key in aliases}

    def _specs(self, connection, tables) -> tuple[LmuChannelSpec, ...]:
        table = self._table(tables, "channelsList")
        rows = connection.execute(
            f"SELECT channelName, frequency, unit FROM {_quote(table)} ORDER BY rowid"
        ).fetchall()
        specs = []
        seen = set()
        for name, frequency, unit in rows:
            hz = _number(frequency)
            if not name or not math.isfinite(hz) or hz <= 0 or str(name).casefold() in seen:
                raise LmuTelemetryFormatError("LMU 채널 이름 또는 샘플링 주파수가 올바르지 않습니다.")
            seen.add(str(name).casefold())
            specs.append(LmuChannelSpec(str(name), hz, str(unit or "")))
        return tuple(specs)

    def _channel(self, connection, tables, name: str) -> np.ndarray:
        table = self._table(tables, name)
        if table is None:
            raise KeyError(name)
        columns = {col.casefold() for col in tables[table]}
        if "value" not in columns or "ts" in columns:
            raise LmuTelemetryFormatError(f"일반 채널 형식이 아닙니다: {name}")
        rows = connection.execute(f"SELECT value FROM {_quote(table)} ORDER BY rowid").fetchall()
        return np.asarray([_number(row[0]) for row in rows], dtype=float)

    def _events(self, connection, tables, name: str):
        table = self._table(tables, name)
        if table is None:
            return None
        if not {"ts", "value"}.issubset({col.casefold() for col in tables[table]}):
            raise LmuTelemetryFormatError(f"이벤트 형식이 아닙니다: {name}")
        rows = connection.execute(
            f"SELECT ts, value FROM {_quote(table)} ORDER BY ts, rowid"
        ).fetchall()
        timestamps = np.asarray([_number(row[0]) for row in rows], dtype=float)
        values = np.asarray([_number(row[1]) for row in rows], dtype=float)
        if not np.all(np.isfinite(timestamps)) or np.any(np.diff(timestamps) <= 0):
            raise LmuTelemetryFormatError(f"이벤트 시각이 중복되거나 올바르지 않습니다: {name}")
        return timestamps, values

    def metadata(self) -> dict[str, Any]:
        with self._connection() as connection:
            return self._metadata(connection, self._tables(connection))

    def channel_specs(self) -> tuple[LmuChannelSpec, ...]:
        with self._connection() as connection:
            return self._specs(connection, self._tables(connection))

    def read_channel(self, name: str) -> np.ndarray:
        with self._connection() as connection:
            tables = self._tables(connection)
            if name.casefold() not in {spec.name.casefold() for spec in self._specs(connection, tables)}:
                raise KeyError(name)
            return self._channel(connection, tables, name)

    def read_events(self, name: str):
        with self._connection() as connection:
            return self._events(connection, self._tables(connection), name)

    @staticmethod
    def _laps(lap_events, lap_times, pits, distance, axis) -> tuple[LmuLapSegment, ...]:
        if lap_events is None:
            return ()
        timestamps, numbers = lap_events
        segments = []
        # Only game Lap events delimit laps. Never infer boundaries from distance
        # resets or assign Lap Time values by array position.
        for index in range(len(timestamps) - 1):
            start, end = float(timestamps[index]), float(timestamps[index + 1])
            number, next_number = numbers[index:index + 2]
            if not (math.isfinite(number) and number.is_integer() and
                    math.isfinite(next_number) and next_number == number + 1):
                continue
            duration = end - start
            recorded = None
            if lap_times is not None:
                ts, values = lap_times
                matches = values[np.abs(ts - end) <= 0.05]
                if len(matches) == 1 and math.isfinite(matches[0]) and matches[0] > 0:
                    recorded = float(matches[0])
            status = "VALIDITY_UNKNOWN" if recorded is not None else "UNTIMED"
            if recorded is not None and abs(recorded - duration) > 0.05:
                status = "TIMING_MISMATCH"
            if pits is not None:
                ts, values = pits
                previous = np.flatnonzero(ts <= start)
                states = values[(ts >= start) & (ts < end)]
                if (len(previous) and values[previous[-1]] > 0) or np.any(states > 0):
                    status = "PIT_LAP"
            first, last, travelled = 0, 0, None
            if axis is not None:
                first, last = (int(np.searchsorted(axis, stamp)) for stamp in (start, end))
                sample = distance[first:last]
                if start < axis[0] - 1e-6 or end > axis[-1] + (axis[1] - axis[0]) + 1e-6:
                    status = "INCOMPLETE"
                elif len(sample) < 2 or not np.all(np.isfinite(sample)):
                    status = "INCOMPLETE"
                else:
                    travelled = float(np.max(sample) - np.min(sample))
                    # Allow a boundary reset within the first sample interval.
                    if np.any(np.diff(sample[1:]) < -50):
                        status = "DISTANCE_DISCONTINUITY"
            segments.append(LmuLapSegment(int(number), start, end, first, last,
                                          travelled, duration, recorded, None, status))
        return tuple(segments)

    def summary(self) -> LmuTelemetrySummary:
        with self._connection() as connection:
            tables = self._tables(connection)
            specs = self._specs(connection, tables)
            metadata = self._metadata(connection, tables)
            counts = {name: int(connection.execute(
                f"SELECT COUNT(*) FROM {_quote(name)}").fetchone()[0])
                for name in tables if name.casefold() not in {"metadata", "channelslist", "eventslist"}}
            events = tuple(name for name, cols in tables.items()
                           if {"ts", "value"}.issubset({col.casefold() for col in cols}))
            distance, axis = np.asarray([], dtype=float), None
            by_name = {spec.name.casefold(): spec for spec in specs}
            dist_spec, gps_spec = by_name.get("lap dist"), by_name.get("gps time")
            if dist_spec and gps_spec and dist_spec.unit == "m" and gps_spec.unit == "s":
                distance = self._channel(connection, tables, dist_spec.name)
                gps = self._channel(connection, tables, gps_spec.name)
                if len(gps) and math.isfinite(gps[0]) and len(distance) >= 2:
                    axis = gps[0] + np.arange(len(distance)) / dist_spec.frequency_hz
            laps = self._laps(self._events(connection, tables, "Lap"),
                              self._events(connection, tables, "Lap Time"),
                              self._events(connection, tables, "In Pits"), distance, axis)
            return LmuTelemetrySummary(self.path, metadata, specs, events, counts, laps)


def discover_lmu_telemetry_dirs() -> tuple[Path, ...]:
    configured = os.getenv("LMU_TELEMETRY_DIRS", "").strip()
    if configured:
        candidates = [Path(item.strip()).expanduser() for item in configured.split(os.pathsep) if item.strip()]
    else:
        candidates = [Path(root) / "Steam/steamapps/common/Le Mans Ultimate/UserData/Telemetry"
                      for root in (os.getenv("ProgramFiles(x86)"), os.getenv("ProgramFiles")) if root]
    unique = {str(path.resolve()).casefold(): path.resolve() for path in candidates}
    return tuple(unique.values())


def list_lmu_telemetry_files(directories: Optional[Iterable[str | Path]] = None) -> list[Path]:
    paths = directories if directories is not None else discover_lmu_telemetry_dirs()
    files = {}
    for directory in paths:
        folder = Path(directory).expanduser()
        if folder.is_dir():
            for path in folder.glob("*.duckdb"):
                if path.is_file():
                    files[str(path.resolve()).casefold()] = path.resolve()
    return sorted(files.values(), key=lambda path: (-path.stat().st_mtime_ns, str(path)))
