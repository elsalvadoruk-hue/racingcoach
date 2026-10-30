"""LMU native wheel observations. No optimum-temperature or grip inference."""
import math
from lmu_frame import _normalise_wheels


def wheel_view(sample, game):
    if game != 'lmu':
        return {'supported': False, 'available': False,
                'reason': 'iRacing 타이어 값은 실시간 예열 측정으로 사용하지 않습니다.', 'wheels': []}
    at, stamp = sample.get('session_time'), sample.get('lmu_wheel_sample_time')
    fresh = (type(at) in (int, float) and type(stamp) in (int, float)
             and math.isfinite(at) and math.isfinite(stamp) and 0 <= at - stamp <= 2
             and sample.get('on_track') is True and sample.get('is_replay') is False)
    rows = _normalise_wheels(sample.get('lmu_wheels')) if fresh else []
    known = any(any(v is not None for k, v in row.items() if k != 'position' and k != 'temperature_c')
                or any(v is not None for v in row['temperature_c']) for row in rows)
    return {'supported': True, 'available': known, 'source': 'LMU_TELEMETRY',
            'sample_time': stamp if fresh else None, 'age_s': at - stamp if fresh else None,
            'units': {'temperature': '°C', 'brake_temperature': '°C', 'pressure': 'kPa', 'wear': 'fraction'},
            'reason': None if known else '현재 바퀴 관측을 확인할 수 없습니다.', 'wheels': rows}
