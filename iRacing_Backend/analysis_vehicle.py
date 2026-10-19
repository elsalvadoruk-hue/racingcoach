"""Measured vehicle, tyre, suspension, fuel and control diagnostics."""

from __future__ import annotations
import math
from typing import Any, Iterable, Optional
import numpy as np
from telemetry_types import LapSegment
from analysis_models import AnalyzeRequest, SessionMode, session_policy

from analysis_numeric import contiguous_groups, finite_values, max_finite, mean_finite, min_finite, round_or_none
from setup_analysis import extract_setup_summary


class VehicleAnalysis:
    def _vehicle_diagnostics(self, target: Optional[LapSegment]) -> dict[str, Any]:
        return {
            "tires": self._tire_diagnostics(target),
            "ride_height": self._ride_height_diagnostics(),
            "aero": self._aero_diagnostics(),
            "fuel": self._fuel_summary(),
            "controls": self._control_summary(),
            "damper_velocity": self._damper_velocity(),
            "gg": self._gg_diagram(),
        }

    def _gg_diagram(self) -> dict[str, Any]:
        """Right turns to the right (iRacing's LatAccel is positive to the left), acceleration up, braking down."""
        lateral, longitudinal = self.channels.get("LatAccel"), self.channels.get("LongAccel")
        speed = self.channels.get("Speed")
        laps = [lap for lap in self.valid_laps if lap.end_idx > lap.start_idx]
        if lateral is None or longitudinal is None or speed is None:
            return {"available": False, "reason": "NO_ACCELERATION_CHANNELS"}
        if not laps:
            return {"available": False, "reason": "NO_VALID_LAP"}
        pick = np.concatenate([np.arange(lap.start_idx, lap.end_idx + 1) for lap in laps])
        x = -lateral[pick].astype(float) / 9.80665
        y = longitudinal[pick].astype(float) / 9.80665
        keep = np.isfinite(x) & np.isfinite(y) & (speed[pick].astype(float) > self.GG_MIN_SPEED_MS)
        x, y = x[keep], y[keep]
        if len(x) < 10 * max(1, self.telemetry.tick_rate):
            return {"available": False, "reason": "TOO_FEW_SAMPLES"}
        angle = (np.degrees(np.arctan2(y, x)) + 360.0) % 360.0
        radius = np.hypot(x, y)
        bins = int(360 / self.GG_BIN_DEG)
        envelope: list[Optional[float]] = []
        for index in range(bins):
            inside = radius[(angle >= index * self.GG_BIN_DEG) & (angle < (index + 1) * self.GG_BIN_DEG)]
            envelope.append(round(float(np.percentile(inside, self.GG_ENVELOPE_PCT)), 3) if len(inside) >= 20 else None)
        every = max(1, len(x) // self.GG_SAMPLE_POINTS)
        return {
            "available": True,
            "bin_deg": self.GG_BIN_DEG,
            "envelope_g": envelope,
            "points": [[round(float(a), 2), round(float(b), 2)] for a, b in zip(x[::every], y[::every])],
            "max_brake_g": round(float(np.percentile(-y, 99)), 2),
            "max_accel_g": round(float(np.percentile(y, 99)), 2),
            "max_lateral_g": round(float(np.percentile(np.abs(x), 99)), 2),
            "mean_combined_g": round(float(np.mean(radius)), 2),
            "laps": [lap.lap_number for lap in laps],
        }

    def _damper_velocity(self) -> dict[str, Any]:
        """Share of time each shock spends at each velocity over the valid laps; None-free and measured only."""
        laps = [lap for lap in self.valid_laps if lap.end_idx > lap.start_idx]
        if not laps:
            return {"available": False, "reason": "NO_VALID_LAP"}
        edges = np.arange(-self.DAMPER_LIMIT_MM_S, self.DAMPER_LIMIT_MM_S + self.DAMPER_BIN_MM_S, self.DAMPER_BIN_MM_S, dtype=float)
        wheels: dict[str, Any] = {}
        for wheel, name in self.DAMPER_WHEELS:
            values = self.channels.get(name)
            if values is None:
                continue
            velocity = np.concatenate([values[lap.start_idx:lap.end_idx + 1].astype(float) for lap in laps]) * 1000.0
            velocity = velocity[np.isfinite(velocity)]
            if len(velocity) < 10 * max(1, self.telemetry.tick_rate):
                continue
            counts, _ = np.histogram(np.clip(velocity, edges[0] + 1e-6, edges[-1] - 1e-6), edges)
            share = lambda mask: round(float(np.mean(mask)) * 100.0, 1)
            bump, rebound = velocity[velocity > 0], -velocity[velocity < 0]
            wheels[wheel] = {
                "share_pct": [round(float(value), 2) for value in counts / len(velocity) * 100.0],
                "bump_pct": share(velocity > 0),
                "rebound_pct": share(velocity < 0),
                "slow_bump_pct": share((velocity > 0) & (velocity <= self.DAMPER_SLOW_MM_S)),
                "fast_bump_pct": share(velocity > self.DAMPER_SLOW_MM_S),
                "slow_rebound_pct": share((velocity < 0) & (velocity >= -self.DAMPER_SLOW_MM_S)),
                "fast_rebound_pct": share(velocity < -self.DAMPER_SLOW_MM_S),
                "median_bump_mm_s": round_or_none(float(np.median(bump)), 1) if len(bump) else None,
                "median_rebound_mm_s": round_or_none(float(np.median(rebound)), 1) if len(rebound) else None,
                "p95_mm_s": round_or_none(float(np.percentile(np.abs(velocity), 95)), 1),
                "samples": int(len(velocity)),
            }
        if not wheels:
            return {"available": False, "reason": "NO_SHOCK_CHANNELS"}
        return {
            "available": True,
            "edges_mm_s": [float(edge) for edge in edges],
            "slow_limit_mm_s": self.DAMPER_SLOW_MM_S,
            "sample_hz": int(self.telemetry.tick_rate),
            "laps": [lap.lap_number for lap in laps],
            "wheels": wheels,
        }

    def _tire_diagnostics(self, target: Optional[LapSegment]) -> dict[str, Any]:
        tires: dict[str, Any] = {}
        if target is None:
            return {"status": "NO_VALID_LAP", "wheels": tires}

        end = target.end_idx
        start = max(target.start_idx, end - self.telemetry.tick_rate)
        window = slice(start, end + 1)
        for wheel in ("LF", "RF", "LR", "RR"):
            surface = [self._median_channel(f"{wheel}temp{position}", window) for position in ("L", "M", "R")]
            carcass = [self._median_channel(f"{wheel}tempC{position}", window) for position in ("L", "M", "R")]
            wear = [self._median_channel(f"{wheel}wear{position}", window) for position in ("L", "M", "R")]
            pressure = self._median_channel(f"{wheel}pressure", window)
            cold_pressure = self._median_channel(f"{wheel}coldPressure", window)
            carcass_dynamic = self._channel_is_dynamic(f"{wheel}tempCL")
            wear_dynamic = self._channel_is_dynamic(f"{wheel}wearL")
            tires[wheel] = {
                "surface_lmr_c": [round_or_none(value, 1) for value in surface],
                "surface_spread_c": round_or_none(max_finite(surface) - min_finite(surface), 1)
                if finite_values(surface)
                else None,
                "carcass_lmr_c": [round_or_none(value, 1) for value in carcass],
                "carcass_status": "DYNAMIC" if carcass_dynamic else "PIT_SNAPSHOT",
                "wear_lmr_pct_remaining": [round_or_none(value * 100, 1) if value is not None else None for value in wear],
                "wear_status": "DYNAMIC" if wear_dynamic else "PIT_SNAPSHOT",
                "hot_pressure_kpa": round_or_none(pressure, 1),
                "cold_pressure_kpa": round_or_none(cold_pressure, 1),
                "pressure_gain_kpa": round_or_none(pressure - cold_pressure, 1)
                if pressure is not None and cold_pressure is not None
                else None,
            }

        front_pressure = mean_finite([tires["LF"]["hot_pressure_kpa"], tires["RF"]["hot_pressure_kpa"]])
        rear_pressure = mean_finite([tires["LR"]["hot_pressure_kpa"], tires["RR"]["hot_pressure_kpa"]])
        lap_trend: list[dict[str, Any]] = []
        for lap in self.valid_laps:
            lap_end = lap.end_idx
            lap_start = max(lap.start_idx, lap_end - self.telemetry.tick_rate)
            lap_window = slice(lap_start, lap_end + 1)
            surface_values = [
                self._median_channel(f"{wheel}temp{position}", lap_window)
                for wheel in ("LF", "RF", "LR", "RR")
                for position in ("L", "M", "R")
            ]
            pressure_values = [
                self._median_channel(f"{wheel}pressure", lap_window)
                for wheel in ("LF", "RF", "LR", "RR")
            ]
            # The track surface over the whole lap (it changes slowly), beside the tyres at the line.
            whole_lap = slice(lap.start_idx, lap.end_idx + 1)
            track_temp = self._median_channel("TrackTempCrew", whole_lap)
            if track_temp is None:
                track_temp = self._median_channel("TrackTemp", whole_lap)
            lap_trend.append(
                {
                    "lap_number": lap.lap_number,
                    "lap_time_s": round_or_none(lap.lap_time_s, 4),
                    "mean_surface_temp_c": round_or_none(mean_finite(surface_values), 1),
                    "mean_hot_pressure_kpa": round_or_none(mean_finite(pressure_values), 1),
                    "mean_track_temp_c": round_or_none(track_temp, 1),
                }
            )

        temp_slope = self._trend_slope(
            [item["lap_number"] for item in lap_trend],
            [item.get("mean_surface_temp_c") for item in lap_trend],
        )
        pressure_slope = self._trend_slope(
            [item["lap_number"] for item in lap_trend],
            [item.get("mean_hot_pressure_kpa") for item in lap_trend],
        )
        valid_lap_count = len(self.valid_laps)
        if valid_lap_count < 3:
            recommendation_reason = (
                f"유효 랩이 {valid_lap_count}개이고 차량별 목표 열간압력 범위가 IBT에 없어 "
                "공기압 변경을 확정하지 않습니다."
            )
        else:
            recommendation_reason = (
                "랩별 온도·압력 추세는 계산했지만 차량별 목표 열간압력 범위와 Garage 상한이 "
                "IBT에 없어 수치 변경은 보류합니다."
            )
        return {
            "status": "VALID" if all(tires[wheel]["hot_pressure_kpa"] is not None for wheel in tires) else "PARTIAL",
            "measurement": "one-second median immediately before the selected lap finish",
            "wheels": tires,
            "front_rear_pressure_delta_kpa": round_or_none(front_pressure - rear_pressure, 1)
            if front_pressure is not None and rear_pressure is not None
            else None,
            "lap_trend": lap_trend,
            "surface_temp_slope_c_per_lap": round_or_none(temp_slope, 2),
            "hot_pressure_slope_kpa_per_lap": round_or_none(pressure_slope, 2),
            "recommendation": "HOLD_PRESSURE",
            "reason": recommendation_reason,
        }

    def _median_channel(self, name: str, window: slice) -> Optional[float]:
        values = self.channels.get(name)
        if values is None:
            return None
        selected = values[window].astype(float)
        selected = selected[np.isfinite(selected)]
        return float(np.median(selected)) if len(selected) else None

    def _channel_is_dynamic(self, name: str) -> bool:
        values = self.channels.get(name)
        if values is None:
            return False
        finite = values[np.isfinite(values.astype(float))].astype(float)
        return bool(len(finite) and float(np.max(finite) - np.min(finite)) >= 1e-5)

    @staticmethod
    def _trend_slope(x_values: list[Any], y_values: list[Optional[float]]) -> Optional[float]:
        points = [
            (float(x), float(y))
            for x, y in zip(x_values, y_values)
            if y is not None and math.isfinite(float(y))
        ]
        if len(points) < 2:
            return None
        x = np.array([point[0] for point in points], dtype=float)
        y = np.array([point[1] for point in points], dtype=float)
        if float(np.ptp(x)) <= 1e-9:
            return None
        slope, _ = np.polyfit(x, y, 1)
        return float(slope)

    def _ride_height_diagnostics(self) -> dict[str, Any]:
        speed = self.channels.get("Speed")
        surface = self.channels.get("PlayerTrackSurface")
        pit = self.channels.get("OnPitRoad")
        if speed is None:
            return {"status": "MISSING"}
        mask = speed * 3.6 >= 80.0
        if surface is not None:
            mask &= surface == 3
        if pit is not None:
            mask &= ~pit.astype(bool)
        if not np.any(mask):
            return {"status": "NO_HIGH_SPEED_SAMPLES"}

        channels = ["CFSRrideHeight", "LFrideHeight", "RFrideHeight", "LRrideHeight", "RRrideHeight"]
        stats: dict[str, Any] = {}
        for name in channels:
            values = self.channels.get(name)
            if values is None:
                continue
            mm = values[mask].astype(float) * 1000.0
            stats[name] = {
                "minimum_mm": round_or_none(float(np.min(mm)), 1),
                "p0_1_mm": round_or_none(float(np.quantile(mm, 0.001)), 1),
                "p1_mm": round_or_none(float(np.quantile(mm, 0.01)), 1),
                "median_mm": round_or_none(float(np.median(mm)), 1),
            }

        center = self.channels.get("CFSRrideHeight")
        front_avg = None
        if self.channels.get("LFrideHeight") is not None and self.channels.get("RFrideHeight") is not None:
            front_avg = (self.channels["LFrideHeight"] + self.channels["RFrideHeight"]) / 2.0
        bottoming_mask = np.zeros(len(speed), dtype=bool)
        if center is not None:
            bottoming_mask |= (center * 1000.0 <= 3.0) & mask
        if front_avg is not None:
            bottoming_mask |= (front_avg * 1000.0 <= 5.0) & mask
        events = contiguous_groups(np.flatnonzero(bottoming_mask), max_gap=self.telemetry.tick_rate // 5, min_len=2)
        affected_valid_laps = [
            lap.lap_number
            for lap in self.valid_laps
            if np.any(bottoming_mask[lap.start_idx : lap.end_idx + 1])
        ]
        return {
            "status": "VALID",
            "bottoming_detected": bool(events),
            "bottoming_events": len(events),
            "affected_valid_laps": affected_valid_laps,
            "threshold_note": "center splitter <=3 mm or front axle mean <=5 mm above 80 km/h",
            "channels": stats,
        }

    def _aero_diagnostics(self) -> dict[str, Any]:
        required = ["Speed", "LFrideHeight", "RFrideHeight", "LRrideHeight", "RRrideHeight"]
        if not all(name in self.channels for name in required):
            return {"status": "MISSING"}
        speed_kph = self.channels["Speed"] * 3.6
        mask = speed_kph >= 200.0
        surface = self.channels.get("PlayerTrackSurface")
        pit = self.channels.get("OnPitRoad")
        if surface is not None:
            mask &= surface == 3
        if pit is not None:
            mask &= ~pit.astype(bool)
        if not np.any(mask):
            return {"status": "NO_200_KPH_SAMPLES"}

        front = (self.channels["LFrideHeight"][mask] + self.channels["RFrideHeight"][mask]) * 500.0
        rear = (self.channels["LRrideHeight"][mask] + self.channels["RRrideHeight"][mask]) * 500.0
        pitch = rear - front
        setup_summary = extract_setup_summary(self.telemetry.setup)
        return {
            "status": "VALID",
            "front_ride_height_median_mm": round(float(np.median(front)), 1),
            "rear_ride_height_median_mm": round(float(np.median(rear)), 1),
            "aero_pitch_median_mm": round(float(np.median(pitch)), 1),
            "aero_pitch_p10_mm": round(float(np.quantile(pitch, 0.10)), 1),
            "aero_pitch_p90_mm": round(float(np.quantile(pitch, 0.90)), 1),
            "minimum_splitter_height_high_speed_mm": round_or_none(
                float(np.min(self.channels["CFSRrideHeight"][mask]) * 1000.0)
                if "CFSRrideHeight" in self.channels
                else None,
                1,
            ),
            "setup_calculated_front_height_mm": setup_summary.get("front_rh_at_speed_mm"),
            "setup_calculated_rear_height_mm": setup_summary.get("rear_rh_at_speed_mm"),
            "rear_wing_angle_deg": setup_summary.get("rear_wing_angle_deg"),
        }

    def _fuel_summary(self) -> dict[str, Any]:
        values = [lap.fuel_used_l for lap in self.valid_laps if lap.fuel_used_l is not None and lap.fuel_used_l > 0]
        median = float(np.median(values)) if values else None
        return {
            "fuel_per_valid_lap_l": round_or_none(median, 3),
            "sample_laps": len(values),
            "qualifying_3_lap_estimate_l": round_or_none(median * 3.0, 1) if median else None,
            "note": (
                "퀄리파잉 기본 구조는 아웃랩 1 + 계측랩 2, 총 3바퀴입니다. "
                "연료량은 정보 표시용이며 셋업 변경 엔진의 입력으로 사용하지 않습니다."
            ),
        }

    def _control_summary(self) -> dict[str, Any]:
        return {
            "brake_bias_pct": round_or_none(self._last_finite("dcBrakeBias"), 2),
            "abs_setting": round_or_none(self._last_finite("dcABS"), 0),
            "tc_setting": round_or_none(self._last_finite("dcTractionControl"), 0),
            "throttle_shape": round_or_none(self._last_finite("dcThrottleShape"), 0),
        }

    def _last_finite(self, channel: str) -> Optional[float]:
        values = self.channels.get(channel)
        if values is None:
            return None
        finite = values[np.isfinite(values.astype(float))]
        return float(finite[-1]) if len(finite) else None
