"""The data folder, and the settings and secrets entered on the screen. Isolated storage, no Google calls."""
import atexit
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

_runtime = tempfile.TemporaryDirectory(prefix="iracing-settings-", ignore_cleanup_errors=True)
atexit.register(_runtime.cleanup)
_root = Path(_runtime.name)
os.environ.update({
    "IRACING_TUNER_DB_PATH": str(_root / "memory.sqlite3"),
    "IRACING_FILE_INDEX_PATH": str(_root / "index.sqlite3"),
    "IRACING_EXTERNAL_REFERENCE_DIR": str(_root / "references"),
    "GARAGE61_API_TOKEN_FILE": str(_root / "garage61_api_token.txt"),
    "GARAGE61_AUTO_COLLECT": "0", "IRACING_AI_ENABLED": "0", "IRACING_IMPORT_LEGACY": "0",
})

from fastapi.testclient import TestClient

import app_settings
import backend_api as api

# Another test module may have imported the app first, with its own temporary folder: use the one in force.
_root = api.MEMORY_DB_PATH.parent
assert Path(tempfile.gettempdir()).resolve() in _root.resolve().parents, _root
_token = api.GARAGE61_API_TOKEN_FILE

KEY = {"type": "service_account", "project_id": "shared-project", "private_key": "-----BEGIN PRIVATE KEY-----\nSECRET\n-----END PRIVATE KEY-----\n",
       "client_email": "coach@shared-project.iam.gserviceaccount.com"}


class Garage61ConnectionMessageTests(unittest.TestCase):
    def test_missing_token_does_not_call_provider(self):
        with patch.object(api, '_garage61_api_token_local', return_value=('', '')), \
                patch.object(api.urllib.request, 'urlopen') as network:
            status = api.garage61_api_connection_status()
        self.assertFalse(status['configured'])
        self.assertNotIn('error_code', status)
        self.assertIn('내 주행 데이터', status['message'])
        network.assert_not_called()

    def test_authentication_and_permission_errors_offer_settings_recovery(self):
        for http, code in [(401, 'API_TOKEN_INVALID'), (403, 'API_PERMISSION_DENIED')]:
            with self.subTest(http=http), \
                    patch.object(api, '_garage61_api_token_local', return_value=('synthetic', 'environment')), \
                    patch.object(api.urllib.request, 'urlopen', side_effect=api.urllib.error.HTTPError(
                        'https://example.invalid', http, 'synthetic', {}, None)):
                status = api.garage61_api_connection_status()
            self.assertEqual(status['error_code'], code)
            self.assertFalse(status['connected'])
            self.assertIn('설정 → 설치 설정', status['message'])


class DataFolderTests(unittest.TestCase):
    def test_the_folder_follows_the_database_then_the_location_file_then_the_project(self):
        with patch.dict(os.environ, {"IRACING_TUNER_DB_PATH": str(_root / "memory.sqlite3")}):
            self.assertEqual(app_settings.data_dir(), _root)
        with patch.dict(os.environ, {"IRACING_TUNER_DB_PATH": "", "IRACING_MEMORY_DB_PATH": ""}):
            with tempfile.TemporaryDirectory() as folder:
                pointer = Path(folder) / "data-location.txt"
                with patch.object(app_settings, "LOCATION_FILE", pointer):
                    self.assertEqual(app_settings.data_dir(), app_settings.ROOT / "data")      # nothing says otherwise
                    pointer.write_text("E:\\somewhere\n", encoding="utf-8")
                    self.assertEqual(app_settings.data_dir(), Path("E:\\somewhere"))
                    with patch.dict(os.environ, {"IRACING_DATA_DIR": folder}):
                        self.assertEqual(app_settings.data_dir(), Path(folder))

    def test_no_drive_account_or_project_is_written_into_the_code(self):
        backend = Path(app_settings.__file__).resolve().parent
        for name in ("app_settings.py", "backend_api.py", "voice_coach.py", "garage61_crawler.py", "radio_audio.py", "spotter.py", "live_pit.py"):
            source = (backend / name).read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"[A-Za-z]:\\?SQ", source), name)                       # a drive and folder of one machine
            self.assertIsNone(re.search(r"getenv\(['\"]GCP_PROJECT_ID['\"], ['\"][^'\"]", source), name)   # a project to fall back on


class SettingsRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(api.app)
        environment = patch.dict(os.environ, {"IRACING_TUNER_DB_PATH": str(api.MEMORY_DB_PATH)})
        environment.start()
        self.addCleanup(environment.stop)
        self.addCleanup(self.reset)

    def reset(self):
        for path in (_root / app_settings.SETTINGS_NAME, _root / app_settings.KEY_NAME, _token):
            path.unlink(missing_ok=True)
        with patch.object(app_settings, "BACKEND", _root / "none"):
            api._reload_settings()

    def test_telemetry_folders_are_checked_saved_and_used_at_once(self):
        view = self.client.get("/api/settings").json()
        self.assertEqual((view["data_dir"], view["settings"]["telemetry_dirs"]), (str(_root), []))
        laps = _root / "laps"
        laps.mkdir(exist_ok=True)
        self.assertEqual(self.client.put("/api/settings", json={"telemetry_dirs": ["relative\\laps"]}).status_code, 422)
        self.assertEqual(self.client.put("/api/settings", json={"telemetry_dirs": [str(_root / "missing")]}).status_code, 422)
        view = self.client.put("/api/settings", json={"telemetry_dirs": [f'"{laps}"']}).json()
        self.assertEqual(view["settings"]["telemetry_dirs"], [str(laps)])
        self.assertIn({"path": str(laps), "exists": True, "added": True}, view["telemetry_folders"])
        self.assertIn(laps, api.TELEMETRY_DIRS)
        self.assertEqual(json.loads((_root / "settings.json").read_text(encoding="utf-8"))["telemetry_dirs"], [str(laps)])
        view = self.client.put("/api/settings", json={"telemetry_dirs": []}).json()
        self.assertNotIn(laps, api.TELEMETRY_DIRS)

    def test_a_key_is_checked_kept_in_the_data_folder_and_never_sent_back(self):
        with patch.object(app_settings, "BACKEND", _root / "none"):      # no key of an older installation in the way
            for bad in ("not json", json.dumps({"type": "authorized_user"}), json.dumps({**KEY, "private_key": ""})):
                self.assertEqual(self.client.put("/api/settings/google-key", json={"value": bad}).status_code, 422)
            self.assertFalse((_root / "gcp-key.json").exists())
            reply = self.client.put("/api/settings/google-key", json={"value": json.dumps(KEY)})
            self.assertEqual(reply.status_code, 200)
            self.assertNotIn("SECRET", reply.text)
            google = reply.json()["google"]
            self.assertEqual((google["key_present"], google["key_project_id"], google["key_account"]),
                             (True, "shared-project", KEY["client_email"]))
            # The project of the key is used unless another one is entered.
            self.assertEqual(os.environ["GCP_PROJECT_ID"], "shared-project")
            self.assertEqual(os.environ["GOOGLE_APPLICATION_CREDENTIALS"], str(_root / "gcp-key.json"))
            self.client.put("/api/settings", json={"gcp_project_id": "another-project"})
            self.assertEqual(os.environ["GCP_PROJECT_ID"], "another-project")
            self.assertEqual(self.client.put("/api/settings", json={"gcp_project_id": "bad project!"}).status_code, 422)
            self.assertNotIn("SECRET", self.client.get("/api/settings").text)
            google = self.client.delete("/api/settings/google-key").json()["google"]
            self.assertFalse(google["key_present"])
            self.assertNotIn("GOOGLE_APPLICATION_CREDENTIALS", os.environ)

    def test_the_garage61_token_is_stored_and_only_its_presence_is_shown(self):
        self.assertEqual(self.client.put("/api/settings/garage61-token", json={"value": "two words"}).status_code, 422)
        reply = self.client.put("/api/settings/garage61-token", json={"value": "Bearer abc123"})
        self.assertEqual((reply.json()["garage61"], _token.read_text(encoding="utf-8")), ({"token_present": True}, "abc123"))
        self.assertNotIn("abc123", reply.text)
        self.assertEqual(self.client.delete("/api/settings/garage61-token").json()["garage61"], {"token_present": False})


if __name__ == "__main__":
    unittest.main()
