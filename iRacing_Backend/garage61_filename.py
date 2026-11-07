"""Garage61 export filename identity shared by analysis and the overlay worker."""
from pathlib import Path
from typing import Any, Optional
import re

GARAGE61_FILENAME_RE = re.compile(
    r"^Garage 61 - (?P<driver>.+?) - (?P<car>.+?) - (?P<track>.+?) - "
    r"(?P<time>\d{2}\.\d{2}\.\d{3}) - (?P<lap_id>[^.]+)\.csv$",
    re.IGNORECASE,
)


def _garage61_lap_time_seconds(value: str) -> Optional[float]:
    parts = str(value or "").strip().split(".")
    if len(parts) != 3:
        return None
    try:
        minutes, seconds, milliseconds = (int(part) for part in parts)
    except ValueError:
        return None
    total = minutes * 60.0 + seconds + milliseconds / 1000.0
    return total if total > 0 else None


def _garage61_track_parts(value: str) -> tuple[str, str]:
    text = str(value or "").strip()
    match = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", text)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return text, ""


def garage61_filename_metadata(path: Path) -> Optional[dict[str, Any]]:
    match = GARAGE61_FILENAME_RE.match(path.name)
    if not match:
        return None
    lap_time_s = _garage61_lap_time_seconds(match.group("time"))
    if lap_time_s is None:
        return None
    track_name, track_config = _garage61_track_parts(match.group("track"))
    return {
        "name": path.name,
        "driver_name": match.group("driver").strip(),
        "car_name": match.group("car").strip(),
        "track_name": track_name,
        "track_config": track_config,
        "lap_time_s": lap_time_s,
        "lap_id": match.group("lap_id").strip(),
    }
