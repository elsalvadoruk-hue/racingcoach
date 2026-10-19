"""Measured GPS paths for analysis visualization."""
from __future__ import annotations
import math
from typing import Any, Optional
import numpy as np
from analysis_numeric import round_or_none


def gps_path(grid: Optional[dict[str, Any]], start: int = 0, end: Optional[int] = None) -> list[dict[str, Any]]:
    """Observed GPS in the common geographic frame; nulls keep gaps unconnected.

    Keep the analysis grid for geometry: pedal-based decimation can cut a corner.
    Never subtract an offset or independently aggregate latitude and longitude.
    """
    if grid is None or "Lat" not in grid or "Lon" not in grid:
        return []
    stop = len(grid["distance_pct"]) if end is None else end + 1
    times = grid.get("time_s")
    path = []
    for index in range(start, stop):
        lat, lon = float(grid["Lat"][index]), float(grid["Lon"][index])
        valid = math.isfinite(lat) and math.isfinite(lon) and abs(lat) > 1e-7 and abs(lon) > 1e-7
        # time since the lap's start line, for the map's ghost replay (None where not observed)
        seconds = float(times[index]) if times is not None else math.nan
        path.append({"lat": round(lat, 8) if valid else None,
                     "lon": round(lon, 8) if valid else None,
                     "distance_pct": round(float(grid["distance_pct"][index]), 7),
                     "time_s": round(seconds, 3) if math.isfinite(seconds) else None})
    return path
