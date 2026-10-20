"""Bounded AnalysisLap bridge into the existing, application-free measurements."""
from __future__ import annotations

from dataclasses import dataclass
import math
import numpy as np

from corner_measurements import CornerMeasurements
from telemetry_core import AnalysisLap, ChannelTrace


# A steering ratio is deliberately absent: it is not an angle in radians.
GRID_CHANNELS = {
    "speed_kph": ("speed_mps", "m/s", 3.6),
    "Brake": ("brake_ratio", "ratio", 1.0),
    "Throttle": ("throttle_ratio", "ratio", 1.0),
    "SteeringWheelAngle": ("steering_angle_rad", "rad", 1.0),
}
BRAKE_METRICS = ("braking_distance_m", "braking_time_s", "brake_entry_speed_kph",
                 "brake_end_speed_kph", "avg_decel_g", "peak_decel_g", "brake_speed_time")


def _continuous(trace: ChannelTrace, start: float, end: float) -> bool:
    """Inspect native samples too: a small hole can fall between render knots."""
    if len(trace.time_s) < 2 or not np.all(np.isfinite(trace.at([start, end]))):
        return False
    left = max(0, int(np.searchsorted(trace.time_s, start, side="right"))-1)
    right = min(len(trace.time_s)-1, int(np.searchsorted(trace.time_s, end, side="left")))
    return bool(np.all(trace.valid_mask[left:right+1]) and
                np.all(np.diff(trace.time_s[left:right+1]) <= 1.5/trace.frequency_hz+1e-9))


@dataclass(frozen=True)
class MeasurementGrid:
    distance_m: np.ndarray
    channels: dict
    support: dict
    span_m: float
    resolution_m: float


def measurement_grid(lap: AnalysisLap, start_time_s: float, end_time_s: float,
                     *, resolution_m: float = 2.0,
                     fixed_distance_bounds: tuple[float, float] | None = None) -> MeasurementGrid:
    """Use channel-native clocks; the normalized fraction refers to this interval."""
    if (not all(isinstance(v, (float, int)) and not isinstance(v, bool) and math.isfinite(v)
                for v in (start_time_s, end_time_s, resolution_m)) or resolution_m <= 0 or
            not lap.start_time_s <= start_time_s < end_time_s <= lap.end_time_s):
        raise ValueError("측정 구간의 시간 경계와 거리 간격을 확인하세요.")
    axis = lap.channels.get("distance_m")
    if (axis is None or axis.unit != "m" or not _continuous(axis, start_time_s, end_time_s) or
            not np.all(axis.valid_mask) or np.any(np.diff(axis.values) <= 0)):
        raise ValueError("연속된 단조 증가 거리 축을 확인할 수 없습니다.")
    start, end = axis.at([start_time_s, end_time_s])
    if fixed_distance_bounds is not None:
        if (len(fixed_distance_bounds) != 2 or
                not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                        for v in fixed_distance_bounds) or
                not axis.values[0] <= fixed_distance_bounds[0] < fixed_distance_bounds[1] <= axis.values[-1]):
            raise ValueError("고정 거리 구간의 경계를 확인하세요.")
        fixed_times = np.interp(fixed_distance_bounds, axis.values, axis.time_s)
        if not np.allclose(fixed_times, [start_time_s, end_time_s], rtol=0, atol=1e-9):
            raise ValueError("고정 거리 구간과 측정 시간 경계가 다릅니다.")
        # Preserve the common geometric interval exactly. A distance→time→
        # distance round trip can straddle ceil(span/resolution) and create
        # different knot counts for otherwise identical comparison windows.
        start, end = fixed_distance_bounds
    span = float(end-start)
    if not math.isfinite(span) or span <= 0 or span/resolution_m > 25000:
        raise ValueError("측정 구간의 거리 지점 수가 제한을 초과했습니다.")
    count = max(2, math.ceil(span/resolution_m)+1)
    distance = np.linspace(start, end, count)
    times = np.interp(distance, axis.values, axis.time_s)
    channels = {"distance_pct": (distance-start)/span,
                "time_s": times-lap.start_time_s,
                "_coverage": np.ones(count, dtype=bool), "_channel_support": {}}
    support = {}
    for name, (key, unit, scale) in GRID_CHANNELS.items():
        trace = lap.channels.get(key)
        info = {"source_key": key, "required_unit": unit}
        values = np.full(count, np.nan)
        if trace is None:
            info.update(status="MISSING", reason="이 의미의 채널이 없습니다.")
        elif trace.unit != unit:
            info.update(status="UNSUPPORTED_UNIT", reason="정규화 단위가 달라 연결하지 않았습니다.")
        else:
            info.update(source_name=trace.source_name, frequency_hz=trace.frequency_hz,
                        input_stage=lap.support.get(key, {}).get("input_stage"))
            values = trace.at(times)*scale
            available = _continuous(trace, start_time_s, end_time_s) and np.all(np.isfinite(values))
            info.update(status="AVAILABLE" if available else "PARTIAL")
            if not available:
                info["reason"] = "구간 안의 원본 샘플에 결측·시간 간격 또는 지원되지 않는 경계가 있습니다."
        channels[name] = values
        # Whole-interval measurements must also reject holes smaller than the grid.
        channels["_channel_support"][name] = np.full(count, info["status"] == "AVAILABLE", dtype=bool)
        support[name] = info
    return MeasurementGrid(distance, channels, support, span, span/(count-1))


def braking_measurement(lap: AnalysisLap, zone: dict) -> dict:
    """Measure a complete, native-threshold brake zone without detecting corners."""
    result = {"status": "UNAVAILABLE", "engine": "CornerMeasurements._brake_and_drive_zones",
              "method": "SPEED_CHANGE_OVER_TIME", "speed_step_kmh": 5.0,
              "distance_resolution_m": None, "channel_support": {},
              "metrics": {key: None for key in BRAKE_METRICS},
              "peak_decel_status": "ACCELERATION_NOT_CONNECTED"}
    if zone.get("status") != "COMPLETE":
        return result | {"reason": "경계·결측을 포함한 제동 구간은 심화 지표를 계산하지 않습니다."}
    try:
        grid = measurement_grid(lap, lap.start_time_s+zone["start_elapsed_s"],
                                lap.start_time_s+zone["end_elapsed_s"])
        result.update(channel_support=grid.support, distance_resolution_m=grid.resolution_m)
        if any(grid.support[key]["status"] != "AVAILABLE" for key in ("speed_kph", "Brake")):
            raise ValueError("구간 전체의 속도와 브레이크 원본 샘플이 모두 필요합니다.")
        speed = grid.channels["speed_kph"]
        if np.any((speed < 0) | (speed > 540)):
            raise ValueError("속도가 측정 계산의 지원 범위를 벗어났습니다.")
        engine = CornerMeasurements()
        # The existing kernel multiplies a fraction by track_length_m. Here both
        # refer to the observed interval; no official track length is inferred.
        engine.track_length_m = grid.span_m
        channel = lambda name: grid.channels.get(name, np.full(len(speed), np.nan))
        values = engine._brake_and_drive_zones(
            grid.channels, channel, channel("Brake"), channel("Throttle"), speed,
            grid.channels["time_s"], 0, len(speed)-1, None)
    except (ValueError, KeyError) as exc:
        return result | {"reason": str(exc)}
    return result | {"status": "AVAILABLE", "metrics": {key: values[key] for key in BRAKE_METRICS}}


CORNER_METRICS = ("duration_s", "min_speed_kmh", "exit_speed_kmh", "brake_start_m",
                  "brake_release_m", "throttle_first_m", "full_throttle_m", "peak_brake",
                  "coast_time_s", "avg_decel_g", "braking_distance_m")


def corner_measurement(lap: AnalysisLap, start_m: float, apex_m: float, end_m: float) -> dict:
    """Adapt a fixed geometric window to the unmodified iRacing measurement kernel.

    The middle is a geometric landmark, not a measured racing-line apex. Input
    positions are first/last threshold observations on the <=2 m grid, not exact
    pedal crossings. Duration alone uses exact inverse native-distance knots.
    """
    result = {
        "status": "UNAVAILABLE", "engine": "CornerMeasurements._compute_corner_metrics",
        "distance_resolution_m": None, "channel_support": {},
        "metrics": {key: None for key in CORNER_METRICS},
        "metric_support": {key: {"status": "UNAVAILABLE", "reason": "측정 구간을 확인하지 못했습니다."}
                           for key in CORNER_METRICS},
        "position_method": "APPROXIMATE_DISTANCE_GRID_THRESHOLD_OBSERVATION",
        "duration_method": "NATIVE_DISTANCE_TIME_INTERPOLATION",
        "definitions": {
            "brake_start_m": "구간 시작부터 기하 중심까지 최초 브레이크 8% 초과 관측 위치",
            "brake_release_m": "구간 시작부터 기하 중심 이후 최대 75m까지 마지막 브레이크 5% 초과 관측 위치",
            "throttle_first_m": "기하 중심 이후 최초 스로틀 20% 초과 관측 위치이며 재가속 시작을 뜻하지 않습니다.",
            "full_throttle_m": "기하 중심 이후 최초 스로틀 95% 초과 관측 위치",
            "peak_brake": "구간 시작부터 기하 중심까지 최대 브레이크 비율",
            "coast_time_s": "구간 시작부터 기하 중심까지 속도 50km/h 초과이며 두 페달 모두 5% 미만인 격자 시간 합계",
        },
    }
    try:
        if (not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                    for v in (start_m, apex_m, end_m)) or not start_m < apex_m < end_m):
            raise ValueError("코너 구간의 시작·기하 중심·종료 거리를 확인하세요.")
        axis = lap.channels.get("distance_m")
        if (axis is None or axis.unit != "m" or len(axis.time_s) < 2 or
                not np.all(axis.valid_mask) or np.any(np.diff(axis.values) <= 0)):
            raise ValueError("연속된 단조 증가 거리축이 필요합니다.")
        if not axis.values[0] <= start_m < end_m <= axis.values[-1]:
            raise ValueError("코너 구간이 관측한 거리 범위를 벗어났습니다.")
        start_t, end_t = np.interp([start_m, end_m], axis.values, axis.time_s)
        grid = measurement_grid(lap, float(start_t), float(end_t),
                                fixed_distance_bounds=(start_m, end_m))
        result.update(channel_support=grid.support, distance_resolution_m=grid.resolution_m)
    except ValueError as exc:
        return result | {"reason": str(exc)}

    # Kernel support masks invalidate the whole interval after any native hole;
    # finite knots on either side must never conceal missing measurements.
    for name, low, high in (("speed_kph", 0, 540), ("Brake", 0, 1), ("Throttle", 0, 1)):
        if grid.support[name]["status"] == "AVAILABLE" and np.any(
                (grid.channels[name] < low) | (grid.channels[name] > high)):
            grid.support[name].update(status="UNSUPPORTED_RANGE", reason="채널 값이 지원 범위를 벗어났습니다.")
            grid.channels["_channel_support"][name][:] = False

    center = int(np.argmin(np.abs(grid.distance_m-apex_m)))
    engine = CornerMeasurements()
    engine.track_length_m = grid.span_m
    values = engine._compute_corner_metrics(grid.channels, 0, center, len(grid.distance_m)-1)
    result["geometric_center_grid_m"] = float(grid.distance_m[center])
    result["boundary_observations"] = {}

    def put(metric, value, dependencies=(), reason=None):
        missing = [name for name in dependencies if grid.support[name]["status"] != "AVAILABLE"]
        if missing:
            reason = "구간 전체의 원본 채널을 사용할 수 없습니다: " + ", ".join(missing)
        if reason is None and value is None:
            reason = "이 구간에서 해당 임계값 관측을 확인하지 못했습니다."
        if reason is None:
            result["metrics"][metric] = float(value)
            result["metric_support"][metric] = {"status": "AVAILABLE"}
            if metric in ("brake_start_m", "brake_release_m", "throttle_first_m", "full_throttle_m"):
                result["metric_support"][metric].update(approximate=True, method="DISTANCE_GRID_THRESHOLD_OBSERVATION")
        else:
            result["metric_support"][metric] = {"status": "UNAVAILABLE", "reason": reason}

    def location(key):
        pct = values[key]
        # The kernel rounds fractions to five decimals. Restore its selected
        # knot rather than exposing that extra fractional rounding as metres.
        return None if pct is None else float(grid.distance_m[int(round(pct*(len(grid.distance_m)-1)))])

    put("duration_s", float(end_t-start_t))
    put("min_speed_kmh", values["min_speed_kph"], ("speed_kph",))
    put("exit_speed_kmh", values["exit_speed_kph"], ("speed_kph",))
    brake_ok = grid.support["Brake"]["status"] == "AVAILABLE"
    brake_at_start = brake_ok and bool(grid.channels["Brake"][0] > .08)
    padding = max(1, int(round(75 / max(1, grid.span_m) * (len(grid.distance_m)-1))))
    release_end = min(len(grid.distance_m), center+padding)-1
    brake_at_search_end = brake_ok and bool(grid.channels["Brake"][release_end] > .05)
    result["boundary_observations"].update(
        brake_active_at_start=brake_at_start if brake_ok else None,
        brake_active_at_release_search_end=brake_at_search_end if brake_ok else None)
    start_reason = "구간 시작부터 브레이크가 활성화되어 시작 위치를 확인할 수 없습니다." if brake_at_start else None
    release_reason = "관측 탐색 경계까지 브레이크가 활성화되어 해제 위치를 확인할 수 없습니다." if brake_at_search_end else None
    put("brake_start_m", location("brake_start_pct"), ("Brake",), start_reason)
    put("brake_release_m", location("brake_release_pct"), ("Brake",), release_reason)
    for metric, key, threshold in (("throttle_first_m", "throttle_first_pct", .20),
                                   ("full_throttle_m", "full_throttle_pct", .95)):
        put(metric, location(key), ("Throttle",))
        result["boundary_observations"][metric+"_active_at_center"] = (
            bool(grid.channels["Throttle"][center] > threshold)
            if grid.support["Throttle"]["status"] == "AVAILABLE" else None)
    put("peak_brake", values["peak_brake"], ("Brake",))
    put("coast_time_s", values["coast_time_s"], ("speed_kph", "Brake", "Throttle"))
    zone_reason = start_reason or release_reason
    put("avg_decel_g", values["avg_decel_g"], ("speed_kph", "Brake"), zone_reason)
    put("braking_distance_m", values["braking_distance_m"], ("speed_kph", "Brake"), zone_reason)
    available = sum(info["status"] == "AVAILABLE" for info in result["metric_support"].values())
    result["status"] = "AVAILABLE" if available == len(CORNER_METRICS) else "PARTIAL" if available else "UNAVAILABLE"
    if result["status"] != "AVAILABLE":
        result["reason"] = "일부 지표는 채널 결측·경계 또는 임계값 미관측으로 계산하지 않았습니다."
    return result
