"""Analysis and A/B API regressions with synthetic telemetry and isolated storage."""
from contextlib import ExitStack
import asyncio
from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
from fastapi.testclient import TestClient

# This fixture sets temporary persistence paths before importing backend_api.
from test_rolling_best import BackToBackLaps, api
from test_practice import FOCUS, run
from practice import PracticeStore


class AnalysisStateTests(BackToBackLaps, unittest.TestCase):
    def setUp(self):
        super().setUp()
        source = self.telemetry([(50, 50), (50, 50), (49, 50)])
        self.sources = {}
        for name in ("baseline.ibt", "candidate.ibt"):
            path = Path(self.work.name) / name
            path.write_bytes(name.encode("ascii"))
            self.sources[name] = replace(source, path=path, signature=api.file_signature(path))
        self.current = run("displayed.ibt")
        self.current["closed_loop_coach"] = {"ranked_focuses": [FOCUS]}
        self.state = {"analysis_result": self.current, "last_analysis_time": 123.0,
                      "file_signature": ("displayed.ibt", 1, 1)}
        self.store = PracticeStore(Path(self.work.name) / "practice.json")
        patches = self.enterContext(ExitStack())
        patches.enter_context(patch.object(api, "shared_sync_state", self.state))
        patches.enter_context(patch.object(api, "memory_store", None))
        patches.enter_context(patch.object(api, "practice_store", self.store))
        self.record = patches.enter_context(patch.object(self.store, "record", wraps=self.store.record))
        self.refresh = patches.enter_context(patch.object(api, "refresh_live_practice"))
        patches.enter_context(patch.object(api, "resolve_telemetry_file",
                                          side_effect=lambda name: self.sources[name].path))
        patches.enter_context(patch.object(api.repository, "load",
                                          side_effect=lambda path: self.sources[path.name]))
        patches.enter_context(patch.object(api, "review_notes", return_value={"notes": [], "pending": []}))
        self.client = TestClient(api.app)
        self.addCleanup(self.client.close)

    def test_ab_keeps_the_displayed_analysis_and_practice_start_available(self):
        before = dict(self.state)
        response = self.client.post("/api/experiments/compare", json={
            "baseline_file": "baseline.ibt", "candidate_file": "candidate.ibt"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["comparison"]["lap_distribution"]["baseline"]), 3)
        self.assertEqual(self.state, before)
        self.assertIs(self.state["analysis_result"], self.current)
        self.record.assert_not_called()
        self.refresh.assert_not_called()

        # A/B results leave the main analysis on screen, so its practice action must still work.
        start = self.client.post("/api/practice/start", json={"filename": "displayed.ibt", "focus_index": 0})
        self.assertEqual(start.status_code, 200, start.text)
        self.assertEqual(start.json()["task"]["source_file"], "displayed.ibt")

    def test_normal_analysis_still_updates_the_current_file_and_practice(self):
        response = self.client.post("/api/analyze", json={
            "filename": "baseline.ibt", "include_ai": False, "persist_memory": False})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.state["analysis_result"]["file"]["name"], "baseline.ibt")
        self.assertEqual(self.state["file_signature"], self.sources["baseline.ibt"].signature)
        self.assertGreater(self.state["last_analysis_time"], 123.0)
        self.record.assert_called_once()
        self.refresh.assert_called_once()
        self.assertIn("practice", response.json())

    def test_request_keeps_parsed_recording_after_repository_eviction(self):
        source = self.sources['baseline.ibt']
        with patch.object(api.repository, 'load', side_effect=AssertionError('recording was already read')):
            result = asyncio.run(api.load_analysis(api.AnalyzeRequest(filename=source.path.name),
                                                   parsed_telemetry=source, update_current_analysis=False))
        self.assertEqual(result['file']['name'], source.path.name)
        self.assertIs(self.state['analysis_result'], self.current)

    def test_changed_or_different_recording_never_reuses_request_cache(self):
        source = self.sources['baseline.ibt']
        for supplied in (replace(source, signature=('stale', 0, 0)),
                         replace(source, path=self.sources['candidate.ibt'].path)):
            with self.subTest(path=supplied.path, signature=supplied.signature):
                with patch.object(api.repository, 'load', return_value=source) as load:
                    result = asyncio.run(api.load_analysis(api.AnalyzeRequest(filename=source.path.name),
                                                           parsed_telemetry=supplied, update_current_analysis=False))
                load.assert_called_once_with(source.path)
                self.assertEqual(result['file']['name'], source.path.name)


class SessionBestTests(BackToBackLaps, unittest.TestCase):
    def test_late_improvement_is_the_session_best_and_has_zero_gap(self):
        source = self.telemetry([(50, 50), (50, 50), (49, 50)])
        source.channels["LapBestLapTime"] = np.full(source.record_count, 100.0)
        source.channels["LapBestLapTime"][-1] = 99.0
        result = api.TelemetryAnalyzer(source).analyze(api.AnalyzeRequest(target_lap=4, include_ai=False))
        self.assertEqual(result["session"]["session_best_time_s"], 99.0)
        self.assertEqual(result["comparison"]["session_best_time_s"], 99.0)
        self.assertEqual(result["comparison"]["gap_to_session_best_s"], 0.0)

    def test_session_best_ignores_unpublished_or_nonfinite_samples(self):
        source = self.telemetry([(50, 50)])
        for values, expected in (([0, -1, np.nan, np.inf, 100, 99], 99.0),
                                 ([0, -1, np.nan, np.inf], None)):
            with self.subTest(values=values):
                source.channels["LapBestLapTime"] = np.asarray(values)
                self.assertEqual(api.TelemetryAnalyzer(source)._session_best_time(), expected)
        source.channels.pop("LapBestLapTime")
        self.assertIsNone(api.TelemetryAnalyzer(source)._session_best_time())


if __name__ == "__main__":
    unittest.main()
