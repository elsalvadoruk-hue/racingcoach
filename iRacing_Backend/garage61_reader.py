"""Garage61 CSV parsing; the caller supplies local metadata lookup."""
from __future__ import annotations
import csv
import math
from pathlib import Path
from typing import Any, Optional
import numpy as np
from analysis_numeric import round_or_none
from telemetry_types import ExternalReferenceLap


class Garage61ReferenceReader:
    NUMERIC_CHANNELS = [
        "Speed", "Brake", "Throttle", "RPM", "SteeringWheelAngle", "Gear",
        "Lat", "Lon", "LatAccel", "LongAccel", "VertAccel", "Yaw", "YawRate",
        "PositionType",
    ]
    BOOLEAN_CHANNELS = ["ABSActive", "DRSActive"]

    def __init__(self, path: Path, *, metadata_lookup):
        self.path = path
        self.metadata_lookup = metadata_lookup

    @staticmethod
    def _as_float(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return math.nan

    @staticmethod
    def _as_bool(value: Any) -> float:
        normalized = str(value if value is not None else "").strip().casefold()
        if normalized in {"1", "true", "yes", "on"}:
            return 1.0
        if normalized in {"0", "false", "no", "off"}:
            return 0.0
        return math.nan

    def parse(self) -> ExternalReferenceLap:
        metadata = self.metadata_lookup(self.path)
        if metadata is None:
            raise ValueError(
                "Garage61 기본 파일명 형식을 인식할 수 없습니다. 파일명을 변경하지 않은 CSV를 사용하십시오."
            )
        with self.path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = set(reader.fieldnames or [])
            required = {"Speed", "LapDistPct", "Brake", "Throttle", "SteeringWheelAngle"}
            missing = sorted(required - fieldnames)
            if missing:
                raise ValueError("Garage61 CSV 필수 채널 누락: " + ", ".join(missing))
            distance: list[float] = []
            data: dict[str, list[float]] = {
                name: []
                for name in self.NUMERIC_CHANNELS + self.BOOLEAN_CHANNELS
                if name in fieldnames
            }
            time_channel = next((name for name in ("SessionTime", "Time", "TimeS", "TimeSeconds") if name in fieldnames), None)
            original_indices: list[float] = []
            original_times: list[float] = []
            original_time_origin: Optional[float] = None
            total_rows = 0
            for original_index, row in enumerate(reader):
                total_rows = original_index + 1
                row_time = self._as_float(row.get(time_channel)) if time_channel else math.nan
                if original_time_origin is None and math.isfinite(row_time):
                    original_time_origin = row_time
                lap_dist = self._as_float(row.get("LapDistPct"))
                if not math.isfinite(lap_dist):
                    continue
                distance.append(lap_dist)
                original_indices.append(float(original_index))
                original_times.append(row_time)
                for name in list(data):
                    if name in self.BOOLEAN_CHANNELS:
                        data[name].append(self._as_bool(row.get(name)))
                    else:
                        data[name].append(self._as_float(row.get(name)))

        if len(distance) < 300:
            raise ValueError("Garage61 CSV 표본 수가 너무 적습니다.")
        dist = np.asarray(distance, dtype=float)
        arrays = {name: np.asarray(values, dtype=float) for name, values in data.items()}
        arrays["_original_sample_index"] = np.asarray(original_indices, dtype=float)
        arrays["_source_time"] = np.asarray(original_times, dtype=float)
        timing_last_index = max(1, total_rows - 1)

        # Garage61 exports can include the first few samples of the next lap.
        # Keep the first full monotonic 0→1 traversal and discard the wrap tail.
        wraps = np.flatnonzero(np.diff(dist) < -0.50)
        if len(wraps):
            stop = int(wraps[0]) + 1
            timing_last_index = max(1, int(arrays["_original_sample_index"][stop - 1]))
            dist = dist[:stop]
            arrays = {name: values[:stop] for name, values in arrays.items()}
        monotonic = np.r_[True, dist[1:] > np.maximum.accumulate(dist[:-1]) + 1e-7]
        dist = dist[monotonic]
        arrays = {name: values[monotonic] for name, values in arrays.items()}

        # The API export includes PositionType. If a small pre/post-lap tail is
        # present, keep the on-track traversal only; never discard the lap when
        # PositionType is absent in an older export.
        position = arrays.get("PositionType")
        if position is not None:
            on_track = np.isfinite(position) & (np.rint(position).astype(int) == 3)
            if np.count_nonzero(on_track) >= 250:
                on_track_dist = dist[on_track]
                if len(on_track_dist) >= 250 and float(np.nanmax(on_track_dist) - np.nanmin(on_track_dist)) >= 0.90:
                    dist = on_track_dist
                    arrays = {name: values[on_track] for name, values in arrays.items()}

        if len(dist) < 250 or float(np.nanmax(dist) - np.nanmin(dist)) < 0.90:
            raise ValueError("Garage61 CSV가 한 바퀴 전체를 포함하지 않습니다.")

        # Garage61 API currently exports Speed in m/s. Keep a defensive unit
        # guard so a future/legacy km/h export cannot silently inflate speeds.
        if "Speed" in arrays:
            finite_speed = arrays["Speed"][np.isfinite(arrays["Speed"])]
            if len(finite_speed):
                p95 = float(np.nanpercentile(finite_speed, 95))
                if 130.0 < p95 < 500.0:
                    arrays["Speed"] = arrays["Speed"] / 3.6
                    metadata["speed_unit_normalized_from"] = "km/h"
                else:
                    metadata["speed_unit_normalized_from"] = "m/s"
                metadata["speed_p95_raw"] = round_or_none(p95, 3)
        metadata["telemetry_sample_count"] = int(len(dist))
        metadata["telemetry_distance_coverage"] = round_or_none(float(np.nanmax(dist) - np.nanmin(dist)), 4)
        metadata["observed_distance_range"] = [round_or_none(float(np.min(dist)), 6), round_or_none(float(np.max(dist)), 6)]
        metadata["channel_numeric_coverage"] = {key: round(float(np.mean(np.isfinite(values))), 4) for key, values in arrays.items() if not key.startswith("_")}

        lap_time_s = float(metadata["lap_time_s"])
        source_times = arrays.pop("_source_time")
        source_indices = arrays.pop("_original_sample_index")
        valid_times = np.isfinite(source_times)
        if np.count_nonzero(valid_times) >= 2 and np.all(np.diff(source_times[valid_times]) > 0):
            # Trust elapsed timestamps (in seconds) when consistent with lap duration.
            span = float(source_times[valid_times][-1] - source_times[valid_times][0])
            if 0.8 * lap_time_s <= span <= 1.2 * lap_time_s:
                sample_time = np.interp(source_indices, source_indices[valid_times], source_times[valid_times])
                sample_time -= original_time_origin if original_time_origin is not None else source_times[valid_times][0]
                metadata["timing_source"] = time_channel
            else:
                sample_time = source_indices / timing_last_index * lap_time_s
                metadata["timing_source"] = "ORIGINAL_SAMPLE_INDEX"
        else:
            sample_time = source_indices / timing_last_index * lap_time_s
            metadata["timing_source"] = "ORIGINAL_SAMPLE_INDEX"
        for name in required - {"LapDistPct"}:
            if np.count_nonzero(np.isfinite(arrays[name])) < 250:
                raise ValueError(f"Garage61 CSV channel {name} has insufficient numeric data")
        # Match IBT channel naming so the existing corner engine can be reused.
        if "ABSActive" in arrays:
            arrays["BrakeABSactive"] = arrays.pop("ABSActive")
        return ExternalReferenceLap(
            path=self.path,
            metadata=metadata,
            distance_pct=dist,
            sample_time_s=sample_time,
            channels=arrays,
        )
