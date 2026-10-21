"""LMU virtual energy, measured per lap the way fuel is (iRacing has none, so this stays empty there).

The live value is the remaining share of the stint allowance in percent (lmu_irsdk.virtual_energy_pct).
Only clean, continuous laps count: no pit road, no yellow, no refill, and at least 0.3 % used.
"""
from collections import deque
import math

YELLOW = 0xC108          # the same caution mask the fuel and sector timing use
MIN_USE_PCT = .3         # a lap that barely moved the value says nothing about the rate
MIN_LAPS = 3             # fewer clean laps: the rate is unknown and nothing is said
REFILL_PCT = 1.          # more than this back in the tank is a pit stop: a new stint


def _number(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


class EnergyWatch:
    def __init__(self):
        self.history = deque(maxlen=5)
        self.reset()

    def reset(self):
        self.history.clear()
        self.lap = None
        self.stint = 0
        self.counted = 0
        self.current = None

    def ingest(self, sample):
        pct, lap = _number(sample.get('virtual_energy_pct')), sample.get('lap_number')
        if pct is None or lap is None:
            self.lap = None
            return
        self.current = pct
        previous = self.lap
        continuous = previous is not None and 0 < sample['session_time'] - previous['at'] <= 2
        if previous and pct > previous['pct'] + REFILL_PCT:
            self.stint += 1
        if previous and lap != previous['lap']:
            use = previous['start'] - pct
            if (previous['clean'] and continuous and lap == previous['lap'] + 1
                    and not sample.get('on_pit_road') and use >= MIN_USE_PCT):
                self.history.append(round(use, 3))
                self.counted += 1
            previous = None
        if previous is None:
            self.lap = {'lap': lap, 'start': pct, 'pct': pct, 'at': sample['session_time'],
                        # A lap joined part-way round would undercount its use.
                        'clean': (not sample.get('on_pit_road') and sample.get('is_replay') is False
                                  and (_number(sample.get('distance_pct')) or 1.) <= .03)}
            return
        if (not continuous or sample.get('on_pit_road') or pct > previous['pct'] + .05
                or int(sample.get('session_flags') or 0) & YELLOW):
            previous['clean'] = False
        previous['pct'], previous['at'] = pct, sample['session_time']

    def view(self, race):
        """Energy figures beside the fuel ones, from the same remaining-laps estimate."""
        pct = self.current
        if pct is None:
            return {}
        per_lap = sum(self.history) / len(self.history) if len(self.history) >= MIN_LAPS else None
        laps = pct / per_lap if per_lap else None
        remaining = _number(race.get('remaining_laps'))
        shortfall = target = None
        if per_lap and remaining is not None:
            need = remaining * per_lap
            if need > pct + .05:
                shortfall = need - pct
                target = pct / remaining if remaining > 0 else None
        fuel_laps = _number(race.get('fuel_laps'))
        return {'energy_pct': round(pct, 1), 'energy_pct_per_lap': round(per_lap, 2) if per_lap else None,
                'energy_observed_laps': len(self.history), 'energy_laps': round(laps, 2) if laps is not None else None,
                'energy_finish_shortfall_pct': round(shortfall, 1) if shortfall is not None else None,
                'energy_target_pct_per_lap': round(target, 2) if target is not None else None,
                'energy_limits_stint': bool(laps is not None and fuel_laps is not None and laps < fuel_laps),
                'energy_last_lap_pct': self.history[-1] if self.history else None,
                'energy_laps_counted': self.counted, 'energy_stint': self.stint}
