"""Distance/coverage/precision regressions with synthetic data and isolated storage."""
import atexit
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

_runtime = tempfile.TemporaryDirectory(prefix="iracing-corners-", ignore_cleanup_errors=True)
atexit.register(_runtime.cleanup)
_root = Path(_runtime.name)
os.environ.update({
    "IRACING_TUNER_DB_PATH": str(_root / "memory.sqlite3"),
    "IRACING_FILE_INDEX_PATH": str(_root / "index.sqlite3"),
    "IRACING_EXTERNAL_REFERENCE_DIR": str(_root / "references"),
    "GARAGE61_API_TOKEN_FILE": str(_root / "no-token.txt"),
    "GARAGE61_AUTO_COLLECT": "0", "IRACING_AI_ENABLED": "0", "IRACING_IMPORT_LEGACY": "0",
})

import numpy as np
import backend_api as api


def telemetry(length=5000, count=5001):
    lap = api.LapSegment(1, 0, count - 1, True, True, "VALID", 100., 0., 1., 1., 0, None)
    distance = np.linspace(0, 1, count)
    channels = {"LapDistPct": distance, "SessionTime": 1000 + distance * 100,
                "LapCurrentLapTime": distance * 100, "Speed": np.full(count, length / 100),
                "Brake": np.zeros(count), "Throttle": np.ones(count), "SteeringWheelAngle": np.zeros(count),
                "Gear": np.where(distance < .5, 3, 5)}
    return api.ParsedTelemetry(_root / "synthetic.ibt", ("synthetic", 1, 1), 60, count, count, 0,
                               "", {}, {}, channels, {"track_length_m": length, "track_name": f"synthetic-{length}"}, {}, [lap])


class CornerComparisonTests(unittest.TestCase):
    def setUp(self):
        api._CORNER_DEFINITIONS.clear()

    def test_interpolation_keeps_observed_ends_and_long_holes_missing(self):
        distance = np.array([.1, .102, .104, .8, .802, .804])
        grid = np.array([0., .1, .101, .104, .2, .799, .801, 1.])
        result = api.interpolate_observed(distance, np.arange(6.), grid, 5000)
        self.assertTrue(np.isnan(result[[0, 4, 5, 7]]).all())
        self.assertAlmostEqual(result[2], .5)
        self.assertAlmostEqual(result[6], 3.5)

    def test_step_channels_never_invent_intermediate_gear_or_abs_states(self):
        distance = np.array([0., .002, .004])
        grid = np.linspace(0, .004, 9)
        result = api.interpolate_observed(distance, np.array([3, 5, 5]), grid, 1000, stepped=True)
        self.assertEqual(set(result), {3., 5.})
        self.assertEqual(result[3], 3)
        self.assertEqual(result[4], 5)

    def test_time_gap_rejects_even_small_spatial_gap(self):
        result = api.interpolate_observed(np.array([.1, .102]), np.array([10., 20.]), np.array([.101]), 5000, np.array([1., 5.]))
        self.assertTrue(np.isnan(result[0]))

    def test_long_track_adapts_analysis_grid_to_two_metres(self):
        analyzer = api.TelemetryAnalyzer(telemetry(24154.4, 18001))
        self.assertLessEqual(analyzer.track_length_m / (analyzer.grid_points - 1), 2)
        self.assertLessEqual(analyzer.grid_points, 25000)
        grid = analyzer._lap_grid(analyzer.valid_laps[0])
        self.assertEqual(set(grid["Gear"][np.isfinite(grid["Gear"])]), {3., 5.})
        self.assertIn("_channel_support", grid)

    def test_visual_overview_preserves_short_brake_pulse_and_gear_event(self):
        count = 5000
        grid = {"distance_pct": np.linspace(0, 1, count), "speed_kph": np.full(count, 150.), "Brake": np.zeros(count), "Throttle": np.ones(count), "Gear": np.full(count, 4.)}
        grid["Brake"][1234:1238] = .9
        grid["Gear"][2351:] = 5
        indices = api.visualization_indices(grid, None, 100)
        self.assertIn(1233, indices)
        self.assertIn(1234, indices)
        self.assertIn(1237, indices)
        self.assertIn(1238, indices)
        self.assertIn(2350, indices)
        self.assertIn(2351, indices)

    def test_gap_or_analysis_mask_disables_loss_and_coaching(self):
        analyzer = api.TelemetryAnalyzer(telemetry(1000, 1001))
        grid = analyzer._lap_grid(analyzer.valid_laps[0])
        reference = {key: value.copy() if isinstance(value, np.ndarray) else value for key, value in grid.items()}
        grid["_analysis_coverage"] = np.ones(len(grid["distance_pct"]), dtype=bool)
        grid["_analysis_coverage"][600:700] = False
        corner = analyzer._corner_analysis(grid, reference, [{"entry": 400, "apex": 650, "exit": 800}])[0]
        self.assertFalse(corner["measurement_usable"])
        self.assertFalse(corner["coaching_usable"])
        self.assertIsNone(corner["delta_s"])
        self.assertEqual(corner["issue_type"], "DATA_GAP")

    def test_long_track_braking_zones_are_not_merged_by_lap_percentage(self):
        source = telemetry(24000, 12001)
        distance_m = source.channels["LapDistPct"] * 24000
        source.channels["Brake"][(distance_m > 480) & (distance_m < 505)] = .7
        source.channels["Brake"][(distance_m > 680) & (distance_m < 705)] = .7
        source.channels["Speed"] -= 25 * np.exp(-((distance_m - 535) / 20) ** 2)
        source.channels["Speed"] -= 25 * np.exp(-((distance_m - 735) / 20) ** 2)
        analyzer = api.TelemetryAnalyzer(source)
        grid = analyzer._lap_grid(analyzer.valid_laps[0])
        definitions = analyzer._detect_corners(grid)
        self.assertEqual(len(definitions), 2)
        self.assertLess(definitions[0]["exit"], definitions[1]["entry"])
        changed_reference = {**grid, "Brake": np.zeros(len(grid["distance_pct"]))}
        self.assertEqual(analyzer._detect_corners(changed_reference), definitions)
        corners = analyzer._corner_analysis(grid, changed_reference, definitions)
        self.assertTrue(all(c["label"].startswith("자동 구간") for c in corners))

    def test_detail_returns_dense_observed_range_and_validates_single_mode(self):
        source = telemetry(24000, 16001)
        payload = api.ComparisonDetailRequest(filename="synthetic.ibt", start_pct=.2, end_pct=.22, points=2400)
        result = api.comparison_detail_payload(source, payload)
        self.assertGreater(len(result["trace"]), 200)
        self.assertLessEqual(result["quality"]["analysis_spacing_m"], 2)
        self.assertFalse(result["quality"]["extrapolation"])
        self.assertGreaterEqual(result["trace"][0]["distance_pct"], .2)
        with self.assertRaises(api.HTTPException):
            api.comparison_detail_payload(source, api.ComparisonDetailRequest(reference_mode="SINGLE", external_reference_files=[]))

    def test_gps_serialization_preserves_holes_and_original_coordinates(self):
        grid = {"distance_pct": np.linspace(0, 1, 5), "Lat": np.array([50.123456789, np.nan, 50.2, 0, 50.3]),
                "Lon": np.array([6.123456789, 6.1, np.nan, 6.2, 6.3])}
        path = api.gps_path(grid)
        self.assertEqual(path[0]["lat"], 50.12345679)
        self.assertEqual(path[0]["lon"], 6.12345679)
        self.assertTrue(all(p["lat"] is None and p["lon"] is None for p in path[1:4]))
        self.assertEqual(api.gps_path(grid, 1, 3), path[1:4])
        self.assertEqual(api.gps_path({"distance_pct": grid["distance_pct"]}), [])

    def test_gps_points_carry_the_time_since_the_lap_start_for_the_ghost_replay(self):
        grid = {"distance_pct": np.linspace(0, 1, 3), "Lat": np.array([50., 50.1, 50.2]), "Lon": np.array([6., 6.1, 6.2]),
                "time_s": np.array([0.0, 41.23456, np.nan])}
        self.assertEqual([p["time_s"] for p in api.gps_path(grid)], [0.0, 41.235, None])
        self.assertTrue(all(p["time_s"] is None for p in api.gps_path({k: v for k, v in grid.items() if k != "time_s"})))

    def test_local_detail_includes_both_actual_gps_paths(self):
        source = telemetry()
        source.channels.update({"Lat": 50 + source.channels["LapDistPct"] * .01,
                                "Lon": 6 + source.channels["LapDistPct"] * .01})
        lap = source.laps[0]
        with patch.object(api.TelemetryAnalyzer, "_select_laps", return_value=(lap, lap, {})):
            result = api.comparison_detail_payload(source, api.ComparisonDetailRequest(start_pct=.2, end_pct=.22))
        self.assertGreater(len(result["reference_track_path"]), 40)
        self.assertEqual(result["track_path"], result["reference_track_path"])
        self.assertEqual(result["reference_line_source"]["type"], "LOCAL_ACTUAL_LAP")

    def test_external_detail_uses_actual_line_grid_and_keeps_aggregate_chart(self):
        source = telemetry()
        def external(analyzer, *_args):
            grid = analyzer._lap_grid(analyzer.valid_laps[0])
            analyzer._external_detail_grid = {**grid, "Lat": np.full(analyzer.grid_points, 51.),
                                               "Lon": np.full(analyzer.grid_points, 7.), "speed_kph": np.full(analyzer.grid_points, 123.)}
            analyzer._external_line_grid = {**grid, "Lat": np.full(analyzer.grid_points, 50.123456789),
                                            "Lon": np.full(analyzer.grid_points, 6.123456789)}
            analyzer._external_line_source = {"reference_file": "actual.csv", "driver_name": "Actual driver", "chart_basis": "AGGREGATE"}
            analyzer._external_detail_distributions = {}
            analyzer._external_detail_support = {}
            analyzer._external_reference_label = "Aggregate"
            return {"status": "READY"}
        for mode in ("SINGLE", "AGGREGATE"):
            with self.subTest(mode=mode), patch.object(api.TelemetryAnalyzer, "_external_reference_analysis", external):
                result = api.comparison_detail_payload(source, api.ComparisonDetailRequest(reference_mode=mode, external_reference_files=["actual.csv"], start_pct=.2, end_pct=.22))
                self.assertTrue(all(p["lat"] == 50.12345679 and p["lon"] == 6.12345679 for p in result["reference_track_path"]))
                self.assertTrue(all(p["reference_speed_kph"] == 123. for p in result["trace"]))
                self.assertEqual(result["reference_line_source"]["reference_file"], "actual.csv")

    def test_official_optimal_endpoints_do_not_fill_visual_trace(self):
        analyzer = api.TelemetryAnalyzer(telemetry())
        grid = analyzer._lap_grid(analyzer.valid_laps[0])
        grid["time_s"][[0, -1]] = np.nan
        grid["speed_kph"][[0, -1]] = np.nan
        grid["_coverage"][[0, -1]] = False
        result = analyzer._personal_optimal()
        self.assertEqual(result["optimal_endpoint_basis"], "OFFICIAL_LAP_TIMES")
        self.assertEqual(result["theoretical_optimal_time_s"], 100.)
        self.assertTrue(np.isnan(grid["time_s"][[0, -1]]).all())
        grid["time_s"][600:650] = np.nan
        grid["_coverage"][600:650] = False
        result = analyzer._personal_optimal()
        self.assertEqual(result["optimal_status"], "INCOMPLETE_SECTOR_COVERAGE")
        self.assertIsNone(result["theoretical_optimal_time_s"])

    def test_comparison_extrema_use_observed_samples_with_missing_ends(self):
        analyzer = api.TelemetryAnalyzer(telemetry())
        lap = analyzer.valid_laps[0]
        grid = analyzer._lap_grid(lap)
        reference = {**grid, "time_s": grid["time_s"] - .25}
        grid["time_s"][[0, -1]] = np.nan
        result = analyzer._comparison(lap, lap, grid, reference)
        self.assertEqual(result["delta_min_s"], .25)
        self.assertEqual(result["delta_max_s"], .25)


if __name__ == "__main__":
    unittest.main()
