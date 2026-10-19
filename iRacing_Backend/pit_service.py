"""Whether a series changes tyres while refuelling (concurrent) or before/after it (sequential), read from
the stops in a drive file. iRacing does not say which rule a series uses (session info has no such field);
it shows only in how a stop went: PitSvFlags' four tyre bits (1, 2, 4, 8) are requested on arrival and
cleared as the tyres are done, FuelLevel rises while fuel goes in.

Seen in the driver's files (2026-10-16):
- Nürburgring 4 h (series 275): fuel 1.0 -> 116.6 s, every tyre bit cleared by 11.2 s  -> concurrent.
- Adelaide 3 h (series 237): fuel 0.7 -> 39.5 s, tyre bits clear from 41.1 s to 56.2 s -> sequential.
Only stops that asked for all four tyres and took at least MIN_FUEL_L count; anything else says nothing.
"""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

MIN_FUEL_L = 5.0          # a stop with less fuel cannot show whether the two overlap
MIN_FUEL_S = 3.0
EDGE_S = 1.0              # tolerance at the ends of the fuelling window
TYRE_BITS = 0xF


def stop_kind(time_s: np.ndarray, fuel_l: np.ndarray, flags: np.ndarray) -> Optional[dict[str, Any]]:
    """One stall stop (samples from arriving in the box to leaving it): CONCURRENT, SEQUENTIAL or None."""
    if len(time_s) < 3:
        return None
    t = np.asarray(time_s, dtype=float) - float(time_s[0])
    fuel = np.asarray(fuel_l, dtype=float)
    bits = np.asarray(flags, dtype=np.int64) & TYRE_BITS
    if not np.all(np.isfinite(fuel)) or int(bits[0]) != TYRE_BITS:
        return None
    gain = float(np.nanmax(fuel) - fuel[0])
    rising = np.flatnonzero(fuel > fuel[0] + 0.3)
    if gain < MIN_FUEL_L or not len(rising):
        return None
    fuel_start = float(t[rising[0]])
    fuel_end = float(t[np.flatnonzero(fuel >= np.nanmax(fuel) - 0.3)[0]])
    if fuel_end - fuel_start < MIN_FUEL_S:
        return None
    cleared = np.flatnonzero(bits < TYRE_BITS)
    done = np.flatnonzero(bits == 0)
    if not len(cleared) or not len(done):
        return None                                   # the tyres were never finished in the box
    first_clear, all_clear = float(t[cleared[0]]), float(t[done[0]])
    if all_clear <= fuel_start + EDGE_S or first_clear >= fuel_end - EDGE_S:
        kind = "SEQUENTIAL"                            # tyres wholly before the fuel, or wholly after it
    elif all_clear < fuel_end - EDGE_S:
        kind = "CONCURRENT"                            # every tyre done while fuel was still going in
    else:
        return None                                   # overlapping but ending together: no clear answer
    return {"kind": kind, "fuel_l": round(gain, 1), "fuel_start_s": round(fuel_start, 1), "fuel_end_s": round(fuel_end, 1),
            "tyres_first_s": round(first_clear, 1), "tyres_done_s": round(all_clear, 1), "stall_s": round(float(t[-1]), 1)}


def detect(channels: dict[str, Any]) -> dict[str, Any]:
    """Every usable stop of a drive file and the rule they agree on (None when there is none or they differ)."""
    needed = ("SessionTime", "PlayerCarInPitStall", "FuelLevel", "PitSvFlags")
    if any(name not in channels for name in needed):
        return {"rule": None, "stops": [], "reason": "NO_CHANNELS"}
    stall = np.asarray(channels["PlayerCarInPitStall"], dtype=float) > 0.5
    edges = np.flatnonzero(np.diff(np.r_[0, stall.astype(int), 0]))
    stops = []
    for start, end in zip(edges[::2], edges[1::2]):
        kind = stop_kind(channels["SessionTime"][start:end], channels["FuelLevel"][start:end], channels["PitSvFlags"][start:end])
        if kind:
            stops.append(kind)
    kinds = {stop["kind"] for stop in stops}
    if not stops:
        return {"rule": None, "stops": [], "reason": "NO_FUEL_AND_TYRE_STOP"}
    if len(kinds) > 1:
        return {"rule": None, "stops": stops, "reason": "STOPS_DISAGREE"}
    return {"rule": kinds.pop(), "stops": stops, "reason": "MEASURED"}
