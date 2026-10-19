"""Pit stops as measured: what a stop costs on each track, and whether new tyres are worth it.

Everything here comes from stops that were actually driven (the history seeded by
scripts/learn_pit_history.py and the stops of the session in progress):

- transit loss per track: the time a stop costs besides standing in the stall
  (slowing for the entry, the pit lane, getting back up to speed);
- fuelling rate and tread wear per lap, per car and track;
- how long tyres take, and what a cold set costs on the laps after the stop.

The tyre advice is one short sentence. In a long race it weighs, in seconds, what a change
costs at this stop against what a worn set may lose over the next stint, both from the
driver's own records; a set is never asked to do a third stint.
"""
import json
import math
import statistics
import threading
from collections import deque
from pathlib import Path

# Measured in the driver's own races (BMW M4 GT3, McLaren 720S GT3; 33 stops). First values, not laws.
TYRE_STALL_S = 21.          # stall time when four tyres are changed and fuelling is shorter (tyres done at 16 s, car released at 21)
SEQUENTIAL_EXTRA_S = 21.4   # extra standing time where tyres only start after fuelling (seven stops, all 21.3-21.6 s)
COLD_TYRE_S = 4.            # a new set against a fuel-only stop: about 3.4 s on the out-lap and the rest on the first flying laps
RELEASE_S = 1.5             # between the end of fuelling and the car leaving the stall
SAFE_WORN_PCT = 36.         # most tread worn in any recorded stint; lap time did not fall off up to here
WORN_PACE_PCT = .0135       # lap time lost per percent of tread worn, as percent of a lap: the unfavourable end of the
                            # measurement over 801 clean laps (-0.0003 +/- 0.0069; the best estimate is no loss at all)
SPRINT_S = 3600.            # shorter races: one stop, tyres stay on (the driver's rule, backed by 13 fuel-only stops)
FASTEST_FUEL_LPS = 2.5      # the quickest fuelling seen; used only to prove that fuelling outlasts a tyre change


def finite(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


class PitKnowledge:
    """Per-track and per-car pit facts, kept in one small JSON file beside the database."""
    VERSION = 1

    def __init__(self, path=None):
        self.path, self.lock, self.data = Path(path) if path else None, threading.Lock(), None

    def _load(self):
        if self.data is None:
            try:
                found = json.loads(self.path.read_text(encoding='utf-8')) if self.path else {}
                self.data = found if found.get('version') == self.VERSION else {}
            except (OSError, ValueError):
                self.data = {}
            self.data.setdefault('version', self.VERSION)
            self.data.setdefault('tracks', {})
            self.data.setdefault('cars', {})
            self.data.setdefault('series', {})
        return self.data

    def _save(self, data):
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            temporary.replace(self.path)
        except OSError:
            pass

    def series_rule(self, series_id):
        """The tyre-change rule last measured for an official series ('CONCURRENT'/'SEQUENTIAL'), or None."""
        key = str(series_id or '').strip()
        if not key or key == '0':
            return None
        with self.lock:
            return (self._load()['series'].get(key) or {}).get('pit_rule')

    def remember_series_rule(self, series_id, rule, source_file=None, stops=0):
        """Keep the rule a drive file showed for its series (pit_service.detect). The latest file wins:
        a series can change its rule from one season to the next."""
        key = str(series_id or '').strip()
        if not key or key == '0' or rule not in ('CONCURRENT', 'SEQUENTIAL'):
            return False
        with self.lock:
            data = self._load()
            entry = data['series'].setdefault(key, {})
            seen = [item for item in (entry.get('seen') or []) if item.get('file') != source_file][-4:]
            seen.append({'file': source_file, 'pit_rule': rule, 'stops': int(stops)})
            changed = entry.get('pit_rule') != rule or entry.get('seen') != seen
            entry.update({'pit_rule': rule, 'seen': seen})
            if changed:
                self._save(data)
            return changed

    @staticmethod
    def track_key(identity):
        track = str(identity.get('track_id') or '').strip()
        return f"{track}|{identity.get('track_config') or ''}" if track else None

    @staticmethod
    def car_key(identity):
        car, track = str(identity.get('car_id') or '').strip(), PitKnowledge.track_key(identity)
        return f'{car}|{track}' if car and track else None

    def track(self, identity):
        with self.lock:
            return dict(self._load()['tracks'].get(self.track_key(identity)) or {})

    def car(self, identity):
        with self.lock:
            return dict(self._load()['cars'].get(self.car_key(identity)) or {})

    def learn(self, identity, transit_loss_s=None, fuel_rate_lps=None, wear_pct_per_lap=None):
        """Add one measurement; the stored value is the median of the last ten."""
        with self.lock:
            data, changed = self._load(), False
            for section, key, name, value, low, high in (
                    ('tracks', self.track_key(identity), 'transit_loss_s', transit_loss_s, 5., 120.),
                    ('cars', self.car_key(identity), 'fuel_rate_lps', fuel_rate_lps, .2, 6.),
                    ('cars', self.car_key(identity), 'wear_pct_per_lap', wear_pct_per_lap, .05, 15.)):
                value = finite(value)
                if key is None or value is None or not low <= value <= high:
                    continue
                entry = data[section].setdefault(key, {})
                samples = (entry.get(name + '_samples') or [])[-9:] + [round(value, 3)]
                entry[name + '_samples'], entry[name] = samples, round(statistics.median(samples), 3)
                changed = True
            if changed:
                self._save(data)
            return changed


def stall_estimate(fuel_l, fuel_rate_lps, tyres, pit_rule):
    """Seconds standing in the stall for this fuel and tyre choice, or None when the fuelling rate is unknown."""
    if not fuel_rate_lps:
        return None
    fuel_s = max(0., fuel_l or 0.) / fuel_rate_lps
    if not tyres:
        return fuel_s + RELEASE_S
    if pit_rule == 'SEQUENTIAL':
        return fuel_s + SEQUENTIAL_EXTRA_S
    return max(fuel_s + RELEASE_S, TYRE_STALL_S)


def tyre_advice(race_s, pit_rule, fuel_l, fuel_rate_lps, wear_pct_per_lap, laps_on_set, next_stint_laps, tyres_ticked=None, wet=False,
                stints_on_set=None, lap_s=None):
    """What to do with the tyres at the coming stop: (decision, [(sentence key, values)...]).

    One short sentence. A race under an hour keeps its tyres. In a longer race the two sides are
    weighed in seconds, from the driver's own records:
    - changing costs the extra standing time (none where fuelling outlasts the tyres) plus the cold set;
    - keeping costs what a worn set may lose over the next stint. The records show no loss at all,
      so the unfavourable end of that measurement is used (WORN_PACE_PCT), which errs toward changing.
    Two limits come first: a set that would end the next stint inside the wear range already
    driven (36 percent) stays on, and a set never does a third stint (the driver's rule).
    decision is 'keep', 'change' or None (nothing can be said). A sentence is added only when the
    pit service setting disagrees with the plan.
    """
    if wet or race_s is None:
        return None, []          # a change of weather decides the tyres, not this
    setting = lambda wanted: ([('tyre_setting_on' if tyres_ticked else 'tyre_setting_off', {})]
                              if tyres_ticked is not None and bool(tyres_ticked) != wanted else [])
    keep, change = ('keep', [('tyre_keep', {}), *setting(False)]), ('change', [('tyre_change', {}), *setting(True)])
    if race_s < SPRINT_S:
        return keep
    if pit_rule not in ('CONCURRENT', 'SEQUENTIAL'):
        return None, []          # the driver has not said how this series works its stops
    known = bool(wear_pct_per_lap) and laps_on_set is not None and next_stint_laps is not None
    if known and wear_pct_per_lap * (laps_on_set + next_stint_laps) <= SAFE_WORN_PCT:
        return keep              # e.g. a short last stint
    if stints_on_set is not None and stints_on_set >= 2:
        return change            # skipped once already
    with_tyres, without = stall_estimate(fuel_l, fuel_rate_lps, True, pit_rule), stall_estimate(fuel_l, fuel_rate_lps, False, pit_rule)
    if with_tyres is not None:
        extra = with_tyres - without
    elif pit_rule == 'SEQUENTIAL':
        extra = SEQUENTIAL_EXTRA_S - RELEASE_S
    else:
        # Without a measured rate: fuelling this much at the fastest rate ever seen still outlasts the tyres?
        extra = 0. if (fuel_l or 0.) / FASTEST_FUEL_LPS + RELEASE_S >= TYRE_STALL_S else None
    if known and lap_s and extra is not None:
        cost_of_changing = extra + COLD_TYRE_S
        may_lose_by_keeping = WORN_PACE_PCT * wear_pct_per_lap * laps_on_set / 100 * lap_s * next_stint_laps
        return keep if may_lose_by_keeping < cost_of_changing else change
    # Wear or pace not measured yet: the plain rules.
    if extra is not None and extra < 3:
        return change            # standing time is the same either way
    if stints_on_set is None:
        return None, []          # joined mid-race: how long this set has been on is not known
    return keep


class TyreWatch:
    """Laps on the set that is on the car, and the wear it showed at the last stop."""
    def __init__(self):
        self.reset()

    def reset(self):
        self.fitted_lap = None        # lap number on which the set went on (None: unknown, e.g. joined mid-race)
        self.stints = None            # stints this set has started: 1 on a new set, 2 after a stop that kept it on
        self.rate = None              # percent of tread per lap, measured at a stop of this session
        self.stop = None
        self.on_track_tread = None    # the stale value shown while driving; a stop replaces it with a fresh reading

    def ingest(self, sample):
        """Returns the wear rate once, when a stop gives a new reading of the tread."""
        lap, tread = sample.get('lap_number'), finite(sample.get('tyre_tread_min'))
        if type(lap) not in (int, float):
            return None
        if self.fitted_lap is None and sample.get('session_state') in (3, 4) and lap <= 1:
            self.fitted_lap, self.stints = 0, 1   # the race starts on a new set
        pit = sample.get('on_pit_road') is True
        speed = finite(sample.get('speed_mps'))
        stopped = pit and speed is not None and speed < .5
        ticked = int(finite(sample.get('pit_sv_flags')) or 0) & 0xF
        learned = None
        if not pit:
            if self.stop is not None:
                if self.stop['changed']:
                    self.fitted_lap, self.stints = lap, 1
                elif self.stints is not None:
                    self.stints += 1
                self.stop = None
            self.on_track_tread = tread
            return None
        if stopped and self.stop is None:
            self.stop = {'asked': 0, 'changed': False, 'read': False}
        if self.stop is not None and stopped:
            # The sim reports the tread only once the car stands in its stall: wait for the value to move.
            if (not self.stop['read'] and tread is not None and self.on_track_tread is not None and abs(tread - self.on_track_tread) > 1e-4
                    and self.fitted_lap is not None and lap - self.fitted_lap >= 3 and 0 < tread < 1):
                self.stop['read'] = True
                self.rate = learned = (1 - tread) * 100 / (lap - self.fitted_lap)
            self.stop['asked'] |= ticked
            if self.stop['asked'] == 0xF and ticked == 0:
                self.stop['changed'] = True       # the crew worked through all four: a new set is on
        return learned

    def laps_on_set(self, lap):
        return lap - self.fitted_lap if self.fitted_lap is not None and type(lap) in (int, float) else None


class PitLossWatch:
    """Measures what a stop costs besides the stall: the laps around it against normal laps."""
    def __init__(self):
        self.reset()

    def reset(self):
        self.lap, self.lap_started, self.touched, self.block = None, None, False, None

    def ingest(self, sample, normal_lap_s):
        """Returns the transit loss in seconds once, when the first clean lap after a stop begins."""
        lap, at = sample.get('lap_number'), finite(sample.get('session_time'))
        if type(lap) not in (int, float) or at is None or sample.get('on_track') is not True:
            self.reset()
            return None
        pit = sample.get('on_pit_road') is True
        speed = finite(sample.get('speed_mps'))
        stalled = pit and speed is not None and speed < .5
        if self.lap is None:
            self.lap, self.lap_started, self.touched = lap, None, True     # joined mid-lap: this lap is not timed
            return None
        found = None
        if lap != self.lap:
            if lap == self.lap + 1 and self.lap_started is not None:
                if self.touched:
                    block = self.block or {'time': 0., 'laps': 0, 'stall': 0.}
                    block['time'] += at - self.lap_started
                    block['laps'] += 1
                    self.block = block
                elif self.block:
                    block, self.block = self.block, None
                    # One or two laps hold the whole stop; what they took beyond normal laps, less the standing time.
                    if normal_lap_s and block['laps'] <= 2 and block['stall'] > 1:
                        found = block['time'] - block['laps'] * normal_lap_s - block['stall']
            else:
                self.block = None
            self.lap, self.lap_started, self.touched = lap, at, False
        if pit:
            self.touched = True
        if stalled:
            block = self.block or {'time': 0., 'laps': 0, 'stall': 0.}
            previous = block.get('stalled_at')
            if previous is not None and 0 < at - previous < 1:
                block['stall'] += at - previous
            block['stalled_at'] = at
            self.block = block
        elif self.block:
            self.block.pop('stalled_at', None)
        return found if found is not None and 5 <= found <= 120 else None
