"""Regression tests; all runtime persistence is isolated before backend import."""
import atexit
import csv
import io
import os
import sqlite3
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

_runtime = tempfile.TemporaryDirectory(prefix="iracing-reliability-")
atexit.register(_runtime.cleanup)
_root = Path(_runtime.name)
os.environ.update({
    "IRACING_TUNER_DB_PATH": str(_root / "memory.sqlite3"),
    "IRACING_EXTERNAL_REFERENCE_DIR": str(_root / "references"),
    "GARAGE61_API_TOKEN_FILE": str(_root / "no-token.txt"),
    "GARAGE61_AUTO_COLLECT": "0", "IRACING_AI_ENABLED": "0",
    "IRACING_IMPORT_LEGACY": "0",
})

import numpy as np
import backend_api as api
import garage61_api_collector as collector


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(dir=_runtime.name)
        self.addCleanup(self.work.cleanup)
        self.directory = Path(self.work.name)

    def make_csv(self, *, timestamps=False, missing_rows=(), bad_speed=False):
        path = self.directory / "Garage 61 - Driver - Car - Track - 01.40.000 - lap1.csv"
        names = ["Speed", "LapDistPct", "Brake", "Throttle", "SteeringWheelAngle"]
        if timestamps:
            names.append("SessionTime")
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=names)
            writer.writeheader()
            for index in range(500):
                row = dict(Speed="invalid" if bad_speed else 45, LapDistPct="invalid" if index in missing_rows else index / 499,
                           Brake=0.25, Throttle=0.6, SteeringWheelAngle=0.1)
                if timestamps:
                    row["SessionTime"] = 400 + index / 499 * 100
                writer.writerow(row)
        return path

    def test_filtered_csv_keeps_original_elapsed_gaps(self):
        lap = api.Garage61ReferenceReader(self.make_csv(missing_rows=range(100, 150))).parse()
        self.assertAlmostEqual(lap.sample_time_s[100], 150 / 499 * 100)
        self.assertGreater(lap.sample_time_s[100] - lap.sample_time_s[99], 10)
        self.assertEqual(lap.metadata["timing_source"], "ORIGINAL_SAMPLE_INDEX")

    def test_csv_elapsed_timestamps_survive_filtered_rows(self):
        lap = api.Garage61ReferenceReader(self.make_csv(timestamps=True, missing_rows=range(100, 150))).parse()
        self.assertAlmostEqual(lap.sample_time_s[100], 150 / 499 * 100)
        self.assertEqual(lap.metadata["timing_source"], "SessionTime")

    def test_filtered_csv_start_does_not_reset_elapsed_time_origin(self):
        lap = api.Garage61ReferenceReader(self.make_csv(timestamps=True, missing_rows=range(20))).parse()
        self.assertAlmostEqual(lap.sample_time_s[0], 20 / 499 * 100)

    def test_csv_health_rejects_broken_numeric_channel_and_caches(self):
        path = self.make_csv(bad_speed=True)
        first = api.external_reference_validation(path)
        self.assertFalse(first["usable"])
        with patch.object(api.Garage61ReferenceReader, "parse", side_effect=AssertionError("cache miss")):
            self.assertEqual(api.external_reference_validation(path), first)

    def completed_channels(self, elapsed=99.99, advance=True):
        return {"LapLastLapTime": np.full(40, 100.), "LapCurrentLapTime": np.r_[np.full(20, elapsed), np.zeros(20)],
                "LapCompleted": np.r_[np.ones(20), np.full(20, 2 if advance else 1)]}

    def test_equal_consecutive_official_times_count_when_completion_confirmed(self):
        result = api.IBTReader.__new__(api.IBTReader)._completed_lap_time(self.completed_channels(), 19, 20, 60)
        self.assertEqual(result, 100.)

    def test_stale_published_time_does_not_validate_invalid_lap(self):
        reader = api.IBTReader.__new__(api.IBTReader)
        self.assertIsNone(reader._completed_lap_time(self.completed_channels(elapsed=95), 19, 20, 60))
        self.assertIsNone(reader._completed_lap_time(self.completed_channels(advance=False), 19, 20, 60))

    def test_abs_missing_is_unknown_and_cannot_trigger_braking_diagnosis(self):
        analyzer = api.TelemetryAnalyzer.__new__(api.TelemetryAnalyzer)
        analyzer.telemetry = type("Telemetry", (), {"tick_rate": 60})()
        analyzer.track_length_m = 4000
        grid = {"distance_pct": np.linspace(0, 1, 100), "time_s": np.linspace(0, 100, 100),
                "speed_kph": np.full(100, 100), "Brake": np.full(100, .5)}
        metrics = analyzer._corner_metrics(grid, 10, 20, 30)
        self.assertIsNone(metrics["abs_duty_pct"])
        reference = {**metrics, "abs_duty_pct": 20}
        evidence, _, _ = analyzer._corner_evidence(.1, metrics, reference)
        self.assertFalse(any("ABS" in item for item in evidence))

    def test_trace_contract_keeps_missing_input_null(self):
        target = {"Brake": np.array([.3]), "Throttle": np.array([.8]), "SteeringWheelAngle": np.array([np.pi / 2]), "Gear": np.array([3])}
        result = api.comparison_input_channels(target, {"Brake": np.array([.4])}, 0)
        self.assertEqual(result["target_steering_deg"], 90)
        self.assertEqual(result["target_gear"], 3)
        self.assertEqual(result["brake"], .3)
        self.assertEqual(result["reference_brake"], .4)
        self.assertIsNone(result["reference_throttle"])
        self.assertIsNone(api.comparison_input_channels({}, None, 0)["target_gear"])

    def test_single_valid_lap_visible_and_partial(self):
        path = self.directory / "single.ibt"
        path.write_bytes(b"dummy")
        lap = type("Lap", (), {"complete": True, "valid": True})()
        telemetry = type("Telemetry", (), {"metadata": {}, "laps": [lap]})()
        with patch.object(api.repository, "load", return_value=telemetry), patch.object(api, "TELEMETRY_DIRS", [self.directory]):
            self.assertEqual(api.list_ibt_files(), [path])
            visibility = api.telemetry_file_visibility(path)
            self.assertTrue(visibility["analyzable"])
            self.assertFalse(visibility["comparison_ready"])
            self.assertEqual(visibility["status"], "PARTIAL")
            self.assertEqual(api.resolve_telemetry_file(path.name), path)

    def test_default_file_selection_skips_unanalyzable_recordings(self):
        newer, older = self.directory / "partial.ibt", self.directory / "valid.ibt"
        with patch.object(api, "_list_raw_ibt_files", return_value=[newer, older]), patch.object(api, "telemetry_file_visibility", side_effect=lambda path: {"analyzable": path == older}):
            self.assertEqual(api.resolve_telemetry_file(), older)
            self.assertEqual(api.resolve_telemetry_file(newer.name), newer)

    def test_default_file_selection_skips_confirmed_zero_but_preserves_explicit_coaching_only(self):
        newer, older = self.directory / 'coaching-only.ibt', self.directory / 'valid.ibt'
        with patch.object(api, '_list_raw_ibt_files', return_value=[newer, older]), patch.object(
                api, 'telemetry_file_visibility', side_effect=lambda path: {
                    'analyzable': True, 'valid_laps': 0 if path == newer else 1, 'status': 'COACHING_ONLY'}):
            self.assertEqual(api.resolve_telemetry_file(), older)
            self.assertEqual(api.resolve_telemetry_file(newer.name), newer)

    def test_default_file_selection_does_not_invent_zero_for_unknown_counts(self):
        path = self.directory / 'pending.ibt'
        with patch.object(api, '_list_raw_ibt_files', return_value=[path]), patch.object(
                api, 'telemetry_file_visibility', return_value={'analyzable': None, 'valid_laps': None}):
            self.assertEqual(api.resolve_telemetry_file(), path)

    def test_retry_honors_long_body_and_standard_header_wait(self):
        self.assertEqual(collector.retry_delay({}, {"details": {"retryAfterSeconds": 65}}, 0), 65)
        self.assertEqual(collector.retry_delay({"Retry-After": "120"}, {"details": {"retryAfterSeconds": 65}}, 0), 120)
        self.assertEqual(collector.retry_delay({"Retry-After": "Thu, 01 Jan 1970 00:02:00 GMT"}, {}, 0, now=0), 120)

    def test_http_429_waits_full_duration_then_retries(self):
        error = urllib.error.HTTPError("https://example.invalid", 429, "rate limited", {}, io.BytesIO(b'{"details":{"retryAfterSeconds":65}}'))
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"success"
        with patch.object(collector.urllib.request, "urlopen", side_effect=[error, response]), patch.object(collector.time, "sleep") as sleep:
            self.assertEqual(collector._request("laps", "synthetic-test-token"), b"success")
            self.assertEqual(sum(item.args[0] for item in sleep.call_args_list), 65)

    def test_structured_feedback_filters_evidence_and_remains_hypothesis(self):
        request = api.AnalyzeRequest(feedback_phase="EXIT", feedback_speed="LOW", feedback_symptom="OVERSTEER")
        enriched = api.request_with_structured_feedback(request)
        self.assertEqual(api.request_with_structured_feedback(enriched).user_feedback, enriched.user_feedback)
        analyzer = api.TelemetryAnalyzer.__new__(api.TelemetryAnalyzer)
        corners = [{"id": "T1", "label": "T1", "target": {"min_speed_kph": 90, "exit_speed_kph": 130}, "reference": {"exit_speed_kph": 140}, "delta_s": .2},
                   {"id": "T2", "label": "T2", "target": {"min_speed_kph": 200, "exit_speed_kph": 230}}]
        result = analyzer._structured_feedback(request, corners, {})
        self.assertEqual(result["status"], "MATCHED")
        self.assertEqual([item["corner"] for item in result["observations"]], ["T1"])
        self.assertEqual(result["observations"][0]["metrics"]["exit_speed_kph"], {"target": 130, "reference": 140})
        self.assertIn("확정하지", result["hypothesis"])
        self.assertEqual(analyzer._structured_feedback(api.AnalyzeRequest(feedback_speed="HIGH"), corners[:1], {})["status"], "NO_MATCH")

    def test_question_placeholder_yaml_preserves_full_setup(self):
        raw = "DriverInfo:\n  Drivers:\n  - UserName: ? ?\n    Initials: ? # placeholder\n    AbbrevName: ? driver\nCarSetup:\n  Chassis:\n    Rear:\n      RearWingAngle: 5 deg\n"
        repaired, count = api.sanitize_iracing_session_yaml(raw)
        self.assertEqual(count, 3)
        data = api.yaml.safe_load(repaired)
        self.assertEqual(data["DriverInfo"]["Drivers"][0]["UserName"], "? ?")
        self.assertEqual(data["DriverInfo"]["Drivers"][0]["AbbrevName"], "? driver")
        self.assertEqual(data["CarSetup"]["Chassis"]["Rear"]["RearWingAngle"], "5 deg")

    def test_ab_setup_values_available_without_memory(self):
        baseline = type("Telemetry", (), {"setup": {"Chassis": {"Rear": {"RearWingAngle": "5 deg"}}}, "laps": []})()
        candidate = type("Telemetry", (), {"setup": {"Chassis": {"Rear": {"RearWingAngle": "6 deg"}}}, "laps": []})()
        with patch.object(api, "telemetry_reference_conditions", side_effect=[{"track_temp_c": 25}, {"track_temp_c": 27}]):
            evidence = api.experiment_setup_evidence(baseline, candidate)
        self.assertEqual(evidence["canonical_setup_changes"][0]["baseline_value"], 5)
        self.assertEqual(evidence["canonical_setup_changes"][0]["candidate_value"], 6)
        self.assertEqual(evidence["conditions"]["differences"]["track_temp_c"], {"baseline": 25, "candidate": 27})

    def test_database_context_closes_connection_and_rolls_back_failures(self):
        store = api.LongTermMemoryStore(self.directory / "isolated.sqlite3")
        with store._connect() as connection:
            connection.execute("CREATE TABLE sample (value INTEGER)")
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")
        with self.assertRaises(RuntimeError):
            with store._connect() as connection:
                connection.execute("INSERT INTO sample VALUES (1)")
                raise RuntimeError("rollback")
        with store._connect() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM sample").fetchone()[0], 0)

    def exploration_fixture(self):
        analyzer = api.TelemetryAnalyzer.__new__(api.TelemetryAnalyzer)
        analyzer.telemetry = type("Telemetry", (), {"setup": {"Chassis": {"Rear": {"ArbBlades": "3"}}}})()
        analyzer.valid_laps = [object()]
        request = api.AnalyzeRequest(feedback_phase="ENTRY", feedback_speed="HIGH", feedback_symptom="UNDERSTEER")
        corners = [{"id": "C1", "label": "Corner 1", "target": {"min_speed_kph": 180, "peak_brake": .4}}]
        feedback = analyzer._structured_feedback(request, corners, {})
        return analyzer, request, feedback

    def test_one_irregular_lap_can_offer_unconfirmed_setup_exploration(self):
        analyzer, request, feedback = self.exploration_fixture()
        result = analyzer._setup_recommendation(api.SessionMode.BASELINE_3LAP,
            {"label": "INSUFFICIENT_DATA"}, [], {}, {}, {}, request=request, feedback=feedback)
        self.assertEqual(result["decision"], "TEST")
        self.assertEqual(result["decision_basis"], "DRIVER_FEEDBACK_EXPLORATION")
        self.assertEqual(result["confirmed_adjustments"], [])
        self.assertFalse(result["exploration"]["requires_consistent_laptimes"])
        self.assertEqual(len(result["test_candidates"]), 1)
        candidate = result["test_candidates"][0]
        self.assertEqual((candidate["original_value"], candidate["value"]), (3, 4))
        self.assertEqual(candidate["tier"], "EXPLORATORY")
        self.assertFalse(candidate["effect_confirmed"])
        self.assertLessEqual(candidate["confidence"], .4)
        self.assertIn("입증한 것은 아닙니다", candidate["evidence"])

    def test_evidence_mode_keeps_original_candidate_requirements(self):
        analyzer, request, feedback = self.exploration_fixture()
        result = analyzer._setup_recommendation(api.SessionMode.BASELINE_3LAP,
            {"label": "DRIVER_LIMITED"}, [], {}, {}, {},
            request=request.model_copy(update={"setup_mode": "EVIDENCE"}), feedback=feedback)
        self.assertEqual(result["decision"], "HOLD")
        self.assertEqual(result["test_candidates"], [])
        self.assertEqual(result["exploration"]["status"], "DISABLED")

    def test_exploration_needs_actual_values_and_a_matching_corner(self):
        _, request, feedback = self.exploration_fixture()
        scenarios = [({}, feedback, 1, "NO_SUPPORTED_SETTING"),
                     ({"rear_arb_blades": 3}, feedback, 0, "NEEDS_VALID_LAP"),
                     ({"rear_arb_blades": 3}, {"status": "NO_MATCH"}, 1, "NO_MATCHING_CORNER")]
        for setup, observed, laps, status in scenarios:
            with self.subTest(status=status):
                result = api.setup_exploration_candidate(request, setup, observed, laps, set())
                self.assertEqual(result["status"], status)
                self.assertNotIn("candidate", result)
        result = api.setup_exploration_candidate(api.AnalyzeRequest(), {"rear_arb_blades": 3}, feedback, 1, set())
        self.assertEqual(result["status"], "NEEDS_FEEDBACK")

    def test_stability_exploration_respects_boundaries_and_existing_evidence(self):
        _, request, feedback = self.exploration_fixture()
        request = request.model_copy(update={"feedback_symptom": "OVERSTEER", "feedback_speed": "LOW"})
        result = api.setup_exploration_candidate(request, {"rear_arb_blades": 3}, feedback, 1, set())
        self.assertEqual(result["candidate"]["value"], 2)
        self.assertTrue(result["candidate"]["validator"]["passed"])
        result = api.setup_exploration_candidate(request, {"rear_arb_blades": 1}, feedback, 1, set())
        self.assertNotIn("candidate", result)
        result = api.setup_exploration_candidate(request, {"rear_arb_blades": 3}, feedback, 1, {"rear_arb_blades"})
        self.assertEqual(result["status"], "EXISTING_CANDIDATE")
        result = api.setup_exploration_candidate(request.model_copy(update={"feedback_symptom": "BRAKING"}),
            {"brake_bias_pct": 53}, feedback, 1, set())
        self.assertEqual(result["status"], "NEEDS_BRAKING_DETAIL")
        self.assertNotIn("candidate", result)

    def test_personal_history_cannot_promote_exploration_to_verified(self):
        candidate = {"source": "DRIVER_FEEDBACK_EXPLORATION", "confidence": .9, "priority_score": .8, "effect_confirmed": True}
        result = api.cap_exploratory_setup_candidates({"test_candidates": [candidate], "experimental_candidates": [candidate]})
        self.assertEqual(candidate["confidence"], .9)  # Input objects stay untouched.
        for key in ("test_candidates", "experimental_candidates"):
            item = result[key][0]
            self.assertEqual(item["confidence"], .4)
            self.assertFalse(item["effect_confirmed"])
            self.assertEqual(item["tier"], "EXPLORATORY")

    def test_exploration_coach_does_not_reimpose_consistency_or_ab_gate(self):
        _, request, feedback = self.exploration_fixture()
        candidate = api.setup_exploration_candidate(request, {"rear_arb_blades": 3}, feedback, 1, set())["candidate"]
        coach = api.build_closed_loop_coach({"setup_recommendation": {"test_candidates": [candidate]},
            "session_policy": {"session_mode": "BASELINE_3LAP"}, "laps": []}, {}, {})
        self.assertEqual(coach["next_run"]["mode"], "SETUP_EXPLORATION")
        self.assertEqual(coach["next_run"]["required_valid_laps"], 1)
        self.assertEqual(coach["next_run"]["effect_confirmation_minimum_laps"], 3)


if __name__ == "__main__":
    unittest.main()
