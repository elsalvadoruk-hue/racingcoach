"""A/B attribution regressions with isolated persistence and no external calls."""
import atexit
import copy
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

_runtime = tempfile.TemporaryDirectory(prefix="iracing-ab-policy-")
atexit.register(_runtime.cleanup)
_root = Path(_runtime.name)
os.environ.update({"IRACING_TUNER_DB_PATH": str(_root / "memory.sqlite3"),
                   "IRACING_EXTERNAL_REFERENCE_DIR": str(_root / "references"),
                   "GARAGE61_API_TOKEN_FILE": str(_root / "no-token.txt"),
                   "GARAGE61_AUTO_COLLECT": "0", "IRACING_AI_ENABLED": "0", "IRACING_IMPORT_LEGACY": "0"})

import backend_api as api


def run_result(times=(100, 100.1, 99.9), **environment):
    conditions = {"fuel_l": 40, "track_temp_c": 25, "weather_declared_wet": False,
                  "tire_compound": 0, "weight_penalty_kg": 0, "power_adjust_pct": 0}
    conditions.update(environment)
    return {"session": {"environment": conditions, "track_temp_c": conditions["track_temp_c"], "track_length_m": 4000},
            "laps": [{"lap_number": index + 1, "status": "VALID", "lap_time_s": value,
                      "start_pct": 0, "end_pct": 1, "on_track_ratio": 1} for index, value in enumerate(times)],
            "vehicle": {}, "performance": {"corners": []}}


def wing_evidence(extra=None):
    diff = {"Chassis.Rear.RearWingAngle": {"baseline": "5 deg", "candidate": "6 deg"}}
    diff.update(extra or {})
    return {"setup_diff": {"full_setup": diff}, "setup_data_available": True}


def corners_on_laps(corrections, relifts=None):
    return [{"id": "C1", "label": "C1", "apex_pct": .2, "speed_class": "LOW", "lap_number": index + 1,
             "target": {"steering_corrections": value, "throttle_relifts": relifts[index] if relifts is not None else 1,
                        "min_speed_kph": 90, "exit_speed_kph": 130}}
            for index, value in enumerate(corrections)]


class ABPolicyTests(unittest.TestCase):
    def compare(self, baseline=None, candidate=None, **kwargs):
        return api.compare_experiments(baseline or run_result(), candidate or run_result((99.6, 99.7, 99.5)),
                                       api.SessionMode.BASELINE_3LAP, setup_evidence=kwargs.pop("setup_evidence", wing_evidence()),
                                       goal=kwargs.pop("goal", "PACE"), **kwargs)

    def test_matched_single_control_pace_effect_can_be_confirmed(self):
        result = self.compare()
        self.assertEqual(result["verdict"], "KEEP")
        self.assertTrue(result["effect_confirmed"])
        self.assertTrue(result["learning_eligibility"]["eligible"])

    def test_condition_mismatches_prevent_setup_learning(self):
        for field, value in {"fuel_l": 5, "weather_declared_wet": True, "tire_compound": 1,
                             "weight_penalty_kg": 10, "power_adjust_pct": 2, "track_temp_c": 30}.items():
            with self.subTest(field=field):
                result = self.compare(candidate=run_result((98, 98.1, 97.9), **{field: value}))
                self.assertEqual(result["conditions_quality"]["status"], "MISMATCHED")
                self.assertEqual(result["verdict"], "INCONCLUSIVE")
                self.assertFalse(result["effect_confirmed"])
                self.assertFalse(result["learning_eligibility"]["eligible"])
                self.assertEqual(result["performance_axes"]["pace"]["status"], "IMPROVED")

    def test_unknown_required_conditions_are_visible_and_not_assumed_equal(self):
        result = self.compare(candidate=run_result((98, 98.1, 97.9), fuel_l=None, weather_declared_wet=None))
        self.assertEqual(result["conditions_quality"]["status"], "UNKNOWN")
        self.assertFalse(result["learning_eligibility"]["eligible"])
        self.assertTrue(result["conditions_quality"]["observation_allowed"])

    def test_changing_run_conditions_and_opposing_wind_prevent_learning(self):
        baseline = run_result(wind_direction_deg=350)
        candidate = run_result((98, 98.1, 97.9), wind_direction_deg=10)
        self.assertEqual(self.compare(baseline, candidate)["conditions_quality"]["status"], "MATCHED")
        candidate["session"]["environment"]["wind_direction_deg"] = 180
        self.assertEqual(self.compare(baseline, candidate)["conditions_quality"]["status"], "MISMATCHED")
        candidate = run_result((98, 98.1, 97.9), unstable_conditions=["track_wetness_code"])
        self.assertFalse(self.compare(candidate=candidate)["learning_eligibility"]["eligible"])

    def test_unregistered_second_setup_control_is_a_confounder(self):
        result = self.compare(setup_evidence=wing_evidence({"Chassis.LeftRear.BumpDamping": {"baseline": 8, "candidate": 12}}))
        self.assertEqual(result["learning_eligibility"]["logical_change_count"], 2)
        self.assertTrue(result["learning_eligibility"]["confounded"])
        self.assertEqual(result["verdict"], "INCONCLUSIVE")
        self.assertEqual(result["observed_verdict"], "KEEP")

    def test_setup_names_do_not_count_as_controls(self):
        result = self.compare(setup_evidence=wing_evidence({"SetupName": {"baseline": "base", "candidate": "candidate"}}))
        self.assertEqual(result["learning_eligibility"]["logical_change_count"], 1)
        self.assertTrue(result["learning_eligibility"]["eligible"])

    def test_missing_baseline_setup_value_cannot_confirm_effect(self):
        evidence = {"setup_diff": {"full_setup": {"Chassis.Rear.RearWingAngle": {"baseline": None, "candidate": "6 deg"}}}}
        result = self.compare(setup_evidence=evidence)
        self.assertEqual(result["verdict"], "INCONCLUSIVE")
        self.assertFalse(result["effect_confirmed"])
        self.assertFalse(result["learning_eligibility"]["eligible"])

    def test_mirrored_registered_height_changes_form_one_control(self):
        diff = {f"Chassis.{side}.RideHeight": {"baseline": "50 mm", "candidate": "51 mm"}
                for side in ("LeftFront", "RightFront")}
        result = self.compare(setup_evidence={"setup_diff": {"full_setup": diff}})
        self.assertFalse(result["learning_eligibility"]["confounded"])
        self.assertEqual(result["learning_eligibility"]["logical_change_count"], 1)

    def test_bmw_raw_setup_aliases_map_to_canonical_controls(self):
        cases = [("Chassis.FrontBrakes.ArbBlades", "3", "4", "front_arb_blades"),
                 ("Chassis.Rear.WingAngle", "-1 degrees", "0 degrees", "rear_wing_angle_deg"),
                 ("Chassis.GearsDifferential.DiffPreload", "80 Nm", "90 Nm", "diff_preload_nm")]
        for key, before, after, expected in cases:
            with self.subTest(key=key):
                changes = api.canonical_setup_changes({key: {"baseline": before, "candidate": after}})
                self.assertEqual(changes[0]["setting"], expected)
                self.assertEqual(changes[0]["raw_keys"], [key])

    def test_survivor_laps_do_not_hide_offtrack_failure_rate(self):
        candidate = run_result((99.6, 99.7, 99.5))
        candidate["laps"] += [{"lap_number": index + 4, "status": "INVALID", "lap_time_s": None,
                               "start_pct": 0, "end_pct": 1, "on_track_ratio": .8,
                               "invalid_reason": "off-track samples present"} for index in range(7)]
        result = self.compare(candidate=candidate)
        self.assertEqual(result["candidate"]["success_rate"], .3)
        self.assertEqual(result["performance_axes"]["stability"]["status"], "WORSENED")
        self.assertEqual(result["verdict"], "REVERT")

    def test_outlap_and_recording_cutoffs_are_excluded_from_success_rate(self):
        result = run_result()
        result["laps"].insert(0, {"lap_number": 0, "status": "OUT_LAP"})
        result["laps"].append({"lap_number": 4, "status": "PARTIAL", "start_pct": 0, "end_pct": .5, "on_track_ratio": 1})
        metrics = api.experiment_metrics(result, api.SessionMode.BASELINE_3LAP)
        self.assertEqual(metrics["attempted_laps"], 3)
        self.assertEqual(metrics["excluded_out_or_cutoff_laps"], 2)
        self.assertEqual(metrics["success_rate"], 1)

    def test_speed_only_is_not_driveability_evidence(self):
        axis = api._ab_driveability_axis({"overall_corner_deltas": {"min_speed_kph": 2, "exit_speed_kph": 2}})
        self.assertEqual(axis["status"], "UNAVAILABLE")

    def test_corner_effect_uses_run_medians_and_preserves_sample_support(self):
        baseline, candidate = run_result(), run_result()
        baseline["ab_corner_metrics"] = corners_on_laps([10, 10, 0])
        candidate["ab_corner_metrics"] = corners_on_laps([1, 1, 100])
        profile = api.experiment_effect_profile(baseline, candidate)
        self.assertEqual(profile["overall_corner_deltas"]["steering_corrections"], -9)
        self.assertEqual(profile["baseline_observation_laps"], 3)
        self.assertEqual(profile["aggregation"], "ACROSS_VALID_LAPS_MEDIAN")

    def test_subjective_improvement_is_saved_separately_with_insufficient_samples(self):
        feedback = {"change": "IMPROVED", "rating": 4, "side_effects": "less exit grip"}
        result = self.compare(run_result((100,)), run_result((99,)), goal="COMFORT", driver_feedback=feedback)
        self.assertEqual(result["verdict"], "INCONCLUSIVE")
        self.assertEqual(result["subjective_feedback"]["preference"], "PREFER_CANDIDATE")
        self.assertEqual(result["subjective_feedback"]["side_effects"], "less exit grip")
        self.assertFalse(result["subjective_feedback"]["effect_confirmed"])

    def test_comfort_requires_stability_or_input_evidence_not_faster_speed(self):
        result = self.compare(goal="COMFORT", driver_feedback={"change": "IMPROVED", "rating": 4})
        self.assertEqual(result["verdict"], "INCONCLUSIVE")
        self.assertFalse(result["effect_confirmed"])

    def test_repeated_input_improvement_can_confirm_comfort(self):
        baseline, candidate = run_result(), run_result()
        baseline["ab_corner_metrics"] = corners_on_laps([3, 3, 3], [2, 2, 2])
        candidate["ab_corner_metrics"] = corners_on_laps([1, 1, 1], [1, 1, 1])
        result = self.compare(baseline, candidate, goal="COMFORT", driver_feedback={"change": "IMPROVED", "rating": 4})
        self.assertEqual(result["verdict"], "KEEP")
        self.assertTrue(result["effect_confirmed"])
        conflicting = self.compare(baseline, candidate, goal="COMFORT", driver_feedback={"change": "WORSENED", "rating": 1})
        self.assertEqual(conflicting["verdict"], "INCONCLUSIVE")

    def test_endurance_goal_requires_longer_objective_sample(self):
        short = self.compare(goal="ENDURANCE")
        self.assertEqual(short["minimum_valid_laps_per_file"], 5)
        self.assertEqual(short["verdict"], "INCONCLUSIVE")
        baseline = run_result((100, 100.1, 99.9, 100, 100.1))
        candidate = run_result((99.6, 99.7, 99.5, 99.6, 99.7))
        self.assertEqual(self.compare(baseline, candidate, goal="ENDURANCE")["verdict"], "KEEP")

    def test_legacy_memory_deduplicates_and_rejects_confounded_or_unknown_results(self):
        result = self.compare()
        outcome = {"result_id": "ab-1", "verdict": "KEEP", "setup_diff": wing_evidence()["setup_diff"], "result": result}
        recommendation = {"experimental_candidates": [{"setting": "rear_wing_angle_deg", "original_value": 5,
                                                        "value": 6, "confidence": .4}]}
        sessions = [{"similarity_score": 1, "experiment_outcomes": [outcome]}, {"similarity_score": 1, "experiment_outcomes": [outcome]}]
        reinforced = api.reinforce_setup_recommendation_with_memory(copy.deepcopy(recommendation), sessions)
        self.assertEqual(reinforced["experimental_candidates"][0]["confidence"], .49)
        self.assertEqual(reinforced["memory_reinforcement"]["matching_outcomes"], 1)
        for invalid_outcome in [dict(outcome, result={"verdict": "KEEP"}),
                                dict(outcome, setup_diff=wing_evidence({"Chassis.LeftRear.BumpDamping": {"baseline": 8, "candidate": 12}})["setup_diff"])]:
            rejected = api.reinforce_setup_recommendation_with_memory(copy.deepcopy(recommendation), [{"experiment_outcomes": [invalid_outcome]}])
            self.assertEqual(rejected["experimental_candidates"][0]["confidence"], .4)

    def test_sensitivity_writer_removes_ineligible_old_attribution(self):
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        columns = "event_id result_id created_at car_name track_name track_config session_mode setting setting_label direction baseline_value candidate_value delta_value unit verdict median_delta_s best_delta_s track_temp_delta_c confidence confounded effects_json".split()
        connection.execute("CREATE TABLE setup_sensitivity_events (" + ",".join(name + " TEXT" for name in columns) + ")")
        store = api.LongTermMemoryStore.__new__(api.LongTermMemoryStore)
        comparison = self.compare()
        kwargs = dict(result_id="ab-1", baseline={"session": {"car_name": "Car"}}, candidate={},
                      session_mode="BASELINE_3LAP", full_setup_diff=wing_evidence()["setup_diff"]["full_setup"])
        update = store._store_setup_sensitivity_events(connection, comparison=comparison, **kwargs)
        self.assertEqual(update["stored_events"], 1)
        comparison["conditions_quality"]["status"] = "UNKNOWN"
        update = store._store_setup_sensitivity_events(connection, comparison=comparison, **kwargs)
        self.assertEqual(update["stored_events"], 0)
        self.assertEqual(connection.execute("SELECT count(*) FROM setup_sensitivity_events").fetchone()[0], 0)

    def test_trial_request_validates_rating_and_keeps_existing_defaults(self):
        request = api.ExperimentRequest(baseline_file="a.ibt", candidate_file="b.ibt", trial_id="trial-1",
                                       driver_feedback={"change": "SAME", "rating": 3, "side_effects": "none"})
        self.assertEqual(request.goal, "COMFORT")
        with self.assertRaises(ValueError):
            api.ExperimentRequest(baseline_file="a.ibt", candidate_file="b.ibt", driver_feedback={"change": "SAME", "rating": 6})


if __name__ == "__main__":
    unittest.main()
