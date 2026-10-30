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
FRESH_SECONDS = 2.      # same continuity window as the measured consumption laps


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
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
        self.current_at = None
        self.last_valid_pct = self.last_valid_at = None
        self.observed_at = self.session_num = None

    def ingest(self, sample):
        at, session = _number(sample.get('session_time')), sample.get('session_num')
        if ((self.session_num is not None and session is not None and session != self.session_num)
                or (at is not None and self.observed_at is not None and at < self.observed_at)):
            self.reset()
        self.observed_at, self.session_num = at, session
        pct, lap = _number(sample.get('virtual_energy_pct')), sample.get('lap_number')
        source_at = _number(sample.get('virtual_energy_sample_time', at))
        # A previous observation is useful history, never a substitute for a current reading.
        self.current = self.current_at = None
        if (pct is None or not 0 <= pct <= 100 or at is None or source_at is None
                or not 0 <= at - source_at <= FRESH_SECONDS or sample.get('is_replay') is not False
                or sample.get('on_track') is False):
            self.lap = None
            return
        self.current, self.current_at = pct, source_at
        self.last_valid_pct, self.last_valid_at = pct, source_at
        if lap is None:
            self.lap = None
            return
        previous = self.lap
        continuous = (previous is not None and 0 < at - previous['at'] <= FRESH_SECONDS
                      and 0 < source_at - previous['source_at'] <= FRESH_SECONDS)
        clean = (sample.get('on_pit_road') is False
                 and not int(sample.get('session_flags') or 0) & YELLOW)
        if previous and continuous and pct > previous['pct'] + REFILL_PCT:
            self.stint += 1
        if previous and lap != previous['lap']:
            use = previous['start'] - pct
            if (previous['clean'] and continuous and lap == previous['lap'] + 1
                    and sample.get('on_pit_road') is False and use >= MIN_USE_PCT):
                self.history.append(round(use, 3))
                self.counted += 1
            previous = None
        if previous is None:
            distance = _number(sample.get('distance_pct'))
            self.lap = {'lap': lap, 'start': pct, 'pct': pct, 'at': at, 'source_at': source_at,
                        # A lap joined part-way round would undercount its use.
                        'clean': clean and distance is not None and 0 <= distance <= .03}
            return
        if not continuous or not clean or pct > previous['pct'] + .05:
            previous['clean'] = False
        previous['pct'], previous['at'] = pct, at
        previous['source_at'] = source_at

    def view(self, race, now=None):
        """Energy figures beside the fuel ones, from the same remaining-laps estimate."""
        pct = self.current
        now = self.observed_at if now is None else _number(now)
        if (pct is None or self.current_at is None or now is None
                or not 0 <= now - self.current_at <= FRESH_SECONDS):
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
                'energy_laps_counted': self.counted, 'energy_stint': self.stint,
                'energy_sample_time': self.current_at, 'energy_age_s': round(now - self.current_at, 3)}
