"""Frozen LMU observations, compared without reopening baseline recordings."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import statistics
from types import SimpleNamespace

from analysis_bridge import corner_measurement
from lmu_comparison import _axis, compatibility
from lmu_consistency import INPUT_LABELS, corner_consistency
from lmu_corners import METHOD

SCHEMA_VERSION = 1
METADATA_KEYS = ('track_name', 'track_layout', 'car_name', 'car_class')
INPUT_KEYS = {'brake_start_m': 'brake_ratio', 'throttle_first_m': 'throttle_ratio',
              'full_throttle_m': 'throttle_ratio'}


def _contract():
    # A changed kernel must not silently compare against an older calculation.
    root = Path(__file__).parent
    return {name: hashlib.sha256((root/name).read_bytes()).hexdigest() for name in
            ('analysis_bridge.py', 'corner_measurements.py', 'telemetry_core.py', 'lmu_practice.py',
             'lmu_analysis.py', 'lmu_telemetry.py', 'lmu_consistency.py', 'lmu_comparison.py')}


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _sources(laps, sources):
    if not 3 <= len(laps) <= 6 or len(laps) != len(sources):
        raise ValueError('연습 비교에는 서로 다른 기록 랩 3~6개가 필요합니다.')
    identities = []
    for lap, source in zip(laps, sources):
        digest = source.get('sha256')
        wal = source.get('wal_sha256')
        if (not isinstance(digest, str) or len(digest) != 64 or
                any(c not in '0123456789abcdef' for c in digest) or
                (wal is not None and (not isinstance(wal, str) or len(wal) != 64 or
                                     any(c not in '0123456789abcdef' for c in wal))) or
                source.get('lap_number') != lap.lap_number):
            raise ValueError('기록 랩의 원본 해시와 랩 번호를 확인할 수 없습니다.')
        # A changed WAL never proves that the same numbered lap is a new drive.
        identities.append((digest, lap.lap_number))
    if len(set(identities)) != len(identities):
        raise ValueError('같은 원본의 같은 랩은 중복 표본으로 사용할 수 없습니다.')
    return identities


def _stages(laps):
    result = {}
    for channel in set(INPUT_KEYS.values()):
        stages = [lap.support.get(channel, {}).get('input_stage') for lap in laps]
        if any(s not in ('UNFILTERED', 'GAME_PROCESSED') for s in stages) or len(set(stages)) != 1:
            raise ValueError('입력 처리 단계가 미확인이거나 달라 연습 기록을 비교할 수 없습니다.')
        result[channel] = stages[0]
    return result


def create_practice_baseline(laps, sources):
    _sources(laps, sources)
    stages = _stages(laps)
    report = corner_consistency(laps)
    if report.get('status') not in ('AVAILABLE', 'PARTIAL') or not report.get('focus'):
        raise ValueError('반복성 관측 과제를 저장할 수 있는 입력 표본과 구간이 없습니다.')
    axis = _axis(laps[0])
    return deepcopy(report | {'schema_version': SCHEMA_VERSION, 'measurement_contract': _contract(),
                             'metadata': {k: laps[0].metadata.get(k) for k in METADATA_KEYS},
                             'input_stages': stages, 'sources': sources,
                             'distance_span_m': float(axis.values[-1]-axis.values[0])})


def _validate_baseline(baseline):
    if (not isinstance(baseline, dict) or type(baseline.get('schema_version')) is not int or
            baseline.get('schema_version') != SCHEMA_VERSION or
            baseline.get('measurement_contract') != _contract()):
        raise ValueError('저장된 연습 기록의 계산 버전이 현재와 달라 비교하지 않았습니다.')
    definition = baseline.get('definition') or {}
    if not isinstance(definition, dict) or definition.get('method') != METHOD:
        raise ValueError('지원하지 않는 저장 구간 정의입니다.')
    if not isinstance(baseline.get('metadata'), dict):
        raise ValueError('저장된 연습 기준의 메타데이터가 올바르지 않습니다.')
    bounds = definition.get('boundaries')
    if not isinstance(bounds, list) or not 1 <= len(bounds) <= 64:
        raise ValueError('저장 구간의 개수 또는 형식이 올바르지 않습니다.')
    ids, end = set(), -math.inf
    for b in bounds:
        if not isinstance(b, dict) or not isinstance(b.get('id'), str):
            raise ValueError('저장 구간의 형식이 올바르지 않습니다.')
        values = [b.get(k) for k in ('start_m', 'apex_m', 'end_m')]
        if not all(_finite(v) for v in values) or not end <= values[0] < values[1] < values[2] or b.get('id') in ids:
            raise ValueError('저장 구간의 경계 또는 식별자가 올바르지 않습니다.')
        end = values[-1]
        ids.add(b.get('id'))
    identity = json.dumps({'method': METHOD, 'boundaries': bounds,
                           'metadata': {k: baseline.get('metadata', {}).get(k) for k in
                                        ('track_name', 'track_layout')}}, sort_keys=True)
    if hashlib.sha256(identity.encode()).hexdigest()[:24] != definition.get('definition_id'):
        raise ValueError('저장된 구간 경계와 정의 식별자가 일치하지 않습니다.')
    rows = baseline.get('corners') or []
    if (not isinstance(rows, list) or len(rows) != len(ids) or
            any(not isinstance(r, dict) or not isinstance(r.get('input_consistency'), dict) for r in rows) or
            {row.get('id') for row in rows} != ids):
        raise ValueError('저장 구간과 기준 관측이 일치하지 않습니다.')
    if (not isinstance(baseline.get('metadata'), dict) or not isinstance(baseline.get('input_stages'), dict) or
            not _finite(baseline.get('distance_span_m')) or baseline['distance_span_m'] <= 0 or
            not isinstance(baseline.get('sources'), list) or not 3 <= len(baseline['sources']) <= 6 or
            any(not isinstance(s, dict) or any(k not in s for k in ('sha256', 'file_id', 'lap_number'))
                for s in baseline['sources'])):
        raise ValueError('저장된 연습 기준의 메타데이터 또는 원본 정보가 올바르지 않습니다.')
    return bounds


def compare_practice(baseline, laps, sources):
    bounds = _validate_baseline(baseline)
    identities = _sources(laps, sources)
    old = {(s['sha256'], s['lap_number']) for s in baseline['sources']}
    # File ID catches an append to an existing recording, digest catches a rename/copy.
    old_ids = {(s['file_id'], s['lap_number']) for s in baseline['sources']}
    if any(i in old or (s['file_id'], s['lap_number']) in old_ids for i, s in zip(identities, sources)):
        raise ValueError('기준에 사용한 랩이 포함되어 있습니다. 다음 주행의 새 랩 3~6개를 선택하세요.')
    if _stages(laps) != baseline['input_stages']:
        raise ValueError('저장한 기준과 입력 처리 단계가 달라 비교하지 않았습니다.')
    reference = SimpleNamespace(metadata=baseline['metadata'])
    for lap in laps:
        compatibility(reference, lap)
        axis = _axis(lap)
        span = axis.values[-1]-axis.values[0]
        if abs(span-baseline['distance_span_m']) > max(50, .01*max(span, baseline['distance_span_m'])):
            raise ValueError('기준과 기록 거리 범위가 달라 비교하지 않았습니다.')
    rows = []
    original = {r['id']: r for r in baseline['corners']}
    for b in bounds:
        measures = [corner_measurement(lap, b['start_m'], b['apex_m'], b['end_m']) for lap in laps]
        current = {}
        for key, label in INPUT_LABELS.items():
            observations = [{'selection_index': i, 'value_m': m['metrics'][key],
                             'active_at_center': m.get('boundary_observations', {}).get(key+'_active_at_center')}
                            for i, m in enumerate(measures) if _finite(m['metrics'].get(key))]
            values = [o['value_m'] for o in observations]
            info = {'status': 'INSUFFICIENT_PASSES', 'label': label, 'sample_count': len(values),
                    'median_m': None, 'min_m': None, 'max_m': None, 'spread_m': None,
                    'approximate': True, 'observations': observations}
            if len(values) >= 3:
                info.update(status='AVAILABLE', median_m=statistics.median(values), min_m=min(values),
                            max_m=max(values), spread_m=max(values)-min(values))
            current[key] = info
        previous = original[b['id']]['input_consistency']
        delta = {key: current[key]['spread_m']-previous[key]['spread_m']
                 if current[key]['status'] == previous[key]['status'] == 'AVAILABLE' else None for key in INPUT_KEYS}
        times = [m['metrics']['duration_s'] for m in measures if _finite(m['metrics'].get('duration_s'))]
        rows.append(b | {'baseline': previous, 'current': current, 'spread_delta_m': delta,
                         'baseline_median_duration_s': original[b['id']].get('median_duration_s'),
                         'current_median_duration_s': statistics.median(times) if len(times) >= 3 else None})
    supported = sum(v is not None for row in rows for v in row['spread_delta_m'].values())
    return {'schema_version': SCHEMA_VERSION, 'status': 'AVAILABLE' if supported == len(rows)*3 else 'PARTIAL' if supported else 'UNAVAILABLE',
            'definition': deepcopy(baseline['definition']), 'lap_count': len(laps), 'corners': rows,
            'sources': deepcopy(sources), 'focus': deepcopy(baseline['focus']), 'conditions': 'NOT_COMPARED',
            'reference_status': 'VALIDITY_UNVERIFIED', 'causal_effect': 'NOT_ESTABLISHED',
            'automatic_best_lap': None,
            'note': '음수는 관측 위치 범위가 줄었다는 뜻이며 주행 개선이나 인과 효과를 확정하지 않습니다.'}
