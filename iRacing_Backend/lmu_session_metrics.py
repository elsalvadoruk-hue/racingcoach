"""Recorded times and native distance splits, independent of rendered points.

The split sum covers only the common observed distance. It is never presented
as an official sector result, a valid-lap record, or a full-lap prediction.
"""
from __future__ import annotations

import statistics

import numpy as np

from lmu_comparison import _axis


def session_metrics(laps, width_m=500.):
    times = [lap.recorded_lap_time_s for lap in laps]
    summary = {'count': len(times), 'fastest_recorded_time_s': min(times) if times else None,
               'median_time_s': statistics.median(times) if times else None,
               'spread_s': max(times)-min(times) if times else None,
               'stddev_s': statistics.pstdev(times) if len(times) >= 2 else None,
               'game_validity': 'UNVERIFIED'}
    splits = {'status': 'INSUFFICIENT_LAPS', 'reason': '구간 조합에는 측정 랩이 2개 이상 필요합니다.',
              'method': 'NATIVE_DISTANCE_SPLITS_V1', 'official_sectors': False,
              'full_lap_prediction': False, 'rows': [], 'observed_combined_minimum_s': None}
    if len(laps) < 2:
        return summary, splits
    try:
        axes = [_axis(lap) for lap in laps]
    except ValueError:
        return summary, splits | {'status': 'UNAVAILABLE', 'reason': '연속된 원본 거리 축이 없는 랩이 있습니다.'}
    low, high = max(float(a.values[0]) for a in axes), min(float(a.values[-1]) for a in axes)
    if high <= low or high-low > 100_000:
        return summary, splits | {'status': 'UNAVAILABLE', 'reason': '공통 관측 거리 범위를 확인하세요.'}
    edges = np.unique(np.r_[low, np.arange((int(low/width_m)+1)*width_m, high, width_m), high])
    durations = [np.diff(np.interp(edges, axis.values, axis.time_s)) for axis in axes]
    for index, (start, end) in enumerate(zip(edges[:-1], edges[1:])):
        passes = [{'lap_number': lap.lap_number, 'duration_s': float(values[index])}
                  for lap, values in zip(laps, durations)]
        fastest = min(passes, key=lambda row: row['duration_s'])
        splits['rows'].append({'start_m': float(start), 'end_m': float(end),
                               'minimum_s': fastest['duration_s'],
                               'source_lap_number': fastest['lap_number'], 'passes': passes})
    splits.update(status='OBSERVED_ONLY', reason='공통 관측 거리의 구간 조합입니다. 공식 섹터나 완주 예상 시간이 아닙니다.',
                  start_m=low, end_m=high, lap_count=len(laps),
                  observed_combined_minimum_s=sum(row['minimum_s'] for row in splits['rows']))
    return summary, splits
