"""LMU units adapter and bounded, measurement-only lap report."""
from __future__ import annotations

import math
import numpy as np
from telemetry_core import AnalysisLap, ChannelTrace, input_zones
from analysis_bridge import braking_measurement


# Explicit units only. Steering remains a ratio, never a wheel angle.
CHANNELS = {
    "speed_mps": (("Ground Speed", "GPS Speed"), {"km/h": 1 / 3.6, "m/s": 1.0}, "m/s", (0, None)),
    "throttle_ratio": (("Throttle Pos Unfiltered", "Throttle Pos"), {"%": .01, "ratio": 1.0, "1": 1.0}, "ratio", (0, 1)),
    "brake_ratio": (("Brake Pos Unfiltered", "Brake Pos"), {"%": .01, "ratio": 1.0, "1": 1.0}, "ratio", (0, 1)),
    "steering_ratio": (("Steering Pos Unfiltered", "Steering Pos"), {"%": .01, "ratio": 1.0, "1": 1.0}, "ratio", (-1, 1)),
    "latitude_deg": (("GPS Latitude",), {"deg": 1.0}, "deg", (-90, 90)),
    "longitude_deg": (("GPS Longitude",), {"deg": 1.0}, "deg", (-180, 180)),
}
SOURCE_CHANNELS = {name for names, *_ in CHANNELS.values() for name in names}


def normalize_lap(metadata, lap, specs, values, gps_start, distance_times, distance_values):
    channels = {"distance_m": ChannelTrace("Lap Dist", "m", specs["lap dist"].frequency_hz,
                distance_times, distance_values, np.ones(len(distance_times), dtype=bool))}
    support = {"distance_m": {"status": "AVAILABLE", "source_name": "Lap Dist", "source_unit": "m",
                               "unit": "m", "frequency_hz": specs["lap dist"].frequency_hz,
                               "invalid_samples": 0, "sample_count": len(distance_times)}}
    for key, (names, scales, unit, limits) in CHANNELS.items():
        spec = next((specs[name.casefold()] for name in names if name.casefold() in specs), None)
        if spec is None:
            support[key] = {"status": "MISSING", "reason": "기록된 채널이 없습니다."}
            continue
        info = {"source_name": spec.name, "source_unit": spec.unit, "unit": unit,
                "frequency_hz": spec.frequency_hz}
        if key.endswith("_ratio"):
            info["input_stage"] = "UNFILTERED" if "Unfiltered" in spec.name else "GAME_PROCESSED"
        support[key] = info
        if spec.unit not in scales:
            info.update(status="UNSUPPORTED_UNIT", reason="단위가 확인되지 않아 변환하지 않았습니다.")
            continue
        raw = values[spec.name]
        times = gps_start + np.arange(len(raw)) / spec.frequency_hz
        selected = (times >= lap.start_time_s) & (times < lap.end_time_s)
        times, data = times[selected], raw[selected] * scales[spec.unit]
        mask = np.isfinite(data)
        low, high = limits
        if low is not None:
            mask &= data >= low
        if high is not None:
            mask &= data <= high
        if len(times) < 2:
            info.update(status="INSUFFICIENT_SAMPLES", reason="랩 안의 샘플이 부족합니다.", sample_count=len(times))
            continue
        complete_time = bool(times[0] <= lap.start_time_s + 1 / spec.frequency_hz + 1e-6 and
                             times[-1] >= lap.end_time_s - 1 / spec.frequency_hz - 1e-6)
        count = int(np.count_nonzero(~mask))
        info.update(status="AVAILABLE" if complete_time and count == 0 else "PARTIAL",
                    sample_count=len(times), invalid_samples=count, full_lap_coverage=complete_time)
        if not np.any(mask):
            info.update(status="INVALID_SAMPLES", reason="유한한 범위 내 샘플이 없습니다.")
            continue
        channels[key] = ChannelTrace(spec.name, unit, spec.frequency_hz, times, data, mask)
    return AnalysisLap("LMU_DUCKDB", lap.lap_number, lap.start_time_s, lap.end_time_s,
                       lap.recorded_lap_time_s, lap.valid, lap.status, metadata, channels, support)


def _map(lap, points, origin=None):
    lat, lon, distance = (lap.channels.get(key) for key in ("latitude_deg", "longitude_deg", "distance_m"))
    if lat is None or lon is None:
        return {"status": "UNAVAILABLE", "reason": "좌표 채널의 단위와 샘플을 확인할 수 없습니다.", "points": []}
    # Keep each observed coordinate pair; only the rendered path is decimated.
    times = lat.time_s
    longitude, positions = lon.at(times), distance.at(times)
    valid = lat.valid_mask & np.isfinite(longitude) & np.isfinite(positions)
    if np.count_nonzero(valid) < 3:
        return {"status": "UNAVAILABLE", "reason": "거리와 일치하는 좌표 샘플이 부족합니다.", "points": []}
    latitude = lat.values
    first = int(np.flatnonzero(valid)[0])
    # Local tangent approximation only. Game coordinates need not be real GPS.
    latitude0, longitude0 = origin if origin is not None else (latitude[first], longitude[first])
    x = (longitude - longitude0) * (math.pi / 180) * 6371000 * math.cos(math.radians(latitude0))
    y = (latitude - latitude0) * (math.pi / 180) * 6371000
    # Do not draw a bridge across missing coordinates or a position jump.
    # The 150 m/s ceiling is a display quality guard, not a game-validity rule.
    edges = np.r_[False, valid[1:] & valid[:-1] & (np.diff(times) <= 1.5 / lat.frequency_hz + 1e-9)]
    jump = np.r_[False, np.hypot(np.diff(x), np.diff(y)) > np.diff(times) * 150 + 5]
    edges &= ~jump
    groups = []
    current = []
    for i in range(len(times)):
        if not valid[i]:
            if current:
                groups.append(current); current = []
            continue
        if current and not edges[i]:
            groups.append(current); current = []
        current.append(i)
    if current:
        groups.append(current)
    groups = [group for group in groups if len(group) >= 2]
    if not groups:
        return {"status": "UNAVAILABLE", "reason": "연속된 좌표 구간이 없습니다.", "points": []}
    # A high number of broken runs is not a useful schematic.
    if len(groups) > points // 2:
        return {"status": "UNAVAILABLE", "reason": "좌표 불연속 구간이 너무 많습니다.", "points": []}
    budget = max(2, points // max(1, len(groups)))
    rows = []
    for segment, indices in enumerate(groups):
        picked = np.unique(np.linspace(0, len(indices) - 1, min(len(indices), budget)).astype(int))
        for k in picked:
            i = indices[k]
            rows.append({"x_m": float(x[i]), "y_m": float(y[i]), "distance_m": float(positions[i]),
                         "elapsed_s": float(times[i] - lap.start_time_s), "segment": segment})
    span = max(max(row["x_m"] for row in rows) - min(row["x_m"] for row in rows),
               max(row["y_m"] for row in rows) - min(row["y_m"] for row in rows))
    if span < 1:
        return {"status": "UNAVAILABLE", "reason": "움직임을 확인할 수 없는 좌표입니다.", "points": []}
    return {"status": "SCHEMATIC", "coordinate_system": "LOCAL_TANGENT_APPROXIMATION",
            "geographic_identity": "UNVERIFIED",
            "origin": "REFERENCE_LAP_SAMPLE" if origin is not None else "FIRST_SUPPORTED_LAP_SAMPLE",
            "source_frequency_hz": lat.frequency_hz, "segment_count": len(groups),
            "position_jump_count": int(np.count_nonzero(jump & valid)), "points": rows}


def lap_report(lap: AnalysisLap, points: int = 301):
    if type(points) is not int or not 3 <= points <= 501:
        raise ValueError("경로 표시 지점은 3~501 범위의 정수여야 합니다.")
    braking = input_zones(lap, "brake_ratio", .08, .05)
    for zone in braking:
        zone["shared_measurement"] = braking_measurement(lap, zone)
    throttle = input_zones(lap, "throttle_ratio", .95, .90)
    def status(key, zones):
        if key not in lap.channels or "distance_m" not in lap.channels:
            return "UNAVAILABLE"
        required = (key, "distance_m", "speed_mps")
        if (any(lap.support.get(name, {}).get("status") != "AVAILABLE" for name in required) or
                any(zone["status"] != "COMPLETE" for zone in zones)):
            return "PARTIAL"
        return "AVAILABLE"
    return {"schema_version": lap.schema_version, "provider": lap.provider,
            "lap_number": lap.lap_number, "recorded_lap_time_s": lap.recorded_lap_time_s,
            "valid": lap.valid, "reference_status": "VALIDITY_UNVERIFIED",
            "status": "MEASUREMENT_ONLY", "channels": lap.support, "track_path": _map(lap, points),
            "braking": {"status": status("brake_ratio", braking),
                        "start_threshold_ratio": .08, "release_threshold_ratio": .05,
                        "minimum_duration_s": .15, "zones": braking},
            "full_throttle": {"status": status("throttle_ratio", throttle),
                              "start_threshold_ratio": .95, "release_threshold_ratio": .90,
                              "minimum_duration_s": .15, "zones": throttle}}
