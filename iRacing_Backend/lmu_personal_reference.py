"""Observed LMU lap library; measurement eligibility never proves game validity."""
from pathlib import Path
import numpy as np


def reference_compatibility(current, reference):
    """LMU pace baseline: only separate observed rain/dry-wet groups.

    Missing weather remains unknown and is shown as a warning, but does not
    prevent using another recording. This never certifies setup equivalence.
    """
    from personal_reference import compatibility

    observed = compatibility(current, reference, same_file=True)
    groups = []
    for conditions, side in ((current, 'current'), (reference, 'reference')):
        surface = observed['surface'][side]
        rain = conditions.get('precipitation_pct')
        try:
            rain = float(rain)
        except (TypeError, ValueError):
            rain = float('nan')
        # Recorded rain takes precedence; absent rain falls back to the measured
        # dry/wet group. Different wetness levels are deliberately not compared.
        groups.append(('WET' if rain > 1 else 'DRY')
                      if np.isfinite(rain) and 0 <= rain <= 100 else surface)
    unknown = 'UNKNOWN' in groups
    mismatch = not unknown and groups[0] != groups[1]
    return {'compatible': not mismatch,
            'reasons': ['DRY_WET_MISMATCH'] if mismatch else [],
            'warnings': ['SURFACE_CONDITION_UNKNOWN'] if unknown else [],
            'surface': observed['surface'],
            'weather_group': {'current': groups[0], 'reference': groups[1]},
            'policy': 'RAIN_OR_SURFACE_ONLY',
            'status': 'INCOMPATIBLE' if mismatch else 'PARTIAL' if unknown else 'SIMILAR'}


def recording_conditions(reader, summary):
    """Read measured weather with explicit units, once for each recording."""
    rows = {lap.lap_number: {} for lap in summary.laps}
    specs = {spec.name.casefold(): spec for spec in summary.channels}
    gps = specs.get('gps time')
    origin = None
    if gps is not None and gps.unit == 's':
        values = reader.read_channel(gps.name)
        if len(values) and np.isfinite(values[0]):
            origin = values[0]
    if origin is not None:
        for channel, key in (('Track Temperature', 'track_temp_c'),
                             ('Ambient Temperature', 'air_temp_c')):
            spec = specs.get(channel.casefold())
            if spec is None or spec.unit not in ('C', 'degC', '°C'):
                continue
            values = reader.read_channel(spec.name)
            times = origin + np.arange(len(values)) / spec.frequency_hz
            for lap in summary.laps:
                selected = values[(times >= lap.start_time_s) & (times < lap.end_time_s)]
                selected = selected[np.isfinite(selected)]
                if len(selected):
                    rows[lap.lap_number][key] = float(np.median(selected))
    names = {name.casefold(): name for name in summary.events}
    for channel, key in (('Maximum Path Wetness (%)', 'track_wetness_pct'),
                         ('Rain Intensity (%)', 'precipitation_pct'),
                         ('Minimum Path Wetness (%)', 'minimum_path_wetness_pct')):
        name = names.get(channel.casefold())
        if name is None:
            continue
        events = reader.read_events(name)
        if events is None:
            continue
        times, values = events
        for lap in summary.laps:
            # Require an observation at/before the start; do not extend a
            # later dry event backwards over an unobserved section.
            before = np.flatnonzero(times <= lap.start_time_s)
            if not len(before):
                continue
            selected = values[(np.arange(len(times)) >= before[-1]) & (times < lap.end_time_s)]
            if not len(selected) or not np.all(np.isfinite(selected)) or np.any((selected < 0) | (selected > 100)):
                continue
            measured = float(np.max(selected))
            rows[lap.lap_number][key] = measured
            # A zero minimum says nothing about the rest of the road.
            if key == 'minimum_path_wetness_pct' and measured > 1:
                rows[lap.lap_number]['weather_declared_wet'] = True
    return rows


def select_reference(adapter, file_id, target_number):
    # Lazy imports preserve LMU's import isolation and avoid opening a DB until
    # the shared desktop explicitly requests automatic reference selection.
    from personal_reference import PersonalReferenceIndex, identity
    from lmu_workspace import _eligible

    index = getattr(adapter.services, 'personal_reference_index', None) or PersonalReferenceIndex()
    reader = adapter.reader(file_id)
    summary = reader.summary()
    metadata = {**summary.metadata, 'track_config': summary.metadata.get('track_layout', '')}
    key = identity(metadata)
    conditions = recording_conditions(reader, summary)
    current = conditions.get(target_number, {})
    indexed = {}
    for candidate_id, path in adapter.known.items():
        try:
            other = adapter.reader(candidate_id)
            other_summary = other.summary()
            if (str(other_summary.metadata.get('car_class', '')).strip().casefold()
                    != str(summary.metadata.get('car_class', '')).strip().casefold()):
                continue
            other_key = identity({**other_summary.metadata,
                                  'track_config': other_summary.metadata.get('track_layout', '')})
            if other_key != key:
                continue
            observed = conditions if candidate_id == file_id else recording_conditions(other, other_summary)
            # Recheck the snapshot before persisting metadata from that source.
            adapter.unchanged()
            signature = adapter.snapshots[path]
            index.index_recording('LMU', path, key, [
                {'lap': lap.lap_number, 'lap_time_s': lap.recorded_lap_time_s,
                 'eligible': _eligible(lap), 'conditions': observed.get(lap.lap_number, {})}
                for lap in other_summary.laps], expected_signature=(str(path.resolve()), signature[0], signature[1]))
            indexed[str(path.resolve())] = candidate_id
        except (OSError, ValueError, KeyError):
            continue
    for candidate in index.candidates('LMU', key):
        candidate_id = indexed.get(str(Path(candidate['path']).resolve()))
        if candidate_id is None:
            continue
        match = reference_compatibility(current, candidate['conditions'])
        if match['compatible']:
            return {'status': 'READY', 'selection': 'FASTEST_COMPATIBLE_PERSONAL_LAP',
                    'filename': candidate_id, 'lap': candidate['lap'],
                    'file_name': adapter.known[candidate_id].name,
                    'lap_time_s': candidate['lap_time_s'], 'conditions': match,
                    'same_lap': candidate_id == file_id and candidate['lap'] == target_number,
                    'validity': 'UNVERIFIED'}
    return {'status': 'UNAVAILABLE', 'reason': 'NO_COMPATIBLE_PERSONAL_LAP'}
