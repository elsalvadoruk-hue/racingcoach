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
    timing_provenance: Optional[dict[str, Any]] = None


@dataclass(frozen=True)
class LmuTelemetrySummary:
    path: Path
    metadata: dict[str, Any]
    channels: tuple[LmuChannelSpec, ...]
    events: tuple[str, ...]
    sample_counts: dict[str, int]
    laps: tuple[LmuLapSegment, ...]


@dataclass(frozen=True)
class LmuDistanceSector:
    sector_number: int
    distance_start_m: float
    distance_end_m: float
    reference_duration_s: float
    target_duration_s: float
    delta_time_s: float
    cumulative_delta_start_s: float
    cumulative_delta_end_s: float
    channels: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class LmuLapComparison:
    reference_lap: int
    target_lap: int
    reference_time_s: float
    target_time_s: float
    delta_time_s: float
    sample_count: int
    distance_start_m: float
    distance_end_m: float
    channels: dict[str, dict[str, Any]]
    points: tuple[dict[str, float], ...]
    status: str
    sector_length_m: int
    sectors: tuple[LmuDistanceSector, ...]
    common_delta_time_s: float


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
            raise LmuTelemetryFormatError(f"채널 테이블이 없습니다: {name}")
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
    def _laps(lap_events, lap_times, pits, distance, axis, *,
              session_type=None, sectors=None) -> tuple[LmuLapSegment, ...]:
        if lap_events is None:
            return ()
        timestamps, numbers = lap_events
        segments = []
        race_offset = 0
        # Lap events delimit ordinary laps. The initial Race counter can include
        # formation: only the game's recorded time plus two independent native
        # observations may recover its timed start. Distance resets alone never
        # create a lap or establish game validity.
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
            provenance = None
            if recorded is not None and abs(recorded - duration) > 0.05:
                status = "TIMING_MISMATCH"
                if (index == 0 and number == 0 and
                        str(session_type or '').strip().casefold() == 'race' and
                        recorded < duration and sectors is not None and axis is not None):
                    inferred = end-recorded
                    sector_times, sector_values = sectors
                    starts = sector_times[1:][(sector_values[:-1] == 0) &
                        (sector_values[1:] == 1) & (np.abs(sector_times[1:]-inferred) <= .05)]
                    reset_indices = np.flatnonzero((np.diff(distance) < -50) &
                        np.isfinite(distance[:-1]) & np.isfinite(distance[1:]) &
                        (distance[1:] >= 0) & (distance[1:] <= 50) &
                        (axis[:-1] <= inferred+.05) & (axis[1:] >= inferred-.05))
                    if start < inferred < end and len(starts) == 1 and len(reset_indices) == 1:
                        reset = int(reset_indices[0])
                        provenance = {'method': 'RACE_INITIAL_RECORDED_TIME_CORROBORATED_V1',
                            'source_lap_counter': int(number), 'original_start_time_s': start,
                            'excluded_leading_s': inferred-start, 'inferred_start_time_s': inferred,
                            'sector_start_time_s': float(starts[0]),
                            'distance_reset_before_time_s': float(axis[reset]),
                            'distance_reset_after_time_s': float(axis[reset+1])}
                        start, duration, status = inferred, recorded, 'VALIDITY_UNKNOWN'
                        race_offset = 1
            if race_offset and provenance is None:
                provenance = {'method': 'RACE_LAP_COUNTER_OFFSET_V1',
                              'source_lap_counter': int(number)}
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
            segments.append(LmuLapSegment(int(number)+race_offset, start, end, first, last,
                                          travelled, duration, recorded, None, status, provenance))
        return tuple(segments)

    def summary(self) -> LmuTelemetrySummary:
        with self._connection() as connection:
            return self._summary(connection, self._tables(connection))

    def _summary(self, connection, tables) -> LmuTelemetrySummary:
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
                          self._events(connection, tables, "In Pits"), distance, axis,
                          session_type=metadata.get('session_type'),
                          sectors=(self._events(connection, tables, "Current Sector")
                                   if metadata.get('session_type', '').strip().casefold() == 'race'
                                   else None))
        return LmuTelemetrySummary(self.path, metadata, specs, events, counts, laps)

    @staticmethod
    def _lap_axis(lap, distance, times):
        mask = (times >= lap.start_time_s) & (times < lap.end_time_s)
        t, d = times[mask], distance[mask]
        # A delayed reset in the first interval may still contain the previous
        # lap's final sample. Never interpolate through that reset.
        if len(d) > 1 and d[1] - d[0] < -50:
            t, d = t[1:], d[1:]
        if (len(d) < 2 or not np.all(np.isfinite(d)) or
                np.any(np.diff(d) < 0) or d[-1] <= d[0]):
            raise LmuTelemetryFormatError("랩 거리 축이 단조 증가하지 않아 비교할 수 없습니다.")
        # At a stop, use the last timestamp at that distance, explicitly.
        keep = np.r_[np.diff(d) > 0, True]
        return t[keep], d[keep]

    @staticmethod
    def _distance_stats(distance, values, start, end, scale):
        # Integrate the original piecewise-linear trace, independent of the
        # display grid. Boundary values are interpolated without extrapolation.
        knots = np.r_[start, distance[(distance > start) & (distance < end)], end]
        samples = np.interp(knots, distance, values) * scale
        integral = np.sum((samples[1:] + samples[:-1]) * np.diff(knots) / 2)
        return {"entry": float(samples[0]), "exit": float(samples[-1]),
                "minimum": float(np.min(samples)), "maximum": float(np.max(samples)),
                "distance_mean": float(integral / (end - start))}

    def analysis_lap(self, lap_number: int):
        """One native snapshot adapted to game-neutral per-channel time axes."""
        from lmu_analysis import SOURCE_CHANNELS, normalize_lap

        if type(lap_number) is not int or lap_number < 0:
            raise ValueError("랩 번호는 0 이상의 정수여야 합니다.")
        with self._connection() as connection:
            tables = self._tables(connection)
            summary = self._summary(connection, tables)
            matches = [lap for lap in summary.laps if lap.lap_number == lap_number]
            if len(matches) != 1:
                raise LmuTelemetryFormatError("분석할 랩을 하나로 확인할 수 없습니다.")
            lap = matches[0]
            if lap.status != "VALIDITY_UNKNOWN" or lap.distance_m is None:
                raise LmuTelemetryFormatError("시간과 거리 진단을 통과한 기록 랩만 분석할 수 있습니다.")
            specs = {spec.name.casefold(): spec for spec in summary.channels}
            gps_spec, distance_spec = specs["gps time"], specs["lap dist"]
            gps = self._channel(connection, tables, gps_spec.name)
            if (len(gps) < 2 or not np.all(np.isfinite(gps)) or np.any(np.diff(gps) <= 0) or
                    not np.allclose(gps, gps[0] + np.arange(len(gps)) / gps_spec.frequency_hz,
                                    rtol=0, atol=1e-5)):
                raise LmuTelemetryFormatError("GPS Time이 채널 주파수와 일치하는 연속 시간 축이 아닙니다.")
            distance = self._channel(connection, tables, distance_spec.name)
            times = gps[0] + np.arange(len(distance)) / distance_spec.frequency_hz
            if times[-1] > gps[-1] + 1 / distance_spec.frequency_hz:
                raise LmuTelemetryFormatError("거리 채널이 GPS Time 기록 범위를 벗어납니다.")
            times, distance = self._lap_axis(lap, distance, times)
            required = {name.casefold() for name in SOURCE_CHANNELS}
            values = {spec.name: self._channel(connection, tables, spec.name)
                      for spec in summary.channels if spec.name.casefold() in required}
            from lmu_control_channels import with_gear_events
            normalized = normalize_lap(summary.metadata, lap, specs, values, gps[0], times, distance)
            return with_gear_events(normalized, self._events(connection, tables, 'Gear'), gps)

    def compare_laps(self, reference_lap: int, target_lap: int,
                     points: int = 101, sector_length_m: int = 500) -> LmuLapComparison:
        if (type(reference_lap) is not int or type(target_lap) is not int or
                type(points) is not int or reference_lap == target_lap or not 2 <= points <= 501):
            raise ValueError("서로 다른 랩 번호와 2~501 범위의 비교 지점이 필요합니다.")
        if type(sector_length_m) is not int or not 100 <= sector_length_m <= 2000:
            raise ValueError("거리 구간 길이는 100~2000 m 범위의 정수여야 합니다.")
        # One read-only snapshot; each selected channel is read once, not once
        # per output point. No reference is chosen automatically.
        with self._connection() as connection:
            tables = self._tables(connection)
            summary = self._summary(connection, tables)
            by_number = {lap.lap_number: lap for lap in summary.laps}
            if len(by_number) != len(summary.laps):
                raise LmuTelemetryFormatError("랩 번호가 중복되어 비교 대상을 구분할 수 없습니다.")
            if reference_lap not in by_number or target_lap not in by_number:
                raise LmuTelemetryFormatError("비교할 랩을 찾을 수 없습니다.")
            reference, target = by_number[reference_lap], by_number[target_lap]
            if any(lap.status != "VALIDITY_UNKNOWN" or lap.distance_m is None
                   for lap in (reference, target)):
                raise LmuTelemetryFormatError("시간과 거리 진단을 통과한 기록 랩만 수동 비교할 수 있습니다. 피트·미완성 랩은 제외합니다.")
            specs = {spec.name.casefold(): spec for spec in summary.channels}
            gps_spec, dist_spec = specs["gps time"], specs["lap dist"]
            gps = self._channel(connection, tables, gps_spec.name)
            distance = self._channel(connection, tables, dist_spec.name)
            expected = gps[0] + np.arange(len(gps)) / gps_spec.frequency_hz
            if (not np.all(np.isfinite(gps)) or np.any(np.diff(gps) <= 0) or
                    not np.allclose(gps, expected, rtol=0, atol=1e-5)):
                raise LmuTelemetryFormatError("GPS Time이 채널 주파수와 일치하는 연속 시간 축이 아닙니다.")
            distance_axis = gps[0] + np.arange(len(distance)) / dist_spec.frequency_hz
            if distance_axis[-1] > gps[-1] + 1 / dist_spec.frequency_hz:
                raise LmuTelemetryFormatError("거리 채널이 GPS Time 기록 범위를 벗어납니다.")
            axes = [self._lap_axis(lap, distance, distance_axis) for lap in (reference, target)]
            traces = {}
            low = max(d[0] for t, d in axes)
            high = min(d[-1] for t, d in axes)
            for name in ("Ground Speed", "GPS Speed", "Throttle Pos", "Brake Pos", "Steering Pos"):
                spec = specs.get(name.casefold())
                if spec is None:
                    continue
                values = self._channel(connection, tables, spec.name)
                times = gps[0] + np.arange(len(values)) / spec.frequency_hz
                pair = []
                for lap, (t, d) in zip((reference, target), axes):
                    if (not len(times) or times[-1] < lap.end_time_s - 1 / spec.frequency_hz - 1e-6):
                        raise LmuTelemetryFormatError(f"랩 전체의 채널 샘플이 부족합니다: {spec.name}")
                    mask = (times >= max(t[0], lap.start_time_s)) & (times <= t[-1]) & (times < lap.end_time_s)
                    sample_times, sample_values = times[mask], values[mask]
                    if len(sample_times) < 2 or not np.all(np.isfinite(sample_values)):
                        raise LmuTelemetryFormatError(f"랩 비교 채널의 샘플이 부족하거나 결측값이 있습니다: {spec.name}")
                    sample_distance = np.interp(sample_times, t, d)
                    keep = np.r_[np.diff(sample_distance) > 0, True]
                    sample_distance, sample_values = sample_distance[keep], sample_values[keep]
                    if len(sample_distance) < 2:
                        raise LmuTelemetryFormatError(f"거리별 채널 샘플이 부족합니다: {spec.name}")
                    pair.append((sample_distance, sample_values))
                    low, high = max(low, sample_distance[0]), min(high, sample_distance[-1])
                traces[spec.name] = (spec, pair)
            if not traces or high <= low:
                raise LmuTelemetryFormatError("두 랩에 공통으로 비교할 수 있는 거리·조작 채널이 없습니다.")
            grid = np.linspace(low, high, points)
            elapsed = [np.interp(grid, d, t) - lap.start_time_s
                       for lap, (t, d) in zip((reference, target), axes)]
            rows = [{"distance_m": float(position), "distance_pct": float(index * 100 / (points - 1)),
                     "reference_elapsed_s": float(elapsed[0][index]),
                     "target_elapsed_s": float(elapsed[1][index]),
                     "delta_time_s": float(elapsed[1][index] - elapsed[0][index])}
                    for index, position in enumerate(grid)]
            metrics = {}
            for name, (spec, pair) in traces.items():
                is_speed = name.casefold() in {"ground speed", "gps speed"}
                scale = 3.6 if is_speed and spec.unit == "m/s" else 1.0
                ref, tar = [np.interp(grid, d, v) * scale for d, v in pair]
                delta = tar - ref
                metrics[name] = {"unit": "km/h" if scale == 3.6 else spec.unit,
                                 "source_frequency_hz": spec.frequency_hz,
                                 "reference_mean": float(np.mean(ref)), "target_mean": float(np.mean(tar)),
                                 "mean_delta": float(np.mean(delta)),
                                 "mean_absolute_delta": float(np.mean(np.abs(delta))),
                                 "max_absolute_delta": float(np.max(np.abs(delta)))}
                for index, row in enumerate(rows):
                    row.update({f"{name}_reference": float(ref[index]),
                                f"{name}_target": float(tar[index]), f"{name}_delta": float(delta[index])})
            # Fixed distance bins are not game timing sectors or named corners.
            first_boundary = (math.floor(low / sector_length_m) + 1) * sector_length_m
            if (high - low) / sector_length_m > 500:
                raise LmuTelemetryFormatError("거리 구간이 너무 많습니다. 구간 길이를 늘리거나 거리 채널을 확인하세요.")
            boundaries = np.r_[low, np.arange(first_boundary, high, sector_length_m), high]
            boundary_elapsed = [np.interp(boundaries, d, t) - lap.start_time_s
                                for lap, (t, d) in zip((reference, target), axes)]
            boundary_delta = boundary_elapsed[1] - boundary_elapsed[0]
            sectors = []
            for index, (start, end) in enumerate(zip(boundaries[:-1], boundaries[1:])):
                channel_stats = {}
                for name, (spec, pair) in traces.items():
                    scale = 3.6 if name.casefold() in {"ground speed", "gps speed"} and spec.unit == "m/s" else 1.0
                    channel_stats[name] = {"unit": metrics[name]["unit"], **{
                        label: self._distance_stats(d, v, start, end, scale)
                        for label, (d, v) in zip(("reference", "target"), pair)}}
                ref_duration, tar_duration = [float(np.diff(axis[index:index + 2])[0])
                                              for axis in boundary_elapsed]
                sectors.append(LmuDistanceSector(index + 1, float(start), float(end),
                    ref_duration, tar_duration, tar_duration - ref_duration,
                    float(boundary_delta[index]), float(boundary_delta[index + 1]), channel_stats))
            return LmuLapComparison(reference_lap, target_lap, reference.recorded_lap_time_s,
                                    target.recorded_lap_time_s,
                                    target.recorded_lap_time_s - reference.recorded_lap_time_s,
                                    points, float(low), float(high), metrics, tuple(rows), "DIAGNOSTIC",
                                    sector_length_m, tuple(sectors), float(boundary_delta[-1] - boundary_delta[0]))


def discover_lmu_telemetry_dirs() -> tuple[Path, ...]:
    configured = os.getenv("LMU_TELEMETRY_DIRS", "").strip()
    if configured:
        candidates = [Path(item.strip()).expanduser() for item in configured.split(os.pathsep) if item.strip()]
    else:
        from lmu_install import game_folders, steam_libraries, GAME_FOLDER
        installed = game_folders() or [library / GAME_FOLDER for library in steam_libraries()[:2]]
        candidates = [folder / "UserData" / "Telemetry" for folder in installed]
        from lmu_settings import saved_folders
        candidates.extend(Path(path) for path in saved_folders())
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
