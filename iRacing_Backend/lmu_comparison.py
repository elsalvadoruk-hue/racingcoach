"""Manual LMU comparison of normalized laps. No automatic valid/best lap claims."""
from __future__ import annotations

import math
import numpy as np

from telemetry_core import AnalysisLap
from lmu_analysis import _map


DISPLAY_CHANNELS = {
    "speed_mps": ("speed_kmh", "km/h", 3.6),
    "throttle_ratio": ("throttle_pct", "%", 100),
    "brake_ratio": ("brake_pct", "%", 100),
    "steering_ratio": ("steering_pct", "%", 100),
}


def compatibility(reference: AnalysisLap, target: AnalysisLap):
    checks = {}
    for key in ("track_name", "track_layout", "car_name", "car_class"):
        a, b = reference.metadata.get(key), target.metadata.get(key)
        normalize = lambda value: " ".join(str(value).split()).casefold() if value is not None else ""
        left, right = normalize(a), normalize(b)
        if not left or not right:
            raise ValueError(f"파일 간 비교에 필요한 메타데이터가 없습니다: {key}")
        if left != right:
            raise ValueError(f"두 기록의 메타데이터가 달라 비교하지 않았습니다: {key}")
        checks[key] = {"reference": a, "target": b, "status": "MATCH"}
    return {"status": "METADATA_MATCH_ONLY", "checks": checks,
            "vehicle_model": "UNVERIFIED", "layout_direction": "UNVERIFIED",
            "conditions": "NOT_COMPARED", "game_validity": "UNVERIFIED"}


def _axis(lap):
    trace = lap.channels.get("distance_m")
    if (trace is None or trace.unit != "m" or len(trace.values) < 2 or
            not np.all(trace.valid_mask) or np.any(np.diff(trace.values) <= 0) or
            np.any(np.diff(trace.time_s) > 1.5 / trace.frequency_hz + 1e-9)):
        raise ValueError("거리 축에 정지·결측·불연속이 있어 파일 간 비교를 수행할 수 없습니다.")
    if (lap.diagnostic_status != "VALIDITY_UNKNOWN" or lap.recorded_lap_time_s is None or
            not math.isfinite(lap.recorded_lap_time_s) or lap.recorded_lap_time_s <= 0):
        raise ValueError("시간과 거리 진단을 통과한 기록 랩만 비교할 수 있습니다.")
    return trace


def _signal(lap, axis, key):
    trace = lap.channels.get(key)
    if trace is None:
        raise ValueError("지원되는 채널이 없습니다.")
    if lap.support.get(key, {}).get("status") != "AVAILABLE":
        raise ValueError("채널이 랩 전체를 지원하지 않거나 결측 샘플이 있습니다.")
    positions = axis.at(trace.time_s)
    selected = np.isfinite(positions)
    times, values, valid, positions = (array[selected] for array in
                                      (trace.time_s, trace.values, trace.valid_mask, positions))
    if (len(times) < 2 or not np.all(valid) or np.any(np.diff(positions) <= 0) or
            np.any(np.diff(times) > 1.5 / trace.frequency_hz + 1e-9)):
        raise ValueError("거리와 연속적으로 연결할 수 있는 채널 샘플이 부족합니다.")
    return positions, values


def _stats(distance, values, start, end):
    knots = np.r_[start, distance[(distance > start) & (distance < end)], end]
    samples = np.interp(knots, distance, values)
    mean = np.sum((samples[1:] + samples[:-1]) * np.diff(knots) / 2) / (end - start)
    return {"entry": float(samples[0]), "exit": float(samples[-1]),
            "minimum": float(np.min(samples)), "maximum": float(np.max(samples)),
            "distance_mean": float(mean)}


def _overlay(reference, target, points):
    lat, lon, dist = (reference.channels.get(key) for key in
                      ("latitude_deg", "longitude_deg", "distance_m"))
    if lat is None or lon is None or dist is None:
        return {"status": "UNAVAILABLE", "reason": "기준 랩의 좌표가 없어 공유 기준 경로를 그릴 수 없습니다.", "paths": []}
    longitudes = lon.at(lat.time_s)
    valid = lat.valid_mask & np.isfinite(longitudes) & np.isfinite(dist.at(lat.time_s))
    if not np.any(valid):
        return {"status": "UNAVAILABLE", "reason": "기준 랩의 거리와 일치하는 좌표가 없습니다.", "paths": []}
    first = int(np.flatnonzero(valid)[0])
    origin = (lat.values[first], longitudes[first])
    paths = [{"role": role, "lap_number": lap.lap_number, **_map(lap, points, origin)}
             for role, lap in (("reference", reference), ("target", target))]
    return {"status": "SCHEMATIC" if all(p["status"] == "SCHEMATIC" for p in paths) else "PARTIAL",
            "alignment": "SHARED_RAW_COORDINATES_UNVERIFIED", "paths": paths}


def compare_laps(reference: AnalysisLap, target: AnalysisLap, points=301, sector_length_m=500):
    if type(points) is not int or not 3 <= points <= 501:
        raise ValueError("비교 표시 지점은 3~501 범위의 정수여야 합니다.")
    if type(sector_length_m) is not int or not 100 <= sector_length_m <= 2000:
        raise ValueError("거리 구간 길이는 100~2000 m 범위의 정수여야 합니다.")
    matched = compatibility(reference, target)
    axes = [_axis(lap) for lap in (reference, target)]
    lengths = [axis.values[-1] - axis.values[0] for axis in axes]
    if abs(lengths[1] - lengths[0]) > max(50, .01 * max(lengths)):
        raise ValueError("두 랩의 기록 거리 범위가 달라 비교하지 않았습니다.")
    low, high = max(a.values[0] for a in axes), min(a.values[-1] for a in axes)
    traces, support = {}, {}
    for key, (name, unit, scale) in DISPLAY_CHANNELS.items():
        info = {role: lap.support.get(key, {"status": "MISSING"})
                for role, lap in (("reference", reference), ("target", target))}
        support[name] = info
        try:
            stages = [lap.support.get(key, {}).get("input_stage") for lap in (reference, target)]
            if key.endswith("_ratio") and (None in stages or stages[0] != stages[1]):
                raise ValueError("두 기록의 입력 처리 단계가 달라 채널을 비교하지 않았습니다.")
            pair = [_signal(lap, axis, key) for lap, axis in zip((reference, target), axes)]
        except ValueError as exc:
            info.update(status="UNAVAILABLE", reason=str(exc))
            continue
        info["status"] = "AVAILABLE"
        traces[name] = (key, unit, [(d, v * scale) for d, v in pair])
        low = max(low, *(d[0] for d, v in pair))
        high = min(high, *(d[-1] for d, v in pair))
    if high <= low:
        raise ValueError("두 랩에 공통 비교 거리 범위가 없습니다.")
    if (high - low) / sector_length_m > 500:
        raise ValueError("거리 구간이 너무 많습니다. 거리 채널이나 구간 길이를 확인하세요.")
    grid = np.linspace(low, high, points)
    def elapsed(positions):
        return [np.interp(positions, axis.values, axis.time_s) - lap.start_time_s
                for lap, axis in zip((reference, target), axes)]
    ref_time, tar_time = elapsed(grid)
    rows = [{"distance_m": float(d), "distance_pct": float(i * 100 / (points - 1)),
             "reference_elapsed_s": float(ref_time[i]), "target_elapsed_s": float(tar_time[i]),
             "delta_time_s": float(tar_time[i] - ref_time[i])} for i, d in enumerate(grid)]
    metrics = {}
    for name, (key, unit, pair) in traces.items():
        ref, tar = [np.interp(grid, d, v) for d, v in pair]
        delta = tar - ref
        metrics[name] = {"unit": unit, "reference_source": reference.support[key],
                         "target_source": target.support[key],
                         "mean_absolute_delta": float(np.mean(np.abs(delta))),
                         "max_absolute_delta": float(np.max(np.abs(delta)))}
        for i, row in enumerate(rows):
            row.update({name + "_reference": float(ref[i]), name + "_target": float(tar[i]),
                        name + "_delta": float(delta[i])})
    first_boundary = (math.floor(low / sector_length_m) + 1) * sector_length_m
    boundaries = np.r_[low, np.arange(first_boundary, high, sector_length_m), high]
    ref_bounds, tar_bounds = elapsed(boundaries)
    deltas = tar_bounds - ref_bounds
    sectors = []
    for i, (start, end) in enumerate(zip(boundaries[:-1], boundaries[1:])):
        ref_duration, tar_duration = float(ref_bounds[i+1]-ref_bounds[i]), float(tar_bounds[i+1]-tar_bounds[i])
        channels = {name: {"unit": unit, **{role: _stats(d, v, start, end)
                    for role, (d, v) in zip(("reference", "target"), pair)}}
                    for name, (_, unit, pair) in traces.items()}
        sectors.append({"sector_number": i+1, "distance_start_m": float(start), "distance_end_m": float(end),
                        "reference_duration_s": ref_duration, "target_duration_s": tar_duration,
                        "delta_time_s": tar_duration-ref_duration,
                        "cumulative_delta_start_s": float(deltas[i]), "cumulative_delta_end_s": float(deltas[i+1]),
                        "channels": channels})
    return {"status": "DIAGNOSTIC", "reference_status": "VALIDITY_UNVERIFIED",
            "reference_lap": reference.lap_number, "target_lap": target.lap_number,
            "reference_time_s": reference.recorded_lap_time_s, "target_time_s": target.recorded_lap_time_s,
            "delta_time_s": target.recorded_lap_time_s-reference.recorded_lap_time_s,
            "reference_valid": reference.valid, "target_valid": target.valid,
            "distance_start_m": float(low), "distance_end_m": float(high), "sample_count": points,
            "points": rows, "channels": metrics, "channel_support": support,
            "sector_length_m": sector_length_m, "sectors": sectors,
            "common_delta_time_s": float(deltas[-1]-deltas[0]),
            "compatibility": matched, "track_overlay": _overlay(reference, target, points)}
