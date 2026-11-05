"""Explicit per-car ranges. Temperature kinds are never mixed."""
from dataclasses import replace
import json
from pathlib import Path
from .positions import position_car
from .snapshot import bounded


class TireTargets:
    def __init__(self, path):
        self.path = Path(path)

    def _read(self):
        try:
            if self.path.stat().st_size > 65536:
                return {}
            data = json.loads(self.path.read_text(encoding='utf-8'))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def get(self, car):
        saved = self._read().get(car)
        if not isinstance(saved, dict):
            return {}
        result = {}
        for low, high, minimum, maximum in (('pressure_low_bar','pressure_high_bar',.4,4.),
                                           ('temp_low_c','temp_high_c',-50.,250.)):
            a, b = bounded(saved.get(low),minimum,maximum), bounded(saved.get(high),minimum,maximum)
            if a is not None and b is not None and a < b:
                result.update({low:a,high:b})
        if isinstance(saved.get('temperature_kind'), str):
            result['temperature_kind'] = saved['temperature_kind'][:32]
        return result

    def save(self, car, values):
        if not car or position_car(car) != car:
            raise ValueError('측정 중인 차량을 확인한 뒤 저장해 주세요.')
        saved = self._read()
        saved[car] = values
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        try:
            temporary.write_text(json.dumps(saved), encoding='utf-8')
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def apply(self, snapshot):
        ranges = self.get(snapshot.car)
        def state(value, low, high, cold, hot):
            return None if value is None or low is None or high is None else cold if value < low else hot if value > high else 'ok'
        wheels = {}
        for w, value in snapshot.wheels.items():
            temperature = None
            if value.temp_kind and value.temp_kind == ranges.get('temperature_kind'):
                temperature = state(value.temp_c,ranges.get('temp_low_c'),ranges.get('temp_high_c'),'cold','hot')
            wheels[w] = replace(value,
                pressure_state=value.pressure_state or state(
                    value.pressure_kpa / 100 if value.pressure_kpa is not None else None,
                    ranges.get('pressure_low_bar'),ranges.get('pressure_high_bar'),'low','high'),
                temp_state=value.temp_state or temperature)
        return replace(snapshot,wheels=wheels)
