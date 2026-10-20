"""Recording discovery updates immediately without touching game/iRacing stores."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from lmu_settings import register_routes, saved_folders
from lmu_telemetry import discover_lmu_telemetry_dirs, list_lmu_telemetry_files


class LmuSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / 'settings' / 'lmu.json'
        self.env = patch.dict(os.environ, {'LMU_SETTINGS_PATH': str(self.path), 'LMU_TELEMETRY_DIRS': ''})
        self.env.start()
        app = FastAPI()
        register_routes(app, discover_lmu_telemetry_dirs)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.temp.cleanup()

    def test_read_does_not_create_store_and_save_updates_file_discovery(self):
        self.assertEqual(self.client.get('/api/lmu/settings').status_code, 200)
        self.assertFalse(self.path.exists())
        folder = self.root / 'Telemetry'
        folder.mkdir()
        source = folder / 'session.duckdb'
        source.write_bytes(b'read-only catalog fixture')
        response = self.client.put('/api/lmu/settings', json={'telemetry_dirs': [str(folder)]})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['restart_required'])
        self.assertEqual(response.json()['source'], 'SAVED')
        self.assertIn(source, list_lmu_telemetry_files())
        self.assertEqual(saved_folders(), [str(folder)])
        self.assertEqual(source.read_bytes(), b'read-only catalog fixture')
        self.client.put('/api/lmu/settings', json={'telemetry_dirs': []})
        self.assertNotIn(source, list_lmu_telemetry_files())

    def test_case_insensitive_duplicates_and_environment_override(self):
        folder = self.root / 'Telemetry'
        other = self.root / 'Environment'
        result = self.client.put('/api/lmu/settings', json={'telemetry_dirs': [str(folder), str(folder).upper()]})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(len(result.json()['settings']['telemetry_dirs']), 1)
        with patch.dict(os.environ, {'LMU_TELEMETRY_DIRS': str(other)}):
            self.assertEqual(discover_lmu_telemetry_dirs(), (other,))
            self.assertEqual(self.client.get('/api/lmu/settings').json()['source'], 'ENVIRONMENT')
            self.assertEqual(saved_folders(), [str(folder)])

    def test_invalid_payload_does_not_replace_saved_settings(self):
        self.client.put('/api/lmu/settings', json={'telemetry_dirs': [str(self.root)]})
        before = self.path.read_bytes()
        for body in ({'telemetry_dirs': ['relative/folder']}, {'telemetry_dirs': ['bad\npath']},
                     {'telemetry_dirs': [], 'google_key': 'forbidden'},
                     {'telemetry_dirs': [str(self.root)] * 13}):
            self.assertEqual(self.client.put('/api/lmu/settings', json=body).status_code, 422)
            self.assertEqual(self.path.read_bytes(), before)

    def test_corrupt_settings_fall_back_without_rewriting(self):
        self.path.parent.mkdir()
        self.path.write_bytes(b'corrupt')
        self.assertEqual(self.client.get('/api/lmu/settings').json()['source'], 'DEFAULT')
        self.assertEqual(self.path.read_bytes(), b'corrupt')


if __name__ == '__main__':
    unittest.main()
