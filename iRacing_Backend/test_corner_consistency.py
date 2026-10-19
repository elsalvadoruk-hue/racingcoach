"""Each corner's time on every valid lap, for the consistency box plots."""
import unittest

from test_rolling_best import BackToBackLaps
from test_analysis_reliability import api


class CornerConsistencyTests(BackToBackLaps, unittest.TestCase):
    def test_every_valid_lap_gives_one_time_per_corner(self):
        analyzer = api.TelemetryAnalyzer(self.telemetry([(40, 40), (42, 40), (44, 40)]))
        result = analyzer._corner_consistency([{"id": "C1", "label": "1번 코너", "apex_pct": .25, "entry_pct": .1, "exit_pct": .4}], 3)
        self.assertTrue(result["available"])
        times = [item["time_s"] for item in result["corners"][0]["passes"]]
        self.assertEqual([item["lap"] for item in result["corners"][0]["passes"]], [2, 3, 4])
        for seconds, half in zip(times, (40, 42, 44)):
            self.assertAlmostEqual(seconds, .3 / .5 * half, delta=.1)

    def test_fewer_than_three_laps_give_nothing(self):
        analyzer = api.TelemetryAnalyzer(self.telemetry([(40, 40), (40, 40)]))
        self.assertFalse(analyzer._corner_consistency([{"id": "C1", "entry_pct": .1, "exit_pct": .4}], 2)["available"])


if __name__ == '__main__':
    unittest.main()
