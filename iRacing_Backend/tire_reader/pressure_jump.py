"""Hold a dashboard pressure that jumps away from the wheel's last value until it repeats.

Windows OCR now and then reads a single frame with one digit wrong (1.64 -> 1.84 bar,
seen on real BMW M4 GT3 drives). Hot pressure moves slowly while driving, so a value
far from the wheel's last accepted value is kept back until the next reading agrees
with it. Nothing is guessed or smoothed: an accepted value is always one that was read.
Pit road, the garage, a new car or session, or a gap in readings starts afresh,
because real pressure changes there (pit stops, setup changes) are large.
"""
from __future__ import annotations

import math

JUMP_KPA = 8.          # larger than one second of real hot-pressure change while driving
RATE_KPA_S = 1.5       # allowed change per second between readings further apart
AGREE_KPA = 3.         # a held value is accepted when the next reading is this close
STALE_S = 10.          # an older last value is no reference


class PressureJumpFilter:
    def __init__(self):
        self.key, self.last, self.pending = None, {}, {}

    def reset(self):
        self.last.clear()
        self.pending.clear()

    def check(self, wheel, kpa, t, *, key=None, pit=False):
        """(accepted kPa or None, held) for one wheel's reading at session time t."""
        if key != self.key:
            self.key = key
            self.reset()
        if kpa is None or not math.isfinite(kpa) or t is None or not math.isfinite(t):
            return kpa, False
        last = self.last.get(wheel)
        if pit or last is None or t < last[1] or t - last[1] > STALE_S:
            return self._accept(wheel, kpa, t), False
        if abs(kpa - last[0]) <= max(JUMP_KPA, RATE_KPA_S * (t - last[1])):
            return self._accept(wheel, kpa, t), False
        pending = self.pending.get(wheel)
        if pending and abs(kpa - pending[0]) <= AGREE_KPA and 0 <= t - pending[1] <= STALE_S:
            return self._accept(wheel, kpa, t), False
        self.pending[wheel] = (kpa, t)
        return None, True

    def _accept(self, wheel, kpa, t):
        self.last[wheel] = (kpa, t)
        self.pending.pop(wheel, None)
        return kpa
