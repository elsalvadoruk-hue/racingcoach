"""Damper velocity histograms over the valid laps (positive = compression)."""
import tempfile
import unittest
from pathlib import Path

import numpy as np
from test_analysis_reliability import api


class DamperVelocityTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)

    def analyzer(self, shocks=True):
        n = 6000
        t = np.arange(n) / 60.0
        channels = {"SessionTime": 100 + t, "LapDistPct": t / 100, "Speed": np.full(n, 40.), "Brake": np.zeros(n),
                    "Throttle": np.ones(n), "Lap": np.full(n, 2), "IsOnTrack": np.ones(n),
                    "PlayerTrackSurface": np.full(n, 3), "OnPitRoad": np.zeros(n)}
        if shocks:
            # 30 mm/s compression for a quarter of the lap, 80 mm/s rebound for a quarter, 400 mm/s (off the scale) for 1%
            v = np.zeros(n)
            v[:1500], v[1500:3000], v[3000:3060] = .03, -.08, .4
            v[3060:] = np.nan
            for wheel in ("LF", "RF", "LR", "RR"):
                channels[f"{wheel}shockVel"] = v.copy()
        path = Path(self.work.name) / 'lap.ibt'
        path.write_bytes(b'test')
        lap = api.LapSegment(2, 0, n - 1, True, True, 'VALID', 100., 0, 1, 1., 0, None)
        telemetry = api.ParsedTelemetry(path, api.file_signature(path), 60, 0, n, 0, '', {}, {}, channels,
                                        {"track_length_m": 4000, "car_name": "Test", "track_name": "Test"}, {}, [lap])
        return api.TelemetryAnalyzer(telemetry)

    def test_time_at_each_velocity_with_bump_positive_and_the_ends_catching_everything_faster(self):
        result = self.analyzer()._damper_velocity()
        self.assertTrue(result["available"])
        lf = result["wheels"]["LF"]
        self.assertEqual(lf["samples"], 3060)                       # unknown samples are not counted
        self.assertAlmostEqual(sum(lf["share_pct"]), 100, delta=.1)
        self.assertAlmostEqual(lf["slow_bump_pct"], 1500 / 3060 * 100, delta=.1)
        self.assertAlmostEqual(lf["fast_rebound_pct"], 1500 / 3060 * 100, delta=.1)
        self.assertAlmostEqual(lf["fast_bump_pct"], 60 / 3060 * 100, delta=.1)
        self.assertAlmostEqual(lf["share_pct"][-1], 60 / 3060 * 100, delta=.05)   # 400 mm/s lands in the last bin
        edges = result["edges_mm_s"]
        bin_30 = next(i for i in range(len(edges) - 1) if edges[i] <= 30 < edges[i + 1])
        self.assertAlmostEqual(lf["share_pct"][bin_30], 1500 / 3060 * 100, delta=.05)

    def test_a_file_without_shock_channels_says_so(self):
        self.assertEqual(self.analyzer(shocks=False)._damper_velocity(), {"available": False, "reason": "NO_SHOCK_CHANNELS"})


if __name__ == '__main__':
    unittest.main()
