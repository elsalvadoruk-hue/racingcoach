"""Expert view: several laps on one distance axis (/api/lap-overlay), synthetic data, isolated storage."""
import atexit
import os
from pathlib import Path
import tempfile
import unittest

_runtime = tempfile.TemporaryDirectory(prefix="iracing-overlay-", ignore_cleanup_errors=True)
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
from fastapi import HTTPException
import backend_api as api

LENGTH = 4000.0
PER_LAP = 2001


def three_laps():
    """Laps 1-3 of 100, 99 and 101 s; lap 2 is invalid. Lap 3 loses its second at the far end of the lap."""
    times, laps, distance, rpm = [], [], [], []
    clock = 1000.0
    for number, (lap_time, valid) in enumerate(((100.0, True), (99.0, False), (101.0, True)), start=1):
        pct = np.linspace(0, 1, PER_LAP)
        lap_clock = pct * lap_time if number != 3 else pct * 100.0 + np.clip(pct - 0.5, 0, None) * 2.0
        start = len(distance)
        distance.extend(pct); times.extend(clock + lap_clock); rpm.extend(6000 + pct * 1000)
        laps.append(api.LapSegment(number, start, start + PER_LAP - 1, valid, valid, "VALID" if valid else "OFF_TRACK",
                                   lap_time, 0., 1., 1., 0, None))
        clock += lap_time + 0.01
    count = len(distance)
    distance = np.asarray(distance)
    channels = {"LapDistPct": distance, "SessionTime": np.asarray(times), "LapCurrentLapTime": np.zeros(count),
                "Speed": np.full(count, 40.0), "Brake": np.where((distance > .3) & (distance < .32), .9, 0.),
                "Throttle": np.where((distance > .3) & (distance < .35), 0., 1.), "SteeringWheelAngle": np.zeros(count),
                "Gear": np.where(distance < .5, 3., 5.), "RPM": np.asarray(rpm), "LatAccel": np.full(count, 9.80665),
                "LongAccel": np.zeros(count)}
    return api.ParsedTelemetry(_root / "overlay.ibt", ("overlay", 1, 1), 60, count, count, 0, "", {}, {}, channels,
                               {"track_length_m": LENGTH, "track_name": "overlay"}, {}, laps)


class LapOverlayTests(unittest.TestCase):
    def setUp(self):
        self.telemetry = three_laps()

    def run_overlay(self, **kwargs):
        return api.lap_overlay_payload(self.telemetry, api.LapOverlayRequest(**kwargs))

    def test_every_lap_on_one_axis_with_the_fastest_valid_lap_as_base(self):
        out = self.run_overlay(laps=[1, 2, 3])
        self.assertEqual(out["base_lap"], 1)                     # lap 2 is quicker but invalid
        self.assertEqual([lap["valid"] for lap in out["laps"]], [True, False, True])
        n = len(out["distance_pct"])
        for lap in ("1", "2", "3"):
            self.assertEqual(len(out["series"][lap]["speed"]), n)
        keys = [c["key"] for c in out["channels"]]
        self.assertEqual(keys[0], "delta")
        for key in ("speed", "throttle", "brake", "gear", "rpm", "lat_g", "long_g"):
            self.assertIn(key, keys)
        self.assertAlmostEqual(out["series"]["1"]["speed"][n // 2], 144.0, places=1)
        self.assertAlmostEqual(out["series"]["1"]["lat_g"][n // 2], 1.0, places=2)
        self.assertIn(out["series"]["1"]["gear"][n // 2], (3.0, 5.0))

    def test_delta_is_cumulative_time_behind_the_base_lap(self):
        out = self.run_overlay(laps=[1, 3], base_lap=1)
        pct = np.asarray(out["distance_pct"])
        delta = np.asarray([np.nan if v is None else v for v in out["series"]["3"]["delta"]])
        early = np.nanmax(np.abs(delta[pct < .45]))
        late = delta[np.argmin(np.abs(pct - .95))]
        self.assertLess(early, 0.02)
        self.assertAlmostEqual(late, 0.9, delta=0.05)              # 2 s lost per lap after half way, 0.45 lap later
        self.assertTrue(all(v in (None, 0.0) for v in out["series"]["1"]["delta"]))

    def test_a_window_returns_only_that_part_of_the_lap(self):
        out = self.run_overlay(laps=[1, 3], start_pct=.3, end_pct=.4, points=100)
        self.assertGreaterEqual(min(out["distance_pct"]), .2995)
        self.assertLessEqual(max(out["distance_pct"]), .4005)

    def test_choosing_the_base_lap(self):
        self.assertEqual(self.run_overlay(laps=[1, 3], base_lap=3)["base_lap"], 3)
        self.assertEqual(self.run_overlay(laps=[1, 3], base_lap=2)["base_lap"], 1)   # not among the laps shown

    def test_unknown_laps_and_bad_ranges_are_refused(self):
        with self.assertRaises(HTTPException):
            self.run_overlay(laps=[9])
        with self.assertRaises(HTTPException):
            self.run_overlay(laps=[1], start_pct=.5, end_pct=.4)

    def test_lap_grids_keep_rpm(self):
        grid = api.TelemetryAnalyzer(self.telemetry)._lap_grid(self.telemetry.laps[0])
        self.assertIn("RPM", grid)


if __name__ == "__main__":
    unittest.main()


class OverlayOtherSourcesTests(unittest.TestCase):
    """Laps of another drive file and Garage61 laps on this file's distance axis."""

    def setUp(self):
        from unittest.mock import patch
        self.here = three_laps()
        self.other = three_laps()
        self.other.metadata = {**self.other.metadata, "car_name": "Other car"}
        patches = [patch.object(api, "resolve_telemetry_file", lambda name: _root / "other 2026-07-24 18-38-59.ibt" if name == "other.ibt" else None),
                   patch.object(api.repository, "load", lambda path: self.other)]
        for item in patches:
            item.start(); self.addCleanup(item.stop)

    def run_overlay(self, **kwargs):
        return api.lap_overlay_payload(self.here, api.LapOverlayRequest(**kwargs))

    def test_a_lap_from_another_file_joins_with_its_own_key_and_delta(self):
        out = self.run_overlay(laps=[1], extra=[{"source": "IBT", "filename": "other.ibt", "lap": 3}])
        keys = [lap["key"] for lap in out["laps"]]
        self.assertEqual(keys[0], "1")
        self.assertTrue(keys[1].startswith("ibt:") and keys[1].endswith("#3"))
        self.assertEqual(out["laps"][1]["label"], "07-24 18:38 · 랩 3")
        self.assertEqual(out["base_key"], "1")
        late = out["series"][keys[1]]["delta"][-5]
        self.assertGreater(late, 0.8)                                  # the other file's lap 3 loses about a second late in the lap

    def test_the_base_can_be_a_lap_of_another_file(self):
        out = self.run_overlay(laps=[1], extra=[{"source": "IBT", "filename": "other.ibt", "lap": 1}],
                               base_key="ibt:other 2026-07-24 18-38-59.ibt#1")
        self.assertEqual(out["base_key"], "ibt:other 2026-07-24 18-38-59.ibt#1")
        self.assertIsNone(out["base_lap"])

    def test_another_track_or_a_missing_lap_is_left_out_with_its_reason(self):
        self.other.metadata = {**self.other.metadata, "track_name": "elsewhere"}
        out = self.run_overlay(laps=[1], extra=[{"source": "IBT", "filename": "other.ibt", "lap": 1}])
        self.assertEqual([lap["key"] for lap in out["laps"]], ["1"])          # this file's lap is still drawn
        self.assertIn("같은 트랙", out["skipped"][0]["reason"])
        self.other.metadata = {**self.other.metadata, "track_name": "overlay"}
        out = self.run_overlay(laps=[1], extra=[{"source": "IBT", "filename": "other.ibt", "lap": 9},
                                                 {"source": "IBT", "filename": "missing.ibt", "lap": 1}])
        self.assertEqual(len(out["skipped"]), 2)
        with self.assertRaises(HTTPException):                               # nothing left to draw
            self.run_overlay(laps=[], extra=[{"source": "IBT", "filename": "missing.ibt", "lap": 1}])

    def test_a_track_id_decides_the_layout_alone(self):
        # An IBT of Le Mans has no layout name; Garage61 writes "24 Heures du Mans". Same iRacing track ID 268.
        self.assertTrue(api._same_track({"track_platform_id": "268", "track_config": ""}, {"track_platform_id": "268", "track_config": "24 Heures du Mans"}))
        self.assertFalse(api._same_track({"track_platform_id": "268"}, {"track_platform_id": "18"}))
        self.assertFalse(api._same_track({"track_name": "Spa", "track_config": "GP"}, {"track_name": "Spa", "track_config": "Endurance"}))

    def test_a_garage61_lap_is_put_on_the_same_axis(self):
        from unittest.mock import patch
        pct = np.linspace(0, 1, 3001)
        reference = api.ExternalReferenceLap(_root / "g61.csv", {"driver_name": "Someone", "lap_time_s": 99.0, "track_name": "overlay",
                                                                 "track_config": "", "clean": True, "car_name": "GT3"},
                                             pct, pct * 99.0, {"Speed": np.full(len(pct), 40.0), "Throttle": np.ones(len(pct))})
        with patch.object(api, "resolve_external_reference_file", lambda name: _root / "g61.csv"), \
                patch.object(api.external_reference_repository, "load", lambda path: reference):
            out = self.run_overlay(laps=[1], extra=[{"source": "GARAGE61", "filename": "g61.csv"}])
        g61 = out["laps"][1]
        self.assertEqual((g61["key"], g61["label"], g61["source"]), ("g61:g61.csv", "Garage61 · Someone", "GARAGE61"))
        n = len(out["distance_pct"])
        self.assertEqual(len(out["series"]["g61:g61.csv"]["speed"]), n)
        self.assertAlmostEqual(out["series"]["g61:g61.csv"]["delta"][n // 2], -0.5, delta=0.05)   # 99 s against 100 s, half way

    def test_moving_a_grid_keeps_gaps_and_steps(self):
        source = {"distance_pct": np.linspace(0, 1, 11), "Speed": np.arange(11.0), "Gear": np.array([3.] * 6 + [4.] * 5)}
        source["Speed"][4] = np.nan
        moved = api._on_distance_axis(source, np.linspace(0, 1, 21))
        self.assertAlmostEqual(moved["Speed"][1], 0.5)
        self.assertTrue(np.isnan(moved["Speed"][7]) and np.isnan(moved["Speed"][8]))   # between 0.3-0.4 and 0.4-0.5 touch the gap
        self.assertEqual(set(moved["Gear"][np.isfinite(moved["Gear"])]), {3.0, 4.0})

    def test_file_laps_lists_finished_laps(self):
        out = api.file_laps_payload(self.here)
        self.assertEqual([lap["lap_number"] for lap in out["laps"]], [1, 2, 3])
        self.assertEqual([lap["valid"] for lap in out["laps"]], [True, False, True])


class TrackTemperatureTests(unittest.TestCase):
    def test_each_lap_shows_the_track_surface_beside_the_tyres(self):
        telemetry = three_laps()
        count = len(telemetry.channels["SessionTime"])
        telemetry.channels["TrackTempCrew"] = np.r_[np.full(PER_LAP, 31.0),
                                                    np.full(count - PER_LAP, 33.4)]
        telemetry.channels["LFtempM"] = np.full(count, 80.0)
        trend = api.TelemetryAnalyzer(telemetry)._tire_diagnostics(telemetry.laps[0])["lap_trend"]
        by_lap = {row["lap_number"]: row for row in trend}
        self.assertEqual(by_lap[1]["mean_track_temp_c"], 31.0)
        self.assertEqual(by_lap[3]["mean_track_temp_c"], 33.4)
        del telemetry.channels["TrackTempCrew"]
        telemetry.channels["TrackTemp"] = np.full(count, 29.5)                # older files: the other channel
        trend = api.TelemetryAnalyzer(telemetry)._tire_diagnostics(telemetry.laps[0])["lap_trend"]
        self.assertEqual(trend[0]["mean_track_temp_c"], 29.5)
