"""Shared distance-grid corner measurements for IBT analysis and live practice."""
from __future__ import annotations
import math
from typing import Any, Optional
import numpy as np
from corner_events import corner_events

def round_or_none(value: Optional[float], digits: int = 3) -> Optional[float]:
    if value is None or not math.isfinite(value):
        return None
    return round(float(value), digits)


def smooth(values: np.ndarray[Any, Any], window: int) -> np.ndarray[Any, Any]:
    if window <= 1 or len(values) < 3:
        return values.astype(float, copy=True)
    window = min(window, len(values) if len(values) % 2 else len(values) - 1)
    window = max(3, window | 1)
    pad = window // 2
    padded = np.pad(values.astype(float), (pad, pad), mode="edge")
    kernel = np.ones(window, dtype=float) / window
    return np.convolve(padded, kernel, mode="valid")



class CornerMeasurements:
    def _raw_lap_for(self, grid):
        # Live practice has only a distance grid, with no file-lap association.
        return None

    def _compute_corner_metrics(
        self,
        grid: dict[str, np.ndarray[Any, Any]],
        entry: int,
        apex: int,
        exit_idx: int,
    ) -> dict[str, Any]:
        raw = self._raw_lap_for(grid)
        events = corner_events(raw, *(float(grid['distance_pct'][i]) for i in
                                     (entry, apex, exit_idx))) if raw is not None else None
        n = len(grid["distance_pct"])
        padding = max(1, int(round(75.0 / max(1.0, self.track_length_m) * (n - 1))))
        pre_entry = max(0, entry - padding)
        post_exit = min(n - 1, exit_idx + padding)
        valid_coverage = np.asarray(grid.get("_coverage", np.ones(n, dtype=bool)), dtype=bool) & np.asarray(grid.get("_analysis_coverage", np.ones(n, dtype=bool)), dtype=bool)
        def channel(name: str) -> np.ndarray:
            values = np.asarray(grid.get(name, np.full(n, np.nan)), dtype=float).copy()
            values[~valid_coverage] = np.nan
            support = grid.get("_channel_support", {}).get(name)
            if support is not None:
                values[~np.asarray(support, dtype=bool)] = np.nan
            return values
        brake, throttle, steering = channel("Brake"), channel("Throttle"), channel("SteeringWheelAngle")
        yaw_rate, lat_accel, speed = channel("YawRate"), channel("LatAccel"), channel("speed_kph")
        abs_values = channel("BrakeABSactive") if "BrakeABSactive" in grid else None
        abs_active = abs_values > 0.5 if abs_values is not None else None

        braking = np.flatnonzero(brake[pre_entry : apex + 1] > 0.08)
        brake_start = pre_entry + int(braking[0]) if len(braking) else None
        brake_release_candidates = np.flatnonzero(brake[entry : min(n, apex + padding)] > 0.05)
        brake_release = entry + int(brake_release_candidates[-1]) if len(brake_release_candidates) else None
        throttle_first_candidates = np.flatnonzero(throttle[apex : post_exit + 1] > 0.20)
        throttle_first = apex + int(throttle_first_candidates[0]) if len(throttle_first_candidates) else None
        full_throttle_candidates = np.flatnonzero(throttle[apex : post_exit + 1] > 0.95)
        full_throttle = apex + int(full_throttle_candidates[0]) if len(full_throttle_candidates) else None

        corner_slice = slice(entry, exit_idx + 1)
        entry_slice = slice(entry, apex + 1)
        exit_slice = slice(apex, exit_idx + 1)
        time_axis = channel("time_s")
        time_steps = np.diff(time_axis, prepend=time_axis[0])
        coast_mask = (
            (throttle[entry_slice] < 0.05)
            & (brake[entry_slice] < 0.05)
            & (speed[entry_slice] > 50)
        )
        braking_mask = brake[entry_slice] > 0.08
        valid_abs = braking_mask & np.isfinite(abs_values[entry_slice]) if abs_values is not None else None
        abs_duty = float(np.mean(abs_active[entry_slice][valid_abs])) if valid_abs is not None and np.any(valid_abs) else None

        throttle_relifts = int(
            np.count_nonzero(
                (throttle[apex:exit_idx] > 0.50)
                & (throttle[apex + 1 : exit_idx + 1] < 0.25)
            )
        ) if exit_idx - apex > 1 else 0

        steering_segment = steering[corner_slice]
        corrections = 0
        if len(steering_segment) > 6:
            filtered_steering = smooth(steering_segment, min(11, len(steering_segment) | 1))
            steering_derivative = np.diff(filtered_steering)
            signs = np.sign(steering_derivative)
            extrema = (np.flatnonzero(signs[:-1] * signs[1:] < 0) + 1).tolist()
            significant_extrema: list[int] = []
            for index in extrema:
                if not significant_extrema or abs(
                    filtered_steering[index] - filtered_steering[significant_extrema[-1]]
                ) > 0.035:
                    significant_extrema.append(index)
            corrections = max(0, len(significant_extrema) - 1)

        steering_load = float(np.nanmean(np.abs(steering_segment))) if np.any(np.isfinite(steering_segment)) else None
        yaw_load = float(np.nanmean(np.abs(yaw_rate[corner_slice]))) if np.any(np.isfinite(yaw_rate[corner_slice])) else None
        efficiency_ratios = np.abs(yaw_rate[corner_slice]) / (np.abs(steering_segment) + 0.05)
        steering_efficiency = float(np.nanmean(efficiency_ratios)) if np.any(np.isfinite(efficiency_ratios)) else None

        def pct_at(index: Optional[int]) -> Optional[float]:
            return round(float(grid["distance_pct"][index]), 5) if index is not None else None

        def event_index(key):
            event = events[key]
            return int(np.argmin(abs(grid['distance_pct'] - event['pct']))) if event else None

        if events is not None:
            brake_start, brake_release, full_throttle = (event_index(key) for key in
                                                        ('brake_start', 'brake_release', 'full_throttle'))
        zones = self._brake_and_drive_zones(grid, channel, brake, throttle, speed, time_axis, brake_start, brake_release, full_throttle)

        native = {}
        event_names = ('brake_start', 'brake_release', 'throttle_first', 'full_throttle', 'min_speed')
        if events is not None:
            native = {key + '_pct': events[key]['pct'] if events[key] else None for key in event_names}
            native.update(abs_duty_pct=round_or_none(events['abs_time_share'], 1),
                          abs_observed_share=events['abs_observed_share'],
                          coast_time_s=round_or_none(events['coast_time_s'], 3),
                          throttle_relifts=events['relifts'],
                          event_uncertainty_m={key: events[key]['uncertainty_m'] if events[key] else None
                                               for key in event_names})

        return {
            **zones,
            "brake_start_pct": pct_at(brake_start),
            "brake_release_pct": pct_at(brake_release),
            "throttle_first_pct": pct_at(throttle_first),
            "full_throttle_pct": pct_at(full_throttle),
            "inputs_usable": bool(np.all(valid_coverage[corner_slice]) and all(np.all(np.isfinite(values[corner_slice])) for values in (brake, throttle, steering))),
            "measurement_usable": bool(np.all(valid_coverage[corner_slice]) and np.all(np.isfinite(speed[corner_slice])) and np.all(np.isfinite(time_axis[corner_slice]))),
            "distance_resolution_m": round_or_none(self.track_length_m / max(1, n - 1), 2),
            "peak_brake": round_or_none(float(np.nanmax(brake[entry_slice])), 3) if np.any(np.isfinite(brake[entry_slice])) else None,
            "abs_duty_pct": round_or_none(abs_duty * 100.0 if abs_duty is not None else None, 1),
            "coast_time_s": round_or_none(float(np.sum(time_steps[entry_slice][coast_mask])), 3) if np.all(np.isfinite(throttle[entry_slice])) and np.all(np.isfinite(brake[entry_slice])) and np.all(np.isfinite(time_steps[entry_slice])) else None,
            "min_speed_kph": round_or_none(float(np.nanmin(speed[corner_slice])), 1) if np.any(np.isfinite(speed[corner_slice])) else None,
            "exit_speed_kph": round_or_none(float(speed[exit_idx]), 1),
            "avg_abs_steer_deg": round_or_none(math.degrees(steering_load), 1) if steering_load is not None else None,
            "avg_abs_yaw_rate": round(yaw_load, 3) if yaw_load is not None else None,
            "peak_lat_g": round_or_none(float(np.nanmax(np.abs(lat_accel[corner_slice]))) / 9.80665, 2)
            if np.any(np.isfinite(lat_accel[corner_slice]))
            else None,
            "steering_efficiency": round(steering_efficiency, 3) if steering_efficiency is not None else None,
            "throttle_relifts": throttle_relifts if np.all(np.isfinite(throttle[exit_slice])) else None,
            "steering_corrections": corrections if np.all(np.isfinite(steering_segment)) else None,
            "event_source": raw.source if raw is not None else 'GRID',
            "event_uncertainty_m": {key: None for key in event_names},
            "abs_observed_share": None,
            "min_speed_pct": float(grid['distance_pct'][entry + np.nanargmin(speed[corner_slice])])
            if np.any(np.isfinite(speed[corner_slice])) else None,
            **native,
        }

    SPEED_STEP_KPH = 5.0          # speed-to-time readings for "how long from one speed to another"
    G = 9.80665

    def _brake_and_drive_zones(self, grid, channel, brake, throttle, speed, time_axis,
                               brake_start: Optional[int], brake_release: Optional[int],
                               full_throttle: Optional[int]) -> dict[str, Any]:
        """The braking zone into a corner and the full-throttle run out of it (RaceStudio 3's
        braking distance, average deceleration and times for speed). Only measured samples count:
        a zone with a gap in speed or time is left out."""
        out: dict[str, Any] = {"braking_distance_m": None, "braking_time_s": None, "brake_entry_speed_kph": None,
                               "brake_end_speed_kph": None, "avg_decel_g": None, "peak_decel_g": None,
                               "brake_speed_time": None, "drive_distance_m": None, "drive_top_speed_kph": None,
                               "drive_speed_time": None}
        pct = np.asarray(grid["distance_pct"], dtype=float)
        n = len(pct)
        long_accel = channel("LongAccel") if "LongAccel" in grid else None

        def measured(start: int, end: int) -> bool:
            return end > start and bool(np.all(np.isfinite(speed[start:end + 1])) and np.all(np.isfinite(time_axis[start:end + 1])))

        def readings(start: int, end: int, falling: bool) -> list[list[float]]:
            """[speed, seconds since start] each time the speed first passes a multiple of SPEED_STEP_KPH."""
            v, t = speed[start:end + 1], time_axis[start:end + 1] - time_axis[start]
            step = self.SPEED_STEP_KPH
            marks = np.arange(math.floor(v[0] / step) * step, math.ceil(v.min() / step) * step - 1e-6, -step) if falling                 else np.arange(math.ceil(v[0] / step) * step, math.floor(v.max() / step) * step + 1e-6, step)
            points = []
            for mark in marks:
                hit = np.flatnonzero(v <= mark if falling else v >= mark)
                if len(hit):
                    points.append([round(float(mark), 1), round(float(t[hit[0]]), 3)])
            return points

        if brake_start is not None and brake_release is not None and measured(brake_start, brake_release):
            seconds = float(time_axis[brake_release] - time_axis[brake_start])
            entry_kph, end_kph = float(speed[brake_start]), float(speed[brake_release])
            out.update({
                "braking_distance_m": round_or_none(float(pct[brake_release] - pct[brake_start]) * self.track_length_m, 1),
                "braking_time_s": round_or_none(seconds, 3),
                "brake_entry_speed_kph": round_or_none(entry_kph, 1),
                "brake_end_speed_kph": round_or_none(end_kph, 1),
                "brake_speed_time": readings(brake_start, brake_release, True),
            })
            zone = long_accel[brake_start:brake_release + 1] if long_accel is not None else None
            if zone is not None and np.all(np.isfinite(zone)):
                out["avg_decel_g"] = round_or_none(float(-np.mean(zone)) / self.G, 2)
                out["peak_decel_g"] = round_or_none(float(-np.min(zone)) / self.G, 2)
            elif seconds > 0:
                out["avg_decel_g"] = round_or_none((entry_kph - end_kph) / 3.6 / seconds / self.G, 2)
        if full_throttle is not None:
            lifted = np.flatnonzero((throttle[full_throttle:] < 0.95) | (brake[full_throttle:] > 0.08) | ~np.isfinite(throttle[full_throttle:]))
            end = full_throttle + int(lifted[0]) - 1 if len(lifted) else n - 1
            if end - full_throttle >= 2 and measured(full_throttle, end):
                out.update({
                    "drive_distance_m": round_or_none(float(pct[end] - pct[full_throttle]) * self.track_length_m, 1),
                    "drive_top_speed_kph": round_or_none(float(np.max(speed[full_throttle:end + 1])), 1),
                    "drive_speed_time": readings(full_throttle, end, False),
                })
        return out

