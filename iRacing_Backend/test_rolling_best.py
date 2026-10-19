"""Rolling best lap, and the lap clock origin when iRacing still shows the previous lap's time after the line."""
import tempfile
import unittest
from pathlib import Path

import numpy as np
from test_analysis_reliability import api


class BackToBackLaps:
    """Builds files of back-to-back laps; shared with test_corner_consistency."""
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)

    def telemetry(self, halves):
        """Back-to-back laps; each lap is two halves taking the given seconds. 60 Hz, 1000 m, constant pace per half."""
        rate, length = 60, 1000.0
        pct, session, lap_clock, speed, laps = [], [], [], [], []
        clock, index = 0.0, 0
        previous = 0.0
        for number, (first, second) in enumerate(halves, start=2):
            start = index
            elapsed = 0.0
            for half, seconds in ((0, first), (1, second)):
                samples = int(round(seconds * rate))
                for k in range(samples):
                    p = half * .5 + (k + .5) / samples * .5
                    pct.append(p)
                    session.append(clock)
                    # iRacing keeps showing the last lap's time for the first 1.5 s of a new lap
                    lap_clock.append(previous + elapsed if elapsed < 1.5 and number > 2 else elapsed)
                    speed.append(500.0 / seconds)
                    clock += 1 / rate
                    elapsed += 1 / rate
                    index += 1
            previous = first + second
            laps.append(api.LapSegment(number, start, index - 1, True, True, 'VALID', first + second, 0, 1, 1., 0, None))
        n = len(pct)
        channels = {"SessionTime": np.array(session), "LapDistPct": np.array(pct), "LapCurrentLapTime": np.array(lap_clock),
                    "Speed": np.array(speed), "Brake": np.zeros(n), "Throttle": np.ones(n), "Lap": np.zeros(n),
                    "IsOnTrack": np.ones(n), "PlayerTrackSurface": np.full(n, 3), "OnPitRoad": np.zeros(n)}
        path = Path(self.work.name) / 'laps.ibt'
        path.write_bytes(b'test')
        return api.ParsedTelemetry(path, api.file_signature(path), rate, 0, n, 0, '', {}, {}, channels,
                                   {"track_length_m": length, "car_name": "Test", "track_name": "Test"}, {}, laps)



class RollingBestTests(BackToBackLaps, unittest.TestCase):
    def test_the_lap_clock_starts_at_the_line_even_while_iracing_still_shows_the_last_lap(self):
        analyzer = api.TelemetryAnalyzer(self.telemetry([(40, 40), (40, 40)]))
        times = analyzer._lap_grid(analyzer.valid_laps[1])["time_s"]
        known = times[np.isfinite(times)]
        self.assertLess(known[0], .5)
        self.assertAlmostEqual(known[-1], 80, delta=.5)

    def test_the_fastest_lap_driven_across_the_line_is_found(self):
        # lap 2: slow first half, fast second; lap 3: fast first half, slow second -> 35 + 35 from mid lap 2
        analyzer = api.TelemetryAnalyzer(self.telemetry([(45, 35), (35, 45), (44, 44)]))
        result = analyzer._rolling_best()
        self.assertAlmostEqual(result["rolling_best_time_s"], 70, delta=.3)
        self.assertEqual(result["rolling_best_laps"], [2, 3])
        self.assertAlmostEqual(result["rolling_best_start_pct"], .5, delta=.02)
        self.assertAlmostEqual(result["rolling_best_gain_s"], 10, delta=.3)
        optimal = analyzer._personal_optimal()
        self.assertLessEqual(optimal["theoretical_optimal_time_s"], 80)

    def test_one_lap_has_no_rolling_best(self):
        analyzer = api.TelemetryAnalyzer(self.telemetry([(40, 40)]))
        self.assertIsNone(analyzer._rolling_best()["rolling_best_time_s"])


if __name__ == '__main__':
    unittest.main()
