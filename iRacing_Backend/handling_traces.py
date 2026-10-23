"""Small, replayable corner bundles for session-log spare space; no stores or SDK."""
from __future__ import annotations

import hashlib
import json
import lzma
import random
from types import SimpleNamespace

import numpy as np

from corner_events import raw_lap, _edges
from drive_summary import CORNER_KEYS

FORMAT, VERSION = 'iracing-coach-handling-traces', 1
MIN_LAPS, MAX_LAPS, MAX_CORNERS = 4, 8, 8
PADDING_S, MAX_PASS_SAMPLES = .5, 24_000
UNITS = {'Speed': 'm/s', 'SteeringWheelAngle': 'rad', 'Brake': 'ratio', 'Throttle': 'ratio',
         'YawRate': 'rad/s', 'LatAccel': 'm/s^2', 'LongAccel': 'm/s^2', 'BrakeABSactive': 'bool'}
CORE = ('Speed', 'SteeringWheelAngle', 'Brake', 'Throttle', 'YawRate', 'LatAccel')


def _values(values):
    # Keep recorded floating-point precision and nulls; never close gaps by resampling.
    return [float(v) if np.isfinite(v) else None for v in values]


def groups(analyzer, summary, seed):
    """Whole corners, evenly spaced valid laps, independent of the current detector's flags.

    These are a subset for future experiments, not a replay of the original full-file
    classifier (its speed bands and reference pool may use other laps and corners).
    """
    laps = list(analyzer.valid_laps)
    if len(laps) < MIN_LAPS:
        return
    selected = np.linspace(0, len(laps) - 1, min(MAX_LAPS, len(laps)), dtype=int)
    source = SimpleNamespace(channels={k: v for k, v in analyzer.channels.items()
                                      if k in (*UNITS, 'SessionTime', 'LapDistPct')},
                             telemetry=analyzer.telemetry, track_length_m=analyzer.track_length_m)
    raws = [(laps[i], raw_lap(source, laps[i])) for i in selected]
    corners = [c for c in summary.get('corners', [])
               if all(isinstance(c.get(k), (int, float)) and np.isfinite(c[k])
                      for k in ('entry_pct', 'exit_pct'))
               and 0 <= c['entry_pct'] < c['exit_pct'] <= 1]
    rng = random.Random(int.from_bytes(hashlib.sha256(str(seed).encode()).digest()[:8], 'big'))
    rng.shuffle(corners)
    metrics = {(p['corner_id'], p['lap']): p for p in summary.get('corner_passes', [])}
    conditions = {p['lap']: p for p in summary.get('lap_conditions', [])}
    for corner in corners[:MAX_CORNERS]:
        passes = []
        for lap, raw in raws:
            if raw is None or not all(k in raw.channels for k in CORE) or not np.isfinite(raw.tick_s) or raw.tick_s <= 0:
                continue
            hits = np.flatnonzero(np.isfinite(raw.pct) & (raw.pct >= corner['entry_pct'])
                                  & (raw.pct <= corner['exit_pct']))
            if len(hits) < 2 or not len(raw.time_s) or not np.isfinite(raw.time_s[0]):
                continue
            pad = int(np.ceil(PADDING_S / raw.tick_s)) + 1
            start, end = max(0, hits[0] - pad), min(len(raw.pct), hits[-1] + pad + 1)
            if end - start > MAX_PASS_SAMPLES:
                continue
            span = slice(start, end)
            columns = {k: _values(raw.channels[k][span]) if k in raw.channels else None for k in UNITS}
            lmu = analyzer.telemetry.metadata.get('game') == 'lmu'
            provenance = {k: ('UNAVAILABLE' if columns[k] is None else
                             'DERIVED_FROM_GPS_SPEED_RESAMPLED' if lmu and k in ('YawRate', 'LatAccel', 'LongAccel') else
                             'SCALED_STEERING_RESAMPLED' if lmu and k == 'SteeringWheelAngle' else
                             'RESAMPLED' if lmu else 'RECORDED') for k in UNITS}
            passes.append({
                'lap': lap.lap_number, 'lap_time_s': lap.lap_time_s, 'source': raw.source,
                'tick_s': raw.tick_s, 'sample_start': int(start), 'samples': int(end - start),
                'time_s': _values(raw.time_s[span] - raw.time_s[0]),
                'distance_pct': _values(raw.pct[span]),
                'connected': _edges(raw)[start:end - 1].tolist(),
                'channels': columns,
                'channel_provenance': provenance,
                'conditions': conditions.get(lap.lap_number),
                'measurement': metrics.get((corner['id'], lap.lap_number)),
                'observations': [r for r in summary.get('handling_observations', [])
                                 if r.get('corner_id') == corner['id'] and r.get('lap') == lap.lap_number],
            })
        if len(passes) >= MIN_LAPS:
            yield {
                'format': FORMAT, 'version': VERSION, 'units': UNITS,
                'time_origin': 'lap_start', 'distance_unit': 'lap_fraction',
                'selection': 'seeded_corners_evenly_spaced_valid_laps_without_event_filter',
                'valid_laps_in_recording': len(laps), 'padding_s': PADDING_S,
                'full_recording_replay': False,
                'recording': summary.get('recording'), 'environment': summary.get('environment'),
                'data_quality': summary.get('data_quality'), 'handling_context': summary.get('handling'),
                'corner': {k: corner.get(k) for k in CORNER_KEYS}, 'passes': passes,
            }


def pack(document):
    return lzma.compress(json.dumps(document, ensure_ascii=False, allow_nan=False,
                                    separators=(',', ':')).encode('utf-8'), preset=6)


def unpack(data):
    return json.loads(lzma.decompress(data).decode('utf-8'))
