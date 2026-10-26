"""The driver's own drive as measured values, for the session-log upload.

No 60 Hz trace, file name, path or setup name: only what the analysis already computes —
each lap's time and validity, each corner's place, every valid lap's pass through every
corner (input points, speeds, ABS share, time) with that lap's conditions, and the per-lap
car-rotation observations of the handling comparison. These are what corner analysis and
the handling comparison are tuned on, so they let a driver's sessions improve them.
"""
from __future__ import annotations

import json
import lzma
import math

FORMAT, VERSION = 'iracing-coach-drive-summary', 1
RECORDING_KEYS = ('game', 'car_name', 'car_path', 'car_platform_id', 'track_name', 'track_path', 'track_platform_id',
                  'track_config', 'track_length_m', 'track_turns', 'session_type', 'session_duration_s',
                  'session_laps_limit', 'estimated_lap_time_s', 'setup_modified', 'setup_passed_tech', 'is_fixed_setup',
                  'weight_penalty_kg', 'power_adjust_pct', 'fuel_max_l', 'steering_scale',
                  'track_length_source', 'recorded_distance_max_m', 'steering_half_range_deg')
LAP_KEYS = ('lap_number', 'status', 'lap_time_s', 'start_pct', 'end_pct', 'on_track_ratio', 'fuel_used_l',
            'invalid_reason', 'delta_to_file_best_s')
CORNER_KEYS = ('id', 'label', 'official_turn_number', 'segment_source', 'entry_pct', 'apex_pct', 'exit_pct',
               'distance_m', 'speed_class')
OBSERVATION_KEYS = ('corner_id', 'phase', 'lap', 'time_s', 'observed', 'counter_observed', 'b_median', 'z_median',
                    'known_fraction', 'unknown_reasons')


def _clean(value, digits=6):
    """JSON-safe copy: non-finite numbers become null, floats keep a fixed precision."""
    if isinstance(value, bool) or value is None or isinstance(value, (int, str)):
        return value
    if isinstance(value, float):
        return round(value, digits) if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): _clean(item, digits) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item, digits) for item in value]
    try:
        number = float(value)          # numpy scalars
    except (TypeError, ValueError):
        return None
    return round(number, digits) if math.isfinite(number) else None


def compact(result, metadata, handling=None):
    """One recording's summary from an analysis result (TelemetryAnalyzer.analyze) and its metadata."""
    performance = result.get('performance') or {}
    recording = {key: metadata.get(key) for key in RECORDING_KEYS if key in metadata}
    environment = (result.get('session') or {}).get('environment')
    passes, conditions = [], {}
    for corner in (performance.get('corner_consistency') or {}).get('corners') or []:
        for item in corner.get('passes') or []:
            # Conditions belong to the lap; keep them once instead of once per corner.
            if item.get('conditions') is not None:
                conditions.setdefault(item.get('lap'), item['conditions'])
            passes.append({'corner_id': corner.get('id'), 'lap': item.get('lap'), 'time_s': item.get('time_s'),
                           'metrics': item.get('metrics')})
    observations = []
    for row in (handling or {}).get('observations') or []:
        entry = {key: row.get(key) for key in OBSERVATION_KEYS}
        entry['events'] = [{'kind': event.get('kind'), 'seconds': event.get('seconds'), 'with': event.get('with'),
                            'z_median': event.get('z_median')} for event in row.get('events') or []]
        observations.append(entry)
    summary = {
        'recording': recording,
        'environment': environment,
        'data_quality': result.get('data_quality'),
        'laps': [{key: lap.get(key) for key in LAP_KEYS} for lap in result.get('laps') or []],
        'corners': [{key: corner.get(key) for key in CORNER_KEYS} for corner in performance.get('corners') or []],
        'corner_passes': passes,
        'lap_conditions': [{'lap': lap, **values} for lap, values in sorted(conditions.items(), key=lambda kv: kv[0] or 0)],
        'handling': None if handling is None else {
            key: handling.get(key) for key in ('method', 'coverage', 'unknown_reasons', 'steer_lag_s', 'fast_steer_rate_rad_s',
                                               'coverage_rule', 'noise_rules', 'reference_rules', 'evidence', 'source')},
        'handling_observations': observations,
    }
    return _clean(summary)


def pack(recordings, app_version=''):
    """xz'd JSON of one session's recordings (a list of compact() results)."""
    document = {'format': FORMAT, 'version': VERSION, 'app_version': app_version, 'recordings': recordings}
    text = json.dumps(document, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    return lzma.compress(text.encode('utf-8'), preset=9)


def unpack(data):
    return json.loads(lzma.decompress(data).decode('utf-8'))
