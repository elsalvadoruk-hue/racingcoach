"""Opening a file quickly after a restart: slow derived results kept on disk (2026-10-14)."""
import atexit
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

_runtime = tempfile.TemporaryDirectory(prefix="iracing-open-", ignore_cleanup_errors=True)
atexit.register(_runtime.cleanup)
_root = Path(_runtime.name)
os.environ.update({
    "IRACING_TUNER_DB_PATH": str(_root / "memory.sqlite3"),
    "IRACING_FILE_INDEX_PATH": str(_root / "index.sqlite3"),
    "IRACING_EXTERNAL_REFERENCE_DIR": str(_root / "references"),
    "GARAGE61_API_TOKEN_FILE": str(_root / "no-token.txt"),
    "GARAGE61_AUTO_COLLECT": "0", "IRACING_AI_ENABLED": "0", "IRACING_IMPORT_LEGACY": "0",
})

import backend_api as api
from analysis_support import JsonCache


class JsonCacheTests(unittest.TestCase):
    def test_answers_survive_a_restart_and_a_changed_file_is_worked_out_again(self):
        with tempfile.TemporaryDirectory() as folder:
            source, store = Path(folder) / "lap.csv", Path(folder) / "cache.json"
            source.write_text("a", encoding="utf-8")
            key = JsonCache.key(source)
            JsonCache(store).put(key, {"usable": True})
            self.assertEqual(JsonCache(store).get(key), {"usable": True})      # read back by a new instance
            time.sleep(.01)
            source.write_text("changed", encoding="utf-8")
            self.assertIsNone(JsonCache(store).get(JsonCache.key(source)))      # another size and time: not reused
            store.write_text("not json", encoding="utf-8")
            self.assertIsNone(JsonCache(store).get(key))                        # a broken file is just an empty cache

    def test_the_oldest_answers_go_first_beyond_the_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = JsonCache(Path(folder) / "cache.json", limit=2)
            for name in "abc":
                cache.put(name, name)
            self.assertEqual([JsonCache(Path(folder) / "cache.json").get(name) for name in "abc"], [None, "b", "c"])


class ReferenceCheckTests(unittest.TestCase):
    def test_a_garage61_lap_is_read_once_and_not_again_after_a_restart(self):
        folder = _root / "references"
        folder.mkdir(exist_ok=True)
        lap = folder / "lap.csv"
        lap.write_text("x", encoding="utf-8")

        class Reader:
            calls = 0

            def __init__(self, path):
                pass

            def parse(self):
                Reader.calls += 1
                return type("Parsed", (), {"metadata": {"timing_source": "LAP"}})()

        with patch.object(api, "Garage61ReferenceReader", Reader), \
                patch.object(api, "_REFERENCE_VALIDATION_DISK", JsonCache(_root / "garage61-validation.json")):
            first = api.external_reference_validation(lap)
            api._REFERENCE_VALIDATION_CACHE.clear()                                # as after starting the app again
            api._REFERENCE_VALIDATION_DISK.entries = None
            second = api.external_reference_validation(lap)
        self.assertEqual(first, second)
        self.assertEqual(first["timing_source"], "LAP")
        self.assertEqual(Reader.calls, 1)


if __name__ == "__main__":
    unittest.main()
