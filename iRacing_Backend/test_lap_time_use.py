"""Per-lap time use: braking, coasting, part and full throttle, cornering (RaceStudio 3 style lap statistics)."""
import tempfile
import unittest
from pathlib import Path

import numpy as np
from test_analysis_reliability import api


class LapTimeUseTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)

    def telemetry(self, **changes):
        n = 6000                                         # 100 s at 60 Hz
        t = np.arange(n) / 60.0
        brake = np.where(t < 20, .5, 0.)                 # 20 s braking
        throttle = np.select([t < 30, t < 60], [0., .5], 1.)   # then 10 s coasting, 30 s part, 40 s full
        lateral = np.where((t >= 50) & (t < 75), 6., .5)  # 25 s above 0.5 g
        channels = {"SessionTime": 100 + t, "LapDistPct": t / 100, "Speed": np.full(n, 40.), "Brake": brake,
                    "Throttle": throttle, "LatAccel": lateral, "Lap": np.full(n, 2), "IsOnTrack": np.ones(n),
                    "PlayerTrackSurface": np.full(n, 3), "OnPitRoad": np.zeros(n)}
        channels.update(changes)
        channels = {name: value for name, value in channels.items() if value is not None}
        path = Path(self.work.name) / 'lap.ibt'
        path.write_bytes(b'test')
        lap = api.LapSegment(2, 0, n - 1, True, True, 'VALID', 100., 0, 1, 1., 0, None)
        return api.ParsedTelemetry(path, api.file_signature(path), 60, 0, n, 0, '', {}, {}, channels,
                                   {"track_length_m": 4000, "car_name": "Test", "track_name": "Test"}, {}, [lap])

    def test_a_lap_is_split_into_braking_coasting_part_and_full_throttle_with_cornering_on_its_own(self):
        telemetry = self.telemetry()
        use = api.TelemetryAnalyzer(telemetry)._lap_time_use(telemetry.laps[0])
        self.assertAlmostEqual(use['share_pct']['brake'], 20, delta=.1)
        self.assertAlmostEqual(use['share_pct']['coast'], 10, delta=.1)
        self.assertAlmostEqual(use['share_pct']['part_throttle'], 30, delta=.1)
        self.assertAlmostEqual(use['share_pct']['full_throttle'], 40, delta=.1)
        self.assertAlmostEqual(use['share_pct']['corner'], 25, delta=.1)
        self.assertAlmostEqual(use['seconds']['full_throttle'], 40, delta=.1)
        row = api.TelemetryAnalyzer(telemetry)._lap_table()[0]
        self.assertEqual(row['time_use'], use)

    def test_without_the_pedals_nothing_is_guessed_and_without_lateral_acceleration_cornering_is_left_out(self):
        telemetry = self.telemetry(Brake=None)
        self.assertIsNone(api.TelemetryAnalyzer(telemetry)._lap_time_use(telemetry.laps[0]))
        telemetry = self.telemetry(LatAccel=None)
        use = api.TelemetryAnalyzer(telemetry)._lap_time_use(telemetry.laps[0])
        self.assertNotIn('corner', use['share_pct'])
        self.assertAlmostEqual(use['share_pct']['full_throttle'], 40, delta=.1)

    def test_a_pause_in_the_recording_and_unknown_samples_are_not_counted(self):
        t = np.arange(6000) / 60.0
        session = 100 + t + np.where(t >= 90, 30., 0.)    # a 30 s hole inside the full-throttle part
        brake = np.where(t < 20, .5, 0.)
        brake[(t >= 20) & (t < 25)] = np.nan              # 5 s of coasting with no brake reading
        telemetry = self.telemetry(SessionTime=session, Brake=brake)
        use = api.TelemetryAnalyzer(telemetry)._lap_time_use(telemetry.laps[0])
        self.assertAlmostEqual(use['counted_s'], 95, delta=.1)
        self.assertAlmostEqual(use['seconds']['coast'], 5, delta=.1)
        self.assertAlmostEqual(use['seconds']['full_throttle'], 40, delta=.1)


if __name__ == '__main__':
    unittest.main()
