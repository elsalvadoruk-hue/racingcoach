"""Pure numeric, sampling and serialization helpers shared by analysis and readers."""
from __future__ import annotations
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Iterable, Optional
import numpy as np


def interpolate_observed(distance: np.ndarray, values: np.ndarray, grid: np.ndarray, track_length_m: Optional[float] = None, sample_times: Optional[np.ndarray] = None, *, stepped: bool = False) -> np.ndarray:
    """Interpolate short observed intervals only; never manufacture missing ends."""
    output = np.full(len(grid), np.nan)
    distance, values = np.asarray(distance, dtype=float), np.asarray(values, dtype=float)
    valid = np.isfinite(distance) & np.isfinite(values)
    if np.count_nonzero(valid) < 2:
        return output
    x, y = distance[valid], values[valid]
    order = np.argsort(x)
    x, y = x[order], y[order]
    unique = np.r_[True, np.diff(x) > 1e-9]
    x, y = x[unique], y[unique]
    if len(x) < 2:
        return output
    left = np.searchsorted(x, grid, side="right") - 1
    exact = np.searchsorted(x, grid, side="left")
    exact = np.clip(exact, 0, len(x) - 1)
    exact_match = np.isclose(grid, x[exact], rtol=0, atol=1e-10)
    interval = np.clip(left, 0, len(x) - 2)
    in_range = (grid >= x[0]) & (grid <= x[-1])
    length = float(track_length_m or 5000.0)
    supported = in_range & ((x[interval + 1] - x[interval]) * length <= 25.0)
    if sample_times is not None:
        times = np.asarray(sample_times, dtype=float)[valid][order][unique]
        time_gap = times[interval + 1] - times[interval]
        supported &= np.isfinite(time_gap) & (time_gap >= 0) & (time_gap <= 0.5)
    values_on_grid = y[np.clip(left, 0, len(y) - 1)] if stepped else np.interp(grid, x, y)
    output[supported | exact_match] = values_on_grid[supported | exact_match]
    return output


def _weighted_quantile_stack(
    stack: np.ndarray[Any, Any],
    weights: np.ndarray[Any, Any],
    quantile: float,
) -> np.ndarray[Any, Any]:
    """Column-wise weighted quantile with NaN-safe reference weights."""

    values = np.asarray(stack, dtype=float)
    if values.ndim != 2 or values.shape[0] == 0:
        return np.asarray([], dtype=float)
    row_weights = np.asarray(weights, dtype=float).reshape(-1)
    if len(row_weights) != values.shape[0]:
        row_weights = np.ones(values.shape[0], dtype=float)
    row_weights = np.where(np.isfinite(row_weights) & (row_weights > 0), row_weights, 0.0)
    if float(np.sum(row_weights)) <= 0:
        row_weights = np.ones(values.shape[0], dtype=float)

    order = np.argsort(np.where(np.isfinite(values), values, np.inf), axis=0)
    sorted_values = np.take_along_axis(values, order, axis=0)
    weight_matrix = np.broadcast_to(row_weights[:, None], values.shape)
    sorted_weights = np.take_along_axis(weight_matrix, order, axis=0)
    sorted_weights = np.where(np.isfinite(sorted_values), sorted_weights, 0.0)
    cumulative = np.cumsum(sorted_weights, axis=0)
    totals = cumulative[-1]
    thresholds = totals * max(0.0, min(1.0, float(quantile)))
    hits = cumulative >= thresholds[None, :]
    indices = np.argmax(hits, axis=0)
    result = sorted_values[indices, np.arange(values.shape[1])]
    result = np.where(totals > 0, result, np.nan)
    return result


def comparison_input_channels(target: dict[str, Any], reference: Optional[dict[str, Any]], index: int) -> dict[str, Any]:
    def value(grid: Optional[dict[str, Any]], channel: str, digits: int, degrees: bool = False) -> Optional[float]:
        values = grid.get(channel) if grid is not None else None
        if values is None or index >= len(values):
            return None
        result = float(values[index])
        return round_or_none(math.degrees(result) if degrees else result, digits)

    output = {}
    for prefix, grid in (("target", target), ("reference", reference)):
        for name, channel, digits, degrees in (("brake", "Brake", 3, False), ("throttle", "Throttle", 3, False), ("steering_deg", "SteeringWheelAngle", 1, True), ("gear", "Gear", 0, False)):
            output[f"{prefix}_{name}"] = value(grid, channel, digits, degrees)
    output.update(brake=output["target_brake"], throttle=output["target_throttle"], steering_deg=output["target_steering_deg"])
    return output


def file_signature(path: Path) -> tuple[str, int, int]:
    stat = path.stat()
    return (str(path.resolve()), stat.st_size, stat.st_mtime_ns)


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_number(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    if isinstance(value, str):
        match = re.search(r"[-+]?\d+(?:\.\d+)?", value)
        if match:
            try:
                return float(match.group(0))
            except ValueError:
                return None
    return None


def regex_value(text: str, pattern: str) -> Optional[str]:
    match = re.search(pattern, text)
    return match.group(1).strip() if match else None


def round_or_none(value: Optional[float], digits: int = 3) -> Optional[float]:
    if value is None or not math.isfinite(value):
        return None
    return round(float(value), digits)


def format_lap_time(seconds: Optional[float]) -> Optional[str]:
    if seconds is None or not math.isfinite(seconds):
        return None
    minutes = int(seconds // 60)
    remainder = seconds - minutes * 60
    return f"{minutes}:{remainder:06.3f}"


def smooth(values: np.ndarray[Any, Any], window: int) -> np.ndarray[Any, Any]:
    if window <= 1 or len(values) < 3:
        return values.astype(float, copy=True)
    window = min(window, len(values) if len(values) % 2 else len(values) - 1)
    window = max(3, window | 1)
    pad = window // 2
    padded = np.pad(values.astype(float), (pad, pad), mode="edge")
    kernel = np.ones(window, dtype=float) / window
    return np.convolve(padded, kernel, mode="valid")


def contiguous_groups(indices: np.ndarray[Any, Any], max_gap: int = 1, min_len: int = 1) -> list[tuple[int, int]]:
    if len(indices) == 0:
        return []
    groups: list[tuple[int, int]] = []
    start = previous = int(indices[0])
    for raw in indices[1:]:
        current = int(raw)
        if current - previous <= max_gap:
            previous = current
            continue
        if previous - start + 1 >= min_len:
            groups.append((start, previous))
        start = previous = current
    if previous - start + 1 >= min_len:
        groups.append((start, previous))
    return groups


def nearest_index(grid: np.ndarray[Any, Any], value: float) -> int:
    return int(np.clip(np.searchsorted(grid, value), 0, len(grid) - 1))


def downsample_indices(length: int, points: int) -> np.ndarray[Any, Any]:
    if length <= points:
        return np.arange(length, dtype=int)
    return np.unique(np.linspace(0, length - 1, points, dtype=int))


def visualization_indices(target: dict[str, Any], reference: Optional[dict[str, Any]], points: int, start: int = 0, end: Optional[int] = None) -> np.ndarray:
    """Keep state/input events and extrema in addition to evenly spaced points."""
    end = len(target["distance_pct"]) - 1 if end is None else end
    length = end - start + 1
    if length <= points:
        return np.arange(start, end + 1, dtype=int)
    selected = set((downsample_indices(length, points) + start).tolist())
    bucket_edges = np.linspace(start, end + 1, max(12, points // 8) + 1, dtype=int)
    for grid in (target, reference):
        if grid is None:
            continue
        for key in ("speed_kph", "Brake", "Throttle", "SteeringWheelAngle", "Gear", "BrakeABSactive", "time_s"):
            if key not in grid:
                continue
            values = np.asarray(grid[key], dtype=float)
            valid = np.isfinite(values[start:end + 1])
            gaps = np.flatnonzero(valid[1:] != valid[:-1]) + start
            for index in gaps:
                selected.update((int(index), int(index + 1)))
            if key in {"Gear", "BrakeABSactive"}:
                changes = np.flatnonzero((values[start + 1:end + 1] != values[start:end]) & valid[1:] & valid[:-1]) + start
                for index in changes:
                    selected.update((int(index), int(index + 1)))
            elif key in {"Brake", "Throttle"}:
                for threshold in (0.05, 0.08, 0.2, 0.95):
                    changes = np.flatnonzero((values[start + 1:end + 1] >= threshold) != (values[start:end] >= threshold)) + start
                    for index in changes:
                        selected.update((int(index), int(index + 1)))
            if key != "time_s":
                for left, right in zip(bucket_edges[:-1], bucket_edges[1:]):
                    segment = values[left:right]
                    if len(segment) and np.any(np.isfinite(segment)):
                        selected.update((left + int(np.nanargmin(segment)), left + int(np.nanargmax(segment))))
    return np.asarray(sorted(selected), dtype=int)


def json_safe(value: Any) -> Any:
    # Most visualization values are plain floats. Avoid repeated container and
    # NumPy checks for them, while preserving subclass/NumPy scalar behavior.
    kind = type(value)
    if kind is float:
        return value if math.isfinite(value) else None
    if kind is str or kind is int or kind is bool or value is None:
        return value
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, np.ndarray):
        return [json_safe(item) for item in value.tolist()]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


def pct_distance(target_pct: Optional[float], reference_pct: Optional[float], length_m: float) -> Optional[float]:
    if target_pct is None or reference_pct is None:
        return None
    return (target_pct - reference_pct) * length_m


def finite_values(values: Iterable[Optional[float]]) -> list[float]:
    return [float(value) for value in values if value is not None and math.isfinite(float(value))]


def mean_finite(values: Iterable[Optional[float]]) -> Optional[float]:
    finite = finite_values(values)
    return sum(finite) / len(finite) if finite else None


def min_finite(values: Iterable[Optional[float]]) -> float:
    finite = finite_values(values)
    return min(finite) if finite else math.nan


def max_finite(values: Iterable[Optional[float]]) -> float:
    finite = finite_values(values)
    return max(finite) if finite else math.nan
