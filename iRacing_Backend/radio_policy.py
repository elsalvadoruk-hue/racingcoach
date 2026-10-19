"""Shared low-workload windows; brief calls tolerate gentle steering."""
import math
from collections import deque


def quiet_window(sample, brief=False):
    def number(key):
        value = sample.get(key)
        return value if type(value) in (int, float) and math.isfinite(value) else None
    speed, brake, steer, throttle = (number(k) for k in ('speed_mps', 'brake', 'steering_rad', 'throttle'))
    if sample.get('on_pit_road') is True or speed is not None and speed < 2:
        return True
    if brake is None or steer is None or brake >= .05:
        return False
    return abs(steer) < .12 or (brief and throttle is not None and throttle >= .9 and abs(steer) < .30)


def speech_seconds(text):
    """Conservative Korean speech estimate at the shared 1.15 playback rate."""
    return max(2., len(''.join((text or '').split())) / 4.5 + 1.)


HOLD_DEFAULT_S = 1.


def speech_ok(sample, brief=False):
    """May a call START now? The driver's hold: not while braking or steering, and not
    within his chosen seconds before it. A call that has started is never cut for this.
    'speech_hold_s' below zero means he switched the hold off."""
    lead = sample.get('speech_hold_s')
    lead = HOLD_DEFAULT_S if type(lead) not in (int, float) or not math.isfinite(lead) else lead
    if lead < 0:
        return True
    if not quiet_window(sample, brief):
        return False
    ahead = sample.get('workload_in_s')
    return type(ahead) not in (int, float) or ahead > lead


def report_window(sample, text=None, brief=False):
    return speech_ok(sample, brief)


class TrackSpeechWindows:
    """Learn this car/layout's repeatable low-workload stretches, in memory only."""
    BINS = 200

    def __init__(self):
        self.reset()

    def reset(self):
        self.laps = deque(maxlen=3)
        self.cells = {}
        self.previous = None
        self.clean = False
        self.rates = deque(maxlen=6)

    def ingest(self, sample):
        p = self.previous
        pct, lap, at = (sample.get(k) for k in ('distance_pct', 'lap_number', 'session_time'))
        valid = (type(pct) in (float, int) and math.isfinite(pct) and 0 <= pct < 1
                 and type(lap) in (float, int) and type(at) in (float, int)
                 and sample.get('on_track') is True and sample.get('is_replay') is False
                 and sample.get('on_pit_road') is False)
        if not valid:
            self.previous = None
            self.cells = {}
            self.clean = False
            self.rates.clear()
            return
        if p and lap == p['lap_number'] + 1 and p['distance_pct'] > .95 and pct < .03:
            if (self.clean and len(self.cells) >= self.BINS * .94
                    and sample.get('incidents') == p.get('incidents')
                    and 0 < at - p['session_time'] <= .5 and not int(sample.get('session_flags') or 0) & 0xC108):
                self.laps.append(self.cells)
            self.cells = {}
            self.clean = True
        elif p is None or lap != p['lap_number']:
            self.cells = {}
            self.clean = pct <= .03
        if p:
            dt, delta = at - p['session_time'], pct - p['distance_pct']
            if not 0 < dt <= .5 or (lap == p['lap_number'] and not 0 <= delta < .05):
                self.clean = False
            if sample.get('incidents') != p.get('incidents') or int(sample.get('session_flags') or 0) & 0xC108:
                self.clean = False
            if lap == p['lap_number'] and 0 < dt <= .5 and 0 < delta < .05:
                self.rates.append(delta / dt)
                low = (quiet_window(p) and quiet_window(sample)
                       and p.get('car_left_right') not in (2, 3, 4, 5, 6)
                       and sample.get('car_left_right') not in (2, 3, 4, 5, 6))
                first, last = int(p['distance_pct'] * self.BINS), int(pct * self.BINS)
                for cell in range(first, min(last, self.BINS - 1) + 1):
                    fraction = max(0., min(pct, (cell + 1) / self.BINS) - max(p['distance_pct'], cell / self.BINS)) / delta
                    total, quiet = self.cells.get(cell, (0., 0.))
                    self.cells[cell] = (total + dt * fraction, quiet + dt * fraction * low)
        self.previous = dict(sample)

    def view(self, sample):
        result = {'speech_window_s': None, 'workload_in_s': None, 'speech_learned_laps': len(self.laps)}
        if sample.get('on_pit_road') is True or (type(sample.get('speed_mps')) in (float, int) and sample['speed_mps'] < 2):
            return result
        pct = sample.get('distance_pct')
        if len(self.laps) < 2 or type(pct) not in (float, int) or not 0 <= pct < 1:
            return result
        if not quiet_window(sample) or sample.get('car_left_right') in (2, 3, 4, 5, 6):
            return {**result, 'speech_window_s': 0., 'workload_in_s': 0.}
        first, seconds, distance = int(pct * self.BINS), 0., 0.
        for offset in range(self.BINS):
            cell = (first + offset) % self.BINS
            observations = [lap.get(cell) for lap in self.laps]
            if any(v is None or v[0] <= 0 or v[1] / v[0] < .95 for v in observations):
                break
            duration = min(v[0] for v in observations)
            if offset == 0:
                duration *= 1 - (pct * self.BINS - first)
            distance += (1 - (pct * self.BINS - first) if offset == 0 else 1) / self.BINS
            seconds += duration
        # A faster current approach must shorten the budget, never extend it.
        if self.rates:
            seconds = min(seconds, distance / max(self.rates))
        seconds = min(seconds, 30.)
        # 'speech_window_s' keeps its safety margin for the display; the hold uses the plain estimate.
        return {**result, 'speech_window_s': round(max(0., seconds * .8 - 1.), 2), 'workload_in_s': round(seconds, 2)}
