"""Private corner notes and route validation; temporary stores only."""
import os
from pathlib import Path
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

_bootstrap = tempfile.TemporaryDirectory(prefix='racing-corner-notes-tests-', ignore_cleanup_errors=True)
os.environ.setdefault('IRACING_TUNER_DB_PATH', str(Path(_bootstrap.name) / 'memory.sqlite3'))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from analysis_support import CornerDefinitionStore
from corner_notes import CornerNotesStore
from corner_notes_routes import CornerNotesRoutesServices, register_routes


class CornerNotesTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.store = CornerNotesStore(self.folder / 'corner-notes.json')
        self.names = CornerDefinitionStore(self.folder / 'corner-definitions.json')
        app = FastAPI()
        register_routes(app, CornerNotesRoutesServices(lambda: self.store, lambda: self.names))
        self.client = TestClient(app)
        self.body = {'game': 'iracing', 'track_id': '123', 'track_config': 'GP', 'pct': .123, 'text': '제동 시작 표식을 확인합니다.'}

    def get(self, **changes):
        return self.client.get('/api/corner-notes', params={**{k: self.body[k] for k in ('game', 'track_id', 'track_config')}, **changes})

    def put(self, **changes):
        return self.client.put('/api/corner-notes', json={**self.body, **changes})

    def test_read_without_file_does_not_create_private_data(self):
        self.assertEqual(self.get().json(), {'notes': []})
        self.assertFalse(self.store.path.exists())

    def test_save_reload_edit_and_delete_in_same_position_bucket(self):
        saved = self.put().json()
        self.assertEqual(saved['pct'], .125)
        self.assertIn('+00:00', saved['updated_at'])
        self.store = CornerNotesStore(self.store.path)
        self.assertEqual(self.get().json()['notes'][0]['text'], self.body['text'])
        self.put(pct=.124, text='가속 지점을 확인합니다.')
        self.assertEqual(len(self.get().json()['notes']), 1)
        self.assertEqual(self.put(text='').status_code, 200)
        self.assertEqual(self.get().json()['notes'], [])

    def test_game_track_layout_and_position_are_separate_without_car_key(self):
        self.put()
        for changes in ({'game': 'lmu'}, {'track_id': '124'}, {'track_config': 'Short'}, {'pct': .14}):
            self.assertEqual(self.put(**changes).status_code, 200)
        self.assertEqual(len(self.get().json()['notes']), 2)
        for changes in ({'game': 'lmu'}, {'track_id': '124'}, {'track_config': 'Short'}):
            self.assertEqual(len(self.get(**changes).json()['notes']), 1)

    def test_limit_accepts_300_unicode_characters_and_rejects_301(self):
        self.assertEqual(self.put(text='가' * 300).status_code, 200)
        self.assertEqual(self.put(text='가' * 301).status_code, 422)
        self.assertEqual(len(self.get().json()['notes'][0]['text']), 300)

    def test_invalid_positions_and_identity_rejected(self):
        for changes in ({'pct': -1}, {'pct': 1.01}, {'pct': 'NaN'}, {'pct': 'Infinity'},
                        {'game': 'other'}, {'track_id': ''}, {'track_id': '   '}, {'text': None}):
            with self.subTest(changes=changes):
                self.assertEqual(self.put(**changes).status_code, 422)
        self.assertFalse(self.store.path.exists())

    def test_confirmed_display_names_are_stored_but_suggestions_are_not_invented(self):
        self.names.set_track_names('123', 'GP', [{'pct': .124, 'number': '4', 'name': '테스트 코너'}])
        self.assertEqual(self.put().json()['number'], '4')
        self.assertEqual(self.get().json()['notes'][0]['name'], '테스트 코너')
        self.assertIsNone(self.put(pct=.2).json()['number'])

    def test_failed_atomic_replace_preserves_existing_bytes_and_removes_temporary_file(self):
        self.put()
        before = self.store.path.read_bytes()
        with patch('corner_notes.os.replace', side_effect=PermissionError('synthetic failure')):
            response = self.put(text='교체할 메모')
        self.assertEqual(response.status_code, 500)
        self.assertIn('저장하지 못했습니다', response.json()['detail'])
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(list(self.folder.glob('corner-notes.json.*.tmp')), [])

    def test_corrupt_or_unreadable_store_is_reported_and_never_overwritten(self):
        self.store.path.write_bytes(b'{broken')
        self.assertEqual(self.get().status_code, 500)
        self.assertEqual(self.put().status_code, 500)
        self.assertEqual(self.store.path.read_bytes(), b'{broken')

    def test_concurrent_different_corners_do_not_lose_notes(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda index: self.store.put('iracing', '123', 'GP', index / 20, str(index)), range(1, 12)))
        self.assertEqual(len(self.store.list('iracing', '123', 'GP')), 11)

    def test_runtime_binding_uses_environment_path_without_writing_other_stores(self):
        import backend_api as api
        with patch.dict(os.environ, {'IRACING_CORNER_NOTES_PATH': str(self.folder / 'runtime.json')}):
            client = TestClient(api.app)
            self.assertEqual(client.put('/api/corner-notes', json=self.body).status_code, 200)
            self.assertEqual(len(client.get('/api/corner-notes', params={k: self.body[k] for k in ('game', 'track_id', 'track_config')}).json()['notes']), 1)
        self.assertTrue((self.folder / 'runtime.json').exists())
        self.assertFalse(self.store.path.exists())
