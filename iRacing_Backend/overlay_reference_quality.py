"""Admission checks for automatic HUD references, separate from offline analysis."""
import csv
import math

import numpy as np


def check_csv(path):
    """Check the original rows before the reader can discard invalid/reversed rows."""
    with path.open(encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or ())
        required = {'LapDistPct', 'Speed', 'Brake', 'Throttle'}
        if not required <= fields:
            raise ValueError('기준 CSV 필수 채널 누락')
        channels = sorted(required | ({'PositionType'} & fields))
        values = np.asarray([[float(row[key]) for key in channels] for row in reader], dtype=float)
    if len(values) < 250 or not np.isfinite(values).all():
        raise ValueError('기준 CSV 표본 누락')
    data = {key: values[:, i] for i, key in enumerate(channels)}
    distance = data['LapDistPct']
    if np.any((distance < 0) | (distance > 1)):
        raise ValueError('기준 CSV 위치 범위 오류')
    wraps = np.flatnonzero(np.diff(distance) < -.5)
    if len(wraps):
        end = int(wraps[0])+1
        # A few post-finish samples are normal in G61 exports. Another lap is not.
        if len(wraps) > 1 or distance[end-1] < .998 or np.max(distance[end:]) > .002:
            raise ValueError('기준 CSV 랩 경계 불명')
        data = {key: rows[:end] for key, rows in data.items()}
        distance = data['LapDistPct']
    if np.any(np.diff(distance) < -1e-6):
        raise ValueError('기준 CSV 역주행·위치 점프')
    for name in ('Brake', 'Throttle'):
        if np.any((data[name] < -.001) | (data[name] > 1.001)):
            raise ValueError('기준 CSV 페달 범위 오류')
    if 'PositionType' in data and np.any(data['PositionType'] != 3):
        raise ValueError('기준 CSV 피트·트랙 밖 표본')


def check_lap(lap, length):
    """Require a full, continuous flying lap with physically consistent distance."""
    distance = np.asarray(lap.distance_pct, dtype=float)
    times = np.asarray(lap.sample_time_s, dtype=float)
    speed = np.asarray(lap.channels['Speed'], dtype=float)
    duration = float(lap.metadata['lap_time_s'])
    if (len(distance) < 250 or not math.isfinite(duration) or duration <= 0
            or not np.isfinite(distance).all() or not np.isfinite(times).all()
            or not np.isfinite(speed).all()):
        raise ValueError('기준 랩 관측 부족')
    margin = min(.002, 25/length)
    if distance[0] > margin or distance[-1] < 1-margin or np.any(np.diff(distance) <= 0):
        raise ValueError('기준 랩 시작·끝 누락')
    dt = np.diff(times)
    if np.any(dt <= 0) or np.max(dt) > .5 or np.max(np.diff(distance))*length > 25:
        raise ValueError('기준 랩 시간·거리 단절')
    if not .98*duration <= times[-1]-times[0] <= 1.02*duration:
        raise ValueError('기준 랩 시간 불일치')
    if np.any((speed < 0) | (speed > 130)):
        raise ValueError('기준 랩 속도 범위 오류')
    slow = 0.
    longest_slow = 0.
    for seconds, a, b in zip(dt, speed[:-1], speed[1:]):
        slow = slow+seconds if min(a, b) < 3 else 0.
        longest_slow = max(longest_slow, slow)
    if longest_slow >= 1:
        raise ValueError('기준 랩 정지·장시간 저속 구간')
    measured_length = float(np.sum((speed[:-1]+speed[1:])*.5*dt))
    ratio = measured_length/(length*(distance[-1]-distance[0]))
    if not .9 <= ratio <= 1.1:
        raise ValueError('기준 랩 속도·거리·시간 불일치')
    for name in ('Brake', 'Throttle'):
        values = np.asarray(lap.channels[name], dtype=float)
        if not np.isfinite(values).all() or np.any((values < -.001) | (values > 1.001)):
            raise ValueError('기준 랩 페달 누락·범위 오류')
    return {'samples': len(distance), 'distance_coverage': float(distance[-1]-distance[0]),
            'max_sample_gap_s': float(np.max(dt)), 'travel_distance_ratio': ratio,
            'longest_low_speed_s': float(longest_slow)}
