"""Game-neutral analysis inputs. No SDK, application state, or persistence."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import numpy as np


@dataclass(frozen=True)
class ChannelTrace:
    source_name: str
    unit: str
    frequency_hz: float
    time_s: np.ndarray
    values: np.ndarray
    valid_mask: np.ndarray
    interpolation: str = "LINEAR"

    def __post_init__(self):
        times, values, mask = (np.array(x, copy=True) for x in
                               (self.time_s, self.values, self.valid_mask))
        times, values, mask = times.astype(float), values.astype(float), mask.astype(bool)
        if (times.ndim != 1 or values.shape != times.shape or mask.shape != times.shape or
                not np.all(np.isfinite(times)) or np.any(np.diff(times) <= 0) or
                not np.isfinite(self.frequency_hz) or self.frequency_hz <= 0 or
                self.interpolation not in {"LINEAR", "STEP"}):
            raise ValueError("채널의 시간축·샘플 형태·보간 방식이 올바르지 않습니다.")
        mask &= np.isfinite(values)
        for name, value in (("time_s", times), ("values", values), ("valid_mask", mask)):
            value.setflags(write=False)
            object.__setattr__(self, name, value)

    def at(self, times) -> np.ndarray:
        """No extrapolation, invalid-sample bridging, or interpolation over gaps."""
        original_shape = np.asarray(times).shape
        query = np.asarray(times, dtype=float).reshape(-1)
        out = np.full(query.shape, np.nan)
        if not len(self.time_s):
            return out.reshape(original_shape)
        right = np.searchsorted(self.time_s, query, side="left")
        clipped = np.minimum(right, len(self.time_s) - 1)
        exact = np.isfinite(query) & (query == self.time_s[clipped])
        exact_valid = exact & self.valid_mask[clipped]
        out[exact_valid] = self.values[clipped[exact_valid]]
        between = np.isfinite(query) & ~exact & (right > 0) & (right < len(self.time_s))
        where = np.flatnonzero(between)
        if len(where):
            upper = right[where]
            lower = upper - 1
            supported = (self.valid_mask[lower] & self.valid_mask[upper] &
                         (self.time_s[upper] - self.time_s[lower] <= 1.5 / self.frequency_hz + 1e-9))
            where, lower, upper = where[supported], lower[supported], upper[supported]
            if self.interpolation == "STEP":
                out[where] = self.values[lower]
            else:
                fraction = (query[where] - self.time_s[lower]) / (self.time_s[upper] - self.time_s[lower])
                out[where] = self.values[lower] + fraction * (self.values[upper] - self.values[lower])
        return out.reshape(original_shape)


@dataclass(frozen=True)
class AnalysisLap:
    provider: str
    lap_number: int
    start_time_s: float
    end_time_s: float
    recorded_lap_time_s: float | None
    valid: bool | None
    diagnostic_status: str
    metadata: dict[str, Any]
    channels: dict[str, ChannelTrace]
    support: dict[str, dict[str, Any]]
    schema_version: int = 1


def input_zones(lap: AnalysisLap, key: str, on: float, off: float,
                minimum_duration_s: float = .15) -> list[dict[str, Any]]:
    """Hysteresis crossings on native input samples, preserving partial runs."""
    trace = lap.channels.get(key)
    distance, speed = lap.channels.get("distance_m"), lap.channels.get("speed_mps")
    if trace is None or distance is None:
        return []
    zones = []
    start = None
    first_index = 0
    start_clipped = False

    def append(end, last_index, end_clipped):
        if start is None or end - start < minimum_duration_s:
            return
        times = np.r_[start, trace.time_s[(trace.time_s > start) & (trace.time_s < end)], end]
        values = trace.at(times)
        # A partial run may end on a supported sample immediately before a gap.
        peak = float(np.max(trace.values[first_index:last_index + 1]))
        positions = distance.at(np.asarray([start, end]))
        supported = bool(np.all(np.isfinite(positions)) and np.all(np.isfinite(distance.at(times))))
        status = "COMPLETE" if supported and not start_clipped and not end_clipped else "PARTIAL"
        speed_values = speed.at(np.asarray([start, end])) * 3.6 if speed else np.full(2, np.nan)
        number = lambda x: float(x) if np.isfinite(x) else None
        mean = None
        if len(times) >= 2 and np.all(np.isfinite(values)):
            mean = float(np.sum((values[1:] + values[:-1]) * np.diff(times) / 2) / (end - start))
        zones.append({"number": len(zones) + 1, "status": status,
                      "start_elapsed_s": float(start - lap.start_time_s),
                      "end_elapsed_s": float(end - lap.start_time_s),
                      "duration_s": float(end - start),
                      "start_distance_m": number(positions[0]), "end_distance_m": number(positions[1]),
                      "distance_m": number(positions[1] - positions[0]) if supported else None,
                      "entry_speed_kmh": number(speed_values[0]), "exit_speed_kmh": number(speed_values[1]),
                      "speed_change_kmh": number(speed_values[1] - speed_values[0]),
                      "peak_input_ratio": peak, "time_mean_input_ratio": mean})

    for i, (time, value, valid) in enumerate(zip(trace.time_s, trace.values, trace.valid_mask)):
        previous_supported = (i > 0 and trace.valid_mask[i - 1] and
                              time - trace.time_s[i - 1] <= 1.5 / trace.frequency_hz + 1e-9)
        if start is not None and (not valid or not previous_supported):
            append(float(trace.time_s[i - 1]), i - 1, True)
            start = None
        if not valid:
            continue
        if start is None and value >= on:
            first_index = i
            start_clipped = not previous_supported
            start = float(time)
            if previous_supported and trace.values[i - 1] < on:
                start = float(trace.time_s[i - 1] + (time - trace.time_s[i - 1]) *
                              (on - trace.values[i - 1]) / (value - trace.values[i - 1]))
            elif previous_supported:
                start_clipped = True
        elif start is not None and value <= off:
            end = float(trace.time_s[i - 1] + (time - trace.time_s[i - 1]) *
                        (off - trace.values[i - 1]) / (value - trace.values[i - 1]))
            append(end, i, False)
            start = None
    if start is not None:
        append(float(trace.time_s[-1]), len(trace.time_s) - 1, True)
    return zones
