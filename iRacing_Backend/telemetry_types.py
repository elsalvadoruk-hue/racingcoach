"""Telemetry records; importing this module opens no files or application stores."""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
import numpy as np
from analysis_numeric import interpolate_observed


@dataclass(frozen=True)
class VariableHeader:
    var_type: int
    offset: int
    count: int
    count_as_time: bool
    name: str
    description: str
    unit: str


@dataclass
class LapSegment:
    lap_number: int
    start_idx: int
    end_idx: int
    complete: bool
    valid: bool
    status: str
    lap_time_s: Optional[float]
    start_pct: float
    end_pct: float
    on_track_ratio: float
    pit_samples: int
    fuel_used_l: Optional[float]
    invalid_reason: Optional[str] = None


@dataclass
class ParsedTelemetry:
    path: Path
    signature: tuple[str, int, int]
    tick_rate: int
    buffer_length: int
    record_count: int
    data_offset: int
    session_text: str
    session_info: dict[str, Any]
    variables: dict[str, VariableHeader]
    channels: dict[str, np.ndarray[Any, Any]]
    metadata: dict[str, Any]
    setup: dict[str, Any]
    laps: list[LapSegment]
    parsed_at: float = field(default_factory=time.time)


@dataclass
class ExternalReferenceLap:
    path: Path
    metadata: dict[str, Any]
    distance_pct: np.ndarray[Any, Any]
    sample_time_s: np.ndarray[Any, Any]
    channels: dict[str, np.ndarray[Any, Any]]

    def grid(self, points: int, track_length_m: Optional[float] = None) -> dict[str, np.ndarray[Any, Any]]:
        count = max(500, int(points))
        grid = np.linspace(0.0, 1.0, count)
        output: dict[str, np.ndarray[Any, Any]] = {"distance_pct": grid}

        def interpolate(values: np.ndarray[Any, Any]) -> Optional[np.ndarray[Any, Any]]:
            numeric = values.astype(float, copy=False)
            valid = np.isfinite(numeric) & np.isfinite(self.distance_pct)
            if np.count_nonzero(valid) < 2:
                return None
            return interpolate_observed(self.distance_pct, numeric, grid, track_length_m or self.metadata.get("track_length_m"), self.sample_time_s)

        time_values = interpolate(self.sample_time_s)
        if time_values is None:
            time_values = np.full(count, np.nan)
        output["time_s"] = time_values
        for name, values in self.channels.items():
            interpolated = interpolate_observed(self.distance_pct, values, grid, track_length_m or self.metadata.get("track_length_m"), self.sample_time_s, stepped=name in {"Gear", "BrakeABSactive"})
            if interpolated is not None:
                output[name] = interpolated
        if "Speed" in output:
            output["speed_kph"] = output["Speed"] * 3.6
        output["_coverage"] = np.isfinite(output["time_s"]) & np.isfinite(output.get("speed_kph", np.full(count, np.nan)))
        output["_channel_support"] = {key: np.isfinite(value) for key, value in output.items() if isinstance(value, np.ndarray) and key != "distance_pct" and not key.startswith("_")}
        return output
