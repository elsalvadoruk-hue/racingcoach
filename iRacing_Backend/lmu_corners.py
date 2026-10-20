"""Conservative reference-geometry windows; no official corner or validity claims.

The fixed 10 m geometry grid is independent of rendered chart points and driving
inputs. Heading vectors span 60 m, and their change is measured over 60 m. Runs
above 8 degrees with a >=15 degree peak and >=20 m length are retained; runs
within 100 m form one complex. Windows extend 180 m before and 120 m after the
curved core, splitting overlapping extensions in the straight between cores.
These conservative candidates can omit gentle bends. The geometric peak is not
a racing apex, and the covered windows do not partition the entire lap.
"""
from __future__ import annotations

import hashlib
import json
import math
import numpy as np

from analysis_bridge import corner_measurement
from lmu_comparison import _axis, _signal, compatibility
from telemetry_core import AnalysisLap


METHOD = "REFERENCE_HEADING_CHANGE_V1"
PARAMETERS = {"grid_m": 10, "heading_span_m": 60, "turn_span_m": 60,
              "threshold_deg": 8, "minimum_peak_deg": 15, "minimum_run_m": 20,
              "merge_gap_m": 100, "entry_extension_m": 180, "exit_extension_m": 120}
DELTA_KEYS = {"min_speed_kmh": None, "exit_speed_kmh": None,
              "brake_start_m": "brake_ratio", "throttle_first_m": "throttle_ratio",
              "full_throttle_m": "throttle_ratio"}


def _geometry(lap, axis):
    for key in ("latitude_deg", "longitude_deg"):
        trace = lap.channels.get(key)
        if trace is None or trace.unit != "deg":
            raise ValueError("기준 랩의 좌표 채널과 단위를 확인할 수 없습니다.")
        _signal(lap, axis, key)  # Whole-lap native coverage, holes and gaps matter.
    lat, lon = lap.channels["latitude_deg"], lap.channels["longitude_deg"]
    positions = axis.at(lat.time_s)
    longitudes = lon.at(lat.time_s)
    selected = np.isfinite(positions)
    times, positions, latitude, longitude = [value[selected] for value in
                                            (lat.time_s, positions, lat.values, longitudes)]
    if (len(positions) < 30 or not np.all(np.isfinite(longitude)) or
            np.any(np.abs(latitude) > 90) or np.any(np.abs(longitude) > 180) or
            np.any(np.diff(positions) <= 0) or np.max(np.diff(positions)) > 25 or
            positions[0]-axis.values[0] > 25 or axis.values[-1]-positions[-1] > 25 or
            positions[-1]-positions[0] < 300):
        raise ValueError("기준 좌표의 샘플 수·연속성·거리 범위를 확인할 수 없습니다.")
    x = (longitude-longitude[0])*math.pi/180*6371000*math.cos(math.radians(latitude[0]))
    y = (latitude-latitude[0])*math.pi/180*6371000
    step = np.hypot(np.diff(x), np.diff(y))
    ratio = step/np.diff(positions)
    if (np.any(step > np.diff(times)*150+5) or np.any(ratio > 2.5) or
            np.any(ratio < .1)):
        raise ValueError("기준 좌표에 점프·정지 또는 거리 축과 맞지 않는 이동이 있습니다.")
    first, last = math.ceil(positions[0]/10)*10, math.floor(positions[-1]/10)*10
    count = (last-first)/10+1
    if count < 30 or count > 25000:
        raise ValueError("좌표 분석의 거리 지점 수가 지원 범위를 벗어났습니다.")
    grid = np.arange(first, last+1, 10.)
    x, y = np.interp(grid, positions, x), np.interp(grid, positions, y)
    heading = np.unwrap(np.arctan2(y[6:]-y[:-6], x[6:]-x[:-6]))
    turning = np.abs(heading[6:]-heading[:-6])
    distances = grid[6:-6]
    active = turning >= math.radians(PARAMETERS["threshold_deg"])
    starts = np.flatnonzero(active & ~np.r_[False, active[:-1]])
    ends = np.flatnonzero(active & ~np.r_[active[1:], False])
    runs, excluded = [], []
    for a, b in zip(starts, ends):
        if (distances[b]-distances[a] < PARAMETERS["minimum_run_m"] or
                np.max(turning[a:b+1]) < math.radians(PARAMETERS["minimum_peak_deg"])):
            continue
        if runs and distances[a]-distances[runs[-1][1]] <= PARAMETERS["merge_gap_m"]:
            runs[-1][1] = int(b)
        else:
            runs.append([int(a), int(b)])
    cores = []
    for a, b in runs:
        if a == 0 or b == len(distances)-1:
            excluded.append({"start_m": float(distances[a]), "end_m": float(distances[b]),
                             "reason": "좌표 경계에서 잘린 굴곡 후보는 제외했습니다."})
        else:
            cores.append((float(distances[a]), float(distances[b]),
                          float(distances[a+int(np.argmax(turning[a:b+1]))])))
    bounds = []
    for i, (start, end, peak) in enumerate(cores):
        left, right = start-PARAMETERS["entry_extension_m"], end+PARAMETERS["exit_extension_m"]
        if i:
            left = max(left, (cores[i-1][1]+start)/2)
        if i+1 < len(cores):
            right = min(right, (end+cores[i+1][0])/2)
        if left < positions[0] or right > positions[-1]:
            excluded.append({"start_m": start, "end_m": end,
                             "reason": "진입·탈출 범위가 좌표 경계에서 잘려 후보를 제외했습니다."})
            continue
        number = len(bounds)+1
        bounds.append({"id": f"geometry-{number}", "label": f"구간 {number}",
                       "start_m": left, "apex_m": peak, "end_m": right,
                       "core_start_m": start, "core_end_m": end})
    if not bounds:
        raise ValueError("경계가 온전히 관측된 뚜렷한 굴곡 후보가 없습니다.")
    if len(bounds) > 64:
        raise ValueError("굴곡 후보가 너무 많아 좌표 품질을 확인해야 합니다.")
    return bounds, excluded


def compare_corners(reference: AnalysisLap, target: AnalysisLap) -> dict:
    """Measure identical reference-defined distance windows on both laps."""
    base = {"status": "UNAVAILABLE", "reference_valid": None, "target_valid": None,
            "reference_status": "VALIDITY_UNVERIFIED", "reference_lap": reference.lap_number,
            "target_lap": target.lap_number, "definition": None, "corners": []}
    matched = compatibility(reference, target)
    axes = [_axis(lap) for lap in (reference, target)]
    lengths = [axis.values[-1]-axis.values[0] for axis in axes]
    if abs(lengths[1]-lengths[0]) > max(50, .01*max(lengths)):
        raise ValueError("두 랩의 기록 거리 범위가 달라 구간을 비교하지 않습니다.")
    base["compatibility"] = matched
    try:
        boundaries, excluded = _geometry(reference, axes[0])
    except ValueError as exc:
        return base | {"reason": str(exc)}
    identity = json.dumps({"method": METHOD, "boundaries": boundaries,
                           "metadata": {k: reference.metadata.get(k) for k in
                                        ("track_name", "track_layout")}}, sort_keys=True)
    definition = {"definition_id": hashlib.sha256(identity.encode()).hexdigest()[:24],
                  "source": "REFERENCE_LAP_GEOMETRY", "status": "REFERENCE_GEOMETRY_UNVERIFIED",
                  "reference_lap": reference.lap_number, "boundaries": boundaries,
                  "method": METHOD, "parameters": PARAMETERS.copy(), "excluded": excluded,
                  "apex_meaning": "MAXIMUM_GEOMETRIC_HEADING_CHANGE",
                  "coverage": "SELECTED_WINDOWS_ONLY",
                  "reason": "기준 랩 좌표의 굴곡 후보이며 공식 코너·지도·레이싱 에이펙스는 미검증입니다."}
    corners = []
    for boundary in boundaries:
        measurements = [corner_measurement(lap, boundary["start_m"], boundary["apex_m"],
                                           boundary["end_m"]) for lap in (reference, target)]
        delta, support = {}, {}
        for key, input_key in DELTA_KEYS.items():
            stages = [lap.support.get(input_key, {}).get("input_stage") for lap in (reference, target)]
            values = [measurement["metrics"].get(key) for measurement in measurements]
            if input_key and (None in stages or stages[0] != stages[1]):
                delta[key] = None
                support[key] = {"status": "INPUT_STAGE_MISMATCH",
                                "reason": "두 랩의 입력 처리 단계가 확인되지 않거나 다릅니다."}
            elif any(value is None or not math.isfinite(value) for value in values):
                delta[key] = None
                support[key] = {"status": "UNAVAILABLE", "reason": "양쪽 랩에서 지원되는 측정값이 필요합니다."}
            else:
                delta[key] = float(values[1]-values[0])
                support[key] = {"status": "AVAILABLE"}
        durations = [measurement["metrics"].get("duration_s") for measurement in measurements]
        duration_delta = (float(durations[1]-durations[0]) if all(
            value is not None and math.isfinite(value) for value in durations) else None)
        corners.append(boundary | {"reference": measurements[0], "target": measurements[1],
                                   "delta_time_s": duration_delta, "delta": delta, "delta_support": support})
    return base | {"status": "AVAILABLE", "definition": definition, "corners": corners,
                   "comparison_note": "각 구간의 차이는 대상 랩에서 기준 랩을 뺀 값이며, 합계는 전체 랩 차이가 아닙니다."}
