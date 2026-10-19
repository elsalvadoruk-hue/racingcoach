"""Braking zone and full-throttle run of a corner: distance, deceleration, time between the same speeds."""
import unittest

import numpy as np
from test_analysis_reliability import api


class BrakeDriveZoneTests(unittest.TestCase):
    def zones(self, long_accel=True):
        analyzer = api.TelemetryAnalyzer.__new__(api.TelemetryAnalyzer)
        analyzer.track_length_m = 1000.0
        n = 1001                                          # 1 m per sample
        t = np.zeros(n)
        v = np.full(n, 200.0)
        # 0-99 m at 200 km/h, 100-199 m braking at 10 m/s² down to about 113 km/h, then full throttle
        for i in range(1, n):
            ms = v[i - 1] / 3.6
            if 100 <= i < 200:
                ms = np.sqrt(max(ms ** 2 - 2 * 10.0, 1.0))
            elif i >= 200:
                ms = np.sqrt(ms ** 2 + 2 * 3.0)
            v[i] = ms * 3.6
            t[i] = t[i - 1] + 2 / ((v[i] + v[i - 1]) / 3.6)
        brake = np.where((np.arange(n) >= 100) & (np.arange(n) < 200), .8, 0.)
        throttle = np.where(np.arange(n) >= 200, 1., 0.)
        grid = {"distance_pct": np.arange(n) / (n - 1), "LongAccel": np.where(brake > 0, -10.0, 3.0)}
        if not long_accel:
            del grid["LongAccel"]
        channel = lambda name: np.asarray(grid[name], dtype=float)
        return analyzer._brake_and_drive_zones(grid, channel, brake, throttle, v, t, 100, 199, 200), v

    def test_braking_distance_deceleration_and_the_speed_readings_come_from_the_measured_zone(self):
        zones, v = self.zones()
        self.assertAlmostEqual(zones["braking_distance_m"], 99, delta=.5)
        self.assertAlmostEqual(zones["avg_decel_g"], 10 / 9.80665, delta=.02)
        self.assertAlmostEqual(zones["brake_entry_speed_kph"], v[100], delta=.05)
        self.assertEqual(zones["brake_speed_time"][0][0], 195.0)        # first whole 5 km/h at or below the entry speed
        self.assertTrue(all(a[0] > b[0] and a[1] <= b[1] for a, b in zip(zones["brake_speed_time"], zones["brake_speed_time"][1:])))
        self.assertGreater(zones["drive_distance_m"], 700)
        self.assertTrue(all(a[0] < b[0] for a, b in zip(zones["drive_speed_time"], zones["drive_speed_time"][1:])))

    def test_without_longitudinal_acceleration_the_deceleration_comes_from_speed_and_time(self):
        zones, _ = self.zones(long_accel=False)
        self.assertAlmostEqual(zones["avg_decel_g"], 10 / 9.80665, delta=.1)
        self.assertIsNone(zones["peak_decel_g"])

    def test_two_laps_are_compared_only_over_the_speeds_both_passed(self):
        band = api.TelemetryAnalyzer.speed_band([[200, 0], [150, 1.0], [100, 2.0]], [[180, 0], [150, .8], [100, 1.9], [90, 2.1]], True)
        self.assertEqual((band["from_kph"], band["to_kph"]), (150, 100))
        self.assertAlmostEqual(band["delta_s"], 1.0 - 1.1, places=3)
        self.assertIsNone(api.TelemetryAnalyzer.speed_band([[200, 0]], [[200, 0]], True))
        self.assertIsNone(api.TelemetryAnalyzer.speed_band(None, [[200, 0], [100, 1]], True))


if __name__ == '__main__':
    unittest.main()
