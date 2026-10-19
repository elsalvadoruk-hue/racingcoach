"""Retired post-drive AI cannot spend tokens, even from an older client."""
import atexit
import asyncio
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

_runtime = tempfile.TemporaryDirectory(prefix="iracing-no-analysis-ai-", ignore_cleanup_errors=True)
atexit.register(_runtime.cleanup)
_folder = Path(_runtime.name)
os.environ.update({
    "IRACING_TUNER_DB_PATH": str(_folder / "memory.sqlite3"),
    "IRACING_VOICE_DB_PATH": str(_folder / "voice.sqlite3"), "IRACING_FILE_INDEX_PATH": str(_folder / "index.sqlite3"),
    "IRACING_EXTERNAL_REFERENCE_DIR": str(_folder / "references"), "GARAGE61_AUTO_COLLECT": "0",
    "IRACING_AI_ENABLED": "0", "IRACING_IMPORT_LEGACY": "0",
})
import backend_api as api
from fastapi.testclient import TestClient


class RemovedAnalysisAITests(unittest.TestCase):
    def test_legacy_ai_flag_preserves_local_briefing_without_cloud_call(self):
        cloud = Mock()
        original = {"briefing": {"source": "LOCAL", "summary": "local result"}, "comparison": {"delta_s": .12}}
        with patch.object(api, "ai_client", cloud):
            result = asyncio.run(api.add_ai_briefing(original, api.AnalyzeRequest(include_ai=True)))
        cloud.models.generate_content.assert_not_called()
        self.assertEqual(result["briefing"], {"source": "LOCAL", "summary": "local result"})
        self.assertEqual(result["comparison"]["delta_s"], .12)
        self.assertFalse(result["ai_usage"]["used"])
        self.assertEqual(result["ai_usage"]["status"], "REMOVED")

    def test_retired_planner_does_not_analyze_or_store(self):
        cloud, store = Mock(), Mock()
        with patch.object(api, "ai_client", cloud), patch.object(api, "plan_store", store), patch.object(api, "load_analysis") as analysis:
            response = TestClient(api.app).post("/api/ai/experiment-plan", json={})
        self.assertEqual(response.status_code, 410)
        cloud.models.generate_content.assert_not_called()
        analysis.assert_not_called()
        store.save.assert_not_called()

    def test_saved_plans_remain_readable(self):
        store = Mock()
        store.recent.return_value = [{"plan_id": "prior-plan", "title": "saved experiment"}]
        with patch.object(api, "plan_store", store):
            response = TestClient(api.app).get("/api/experiment-plans")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["plans"][0]["plan_id"], "prior-plan")
