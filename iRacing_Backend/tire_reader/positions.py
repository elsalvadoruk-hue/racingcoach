"""Independent per-car tire window positions, with legacy single-position import."""
import json
import re
from pathlib import Path
from .vehicle_dash import canonical_car


def position_car(car):
    if isinstance(car, str) and re.fullmatch(r'lmu-car-[a-z0-9]+(?:-[a-z0-9]+)*', car) and len(car) <= 180:
        return car
    return canonical_car(car)


def coordinates(value):
    if not isinstance(value, dict):
        return None
    x, y = value.get('x'), value.get('y')
    return (x, y) if all(type(v) is int and -50000 <= v <= 50000 for v in (x, y)) else None


class TirePositions:
    def __init__(self, path):
        self.path = Path(path)

    def _read(self):
        try:
            if self.path.stat().st_size > 16384:
                return {}
            data = json.loads(self.path.read_text(encoding='utf-8'))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def load(self, car=None):
        data = self._read()
        cars = data.get('cars', {})
        own = coordinates(cars.get(car)) if isinstance(cars, dict) else None
        return own or coordinates(data.get('default')) or coordinates(data)

    def _write(self, data):
        if not data.get('cars') and not data.get('default'):
            self.path.unlink(missing_ok=True)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        try:
            temporary.write_text(json.dumps(data), encoding='utf-8')
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def _normalized(self):
        data = self._read()
        default = coordinates(data.get('default')) or coordinates(data)
        saved = data.get('cars', {})
        cars = {key: value for key, value in saved.items() if position_car(key) == key and coordinates(value)} if isinstance(saved, dict) else {}
        return {'version': 2, 'default': dict(zip(('x', 'y'), default)) if default else None, 'cars': cars}

    def save(self, car, position):
        if car is not None and position_car(car) != car:
            raise ValueError('Invalid tire overlay car')
        if not isinstance(position, (tuple, list)) or len(position) != 2 or coordinates(dict(zip(('x', 'y'), position))) is None:
            raise ValueError('Invalid tire overlay position')
        data = self._normalized()
        target = dict(zip(('x', 'y'), position))
        if car is None:
            data['default'] = target
        else:
            data['cars'][car] = target
        self._write(data)

    def reset(self, car=None):
        data = self._normalized()
        if car is None:
            data['default'] = None
        else:
            data['cars'].pop(car, None)
        self._write(data)
