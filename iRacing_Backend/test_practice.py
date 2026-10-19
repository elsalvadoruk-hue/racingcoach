import json
import tempfile
import unittest
from pathlib import Path

from practice import PracticeStore, check, corner_snapshot


def run(name, full_throttle=0.30, times=(10.0, 10.1, 10.2), apex=0.25, track="179", laps=True):
    """A small analysis result with one focus corner (C1) and one other corner (C2)."""
    corners = [
        {"id": "C1", "label": "5번 코너", "apex_pct": apex, "entry_pct": apex - 0.02, "exit_pct": apex + 0.02,
         "target": {"full_throttle_pct": full_throttle, "min_speed_kph": 90.0}},
        {"id": "C2", "label": "9번 코너", "apex_pct": 0.6, "entry_pct": 0.58, "exit_pct": 0.62,
         "target": {"full_throttle_pct": 0.65, "min_speed_kph": 120.0}},
    ]
    consistency = {"corners": [{"id": "C1", "passes": [{"lap": i + 1, "time_s": t} for i, t in enumerate(times)]},
                               {"id": "C2", "passes": [{"lap": 1, "time_s": 5.0}]}]} if laps else None
    return {
        "file": {"name": name},
        "session": {"car_path": "mclaren720sgt3", "car_name": "McLaren", "track_platform_id": track,
                    "track_name": "Long Beach", "track_config": "", "track_length_m": 3000.0},
        "performance": {"corners": corners, "corner_consistency": consistency},
    }


FOCUS = {"root_cause": "THROTTLE_RETURN_LATE", "root_cause_label": "풀스로틀 복귀 지연",
         "affected_corners": ["5번 코너"], "action": "5번 코너에서 더 일찍 풀스로틀로", "total_observed_loss_s": 0.3}


class PracticeTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / "practice.json"
        self.store = PracticeStore(self.path)

    def tearDown(self):
        self.folder.cleanup()

    def test_snapshot_keeps_the_focus_corner_and_its_median_time(self):
        snap = corner_snapshot(run("a.ibt"), ["C1"])
        self.assertEqual([c["id"] for c in snap], ["C1"])
        self.assertAlmostEqual(snap[0]["median_s"], 10.1)
        self.assertEqual(snap[0]["laps"], 3)

    def test_the_file_a_task_came_from_does_not_check_itself(self):
        first = run("a.ibt")
        self.store.start(first, FOCUS)
        view = self.store.record(first)
        self.assertEqual(view["check"]["status"], "WAITING")
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["tasks"][0]["checks"], [])

    def test_earlier_throttle_and_a_faster_corner_is_achieved(self):
        self.store.start(run("a.ibt"), FOCUS)
        # 0.002 of 3000 m = 6 m earlier full throttle; corner median 10.1 -> 9.9
        view = self.store.record(run("b.ibt", full_throttle=0.298, times=(9.8, 9.9, 10.0)))
        result = view["check"]
        self.assertEqual(result["status"], "ACHIEVED")
        self.assertAlmostEqual(result["corners"][0]["input_change"], -6.0)
        self.assertAlmostEqual(result["total_gain_s"], 0.2)

    def test_changed_input_without_time_gain_is_input_only(self):
        self.store.start(run("a.ibt"), FOCUS)
        result = self.store.record(run("b.ibt", full_throttle=0.298, times=(10.2, 10.3, 10.4)))["check"]
        self.assertEqual(result["status"], "INPUT_ONLY")
        self.assertIn("줄지 않았습니다", result["summary"])

    def test_faster_without_the_asked_input_is_faster_other(self):
        self.store.start(run("a.ibt"), FOCUS)
        result = self.store.record(run("b.ibt", full_throttle=0.30, times=(9.8, 9.9, 10.0)))["check"]
        self.assertEqual(result["status"], "FASTER_OTHER")

    def test_nothing_changed_is_not_yet(self):
        self.store.start(run("a.ibt"), FOCUS)
        self.assertEqual(self.store.record(run("b.ibt"))["check"]["status"], "NOT_YET")

    def test_without_corner_times_it_says_the_time_was_not_compared(self):
        self.store.start(run("a.ibt"), FOCUS)
        result = self.store.record(run("b.ibt", full_throttle=0.298, laps=False))["check"]
        self.assertEqual(result["status"], "INPUT_ONLY")
        self.assertIsNone(result["total_gain_s"])
        self.assertIn("비교하지 못했습니다", result["summary"])

    def test_a_corner_that_cannot_be_found_is_no_data(self):
        self.store.start(run("a.ibt"), FOCUS)
        result = self.store.record(run("b.ibt", apex=0.40))["check"]
        self.assertEqual(result["status"], "NO_DATA")
        self.assertFalse(result["corners"][0]["matched"])

    def test_another_track_has_no_active_task(self):
        self.store.start(run("a.ibt"), FOCUS)
        view = self.store.record(run("b.ibt", track="18"))
        self.assertIsNone(view["active"])
        self.assertIsNone(view["check"])

    def test_one_check_per_file_and_it_survives_a_restart(self):
        self.store.start(run("a.ibt"), FOCUS)
        self.store.record(run("b.ibt"))
        self.store.record(run("b.ibt", full_throttle=0.298, times=(9.8, 9.9, 10.0)))
        again = PracticeStore(self.path)
        task = again.active({"car": "mclaren720sgt3", "track": "179", "config": ""})
        self.assertEqual(len(task["checks"]), 1)
        self.assertEqual(task["checks"][0]["status"], "ACHIEVED")

    def test_a_new_task_replaces_the_old_one_and_history_counts_causes(self):
        first = self.store.start(run("a.ibt"), FOCUS)
        self.store.record(run("b.ibt", full_throttle=0.298, times=(9.8, 9.9, 10.0)))
        second = self.store.start(run("b.ibt"), dict(FOCUS, root_cause="BRAKE_RELEASE_LATE", root_cause_label="브레이크 해제 지연"))
        statuses = {t["id"]: t["status"] for t in self.store.data["tasks"]}
        self.assertEqual(statuses[first["id"]], "REPLACED")
        self.store.close(second["id"], "DONE")
        history = self.store.history()
        causes = {e["root_cause"]: e for e in history["by_cause"]}
        self.assertEqual(set(causes), {"BRAKE_RELEASE_LATE"})      # a replaced task is not counted as tried
        self.assertEqual(causes["BRAKE_RELEASE_LATE"]["done"], 1)

    def test_close_rejects_unknown_status_and_task(self):
        task = self.store.start(run("a.ibt"), FOCUS)
        with self.assertRaises(ValueError):
            self.store.close(task["id"], "WON")
        with self.assertRaises(KeyError):
            self.store.close("missing", "DONE")

    def test_a_focus_without_measured_corners_cannot_start(self):
        with self.assertRaises(ValueError):
            self.store.start(run("a.ibt"), dict(FOCUS, affected_corners=["99번 코너"]))

    def test_gain_found_is_the_best_check_of_a_finished_task(self):
        task = self.store.start(run("a.ibt"), FOCUS)
        self.store.record(run("b.ibt", full_throttle=0.298, times=(9.8, 9.9, 10.0)))
        self.store.record(run("c.ibt", times=(10.3, 10.4, 10.5)))
        self.store.close(task["id"], "DONE")
        entry = self.store.history()["by_cause"][0]
        self.assertAlmostEqual(entry["gain_s"], 0.2)

    def test_check_uses_the_task_track_length_when_the_new_run_has_none(self):
        task = self.store.start(run("a.ibt"), FOCUS)
        later = run("b.ibt", full_throttle=0.298)
        later["session"]["track_length_m"] = None
        self.assertAlmostEqual(check(task, later)["corners"][0]["input_change"], -6.0)


if __name__ == "__main__":
    unittest.main()
