"""Recording selection policy derived only from already available metadata."""
from __future__ import annotations

import math
from numbers import Real


FILTER_POLICY = 'EXCLUDE_CONFIRMED_EMPTY_OR_NO_USABLE_LAPS'


def _count(value):
    return value if isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value) else None


def is_selectable_recording(info):
    """Unknown counts remain candidates; explicit analysis has its own policy."""
    if not info:
        return True
    if info.get('analyzable') is False or info.get('selectable') is False:
        return False
    if any((count := _count(info.get(key))) is not None and count <= 0
           for key in ('detected_laps', 'lap_count', 'complete_laps')):
        return False
    # Legacy LMU summaries expose measurable laps without an official verdict.
    usable = _count(info.get('measurement_laps'))
    if usable is None:
        usable = _count(info.get('valid_laps'))
    return usable is None or usable > 0
