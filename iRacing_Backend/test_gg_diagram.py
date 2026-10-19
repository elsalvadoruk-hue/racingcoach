"""G-G diagram: right turns to the right, acceleration up, braking down; the envelope per direction."""
import tempfile
import unittest
from pathlib import Path

import numpy as np
from test_analysis_reliability import api


class GGDiagramTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)

    def analyzer(self, accelerations=True):
        n = 6000
        t = np.arange(n) / 60.0
        quarter = n // 4
        lat = np.zeros(n); lon = np.zeros(n)
        lat[:quarter] = -1.5 * 9.80665               # iRacing: negative lateral = right turn
        lon[quarter:2 * quarter] = -1.8 * 9.80665     # braking
        lon[2 * quarter:3 * quarter] = .6 * 9.80665   # accelerating
        speed = np.full(n, 40.)
        speed[3 * quarter:3 * quarter + 600] = 2.     # crawling samples are left out
        channels = {"SessionTime": 100 + t, "LapDistPct": t / 100, "Speed": speed, "Brake": np.zeros(n),
                    "Throttle": np.ones(n), "Lap": np.full(n, 2), "IsOnTrack": np.ones(n),
                    "PlayerTrackSurface": np.full(n, 3), "OnPitRoad": np.zeros(n)}
        if accelerations:
            channels.update(LatAccel=lat, LongAccel=lon)
        path = Path(self.work.name) / 'lap.ibt'
        path.write_bytes(b'test')
        lap = api.LapSegment(2, 0, n - 1, True, True, 'VALID', 100., 0, 1, 1., 0, None)
        return api.TelemetryAnalyzer(api.ParsedTelemetry(path, api.file_signature(path), 60, 0, n, 0, '', {}, {}, channels,
                                                          {"track_length_m": 4000, "car_name": "Test", "track_name": "Test"}, {}, [lap]))

    def test_directions_and_maxima(self):
        gg = self.analyzer()._gg_diagram()
        self.assertTrue(gg["available"])
        self.assertAlmostEqual(gg["max_lateral_g"], 1.5, delta=.01)
        self.assertAlmostEqual(gg["max_brake_g"], 1.8, delta=.01)
        self.assertAlmostEqual(gg["max_accel_g"], .6, delta=.01)
        envelope = gg["envelope_g"]
        self.assertAlmostEqual(envelope[0], 1.5, delta=.01)          # 0-10 degrees: right turn, to the right
        self.assertAlmostEqual(envelope[9], .6, delta=.01)           # 90-100 degrees: acceleration, up
        self.assertAlmostEqual(envelope[27], 1.8, delta=.01)         # 270-280 degrees: braking, down
        self.assertTrue(all(abs(x) < 1e-9 or abs(x - 1.5) < .01 for x, _ in gg["points"]))

    def test_without_acceleration_channels_it_says_so(self):
        self.assertEqual(self.analyzer(False)._gg_diagram(), {"available": False, "reason": "NO_ACCELERATION_CHANNELS"})


if __name__ == '__main__':
    unittest.main()
