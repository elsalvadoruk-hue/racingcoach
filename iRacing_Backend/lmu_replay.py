"""Bounded offline LMU replay. Elapsed lap time is shared, not session time."""
from __future__ import annotations

import math
import numpy as np

from lmu_analysis import _map
from lmu_comparison import compatibility, _axis


def replay_laps(laps, points=501):
    if not 2 <= len(laps) <= 6:
        raise ValueError("재생할 랩을 2~6개 직접 선택하세요.")
    if type(points) is not int or not 3 <= points <= 1001:
        raise ValueError("재생 지점은 3~1001 범위의 정수여야 합니다.")
    axes = [_axis(lap) for lap in laps]
    checks = [compatibility(laps[0], lap) for lap in laps[1:]]
    lengths = [axis.values[-1]-axis.values[0] for axis in axes]
    if max(lengths)-min(lengths) > max(50, .01*max(lengths)):
        raise ValueError("선택한 랩의 거리 범위가 달라 재생하지 않았습니다.")
    lat, lon = (laps[0].channels.get(key) for key in ("latitude_deg", "longitude_deg"))
    if lat is None or lon is None:
        raise ValueError("첫 번째 랩의 좌표가 없어 공유 기준 경로를 그릴 수 없습니다.")
    longitudes = lon.at(lat.time_s)
    supported = lat.valid_mask & np.isfinite(longitudes) & np.isfinite(axes[0].at(lat.time_s))
    if not np.any(supported):
        raise ValueError("첫 번째 랩의 거리와 일치하는 좌표가 없습니다.")
    first = int(np.flatnonzero(supported)[0])
    origin = (float(lat.values[first]), float(longitudes[first]))
    duration = max(lap.end_time_s-lap.start_time_s for lap in laps)
    grid = np.linspace(0, duration, points)
    frames = [{"elapsed_s": float(t), "laps": []} for t in grid]
    paths = []
    number = lambda value: float(value) if np.isfinite(value) else None
    for index, (lap, axis) in enumerate(zip(laps, axes)):
        path = _map(lap, min(points, 501), origin)
        if path["status"] != "SCHEMATIC":
            raise ValueError(f"선택 {index+1}의 연속 좌표 경로를 확인할 수 없습니다.")
        paths.append({"selection_index": index, "lap_number": lap.lap_number,
                      "recorded_lap_time_s": lap.recorded_lap_time_s, "valid": lap.valid,
                      "boundary_duration_s": lap.end_time_s-lap.start_time_s,
                      "channels": lap.support, **path})
        # Native edges, not decimated render points, guard marker interpolation.
        latitude, longitude = lap.channels["latitude_deg"], lap.channels["longitude_deg"]
        times = latitude.time_s
        lons = longitude.at(times)
        native_distances = axis.at(times)
        valid = latitude.valid_mask & np.isfinite(lons) & np.isfinite(native_distances)
        xs = (lons-origin[1]) * math.pi/180 * 6371000 * math.cos(math.radians(origin[0]))
        ys = (latitude.values-origin[0]) * math.pi/180 * 6371000
        edges = (valid[1:] & valid[:-1] &
                 (np.diff(times) <= 1.5/latitude.frequency_hz + 1e-9) &
                 (np.hypot(np.diff(xs),np.diff(ys)) <= np.diff(times)*150+5))
        # Isolated jump samples must not appear even at their exact timestamp.
        connected = valid & (np.r_[False, edges] | np.r_[edges, False])
        absolute = lap.start_time_s+grid
        distances = axis.at(absolute)
        values = {key: trace.at(absolute) for key, trace in lap.channels.items()
                  if key in ("speed_mps", "brake_ratio", "throttle_ratio", "steering_ratio")}
        for frame, timestamp, distance, n in zip(frames, absolute, distances, range(points)):
            entry = {"selection_index": index, "status": "NO_POSITION", "position": None,
                     "distance_m": number(distance)}
            if timestamp >= lap.end_time_s-1e-9:
                entry.update(status="FINISHED", distance_m=None)
            else:
                right = int(np.searchsorted(times, timestamp, side="left"))
                pos = None
                if right < len(times) and abs(times[right]-timestamp) < 1e-9 and connected[right]:
                    pos = (xs[right], ys[right])
                elif 0 < right < len(times) and edges[right-1]:
                    fraction = (timestamp-times[right-1])/(times[right]-times[right-1])
                    pos = (xs[right-1]+fraction*(xs[right]-xs[right-1]),
                           ys[right-1]+fraction*(ys[right]-ys[right-1]))
                if pos is not None and np.isfinite(distance):
                    entry.update(status="SUPPORTED", position={"x_m": float(pos[0]), "y_m": float(pos[1])})
            for key in ("speed_mps", "brake_ratio", "throttle_ratio", "steering_ratio"):
                entry[key] = number(values[key][n]) if key in values and entry["status"] != "FINISHED" else None
            frame["laps"].append(entry)
    return {"status": "OFFLINE_DIAGNOSTIC_REPLAY", "reference_status": "VALIDITY_UNVERIFIED",
            "alignment": "LAP_EVENT_ELAPSED_TIME", "coordinate_alignment": "SHARED_RAW_COORDINATES_UNVERIFIED",
            "frame_count": points, "duration_s": float(duration), "paths": paths, "frames": frames,
            "compatibility": checks}
