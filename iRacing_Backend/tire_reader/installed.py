"""Which cold pressure is actually on the car, and what iRacing measured at the last stop.

Evidence only, never an assumption:
- Leaving the garage: the garage setup's cold pressure is what was installed ('garage_start').
- A pit stop that changed a tire: PitSvFlags requested that tire on arrival and cleared it
  during the stop, or the stop reset its wear to new. That tire now has the pit service
  pressure that was set at the time ('pit_tire_change').
- A stop that did not change a tire keeps its previous value; a stop that cannot be judged
  (requested but never confirmed) leaves the tire unknown ('pit_unconfirmed').

Values are kPa. Nothing here reads the screen.
"""
from .sdk import SDK_WHEELS, finite
from .snapshot import WHEELS, bounded

TIRE_BITS = dict(zip(WHEELS, (0x01, 0x02, 0x04, 0x08)))  # PitSvFlags LF/RF/LR/RR tire change
NEW_WEAR = .98


def _pit_values(row):
    """iRacing's at-the-pits tire measurements: inner/middle/outer temperature and wear."""
    result = {}
    for w, sdk in zip(WHEELS, SDK_WHEELS):
        temps = [bounded(row.get(f'{sdk}tempC{side}'), -50, 250) for side in 'LMR']
        wear = [bounded(row.get(f'{sdk}wear{side}'), 0, 1) for side in 'LMR']
        result[w] = {'temp_lmr_c': temps, 'wear_lmr': wear}
    return result


def _garage_cold(row):
    values = {}
    for w, sdk in zip(WHEELS, SDK_WHEELS):
        value = bounded(row.get(sdk + 'coldPressure'), 40, 400)
        if value is None:
            value = bounded((row.get('garage_cold_pressure_kpa') or {}).get(w), 40, 400)
        values[w] = value
    return values


class InstalledPressure:
    def __init__(self):
        self.reset()

    def reset(self):
        self.identity = None
        self.installed = {w: None for w in WHEELS}
        self.source = {w: None for w in WHEELS}
        self.was_garage = False
        self.garage_cold = {w: None for w in WHEELS}
        self.last_pit_values = None
        self.stop = None          # state while in the pit stall
        self.pit_snapshot = None  # last completed stop
        self.stops = 0

    def update(self, row):
        """row: one SdkReader snapshot. Returns the fields added to the tire context."""
        identity = tuple(row.get(k) for k in ('generation', 'car_path', 'track_id', 'track_config',
                                               'session_id', 'subsession_id'))
        if identity != self.identity:
            self.reset()
            self.identity = identity
        garage = row.get('IsInGarage') in (True, 1)
        on_track = row.get('IsOnTrack') in (True, 1)
        if garage:
            # The setup can still change in the garage; the last value before leaving counts.
            self.garage_cold, self.was_garage = _garage_cold(row), True
        elif on_track and self.was_garage:
            for w in WHEELS:
                self.installed[w] = self.garage_cold[w]
                self.source[w] = 'garage_start' if self.garage_cold[w] is not None else None
            self.was_garage = False
        # Joining a session already on track gives no evidence: values stay unknown.
        self._pit(row)
        return {'installed_cold_kpa': dict(self.installed), 'installed_cold_source': dict(self.source),
                'pit_snapshot': self.pit_snapshot}

    def _pit(self, row):
        stall = row.get('PlayerCarInPitStall') in (True, 1)
        flags = row.get('PitSvFlags')
        flags = int(flags) if finite(flags) is not None else None
        pending = {w: bounded(row.get(f'PitSv{sdk}P'), 40, 400) for w, sdk in zip(WHEELS, SDK_WHEELS)}
        if stall:
            if self.stop is None:
                self.stop = {'requested': {w: flags is not None and bool(flags & bit) for w, bit in TIRE_BITS.items()},
                             'cleared': {w: False for w in WHEELS}, 'pending': dict(pending),
                             'before': self.last_pit_values,
                             't': finite(row.get('SessionTime'))}
            else:
                for w, bit in TIRE_BITS.items():
                    if flags is not None and self.stop['requested'][w] and not flags & bit:
                        self.stop['cleared'][w] = True
                    if pending[w] is not None and not self.stop['cleared'][w]:
                        # The pressure in effect when the tire was fitted.
                        self.stop['pending'][w] = pending[w]
        elif self.stop is not None:
            self._finish_stop(row)
        self.last_pit_values = _pit_values(row)

    def _finish_stop(self, row):
        stop, self.stop = self.stop, None
        after = _pit_values(row)
        changed = {}
        for w in WHEELS:
            wear = [v for v in after[w]['wear_lmr'] if v is not None]
            before = [v for v in ((stop['before'] or {}).get(w, {}).get('wear_lmr') or []) if v is not None]
            renewed = bool(wear and before and min(wear) >= NEW_WEAR and min(before) < NEW_WEAR)
            if stop['cleared'][w] or renewed:
                changed[w] = True
                self.installed[w] = stop['pending'][w]
                self.source[w] = 'pit_tire_change' if stop['pending'][w] is not None else 'pit_unconfirmed'
            elif stop['requested'][w]:
                changed[w] = None
                self.installed[w], self.source[w] = None, 'pit_unconfirmed'
            else:
                changed[w] = False
        self.stops += 1
        self.pit_snapshot = {'stop': self.stops, 't': finite(row.get('SessionTime')), 'wheels': after,
                             'tire_changed': changed}
