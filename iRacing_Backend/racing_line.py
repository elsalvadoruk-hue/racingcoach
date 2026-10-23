"""Observed racing-line offsets in one geographic frame, without alignment shifts.

Projection is left-positive internally. With an observed corner turn direction,
public offsets are inside-negative/outside-positive; without observed yaw the
offsets stay unavailable rather than changing their sign convention.
"""
from __future__ import annotations

import math
import numpy as np

EARTH_R_M = 6371000.0
WINDOW_M = 20.0
MARGIN_M = 60.0
ALIGNMENT_LIMIT_M = 1.5


def _empty(alignment=None):
    return {'entry_m': None, 'apex_m': None, 'exit_m': None,
            'side_words': {}, 'side_basis': None, 'path_length_delta_m': None,
            'quality': 'low', 'alignment_m': alignment}


def _axis(grid, length):
    if grid is None:
        return None
    try:
        axis = np.asarray(grid['distance_pct'], dtype=float) * length
        if axis.ndim != 1 or len(axis) < 3 or not np.all(np.isfinite(axis)) or np.any(np.diff(axis) <= 0):
            return None
        lat, lon = (np.asarray(grid[key], dtype=float) for key in ('Lat', 'Lon'))
        if lat.shape != axis.shape or lon.shape != axis.shape:
            return None
        valid = (np.isfinite(lat) & np.isfinite(lon) & (np.abs(lat) > 1e-7)
                 & (np.abs(lon) > 1e-7) & (np.abs(lat) <= 90) & (np.abs(lon) <= 180))
        for key in ('_coverage', '_analysis_coverage'):
            if key in grid:
                mask = np.asarray(grid[key], dtype=bool)
                if mask.shape != valid.shape:
                    return None
                valid &= mask
        return axis, lat, lon, valid
    except (KeyError, TypeError, ValueError):
        return None


def _section(path, start, end):
    """Clip the observed polyline, never extrapolate or join across missing GPS."""
    axis, xy, valid = path
    if not math.isfinite(start) or not math.isfinite(end) or start > end or start < axis[0] or end > axis[-1]:
        return None
    first = max(0, int(np.searchsorted(axis, start, side='right')) - 1)
    last = min(len(axis) - 1, int(np.searchsorted(axis, end, side='left')))
    if not np.all(valid[first:last + 1]):
        return None
    # Resampling cannot turn a large unobserved interval into a segment.
    if np.any(np.diff(axis[first:last + 1]) > WINDOW_M):
        return None
    observed_axis = axis[first:last + 1]
    observed_xy = xy[first:last + 1]
    inside = (observed_axis > start) & (observed_axis < end)
    positions = np.r_[start, observed_axis[inside], end] if end > start else np.array([start])
    points = np.column_stack([np.interp(positions, observed_axis, observed_xy[:, dim]) for dim in range(2)])
    return positions, points


def _project(path, position, point, bounds=None):
    axis = path[0]
    lower, upper = (axis[0], axis[-1]) if bounds is None else bounds
    clipped = _section(path, max(lower, position - WINDOW_M), min(upper, position + WINDOW_M))
    if clipped is None or len(clipped[1]) < 2:
        return None
    _, points = clipped
    vectors = np.diff(points, axis=0)
    norm2 = np.sum(vectors * vectors, axis=1)
    usable = norm2 > 1e-10
    if not np.any(usable):
        return None
    vectors, starts, norm2 = vectors[usable], points[:-1][usable], norm2[usable]
    fraction = np.clip(np.sum((point - starts) * vectors, axis=1) / norm2, 0, 1)
    residual = point - (starts + vectors * fraction[:, None])
    chosen = int(np.argmin(np.sum(residual * residual, axis=1)))
    cross = vectors[chosen, 0] * residual[chosen, 1] - vectors[chosen, 1] * residual[chosen, 0]
    return float(cross / math.sqrt(norm2[chosen]))


def _supported_sections(path, starts, ends):
    """Check independent clips in bulk, retaining _section's boundary rules."""
    axis, _, valid = path
    first = np.clip(np.searchsorted(axis, starts, side='right') - 1, 0, len(axis) - 1)
    last = np.clip(np.searchsorted(axis, ends, side='left'), 0, len(axis) - 1)
    missing = np.r_[0, np.cumsum(~valid)]
    gaps = np.r_[0, np.cumsum(np.diff(axis) > WINDOW_M)]
    supported = ((starts >= axis[0]) & (ends <= axis[-1]) & (starts <= ends)
                 & (missing[last + 1] == missing[first]) & (gaps[last] == gaps[first]))
    return first, last, supported


def _alignment_samples(target, reference, positions):
    """Project every straight sample using bounded batches of local segments.

    This is the same clipped-polyline projection as _section/_project. The
    masks reject each whole window before projection; neither missing GPS nor
    a large observation gap becomes a new connecting segment.
    """
    if not len(positions):
        return positions, np.empty(0)
    _, _, supported = _supported_sections(target, positions, positions)
    axis, xy, _ = reference
    lower, upper = np.maximum(axis[0], positions - WINDOW_M), np.minimum(axis[-1], positions + WINDOW_M)
    first, last, reference_supported = _supported_sections(reference, lower, upper)
    selected = supported & reference_supported & (last > first)
    positions, lower, upper, first, last = (values[selected] for values in (positions, lower, upper, first, last))
    if not len(positions):
        return positions, np.empty(0)
    points = np.column_stack([np.interp(positions, target[0], target[1][:, dim]) for dim in range(2)])
    selected_positions, offsets = [], []
    # Also bound temporary arrays for unusually dense observed axes.
    width = int(np.max(last - first))
    batch_size = max(1, min(256, 65536 // width))
    for start in range(0, len(positions), batch_size):
        end = min(len(positions), start + batch_size)
        indices = first[start:end, None] + np.arange(width)
        present = indices < last[start:end, None]
        indices = np.minimum(indices, last[start:end, None] - 1)
        segment_starts = np.maximum(axis[indices], lower[start:end, None])
        segment_ends = np.minimum(axis[indices + 1], upper[start:end, None])
        starts = np.stack([np.interp(segment_starts, axis, xy[:, dim]) for dim in range(2)], axis=-1)
        ends = np.stack([np.interp(segment_ends, axis, xy[:, dim]) for dim in range(2)], axis=-1)
        vectors = ends - starts
        norm2 = np.sum(vectors * vectors, axis=2)
        usable = present & (norm2 > 1e-10)
        point = points[start:end, None, :]
        fraction = np.clip(np.sum((point - starts) * vectors, axis=2) / np.where(usable, norm2, 1), 0, 1)
        residual = point - (starts + vectors * fraction[:, :, None])
        distances = np.where(usable, np.sum(residual * residual, axis=2), np.inf)
        chosen = np.argmin(distances, axis=1)
        rows = np.arange(end - start)
        keep = np.any(usable, axis=1)
        cross = (vectors[rows, chosen, 0] * residual[rows, chosen, 1]
                 - vectors[rows, chosen, 1] * residual[rows, chosen, 0])
        selected_norm = norm2[rows, chosen][keep]
        roots = np.fromiter((math.sqrt(value) for value in selected_norm), dtype=float, count=len(selected_norm))
        selected_positions.extend(positions[start:end][keep])
        offsets.extend(np.abs(cross[keep] / roots))
    return np.asarray(selected_positions), np.asarray(offsets)


def racing_line_context(target_grid, reference_grid, track_length_m, corner_ranges=()):
    """Prepare common coordinates and one lap-wide straight alignment check."""
    try:
        length = float(track_length_m)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(length) or length <= 0:
        return None
    if reference_grid is None:
        return None
    # Production lap grids carry provenance. Unmarked Garage61 aggregate grids
    # can contain independently median Lat/Lon and are not an observed lap.
    if target_grid.get('_source_file') and not reference_grid.get('_source_file'):
        return None
    target_game = target_grid.get('_game') or 'iracing'
    reference_game = reference_grid.get('_game') or 'iracing'
    if target_game != reference_game:
        return None
    if target_game == 'lmu' and (not target_grid.get('_source_file') or
                               target_grid.get('_source_file') != reference_grid.get('_source_file')):
        return None
    target, reference = (_axis(grid, length) for grid in (target_grid, reference_grid))
    if target is None or reference is None or not np.any(reference[3]):
        return None
    lat0 = float(np.median(reference[1][reference[3]]))
    lon0 = float(np.median(reference[2][reference[3]]))
    def geographic_path(source):
        axis, lat, lon, valid = source
        xy = np.column_stack([EARTH_R_M * math.cos(math.radians(lat0)) * np.radians(lon - lon0),
                              EARTH_R_M * np.radians(lat - lat0)])
        return axis, xy, valid
    target, reference = geographic_path(target), geographic_path(reference)
    axis, xy, valid = reference
    outside = valid.copy()
    for start, end in corner_ranges:
        outside &= (axis < start * length) | (axis > end * length)
    # Use every supported outside-corner sample; compute the median once per pair.
    positions, gaps = _alignment_samples(target, reference, axis[outside])
    alignment = float(np.median(gaps)) if len(gaps) >= 3 and positions[-1] - positions[0] >= 20 else None
    return {'target': target, 'reference': reference, 'alignment_m': alignment, 'length': length}


def racing_line_offsets(target_grid, reference_grid, corner, track_length_m, *, _context=None):
    """Measure target brake/minimum-speed/exit against nearby reference segments.

    Invalid/missing events stay None individually. Missing geometry, alignment,
    or corner coverage makes the whole comparison low quality with no values.
    """
    context = _context
    if context is None:
        context = racing_line_context(target_grid, reference_grid, track_length_m,
                                      [(corner.get('entry_pct', 0), corner.get('exit_pct', 1))])
    if context is None:
        return _empty()
    alignment = context['alignment_m']
    if alignment is None or alignment > ALIGNMENT_LIMIT_M or not corner.get('measurement_usable', True):
        return _empty(round(alignment, 3) if alignment is not None else None)
    length = context['length']
    try:
        entry, exit_position = (float(corner[key]) * length for key in ('entry_pct', 'exit_pct'))
    except (KeyError, TypeError, ValueError):
        return _empty(round(alignment, 3))
    if not math.isfinite(entry) or not math.isfinite(exit_position) or entry >= exit_position:
        return _empty(round(alignment, 3))
    target, reference = context['target'], context['reference']
    pieces = [_section(path, entry, exit_position) for path in (target, reference)]
    if any(piece is None for piece in pieces):
        return _empty(round(alignment, 3))
    path_lengths = [float(np.sum(np.linalg.norm(np.diff(piece[1], axis=0), axis=1))) for piece in pieces]
    bounds = (max(reference[0][0], entry - MARGIN_M), min(reference[0][-1], exit_position + MARGIN_M))
    direction = 0
    yaw = reference_grid.get('YawRate')
    if yaw is not None and np.asarray(yaw).shape == reference[0].shape:
        yaw = np.asarray(yaw, dtype=float)
        inside = (reference[0] >= entry) & (reference[0] <= exit_position)
        if np.all(np.isfinite(yaw[inside])) and np.any(inside):
            median_yaw = float(np.median(yaw[inside]))
            direction = int(np.sign(median_yaw))
    result = _empty(round(alignment, 3))
    result.update(quality='ok', path_length_delta_m=round(path_lengths[0] - path_lengths[1], 3),
                  side_basis='inside_outside' if direction else None)
    if not direction:
        result['reason'] = 'TURN_DIRECTION_UNKNOWN'
        return result
    metrics = corner.get('target') or {}
    for name, pct in (('entry', metrics.get('brake_start_pct')),
                      ('apex', metrics.get('min_speed_pct')), ('exit', corner.get('exit_pct'))):
        try:
            position = float(pct) * length
        except (TypeError, ValueError):
            continue
        if not math.isfinite(position) or position < bounds[0] or position > bounds[1]:
            continue
        point = _section(target, position, position)
        lateral = _project(reference, position, point[1][0], bounds) if point is not None else None
        if lateral is None:
            continue
        value = -direction * lateral
        result[name + '_m'] = round(value, 3)
        result['side_words'][name] = '안쪽' if value < 0 else '바깥'
    return result
