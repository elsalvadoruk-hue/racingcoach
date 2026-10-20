"""Analytic geometry and channel-quality checks without game files."""
from dataclasses import replace
import json
import unittest
import numpy as np

from telemetry_core import AnalysisLap, ChannelTrace
from lmu_corners import compare_corners


def lap(speed=50., number=1, bend=(400., 600.)):
    distance = np.arange(0., 1501.)
    times = distance/speed
    heading = np.clip((distance-bend[0])/(bend[1]-bend[0]), 0, 1)*np.pi/2
    x, y = np.cumsum(np.cos(heading)), np.cumsum(np.sin(heading))
    channels = {}
    support = {}
    def add(key, unit, values, stage=None):
        channels[key] = ChannelTrace(key, unit, speed, times, values, np.ones(len(times), dtype=bool))
        support[key] = {"status": "AVAILABLE", "input_stage": stage}
    add("distance_m", "m", distance)
    add("latitude_deg", "deg", 45+y/6371000*180/np.pi)
    add("longitude_deg", "deg", 9+x/(6371000*np.cos(np.pi/4))*180/np.pi)
    add("speed_mps", "m/s", np.full(len(times), speed))
    add("brake_ratio", "ratio", np.where((distance>=300)&(distance<=450), .5, 0), "GAME_PROCESSED")
    add("throttle_ratio", "ratio", np.where(distance>=600, 1., 0), "GAME_PROCESSED")
    metadata = {"track_name": "Synthetic", "track_layout": "A", "car_name": "GT", "car_class": "GT3"}
    return AnalysisLap("LMU_DUCKDB", number, 0., times[-1], times[-1], None,
                       "VALIDITY_UNKNOWN", metadata, channels, support)


def change(lap_, key, values=None, mask=None, times=None, frequency=None):
    old = lap_.channels[key]
    trace = replace(old, values=old.values if values is None else values,
                    valid_mask=old.valid_mask if mask is None else mask,
                    time_s=old.time_s if times is None else times,
                    frequency_hz=old.frequency_hz if frequency is None else frequency)
    return replace(lap_, channels=lap_.channels | {key: trace})


def geometric_path(turns):
    result = lap()
    distance = result.channels["distance_m"].values
    heading = sum(np.clip((distance-start)/(end-start), 0, 1)*angle for start,end,angle in turns)
    x, y = np.cumsum(np.cos(heading)), np.cumsum(np.sin(heading))
    result = change(result, "latitude_deg", values=45+y/6371000*180/np.pi)
    return change(result, "longitude_deg", values=9+x/(6371000*np.cos(np.pi/4))*180/np.pi)


class GeometryCornerTests(unittest.TestCase):
    def test_identical_fixed_windows_and_analytic_native_duration(self):
        reference, target = lap(), lap(40., 2)
        report = compare_corners(reference, target)
        self.assertEqual(report["status"], "AVAILABLE")
        self.assertEqual(len(report["corners"]), 1)
        self.assertEqual(report["definition"]["status"], "REFERENCE_GEOMETRY_UNVERIFIED")
        self.assertEqual(report["definition"]["coverage"], "SELECTED_WINDOWS_ONLY")
        c = report["corners"][0]
        span = c["end_m"]-c["start_m"]
        self.assertAlmostEqual(c["reference"]["metrics"]["duration_s"], span/50)
        self.assertAlmostEqual(c["target"]["metrics"]["duration_s"], span/40)
        self.assertAlmostEqual(c["delta_time_s"], span*(1/40-1/50))
        self.assertAlmostEqual(c["delta"]["min_speed_kmh"], -36)
        self.assertIsNone(report["reference_valid"])
        self.assertIsNone(report["target_valid"])
        self.assertNotIn("total_delta_s", report)
        json.dumps(report, allow_nan=False)

    def test_definition_independent_of_target_geometry_and_driving_inputs(self):
        reference, target = lap(), lap(40., 2)
        a = compare_corners(reference, target)
        target = replace(target, channels={k:v for k,v in target.channels.items() if "_deg" not in k})
        target = change(target, "throttle_ratio", values=np.zeros(1501))
        b = compare_corners(reference, target)
        reference = change(reference, "speed_mps", values=np.ones(1501))
        reference = change(reference, "brake_ratio", values=np.zeros(1501))
        c = compare_corners(reference, target)
        self.assertEqual(a["definition"], b["definition"])
        self.assertEqual(b["definition"], c["definition"])

    def test_input_stages_suppress_only_corresponding_deltas(self):
        reference, target = lap(), lap(40., 2)
        support = {k:dict(v) for k,v in target.support.items()}
        support["brake_ratio"]["input_stage"] = "UNFILTERED"
        support["throttle_ratio"].pop("input_stage")
        c = compare_corners(reference, replace(target, support=support))["corners"][0]
        self.assertIsNone(c["delta"]["brake_start_m"])
        self.assertIsNone(c["delta"]["throttle_first_m"])
        self.assertEqual(c["delta_support"]["brake_start_m"]["status"], "INPUT_STAGE_MISMATCH")
        self.assertIsNotNone(c["delta"]["min_speed_kmh"])
        self.assertIsNotNone(c["delta_time_s"])

    def test_missing_holey_discontinuous_jumping_sparse_coordinates_are_unavailable(self):
        reference, target = lap(), lap()
        missing = replace(reference, channels={k:v for k,v in reference.channels.items() if k!="latitude_deg"})
        mask = np.ones(1501, dtype=bool); mask[411] = False
        hole = change(reference, "latitude_deg", mask=mask)
        values = reference.channels["longitude_deg"].values.copy(); values[500] += .1
        jump = change(reference, "longitude_deg", values=values)
        times = reference.channels["latitude_deg"].time_s.copy(); times[700:] += 2
        gap = change(reference, "latitude_deg", times=times)
        old = reference.channels["latitude_deg"]
        sparse = replace(reference, channels=reference.channels | {"latitude_deg": replace(
            old, values=old.values[::50], time_s=old.time_s[::50], valid_mask=old.valid_mask[::50], frequency_hz=1)})
        for candidate in (missing, hole, jump, gap, sparse):
            with self.subTest(candidate=candidate.channels.keys()):
                result = compare_corners(candidate, target)
                self.assertEqual(result["status"], "UNAVAILABLE")
                self.assertTrue(result["reason"])
                self.assertEqual(result["corners"], [])

    def test_straight_and_boundary_clipped_curve_do_not_fabricate_windows(self):
        straight = lap(bend=(2000, 2200))
        clipped = lap(bend=(0, 200))
        for candidate in (straight, clipped):
            result = compare_corners(candidate, lap())
            self.assertEqual(result["status"], "UNAVAILABLE")

    def test_metadata_distance_and_recorded_time_fail_like_lap_comparison(self):
        reference, target = lap(), lap()
        bad_metadata = replace(target, metadata=target.metadata | {"car_class": "Other"})
        backwards = target.channels["distance_m"].values.copy(); backwards[500] = backwards[499]
        bad_distance = change(target, "distance_m", values=backwards)
        wrong_length = change(target, "distance_m", values=target.channels["distance_m"].values*2)
        for candidate in (bad_metadata, bad_distance, wrong_length, replace(target, recorded_lap_time_s=None)):
            with self.subTest(candidate=candidate.recorded_lap_time_s), self.assertRaises(ValueError):
                compare_corners(reference, candidate)

    def test_target_coverage_does_not_move_or_clip_definition(self):
        reference, target = lap(), lap()
        normal = compare_corners(reference, target)
        shifted = change(target, "distance_m", values=target.channels["distance_m"].values+600)
        report = compare_corners(reference, shifted)
        self.assertEqual(report["definition"], normal["definition"])
        self.assertIsNone(report["corners"][0]["delta_time_s"])
        self.assertIsNone(report["corners"][0]["target"]["metrics"]["duration_s"])

    def test_complexes_merge_and_neighbor_extensions_split_without_overlap(self):
        compound = geometric_path([(400,460,np.pi/2),(500,560,-np.pi/2)])
        self.assertEqual(len(compare_corners(compound, compound)["corners"]), 1)
        separate = geometric_path([(350,500,np.pi/2),(750,900,np.pi/2)])
        report = compare_corners(separate, separate)
        self.assertEqual(len(report["corners"]), 2)
        self.assertLessEqual(report["corners"][0]["end_m"], report["corners"][1]["start_m"])
        for boundary in report["definition"]["boundaries"]:
            self.assertLess(boundary["start_m"], boundary["apex_m"])
            self.assertLess(boundary["apex_m"], boundary["end_m"])

    def test_boundary_partial_candidate_is_explicitly_excluded(self):
        reference = geometric_path([(350,500,np.pi/2),(1400,1500,np.pi/2)])
        report = compare_corners(reference, reference)
        self.assertEqual(report["status"], "AVAILABLE")
        self.assertEqual(len(report["corners"]), 1)
        self.assertTrue(report["definition"]["excluded"])


if __name__ == "__main__":
    unittest.main()
