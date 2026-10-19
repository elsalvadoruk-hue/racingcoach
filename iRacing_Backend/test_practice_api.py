"""Practice routes with isolated storage: start from the last analysis, refuse other files, close."""
import atexit
import os
from pathlib import Path
import tempfile
import unittest

_runtime = tempfile.TemporaryDirectory(prefix="iracing-practice-", ignore_cleanup_errors=True)
atexit.register(_runtime.cleanup)
_root = Path(_runtime.name)
os.environ.update({
    "IRACING_TUNER_DB_PATH": str(_root / "memory.sqlite3"),
    "IRACING_FILE_INDEX_PATH": str(_root / "index.sqlite3"),
    "IRACING_EXTERNAL_REFERENCE_DIR": str(_root / "references"),
    "GARAGE61_API_TOKEN_FILE": str(_root / "no-token.txt"),
    "GARAGE61_AUTO_COLLECT": "0", "IRACING_AI_ENABLED": "0", "IRACING_IMPORT_LEGACY": "0",
})

from fastapi.testclient import TestClient
import backend_api as api
from practice import PracticeStore
from test_practice import FOCUS, run


class PracticeRouteTests(unittest.TestCase):
    def setUp(self):
        self.default_path = api.practice_store.path
        self.saved = (api.practice_store, api.shared_sync_state.get("analysis_result"))
        api.practice_store = PracticeStore(_root / "practice-tasks.json")
        result = run("a.ibt")
        result["closed_loop_coach"] = {"ranked_focuses": [FOCUS]}
        api.shared_sync_state["analysis_result"] = result
        self.client = TestClient(api.app)

    def tearDown(self):
        api.practice_store, api.shared_sync_state["analysis_result"] = self.saved

    def test_the_store_lives_in_the_data_folder_not_the_personal_one(self):
        self.assertEqual(self.default_path.parent, Path(api.MEMORY_DB_PATH).parent)
        # Whichever test module imported backend_api first, its DB (and so practice-tasks.json) is a temp one.
        self.assertTrue(Path(api.MEMORY_DB_PATH).resolve().is_relative_to(Path(tempfile.gettempdir()).resolve()))

    def test_start_then_close(self):
        response = self.client.post("/api/practice/start", json={"filename": "a.ibt", "focus_index": 0})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["practice"]["check"]["status"], "WAITING")
        task_id = body["task"]["id"]
        closed = self.client.post(f"/api/practice/{task_id}/close", json={"status": "DONE"})
        self.assertEqual(closed.status_code, 200)
        self.assertEqual(closed.json()["history"]["by_cause"][0]["done"], 1)

    def test_start_refuses_a_file_that_is_not_the_last_analysis(self):
        response = self.client.post("/api/practice/start", json={"filename": "other.ibt", "focus_index": 0})
        self.assertEqual(response.status_code, 409)

    def test_start_refuses_a_missing_focus_and_close_an_unknown_task(self):
        self.assertEqual(self.client.post("/api/practice/start", json={"filename": "a.ibt", "focus_index": 3}).status_code, 404)
        self.assertEqual(self.client.post("/api/practice/nope/close", json={"status": "DONE"}).status_code, 404)
        self.assertEqual(self.client.post("/api/practice/nope/close", json={"status": "WON"}).status_code, 422)

    def test_the_driver_picks_whether_the_focus_is_measured_on_own_laps_or_garage61(self):
        mine = dict(FOCUS, source="PERSONAL_LAP_COMPARISON")
        fast = dict(FOCUS, source="GARAGE61_REFERENCE_CONSENSUS")
        api.shared_sync_state["analysis_result"]["practice_focuses"] = {"PERSONAL": [mine], "GARAGE61": [fast]}
        task = self.client.post("/api/practice/start", json={"filename": "a.ibt", "focus_index": 0, "source": "GARAGE61"}).json()["task"]
        self.assertEqual(task["reference_source"], "GARAGE61_REFERENCE_CONSENSUS")
        task = self.client.post("/api/practice/start", json={"filename": "a.ibt", "focus_index": 0, "source": "PERSONAL"}).json()["task"]
        self.assertEqual(task["reference_source"], "PERSONAL_LAP_COMPARISON")
        # no Garage61 laps in this analysis: nothing to start from that list
        api.shared_sync_state["analysis_result"]["practice_focuses"] = {"PERSONAL": [mine], "GARAGE61": []}
        self.assertEqual(self.client.post("/api/practice/start", json={"filename": "a.ibt", "focus_index": 0, "source": "GARAGE61"}).status_code, 404)

    def test_both_focus_lists_come_from_one_analysis(self):
        loss = {"corner": "T1", "delta_s": 0.3}
        result = {"external_reference": {"top_losses": [loss]}, "performance": {"top_losses": [loss]},
                  "closed_loop_coach": {"ranked_focuses": ["g61"]}}
        seen = []
        original = api.build_closed_loop_coach
        api.build_closed_loop_coach = lambda r, *_: seen.append(r["external_reference"]) or {"ranked_focuses": ["mine"]}
        try:
            sets = api.practice_focus_sets(result, {}, {})
        finally:
            api.build_closed_loop_coach = original
        self.assertEqual(sets, {"PERSONAL": ["mine"], "GARAGE61": ["g61"]})
        self.assertEqual(seen, [{}])                        # the personal list ignores the Garage61 losses
        self.assertEqual(api.practice_focus_sets({"closed_loop_coach": {"ranked_focuses": ["mine"]}}, {}, {}),
                         {"PERSONAL": ["mine"], "GARAGE61": []})


if __name__ == "__main__":
    unittest.main()


class DrillRouteTests(unittest.TestCase):
    def setUp(self):
        self.saved = (api.practice_store, api.shared_sync_state.get("analysis_result"))
        api.practice_store = PracticeStore(_root / "drill-tasks.json")
        result = run("a.ibt")
        result["closed_loop_coach"] = {"ranked_focuses": [FOCUS]}
        result["visualizations"] = {"track_length_m": 3000.0}
        api.shared_sync_state["analysis_result"] = result
        self.client = TestClient(api.app)
        self.task = self.client.post("/api/practice/start", json={"filename": "a.ibt", "focus_index": 0}).json()["task"]

    def tearDown(self):
        self.client.delete("/api/drill")
        api.practice_store, api.shared_sync_state["analysis_result"] = self.saved

    def test_start_status_and_stop(self):
        started = self.client.post(f"/api/practice/{self.task['id']}/drill", json={"filename": "a.ibt", "corner_label": "5번 코너"})
        self.assertEqual(started.status_code, 200)
        plan = started.json()["plan"]
        self.assertEqual((plan["label"], plan["cause"], plan["task_id"]), ("5번 코너", "THROTTLE_RETURN_LATE", self.task["id"]))
        self.assertEqual(plan["base_value"], 0.30)                      # the task's own full-throttle point
        self.assertTrue(self.client.get("/api/drill").json()["active"])
        self.assertEqual(self.client.delete("/api/drill").json(), {"active": False})

    def test_another_file_or_task_is_refused(self):
        self.assertEqual(self.client.post(f"/api/practice/{self.task['id']}/drill", json={"filename": "b.ibt", "corner_label": "5번 코너"}).status_code, 409)
        self.assertEqual(self.client.post("/api/practice/nope/drill", json={"filename": "a.ibt", "corner_label": "5번 코너"}).status_code, 404)
        self.assertEqual(self.client.post(f"/api/practice/{self.task['id']}/drill", json={"filename": "a.ibt", "corner_label": "99번 코너"}).status_code, 422)


class DrillFuelTests(unittest.TestCase):
    def test_fuel_is_the_task_laps_start_plus_two_warm_up_laps(self):
        from unittest.mock import patch
        import numpy as np
        lap = api.LapSegment(3, 100, 199, True, True, "VALID", 100., 0., 1., 1., 0, None)
        telemetry = type("T", (), {"laps": [lap], "channels": {"FuelLevel": np.r_[np.full(100, 60.0), np.full(100, 41.3)]}})()
        result = {"comparison": {"target_lap": 3}, "laps": [{"status": "VALID", "fuel_used_l": 2.4}, {"status": "VALID", "fuel_used_l": 2.6},
                                                           {"status": "OFF_TRACK", "fuel_used_l": 9.0}]}
        with patch.object(api, "resolve_telemetry_file", lambda name: _root / "a.ibt"), patch.object(api.repository, "load", lambda path: telemetry):
            fuel = api.drill_fuel(result, "a.ibt")
        self.assertEqual(fuel, {"lap": 3, "lap_start_l": 41.3, "per_lap_l": 2.5, "warm_laps": 2, "suggest_l": 47})
        with patch.object(api, "resolve_telemetry_file", lambda name: _root / "a.ibt"), patch.object(api.repository, "load", lambda path: telemetry):
            self.assertEqual(api.drill_fuel(result, "a.ibt", 1)["suggest_l"], 44)     # a long lap: one warm-up lap
        self.assertEqual(api.drill_lap_time({"comparison": {"target_time_s": 118.4}}), 118.4)
        self.assertEqual(api.drill_lap_time({"comparison": {}, "laps": [{"status": "VALID", "lap_time_s": 70.0}, {"status": "VALID", "lap_time_s": 72.0}]}), 71.0)
        self.assertIsNone(api.drill_fuel({"comparison": {}, "laps": []}, "a.ibt"))


class DrillReferenceTests(unittest.TestCase):
    """A task measured against Garage61 fast laps beeps at their braking and full-throttle points."""

    def setUp(self):
        self.saved = (api.practice_store, api.shared_sync_state.get("analysis_result"))
        api.practice_store = PracticeStore(_root / "drill-ref-tasks.json")
        self.client = TestClient(api.app)

    def tearDown(self):
        self.client.delete("/api/drill")
        api.practice_store, api.shared_sync_state["analysis_result"] = self.saved

    def start(self, source, external_ready=True):
        result = run("a.ibt")
        result["closed_loop_coach"] = {"ranked_focuses": [dict(FOCUS, source=source)]}
        result["visualizations"] = {"track_length_m": 3000.0}
        result["comparison"] = {"reference_lap": 7}
        fast = [dict(corner, reference={"brake_start_pct": 0.232, "full_throttle_pct": 0.262}) for corner in result["performance"]["corners"]]
        result["external_reference"] = {"status": "READY" if external_ready else "UNAVAILABLE", "corners": fast,
                                        "aggregate": {"reference_label": "Garage61 조건 가중 종합 기준 · 5랩"}}
        api.shared_sync_state["analysis_result"] = result
        task = self.client.post("/api/practice/start", json={"filename": "a.ibt", "focus_index": 0}).json()["task"]
        self.assertEqual(task["reference_source"], source)
        return self.client.post(f"/api/practice/{task['id']}/drill", json={"filename": "a.ibt", "corner_label": "5번 코너"}).json()["plan"]

    def test_garage61_task_beeps_at_the_fast_laps(self):
        plan = self.start("GARAGE61_REFERENCE_CONSENSUS")
        self.assertEqual((plan["reference_source"], plan["reference_label"]), ("GARAGE61", "Garage61 조건 가중 종합 기준 · 5랩"))
        self.assertEqual((plan["brake_pct"], plan["throttle_pct"]), (0.232, 0.262))

    def test_personal_task_or_missing_garage61_keeps_the_own_reference_lap(self):
        plan = self.start("PERSONAL_LAP_COMPARISON")
        self.assertEqual((plan["reference_source"], plan["reference_label"]), ("PERSONAL", "내 기준 랩 7"))
        self.assertNotEqual(plan["brake_pct"], 0.232)
        self.tearDown(); self.setUp()
        plan = self.start("GARAGE61_REFERENCE_CONSENSUS", external_ready=False)
        self.assertEqual(plan["reference_source"], "PERSONAL")
