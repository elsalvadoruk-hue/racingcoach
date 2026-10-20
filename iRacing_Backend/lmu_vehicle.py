"""Bounded, read-only native LMU vehicle observations without setup diagnosis."""
from __future__ import annotations

import math
import numpy as np

from lmu_telemetry import LmuTelemetryFormatError, _number, _quote
from telemetry_core import AnalysisLap, ChannelTrace


MAX_NATIVE_ROWS = 2_000_000
MAX_SUMMARY_ROWS = 2_000_000
_DEPENDENCIES = ("GPS Time", "Lap Dist", "Lap", "Lap Time", "In Pits")
# Names are a closed SQL allowlist. Wheel component identities remain unknown.
CHANNELS = {
    "Tyres Wear": ("타이어 마모 기록", "타이어", 4, {"%": ("%", 1)}),
    "TyresCarcassTemp": ("타이어 카커스 온도", "타이어", 4, {"C": ("C", 1)}),
    "TyresPressure": ("타이어 압력", "타이어", 4, {"kPa": ("kPa", 1)}),
    "TyresRimTemp": ("타이어 림 온도", "타이어", 4, {"C": ("C", 1)}),
    "TyresRubberTemp": ("타이어 고무 온도", "타이어", 4, {"C": ("C", 1)}),
    "TyresTempCentre": ("타이어 중앙 온도 기록", "타이어", 4, {"C": ("C", 1)}),
    "TyresTempLeft": ("타이어 Left 온도 기록", "타이어", 4, {"C": ("C", 1)}),
    "TyresTempRight": ("타이어 Right 온도 기록", "타이어", 4, {"C": ("C", 1)}),
    "RideHeights": ("차고 기록", "차체·서스펜션", 4, {"m": ("mm", 1000), "mm": ("mm", 1)}),
    "FrontRideHeight": ("전방 차고", "차체·서스펜션", 1, {"m": ("mm", 1000), "mm": ("mm", 1)}),
    "RearRideHeight": ("후방 차고", "차체·서스펜션", 1, {"m": ("mm", 1000), "mm": ("mm", 1)}),
    "Front3rdDeflection": ("전방 서드 서스펜션 변위", "차체·서스펜션", 1, {"m": ("mm", 1000), "mm": ("mm", 1)}),
    "Rear3rdDeflection": ("후방 서드 서스펜션 변위", "차체·서스펜션", 1, {"m": ("mm", 1000), "mm": ("mm", 1)}),
    "Susp Pos": ("서스펜션 위치", "차체·서스펜션", 4, {"m": ("mm", 1000), "mm": ("mm", 1)}),
    "Wheel Speed": ("휠 속도 기록", "차체·서스펜션", 4, {"m/s": ("km/h", 3.6), "km/h": ("km/h", 1)}),
    "Fuel Level": ("연료량", "연료·에너지", 1, {"L": ("L", 1)}),
    "Virtual Energy": ("가상 에너지 기록", "연료·에너지", 1, {"%": ("%", 1)}),
    "SoC": ("배터리 충전 상태", "연료·에너지", 1, {"%": ("%", 1)}),
    "Engine Oil Temp": ("엔진 오일 온도", "엔진·브레이크", 1, {"C": ("C", 1)}),
    "Engine Water Temp": ("엔진 냉각수 온도", "엔진·브레이크", 1, {"C": ("C", 1)}),
    "Brakes Temp": ("브레이크 온도", "엔진·브레이크", 4, {"C": ("C", 1)}),
    "Brakes Core Temp": ("브레이크 코어 온도", "엔진·브레이크", 4, {"C": ("C", 1)}),
    "Brakes Air Temp": ("브레이크 공기 온도", "엔진·브레이크", 4, {"C": ("C", 1)}),
    "Brake Thickness": ("브레이크 두께 기록", "엔진·브레이크", 4, {"%": ("%", 1)}),
}
_ALIASES = {name.casefold(): name for name in CHANNELS}
_NUMERIC_TYPES = {"FLOAT", "DOUBLE", "REAL", "TINYINT", "SMALLINT", "INTEGER", "BIGINT",
                  "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT", "HUGEINT", "UHUGEINT"}


def _contiguous_rows(connection, table):
    count, first, last = connection.execute(
        f"SELECT COUNT(*), MIN(rowid), MAX(rowid) FROM {_quote(table)}").fetchone()
    return bool(count and first == 0 and last == count-1)


def _catalog(connection, reader, tables, summary):
    specs = {spec.name.casefold(): spec for spec in summary.channels}
    result = []
    for name, (label, group, count, conversions) in CHANNELS.items():
        spec = specs.get(name.casefold())
        entry = {"name": name, "label": label, "group": group, "status": "UNAVAILABLE",
                 "source_unit": spec.unit if spec else None, "unit": None}
        table = reader._table(tables, name)
        if spec is None or table is None:
            entry["reason"] = "파일에 기록된 채널 또는 채널 테이블이 없습니다."
        elif spec.unit not in conversions:
            entry["reason"] = "원본 단위가 지원 목록에 없어 변환하거나 해석하지 않았습니다."
        else:
            entry["unit"] = conversions[spec.unit][0]
            expected = {"value"} if count == 1 else {"value1", "value2", "value3", "value4"}
            fields = connection.execute(f"PRAGMA table_info({_quote(table)})").fetchall()
            types = {str(row[1]).casefold(): str(row[2]).upper() for row in fields}
            if set(types) != expected or any(kind not in _NUMERIC_TYPES for kind in types.values()):
                entry["reason"] = "지원하는 숫자형 단일 값 또는 4성분 채널 스키마가 아닙니다."
            elif summary.sample_counts.get(table, 0) > MAX_NATIVE_ROWS:
                entry["reason"] = "채널의 원본 행 수가 메모리 한도를 초과했습니다."
            elif summary.sample_counts.get(table, 0) == 0:
                entry["reason"] = "채널에 저장된 원본 표본이 없습니다."
            else:
                entry["status"] = "AVAILABLE"
        result.append(entry)
    return result


def _decimate(values, valid, limit):
    """Keep native extrema per bucket; original segment IDs prevent gap bridges."""
    if np.count_nonzero(valid) <= limit:
        return np.flatnonzero(valid)
    chosen = set()
    for indices in np.array_split(np.arange(len(values)), max(1, limit//2)):
        indices = indices[valid[indices]]
        if len(indices):
            chosen.add(int(indices[np.argmin(values[indices])]))
            chosen.add(int(indices[np.argmax(values[indices])]))
    # Odd budgets can retain an endpoint in addition to the bucket extrema.
    for index in (int(np.flatnonzero(valid)[0]), int(np.flatnonzero(valid)[-1])):
        if len(chosen) < limit:
            chosen.add(index)
    return np.asarray(sorted(chosen), dtype=int)


def _vehicle_read(reader, lap_number, channel_name="TyresPressure", points=301, *, _native_sink=None):
    if type(lap_number) is not int or lap_number < 0:
        raise ValueError("랩 번호는 0 이상의 정수여야 합니다.")
    if type(points) is not int or not 3 <= points <= 501:
        raise ValueError("차량 관측 표시 지점은 3~501개여야 합니다.")
    if not isinstance(channel_name, str) or channel_name.casefold() not in _ALIASES:
        raise ValueError("지원 목록에서 차량 관측 채널을 선택하세요.")
    name = _ALIASES[channel_name.casefold()]
    label, _, component_count, conversions = CHANNELS[name]
    base = {"status": "UNAVAILABLE", "lap_number": lap_number, "recorded_lap_time_s": None,
            "reference_status": "VALIDITY_UNVERIFIED", "catalog": [],
            "channel": {"name": name, "label": label, "source_unit": None, "unit": None,
                        "frequency_hz": None, "wheel_order": "UNVERIFIED" if component_count == 4 else "NOT_APPLICABLE",
                        "components": []}}
    with reader._connection() as connection:
        tables = reader._tables(connection)
        total = 0
        # _summary reads these native arrays in full. Bound them before calling it.
        for dependency in _DEPENDENCIES:
            table = reader._table(tables, dependency)
            if table is not None:
                count = connection.execute(f"SELECT COUNT(*) FROM {_quote(table)}").fetchone()[0]
                total += count
                if count > MAX_NATIVE_ROWS or total > MAX_SUMMARY_ROWS:
                    return base | {"reason": "랩 진단에 필요한 원본 행 수가 메모리 한도를 초과했습니다."}
        for dependency in ("GPS Time", "Lap Dist"):
            table = reader._table(tables, dependency)
            if table is None or not _contiguous_rows(connection, table):
                return base | {"reason": f"{dependency}의 원본 행 순서가 끊겨 시간·거리 축을 확인할 수 없습니다."}
        summary = reader._summary(connection, tables)
        catalog = _catalog(connection, reader, tables, summary)
        base["catalog"] = catalog
        entry = next(item for item in catalog if item["name"] == name)
        base["channel"].update(source_unit=entry["source_unit"], unit=entry["unit"])
        laps = [lap for lap in summary.laps if lap.lap_number == lap_number]
        if len(laps) != 1:
            return base | {"reason": "요청한 랩을 하나로 확인할 수 없습니다."}
        lap = laps[0]
        recorded = lap.recorded_lap_time_s
        if (lap.status != "VALIDITY_UNKNOWN" or recorded is None or
                not math.isfinite(recorded) or recorded <= 0):
            return base | {"reason": "시간과 거리 진단을 통과한 기록 랩만 차량 관측에 사용할 수 있습니다."}
        base["recorded_lap_time_s"] = float(recorded)
        if entry["status"] != "AVAILABLE":
            return base | {"reason": entry["reason"]}
        spec = next(spec for spec in summary.channels if spec.name.casefold() == name.casefold())
        base["channel"]["frequency_hz"] = float(spec.frequency_hz)
        gps_table = reader._table(tables, "GPS Time")
        # _summary already checked this clock's unit and finite first value.
        gps = connection.execute(f"SELECT value FROM {_quote(gps_table)} ORDER BY rowid LIMIT 1").fetchone()
        origin = _number(gps[0]) if gps else math.nan
        if not math.isfinite(origin):
            return base | {"reason": "채널 시간축의 시작 시각을 확인할 수 없습니다."}
        table = reader._table(tables, name)
        row_count = summary.sample_counts[table]
        first_id, last_id = connection.execute(f"SELECT MIN(rowid), MAX(rowid) FROM {_quote(table)}").fetchone()
        if first_id != 0 or last_id != row_count-1:
            return base | {"reason": "채널의 원본 행 순서가 끊겨 주파수 기반 시간축을 확인할 수 없습니다."}
        # Fetch only native samples in/near the selected lap. Final comparisons
        # enforce [start,end) exactly, without interpolation or extrapolation.
        sample_start = (lap.start_time_s-origin)*spec.frequency_hz
        sample_end = (lap.end_time_s-origin)*spec.frequency_hz
        if not math.isfinite(sample_start) or not math.isfinite(sample_end):
            return base | {"reason": "채널 주파수와 랩 경계로 유한한 표본 범위를 만들 수 없습니다."}
        first = min(row_count, max(0, math.floor(sample_start)-1))
        stop = min(row_count, max(0, math.ceil(sample_end)+1))
        fields = ["value"] if component_count == 1 else ["value1", "value2", "value3", "value4"]
        cursor = connection.execute(
            f"SELECT {','.join(_quote(field) for field in fields)} FROM {_quote(table)} ORDER BY rowid LIMIT ? OFFSET ?",
            [max(0, stop-first), first])
        raw = np.empty((max(0, stop-first), component_count), dtype=float)
        offset = 0
        while batch := cursor.fetchmany(16384):
            length = len(batch)
            raw[offset:offset+length] = [[_number(value) for value in row] for row in batch]
            offset += length
        raw = raw[:offset]
        stamps = origin+(first+np.arange(len(raw)))/spec.frequency_hz
        selected = (stamps >= lap.start_time_s) & (stamps < lap.end_time_s)
        stamps, raw = stamps[selected], raw[selected]
        with np.errstate(over="ignore", invalid="ignore"):
            scaled = raw*conversions[spec.unit][1]
        if _native_sink is not None:
            gps_spec = next(item for item in summary.channels if item.name.casefold() == "gps time")
            gps_values = reader._channel(connection, tables, gps_spec.name)
            if (gps_spec.unit != "s" or len(gps_values) < 2 or not np.all(np.isfinite(gps_values)) or
                    not np.allclose(gps_values, origin+np.arange(len(gps_values))/gps_spec.frequency_hz,
                                    rtol=0, atol=1e-5)):
                return base | {"reason": "GPS Time이 채널 주파수와 일치하는 연속 시간 축이 아닙니다."}
            dist_spec = next(item for item in summary.channels if item.name.casefold() == "lap dist")
            dist = reader._channel(connection, tables, dist_spec.name)
            dist_times = origin+np.arange(len(dist))/dist_spec.frequency_hz
            if len(dist_times) and dist_times[-1] > gps_values[-1]+1/dist_spec.frequency_hz:
                return base | {"reason": "거리 채널이 GPS Time 기록 범위를 벗어납니다."}
            dist_times, dist = reader._lap_axis(lap, dist, dist_times)
            distance = ChannelTrace(dist_spec.name, "m", dist_spec.frequency_hz, dist_times,
                                    dist, np.ones(len(dist), dtype=bool))
            analysis_channels = {"distance_m": distance}
            for source, key in (("GPS Latitude", "latitude_deg"), ("GPS Longitude", "longitude_deg")):
                coordinate = next((item for item in summary.channels if item.name.casefold() == source.casefold()), None)
                coordinate_table = reader._table(tables, source)
                if (coordinate is None or coordinate.unit != "deg" or coordinate_table is None or
                        summary.sample_counts.get(coordinate_table, MAX_NATIVE_ROWS+1) > MAX_NATIVE_ROWS or
                        not _contiguous_rows(connection, coordinate_table)):
                    continue
                try:
                    raw_coordinate = reader._channel(connection, tables, coordinate.name)
                    coordinate_times = origin+np.arange(len(raw_coordinate))/coordinate.frequency_hz
                    keep = (coordinate_times >= lap.start_time_s) & (coordinate_times < lap.end_time_s)
                    analysis_channels[key] = ChannelTrace(coordinate.name, "deg", coordinate.frequency_hz,
                        coordinate_times[keep], raw_coordinate[keep], np.isfinite(raw_coordinate[keep]))
                except (LmuTelemetryFormatError, ValueError):
                    # Geometry is optional for vehicle comparison, never a
                    # reason to hide otherwise supported scalar observations.
                    continue
            analysis = AnalysisLap("LMU_DUCKDB", lap.lap_number, lap.start_time_s, lap.end_time_s,
                                   lap.recorded_lap_time_s, None, lap.status, summary.metadata,
                                   analysis_channels, {key: {"status": "AVAILABLE"} for key in analysis_channels})
            _native_sink.update(lap=analysis, time_s=stamps, values=scaled, fields=tuple(fields))
        coverage = bool(len(stamps) and stamps[0] <= lap.start_time_s+1/spec.frequency_hz+1e-9 and
                        stamps[-1] >= lap.end_time_s-1/spec.frequency_hz-1e-9)
        for index, field in enumerate(fields):
            values = scaled[:, index]
            valid = np.isfinite(values)
            valid_count = int(np.count_nonzero(valid))
            # A new segment begins after every invalid native sample, even if
            # decimation omits that sample and both surrounding boundaries.
            begins = valid & ~np.r_[False, valid[:-1]] if len(valid) else np.empty(0, dtype=bool)
            segments = np.cumsum(begins)-1
            render = _decimate(values, valid, points) if valid_count else np.empty(0, dtype=int)
            component = {"id": field, "label": field if component_count == 4 else "측정값",
                         "status": "AVAILABLE" if valid_count == len(values) and valid_count and coverage else "PARTIAL" if valid_count else "UNAVAILABLE",
                         "sample_count": len(values), "valid_samples": valid_count,
                         "invalid_samples": len(values)-valid_count,
                         "minimum": float(np.min(values[valid])) if valid_count else None,
                         "maximum": float(np.max(values[valid])) if valid_count else None,
                         "mean": math.fsum(float(value)/valid_count for value in values[valid]) if valid_count else None,
                         "points": [{"elapsed_s": float(stamps[i]-lap.start_time_s), "value": float(values[i]),
                                     "segment": int(segments[i])} for i in render]}
            base["channel"]["components"].append(component)
        statuses = [component["status"] for component in base["channel"]["components"]]
        base["status"] = "AVAILABLE" if all(status == "AVAILABLE" for status in statuses) else "PARTIAL" if any(status != "UNAVAILABLE" for status in statuses) else "UNAVAILABLE"
        if base["status"] != "AVAILABLE":
            base["reason"] = "선택 랩의 일부 원본 값이 결측이거나 채널의 기록 범위가 부족합니다."
        base["channel"]["statistics_method"] = "NATIVE_VALID_SAMPLE_ARITHMETIC_MEAN"
        base["channel"]["decimation_method"] = "NATIVE_BUCKET_MIN_MAX"
        base["channel"]["decimation_note"] = "표시 예산 안에서 버킷별 최솟값·최댓값을 우선 보존합니다. 3점일 때 양 끝점 모두의 보존은 보장하지 않습니다. 통계는 표시점 수와 무관합니다."
        base["channel"]["interpretation_note"] = "성분의 휠 순서와 Left/Right 온도의 내·외측 대응은 미확인입니다. 적정값이나 셋업 변경을 판정하지 않습니다."
        return base


def vehicle_report(reader, lap_number, channel_name="TyresPressure", points=301):
    return _vehicle_read(reader, lap_number, channel_name, points)


def read_vehicle_native(reader, lap_number, channel_name="TyresPressure"):
    """Internal bounded native arrays for distance comparison; never render data.

    The report remains a JSON-safe description. Native arrays and AnalysisLap
    are separate and must never be included directly in an API response.
    """
    native = {}
    report = _vehicle_read(reader, lap_number, channel_name, 3, _native_sink=native)
    return report, native
