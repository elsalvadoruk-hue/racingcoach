import tempfile
import unittest
from pathlib import Path

import numpy as np

import pit_service
from live_pit import PitKnowledge


def stop(fuel_from, fuel_to, tyres_first, tyres_done, length=60.0, litres=60.0, rate=60):
    """One stall at 60 Hz: fuel rises from fuel_from to fuel_to s; tyre bits clear one by one between
    tyres_first and tyres_done s (all four requested on arrival)."""
    time = np.arange(0, length, 1 / rate)
    fuel = 5.0 + np.clip((time - fuel_from) / (fuel_to - fuel_from), 0, 1) * litres
    marks = np.linspace(tyres_first, tyres_done, 4)
    flags = np.full(len(time), 0x10 | 0xF, dtype=np.int64)
    for bit, at in zip((1, 2, 4, 8), marks):
        flags[time >= at] &= ~bit
    return time, fuel, flags


class StopTests(unittest.TestCase):
    def test_tyres_done_while_fuel_goes_in_is_concurrent(self):
        # Nürburgring 4 h, 2026-10-10: fuel 1.0 -> 116.6 s, tyres cleared 0.7 -> 11.2 s.
        found = pit_service.stop_kind(*stop(1.0, 116.6, 0.7, 11.2, length=118))
        self.assertEqual(found["kind"], "CONCURRENT")

    def test_tyres_after_the_fuel_is_sequential(self):
        # Adelaide 3 h, 2026-07-25: fuel 0.7 -> 39.5 s, tyres cleared 41.1 -> 56.2 s.
        self.assertEqual(pit_service.stop_kind(*stop(0.7, 39.5, 41.1, 56.2, length=61))["kind"], "SEQUENTIAL")

    def test_tyres_before_the_fuel_is_sequential(self):
        self.assertEqual(pit_service.stop_kind(*stop(22.0, 50.0, 3.0, 21.0))["kind"], "SEQUENTIAL")

    def test_a_stop_that_shows_nothing_says_nothing(self):
        time, fuel, flags = stop(1.0, 40.0, 5.0, 20.0)
        self.assertIsNone(pit_service.stop_kind(time, fuel, flags & ~0xF))            # no tyres asked for
        self.assertIsNone(pit_service.stop_kind(*stop(1.0, 40.0, 5.0, 20.0, litres=2.0)))   # a splash of fuel
        self.assertIsNone(pit_service.stop_kind(*stop(1.0, 40.0, 30.0, 39.8)))         # ending together
        time, fuel, flags = stop(1.0, 40.0, 5.0, 20.0)
        flags[:] = 0x1F                                                                 # tyres never finished
        self.assertIsNone(pit_service.stop_kind(time, fuel, flags))


class DetectTests(unittest.TestCase):
    def channels(self, *stops):
        parts = {"SessionTime": [], "PlayerCarInPitStall": [], "FuelLevel": [], "PitSvFlags": []}
        clock = 0.0
        for time, fuel, flags in stops:
            gap = np.arange(0, 30, 1 / 60)                                              # driving between stops
            for name, values in (("SessionTime", np.r_[clock + gap, clock + 30 + time]), ("PlayerCarInPitStall", np.r_[np.zeros(len(gap)), np.ones(len(time))]),
                                 ("FuelLevel", np.r_[np.full(len(gap), fuel[0]), fuel]), ("PitSvFlags", np.r_[np.zeros(len(gap), dtype=np.int64), flags])):
                parts[name].append(values)
            clock += 30 + time[-1] + 1
        return {name: np.concatenate(values) for name, values in parts.items()}

    def test_agreeing_stops_give_the_rule(self):
        found = pit_service.detect(self.channels(stop(0.7, 39.5, 41.1, 56.2, length=61), stop(0.8, 30.2, 32.0, 47.0, length=52)))
        self.assertEqual((found["rule"], len(found["stops"]), found["reason"]), ("SEQUENTIAL", 2, "MEASURED"))

    def test_disagreeing_stops_or_none_give_no_rule(self):
        self.assertIsNone(pit_service.detect(self.channels(stop(1.0, 100.0, 0.7, 11.0, length=101), stop(0.7, 39.5, 41.1, 56.2, length=61)))["rule"])
        self.assertEqual(pit_service.detect({"SessionTime": np.zeros(3)})["reason"], "NO_CHANNELS")


class SeriesMemoryTests(unittest.TestCase):
    def test_a_series_keeps_its_latest_rule_and_unofficial_sessions_keep_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "pit-knowledge.json"
            knowledge = PitKnowledge(path)
            self.assertIsNone(knowledge.series_rule("237"))
            self.assertTrue(knowledge.remember_series_rule("237", "SEQUENTIAL", "a.ibt", 2))
            self.assertFalse(knowledge.remember_series_rule("237", "SEQUENTIAL", "a.ibt", 2))    # nothing new
            self.assertFalse(knowledge.remember_series_rule("0", "CONCURRENT", "b.ibt", 1))
            self.assertFalse(knowledge.remember_series_rule("275", "MAYBE", "b.ibt", 1))
            self.assertEqual(PitKnowledge(path).series_rule("237"), "SEQUENTIAL")                 # kept on disk
            knowledge.remember_series_rule("237", "CONCURRENT", "c.ibt", 1)
            self.assertEqual(PitKnowledge(path).series_rule(237), "CONCURRENT")
            self.assertIsNone(knowledge.series_rule("0"))


if __name__ == "__main__":
    unittest.main()
